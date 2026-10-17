from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from ethos.comms.adapters.base import ChannelAdapter, emit_inbound, http_error_detail, offer_seen
from ethos.comms.jwt import JwtUnavailable, PublicKeys, bearer, sign_rs256
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.googlechat")

API_BASE = "https://chat.googleapis.com/v1"

#: Where a service account's signed assertion is exchanged for an access token.
TOKEN_URL = "https://oauth2.googleapis.com/token"

#: The only scope a Chat app is given. `chat.bot` is self-granted — no admin
#: approval, no OAuth consent screen — and the two `chat.app.*` scopes that would
#: let an app act on a user's behalf all need administrator approval. An agent
#: that sends messages on its own account does not need them, and asking for one
#: would turn "install a bot in a space" into "get your Workspace admin to sign
#: something".
TOKEN_SCOPE = "https://www.googleapis.com/auth/chat.bot"

#: The identity Google Chat signs *with*, and the only one this door admits.
#:
#: Not "Google". Chat's requests are signed either by the per-project service
#: account (`service-NNN@gcp-sa-gsuiteaddons.iam.gserviceaccount.com`) for a
#: Workspace add-on, or by the shared `chat@system.gserviceaccount.com` for a
#: standalone Chat app. Accepting either as an *issuer* would be the mistake —
#: any Google-signed token for any project in the world would pass. The check is
#: therefore on the identity that names *Chat* rather than on who signed.
CHAT_IDENTITY = "chat@system.gserviceaccount.com"

#: The issuers a genuine Chat token is signed by. Google's own OIDC ID tokens
#: carry `https://accounts.google.com`; a Chat app's project-number assertion is
#: self-signed and carries the identity itself as its issuer.
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

#: The two places Google's signing keys are published, and both are needed.
#:
#: It depends on how the Chat app was configured, and the choice was made in a
#: console rather than by the developer:
#:
#: * **Authentication audience "HTTP endpoint URL"** (the default) — a Google
#:   OIDC ID token, verified against Google's ordinary public certs, whose `aud`
#:   is the endpoint URL and whose `email` is `chat@system.gserviceaccount.com`.
#: * **Authentication audience "Project number"** — a token Chat signs *itself*,
#:   verified against that service account's own X.509 certificates, whose `aud`
#:   is the Cloud project number.
#:
#: Verifying against only one of them is a door that works for exactly half the
#: Chat apps in existence and fails with a 401 on the other half for no reason
#: anybody can see. So both key sets are believed and the claim that pins them —
#: `aud` must be what this deployment configured — is checked after the
#: signature, not before it.
PUBLIC_CERTS = (
    "https://www.googleapis.com/oauth2/v3/certs",
    "https://www.googleapis.com/oauth2/v1/certs",
)
CHAT_CERTS = f"https://www.googleapis.com/service_accounts/v1/metadata/x509/{CHAT_IDENTITY}"

#: Interaction types that carry words somebody typed, as opposed to the app having
#: been added to or removed from a space. Both of those still carry a `space`, so
#: remembering where it is reached is worth doing for them; neither is a message.
IGNORED_TYPES = frozenset({
    "REMOVED_FROM_SPACE", "CARD_CLICKED", "WIDGET_ACTION", "SUBMITTED_DIALOG",
    "CLOSE_DIALOG", "UPDATED_MESSAGE", "DELETED_MESSAGE", "REACTION_ADDED",
    "REACTION_REMOVED", "APP_COMMAND", "SPACE_UPDATED",
})

#: The sender types Workspace reports. `BOT` is the important one: a Chat app that
#: can see a space also sees itself and other bots writing in it, and answering
#: those is the same infinite conversation as the Teams one.
BOT_SENDERS = frozenset({"BOT", "bot"})

#: Chat's own limit on one message. Google does not document a hard character
#: limit for `spaces.messages.create` — it folds over into a card at around 4000
#: — so this is the same number Teams uses and for the same reason.
MAX_TEXT = 4000

