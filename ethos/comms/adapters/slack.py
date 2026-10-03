from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from ethos.comms.adapters.base import ChannelAdapter
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.slack")

API_BASE = "https://slack.com/api"

# Slack's own version prefix, which is part of the string that gets signed.
SIGNATURE_VERSION = "v0"

# How old a signed request may be before it is refused, in seconds.
#
# Slack signs a timestamp into every request precisely so a captured one cannot
# be replayed forever, and the check is the only thing that makes the signature
# worth having: without it, anybody who once saw a valid POST — a proxy log, a
# packet capture — can resend it and the agent will deliberate on the same
# message again. Slack documents five minutes; this is that, with no clock skew
# allowance, because a generous window is a window an attacker chooses.
MAX_SIGNATURE_AGE_S = 300

# The subtypes that are the same message arriving twice under two names. Slack
# sends an `edited` event for a change to a message already delivered, and
# `message_changed` is the envelope it arrives in. Answering either would make
# the agent reply to somebody fixing a typo, which is the most reliable way to
# make a channel feel like it is talking to itself.
#
# `me_message` is here for a different and sharper reason: it is what this bot's
# own `chat.postMessage` looks like coming back. Without it, every reply the agent
# sends is delivered straight back to it as an inbound message, and the two ends
# will keep answering each other until somebody notices.
IGNORED_SUBTYPES = frozenset({
    "message_changed",
    "message_deleted",
    "message_replied",
    "me_message",
    "bot_message",
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
})


def slack_signature(secret: str, *, now_fn: Any = time.time) -> Any:
    """Slack's proof: `v0=<hexdigest>` over `v0:<timestamp>:<body>`.

    Two things differ from Meta's and both matter. The signed string wraps the
    body in a version prefix and the request's own timestamp, so the same bytes
    signed at two different seconds are two different signatures — which is what
    makes a captured request expire. And the timestamp itself is refused when it
    is old, because a signature that never goes stale is a permanent credential.

    `now_fn` is a parameter rather than a direct call to `time.time` so that the
    expiry is a thing a test can stand on the far side of, instead of a claim
    about behaviour nobody has watched happen.
    """
    def verify(headers: dict[str, str], raw: bytes) -> bool:
        signature = headers.get("x-slack-signature")
        stamp = headers.get("x-slack-request-timestamp")
        if not signature or not stamp:
            return False
        try:
            sent_at = int(stamp)
        except ValueError:
            return False
        # An unsigned-because-malformed timestamp is refused rather than treated
        # as "now", or a request with no usable timestamp would be the one
        # request that never expires.
        if abs(now_fn() - sent_at) > MAX_SIGNATURE_AGE_S:
            return False
        base = b":".join([
            SIGNATURE_VERSION.encode("utf-8"),
            stamp.encode("utf-8"),
            raw,
        ])
        expected = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        return hmac.compare_digest(f"{SIGNATURE_VERSION}={expected}", signature.strip())

    return verify


def slack_address(channel: str) -> str:
    """The channel id as the send path wants it: `slack:<id>`.

    A channel id, not a user id, and the reason is the reply path. A reply is
    sent to the `person_id` of the message it answers, so an address that named
    the person would have to be turned back into a conversation before anything
    could be sent — and a person with two conversations has no single one to pick.
    Addressing the conversation itself is what makes "asked on Slack, answered on
    Slack" work without the life loop having to know which platform it is on.
    """
    return f"slack:{str(channel).strip()}"


