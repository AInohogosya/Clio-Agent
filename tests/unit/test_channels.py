from __future__ import annotations

import asyncio
import errno
import hashlib
import hmac
import json
import socket
import sys
from typing import Any

import pytest

from ethos.comms.adapters import telegram as telegram_adapter_module
from ethos.comms.adapters.telegram import TelegramAdapter, _display_name, _handle
from ethos.comms.adapters.whatsapp import WhatsappAdapter, normalize_phone
from ethos.comms.webhook import WebhookReceiver, meta_signature
from ethos.schemas.messages import Message

APP_SECRET = "app-secret-for-tests"
VERIFY_TOKEN = "verify-token-for-tests"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def signed(raw: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    """Headers Meta would send: an HMAC-SHA256 over the body, base the server checks."""
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {
        "x-hub-signature-256": f"sha256={digest}",
        "content-type": "application/json",
        "content-length": str(len(raw)),
    }


async def post(port: int, path: str, raw: bytes, headers: dict[str, str]) -> tuple[int, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"POST {path} HTTP/1.1\r\nhost: 127.0.0.1\r\n"
    head += "".join(f"{name}: {value}\r\n" for name, value in headers.items())
    head += "\r\n"
    writer.write(head.encode("latin-1") + raw)
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5)
    writer.close()
    status = int(response.split(b" ", 2)[1])
    return status, response.decode("latin-1", errors="replace")


async def get(port: int, path: str) -> tuple[int, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nhost: 127.0.0.1\r\n\r\n".encode("latin-1"))
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5)
    writer.close()
    status = int(response.split(b" ", 2)[1])
    return status, response.decode("latin-1", errors="replace")


def wa_body(text: str = "hello there", sender: str = "819012345678", mid: str = "wamid.ABC") -> bytes:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "contacts": [{"profile": {"name": "Someone"}, "wa_id": sender}],
                    "messages": [{"from": sender, "id": mid, "timestamp": "1700000000",
                                  "type": "text", "text": {"body": text}}],
                },
            }],
        }],
    }).encode()


# --------------------------------------------------------------- the receiver


async def test_a_signed_body_reaches_the_route_handler():
    """The end the whole door exists for: verified bytes become a parsed payload."""
    delivered: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        delivered.append(payload)

    raw = b'{"a":1}'
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=APP_SECRET, verify_token=VERIFY_TOKEN, handler=handler)
    await receiver.start()
    try:
        status, _ = await post(port, "/hooks/test", raw, signed(raw))
    finally:
        await receiver.stop()
    assert status == 200
    assert delivered == [{"a": 1}]


async def test_unsigned_body_is_refused():
    delivered: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        delivered.append(payload)

    raw = b'{"entry":[]}'
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=APP_SECRET, handler=handler)
    await receiver.start()
    try:
        status, _ = await post(port, "/hooks/test", raw,
                               {"content-length": str(len(raw))})
    finally:
        await receiver.stop()
    assert status == 401
    assert delivered == []


async def test_wrong_secret_is_refused():
    delivered: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        delivered.append(payload)

    raw = b'{"entry":[]}'
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=APP_SECRET, handler=handler)
    await receiver.start()
    try:
        status, _ = await post(port, "/hooks/test", raw, signed(raw, secret="wrong"))
    finally:
        await receiver.stop()
    assert status == 401
    assert delivered == []


async def test_route_without_a_secret_is_a_closed_door():
    """An unconfigured route answers 401, not 200.

    The failure mode of forgetting a secret has to be a closed door. If it were
    an open one, `enabled: true` with a missing environment variable would
    publish an endpoint that writes to the agent's bus with no authentication at
    all — and it would look healthy.
    """
    delivered: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        delivered.append(payload)

    raw = b'{"entry":[]}'
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=None, handler=handler)
    await receiver.start()
    try:
        status, _ = await post(port, "/hooks/test", raw, signed(raw))
    finally:
        await receiver.stop()
    assert status == 401
    assert delivered == []


async def test_signature_covers_the_bytes_not_the_parse():
    """Re-serializing the body changes the signature.

    Which is the point: a receiver that verified after `json.loads` would be
    verifying something the sender never signed, since JSON has no canonical
    form. A duplicate key is the cheapest way to show the two bytes apart.
    """
    delivered: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        delivered.append(payload)

    signed_raw = b'{"a":1,"a":2}'
    other_raw = b'{"a":2}'
    # Same signature, carried over a different body. The content-length is the
    # one for the body actually sent, or the receiver would block waiting for
    # bytes that never come and the refusal would be a timeout.
    headers = {**signed(signed_raw), "content-length": str(len(other_raw))}
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=APP_SECRET, handler=handler)
    await receiver.start()
    try:
        first, _ = await post(port, "/hooks/test", signed_raw, signed(signed_raw))
        # Signed for `signed_raw`, carrying `other_raw`'s meaning.
        second, _ = await post(port, "/hooks/test", other_raw, headers)
    finally:
        await receiver.stop()
    assert first == 200
    assert second == 401
    assert len(delivered) == 1


async def test_unknown_path_is_404():
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=APP_SECRET, handler=lambda p: asyncio.sleep(0))
    await receiver.start()
    try:
        status, _ = await post(port, "/hooks/nope", b"{}", signed(b"{}"))
    finally:
        await receiver.stop()
    assert status == 404


async def test_body_over_the_cap_is_refused():
    raw = b'{"a":1}'
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0), max_body_bytes=4)
    receiver.route("/hooks/test", secret=APP_SECRET, handler=lambda p: asyncio.sleep(0))
    await receiver.start()
    try:
        status, _ = await post(port, "/hooks/test", raw, signed(raw))
    finally:
        await receiver.stop()
    assert status == 413


async def test_handshake_echoes_the_challenge():
    """The one unauthenticated exchange, and it writes nothing.

    Meta compares the response body to the challenge as text, so the answer has
    to be the bare challenge rather than JSON — a `200` whose body is quoted
    fails verification for a reason that looks like a wrong verify token.
    """
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=APP_SECRET, verify_token=VERIFY_TOKEN,
                   handler=lambda p: asyncio.sleep(0))
    await receiver.start()
    try:
        ok, body = await get(
            port,
            f"/hooks/test?hub.mode=subscribe&hub.verify_token={VERIFY_TOKEN}&hub.challenge=31415",
        )
        bad, _ = await get(
            port, "/hooks/test?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=31415",
        )
        not_sub, _ = await get(
            port, f"/hooks/test?hub.mode=other&hub.verify_token={VERIFY_TOKEN}&hub.challenge=1",
        )
        no_challenge, _ = await get(
            port, f"/hooks/test?hub.mode=subscribe&hub.verify_token={VERIFY_TOKEN}",
        )
    finally:
        await receiver.stop()
    assert ok == 200
    assert body.endswith("31415")
    assert bad == 403
    assert not_sub == 403
    assert no_challenge == 404


async def test_a_failing_handler_still_answers_2xx():
    """A delivery this process failed to hand on is not answered with an error.

    Non-2xx makes the sender redeliver, and a redelivery that *does* get recorded
    is a duplicate inbound — worse than a dropped one, because the agent
    deliberates on both.
    """
    async def handler(payload: dict[str, Any]) -> None:
        raise RuntimeError("the downstream is down")

    raw = b'{"entry":[]}'
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route("/hooks/test", secret=APP_SECRET, handler=handler)
    await receiver.start()
    try:
        status, _ = await post(port, "/hooks/test", raw, signed(raw))
    finally:
        await receiver.stop()
    assert status == 200


# ------------------------------------------------------------------ whatsapp


def test_phone_is_normalized_to_e164_digits():
    """One person, one spelling.

    The same number arrives as `+81 90-1234-5678` from a profile and as bare
    digits from a webhook. Two spellings would be two `person_id`s — a split
    relationship record and two interruption budgets for one human.
    """
    assert normalize_phone("+81 90-1234-5678") == "819012345678"
    assert normalize_phone("819012345678") == "819012345678"
    assert normalize_phone("(090) 1234-5678") == "09012345678"


def test_parse_finds_the_message_in_the_nested_body():
    adapter = WhatsappAdapter("1", "token", [])
    found = adapter.parse(json.loads(wa_body(text="are you there")))
    assert found == [("819012345678", "wamid.ABC", "are you there", "Someone")]


def test_parse_reads_the_senders_display_name_off_the_same_body():
    """The name was in the payload the whole time.

    `contacts` sits beside `messages` in the same `value`, keyed by the same
    number as `messages[].from`, so the WhatsApp display name a sender set for
    themselves arrives in the very body this parser was already reading. It was
    discarded, and the owner of a business number was recorded as
    `wa:819012345678` and nothing else — which is why admitting somebody to a
    WhatsApp door meant transcribing a phone number out of a webhook body.
    """
    adapter = WhatsappAdapter("1", "token", [])
    body = json.loads(wa_body(text="hi", sender="+81 90-1234-5678"))
    assert adapter.parse(body) == [
        ("+81 90-1234-5678", "wamid.ABC", "hi", "Someone"),
    ]


def test_a_message_from_a_contact_meta_could_not_name_has_no_name():
    """`None` rather than an invented one.

    `contacts` is optional in Meta's schema and a profile may carry no name, so
    both cases reach here. What must not happen is the number being passed off as
    a name — a row reading `from 819012345678` is a person who does not exist.
    """
    adapter = WhatsappAdapter("1", "token", [])
    body = json.loads(wa_body(text="hi"))
    value = body["entry"][0]["changes"][0]["value"]
    value.pop("contacts")
    assert adapter.parse(body) == [("819012345678", "wamid.ABC", "hi", None)]

    body = json.loads(wa_body(text="hi"))
    body["entry"][0]["changes"][0]["value"]["contacts"][0]["profile"]["name"] = "   "
    assert adapter.parse(body) == [("819012345678", "wamid.ABC", "hi", None)]


