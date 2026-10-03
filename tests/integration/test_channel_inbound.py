from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
from typing import Any
from uuid import UUID, uuid4

import pytest

from ethos.comms.host import CommsHost, inbound_to_bus
from ethos.config import WebhookConfig, WhatsappChannelConfig
from ethos.schemas.messages import Message
from ethos.social.deliberation import SocialCognition

pytestmark = pytest.mark.integration

APP_SECRET = "integration-app-secret"
VERIFY_TOKEN = "integration-verify-token"
ALLOWED = "819012345678"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wa_body(text: str, sender: str = ALLOWED, mid: str | None = None) -> bytes:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "0",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "contacts": [{"profile": {"name": "Someone"}, "wa_id": sender}],
                    "messages": [{
                        "from": sender, "id": mid or f"wamid.{uuid4().hex}",
                        "timestamp": "1700000000", "type": "text",
                        "text": {"body": text},
                    }],
                },
            }],
        }],
    }).encode()


async def deliver(port: int, path: str, raw: bytes, secret: str = APP_SECRET) -> tuple[int, str]:
    digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    head = f"POST {path} HTTP/1.1\r\nhost: 127.0.0.1\r\n"
    head += f"x-hub-signature-256: sha256={digest}\r\n"
    head += f"content-length: {len(raw)}\r\n\r\n"
    writer.write(head.encode("latin-1") + raw)
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5)
    writer.close()
    return int(response.split(b" ", 2)[1]), response.decode("latin-1", errors="replace")