class SlackAdapter(ChannelAdapter):
    """Slack bot: a signed Events API webhook in, `chat.postMessage` out.

    Like WhatsApp and unlike Telegram, this channel cannot be polled: Slack POSTs
    each event to a URL declared in the app's configuration, so inbound exists
    only where that URL lands. The receiver is `ethos.comms.webhook`'s, and this
    adapter owns the parsing, the signature and the send.

    Outbound has one trap worth naming, because it is the same shape as the
    WhatsApp 24-hour window and is missed by anything that only checks the HTTP
    status. `chat.postMessage` answers **200 with `{"ok": false}`** when it
    refuses — `channel_not_found`, `not_in_channel`, `missing_scope`. A send
    that only looked at the status code would record those as delivered, and a
    surface that believes a message arrived when it did not is the specific lie
    this whole class of check exists to prevent.
    """

    name = "slack"
    #: How many users to remember a name for. Sized so a busy channel never
    #: re-asks for somebody it has already met, and small enough that a door set
    #: to answer the public cannot be made to grow it without bound.
    NAME_CACHE_LIMIT = 4096

    def __init__(
        self,
        bot_token: str,
        allowed_ids: list[str] | None = None,
        accept_from_anyone: bool = False,
        http: Any = None,
        now_fn: Any = time.time,
        self_user_id: str | None = None,
    ):
        self.bot_token = bot_token
        # Ids as Slack writes them, compared without a prefix. A user id starts
        # with `U` and a channel id with `C` or `D`, so one flat set admits
        # either kind without the operator having to know which kind they have.
        self.allowed = {str(value).strip() for value in (allowed_ids or []) if str(value).strip()}
        self.accept_from_anyone = accept_from_anyone
        self.http = http
        self._owns_http = http is None
        self.now_fn = now_fn
        # The bot's own user id, learned at start-up and used to drop its own
        # messages. Slack marks those `author.bot`, which is the usual filter,
        # but an id learned from the API is a fact rather than a convention.
        self.self_user_id = self_user_id
        # Slack retries anything it does not see a 2xx for, and a redelivery
        # recorded twice is a message the agent deliberates on twice. Bounded,
        # because this bridges a restart rather than keeping a history.
        self._seen_event_ids: set[str] = set()
        # User id to the name `users.info` last reported, and to the fact that it
        # was asked and could not answer. The negative is the half that matters:
        # without it, an app without the `users:read` scope asks once per message
        # and gets refused once per message, forever, and the cost of that is one
        # HTTP round trip on the path of every inbound message.
        #
        # Bounded for the same reason the dedupe sets are, and it matters *more*
        # here: those grow with redeliveries and this grows with the number of
        # distinct people who have ever written. A door set to answer anybody is
        # exactly the door that meets an unbounded number of them, so an unbounded
        # name cache is a slow leak created by the setting this change adds.
        self._names: dict[str, str | None] = {}

    async def start(self) -> None:
        if self.http is None:
            import httpx

            self.http = httpx.AsyncClient(timeout=20.0)
        if self.self_user_id is None:
            self.self_user_id = await self._whoami()
        logger.info("comms.slack.started", allowed=len(self.allowed), user=self.self_user_id,
                    open_to_all=self.accept_from_anyone)

    async def listen(self) -> None:
        """Nothing on the adapter: the receiver is the host's, not this object's.

        The webhook listener is one loopback port shared by every push channel, so
        it is owned by `CommsHost` and started once, by whichever process is the
        listening one. The adapter owns parsing and sending; the receiver owns the
        door.
        """

    async def stop(self) -> None:
        if self._owns_http and self.http is not None:
            await self.http.aclose()
        self.http = None

    async def _whoami(self) -> str | None:
        """The bot's own user id, or `None` when the API will not say.

        Never raised and never fatal. A deployment whose `chat:write` scope is
        missing cannot send either, so a failure here is a send failure that the
        first `send` will report in far more detail; refusing to start over it
        would turn a recoverable misconfiguration into a channel that is off.
        """
        if self.http is None:
            return None
        try:
            response = await self.http.get(
                f"{API_BASE}/auth.test", headers=self._headers(),
            )
            payload = response.json()
        except Exception:
            logger.exception("comms.slack.auth_test_failed")
            return None
        user_id = payload.get("user_id") if isinstance(payload, dict) else None
        return str(user_id) if isinstance(user_id, str) and user_id else None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bot_token}"}

    def accepts(self, user: str | None, channel: str | None) -> bool:
        """Whether this author, in this conversation, is somebody to answer.

        An empty allowlist admits nobody, which is the same choice Telegram and
        WhatsApp make and for the same reason: a bot token is not a secret to the
        workspace's members, and an agent with a shell answering whoever found its
        name is a published endpoint.

        Either id may be on the list. A person is identified by their user id but
        reached by a channel, and an operator who has only ever seen one of the two
        should not have to work out which half this is.

        `accept_from_anyone` answers yes to a workspace nobody listed, which is
        what an agent installed to be talked to by anybody in it needs — and which
        is a decision somebody has to make deliberately, so it is its own flag
        rather than an empty list quietly meaning the opposite of what it means
        on every other channel.
        """
        if self.accept_from_anyone:
            return True
        if not self.allowed:
            return False
        return bool(
            (user and str(user).strip() in self.allowed)
            or (channel and str(channel).strip() in self.allowed)
        )

    async def user_name(self, user_id: str | None) -> str | None:
        """What Slack calls this user, or `None` when it will not say.

        The one channel here whose inbound payload carries no name at all: a
        `message` event holds `user`, and the profile behind it has to be asked
        for separately. So this is the one adapter that spends a request to learn
        who is writing, and the cache is what keeps that affordable — a busy
        channel would otherwise spend one API call per message on a question
        whose answer does not change from one message to the next.

        The cache records refusals as `None` and does not ask again, which is the
        part that stops this being a cost on the inbound path: `users.info` needs
        the `users:read` scope, an app without it is refused `missing_scope` for
        every message, and a per-message retry loop for an answer that will never
        arrive is worse than having no name at all. `self._names` maps to
        `str | None` rather than holding known names for exactly this reason — a
        key present with a `None` value means "asked, refused, not asking again".
        """
        key = str(user_id or "").strip()
        if not key:
            return None
        if key in self._names:
            return self._names[key]
        # Dropped before the answer is stored rather than after, so the cache is
        # never over its bound even for the length of one lookup. Insertion order
        # is the order the people arrived in, which is the right end to forget:
        # whoever wrote last is the one still in the conversation.
        if len(self._names) >= self.NAME_CACHE_LIMIT:
            for stale in list(self._names)[:self.NAME_CACHE_LIMIT // 2]:
                del self._names[stale]
        name: str | None = None
        if self.http is not None:
            payload: Any = None
            try:
                response = await self.http.get(
                    f"{API_BASE}/users.info", params={"user": key}, headers=self._headers(),
                )
                payload = response.json()
            except Exception:
                # Never fatal: the message still gets answered, addressed by the id
                # it always was. A failure here costs a name, not a conversation.
                logger.warning("comms.slack.users_info_failed", user=key)
            profile = payload.get("user") if isinstance(payload, dict) else None
            if isinstance(profile, dict):
                # `real_name` first and `name` second: Slack lets a user leave the
                # first one empty and only the second is always set, so reading
                # them in the other order is how a workspace's most distinctive
                # names get replaced by their login handles.
                real = profile.get("real_name") or profile.get("name")
                if isinstance(real, str) and real.strip():
                    name = real.strip()
        if name is None:
            # Logged once per user rather than once per message, because that is
            # what the cache turns this into and the line names the missing scope.
            logger.info("comms.slack.no_user_name", user=key,
                        hint="add the users:read scope to name senders")
        self._names[key] = name
        return name

    def mark_seen(self, event_id: str | None) -> bool:
        """True the first time an id is offered, False for every redelivery."""
        if not event_id:
            return True
        if event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event_id)
        if len(self._seen_event_ids) > 2048:
            for stale in list(self._seen_event_ids)[:512]:
                self._seen_event_ids.discard(stale)
        return True

    def parse(self, payload: dict[str, Any]) -> list[tuple[str, str, str, str, str]]:
        """
        The messages in an Events API body, as
        `(user, channel, event_id, provider_ts, text)`.

        Total on purpose, for the reason the WhatsApp parser is: a webhook that
        answers 500 on an event type it does not know is one Slack retries
        forever, and a body carrying a `url_verification` and a `message` in the
        same envelope is a shape this has to survive rather than reject.
        """
        out: list[tuple[str, str, str, str, str]] = []
        if payload.get("type") != "event_callback":
            return out
        event = payload.get("event")
        if not isinstance(event, dict):
            return out
        # Three separate ways a message is not somebody talking to this agent, and
        # each is checked before the text is even looked at: an event from any bot
        # (including this one), a subtype that means "this already happened", and
        # an author the API has already told us is us.
        if event.get("bot_id"):
            return out
        if str(event.get("subtype") or "") in IGNORED_SUBTYPES:
            return out
        if event.get("type") != "message":
            return out
        user = event.get("user")
        channel = event.get("channel")
        text = event.get("text")
        if not user or not channel or not isinstance(text, str) or not text.strip():
            return out
        author = str(user)
        if self.self_user_id and author == self.self_user_id:
            return out
        return [(
            author,
            str(channel),
            str(payload.get("event_id") or ""),
            str(event.get("ts") or ""),
            text.strip(),
        )]

    def challenge(self, payload: dict[str, Any]) -> str | None:
        """The challenge a URL-verification POST must be answered with.

        Slack proves a callback URL by POSTing `{"type": "url_verification",
        "challenge": …}` once, at configuration time, and comparing the response
        body to the challenge as text. It is unsigned — there is nothing signed
        yet — so the receiver answers it before any signature check, and the
        reason it is safe is that it writes nothing and grants nothing.
        """
        if payload.get("type") != "url_verification":
            return None
        challenge = payload.get("challenge")
        return str(challenge) if isinstance(challenge, str) and challenge else None

    async def handle_webhook(self, payload: dict[str, Any], on_inbound: Any) -> int:
        if self.challenge(payload) is not None:
            return 0
        accepted = 0
        for user, channel, event_id, provider_ts, text in self.parse(payload):
            if not self.accepts(user, channel):
                logger.info("comms.slack.sender_not_allowed", user=user, channel=channel)
                continue
            if not self.mark_seen(event_id):
                continue
            try:
                await on_inbound(Message(
                    channel=self.name,
                    person_id=slack_address(channel),
                    text=text,
                    conversation_key=f"slack:{channel}",
                    attachments=[{"user": user, "ts": provider_ts}],
                    # Slack sends a channel id as the sender here rather than the
                    # author, which is deliberate for the reply path and useless
                    # for the conversation: two people in one channel produce one
                    # `person_id` and one reply going to both. So the author stays
                    # in `attachments` where it always was, and is now also named.
                    sender_name=await self.user_name(user),
                ))
                accepted += 1
            except Exception:
                logger.exception("comms.slack.inbound_failed", channel=channel)
        return accepted

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        if self.http is None:
            return {"status": "failed", "error": "slack adapter not started"}
        if not person_id or not person_id.startswith("slack:"):
            return {"status": "failed", "error": "slack adapter requires slack: person_id"}
        channel = person_id.split(":", 1)[1].strip()
        if not channel:
            return {"status": "failed", "error": "no destination channel"}
        body: dict[str, Any] = {"channel": channel, "text": text[:39_000]}
        # A reply that is a reply, when the caller said which message it answers.
        # Slack's `thread_ts` is a channel-local timestamp rather than an id, so
        # this is only used when one was actually carried through.
        thread = _thread_stamp(in_reply_to)
        if thread:
            body["thread_ts"] = thread
        try:
            response = await self.http.post(
                f"{API_BASE}/chat.postMessage", json=body, headers=self._headers(),
            )
        except Exception as exc:
            logger.exception("comms.slack.send_failed")
            return {"status": "failed", "error": str(exc)}
        if response.status_code >= 400:
            detail = _error_detail(response)
            logger.warning("comms.slack.send_refused", status=response.status_code, detail=detail)
            return {"status": "failed", "error": detail, "http_status": response.status_code}
        # The 200 that is not a success. `chat.postMessage` reports every refusal
        # this way, so the body is the answer and the status code is only the
        # answer to whether the request itself was understood.
        try:
            payload = response.json()
        except Exception:
            return {"status": "failed", "error": "slack returned a body that is not json"}
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            error = payload.get("error") if isinstance(payload, dict) else None
            detail = str(error or "slack_refused")[:300]
            logger.warning("comms.slack.send_refused", detail=detail)
            return {"status": "failed", "error": detail}
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        return {
            "status": "sent",
            "channel": channel,
            "provider_message_id": str(message.get("ts") or ""),
        }


def _thread_stamp(in_reply_to: Any) -> str:
    """A Slack `ts` out of whatever the caller had, or `''`.

    Deliberately narrow: only a string that is exactly the shape of a Slack
    timestamp is used, because posting a wrong `thread_ts` silently files a
    message under a thread that does not exist, and an empty one only means the
    reply is top-level.
    """
    if not isinstance(in_reply_to, str):
        return ""
    candidate = in_reply_to.strip()
    if not candidate or len(candidate) > 32:
        return ""
    head, _, tail = candidate.partition(".")
    if not head.isdigit() or not tail.isdigit() or not 6 <= len(tail) <= 6:
        return ""
    return candidate


def _error_detail(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"http_{response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if error:
            return str(error)[:300]
    return f"http_{response.status_code}"