def test_parse_ignores_status_receipts():
    """A delivery receipt is not a message.

    Meta puts `statuses` next to `messages` on the same value, and a receipt has
    no text. Treating one as a message would put the agent's own deliveries back
    in front of it as inbound words from a person.
    """
    adapter = WhatsappAdapter("1", "token", [])
    body = {
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messages": [],
                    "statuses": [{"id": "wamid.X", "status": "delivered",
                                  "recipient_id": "819012345678",
                                  "timestamp": "1700000000"}],
                },
            }],
        }],
    }
    assert adapter.parse(body) == []


@pytest.mark.parametrize("payload", [
    {},
    {"entry": []},
    {"entry": [{}]},
    {"entry": [{"changes": [{}]}]},
    {"entry": [{"changes": [{"value": {}}]}]},
    {"entry": [{"changes": [{"value": {"messages": [{}]}}]}]},
    {"entry": [{"changes": [{"value": {"messages": [{"from": "1"}]}}]}]},
    {"entry": "not-a-list"},
    {"entry": [None, 3]},
])
def test_parse_is_total(payload):
    """An unknown shape yields nothing rather than raising.

    A webhook that answers 500 on a shape it does not recognize is a webhook the
    sender retries forever, so an unknown field has to cost nothing.
    """
    assert WhatsappAdapter("1", "token", []).parse(payload) == []


def test_non_text_messages_carry_no_text_and_are_dropped():
    adapter = WhatsappAdapter("1", "token", [])
    body = json.loads(wa_body())
    body["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": "819012345678", "id": "wamid.IMG", "type": "image",
        "image": {"id": "media-1"},
    }
    assert adapter.parse(body) == []


def test_allowlist_admits_nobody_when_empty():
    """The default is a closed door.

    An unfiltered business number answers every stranger who finds it, and the
    failure is silent — the messages arrive, they just go to whoever asked.
    """
    adapter = WhatsappAdapter("1", "token", [])
    assert adapter.accepts("819012345678") is False


def test_allowlist_admits_only_listed_numbers():
    adapter = WhatsappAdapter("1", "token", ["+81 90-1234-5678"])
    assert adapter.accepts("819012345678") is True
    assert adapter.accepts("81909999999") is False


def test_redelivery_is_recorded_once():
    """Meta redelivers anything it did not see a 2xx for.

    A redelivery that became a second inbound row is one message the agent
    deliberates on twice — two replies, and an interruption budget spent twice.
    """
    adapter = WhatsappAdapter("1", "token", [])
    assert adapter.mark_seen("wamid.ABC") is True
    assert adapter.mark_seen("wamid.ABC") is False
    assert adapter.mark_seen("wamid.OTHER") is True


def test_seen_ids_are_bounded():
    adapter = WhatsappAdapter("1", "token", [])
    for i in range(5000):
        adapter.mark_seen(f"wamid.{i}")
    assert len(adapter._seen_message_ids) <= 2048


async def test_webhook_body_reaches_the_inbound_handler():
    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        received.append(message)

    adapter = WhatsappAdapter("1", "token", ["819012345678"])
    accepted = await adapter.handle_webhook(json.loads(wa_body(text="hi")), on_inbound)
    assert accepted == 1
    assert len(received) == 1
    assert received[0].channel == "whatsapp"
    assert received[0].person_id == "wa:819012345678"
    assert received[0].conversation_key == "whatsapp:819012345678"
    assert received[0].text == "hi"
    # The name Meta put in the same body, carried through to the agent.
    assert received[0].sender_name == "Someone"
    assert received[0].sender_who() == "Someone (wa:819012345678)"


def test_whatsapp_can_be_told_to_answer_anybody():
    """The door where there is no list to write.

    A business number is published, so "whoever found it" is a population that
    cannot be enumerated — which is why this is a setting and not a longer
    allowlist. Still opt-in, because the difference between an agent that talks to
    the public and a business number that replies to every stranger is a decision
    and not a default.
    """
    assert WhatsappAdapter("1", "token", [], accept_from_anyone=True).accepts(
        "819012345678") is True
    assert WhatsappAdapter("1", "token", []).accepts("819012345678") is False
    # A listed number is still listed: this adds everybody, it does not narrow.
    assert WhatsappAdapter("1", "token", ["819012345678"], accept_from_anyone=True).accepts(
        "819012345679") is True


async def test_unlisted_sender_is_not_recorded_at_all():
    """Not merely unanswered — absent from the transcript.

    A stranger's message written to `messages` and then deliberately ignored is
    still something the agent will read, deliberate about, and count a silence
    against, on every restart until it ages out.
    """
    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        received.append(message)

    adapter = WhatsappAdapter("1", "token", ["81909999999"])
    accepted = await adapter.handle_webhook(json.loads(wa_body(sender="819012345678")), on_inbound)
    assert accepted == 0
    assert received == []


async def test_a_handler_that_raises_does_not_stop_the_batch():
    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        if message.text == "boom":
            raise RuntimeError("downstream")
        received.append(message)

    adapter = WhatsappAdapter("1", "token", ["819012345678"])
    payload = {
        "entry": [{
            "id": "0",
            "changes": [{
                "value": {
                    "messages": [
                        {"from": "819012345678", "id": "wamid.1", "text": {"body": "boom"}},
                        {"from": "819012345678", "id": "wamid.2", "text": {"body": "after"}},
                    ],
                },
            }],
        }],
    }
    accepted = await adapter.handle_webhook(payload, on_inbound)
    assert accepted == 1
    assert [m.text for m in received] == ["after"]


class FakeResponse:
    def __init__(self, status_code: int, payload: Any):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeHttp:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, json: Any, headers: Any) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response

    async def aclose(self) -> None:
        pass


async def test_send_posts_to_graph_with_the_bearer_token():
    http = FakeHttp(FakeResponse(200, {"messages": [{"id": "wamid.SENT"}]}))
    adapter = WhatsappAdapter("12345", "secret-token", [], http=http)
    result = await adapter.send(person_id="wa:819012345678", text="hello")
    assert result["status"] == "sent"
    assert result["provider_message_id"] == "wamid.SENT"
    call = http.calls[0]
    assert call["url"] == "https://graph.facebook.com/v23.0/12345/messages"
    assert call["headers"]["Authorization"] == "Bearer secret-token"
    assert call["json"]["to"] == "819012345678"
    assert call["json"]["text"]["body"] == "hello"


async def test_a_refusal_is_reported_as_failed_with_its_reason():
    """Outside the 24-hour window Meta refuses free text, and it must not read as sent.

    A transcript that says "sent" for a message nobody received is the specific
    lie this repository's reply-path tests exist to prevent — and on WhatsApp the
    refusal is a normal outcome, not an outage, so it has to be legible as one.
    """
    http = FakeHttp(FakeResponse(400, {
        "error": {"code": 131056, "message": "Re-engagement message",
                  "error_user_msg": "Message failed to send because more than 24 hours"},
    }))
    adapter = WhatsappAdapter("12345", "t", [], http=http)
    result = await adapter.send(person_id="wa:819012345678", text="hello")
    assert result["status"] == "failed"
    assert "24 hours" in result["error"]


async def test_send_refuses_a_person_id_that_is_not_a_number():
    http = FakeHttp(FakeResponse(200, {}))
    adapter = WhatsappAdapter("12345", "t", [], http=http)
    result = await adapter.send(person_id="tg:42", text="hello")
    assert result["status"] == "failed"
    assert http.calls == []


# ------------------------------------------------------------------ telegram


class FakeChat:
    def __init__(self, chat_id: int, chat_type: str = "private"):
        self.id = chat_id
        self.type = chat_type


def test_telegram_allowlist_admits_nobody_when_empty():
    assert TelegramAdapter("t", []).accepts(FakeChat(42)) is False


def test_telegram_allowlist_admits_a_listed_chat():
    assert TelegramAdapter("t", [42]).accepts(FakeChat(42)) is True
    assert TelegramAdapter("t", [42]).accepts(FakeChat(43)) is False


def test_telegram_refuses_groups_even_when_listed():
    """A group is not a conversation.

    Everyone's words arrive as one sender and one reply goes back to all of
    them — a broadcast with a reply-to in it. So a listed group is still refused
    unless the operator says otherwise.
    """
    assert TelegramAdapter("t", [42], allow_groups=False).accepts(
        FakeChat(42, "supergroup")) is False
    assert TelegramAdapter("t", [42], allow_groups=True).accepts(
        FakeChat(42, "supergroup")) is True


def test_telegram_can_be_told_to_answer_anybody():
    """The setting an agent meant for the public needs, and it is opt-in.

    Not an empty-list convention. `allowed_chat_ids` is empty on a fresh install
    and on a deployment that has deliberately listed nobody, and those are opposite
    states — so "answer whoever finds this bot" has to be written down rather than
    inferred from a list nobody has filled in yet.
    """
    open_door = TelegramAdapter("t", [], accept_from_anyone=True)
    assert open_door.accepts(FakeChat(42)) is True
    assert open_door.accepts(FakeChat(999999, "supergroup")) is False

    # Still narrow by default, with a token in hand.
    assert TelegramAdapter("t", []).accepts(FakeChat(42)) is False
    # And a listed chat is still listed: turning this on adds everybody rather than
    # narrowing to whoever is already on the list.
    assert TelegramAdapter("t", [42], accept_from_anyone=True).accepts(FakeChat(43)) is True


