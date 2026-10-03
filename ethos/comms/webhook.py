from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

from ethos.observability.logger import get_logger

logger = get_logger("ethos.comms.webhook")

InboundHandler = Callable[[str, dict[str, Any]], Awaitable[None]]

# A route's answer to "is this body really from the provider". Given the headers
# as they arrived and the raw bytes, because every provider signs the bytes and
# not the parse — see `WebhookRoute.authentic_body`.
Authenticator = Callable[[dict[str, str], bytes], bool]

# A route's answer to "is this the handshake, and what does it want echoed". Given
# the parsed body, and asked *before* the signature is checked, because Slack
# proves a callback URL with an unsigned POST. Returns `None` for everything else.
# See `WebhookReceiver._serve` for why answering one of these is safe.
ChallengeProbe = Callable[[dict[str, Any]], "str | None"]

Handler = Callable[[dict[str, Any]], Awaitable[None]]

# Enough for a signed webhook request and no more. A real one carries a handful
# of headers; a client sending thousands is not one.
MAX_HEADERS = 64


def meta_signature(secret: str) -> Authenticator:
    """Meta's proof: `sha256=<hexdigest>` over the raw body, keyed by app secret.

    A closure rather than a branch inside `authentic_body`, because the comparison
    itself is the part worth being able to test and read on its own, and because
    the second channel to need a signature must not be a second `if` in the same
    method.
    """
    def verify(headers: dict[str, str], raw: bytes) -> bool:
        # Only the sha256 header, and only the sha256 comparison. The v1
        # `x-hub-signature` carries a sha1 digest, and comparing it against a
        # `sha256=` prefix would refuse every v1-signed request anyway — a
        # fallback that can never succeed is not a fallback, it is a claim
        # about support that is not being made.
        signature = headers.get("x-hub-signature-256")
        if not signature:
            return False
        expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        # Constant time, over the whole value: a short-circuiting compare leaks
        # how many leading characters were right.
        return hmac.compare_digest(f"sha256={expected}", signature.strip())

    return verify


