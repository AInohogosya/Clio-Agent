from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

from ethos.comms.adapters.base import ChannelAdapter, emit_inbound, http_error_detail, offer_seen
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.line")

API_BASE = "https://api.line.me/v2/bot"

#: LINE's own limit on one text message, and it is a hard one: the API refuses a
#: longer body rather than accepting it and truncating on the sender's behalf,
#: which would put a message in the transcript as though it had arrived whole.
MAX_TEXT = 5000

#: The answer to LINE's console "Verify" button, which GETs the webhook URL once
#: at configuration time and marks the door invalid on anything that is not a 2xx.
#:
#: A constant rather than an echo, because there is nothing in the request to
#: echo: the check is "can this URL be reached", and a reply that varied with the
#: request would be answering a question nobody asked.
PONG = "OK"


def line_signature(secret: str) -> Any:
    """LINE's proof: base64 HMAC-SHA256 of the raw body, keyed by the channel secret.

    A closure for the same reason `webhook.meta_signature` is one: the comparison
    is the part worth being able to read and test on its own, and it must be
    computed over the bytes exactly as they arrived. Verifying after `json.loads`
    would be verifying something LINE did not sign — JSON has no canonical form,
    so a parse-and-re-serialize round trip can produce bytes that hash the same
    as the sender's original while meaning something else.

    Unlike Slack's, there is no timestamp in the signed string and therefore no
    expiry to enforce. That is a real difference and it is not fixable from here:
    a captured LINE request can be replayed forever, which is what the `event_id`
    dedupe below is for. It is a weaker guarantee than the Slack door has, and
    saying so is more useful than implying the two are the same shape.
    """
    def verify(headers: dict[str, str], raw: bytes) -> bool:
        signature = headers.get("x-line-signature")
        if not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
        # Base64, not hex: LINE states the header in base64 and comparing against
        # a hex digest would refuse every genuine request.
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(expected, signature.strip())

    return verify


def line_address(user_id: str) -> str:
    """A LINE user id as the send path wants it: `line:<userId>`.

    A user id and not a group id, and the reason is the same one Slack's address
    is a channel rather than a person. A reply is sent to the `person_id` of the
    message it answers, so the address has to name something the API can be asked
    to deliver to — and for LINE that is the user. `parse` only admits
    `source.type == "user"` for the same reason: a group id would turn every
    member's words into one sender and one reply to everybody in the room.
    """
    return f"line:{str(user_id).strip()}"