class FakeUser:
    """The slice of a Telegram `User` this adapter reads.

    A real object rather than a dict because that is what `python-telegram-bot`
    hands the handler, and a dict would be a second parser for a shape the library
    has already decided.
    """

    def __init__(self, first_name: str | None = None, last_name: str | None = None,
                 username: str | None = None):
        self.first_name = first_name
        self.last_name = last_name
        self.username = username


def test_telegram_names_the_sender_rather_than_only_the_chat():
    """The point of the whole change, stated as two assertions about one object.

    `update.effective_user` is on every update Telegram sends. Reading only
    `effective_chat.id` is why a Telegram message reached the agent as
    `tg:819012345678` and why admitting its own owner to a Telegram door meant
    transcribing a number out of a chat.
    """
    assert _display_name(FakeUser("Ada", "Lovelace")) == "Ada Lovelace"
    assert _handle(FakeUser("Ada", "Lovelace", "ada")) == "ada"


def test_telegram_survivors_of_an_account_that_has_no_name():
    """`None` rather than a placeholder.

    Telegram's `first_name` is required but can be one character or a row of emoji,
    and a user may set a `@handle` and nothing else. Rendering either as a name
    would be inventing a person, and an empty string would reach the prompt as a
    sender who exists and has no name.
    """
    assert _display_name(None) is None
    assert _display_name(FakeUser("   ")) is None
    assert _display_name(FakeUser("Ada")) == "Ada"
    assert _handle(FakeUser("Ada")) is None
    # A handle stored with its `@` reads back without one, because Telegram's own
    # field has none and `sender_label` is what puts the `@` back in.
    assert _handle(FakeUser("Ada", username="@ada")) == "ada"


def test_telegram_deduplicates_replayed_updates():
    """A restart mid-turn makes Telegram redeliver.

    Without this, one "good morning" becomes two inbound rows and the agent
    answers it twice.
    """
    adapter = TelegramAdapter("t", [42])
    assert adapter.mark_seen_update(100) is True
    assert adapter.mark_seen_update(100) is False
    assert adapter.mark_seen_update(101) is True


def test_telegram_seen_updates_are_bounded():
    adapter = TelegramAdapter("t", [42])
    for i in range(9000):
        adapter.mark_seen_update(i)
    assert len(adapter._seen_updates) <= 4096


async def test_telegram_send_refuses_a_foreign_person_id():
    adapter = TelegramAdapter("t", [42])
    result = await adapter.send(person_id="wa:819012345678", text="hi")
    assert result["status"] == "failed"


# ------------------------------------------------- one listener per deployment


def _core_style_config(monkeypatch: pytest.MonkeyPatch, telegram: bool = True):
    """The shipped config with one polling channel on and no credentials needed.

    `ethos-core` and `ethos-comms` both build their adapters from one file, which
    is the whole reason this split has to hold rather than being a convention.
    """
    from ethos.config import TelegramChannelConfig, load_config

    config = load_config("config")
    config.channels.cli.enabled = False
    config.channels.web.enabled = False
    config.channels.whatsapp.enabled = False
    config.channels.email.enabled = False
    config.channels.telegram = TelegramChannelConfig(enabled=telegram, token_env="TEST_TG_TOKEN")
    monkeypatch.setenv("TEST_TG_TOKEN", "test-token")
    return config


class RecordingAdapter:
    """An adapter that reports which halves of itself were opened."""

    name = "recording"

    def __init__(self) -> None:
        self.started = False
        self.listening = False

    async def start(self) -> None:
        self.started = True

    async def listen(self) -> None:
        self.listening = True

    async def stop(self) -> None:
        return None

    async def send(self, **_: Any) -> dict[str, Any]:
        return {"status": "sent"}


def _host_with(config: Any, adapter: RecordingAdapter) -> Any:
    """A host whose *plan* is one recording adapter, so `start` can be observed on it.

    Substituted at the plan rather than by writing into `host.adapters`, because
    `start` now decides what to build: an adapter the plan does not name is one it
    replaces before it looks at it. The invariant under test is unchanged — which
    half of an adapter a given process opens — and this is the seam that reaches it.
    """
    from ethos.comms.host import CommsHost, _Plan

    host = CommsHost(config, on_inbound=lambda _m: asyncio.sleep(0))
    plan = _Plan(adapters={"recording": adapter}, signatures={"recording": ("recording",)})
    host.plan = lambda _channels: plan  # type: ignore[method-assign]
    return host


async def test_the_listening_process_opens_every_door(monkeypatch: pytest.MonkeyPatch):
    adapter = RecordingAdapter()
    host = _host_with(_core_style_config(monkeypatch), adapter)
    await host.start(listen=True)
    try:
        assert (adapter.started, adapter.listening) == (True, True)
    finally:
        await host.stop()


async def test_the_answering_process_opens_no_door(monkeypatch: pytest.MonkeyPatch):
    """`ethos-core` starts every channel to send on it and listens on none.

    This is the arrangement the channel feature needs. Both processes build the
    same adapters from the same file, so a listener opened by both is not two
    listeners — it is one listener and one permanent failure, which on Telegram
    means a `getUpdates` conflict that keeps the bot deaf while every log line
    says the channel started.
    """
    adapter = RecordingAdapter()
    host = _host_with(_core_style_config(monkeypatch), adapter)
    await host.start(listen=False)
    try:
        assert adapter.started is True, "it has to be able to answer on this door"
        assert adapter.listening is False, "and it must not open it a second time"
    finally:
        await host.stop()


async def test_a_door_only_the_listening_process_opens_stays_shut_for_the_others(monkeypatch: pytest.MonkeyPatch):
    """The webhook port is the same claim as a poller, and belongs to the host.

    WhatsApp has no polling at all, so the receiver is the only way anything ever
    arrives on that channel — and it is one loopback port. Two receivers for it is
    one receiver and an `EADDRINUSE` in a log, which reads as a channel that is not
    being spoken to.

    Asserted against a real receiver rather than a stand-in, because the claim worth
    making is both halves of it: the answering process still needs the routes, so it
    knows where a reply goes, and it must not have taken the port to get them.
    """
    from ethos.comms.host import CommsHost

    config = _slack_style_config(monkeypatch, enabled=True)
    host = CommsHost(config, on_inbound=lambda _m: asyncio.sleep(0))
    await host.start(listen=False)
    try:
        assert host.receiver is not None, "it registers the routes it will send on"
        assert sorted(host.receiver.routes) == ["/hooks/slack"]
        assert host.receiver.started is False, "and it must not bind the port"
    finally:
        await host.stop()


class _PortTaken(RecordingAdapter):
    """An adapter whose door somebody else is already holding."""

    def __init__(self) -> None:
        super().__init__()
        self.why = errno.EADDRINUSE

    async def start(self) -> None:
        raise OSError(self.why, "Address already in use")


async def test_a_door_the_other_process_holds_is_not_logged_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """The one failure this arrangement is *for*, said as what it is.

    `ethos-core` builds the same adapters `ethos-comms` does and opens none of the
    doors, so `EADDRINUSE` on the web socket is the design working: this process
    was told to answer, and `ethos-comms` is already answering. It was logged as
    `adapter_start_failed` with a full traceback on every start of every agent,
    which is how a wall of red in the log teaches somebody to stop reading the log
    — and the log is where the start-up failures worth reading are.
    """
    from structlog.testing import capture_logs

    host = _host_with(_core_style_config(monkeypatch, telegram=False), _PortTaken())
    with capture_logs() as logs:
        await host.start(listen=False)
    try:
        events = [entry.get("event") for entry in logs]
        assert "comms.adapter_door_held_elsewhere" in events
        assert "comms.adapter_start_failed" not in events
    finally:
        await host.stop()


async def test_a_door_nobody_is_holding_is_still_a_failure(monkeypatch: pytest.MonkeyPatch):
    """The quietening is for the designed case only.

    In the process that *is* meant to hold the doors, a port that is taken means
    something else has it: a second comms, a stale process, a different program on
    the port. That is a real fault, and it keeps the loud line.
    """
    from structlog.testing import capture_logs

    host = _host_with(_core_style_config(monkeypatch, telegram=False), _PortTaken())
    with capture_logs() as logs:
        await host.start(listen=True)
    try:
        events = [entry.get("event") for entry in logs]
        assert "comms.adapter_start_failed" in events
        assert "comms.adapter_door_held_elsewhere" not in events
    finally:
        await host.stop()


async def test_email_only_fetches_when_it_is_listening():
    """One mailbox fetched twice is one mailbox half-read.

    `ethos-core` builds the SMTP sender because it has to be able to answer by
    email; the IMAP loop belongs to the process that listens, because the second
    login to a mailbox sees nothing that has not already been marked seen and
    reports no reason for it.
    """
    from ethos.comms.adapters.email import EmailAdapter

    adapter = EmailAdapter("a@b.c", "pw", "imap", "smtp", 60, lambda _m: asyncio.sleep(0))
    await adapter.start()
    try:
        assert adapter._task is None, "starting to send must not open a mailbox"
    finally:
        await adapter.stop()
    await adapter.listen()
    try:
        assert adapter._task is not None
    finally:
        await adapter.stop()


def _email_bytes(sender: str, subject: str = "hello", body: str = "are you there") -> bytes:
    return (
        f"From: {sender}\r\n"
        f"To: agent@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body}\r\n"
    ).encode()


