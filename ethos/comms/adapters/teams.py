from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

from ethos.comms.adapters.base import ChannelAdapter, emit_inbound, http_error_detail, offer_seen
from ethos.comms.jwt import PublicKeys, bearer
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.teams")

#: Where the Bot Framework issues the token that authenticates *outbound* sends.
#: The tenant is fixed to `botframework.com` rather than `common` on purpose: a
#: bot's credentials live in one tenant, and `common` would let a token minted
#: against any tenant on earth be spent here.
TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"

#: The scope that token is asked for. `.default` means "the app registration's own
#: granted scopes", which is how the Bot Framework grants Connector access — there
#: is no way to name it, and asking for it by hand is refused.
TOKEN_SCOPE = "https://api.botframework.com/.default"

#: Where a channel token's signing keys are published, reached through the
#: OpenID metadata document rather than hardcoded so a Microsoft redirect is
#: followed rather than turned into a closed door. The fallback is what the
#: metadata document's `jwks_uri` resolves to, and it is live — the other
#: spelling of this URL, without the `v1/`, answers 404 and would have been a
#: door with no keys at all.
OPENID_METADATA = "https://login.botframework.com/v1/.well-known/openidconfiguration"
JWKS_FALLBACK = "https://login.botframework.com/v1/.well-known/keys"

#: The only issuer a channel token may carry. Bot Framework states this as
#: `https://api.botframework.com`, and the SDK compares it exactly — the same
#: comparison this makes, which is why an emulator token (a different issuer,
#: issued for localhost during development) is refused here and not merely
#: inconvenient.
ISSUER = "https://api.botframework.com"

#: Activity types that are somebody talking, as opposed to the framework
#: describing itself. `message` is the one; `messageReaction`, `invoke` and the
#: rest are answered differently by a real Bot Framework adapter and are not
#: answered at all by this one.
IGNORED_TYPES = frozenset({
    "typing", "conversationUpdate", "membersAdded", "membersRemoved",
    "conversationReaction", "messageReaction", "messageUpdate",
    "event", "invoke", "installationUpdate", "tokenResponse", "endOfConversation",
})