class RecordingBus:
    """A bus that records what was published, without needing one.

    The real `EventBus` writes to Postgres and the point here is what the adapter
    hands it — so this stands in for the store and keeps the assertions about the
    payload, which is where the channel's identity has to survive.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any], str]] = []

    async def publish(self, kind: str, payload: dict[str, Any], trust: str = "internal") -> None:
        self.published.append((kind, payload, trust))


def loaded_config() -> Any:
    """The shipped config, as a fresh object each time.

    Built rather than shared because these tests edit it: a config mutated in
    place would leak an enabled channel into whichever test ran next.
    """
    from ethos.config import load_config

    return load_config("config")


def whatsapp_config(port: int, allowed: list[str] | None = None,
                    accept_from_anyone: bool = False) -> Any:
    """A config with one channel on, and everything else off.

    The two admission arguments are parameters rather than two more copies of this
    function, because the pair is the thing under test elsewhere: a door that is
    open to everybody with an empty list is a state that has to be constructible
    from config, and a second copy of the body would be a second place for the two
    to disagree.
    """
    config = loaded_config()
    config.channels.cli.enabled = False
    config.channels.web.enabled = False
    config.channels.email.enabled = False
    config.channels.whatsapp = WhatsappChannelConfig(
        enabled=True,
        phone_number_id_env="TEST_WHATSAPP_PHONE_ID",
        access_token_env="TEST_WHATSAPP_ACCESS_TOKEN",
        app_secret_env="TEST_WHATSAPP_APP_SECRET",
        verify_token_env="TEST_WHATSAPP_VERIFY_TOKEN",
        allowed_phone_numbers=allowed if allowed is not None else [ALLOWED],
        accept_from_anyone=accept_from_anyone,
    )
    config.channels.webhook = WebhookConfig(enabled=True, port=port)
    return config


@pytest.fixture(autouse=True)
async def clean_inbound(db):
    """
    Leaves no undeliberated message behind.

    `SocialCognition.pending_inbound` reads *every* inbound row whose
    `social_decision` is null, from the whole database — it is not scoped to one
    test. So a row this file records stays pending, and the next test that runs a
    life loop finds social work waiting and deliberates about it instead of
    advancing the goal it meant to advance. Which is correct behaviour, and makes
    these tests fail for a reason that has nothing to do with channels.

    So the rows are removed rather than marked handled. Marking them would leave
    a transcript full of "ignored" messages that no test wrote and no one
    deliberated about.
    """
    yield
    try:
        await db.execute("DELETE FROM messages WHERE conversation_key LIKE 'whatsapp:%'")
    except Exception:
        pass


@pytest.fixture()
def whatsapp_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TEST_WHATSAPP_PHONE_ID", "1234567890")
    monkeypatch.setenv("TEST_WHATSAPP_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("TEST_WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("TEST_WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)


async def test_a_signed_webhook_arrives_as_an_inbound_message(db, whatsapp_env):
    """The whole claim, end to end: a door becomes a message the agent can read.

    Bytes signed by the app secret, over the listener, into `record_inbound`, and
    onto the bus as a percept. Everything downstream of that is the same road a
    browser message takes — which is the point of `inbound_to_bus`.
    """
    port = free_port()
    bus = RecordingBus()
    social = SocialCognition(loaded_config(), db=db)
    host = CommsHost(whatsapp_config(port), on_inbound=inbound_to_bus(bus, social))
    host.build_adapters()
    assert "whatsapp" in host.adapters, host.adapters
    assert host.receiver is not None, "an enabled push channel needs somewhere to be pushed to"
    await host.receiver.start()
    try:
        status, _ = await deliver(port, "/hooks/whatsapp", wa_body("are you awake?"))
    finally:
        await host.receiver.stop()

    assert status == 200
    kind, payload, trust = bus.published[0]
    assert kind == "msg.inbound"
    assert payload["channel"] == "whatsapp"
    assert payload["person_id"] == f"wa:{ALLOWED}"
    assert payload["text"] == "are you awake?"
    assert payload["conversation_key"] == f"whatsapp:{ALLOWED}"
    # A listed number is a known sender; the attention scorer weights the two
    # differently, and calling a stranger "known" would be how an unfiltered
    # channel talks past the interruption budgets.
    assert payload["sender_class"] == "known"
    assert trust == "known"

    row = await db.fetchrow(
        "SELECT channel, person_id, text FROM messages WHERE id = $1",
        UUID(payload["message_id"]),
    )
    assert dict(row) == {
        "channel": "whatsapp", "person_id": f"wa:{ALLOWED}", "text": "are you awake?",
    }


async def test_the_senders_name_reaches_the_row_and_the_percept(db, whatsapp_env):
    """The name has to survive the whole road, not just the adapter.

    `contacts[].profile.name` is in the webhook body the adapter was already
    reading, and it was thrown away. Asserted here at the *end* of the pipeline on
    purpose: the failure mode for a name is not an exception in the adapter but a
    quietly absent column four steps later, and only a test that reads the row back
    out of Postgres can tell the difference between "not captured" and "captured
    and lost".
    """
    port = free_port()
    bus = RecordingBus()
    social = SocialCognition(loaded_config(), db=db)
    host = CommsHost(whatsapp_config(port), on_inbound=inbound_to_bus(bus, social))
    host.build_adapters()
    await host.receiver.start()
    try:
        status, _ = await deliver(port, "/hooks/whatsapp", wa_body("who am I?"))
    finally:
        await host.receiver.stop()
    assert status == 200

    _, payload, _ = bus.published[0]
    # The percept carries it, because the percept is what the attention scorer and
    # the focus title read.
    assert payload["sender_name"] == "Someone"
    assert payload["summary"].startswith("Message from Someone (wa:")
    # And the row has it, because the row is what the transcript is.
    row = await db.fetchrow(
        "SELECT person_id, sender_name, sender_username FROM messages WHERE id = $1",
        UUID(payload["message_id"]),
    )
    # WhatsApp has no handle concept at all, so this half is honestly absent rather
    # than filled in with the display name a second time.
    assert dict(row) == {
        "person_id": f"wa:{ALLOWED}", "sender_name": "Someone", "sender_username": None,
    }


async def test_a_channel_that_names_nobody_still_records_the_message(db, whatsapp_env):
    """The floor: a sender with no name is a sender, not a hole.

    Every channel here can be talked to by somebody it cannot name, and the
    fallback has to keep the address — a message arriving with no name at all must
    not be dropped, and must not be recorded as an empty string that renders as a
    person who exists and has no name.
    """
    port = free_port()
    bus = RecordingBus()
    social = SocialCognition(loaded_config(), db=db)
    host = CommsHost(whatsapp_config(port), on_inbound=inbound_to_bus(bus, social))
    host.build_adapters()
    await host.receiver.start()
    body = json.loads(wa_body("anonymous"))
    del body["entry"][0]["changes"][0]["value"]["contacts"]
    try:
        status, _ = await deliver(port, "/hooks/whatsapp", json.dumps(body).encode())
    finally:
        await host.receiver.stop()
    assert status == 200

    _, payload, _ = bus.published[0]
    assert payload["sender_name"] is None
    assert payload["summary"].startswith(f"Message from wa:{ALLOWED}: ")
    row = await db.fetchrow(
        "SELECT sender_name, sender_username FROM messages WHERE id = $1",
        UUID(payload["message_id"]),
    )
    assert dict(row) == {"sender_name": None, "sender_username": None}


async def test_a_whatsapp_stranger_is_answered_when_the_door_is_open(db, whatsapp_env):
    """`accept_from_anyone`, through the config, all the way to a written row.

    Written this way round rather than as an adapter unit test because the thing
    that can be wrong is the wiring: a flag that exists on the config model, reads
    back correctly, and is never passed to the adapter is a flag that looks like it
    works. The only way to tell is a stranger's number arriving and being kept.
    """
    port = free_port()
    bus = RecordingBus()
    social = SocialCognition(loaded_config(), db=db)
    host = CommsHost(
        whatsapp_config(port, allowed=["819012345678"], accept_from_anyone=True),
        on_inbound=inbound_to_bus(bus, social),
    )
    host.build_adapters()
    await host.receiver.start()
    try:
        # A number that is on no list, on a door that has been told to answer all.
        status, _ = await deliver(port, "/hooks/whatsapp", wa_body("hello?", sender="81909999999"))
    finally:
        await host.receiver.stop()

    assert status == 200
    assert len(bus.published) == 1, "a stranger is recorded on a door open to everyone"
    assert bus.published[0][1]["person_id"] == "wa:81909999999"
    # And still a known sender as far as attention is concerned, which is what the
    # interruption budgets key off: admitting somebody is not the same as trusting
    # them, and conflating the two would let a stranger past the budget as well.
    assert bus.published[0][1]["sender_class"] == "known"


async def test_an_unlisted_number_is_never_written(db, whatsapp_env):
    """Refused at the door, so there is nothing for the agent to deliberate on.

    Not recorded and then ignored: a stranger's words in `messages` would be read,
    answered or not, and counted as a silence, on every restart until it aged out.
    """
    port = free_port()
    bus = RecordingBus()
    social = SocialCognition(loaded_config(), db=db)
    host = CommsHost(whatsapp_config(port), on_inbound=inbound_to_bus(bus, social))
    host.build_adapters()
    await host.receiver.start()
    try:
        status, _ = await deliver(port, "/hooks/whatsapp", wa_body("hi", sender="81909999999"))
    finally:
        await host.receiver.stop()

    assert status == 200, "the delivery is acknowledged; it is simply not admitted"
    assert bus.published == []
    count = await db.fetchval(
        "SELECT COUNT(*) FROM messages WHERE channel = 'whatsapp' AND text = $1", "hi",
    )
    assert int(count or 0) == 0


async def test_a_redelivery_is_acknowledged_and_not_recorded_twice(db, whatsapp_env):
    """Meta redelivers anything it did not see a 2xx for."""
    port = free_port()
    bus = RecordingBus()
    social = SocialCognition(loaded_config(), db=db)
    host = CommsHost(whatsapp_config(port), on_inbound=inbound_to_bus(bus, social))
    host.build_adapters()
    await host.receiver.start()
    raw = wa_body("only once", mid="wamid.FIXED-ID")
    try:
        first, _ = await deliver(port, "/hooks/whatsapp", raw)
        second, _ = await deliver(port, "/hooks/whatsapp", raw)
    finally:
        await host.receiver.stop()

    assert first == 200
    assert second == 200
    assert len(bus.published) == 1, "one message, one deliberation"


async def test_the_same_road_as_the_browser(whatsapp_env):
    """A WhatsApp message and a browser message differ in one field.

    They reach the percept with the same shape, the same sender class and the
    same summary grammar, which is what "one road" has to mean if it is to mean
    anything.
    """
    bus = RecordingBus()
    handler = inbound_to_bus(bus, _NullSocial())
    await handler(Message(channel="whatsapp", person_id=f"wa:{ALLOWED}", text="hello",
                          conversation_key=f"whatsapp:{ALLOWED}"))
    await handler(Message(channel="web", person_id="owner", text="hello",
                          conversation_key="web:owner"))
    webhooks, web = bus.published
    assert webhooks[0] == web[0] == "msg.inbound"
    assert set(webhooks[1]) == set(web[1]), "the same fields, whatever the door"
    assert webhooks[2] == web[2] == "known"
    # Only the door differs. Two vocabularies for one agent is the thing this
    # whole layer exists to avoid.
    assert webhooks[1]["channel"] == "whatsapp" and web[1]["channel"] == "web"
    assert webhooks[1]["conversation_key"] == f"whatsapp:{ALLOWED}"


class _NullSocial:
    async def record_inbound(self, message: Message) -> None:
        return None


async def test_an_unconfigured_channel_is_a_closed_door(monkeypatch: pytest.MonkeyPatch):
    """Missing credentials skip the channel instead of half-building it.

    A webhook with a token but no app secret would answer 401 to every delivery,
    which from the outside is a channel that has stopped working — so the answer
    is to not open it at all.
    """
    for name in (
        "TEST_WHATSAPP_PHONE_ID", "TEST_WHATSAPP_ACCESS_TOKEN",
        "TEST_WHATSAPP_APP_SECRET", "TEST_WHATSAPP_VERIFY_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    port = free_port()
    host = CommsHost(whatsapp_config(port), on_inbound=lambda message: asyncio.sleep(0))
    host.build_adapters()
    assert "whatsapp" not in host.adapters
    assert host.receiver is None


async def test_whatsapp_needs_the_webhook_listener(monkeypatch: pytest.MonkeyPatch):
    """An enabled channel with nowhere to be pushed to is not started.

    There is no polling fallback to fall back on, so a WhatsApp adapter with no
    receiver would sit there able to send and unable to hear — the worst kind of
    half-working.
    """
    monkeypatch.setenv("TEST_WHATSAPP_PHONE_ID", "1")
    monkeypatch.setenv("TEST_WHATSAPP_ACCESS_TOKEN", "t")
    monkeypatch.setenv("TEST_WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("TEST_WHATSAPP_VERIFY_TOKEN", VERIFY_TOKEN)
    config = whatsapp_config(free_port())
    config.channels.webhook.enabled = False
    host = CommsHost(config, on_inbound=lambda message: asyncio.sleep(0))
    host.build_adapters()
    assert "whatsapp" not in host.adapters
    assert host.receiver is None