class LineAdapter(ChannelAdapter):
    """LINE Messaging API: a signed webhook in, the push endpoint out.

    The same shape as WhatsApp and Slack and none of Telegram's: LINE POSTs each
    event to a URL declared in the channel's console, so inbound exists only where
    that URL lands. The receiver is `ethos.comms.webhook`'s, and this adapter owns
    the parsing, the signature and the send.

    **Why push rather than reply.** LINE has both, and reply is the one a naive
    implementation reaches for: you POST to `/message/reply` with the
    `replyToken` that arrived with the event. That token is single-use and short-
    lived, and an agent that thinks for longer than it lives cannot use it. A
    deliberate answer to a question asked yesterday is the whole job here, so
    this always pushes. The cost is one API call the reply endpoint would have
    made free, and the benefit is that an answer is not silently impossible
    because the agent was thinking.

    **What LINE will not tell us.** A webhook event names the sender by user id
    and carries no profile at all — no display name, no handle, nothing like
    WhatsApp's `contacts` array or Discord's `author`. So this is the one adapter
    that spends a request to learn who is writing, exactly like Slack's
    `users.info`, and the cache is what keeps that affordable. Without the
    `profile` permission LINE answers 404 for *every* user, so the refusal is
    cached too: a retry loop for an answer that will never arrive costs one HTTP
    round trip on the path of every inbound message, forever.
    """

    name = "line"
    #: How many users to remember a name for. Bounded for Slack's reason: a door
    #: set to answer the public meets an unbounded number of them.
    NAME_CACHE_LIMIT = 4096

    def __init__(
        self,
        channel_access_token: str,
        channel_secret: str,
        allowed_ids: list[str] | None = None,
        accept_from_anyone: bool = False,
        http: Any = None,
        now_fn: Any = time.time,
    ):
        self.channel_access_token = channel_access_token
        self.channel_secret = channel_secret
        # User ids as LINE writes them, `U` followed by 32 hex characters. One
        # flat set: there is only one kind of id this door admits, because a
        # group is refused in `parse` rather than filtered here.
        self.allowed = {str(value).strip() for value in (allowed_ids or []) if str(value).strip()}
        self.accept_from_anyone = accept_from_anyone
        self.http = http
        self._owns_http = http is None
        self.now_fn = now_fn
        # LINE does not retry a delivery that gets a 2xx, but it does redeliver
        # on a timeout or a connection it believes was cut — and a redelivery
        # recorded twice is a message the agent deliberates on twice. Bounded,
        # because this bridges a restart rather than keeping a history.
        self._seen_event_ids: set[str] = set()
        # User id to the display name `profile` last reported, and to the fact
        # that it was asked and could not answer. The negative is the half that
        # matters; see the class docstring.
        self._names: dict[str, str | None] = {}

    async def start(self) -> None:
        await self._ensure_http()
        logger.info("comms.line.started", allowed=len(self.allowed),
                    open_to_all=self.accept_from_anyone)

    async def listen(self) -> None:
        """Nothing on the adapter: the receiver is the host's, not this object's.

        The webhook listener is one loopback port shared by every push channel, so
        it is owned by `CommsHost` and started once, by whichever process is the
        listening one. The adapter owns parsing and sending; the receiver owns the
        door.
        """

    async def stop(self) -> None:
        await self._close_http()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.channel_access_token}"}

    def accepts(self, user: str | None, channel: str | None) -> bool:
        """Whether this author, in this conversation, is somebody to answer.

        Only the user id can match: the `channel` argument is a group's id, which
        `parse` never admits, so accepting on it would be a second, invisible
        allowlist written by whoever adds the bot to a room.
        """
        return self._accepts_id(user, None)

    async def user_name(self, user_id: str | None) -> str | None:
        """What this user calls themselves, or `None` when LINE will not say.

        The cache records refusals as `None` and does not ask again, which is the
        part that stops this being a cost on the inbound path. The `profile`
        permission is off by default on a channel that has never asked for it, so
        the common case for a new door is that this returns `None` for everyone
        and must stop trying — the messages are still answered, addressed by the
        id they always were. A failure here costs a name, not a conversation.
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
                    f"{API_BASE}/profile/{key}", headers=self._headers(),
                )
                if response.status_code < 400:
                    payload = response.json()
            except Exception:
                logger.warning("comms.line.profile_failed", user=key)
            display = payload.get("displayName") if isinstance(payload, dict) else None
            if isinstance(display, str) and display.strip():
                name = display.strip()
        if name is None:
            # Logged once per user rather than once per message, because that is
            # what the cache turns this into.
            logger.info("comms.line.no_user_name", user=key,
                        hint="enable the profile permission to name senders")
        self._names[key] = name
        return name

    def mark_seen(self, event_id: str | None) -> bool:
        """True the first time an id is offered, False for every redelivery."""
        return offer_seen(self._seen_event_ids, event_id, limit=2048, drop=512)

    def parse(self, payload: dict[str, Any]) -> list[tuple[str, str, str]]:
        """
        The messages in a webhook body, as `(user_id, event_id, text)`.

        Total on purpose, for the reason the WhatsApp and Slack parsers are: a
        webhook that answers 500 on an event type it does not know is one LINE
        retries, and LINE sends at least five types this agent has no use for —
        follow, join, leave, unfollow, and every non-text message.

        Three shapes are skipped before the text is even looked at, and each for a
        reason rather than as tidiness:

        * **Anything whose `source.type` is not `user`.** A LINE bot added to a
          room or a group receives `group` and `room` events with a conversation
          id rather than a person, and a reply to a conversation id is a message
          to everybody in it. Telegram refuses groups for the same reason and
          Discord and Slack do not, because on those a channel *is* the
          conversation and on LINE it is an alternative to one.
        * **Anything that is not a text message.** Sticker, image, location, file,
          audio, video and the rest all carry a `message` block with no `text`,
          and a reply to a sticker is a reply that says nothing about it.

        There is deliberately **no** "is this the bot talking to itself" check
        here, and its absence is a fact about LINE rather than an oversight: a
        webhook on a 1:1 chat receives what the *user* sent and not the bot's
        own pushes, because there is no room in which the bot and the user could
        be confused for one another. Discord and the Bot Framework both deliver a
        bot its own messages, which is why both of those filter on the sender
        being this app — and this adapter can say so plainly because the case
        does not arise.
        """
        out: list[tuple[str, str, str]] = []
        for event in payload.get("events") or []:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "message":
                continue
            source = event.get("source")
            if not isinstance(source, dict) or source.get("type") != "user":
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("type") != "text":
                continue
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            user = source.get("userId")
            if not user:
                continue
            out.append((str(user), str(event.get("id") or ""), text.strip()))
        return out

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        destination = self._destination(person_id, "line")
        if isinstance(destination, dict):
            return destination
        user = destination
        body = {
            "to": user,
            "messages": [{"type": "text", "text": text[:MAX_TEXT]}],
        }
        try:
            response = await self.http.post(
                f"{API_BASE}/message/push", json=body, headers=self._headers(),
            )
        except Exception as exc:
            logger.exception("comms.line.send_failed")
            return {"status": "failed", "error": str(exc)}
        if response.status_code >= 400:
            # LINE states the refusal plainly — a revoked token, a user who has
            # blocked the bot, a message that is too long — and passing it
            # through is what lets the transcript tell "the agent chose silence"
            # from "the agent tried to speak and was not allowed to".
            detail = _error_detail(response)
            logger.warning("comms.line.send_refused", status=response.status_code, detail=detail)
            return {"status": "failed", "error": detail, "http_status": response.status_code}
        return {
            "status": "sent",
            "to": user,
            "provider_message_id": _sent_message_id(response),
        }

    async def handle_webhook(self, payload: dict[str, Any], on_inbound: Any) -> int:
        accepted = 0
        for user, event_id, text in self.parse(payload):
            if not self.accepts(user, None):
                logger.info("comms.line.sender_not_allowed", user=user)
                continue
            if not self.mark_seen(event_id):
                continue
            if await emit_inbound(
                on_inbound,
                Message(
                    channel=self.name,
                    person_id=line_address(user),
                    text=text,
                    conversation_key=f"line:{user}",
                    attachments=[{"event_id": event_id}],
                    # LINE carries no profile in the event, so the name costs a
                    # request and is cached; see `user_name`.
                    sender_name=await self.user_name(user),
                ),
                log_event="comms.line.inbound_failed", logger=logger,
            ):
                accepted += 1
        return accepted


def _error_detail(response: Any) -> str:
    def pick(payload: dict[str, Any]) -> Any:
        details = payload.get("details")
        if isinstance(details, list) and details and isinstance(details[0], dict):
            return details[0].get("message")
        return payload.get("message")

    return http_error_detail(response, pick)


def _sent_message_id(response: Any) -> str:
    """The push id out of a 200, or `''`.

    LINE answers `{"sentMessages":[{"id":"…"}]}` and there is nothing else useful
    in it. A push id is the conversation id and cannot be replied to — there is no
    reply endpoint for a push — which is why this is reported for the record and
    never used as a threading key.
    """
    try:
        payload = response.json()
    except Exception:
        return ""
    sent = payload.get("sentMessages") if isinstance(payload, dict) else None
    if isinstance(sent, list) and sent and isinstance(sent[0], dict):
        return str(sent[0].get("id") or "")
    return ""


__all__ = ["LineAdapter", "line_address", "line_signature", "MAX_TEXT", "PONG"]