class WebhookReceiver:
    """One loopback HTTP listener that push channels are delivered to.

    A channel that polls — Telegram, email — needs nothing but credentials. A
    channel that pushes cannot be polled at all: WhatsApp Cloud API has no
    "give me everything since" call, it POSTs to a URL Meta was told in advance,
    and that URL has to be reachable from the internet. So this is the door such
    a channel knocks on, and it is deliberately the smallest thing that can be
    that door.

    Three properties are non-negotiable, and each is a decision rather than a
    detail:

    **It binds loopback.** Never `0.0.0.0`. Reaching this listener from outside
    the machine is the job of a tunnel — `cloudflared`, `ngrok` — which
    terminates TLS and forwards to a port the operator chose. A listener that
    bound a public interface would need to terminate TLS itself to be usable at
    all, and that is a much larger thing to get right than forwarding bytes.

    **Every body is authenticated before it is parsed.** Each route carries a
    secret, and the signature is checked over the raw bytes. Verifying after
    `json.loads` would be verifying something the sender did not sign: JSON has
    no canonical form, so a parse-and-re-serialize round trip can produce bytes
    that hash the same as the attacker's original while meaning something else.

    **The shared secret is optional per route, and its absence is a refusal, not
    a pass.** `secret=None` exists for the handshake routes, where there is
    nothing signed yet — the GET that proves the URL is yours. A POST route with
    no secret is a configuration mistake, and it answers 503 rather than
    admitting an unauthenticated writer.

    A webhook that returns anything but a fast 2xx is retried by the sender, and
    retried deliveries duplicate inbound messages. Handlers are therefore
    expected to be quick; anything slow belongs on the life loop, not here.

    The proofs are the providers', not this class's. Meta signs the body with a
    secret and proves its callback URL with a `GET`; Slack signs a version
    prefix and a timestamp around the body and proves the same URL with a `POST`
    that asks for a challenge to be echoed. A route is given the scheme it needs
    and gets nothing else, which is why adding a second push channel did not mean
    teaching this listener about Slack.
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_event: InboundHandler,
        max_body_bytes: int = 262144,
    ):
        self.host = host
        self.port = port
        self.on_event = on_event
        self.max_body_bytes = max_body_bytes
        self.routes: dict[str, WebhookRoute] = {}
        self._server: Any = None

    @property
    def started(self) -> bool:
        """Whether this listener currently holds its port.

        Asked rather than assumed, because the one process that opens it and the one
        that only registers routes on it are different processes: a channel that has
        been configured must be able to tell "not mine to open" from "mine, and not
        open yet", and a second `start` on a bound port is the failure this avoids.
        """
        return self._server is not None

    def route(
        self,
        path: str,
        secret: str | None,
        handler: Handler,
        verify_token: str | None = None,
        authentic: Authenticator | None = None,
        challenge: ChallengeProbe | None = None,
    ) -> None:
        self.routes[path] = WebhookRoute(path=path, secret=secret, handler=handler,
                                         verify_token=verify_token, authentic=authentic,
                                         challenge=challenge)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        logger.info("comms.webhook.listening", host=self.host, port=self.port,
                    paths=sorted(self.routes))

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                logger.exception("comms.webhook.close_failed")
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except Exception:
            logger.exception("comms.webhook.handler_failed")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not request_line:
            return
        try:
            method, raw_path, _ = request_line.decode("latin-1").split(" ", 2)
        except ValueError:
            await _respond(writer, 400, "bad_request")
            return

        headers = await _read_headers(reader)

        path, _, query = raw_path.partition("?")
        route = self.routes.get(path)
        if route is None:
            # Before anything is read, so an unknown path cannot be used to make
            # this listener buffer a body it will only discard.
            await _respond(writer, 404, "not_found")
            return

        if method == "GET":
            await self._handle_get(route, query, writer)
            return
        if method != "POST":
            await _respond(writer, 405, "method_not_allowed")
            return

        length = _content_length(headers)
        if length is None or length > self.max_body_bytes:
            await _respond(writer, 413, "body_too_large")
            return
        raw = await reader.readexactly(length) if length else b""

        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            await _respond(writer, 400, "bad_json")
            return
        if not isinstance(payload, dict):
            await _respond(writer, 400, "bad_payload")
            return

        # The handshake, asked before the signature and answered without it.
        #
        # Slack proves a callback URL by POSTing `{"type": "url_verification",
        # "challenge": …}` once, at configuration time — and there is nothing signed
        # about it, because at that moment the operator has not yet been asked for
        # the signing secret in any way this listener could check. Meta asks the
        # same question with a signed token in a GET, which is the `verify_token`
        # path below.
        #
        # Answering an unsigned body is only safe because answering it *does
        # nothing*: the reply is the caller's own string, echoed back, and no
        # handler runs and no message is recorded. So a forged body of this shape
        # gets an echo, which it could have computed itself. The parse here is
        # being used to recognise a no-op, never to decide that a writer is
        # allowed in — that is still `authentic_body`, and it is still below.
        if route.challenge is not None:
            answer = route.challenge(payload)
            if isinstance(answer, str):
                await _respond_plain(writer, 200, answer)
                return

        if not route.authentic_body(headers, raw):
            await _respond(writer, 401, "bad_signature")
            return

        try:
            await route.handler(payload)
        except Exception:
            # The delivery is acknowledged before it is known to have succeeded.
            # An exception here means *this process* failed to hand the message
            # on, and answering non-2xx would make the sender redeliver a message
            # that may already be recorded — a duplicate inbound is worse than a
            # dropped one, because the agent deliberates on both.
            logger.exception("comms.webhook.delivery_failed", path=path)
        await _respond(writer, 200, "ok")

    async def _handle_get(self, route: WebhookRoute, query: str, writer: asyncio.StreamWriter) -> None:
        """The subscribe handshake: a URL proves itself once, by echoing a token.

        This is the one unauthenticated write in the system, and it writes
        nothing. It answers a challenge with the operator's own token, which
        changes nothing about this process; what it establishes is that whoever
        configured the webhook at the provider is the operator, because only they
        know the token.
        """
        params = _parse_query(query)
        if "hub.challenge" not in params:
            await _respond(writer, 404, "not_found")
            return
        if params.get("hub.mode") != "subscribe":
            await _respond(writer, 403, "bad_mode")
            return
        if not route.handshake_ok(params):
            await _respond(writer, 403, "bad_verify_token")
            return
        await _respond_plain(writer, 200, params["hub.challenge"])


class WebhookRoute:
    """One path on the receiver, and the secret that speaks for it.

    Three different proofs, because the providers ask for three different things.
    The handshake is a `GET` carrying a token the operator chose, and it is proved
    by echoing that token. Everything after it is a `POST` whose body Meta signed
    with the app secret, proved by recomputing that signature over the bytes
    exactly as they arrived — and Slack signs the same kind of body the same way
    except that it mixes a timestamp and its own version prefix into the string
    being signed, which is why the scheme is a value on the route rather than a
    method on this class.
    """

    def __init__(
        self,
        path: str,
        secret: str | None,
        handler: Handler,
        verify_token: str | None = None,
        authentic: Authenticator | None = None,
        challenge: ChallengeProbe | None = None,
    ):
        self.path = path
        self.secret = secret
        self.handler = handler
        self.verify_token = verify_token
        self.authenticator = authentic
        self.challenge = challenge

    def authentic_body(self, headers: dict[str, str], raw: bytes) -> bool:
        """Whether a body may become a message.

        A route with no secret answers False. `None` means "there is nothing to
        check against", and treating that as permission would make forgetting a
        secret indistinguishable from configuring one correctly — so the failure
        mode of an unconfigured route is a closed door, not an open one.
        """
        if not self.secret:
            return False
        if self.authenticator is not None:
            return self.authenticator(headers, raw)
        return meta_signature(self.secret)(headers, raw)

    def handshake_ok(self, params: dict[str, str]) -> bool:
        """Whether the subscribe handshake may be answered.

        Constant time because the token is the only thing between this URL and
        anyone who guessed it, and a short-circuiting compare would leak how much
        of a guess was right.
        """
        if not self.verify_token:
            return False
        return hmac.compare_digest(params.get("hub.verify_token", ""), self.verify_token)


async def _read_headers(reader: asyncio.StreamReader) -> dict[str, str]:
    """Headers, lowercased, until the blank line.

    Bounded rather than unbounded on purpose: a client that never sends one can
    hold this reader open, and the only reason this loop is not a `while True`
    on `data` is the readline timeout underneath it.
    """
    headers: dict[str, str] = {}
    for _ in range(MAX_HEADERS):
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        if line in (b"\r\n", b"\n", b""):
            return headers
        name, _, value = line.decode("latin-1").partition(":")
        if name:
            headers[name.strip().lower()] = value.strip()
    return headers


def _content_length(headers: dict[str, str]) -> int | None:
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_query(query: str) -> dict[str, str]:
    from urllib.parse import parse_qs

    return {key: values[0] for key, values in parse_qs(query, keep_blank_values=True).items()}


async def _respond(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    body = json.dumps({"status": reason}).encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"content-type: application/json\r\n"
        f"content-length: {len(body)}\r\n"
        "connection: close\r\n\r\n"
    ).encode("latin-1")
    writer.write(head + body)
    await writer.drain()


async def _respond_plain(writer: asyncio.StreamWriter, status: int, body: str) -> None:
    """A plain-text answer, because the handshake asks for one.

    Meta compares the response body to the challenge as text. JSON-quoting it
    would answer `200` with a body that is not the challenge, which fails
    verification for a reason that looks like a wrong verify token.
    """
    payload = body.encode("utf-8")
    head = (
        f"HTTP/1.1 {status} OK\r\n"
        f"content-type: text/plain\r\n"
        f"content-length: {len(payload)}\r\n"
        "connection: close\r\n\r\n"
    ).encode("latin-1")
    writer.write(head + payload)
    await writer.drain()