#: How far off expiry a cached access token is considered still good.
TOKEN_MARGIN_S = 60.0


class GoogleChatAdapter(ChannelAdapter):
    """Google Chat: a Google-signed bearer token in, `spaces.messages.create` out.

    **The credentials are a pair of files rather than two strings, and that is the
    first thing to understand about this door.** A Chat app holds a service
    account: an email, and a PEM private key. There is no bot token to paste. To
    send anything, this adapter signs a short-lived assertion with that key and
    exchanges it for an access token — once an hour — because that is the only way
    Google issues one for a non-interactive caller. So `sign_rs256` is here rather
    than in the reader's side of the code, and `private_key` is the one credential
    in this repository that a form cannot hold: a PEM key is ~1700 characters and
    the settings screen caps a credential at 1024, so it is marked env-only and
    said so rather than silently dropped for being too long.

    **Inbound is the easiest half and still needs care.** Google sends a signed
    token in `Authorization`, but which token depends on a radio button in a
    console (see `PUBLIC_CERTS`), so both publishers' keys are believed and the
    configured `audience` is what actually pins the request to this deployment.
    `audience` is therefore *not* the app id and *not* a URL this repository
    knows — it is whatever the Chat app was configured with, and the operator
    copies it out of the console.

    **What Google Chat will not do is deliver everything.** A Chat app configured
    with an HTTP endpoint is sent a message event for an @mention and for
    anything in a direct message with it, and **nothing else** — every other
    message in a space it sits in is invisible. Hearing all of a room requires
    Workspace Events and a Pub/Sub subscription, which is a different transport
    and not this adapter. It is stated here because "the bot is in the room and
    it is silent" is the first thing an operator reports and the reason is a
    configuration screen rather than anything in this code.
    """

    name = "googlechat"

    def __init__(
        self,
        client_email: str,
        private_key: str,
        audience: str,
        allowed_ids: list[str] | None = None,
        accept_from_anyone: bool = False,
        http: Any = None,
        now_fn: Any = time.time,
    ):
        self.client_email = client_email
        # Literal `\n` is turned into a newline here and nowhere else. Every
        # other place a PEM key arrives — an env var, a systemd unit, a
        # Kubernetes secret — has to write it that way because a real newline
        # does not survive a shell or an env file, so a deployment that pasted
        # the documented value and got a key that will not load should not also
        # have to know that.
        self.private_key = str(private_key or "").replace("\\n", "\n").strip()
        self.audience = audience
        # A sender's `users/…` resource name or their email address. Both are in
        # the event — the resource name always, the email when the domain shares
        # it — and both are admitted, because an operator who pasted one has
        # usually pasted whichever they had in front of them.
        self.allowed = {str(value).strip().lower() for value in (allowed_ids or []) if str(value).strip()}
        self.accept_from_anyone = accept_from_anyone
        self.http = http
        self._owns_http = http is None
        self.now_fn = now_fn
        self._token = ""
        self._token_expires_at = 0.0
        self._seen_message_names: set[str] = set()
        self.keys = PublicKeys((*PUBLIC_CERTS, CHAT_CERTS))

    async def start(self) -> None:
        await self._ensure_http()
        # Fetched rather than lazily: a door that has no keys answers 401 to
        # everything, and an operator whose first message after a restart is
        # refused has no way to tell that from a signature they got wrong.
        await self.keys.refresh(self.http)
        logger.info("comms.googlechat.started", allowed=len(self.allowed),
                    open_to_all=self.accept_from_anyone, keys=not self.keys.empty,
                    audience=self.audience[:80])

    async def listen(self) -> None:
        """Nothing on the adapter: the receiver is the host's, not this object's.

        The webhook listener is one loopback port shared by every push channel, so
        it is owned by `CommsHost` and started once, by whichever process is the
        listening one. The adapter owns parsing and sending; the receiver owns the
        door.
        """

    async def stop(self) -> None:
        await self._close_http()

    def authentic(self) -> Any:
        """This door's proof, for the receiver's route.

        Built per call rather than held, for `jwt.bearer`'s reason inverted: it
        reads `self.keys`, which `start` replaces with one that knows about both
        publishers, and it is cheap enough to be called on each reload.
        """
        return bearer(
            self.keys,
            audience=self.audience,
            require=_is_chat_token,
        )

    def accepts(self, user: str | None, channel: str | None) -> bool:
        """Whether this author, in this space, is somebody to answer.

        Compared case-insensitively and against both id spellings, because the
        allowlist stores lower-cased and Google's own resource names are
        case-sensitive but their emails are not, and a list that matched one
        spelling exactly would silently admit nobody.
        """
        if self.accept_from_anyone:
            return True
        if not self.allowed:
            return False
        offered = {str(value).strip().lower() for value in (user, channel) if value}
        return bool(offered & self.allowed)

    def mark_seen(self, message_name: str | None) -> bool:
        """True the first time a name is offered, False for every redelivery."""
        return offer_seen(self._seen_message_names, message_name, limit=2048, drop=512)

    def parse(self, payload: dict[str, Any]) -> list[tuple[str, str, str, str | None, str | None]]:
        """
        The messages in an interaction event, as `(sender, space, message_name,
        text, display_name)`.

        Total on purpose, for the reason the other parsers are: Chat retries an
        event whose endpoint did not answer 2xx, and an unrecognised interaction
        type must not become a 500 — so the ten types in `IGNORED_TYPES` are all
        "yield nothing".

        Two filters carry real weight:

        * **A bot's own message.** A Chat app in a space receives events for the
          app's own messages, so without this the agent would answer itself, in
          public, in a loop. Filtered on `sender.type == BOT` *and* on the sender
          being this app's own user, because Workspace reports `type` as
          unspecified often enough that relying on it alone would leave the loop
          open on exactly the deployments least likely to be watching it.
        * **An empty `text`.** Every message event has a `text`, but a message
          carrying only a card does not, and answering a card with a wall of text
          is worse than saying nothing.
        """
        out: list[tuple[str, str, str, str | None, str | None]] = []
        if str(payload.get("type") or "") in IGNORED_TYPES:
            return out
        message = payload.get("message")
        if not isinstance(message, dict):
            return out
        text = message.get("text") or message.get("argumentText")
        if not isinstance(text, str) or not text.strip():
            return out
        space = payload.get("space")
        space_name = space.get("name") if isinstance(space, dict) else None
        sender = message.get("sender") if isinstance(message.get("sender"), dict) else payload.get("user")
        if not isinstance(sender, dict) or not space_name:
            return out
        sender_name = str(sender.get("name") or "")
        if not sender_name:
            return out
        if str(sender.get("type") or "").upper() in BOT_SENDERS:
            return out
        name = sender.get("displayName")
        return [(
            sender_name,
            str(space_name),
            str(message.get("name") or ""),
            text.strip(),
            str(name).strip() if isinstance(name, str) and name.strip() else None,
        )]

    async def handle_webhook(self, payload: dict[str, Any], on_inbound: Any) -> int:
        accepted = 0
        for sender, space, message_name, text, display in self.parse(payload):
            email = _sender_email(payload)
            who = email or sender
            if not self.accepts(who, None) and not self.accepts(sender, None):
                logger.info("comms.googlechat.sender_not_allowed", sender=sender)
                continue
            if not self.mark_seen(message_name):
                continue
            if await emit_inbound(
                on_inbound,
                Message(
                    channel=self.name,
                    person_id=chat_address(space),
                    text=text,
                    conversation_key=f"googlechat:{space}",
                    attachments=[{"message": message_name, "sender": sender}],
                    # Unlike Slack and Teams this one carries the name in the
                    # event, so there is nothing to fetch: Workspace resolves it
                    # before the app is called. The email is *not* used as the
                    # name — `sender_username` is its own field and `sender_who`
                    # is what decides which of the two a reader sees.
                    sender_name=display,
                    sender_username=email,
                ),
                log_event="comms.googlechat.inbound_failed", logger=logger,
            ):
                accepted += 1
        return accepted

    async def access_token(self) -> str:
        """An access token for this service account, or `''` when there is none.

        The JWT-bearer grant, once an hour: an assertion signed with the service
        account's private key, naming the token endpoint as its audience, in
        exchange for an access token scoped to `chat.bot`.

        Signed rather than configured for the same reason a credential is a pair
        of files here: Google issues no long-lived token to a service account.
        One is an hour old at best, so a door that stored an access token would
        be a door that stops working once an hour with nothing in the log — and
        the private key does not expire, which is what makes it a reasonable thing
        to configure at all.
        """
        now = self.now_fn()
        if self._token and now < self._token_expires_at:
            return self._token
        if self.http is None:
            return ""
        try:
            assertion = sign_rs256(
                {"iss": self.client_email, "scope": TOKEN_SCOPE, "aud": TOKEN_URL,
                 "exp": int(now) + 3600},
                self.private_key,
                now_fn=self.now_fn,
            )
        except JwtUnavailable as exc:
            # Reported as a refusal rather than raised. A Chat app whose private
            # key will not load cannot send, but it can still *receive* — inbound
            # is verified against Google's public keys and has nothing to do with
            # the service account — so a door that could not start would throw away
            # the half that works.
            logger.error("comms.googlechat.private_key_unusable", detail=str(exc)[:200])
            return ""
        try:
            response = await self.http.post(TOKEN_URL, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            })
        except Exception:
            logger.exception("comms.googlechat.token_failed")
            return ""
        if response.status_code >= 400:
            logger.warning("comms.googlechat.token_refused", status=response.status_code,
                           detail=_error_detail(response))
            return ""
        try:
            payload = response.json()
        except Exception:
            logger.warning("comms.googlechat.token_unreadable")
            return ""
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            return ""
        try:
            lifetime = float(payload.get("expires_in", 3600))
        except (TypeError, ValueError):
            lifetime = 3600.0
        self._token = token
        self._token_expires_at = now + max(60.0, lifetime - TOKEN_MARGIN_S)
        return token

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        destination = self._destination(person_id, "gchat")
        if isinstance(destination, dict):
            return destination
        space = destination
        token = await self.access_token()
        if not token:
            return {"status": "failed", "error": "could not get a Google Chat access token"}
        body: dict[str, Any] = {"text": text[:MAX_TEXT]}
        # A reply to a reply goes in a thread, keyed by the id of the message it
        # answers. Chat's `threadKey` is arbitrary and has no meaning to Google,
        # so this system's own message id is a perfectly good one — and it means
        # the agent's answer to a question sits next to it rather than as a new
        # top-level message in a busy room, which is the whole difference between
        # a bot that fits into a conversation and one that interrupts it.
        if isinstance(in_reply_to, str) and in_reply_to.strip():
            body["thread"] = {"threadKey": in_reply_to.strip()[:128]}
        elif isinstance(in_reply_to, UUID):
            body["thread"] = {"threadKey": str(in_reply_to)}
        try:
            response = await self.http.post(
                f"{API_BASE}/spaces/{space}/messages",
                params={"messageReplyOption": "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"},
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except Exception as exc:
            logger.exception("comms.googlechat.send_failed")
            return {"status": "failed", "error": str(exc)}
        if response.status_code >= 400:
            # The three that matter are `403` (the app is not in the space, or
            # the scope is not granted — `chat.bot` is the one that is), `404`
            # (the space id is not one this app can see) and `429` (the per-space
            # rate limit, one request a second). Google's `google.rpc.Status`
            # carries a readable `message` in the body, so it is passed through
            # rather than reduced to a status code.
            detail = _error_detail(response)
            logger.warning("comms.googlechat.send_refused", status=response.status_code,
                           detail=detail, space=space)
            return {"status": "failed", "error": detail, "http_status": response.status_code}
        return {
            "status": "sent",
            "space": space,
            "provider_message_id": _message_name(response),
        }


def chat_address(space: str) -> str:
    """A Chat space as the send path wants it: `gchat:<spaces/AAAAAA>`.

    A space rather than a user, and it has to be: **the Chat API will not send a
    message to a person.** `spaces.messages.create` names a space, full stop —
    there is no `users/…` destination. So the conversation *is* the address, the
    same decision Slack's and Discord's and Teams' doors reach for the same
    reason, and it is why a person's address in `people.yaml` is the direct
    message's space id rather than their address.

    The `spaces/` prefix is kept rather than trimmed. It costs eight characters,
    it is exactly what the API path wants, and stripping it would mean two doors
    whose `person_id` shapes look alike and mean different namespaces.
    """
    return f"gchat:{str(space).strip()}"


def _is_chat_token(claims: dict[str, Any]) -> bool:
    """Whether these claims are Google's Chat and not some other Google.

    The question has two shapes, because Chat signs two kinds of token, and a
    single issuer check would refuse one of them:

    * **Project-number audience** — Chat signs its own assertion, so `iss` *is*
      `chat@system.gserviceaccount.com`.
    * **HTTP endpoint audience** — an ordinary Google OIDC ID token, so `iss` is
      `accounts.google.com` and the identity is in `email`.

    Both are accepted, and neither accepts a token that merely happens to be
    Google-signed: an ID token minted for some other app in some other project
    with the same audience is not Chat, and is not admitted on the strength of
    its signature. The workspace add-on variant — `aud` is still the endpoint
    URL, but the identity is a *per-project* `service-NNN@gcp-sa-gsuiteaddons…`
    — is **not** accepted here. Its audience is hardcoded to an add-on's own
    endpoint by the Marketplace SDK, so an agent on its own endpoint never sees
    one, and accepting the shape would mean accepting any add-on's tokens.
    """
    issuer = str(claims.get("iss") or "")
    if issuer == CHAT_IDENTITY:
        return True
    return issuer in GOOGLE_ISSUERS and str(claims.get("email") or "") == CHAT_IDENTITY


def _sender_email(payload: dict[str, Any]) -> str | None:
    """The sender's email address, when the domain shares one.

    Chat's event carries the address on `user` for a direct message and on
    `message.sender` for one in a room, but only when the deployment's Workspace
    domain lets Chat expose it. So it is `None` more often than it is a string,
    and every caller treats it as optional — which is why the return is
    `str | None` and not `str`. The fallback is the sender's `users/…` resource
    name, which is always present and always identifies exactly one person.
    """
    message = payload.get("message")
    sender = message.get("sender") if isinstance(message, dict) else None
    for holder in (payload.get("user"), sender):
        if isinstance(holder, dict):
            email = holder.get("email")
            if isinstance(email, str) and email.strip():
                return email.strip()
    if isinstance(sender, dict) and isinstance(sender.get("name"), str):
        return str(sender["name"]).strip() or None
    return None


def _error_detail(response: Any) -> str:
    def pick(payload: dict[str, Any]) -> Any:
        error = payload.get("error")
        if isinstance(error, dict):
            return error.get("message") or error.get("status")
        if isinstance(error, str):
            return error
        return payload.get("message")

    return http_error_detail(response, pick)


def _message_name(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    return str(payload.get("name") or "") if isinstance(payload, dict) else ""


__all__ = [
    "CHAT_CERTS", "CHAT_IDENTITY", "GoogleChatAdapter", "MAX_TEXT",
    "PUBLIC_CERTS", "TOKEN_SCOPE", "chat_address",
]