def test_email_names_the_sender_from_the_from_header():
    """A name that was parsed since before this adapter existed, and thrown away.

    `parseaddr` splits `From` into a display name and an address, and this line has
    been assigning both since the first version: the name half went into a local
    variable nothing read. So every email reached the agent as a bare address.
    """
    from ethos.comms.adapters.email import EmailAdapter

    adapter = EmailAdapter("a@b.c", "pw", "imap", "smtp", 60, lambda _m: asyncio.sleep(0))
    message = adapter._parse_email(_email_bytes("Ada Lovelace <ada@example.com>"))
    assert message is not None
    assert message.sender_name == "Ada Lovelace"
    # The address is untouched, because that is what a reply is sent to.
    assert message.person_id == "email:ada@example.com"
    assert message.conversation_key == "email:ada@example.com"


def test_email_does_not_pass_an_address_off_as_a_name():
    """The distinction between a name and something that looks like one.

    Mail clients send `From: alice@example.com` constantly, and rendering that as a
    person's name is the difference between "the agent knows who wrote to it" and
    "the agent knows a string that looks like a name". So it is `None`, and the
    address — which is already `person_id` — is what the agent sees.
    """
    from ethos.comms.adapters.email import EmailAdapter, _header_name

    assert _header_name("alice@example.com", "alice@example.com") is None
    assert _header_name("", "alice@example.com") is None
    assert _header_name(None, "") is None
    # The quotes are RFC 5322 syntax, not part of the name.
    assert _header_name('"Ada Lovelace"', "ada@example.com") == "Ada Lovelace"
    assert _header_name("Ada Lovelace", "ada@example.com") == "Ada Lovelace"

    adapter = EmailAdapter("a@b.c", "pw", "imap", "smtp", 60, lambda _m: asyncio.sleep(0))
    bare = adapter._parse_email(_email_bytes("alice@example.com"))
    assert bare is not None
    assert bare.sender_name is None
    assert bare.sender_who() == "email:alice@example.com"


class _FakeUpdater:
    def __init__(self) -> None:
        self.polling = 0
        self.stopped = 0

    async def start_polling(self, **_kwargs: Any) -> None:
        self.polling += 1

    async def stop(self) -> None:
        self.stopped += 1


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return type("Sent", (), {"message_id": 1})()


class _FakeApplication:
    """Enough of a Telegram `Application` to see which half was reached.

    Stands in for the SDK so the real `TelegramAdapter.start` and `.listen` run
    against it: what is under test is which of the two calls the long-poll, and
    that is only visible by counting `start_polling` on the object they receive.
    """

    def __init__(self) -> None:
        self.bot = _FakeBot()
        self.updater = _FakeUpdater()
        self.initialized = 0
        self.started = 0
        self.handlers: list[Any] = []
        # Every timeout the builder was given, so a test can check that the door
        # is bounded rather than trusting the adapter's own comment about it.
        self.timeouts: dict[str, float] = {}

    @classmethod
    def _builder(cls, app: _FakeApplication) -> Any:
        def _timeout(name: str) -> Any:
            def setter(_self: Any, value: float) -> Any:
                app.timeouts[name] = value
                return builder
            return setter

        builder = type("B", (), {
            "token": lambda _self, _token: builder,
            "connect_timeout": _timeout("connect"),
            "read_timeout": _timeout("read"),
            "write_timeout": _timeout("write"),
            "pool_timeout": _timeout("pool"),
            "build": lambda _self: app,
        })()
        return builder

    def add_handler(self, handler: Any) -> None:
        self.handlers.append(handler)

    async def initialize(self) -> None:
        self.initialized += 1

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class _FakeTelegramModule:
    """The `telegram` package as the adapter imports it.

    The handlers are kept rather than swallowed, because the callback the adapter
    builds inside `start` is the only place the sender's name is read at all —
    `update.effective_user` does not survive into anything a test could inspect
    afterwards. Holding the callback is what lets a real inbound message be driven
    through the real handler rather than through a copy of its body.
    """

    def __init__(self, app: _FakeApplication) -> None:
        self.app = app
        self.callbacks: list[Any] = []
        # All four names live on `ext`, because that is where the adapter asks for
        # them: `Application`, `MessageHandler`, `filters` and `CommandHandler` are
        # `telegram.ext` names and never top-level ones. A double that put them on
        # `telegram` instead would let the adapter's old `from telegram import ...`
        # pass while the real import failed on a machine with the library
        # installed — which is the bug this correction is about.
        self.ext = type("Ext", (), {
            "Application": type("A", (), {
                "builder": staticmethod(lambda: _FakeApplication._builder(app)),
            }),
            "MessageHandler": self._handler,
            "filters": type("F", (), {"TEXT": 1, "COMMAND": 2})(),
            "CommandHandler": lambda *_a, **_k: object(),
        })()

    def _handler(self, *_args: Any, **_kwargs: Any) -> Any:
        callback = _args[-1] if _args else None
        if callable(callback):
            self.callbacks.append(callback)
        return object()


class _FakeTgMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, update_id: int, chat_id: int, text: str,
                 user: Any = None, chat_type: str = "private") -> None:
        self.update_id = update_id
        self.effective_message = _FakeTgMessage(text)
        self.effective_chat = FakeChat(chat_id, chat_type)
        self.effective_user = user


async def _started_telegram(monkeypatch: pytest.MonkeyPatch, adapter: TelegramAdapter,
                            module: _FakeTelegramModule) -> None:
    app = _FakeApplication()
    module.app = app
    module.callbacks = []
    monkeypatch.setitem(sys.modules, "telegram", module)
    monkeypatch.setitem(sys.modules, "telegram.ext", module.ext)
    await adapter.start()


async def test_a_telegram_message_arrives_with_the_name_of_the_person_who_sent_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """The thing this change is for, end to end through the real handler.

    Not asserted on the adapter's helper: the only place `effective_user` is read is
    inside the closure `start` builds, so a test that calls `_display_name` directly
    would pass with the wiring that actually matters removed. So the real handler is
    driven with a real `Update`-shaped object and what it hands the bus is checked.
    """
    received: list[Message] = []
    module = _FakeTelegramModule(_FakeApplication())

    async def on_inbound(message: Message) -> None:
        received.append(message)

    adapter = TelegramAdapter("t", [819012345678], on_inbound=on_inbound)
    await _started_telegram(monkeypatch, adapter, module)

    handle = module.callbacks[-1]
    await handle(_FakeUpdate(
        1, 819012345678, "are you there?",
        user=FakeUser("Ada", "Lovelace", username="ada"),
    ), None)

    assert len(received) == 1
    message = received[0]
    # The address is untouched, because it is what a reply is sent to.
    assert message.person_id == "tg:819012345678"
    assert message.conversation_key == "telegram:819012345678"
    # And the agent now has a name to read rather than eleven digits.
    assert message.sender_name == "Ada Lovelace"
    assert message.sender_username == "ada"
    assert message.sender_who() == "Ada Lovelace (@ada, tg:819012345678)"


async def test_a_telegram_group_message_names_its_author_not_the_room(
    monkeypatch: pytest.MonkeyPatch,
):
    """Which is why the name is read per message rather than per chat.

    In a group `person_id` is the chat, so every member shares one address and one
    reply going to all of them. The author is the only thing that distinguishes
    them, and a name taken from the chat would be the same name for everybody in
    it.
    """
    received: list[Message] = []
    module = _FakeTelegramModule(_FakeApplication())
    adapter = TelegramAdapter("t", [-1001234567890], allow_groups=True,
                              on_inbound=received.append)
    await _started_telegram(monkeypatch, adapter, module)
    handle = module.callbacks[-1]
    await handle(_FakeUpdate(1, -1001234567890, "hello", user=FakeUser("Grace"),
                             chat_type="supergroup"), None)
    assert received[0].sender_name == "Grace"
    assert received[0].person_id == "tg:-1001234567890"


async def test_a_telegram_stranger_is_answered_when_the_door_is_open(
    monkeypatch: pytest.MonkeyPatch,
):
    """`accept_from_anyone` reaching the message, not only `accepts`.

    The flag on an adapter is a setting; a message arriving from a chat that is on
    no list is the thing that setting is for. Also asserted on the refusal path:
    the same stranger on a narrow door is turned away with the id to be added,
    because silence from a bot somebody just added is indistinguishable from a
    broken one.
    """
    received: list[Message] = []
    module = _FakeTelegramModule(_FakeApplication())
    public = TelegramAdapter("t", [], accept_from_anyone=True, on_inbound=received.append)
    await _started_telegram(monkeypatch, public, module)
    await module.callbacks[-1](_FakeUpdate(1, 4242, "hello", user=FakeUser("Ada")), None)
    assert [m.person_id for m in received] == ["tg:4242"]

    narrow = TelegramAdapter("t", [42], on_inbound=received.append)
    await _started_telegram(monkeypatch, narrow, module)
    update = _FakeUpdate(2, 4242, "hello", user=FakeUser("Ada"))
    before = len(received)
    await module.callbacks[-1](update, None)
    assert len(received) == before, "a chat on no list is not recorded at all"
    assert any("4242" in reply for reply in update.effective_message.replies), (
        "the refusal has to carry the id the allowlist wants"
    )


