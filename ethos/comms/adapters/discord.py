from __future__ import annotations

import asyncio
import json
from typing import Any

from ethos.comms.adapters.base import ChannelAdapter
from ethos.observability.logger import get_logger
from ethos.schemas.messages import Message

logger = get_logger("ethos.comms.discord")

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
API_BASE = "https://discord.com/api/v10"

# Gateway intent bits, by name, because the numbers are not memorable and a wrong
# one is a bot that is connected and hears nothing.
#
#   MESSAGE_CONTENT is the one that matters most and is the one a portal will
#   have left off: without it Discord delivers message events with empty `content`
#   and no error, which looks exactly like a channel where nobody is speaking.
#   GUILD_MESSAGES covers a server text channel and DIRECT_MESSAGES a DM.
INTENT_GUILD_MESSAGES = 1 << 9
INTENT_DIRECT_MESSAGES = 1 << 12
INTENT_MESSAGE_CONTENT = 1 << 15
DEFAULT_INTENTS = INTENT_GUILD_MESSAGES | INTENT_DIRECT_MESSAGES | INTENT_MESSAGE_CONTENT

# Discord's own limits. A message is 2000 characters and the gateway will not
# carry more, so a longer one is split rather than refused — the agent's replies
# run to several sentences routinely, and silently truncating one is the same lie
# as recording an undelivered send as delivered.
MAX_MESSAGE_CHARS = 2000

# Heartbeat pacing. Discord closes a connection whose heartbeats stop, and it
# asks for the first one to be jittered so that a fleet of clients restarted by
# the same deploy does not arrive as one spike.
HEARTBEAT_JITTER_S = 5.0

# The bounds `_heartbeat_interval` will believe, in seconds. Discord's own number
# lands around 41; a floor of one keeps a hostile `0` from becoming a busy loop
# and a ceiling of 120 keeps a mistaken large value from being zombied by the
# server for looking idle.
MIN_HEARTBEAT_S = 1.0
MAX_HEARTBEAT_S = 120.0
DEFAULT_HEARTBEAT_S = 41.25

# Reconnection backoff bounds. The floor is long enough that a revoked token —
# which fails immediately, every time — does not become a hot loop against
# Discord's rate limiter, and the ceiling is short enough that a network blip is
# not a long outage.
RECONNECT_MIN_S = 2.0
RECONNECT_MAX_S = 60.0


def discord_address(channel: str) -> str:
    """The channel id as the send path wants it: `dc:<id>`.

    A channel id, for the same reason Slack's is: a reply is sent to the
    `person_id` of the message it answers, and a channel is the one identifier
    that says where that answer goes. A DM channel is one-per-person in Discord,
    so for the ordinary case this names a person and a conversation at once; for
    a server channel it names the conversation, which is what a broadcast to a
    room is.
    """
    return f"dc:{str(channel).strip()}"


def _author_name(author: dict[str, Any]) -> str | None:
    """What a Discord user goes by, or `None`.

    `global_name` first and `username` second, and the order is the whole point:
    Discord deprecated the old four-digit `discriminator` scheme, so `username` is
    now a login handle — unique, lowercase-ish, and often a string of digits and
    underscores — while `global_name` is the display name the user actually
    chose. Reading them the other way round gives every sender a handle where a
    name was available, which is the same mistake as reading Slack's `name` before
    its `real_name`.

    Also the one channel that does not need a round trip for this, and the only
    reason the Slack adapter's lookup looks unusual next to it: Discord puts the
    whole `author` object on the `MESSAGE_CREATE` event, so the name of the person
    writing arrives with the message.
    """
    for key in ("global_name", "username"):
        value = author.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