#: Teams prefixes an @mention of the bot with an XML-ish tag naming it, so every
#: message aimed at this agent arrives with `<at>Ethos</at> please do the thing`
#: in it. Left in, the agent reads its own name as the first word the person
#: said and every stored conversation opens with it.
MENTION = re.compile(r"<at[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)

#: How many conversations to remember a service URL for. Bounded for the reason
#: every other cache in this tree is bounded: a door set to answer anybody meets
#: an unbounded number of them.
SERVICE_CACHE_LIMIT = 2048

#: Where a `serviceUrl` is allowed to point, as host suffixes.
#:
#: The only value that would make this a security decision is the value itself,
#: and it comes from the request body — so this is the check that stops a body
#: from naming a URL this process will POST to. Narrow by default: the global
#: Teams endpoints are `*.trafficmanager.net`, and a sovereign cloud or a
#: customer-hosted channel adds its own, which is why it is configuration rather
#: than a constant inside this module. A deployment that needs another host adds
#: it deliberately rather than by widening a check nobody reads.
DEFAULT_SERVICE_HOSTS = ("trafficmanager.net", "botframework.com")

#: Teams' own limit on one message. Lower than Teams' documented 4096, because
#: the documented limit counts the text plus the bot's mention tag and the
#: activity envelope around it, and this is the text alone.
MAX_TEXT = 4000


class TeamsAdapter(ChannelAdapter):
    """Microsoft Teams, through the Bot Framework: a token-verified webhook in,
    a Connector POST out.

    **This is the hardest of the three new doors, and the difficulty is worth
    naming before anything else.** Teams is not a messaging API with a webhook and
    a send endpoint. It is a framework that will only talk to a bot you have
    registered with Microsoft's identity platform, and both directions are
    consequences of that:

    * **Inbound proves identity with a token, not a signature.** The Bot
      Framework cannot sign the body, because the body is not what it vouches
      for — it vouches for the *connection*, and does so by presenting a JWT it
      minted, naming this bot's App ID in `aud`. So the proof is
      `ethos.comms.jwt`, and a route with nothing to compare becomes a route that
      verifies a credential.

    * **Outbound needs a token of its own, and a *per-conversation* destination.**
      The App ID and app password are exchanged for a Connector token once an
      hour, and every send is a POST to `{serviceUrl}/v3/conversations/{id}/activities`
      — where `serviceUrl` is a property of the *inbound activity*, not of this
      bot. A deployment that talks to two clouds reaches two service URLs. So the
      destination is a pair, and only one half of it fits in a `person_id`.

    **What that costs, stated rather than hidden.** `person_id` is
    `teams:<conversationId>` and the service URL is remembered per conversation.
    The cache is bounded and it does not survive a restart, so the honest
    consequence is this: an *unsolicited* Teams message after a restart is refused
    with an error saying why, and a *reply* — which is the only send this agent
    makes from an inbound message — works, because inbound repopulates the cache
    before the deliberation that answers it. The alternative, packing both halves
    into `person_id`, would make the address a 300-character blob in a column
    every other channel uses a 20-character id in, and `MAX_ADDRESS_LENGTH` in the
    interface is 128 for good reason.

    **Where `serviceUrl` is checked.** Because it is a URL this process will POST
    to, named by a request body, it is compared against a configured list of
    hosts before it is believed. That check is not about the token — a forged
    `serviceUrl` inside a genuinely signed activity is not something an attacker
    can produce — it is about a *misconfigured* conversation sending the agent's
    words to an internal address that happens to be reachable.
    """

    name = "teams"

    def __init__(
        self,
        app_id: str,
        app_password: str,
        allowed_ids: list[str] | None = None,
        accept_from_anyone: bool = False,
        service_url_hosts: list[str] | None = None,
        http: Any = None,
        now_fn: Any = time.time,
    ):
        self.app_id = app_id
        self.app_password = app_password
        # AAD object ids. Teams writes them as `29:<opaque>` on most tenants and
        # as a bare GUID on others, so the set stores whatever the activity said
        # rather than guessing a shape — see the bridge's `aad` allowlist kind,
        # which is where the guess *is* made and is narrow.
        self.allowed = {str(value).strip() for value in (allowed_ids or []) if str(value).strip()}
        self.accept_from_anyone = accept_from_anyone
        self.service_url_hosts = tuple(
            host.strip().lower().lstrip(".")
            for host in (service_url_hosts if service_url_hosts is not None else DEFAULT_SERVICE_HOSTS)
            if str(host).strip()
        )
        self.http = http
        self._owns_http = http is None
        self.now_fn = now_fn
        # The Connector token and its expiry. One token serves every conversation
        # and every service URL, because it authenticates the *bot* and the
        # `aud` is the Connector rather than the destination.
        self._token = ""
        self._token_expires_at = 0.0
        # Conversation id to the service URL it was last seen arriving on, in
        # insertion order so the oldest is the one forgotten. See the class
        # docstring for why this cannot live in `person_id`.
        self._service_urls: dict[str, str] = {}
        # Bot Framework redelivers an activity it did not see a 2xx for, and a
        # redelivery recorded twice is a message the agent deliberates on twice.
        self._seen_activity_ids: set[str] = set()
        # The Bot Framework signs every activity it sends, and every adapter
        # here needs a proof object. Built here rather than in `host` so the key
        # set and the identity that must match travel together.
        self.keys = PublicKeys((JWKS_FALLBACK,))

    async def start(self) -> None:
        await self._ensure_http()
        # The OpenID metadata document names the `jwks_uri`, and following it is
        # the difference between this door working and this door working until
        # Microsoft moves something. Fetched once at start rather than per
        # request, because the request path cannot await — see `jwt.PublicKeys`.
        await self._load_keys()
        logger.info("comms.teams.started",
                    allowed=len(self.allowed), open_to_all=self.accept_from_anyone,
                    keys=not self.keys.empty, service_hosts=list(self.service_url_hosts))

    async def listen(self) -> None:
        """Nothing on the adapter: the receiver is the host's, not this object's.

        The webhook listener is one loopback port shared by every push channel, so
        it is owned by `CommsHost` and started once, by whichever process is the
        listening one. The adapter owns parsing and sending; the receiver owns the
        door.
        """

    async def stop(self) -> None:
        await self._close_http()

    async def _load_keys(self) -> None:
        """Fetch the signing keys from wherever Microsoft says they are.

        The metadata document first and the well-known path second, and the
        second is a genuine fallback rather than a second opinion: the metadata
        endpoint is the documented way in and the well-known path is what it
        resolves to, so a failure in one is very likely a failure in both — but
        "very likely" is not a reason to leave a door with no keys at all.
        """
        if self.http is None:
            return
        jwks = JWKS_FALLBACK
        try:
            response = await self.http.get(OPENID_METADATA, timeout=10.0)
            if response.status_code < 400:
                metadata = response.json()
                named = metadata.get("jwks_uri") if isinstance(metadata, dict) else None
                if isinstance(named, str) and named.startswith("https://"):
                    jwks = named
        except Exception:
            logger.warning("comms.teams.openid_unreadable")
        self.keys = PublicKeys((jwks, JWKS_FALLBACK))
        await self.keys.refresh(self.http)

    def authentic(self) -> Any:
        """This door's proof, for the receiver's route.

        A method rather than an attribute so it reads the key set at call time:
        `_load_keys` replaces `self.keys` with one pointed at the right `jwks_uri`,
        and an authenticator built before that would be verifying against the
        fallback's idea of the keys forever.
        """
        return bearer(
            self.keys,
            audience=self.app_id,
            require=_is_channel_token,
        )

    def accepts(self, user: str | None, channel: str | None) -> bool:
        """Whether this author, in this conversation, is somebody to answer.

        Either id may be on the list, for the reason the shared rule gives: a
        person is identified by their AAD id but reached by a conversation, and an
        operator who has only seen one of the two should not have to work out
        which half this is.
        """
        return self._accepts_id(user, channel)

    def mark_seen(self, activity_id: str | None) -> bool:
        """True the first time an id is offered, False for every redelivery."""
        return offer_seen(self._seen_activity_ids, activity_id, limit=2048, drop=512)

    def remember_service_url(self, conversation_id: str, service_url: str) -> bool:
        """Note where a conversation is reached, and whether the URL was allowed.

        The message is *not* refused when the URL is not allowed, and that
        asymmetry is deliberate. The activity itself was authenticated against
        Microsoft's keys, so it is genuine — dropping it would throw away a real
        message from a real person because this deployment is configured for the
        wrong cloud. Instead the message is answered and the reply is refused with
        an error naming the host, which is the fact the operator can act on.
        """
        if not self.service_url_allowed(service_url):
            logger.warning("comms.teams.service_url_refused", host=_host_of(service_url))
            return False
        # Teams writes the URL with its trailing slash and the Connector path is
        # joined onto it, so a stored `…/amer/` would become `…/amer//v3/…` — a
        # send that looks right and answers 404. Normalised here rather than
        # joined carefully in two places.
        service_url = str(service_url).rstrip("/")
        existing = self._service_urls.get(conversation_id)
        if existing == service_url:
            return True
        # Dropped from the front rather than the middle, because a Teams
        # conversation's URL does not move and the entries that have been here
        # longest are the ones a person is least likely to write to next.
        if len(self._service_urls) >= SERVICE_CACHE_LIMIT:
            for stale in list(self._service_urls)[:SERVICE_CACHE_LIMIT // 2]:
                del self._service_urls[stale]
        self._service_urls[conversation_id] = service_url
        return True

    def service_url_allowed(self, service_url: str) -> bool:
        """Whether this process is willing to POST to that URL.

        `https` only, and a host that is one of the configured suffixes or a
        subdomain of one. Both halves matter and for different reasons: the
        scheme stops a body naming `http://169.254.169.254/`, and the suffix stops
        a body naming `https://evil.example` — a *host match* alone would accept
        a suffix as a substring, so `trafficmanager.net.evil.example` would pass.
        """
        parsed = urlparse(str(service_url or ""))
        if parsed.scheme != "https" or not parsed.netloc:
            return False
        host = (parsed.hostname or "").lower()
        return any(host == suffix or host.endswith(f".{suffix}") for suffix in self.service_url_hosts)

    def parse(self, payload: dict[str, Any]) -> list[tuple[str, str, str, str]]:
        """
        The messages in an Activity body, as `(from_id, conversation_id,
        activity_id, text)`.

        Total on purpose, for the reason the other parsers are: the Bot Framework
        redelivers anything it did not see a 2xx for, and an activity type this
        agent has no use for must not turn into a 500 — so the ~twenty types in
        `IGNORED_TYPES` are all "yield nothing", not "raise".

        Two things are stripped before the text is answered for, and one is
        dropped entirely:

        * **The bot's own activities.** Teams delivers a bot its own messages,
          which without this filter is the tightest possible loop: the agent's
          words come back as an inbound message, the agent answers them, and two
          ends of one conversation keep going until somebody notices. Compared
          against `app_id` rather than a remembered id, because `from.id` on a
          bot's own activity *is* its app id and a learned id would be a second
          answer to a question the token already answered.
        * **The `@mention` tag.** See `MENTION`.
        """
        out: list[tuple[str, str, str, str]] = []
        if payload.get("type") != "message":
            return out
        conversation = payload.get("conversation")
        conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
        sender = payload.get("from")
        author = sender.get("id") if isinstance(sender, dict) else None
        text = payload.get("text")
        if not conversation_id or not author or not isinstance(text, str) or not text.strip():
            return out
        author_id = str(author)
        if author_id == self.app_id:
            return out
        return [(author_id, str(conversation_id), str(payload.get("id") or ""),
                strip_mentions(text).strip())]

    async def handle_webhook(self, payload: dict[str, Any], on_inbound: Any) -> int:
        accepted = 0
        conversation = payload.get("conversation")
        conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
        service_url = payload.get("serviceUrl")
        # Remembered before the allowlist is consulted, because it is the same
        # fact whoever is speaking: this conversation is reached here. Not
        # remembered for an activity with no conversation, which cannot be
        # answered at all.
        if isinstance(conversation_id, str) and isinstance(service_url, str):
            self.remember_service_url(conversation_id, service_url)
        for user, conversation_id, activity_id, text in self.parse(payload):
            if not text:
                continue
            if not self.accepts(user, conversation_id):
                logger.info("comms.teams.sender_not_allowed", user=user)
                continue
            if not self.mark_seen(activity_id):
                continue
            sender = payload.get("from")
            name = sender.get("name") if isinstance(sender, dict) else None
            if await emit_inbound(
                on_inbound,
                Message(
                    channel=self.name,
                    person_id=teams_address(conversation_id),
                    text=text,
                    conversation_key=f"teams:{conversation_id}",
                    attachments=[{"activity_id": activity_id, "user": user}],
                    # Unlike LINE and Slack, the name is *in* the activity: the
                    # Connector resolves it from Azure AD before delivering, so
                    # there is nothing to fetch and no cache to bound.
                    sender_name=str(name).strip() or None,
                ),
                log_event="comms.teams.inbound_failed", logger=logger,
            ):
                accepted += 1
        return accepted

    async def connector_token(self) -> str:
        """A Connector token for this bot, or `''` when it will not issue one.

        One an hour, shared by every conversation and every service URL, because
        it authenticates the bot rather than a destination: the `aud` is the
        Connector API and the resource it is spent on is chosen by the URL. So the
        only thing that would make it conversation-specific is a thing it is not.

        Cached with a minute of margin rather than to the second. A token fetched
        at exactly its expiry is a token that has already expired by the time the
        request it was fetched for reaches the service — and the failure that
        produces is a 401 on a send, reported as "the agent tried to speak and
        was refused", which points at nothing at all.
        """
        now = self.now_fn()
        if self._token and now < self._token_expires_at:
            return self._token
        if self.http is None:
            return ""
        try:
            response = await self.http.post(TOKEN_URL, data={
                "grant_type": "client_credentials",
                "client_id": self.app_id,
                "client_secret": self.app_password,
                "scope": TOKEN_SCOPE,
            })
        except Exception:
            logger.exception("comms.teams.token_failed")
            return ""
        if response.status_code >= 400:
            logger.warning("comms.teams.token_refused", status=response.status_code,
                           detail=_error_detail(response))
            return ""
        try:
            payload = response.json()
        except Exception:
            logger.warning("comms.teams.token_unreadable")
            return ""
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            return ""
        try:
            lifetime = float(payload.get("expires_in", 3600))
        except (TypeError, ValueError):
            lifetime = 3600.0
        self._token = token
        self._token_expires_at = now + max(60.0, lifetime - 60.0)
        return token

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        destination = self._destination(person_id, "teams")
        if isinstance(destination, dict):
            return destination
        conversation_id = destination
        # The half of the destination that cannot live in a `person_id`. Reported
        # as a refusal with a reason rather than as a silent no-op, because this
        # is the one case where a configured, credentialed, open door cannot send
        # and the operator has no other way to find out.
        service_url = self._service_urls.get(conversation_id, "")
        if not service_url:
            return {
                "status": "failed",
                "error": (
                    "no Teams service URL is remembered for this conversation; "
                    "someone must message the bot on Teams first"
                ),
            }
        token = await self.connector_token()
        if not token:
            return {"status": "failed", "error": "could not get a Bot Framework connector token"}
        body = {"type": "message", "text": text[:MAX_TEXT]}
        try:
            response = await self.http.post(
                f"{service_url}/v3/conversations/{conversation_id}/activities",
                json=body, headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as exc:
            logger.exception("comms.teams.send_failed")
            return {"status": "failed", "error": str(exc)}
        if response.status_code >= 400:
            # The three refusals this endpoint actually produces are worth
            # separating in the message: `401` means the app password is wrong,
            # `403` means the bot has been removed from the conversation, and
            # `404` means the conversation id is one Microsoft no longer has. All
            # of them come back as a status code with an empty body, so the
            # status is put in the error rather than left to be guessed at.
            detail = _error_detail(response)
            logger.warning("comms.teams.send_refused", status=response.status_code,
                           conversation=conversation_id)
            return {"status": "failed", "error": f"http_{response.status_code}: {detail}",
                    "http_status": response.status_code}
        return {
            "status": "sent",
            "conversation_id": conversation_id,
            "provider_message_id": str(payload_id(response)),
        }


def teams_address(conversation_id: str) -> str:
    """A conversation id as the send path wants it: `teams:<conversationId>`.

    A conversation and not a person, for the reason Slack's is a channel: a reply
    is sent to the `person_id` of the message it answers, so the address has to
    name something the Connector will accept a POST for, and that is the
    conversation. A Teams user belongs to at least two conversations — a personal
    chat and any team they are in — so there is no single user id to name and no
    way for the life loop to pick one without being told.
    """
    return f"teams:{str(conversation_id).strip()}"


def strip_mentions(text: str) -> str:
    """A Teams message without its own bot mention.

    An @mention of the bot arrives as `<at>Name</at>` with the display name the
    app registered, so the tag is stripped rather than matched: the person did
    not type it, the framework did, and leaving it in would put the agent's own
    name at the start of every conversation it is in. A `<at>` with nothing after
    it is left alone rather than deleted, because a half-written tag is somebody
    mid-sentence and quietly deleting the rest of a line is worse than leaving it.
    """
    return MENTION.sub("", text)


def _is_channel_token(claims: dict[str, Any]) -> bool:
    """Whether these claims are a Bot Framework channel token and not another kind.

    The Bot Framework issues several tokens and only one of them is spent on a
    messaging endpoint. A token from the emulator — the one a developer runs
    against with `MicrosoftAppPassword` blank — carries a different issuer and
    exists so a bot can be developed on localhost without a registration. This
    door has an App ID, because an App ID is its audience, so the emulator case
    cannot be served here at all and the refusal is explicit rather than an
    audience mismatch somebody has to decode from a log line.
    """
    return str(claims.get("iss") or "") == ISSUER


def _host_of(service_url: str) -> str:
    """The host of a URL, for a log line naming what was refused."""
    return (urlparse(str(service_url or "")).hostname or "")[:120]


def _error_detail(response: Any) -> str:
    """The Connector's refusal reason, which is usually in no body at all.

    The Connector answers a refused send with a status code and, most of the time,
    nothing else — so this falls back to the status rather than inventing a
    reason, and the adapter's caller puts the status in the error text too.
    """
    def pick(payload: dict[str, Any]) -> Any:
        error = payload.get("error")
        if isinstance(error, dict):
            return error.get("message")
        if isinstance(error, str):
            return error
        return payload.get("message")

    return http_error_detail(response, pick)


def payload_id(response: Any) -> str:
    """The activity id out of a 200, or `''`.

    The Connector answers a send with the activity it accepted, which is the id
    to reply to later. Not used as a threading key — Teams threading is a
    `replyToId` inside the activity rather than a separate endpoint — and not
    usable as one here: `Message.in_reply_to` is a UUID of *this* system's rows,
    and a Teams activity id is Microsoft's, so the two cannot be the same value.
    """
    try:
        payload = response.json()
    except Exception:
        return ""
    return str(payload.get("id") or "") if isinstance(payload, dict) else ""


__all__ = [
    "DEFAULT_SERVICE_HOSTS", "IGNORED_TYPES", "MAX_TEXT", "TeamsAdapter",
    "strip_mentions", "teams_address",
]