async def test_the_long_poll_is_the_listening_half(monkeypatch: pytest.MonkeyPatch):
    """`start` opens the bot; only `listen` starts asking Telegram for updates.

    The adapter's own `start` and `listen` run, against a stand-in application,
    because the split is the whole claim: `ethos-core` has to end up able to
    *send* on Telegram — a reply to a question asked there is what leaves through
    this bot — without ever issuing a `getUpdates`. A second long-poll is not a
    second reader, it is a `409 Conflict` for as long as the first process lives,
    and the channel then receives nothing while every log line says it started.
    """
    app = _FakeApplication()
    fake = _FakeTelegramModule(app)
    monkeypatch.setitem(sys.modules, "telegram", fake)
    monkeypatch.setitem(sys.modules, "telegram.ext", fake.ext)

    adapter = TelegramAdapter("t", [42])
    await adapter.start()
    try:
        assert app.initialized == 1 and app.started == 1, "the bot client is live"
        assert app.updater.polling == 0, "starting to send must not open a poller"
        assert adapter._polling is False

        # Because a door that cannot be closed quickly is a settings screen that
        # looks broken, every timeout the builder is handed has to be a number the
        # adapter chose rather than the library's own default.
        assert app.timeouts == {
            "connect": 10.0,
            "read": telegram_adapter_module.POLL_READ_TIMEOUT_S,
            "write": 10.0,
            "pool": 5.0,
        }
        assert app.timeouts["read"] <= telegram_adapter_module.STOP_TIMEOUT_S

        result = await adapter.send(person_id="tg:42", text="answered")
        assert result["status"] == "sent"
        assert app.bot.sent == [{"chat_id": 42, "text": "answered"}]

        await adapter.listen()
        assert app.updater.polling == 1, "the listening process opens the poller"
        assert adapter._polling is True

        # Twice, because a host that reopens a door must not open it twice.
        await adapter.listen()
        assert app.updater.polling == 1
    finally:
        await adapter.stop()


# --------------------------------------------------------------------- slack

SLACK_SECRET = "slack-signing-secret-for-tests"


def slack_headers(raw: bytes, secret: str = SLACK_SECRET, stamp: int = 1_700_000_000) -> dict[str, str]:
    """The headers Slack would send, over the string Slack actually signs."""
    base = b":".join([b"v0", str(stamp).encode(), raw])
    digest = hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return {
        "x-slack-signature": f"v0={digest}",
        "x-slack-request-timestamp": str(stamp),
        "content-type": "application/json",
        "content-length": str(len(raw)),
    }


def slack_event(
    text: str = "hello there",
    user: str = "U024BE7LH",
    channel: str = "C024BE7LR",
    event_id: str = "Ev0PV52K21",
    **event: Any,
) -> bytes:
    return json.dumps({
        "type": "event_callback",
        "event_id": event_id,
        "team_id": "T024BE7LD",
        "event": {"type": "message", "user": user, "channel": channel,
                  "text": text, "ts": "1700000000.000100", **event},
    }).encode()


def test_slack_allowlist_admits_nobody_when_empty():
    """Same rule as every other channel here, and the same reason.

    A Slack bot token is not a secret to the workspace's members, so a bot with no
    allowlist answers whoever adds it — and an agent holding a shell answering
    strangers is a published endpoint rather than a convenience.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    assert SlackAdapter("xoxb-t", []).accepts("U1", "C1") is False
    assert SlackAdapter("xoxb-t", ["U1"]).accepts("U1", "C1") is True
    assert SlackAdapter("xoxb-t", ["U1"]).accepts("U2", "C1") is False


def test_slack_can_be_told_to_answer_anybody():
    """The same opt-in as everywhere else, and for the same reason.

    A Slack door set up for a whole workspace has no list of everybody in it, so
    this is the setting that makes the channel usable at all — and it is still not
    something an empty list may imply.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    assert SlackAdapter("xoxb-t", [], accept_from_anyone=True).accepts("U9", "C9") is True
    assert SlackAdapter("xoxb-t", []).accepts("U9", "C9") is False
    # Either kind of id still admits, so a listed person is not locked out by this.
    assert SlackAdapter("xoxb-t", ["U1"], accept_from_anyone=True).accepts("U1", "C9") is True