class DiscordAdapter(ChannelAdapter):
    """Discord bot: a gateway socket in, the REST messages endpoint out.

    Discord is the one channel here with neither a long-poll nor a webhook. The
    only way to see a message as it is written is to hold a gateway connection
    open, so `listen` is a real listener here and — like the Telegram poller and
    the webhook port — it can be opened by exactly one process. There is no
    delivery id to deduplicate on, because there are no redeliveries: Discord
    pushes once, and a reconnect starts a fresh session.

    **What a dropped connection costs.** This does not resume a session. A message
    written while the socket was down is not replayed, and a gateway reconnect
    cannot ask for it: that is what `RESUME` is for, and resuming needs a session
    id and a sequence number kept alive across the gap. So the honest statement is
    that this channel has Telegram's property (polling, so nothing is lost while
    the process is merely slow) and not IMAP's (nothing is lost across a
    restart). Messages sent to the agent while it is disconnected are not seen.
    Turning on RESUME would mean holding a sequence number and re-requesting
    everything after it, and a half-correct implementation of that is worse than
    none: it would replay messages the agent already answered.

    The address space is `dc:<channel id>`; see `discord_address`.
    """

    name = "discord"

    def __init__(
        self,
        token: str,
        allowed_ids: list[str] | None = None,
        intents: int = DEFAULT_INTENTS,
        accept_from_anyone: bool = False,
        http: Any = None,
        gateway_url: str = GATEWAY_URL,
        connect: Any = None,
        on_inbound: Any = None,
        application_id: str | None = None,
    ):
        self.token = token
        # As on Slack: an empty allowlist admits nobody, and either kind of id may
        # be on the list because an operator has a user id or a channel id and
        # should not have to work out which half this is.
        self.allowed = {str(value).strip() for value in (allowed_ids or []) if str(value).strip()}
        self.accept_from_anyone = accept_from_anyone
        self.intents = intents
        self.http = http
        self._owns_http = http is None
        self.gateway_url = gateway_url
        # Injectable so a test can stand in a socket and drive frames through the
        # real state machine, rather than asserting on a copy of the logic.
        self._connect = connect
        self.on_inbound = on_inbound
        # The bot's own id, learned at start-up and used to drop its own messages.
        # Discord marks those `author.bot`, which is the usual filter, but an id
        # known in advance is a fact rather than a convention — and it is what lets
        # `parse` be tested without an HTTP call.
        self.application_id = application_id
        self._task: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        # The socket currently held, so the heartbeat can reach it. A task and the
        # thing it is beating are separate lifetimes: the heartbeat starts on
        # HELLO, which is a frame *after* the socket exists, and outlives several
        # sends.
        self._socket: Any = None
        self._closing = False
        # Discord does not redeliver, so there is no dedupe set here. What does
        # need remembering is the connection's own liveness, so `stop` and the
        # status line can both say whether this channel is actually holding a
        # socket rather than merely having been asked to.
        self.connected = False

    # ------------------------------------------------------------------ doors

    async def start(self) -> None:
        """The half that speaks: an HTTP client, and the bot's own identity.

        Deliberately not the gateway. `ethos-core` builds this adapter to reply
        and never polls it, exactly as it does for Telegram, and the identity is
        fetched here because it is what `send` needs to attribute nothing to.
        """
        if self.http is None:
            import httpx

            self.http = httpx.AsyncClient(timeout=20.0)
        self.application_id = await self._whoami()
        logger.info("comms.discord.ready_to_send", allowed=len(self.allowed),
                    application_id=self.application_id, open_to_all=self.accept_from_anyone)

    async def listen(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._closing = False
        self._task = asyncio.create_task(self._run(), name="comms.discord.gateway")

    async def stop(self) -> None:
        self._closing = True
        for task in (self._heartbeat, self._task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._heartbeat, self._task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._heartbeat = None
        self._task = None
        self.connected = False
        if self._owns_http and self.http is not None:
            await self.http.aclose()
        self.http = None

    async def _whoami(self) -> str | None:
        """The application id behind the token, or `None` when the API will not say.

        Never raised and never fatal, for the reason it is not on the send path:
        a token that cannot be read here will not be accepted by `send` either,
        and refusing to start would turn a misconfiguration into a channel that
        is silently absent.
        """
        if self.http is None:
            return None
        try:
            response = await self.http.get(f"{API_BASE}/users/@me", headers=self._headers())
            payload = response.json()
        except Exception:
            logger.exception("comms.discord.whoami_failed")
            return None
        user_id = payload.get("id") if isinstance(payload, dict) else None
        return str(user_id) if isinstance(user_id, str) and user_id else None

    def _headers(self) -> dict[str, str]:
        # `Bot`, not `Bearer`: Discord's own API prefix, and a request that says
        # `Bearer` is refused with a 401 whose body does not mention the reason.
        return {"Authorization": f"Bot {self.token}"}

    # --------------------------------------------------------------- gateway

    async def _run(self) -> None:
        """Hold a gateway connection, for as long as this process is alive.

        A loop rather than a connection: Discord closes idle or unhappy sockets
        on its own schedule, and treating that as the end of the channel would
        mean one routine reconnect was the last one.
        """
        delay = RECONNECT_MIN_S
        while not self._closing:
            try:
                await self._session()
                delay = RECONNECT_MIN_S
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("comms.discord.gateway_failed")
            finally:
                self.connected = False
                await self._stop_heartbeat()
            if self._closing:
                return
            # Logged with the wait in it, so a channel that has been down for a
            # while says so at the level somebody reads rather than only in a
            # traceback nobody opens.
            logger.warning("comms.discord.reconnecting", wait_s=round(delay, 1))
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_S)

    async def _session(self) -> None:
        socket = await self._socket_open()
        try:
            async for raw in socket:
                frame = _decode(raw)
                if frame is None:
                    continue
                op = frame.get("op")
                if op == 10:  # HELLO — the interval to heartbeat at, then identify.
                    self._start_heartbeat(_heartbeat_interval(frame.get("d")))
                    await self._identify(socket)
                elif op == 11:  # HEARTBEAT_ACK
                    continue
                elif op == 1:  # HEARTBEAT — asked for one, right now.
                    await self._identify_payload(socket, op=1)
                elif op == 7:  # RECONNECT — the server wants a new session.
                    raise _Reconnect("gateway asked to reconnect")
                elif op == 9:  # INVALID_SESSION — ours is finished; reconnect fresh.
                    raise _Reconnect("gateway invalidated the session")
                elif op == 0:  # DISPATCH
                    await self._dispatch(frame)
            raise _Reconnect("gateway closed")
        finally:
            await self._socket_close(socket)

    async def _socket_open(self) -> Any:
        socket = await self._socket_factory()
        self._socket = socket
        self.connected = True
        return socket

    async def _socket_close(self, socket: Any) -> None:
        self.connected = False
        if self._socket is socket:
            self._socket = None
        await _aclose(socket)

    async def _socket_factory(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.gateway_url)
        import websockets

        return await websockets.connect(self.gateway_url, max_size=2**20)

    async def _identify(self, socket: Any) -> None:
        await self._identify_payload(socket, op=2)

    async def _identify_payload(self, socket: Any, *, op: int) -> None:
        await socket.send(json.dumps({
            "op": op,
            "d": {
                # The presence block is required by the identify payload's shape
                # and ignored for a bot. Omitting the key is refused; sending
                # `None` for it is what a library that has never met a bot does.
                "token": self.token,
                "intents": self.intents,
                "properties": {"os": "linux", "browser": "ethos", "device": "ethos"},
            },
        }))

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        if frame.get("t") != "MESSAGE_CREATE":
            return
        message = frame.get("d")
        if not isinstance(message, dict):
            return
        parsed = self.parse(message)
        if parsed is None:
            return
        user, channel, provider_id, text, name = parsed
        if not self.accepts(user, channel):
            logger.info("comms.discord.sender_not_allowed", user=user, channel=channel)
            return
        try:
            await self.on_inbound(Message(
                channel=self.name,
                person_id=discord_address(channel),
                text=text,
                conversation_key=f"discord:{channel}",
                attachments=[{"user": user, "message_id": provider_id}],
                sender_name=name,
            ))
        except Exception:
            logger.exception("comms.discord.inbound_failed", channel=channel)

    def _start_heartbeat(self, interval: float) -> None:
        # Cancelled, not awaited-and-then-cancelled: the old task holds a reference
        # to the *previous* socket, and awaiting its shutdown before starting the
        # new one would leave a window in which two tasks are both due to beat.
        self._cancel_heartbeat()
        self._heartbeat = asyncio.create_task(self._beat(interval), name="comms.discord.heartbeat")

    def _cancel_heartbeat(self) -> None:
        task, self._heartbeat = self._heartbeat, None
        if task is not None and not task.done():
            task.cancel()

    async def _stop_heartbeat(self) -> None:
        task = self._heartbeat
        self._cancel_heartbeat()
        if task is None:
            return
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    async def _beat(self, interval: float) -> None:
        """A heartbeat for as long as the socket lives.

        A send that fails is not treated as the end of the connection, because it
        is not one: the socket is still open and the session is still good, and
        tearing it down over one refused write would turn a rate limit into a
        reconnect storm. The task only ends when the socket it is beating is gone.
        """
        # The first beat is jittered and the rest are not. Discord asks for
        # jitter on the first only; a constant offset on every beat is a clock
        # that is only ever right until something else is scheduled on the same
        # second. Derived from the token rather than `hash()`, because salted
        # per-process hashing would make the same deployment beat at a different
        # offset on every restart, which is the opposite of the point.
        offset = (sum(self.token.encode("utf-8")) % 1000) / 1000.0
        await asyncio.sleep(interval + offset * HEARTBEAT_JITTER_S)
        while True:
            socket = self._socket
            if socket is None:
                return
            try:
                await socket.send(json.dumps({"op": 1, "d": None}))
            except Exception:
                return
            await asyncio.sleep(interval)

    # ------------------------------------------------------------- filtering

    def accepts(self, user: str | None, channel: str | None) -> bool:
        """Whether this author, in this conversation, is somebody to answer.

        An empty allowlist admits nobody, for the reason it does on every other
        channel here: a bot token is public to anybody who can see the bot, and an
        agent holding a shell answering whoever found its name is a published
        endpoint.

        `accept_from_anyone` is the deliberate way to say yes to a server nobody
        listed. Discord is the channel where that matters most and is most
        dangerous: a bot that is on one server with `MESSAGE_CONTENT` sees every
        message in every channel it is in, and a per-user allowlist is the only
        thing between that and an agent answering a room it was added to. So it
        is a separate, named switch — never what an empty list happens to mean.
        """
        if self.accept_from_anyone:
            return True
        if not self.allowed:
            return False
        return bool(
            (user and str(user).strip() in self.allowed)
            or (channel and str(channel).strip() in self.allowed)
        )

    def parse(self, message: dict[str, Any]) -> tuple[str, str, str, str, str | None] | None:
        """One dispatched message as `(user, channel, message_id, text, name)`.

        Returns `None` for everything that is not a person talking to this agent,
        and the checks are in order of how cheap they are. A bot's own message is
        first: Discord marks those `author.bot`, and this adapter's own replies
        arrive the same way, so missing it is how an agent ends up answering
        itself in a loop.

        Total rather than raising, for the same reason the WhatsApp parser is: a
        message shape this does not recognise must be skipped, not turned into an
        exception that closes the socket. The type check is here rather than left
        to the caller because "total" is a claim about this function, and a claim
        that holds only when the caller happens to have checked first is not one.
        """
        if not isinstance(message, dict):
            return None
        author = message.get("author")
        if not isinstance(author, dict):
            return None
        if author.get("bot") or author.get("webhook_id"):
            return None
        user = author.get("id")
        channel = message.get("channel_id")
        text = message.get("content")
        if not user or not channel or not isinstance(text, str) or not text.strip():
            return None
        if self.application_id and str(user) == self.application_id:
            return None
        return (
            str(user), str(channel), str(message.get("id") or ""), text.strip(),
            _author_name(author),
        )

    # ------------------------------------------------------------------ send

    async def send(self, *, person_id: str | None, text: str, urgency: str = "normal",
                   conversation_key: str | None = None, in_reply_to: Any = None) -> dict[str, Any]:
        if self.http is None:
            return {"status": "failed", "error": "discord adapter not started"}
        if not person_id or not person_id.startswith("dc:"):
            return {"status": "failed", "error": "discord adapter requires dc: person_id"}
        channel = person_id.split(":", 1)[1].strip()
        if not channel:
            return {"status": "failed", "error": "no destination channel"}
        chunks = _split_message(text)
        sent: list[str] = []
        for chunk in chunks:
            try:
                response = await self.http.post(
                    f"{API_BASE}/channels/{channel}/messages",
                    json={"content": chunk},
                    headers=self._headers(),
                )
            except Exception as exc:
                logger.exception("comms.discord.send_failed", channel=channel)
                return {"status": "failed", "error": str(exc), "sent_chunks": len(sent)}
            if response.status_code >= 400:
                # Discord answers a rate limit with 429 and a retry-after, and a
                # missing scope or a channel the bot is not in with 403 — both are
                # refusals, and both are reported as refusals rather than as a
                # send that failed for an unnamed reason.
                detail = _error_detail(response)
                logger.warning("comms.discord.send_refused", status=response.status_code,
                               detail=detail)
                return {"status": "failed", "error": detail,
                        "http_status": response.status_code, "sent_chunks": len(sent)}
            try:
                payload = response.json()
            except Exception:
                payload = None
            provider_id = ""
            if isinstance(payload, dict) and payload.get("id"):
                provider_id = str(payload["id"])
            sent.append(provider_id)
        return {
            "status": "sent",
            "channel": channel,
            # The last chunk's id, because that is the message a reply to this one
            # should point at. A reader wanting all of them has `sent_chunks`.
            "provider_message_id": sent[-1] if sent else "",
            "sent_chunks": len(sent),
        }


class _Reconnect(Exception):
    """The socket ended and a fresh one is wanted. Not a failure to report."""


def _decode(raw: Any) -> dict[str, Any] | None:
    """One gateway frame, or `None` for anything that is not one.

    `encoding=json` is asked for in the URL, so a frame is text. A binary frame
    would mean a zlib stream nobody asked for, and it is skipped rather than
    parsed: a reader that raises on an unexpected frame is a reader that dies on
    a server adding a field.
    """
    if isinstance(raw, (bytes, bytearray)):
        return None
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return frame if isinstance(frame, dict) else None


def _heartbeat_interval(payload: Any) -> float:
    """How often to beat, in **seconds**, from the HELLO frame.

    Discord sends the interval in milliseconds — `41250` means forty-one seconds —
    and reading it as seconds would put a client on a heartbeat interval six times
    longer than the server asked for. The server's own limit is what a client
    exceeding it is disconnected for, so the two errors here are a bot that is
    zombied within a minute and a bot that busy-loops; both are worth a floor and
    a cap, and both bounds sit well inside Discord's own range so neither costs
    anything.
    """
    value = payload.get("heartbeat_interval") if isinstance(payload, dict) else None
    try:
        milliseconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_S
    if milliseconds <= 0:
        return DEFAULT_HEARTBEAT_S
    return max(MIN_HEARTBEAT_S, min(milliseconds / 1000.0, MAX_HEARTBEAT_S))


def _split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """
    A message, in pieces Discord will accept.

    Split on a line boundary where one is near the cut, because a message broken
    mid-sentence is worse to read than one broken at a sentence, and a paragraph
    is a better place to break than the middle of a word. Only when there is no
    whitespace at all does it fall back to a hard cut — which is a real answer
    rather than a refusal: this is the agent's own words, and dropping them is
    the one outcome nobody chose.
    """
    body = text or ""
    if len(body) <= limit:
        return [body] if body else []
    chunks: list[str] = []
    rest = body
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks


def _error_detail(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"http_{response.status_code}"
    if isinstance(payload, dict):
        # Discord's errors carry a `message` meant for a developer and a `code`.
        # The message is the one that names what is missing, so it is preferred.
        error = payload.get("message")
        if isinstance(error, str) and error:
            return error[:300]
        code = payload.get("code")
        if code:
            return f"discord_error_{code}"
    return f"http_{response.status_code}"


async def _aclose(socket: Any) -> None:
    if socket is None:
        return
    close = getattr(socket, "close", None)
    if close is None:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("comms.discord.socket_close_failed")