class _RecordingHttp:
    """The one API call Slack makes for a name, counted so the cache can be seen.

    A real `httpx` client would do; this records the calls instead of making them,
    because the thing under test is *how many* of them there are and a client that
    actually dialled Slack would turn that into a network test.
    """

    def __init__(self, profile: Any = None, status_code: int = 200) -> None:
        self.calls: list[str] = []
        self._profile = profile if profile is not None else {"ok": True, "user": {"real_name": "Ada Lovelace"}}
        self.status_code = status_code

    async def get(self, url: str, **_kwargs: Any) -> Any:
        self.calls.append(url)
        return FakeResponse(self.status_code, self._profile)

    async def post(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("this test is about the name lookup")


async def test_slack_names_the_sender_by_asking_slack_once():
    """The one channel whose inbound payload carries no name at all.

    A `message` event holds `user` and nothing else; the profile behind it is a
    separate `users.info` call. So this adapter spends a request to learn who is
    writing, and the cache is what keeps that affordable — a busy channel would
    otherwise spend one API call per message on an answer that does not change.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    received: list[Message] = []
    http = _RecordingHttp()
    adapter = SlackAdapter("xoxb-t", ["U024BE7LH"], http=http, self_user_id="U9SELF")
    await adapter.start()
    try:
        assert adapter.parse(json.loads(slack_event())) != []
        for index in range(3):
            body = slack_event(text=f"hello {index}", event_id=f"Ev{index}")
            await adapter.handle_webhook(json.loads(body), received.append)
    finally:
        await adapter.stop()

    assert [m.sender_name for m in received] == ["Ada Lovelace"] * 3
    # One call, not three. The user id does not change between messages.
    assert len(http.calls) == 1
    # `person_id` stays the channel, because that is what a reply is sent to, and
    # the author stays in `attachments` where it always was.
    assert received[0].person_id == "slack:C024BE7LR"
    assert received[0].attachments == [{"user": "U024BE7LH", "ts": "1700000000.000100"}]


async def test_slack_falls_back_to_the_id_when_it_may_not_ask():
    """`missing_scope` is the normal case, and it must cost nothing after the first.

    Naming senders on Slack needs `users:read`. An app without it is refused
    `missing_scope` for every message, and a per-message retry loop for an answer
    that will never arrive is worse than having no name at all — it is an API call
    on the path of every inbound message, forever. So the refusal is remembered.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    received: list[Message] = []
    http = _RecordingHttp({"ok": False, "error": "missing_scope"})
    adapter = SlackAdapter("xoxb-t", ["U024BE7LH"], http=http, self_user_id="U9SELF")
    await adapter.start()
    try:
        for index in range(3):
            await adapter.handle_webhook(
                json.loads(slack_event(text=f"hi {index}", event_id=f"Ev{index}")),
                received.append,
            )
    finally:
        await adapter.stop()

    assert [m.sender_name for m in received] == [None, None, None]
    assert len(http.calls) == 1, "a refusal has to be remembered, not retried"
    # And the message is still delivered, addressed as it always was. A missing name
    # costs a name, not a conversation.
    assert [m.person_id for m in received] == ["slack:C024BE7LR"] * 3


async def test_a_slack_user_lookup_that_raises_is_not_an_inbound_failure():
    """The name is a nice-to-have and is treated as one.

    `users.info` going wrong — a timeout, a dropped connection — must not cost the
    message, and must not take the whole webhook handler down with it.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    class BrokenHttp:
        async def get(self, *_args: Any, **_kwargs: Any) -> Any:
            raise TimeoutError("slack is slow")

        async def post(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("this test is about the name lookup")

    received: list[Message] = []
    adapter = SlackAdapter("xoxb-t", ["U024BE7LH"], http=BrokenHttp(), self_user_id="U9SELF")
    await adapter.start()
    try:
        await adapter.handle_webhook(json.loads(slack_event()), received.append)
    finally:
        await adapter.stop()
    assert len(received) == 1
    assert received[0].sender_name is None


async def test_the_slack_name_cache_is_bounded():
    """The one cache here that grows with strangers rather than with redeliveries.

    The dedupe sets grow with retries and are bounded for that reason; this one
    grows with the number of *distinct people* who have ever written, and a door
    set to `accept_from_anyone` is precisely the door that meets an unbounded
    number of them. Unbounded, it is a slow leak created by the setting that adds
    it.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    http = _RecordingHttp()
    adapter = SlackAdapter("xoxb-t", [], accept_from_anyone=True, http=http)
    await adapter.start()
    try:
        for index in range(SlackAdapter.NAME_CACHE_LIMIT + 500):
            await adapter.user_name(f"U{index}")
    finally:
        await adapter.stop()
    assert len(adapter._names) <= SlackAdapter.NAME_CACHE_LIMIT


def test_slack_allowlist_admits_either_a_person_or_a_conversation():
    """An operator has a user id or a channel id and should not have to know which.

    The two are different halves of one fact — who may talk to the agent, and where
    — and the reply path needs the second while the filter needs the first. A
    deployment should be able to allowlist whichever it has.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    by_user = SlackAdapter("x", ["U1"])
    assert by_user.accepts("U1", "C9") is True
    by_channel = SlackAdapter("x", ["C1"])
    assert by_channel.accepts("U9", "C1") is True
    assert by_channel.accepts("U1", "C2") is False


def test_the_slack_signature_covers_a_version_a_timestamp_and_the_body():
    """The three things that are not the body are the two that make it expire.

    Slack signs `v0:<timestamp>:<body>` rather than the body alone, so the same
    bytes signed at two different seconds are two different signatures. A receiver
    that recomputed over the body on its own would refuse every real request.
    """
    from ethos.comms.adapters.slack import slack_signature

    verify = slack_signature(SLACK_SECRET, now_fn=lambda: 1_700_000_000.0)
    raw = b'{"type":"event_callback"}'
    assert verify(slack_headers(raw), raw) is True

    # A signature is bound to the timestamp it was made at. Headers that carry one
    # computed for 1_700_000_000 but claim 1_700_000_030 are a body rewritten to
    # sit inside someone else's window, and they do not verify.
    mixed = slack_headers(raw)
    mixed["x-slack-request-timestamp"] = str(1_700_000_030)
    assert verify(mixed, raw) is False

    # And to the bytes it was made over, which is the property the timestamp was
    # added *around* rather than instead of.
    assert verify(slack_headers(raw), b'{"type":"event_callback","x":1}') is False


def test_a_replayed_slack_request_expires():
    """A captured request is not a permanent credential.

    The timestamp is in the signature precisely so that it can be refused once it
    is old. Without the check the signature is worth nothing to anybody who ever
    saw one valid POST — a proxy log, a packet capture — and the agent would
    deliberate on the same message again.
    """
    from ethos.comms.adapters.slack import MAX_SIGNATURE_AGE_S, slack_signature

    raw = b'{"type":"event_callback"}'
    sent_at = 1_700_000_000
    headers = slack_headers(raw, stamp=sent_at)
    just_inside = slack_signature(SLACK_SECRET, now_fn=lambda: sent_at + MAX_SIGNATURE_AGE_S - 1)
    long_after = slack_signature(SLACK_SECRET, now_fn=lambda: sent_at + MAX_SIGNATURE_AGE_S + 1)
    assert just_inside(headers, raw) is True
    assert long_after(headers, raw) is False


def test_a_slack_request_with_no_usable_timestamp_is_refused():
    """A malformed timestamp is not treated as "now".

    Otherwise the one request that could never expire would be the one carrying no
    timestamp at all — which is exactly the request a forger would send.
    """
    from ethos.comms.adapters.slack import slack_signature

    verify = slack_signature(SLACK_SECRET, now_fn=lambda: 1_700_000_000.0)
    for stamp in ("", "not-a-number", "1e9"):
        headers = {"x-slack-signature": "v0=deadbeef", "x-slack-request-timestamp": stamp}
        assert verify(headers, b"{}") is False


def test_slack_parse_ignores_edits_bots_and_its_own_messages():
    """Three ways a message is not somebody talking to this agent.

    `message_changed` is a message already delivered arriving again under a new
    name, and `me_message` is this bot's own `chat.postMessage` coming back. The
    last one is the sharp: without it every reply the agent sends is handed
    straight back to it as an inbound message, and the two ends answer each other
    until somebody notices.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    adapter = SlackAdapter("xoxb-t", ["U1"], self_user_id="U9SELF")
    assert adapter.parse(json.loads(slack_event())) != []
    assert adapter.parse(json.loads(slack_event(subtype="message_changed"))) == []
    assert adapter.parse(json.loads(slack_event(subtype="me_message"))) == []
    assert adapter.parse(json.loads(slack_event(bot_id="B1"))) == []
    # Even with no `bot_id` and no subtype: the API told us our own user id.
    assert adapter.parse(json.loads(slack_event(user="U9SELF"))) == []


@pytest.mark.parametrize("payload", [
    {},
    {"type": "event_callback"},
    {"type": "event_callback", "event": "not-an-object"},
    {"type": "event_callback", "event": {"type": "reaction_added"}},
    {"type": "event_callback", "event": {"type": "message", "text": "no user"}},
    {"type": "url_verification", "challenge": "abc"},
    {"type": "url_verification"},
    {"entry": "not-an-object"},
])
def test_slack_parse_is_total(payload):
    """A body shape this does not know yields nothing rather than an exception.

    A webhook that answers 500 on an unrecognised event is one Slack retries
    forever, so an unknown field has to cost nothing — and the URL check arriving
    in the same envelope as a real event is a shape this has to survive rather
    than reject.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    adapter = SlackAdapter("xoxb-t", ["U1"])
    assert adapter.parse(payload) == []


def test_a_redelivered_slack_event_is_recorded_once():
    from ethos.comms.adapters.slack import SlackAdapter

    adapter = SlackAdapter("xoxb-t", ["U1"])
    assert adapter.mark_seen("Ev1") is True
    assert adapter.mark_seen("Ev1") is False
    assert adapter.mark_seen(None) is True


async def test_a_signed_slack_event_reaches_the_inbound_handler():
    """The end the whole door exists for, over the real receiver and a real socket."""
    from ethos.comms.adapters.slack import SlackAdapter, slack_signature

    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        received.append(message)

    adapter = SlackAdapter("xoxb-t", ["U1"], now_fn=lambda: 1_700_000_000.0)
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route(
        "/hooks/slack",
        secret=SLACK_SECRET,
        authentic=slack_signature(SLACK_SECRET, now_fn=lambda: 1_700_000_000.0),
        challenge=adapter.challenge,
        handler=lambda payload: adapter.handle_webhook(payload, on_inbound),
    )
    await receiver.start()
    try:
        raw = slack_event(text="are you there", user="U1", channel="C1")
        status, _ = await post(port, "/hooks/slack", raw, slack_headers(raw))
    finally:
        await receiver.stop()
    assert status == 200
    assert [m.text for m in received] == ["are you there"]
    # The address is the conversation, because a reply has to go back to it.
    assert received[0].person_id == "slack:C1"
    assert received[0].channel == "slack"


async def test_an_unsigned_slack_event_is_refused():
    from ethos.comms.adapters.slack import SlackAdapter, slack_signature

    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        received.append(message)

    adapter = SlackAdapter("xoxb-t", ["U1"])
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route(
        "/hooks/slack",
        secret=SLACK_SECRET,
        authentic=slack_signature(SLACK_SECRET),
        challenge=adapter.challenge,
        handler=lambda payload: adapter.handle_webhook(payload, on_inbound),
    )
    await receiver.start()
    try:
        raw = slack_event(user="U1", channel="C1")
        status, _ = await post(port, "/hooks/slack", raw, {"content-length": str(len(raw))})
    finally:
        await receiver.stop()
    assert status == 401
    assert received == []


async def test_the_url_verification_challenge_is_echoed_with_no_signature():
    """Slack proves a callback URL with an unsigned POST, and the answer is text.

    There is nothing signed about it — at that moment the operator has not been
    asked for the signing secret in any way this listener could check — so the
    receiver answers before the signature check. It is safe because answering it
    does nothing: the reply is the caller's own string, echoed, and no handler
    runs.
    """
    from ethos.comms.adapters.slack import SlackAdapter, slack_signature

    ran: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        ran.append(payload)

    adapter = SlackAdapter("xoxb-t", ["U1"])
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route(
        "/hooks/slack",
        secret=SLACK_SECRET,
        authentic=slack_signature(SLACK_SECRET),
        challenge=adapter.challenge,
        handler=handler,
    )
    await receiver.start()
    try:
        raw = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
        status, response = await post(port, "/hooks/slack", raw, {"content-length": str(len(raw))})
    finally:
        await receiver.stop()
    assert status == 200
    assert "abc123" in response
    assert ran == [], "the handshake must not reach the handler"


async def test_a_signed_slack_send_posts_to_the_web_api():
    from ethos.comms.adapters.slack import SlackAdapter

    http = FakeHttp(FakeResponse(200, {"ok": True, "message": {"ts": "1700.0002"}}))
    adapter = SlackAdapter("xoxb-token", [], http=http)
    result = await adapter.send(person_id="slack:C1", text="hello")
    assert result["status"] == "sent"
    assert result["provider_message_id"] == "1700.0002"
    call = http.calls[0]
    assert call["url"] == "https://slack.com/api/chat.postMessage"
    assert call["headers"]["Authorization"] == "Bearer xoxb-token"
    assert call["json"] == {"channel": "C1", "text": "hello"}


async def test_a_slack_200_that_says_not_ok_is_a_refusal():
    """`chat.postMessage` answers 200 with `ok: false` for every refusal it has.

    A send that only read the status code would record `channel_not_found`,
    `not_in_channel` and a missing `chat:write` scope as delivered, and a surface
    that believes a message arrived when it did not is the specific lie this whole
    class of check exists to prevent.
    """
    from ethos.comms.adapters.slack import SlackAdapter

    http = FakeHttp(FakeResponse(200, {"ok": False, "error": "not_in_channel"}))
    adapter = SlackAdapter("xoxb-token", [], http=http)
    result = await adapter.send(person_id="slack:C1", text="hello")
    assert result["status"] == "failed"
    assert result["error"] == "not_in_channel"


async def test_slack_send_refuses_a_person_id_from_another_channel():
    from ethos.comms.adapters.slack import SlackAdapter

    http = FakeHttp(FakeResponse(200, {"ok": True}))
    adapter = SlackAdapter("xoxb-token", [], http=http)
    result = await adapter.send(person_id="tg:42", text="hello")
    assert result["status"] == "failed"
    assert http.calls == [], "a foreign id must not be posted anywhere"


def test_a_slack_thread_is_used_only_when_the_timestamp_is_one():
    """`thread_ts` is a channel-local stamp, so a wrong one files a reply nowhere.

    It is only used when the caller actually carried a string shaped like a Slack
    timestamp; anything else leaves the reply top-level, which is visible, rather
    than filing it under a thread that does not exist.
    """
    from ethos.comms.adapters.slack import _thread_stamp

    assert _thread_stamp("1700000000.000100") == "1700000000.000100"
    assert _thread_stamp("1700000000.1") == ""
    assert _thread_stamp("not-a-timestamp") == ""
    assert _thread_stamp(None) == ""
    assert _thread_stamp(12345) == ""


# ------------------------------------------------------------------- discord

DISCORD_TOKEN = "discord-bot-token-for-tests"


def hello_frame(interval_ms: int = 41_250) -> dict[str, Any]:
    return {"op": 10, "d": {"heartbeat_interval": interval_ms, "_trace": []}}


def dispatch(text: str = "hello there", user: str = "1234", channel: str = "9999", **message: Any) -> dict[str, Any]:
    return {
        "op": 0,
        "t": "MESSAGE_CREATE",
        "d": {"id": "5000", "content": text, "channel_id": channel,
              "author": {"id": user, "bot": False}, **message},
    }


class FakeSocket:
    """A gateway socket, close enough to drive the real state machine.

    The frames are the protocol and nothing else: the adapter's own frame
    handling, its identify and its heartbeat all run against this, which is the
    only way a test can tell the difference between a channel that connects and a
    channel that hears.
    """

    def __init__(self, frames: list[dict[str, Any]]):
        self.frames = list(frames)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def __aiter__(self) -> Any:
        return self._frames()

    async def _frames(self) -> Any:
        for frame in self.frames:
            yield json.dumps(frame)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        self.closed = True


def test_discord_allowlist_admits_nobody_when_empty():
    from ethos.comms.adapters.discord import DiscordAdapter

    assert DiscordAdapter("t", []).accepts("1", "9") is False
    assert DiscordAdapter("t", ["1"]).accepts("1", "9") is True
    assert DiscordAdapter("t", ["1"]).accepts("2", "9") is False


def test_a_bot_message_is_never_inbound():
    """Discord marks its own messages `author.bot`, and this adapter's replies too.

    Missing that check is how an agent ends up answering itself in a loop, and it
    is the first thing to be tested because it is the cheapest: it costs one
    boolean read before any parsing happens at all.
    """
    from ethos.comms.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(DISCORD_TOKEN, ["1234"])
    assert adapter.parse({"author": {"id": "1234", "bot": True}, "channel_id": "9",
                          "content": "mine"}) is None
    assert adapter.parse({"author": {"id": "1234", "webhook_id": "7"}, "channel_id": "9",
                          "content": "mine"}) is None
    assert adapter.parse({"author": {"id": "1234"}, "channel_id": "9", "content": "  "}) is None
    assert adapter.parse({"content": "no author", "channel_id": "9"}) is None
    assert adapter.parse({"id": "5000", "author": {"id": "1234"},
                          "channel_id": "9", "content": "hi"}) == ("1234", "9", "5000", "hi", None)


def test_discord_parse_drops_its_own_application_id():
    """The id the API reports is a fact; `author.bot` is a convention.

    A bot whose own id is known can drop itself without trusting that a flag was
    set, which matters because an empty `content` on every event is also what a
    missing `MESSAGE_CONTENT` intent looks like.
    """
    from ethos.comms.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(DISCORD_TOKEN, ["1234"], application_id="1234")
    assert adapter.parse({"author": {"id": "1234"}, "channel_id": "9", "content": "mine"}) is None


@pytest.mark.parametrize("message", [
    {},
    "not-an-object",
    {"author": "not-an-object", "content": "hi"},
    {"author": {}, "channel_id": "9", "content": "hi"},
    {"author": {"id": "1"}, "content": "no channel"},
    {"author": {"id": "1"}, "channel_id": "9"},
    {"author": {"id": "1"}, "channel_id": "9", "content": 42},
])
def test_discord_parse_is_total(message):
    """An unknown message shape is skipped, not raised.

    Raising here would end the gateway session, and the reconnect loop would open
    another one, and the next unknown shape would do the same: a message type
    added by the server would cost the channel its listener entirely.
    """
    from ethos.comms.adapters.discord import DiscordAdapter

    assert DiscordAdapter(DISCORD_TOKEN, ["1"]).parse(message) is None


def test_a_heartbeat_interval_is_believed_within_limits():
    """A hostile or mistaken `0` must not become a busy loop.

    The floor is far below Discord's own maximum, so it costs nothing; the cap is
    what saves the process from being zombied by the server for looking idle.
    """
    from ethos.comms.adapters.discord import _heartbeat_interval

    assert _heartbeat_interval({"heartbeat_interval": 41_250}) == 41.25
    assert _heartbeat_interval({"heartbeat_interval": 0}) == 41.25
    assert _heartbeat_interval({"heartbeat_interval": "soon"}) == 41.25
    assert _heartbeat_interval({"heartbeat_interval": 10_000_000}) == 120.0
    assert _heartbeat_interval(None) == 41.25


def test_a_long_message_is_split_at_a_line_and_never_cut_mid_word():
    """2000 characters is Discord's cap, and the agent's replies are often longer.

    Splitting rather than truncating is the point: a message cut down is shown in
    the transcript as though it had been sent whole, and the missing half is only
    known to the process that dropped it.
    """
    from ethos.comms.adapters.discord import _split_message

    assert _split_message("short") == ["short"]
    assert _split_message("") == []
    body = "line one\n" + ("x" * 900 + "\n") * 5
    chunks = _split_message(body)
    assert all(len(chunk) <= 2000 for chunk in chunks)
    # Every character survives: a chunk that lost a newline or a word would be a
    # reply the agent thinks it sent whole.
    assert "".join(chunks).replace("\n", "") == body.replace("\n", "")
    # A body with no whitespace anywhere still arrives whole, just in pieces.
    unbreakable = _split_message("y" * 4500)
    assert all(len(chunk) <= 2000 for chunk in unbreakable)
    assert sum(len(chunk) for chunk in unbreakable) >= 4500 - 2


async def test_a_hello_identifies_and_a_dispatch_becomes_a_message():
    """The whole receive path: connect, identify, hear, record.

    Every frame the adapter reacts to is driven here — HELLO, then a dispatch —
    so what is under test is the state machine and not a copy of its rules.
    """
    from ethos.comms.adapters.discord import DiscordAdapter

    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        received.append(message)

    socket = FakeSocket([hello_frame(), dispatch(
        text="are you there", user="1234", channel="9999",
        author={"id": "1234", "bot": False, "global_name": "Ada Lovelace", "username": "ada"},
    )])
    adapter = DiscordAdapter(
        DISCORD_TOKEN, ["1234"], on_inbound=on_inbound, connect=lambda _url: _ready(socket),
    )
    # The session ends when the frames run out, which is a reconnect rather than a
    # failure, so it is driven directly instead of through the reconnect loop.
    await _run_session(adapter)
    assert received[0].text == "are you there"
    # The address is the conversation, because a reply has to go back to it.
    assert received[0].person_id == "dc:9999"
    assert received[0].channel == "discord"
    # And the person inside it is named, which Discord needs no round trip for.
    assert received[0].sender_name == "Ada Lovelace"
    identify = [frame for frame in socket.sent if frame["op"] == 2]
    assert len(identify) == 1
    assert identify[0]["d"]["token"] == DISCORD_TOKEN
    assert identify[0]["d"]["intents"] == adapter.intents
    assert socket.closed is True


def test_discord_prefers_the_display_name_over_the_handle():
    """`global_name` first, and the order is the whole point.

    Discord dropped the old four-digit discriminators, so `username` is now a login
    handle — often digits and underscores — while `global_name` is what the user
    chose to be called. Reading them the other way round gives every sender a
    handle where a name was sitting in the same payload.
    """
    from ethos.comms.adapters.discord import DiscordAdapter, _author_name

    adapter = DiscordAdapter(DISCORD_TOKEN, ["1234"])
    assert adapter.parse({
        "id": "1", "channel_id": "9", "content": "hi",
        "author": {"id": "1234", "global_name": "Ada Lovelace", "username": "ada"},
    }) == ("1234", "9", "1", "hi", "Ada Lovelace")
    # A user who never set one still has a handle, so still has a name.
    assert _author_name({"username": "ada"}) == "ada"
    # And one with neither is `None` rather than a blank string, which would reach
    # the prompt as a sender who exists and has no name.
    assert _author_name({"id": "1234"}) is None


def test_discord_can_be_told_to_answer_anybody():
    """The door where this is most dangerous, which is why it is a named switch.

    A bot holding `MESSAGE_CONTENT` reads every message in every channel it has
    been added to, so an unfiltered Discord agent answers rooms it was dropped
    into — and reading an empty `allowed_ids` as permission to do that would make
    every Discord setup that has not been filled in yet do exactly that.
    """
    from ethos.comms.adapters.discord import DiscordAdapter

    assert DiscordAdapter(DISCORD_TOKEN, []).accepts("9", "C1") is False
    assert DiscordAdapter(DISCORD_TOKEN, [], accept_from_anyone=True).accepts("9", "C1") is True
    assert DiscordAdapter(DISCORD_TOKEN, ["1234"], accept_from_anyone=True).accepts("9", "C1") is True


async def test_an_unlisted_author_is_not_recorded_at_all():
    from ethos.comms.adapters.discord import DiscordAdapter

    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        received.append(message)

    socket = FakeSocket([hello_frame(), dispatch(user="9999", channel="9999")])
    adapter = DiscordAdapter(DISCORD_TOKEN, ["1234"], on_inbound=on_inbound, connect=lambda _u: _ready(socket))
    await _run_session(adapter)
    assert received == []


async def test_an_unreadable_frame_does_not_end_the_connection():
    """A frame this build does not understand is skipped, not fatal.

    A server adding a field, or a compressed frame arriving because `encoding`
    was ignored, must not cost the channel its listener: raising here would close
    the socket, which is the reconnect loop's job and not this function's.
    """
    from ethos.comms.adapters.discord import DiscordAdapter

    received: list[Message] = []

    async def on_inbound(message: Message) -> None:
        received.append(message)

    class _Mixed(FakeSocket):
        def __aiter__(self) -> Any:
            async def _gen() -> Any:
                yield json.dumps(hello_frame())
                yield b"\x00\x01binary"
                yield "not json at all"
                yield json.dumps(dispatch(text="still here", user="1234"))
            return _gen()

    socket = _Mixed([])
    adapter = DiscordAdapter(DISCORD_TOKEN, ["1234"], on_inbound=on_inbound, connect=lambda _u: _ready(socket))
    await _run_session(adapter)
    assert [m.text for m in received] == ["still here"]


async def test_a_gateway_that_asks_to_reconnect_is_a_reconnect_not_a_failure():
    """op 7 and op 9 both mean "open a new session", and neither is an error.

    Treated as a failure they would each print a traceback on a routine event, and
    a log that prints a traceback for normal operation is a log nobody reads.
    """
    from ethos.comms.adapters.discord import DiscordAdapter, _Reconnect

    socket = FakeSocket([{"op": 7, "d": None}])
    adapter = DiscordAdapter(DISCORD_TOKEN, ["1234"], connect=lambda _u: _ready(socket))
    with pytest.raises(_Reconnect):
        await adapter._session()
    assert socket.closed is True


async def test_starting_to_send_does_not_open_the_gateway():
    """`start` is the half that speaks; only `listen` holds the socket.

    The same split the Telegram adapter keeps, and for the same reason: two
    processes opening one Discord gateway is not two readers. A second IDENTIFY
    invalidates the first session, and the channel then hears nothing while every
    log line says it connected.
    """
    from ethos.comms.adapters.discord import DiscordAdapter

    http = FakeHttp(FakeResponse(200, {"id": "42"}))
    adapter = DiscordAdapter(DISCORD_TOKEN, ["1234"], http=http, connect=_never_connect)
    await adapter.start()
    try:
        assert adapter._task is None
        assert adapter.connected is False
        result = await adapter.send(person_id="dc:9999", text="answered")
        assert result["status"] == "sent"
    finally:
        await adapter.stop()


async def test_a_discord_send_posts_to_the_channel_with_the_bot_prefix():
    from ethos.comms.adapters.discord import DiscordAdapter

    http = FakeHttp(FakeResponse(200, {"id": "6000"}))
    adapter = DiscordAdapter(DISCORD_TOKEN, [], http=http)
    result = await adapter.send(person_id="dc:9999", text="hello")
    assert result["status"] == "sent"
    assert result["provider_message_id"] == "6000"
    call = http.calls[0]
    assert call["url"] == "https://discord.com/api/v10/channels/9999/messages"
    # `Bot`, not `Bearer`: Discord refuses a `Bearer` request with a 401 whose body
    # does not mention the reason.
    assert call["headers"]["Authorization"] == f"Bot {DISCORD_TOKEN}"
    assert call["json"] == {"content": "hello"}


async def test_a_discord_refusal_is_reported_as_a_refusal():
    from ethos.comms.adapters.discord import DiscordAdapter

    http = FakeHttp(FakeResponse(403, {"message": "Missing Access", "code": 50001}))
    adapter = DiscordAdapter(DISCORD_TOKEN, [], http=http)
    result = await adapter.send(person_id="dc:9999", text="hello")
    assert result["status"] == "failed"
    assert "Missing Access" in result["error"]
    assert result["http_status"] == 403


async def test_a_discord_send_refuses_a_person_id_from_another_channel():
    from ethos.comms.adapters.discord import DiscordAdapter

    http = FakeHttp(FakeResponse(200, {"id": "1"}))
    adapter = DiscordAdapter(DISCORD_TOKEN, [], http=http)
    result = await adapter.send(person_id="slack:C1", text="hello")
    assert result["status"] == "failed"
    assert http.calls == []


async def _run_session(adapter: Any) -> None:
    """Drive one gateway session, treating the socket ending as what it is.

    A socket that runs out of frames has ended, and the adapter's answer to that
    is a reconnect — so the tests below assert on what happened *before* the end
    rather than pretending the session completed.
    """
    from ethos.comms.adapters.discord import _Reconnect

    try:
        await adapter._session()
    except _Reconnect:
        pass


def _ready(value: Any) -> Any:
    """An awaitable resolving to a value, for the adapter's socket factory."""
    async def _inner() -> Any:
        return value

    return _inner()


async def _never_connect(_url: str) -> Any:
    raise AssertionError("the gateway must not be opened by `start`")


# ------------------------------------------------- the receiver's own schemes


async def test_a_route_may_use_a_signature_scheme_that_is_not_metas(monkeypatch: pytest.MonkeyPatch):
    """Two push channels, two proofs, one listener.

    This is what the second signature scheme bought. Before it, adding Slack meant
    teaching the receiver about Slack; now it means handing the route the check
    that provider asks for, and the Meta path is untouched.
    """
    delivered: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        delivered.append(payload)

    raw = b'{"a":1}'
    port = free_port()
    receiver = WebhookReceiver("127.0.0.1", port, lambda *a: asyncio.sleep(0))
    receiver.route(
        "/hooks/other",
        secret="other-secret",
        authentic=meta_signature("other-secret"),
        handler=handler,
    )
    await receiver.start()
    try:
        good, _ = await post(port, "/hooks/other", raw, {
            "x-hub-signature-256": f"sha256={hmac.new(b'other-secret', raw, hashlib.sha256).hexdigest()}",
            "content-length": str(len(raw)),
        })
        bad, _ = await post(port, "/hooks/other", raw, {"content-length": str(len(raw))})
    finally:
        await receiver.stop()
    assert good == 200
    assert bad == 401
    assert delivered == [{"a": 1}]


# ------------------------------------------ two push channels, one port

def _slack_style_config(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Any:
    """The shipped config with one webhook channel on and no credentials needed."""
    from ethos.config import DiscordChannelConfig, SlackChannelConfig, load_config

    config = load_config("config")
    config.channels.cli.enabled = False
    config.channels.web.enabled = False
    config.channels.telegram.enabled = False
    config.channels.whatsapp.enabled = False
    config.channels.email.enabled = False
    config.channels.slack = SlackChannelConfig(**{"enabled": False, **overrides})
    config.channels.discord = DiscordChannelConfig(enabled=False)
    config.channels.webhook.enabled = True
    config.channels.webhook.port = free_port()
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SLACK_SECRET)
    return config


async def test_slack_is_built_only_when_the_webhook_can_receive_it(monkeypatch: pytest.MonkeyPatch):
    """No `webhook.enabled`, no Slack — and it says so.

    Skipped loudly rather than quietly, because a channel that is on in the config
    and absent from the process is a channel an operator will believe is working.
    """
    from ethos.comms.host import CommsHost

    config = _slack_style_config(monkeypatch)
    config.channels.webhook.enabled = False
    host = CommsHost(config, on_inbound=lambda _m: asyncio.sleep(0))
    host.build_adapters()
    assert "slack" not in host.adapters
    assert host.receiver is None


async def test_slack_is_skipped_when_a_credential_is_missing(monkeypatch: pytest.MonkeyPatch):
    """A token with no signing secret is a route that refuses every delivery.

    That looks exactly like a channel nobody is speaking to, so the absence is
    reported rather than left to be discovered one silent delivery at a time.
    """
    from ethos.comms.host import CommsHost

    config = _slack_style_config(monkeypatch)
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    host = CommsHost(config, on_inbound=lambda _m: asyncio.sleep(0))
    host.build_adapters()
    assert "slack" not in host.adapters


async def test_two_push_channels_share_one_receiver(monkeypatch: pytest.MonkeyPatch):
    """A tunnel exposes one URL, so a second push channel is a path and not a port.

    A second `WebhookReceiver` on the same port would race the first for it, and
    the loser is logged as a webhook that failed to start — which is the whole of
    what an operator sees when the channel stops receiving.
    """
    from ethos.comms.host import CommsHost

    config = _slack_style_config(monkeypatch, enabled=True)
    config.channels.whatsapp.enabled = True
    for name in ("WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_ACCESS_TOKEN",
                 "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"):
        monkeypatch.setenv(name, "x")
    host = CommsHost(config, on_inbound=lambda _m: asyncio.sleep(0))
    host.build_adapters()
    try:
        assert set(host.adapters) == {"slack", "whatsapp"}
        assert sorted(host.receiver.routes) == ["/hooks/slack", "/hooks/whatsapp"]
    finally:
        assert host.receiver is not None


async def test_discord_is_skipped_without_a_token(monkeypatch: pytest.MonkeyPatch):
    from ethos.comms.host import CommsHost
    from ethos.config import DiscordChannelConfig

    config = _slack_style_config(monkeypatch)
    config.channels.discord = DiscordChannelConfig(enabled=True, token_env="ABSENT_DISCORD_TOKEN")
    monkeypatch.delenv("ABSENT_DISCORD_TOKEN", raising=False)
    host = CommsHost(config, on_inbound=lambda _m: asyncio.sleep(0))
    host.build_adapters()
    assert "discord" not in host.adapters


async def test_discord_needs_no_webhook_because_it_has_no_door(monkeypatch: pytest.MonkeyPatch):
    """The one channel here with no callback URL, and so no reason to need one."""
    from ethos.comms.host import CommsHost
    from ethos.config import DiscordChannelConfig

    config = _slack_style_config(monkeypatch)
    config.channels.webhook.enabled = False
    config.channels.discord = DiscordChannelConfig(enabled=True, token_env="TEST_DC_TOKEN")
    monkeypatch.setenv("TEST_DC_TOKEN", DISCORD_TOKEN)
    host = CommsHost(config, on_inbound=lambda _m: asyncio.sleep(0))
    host.build_adapters()
    try:
        assert "discord" in host.adapters
        assert host.receiver is None
    finally:
        await host.stop()
