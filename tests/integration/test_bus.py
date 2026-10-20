from __future__ import annotations

import asyncio

import pytest

from ethos.bus import EventBus
from ethos.schemas.events import Event

pytestmark = pytest.mark.integration


async def test_publish_and_subscribe_roundtrip(db):
    bus = EventBus(db, source="test")
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("test.kind", handler)
    await bus.start()
    try:
        await bus.publish("test.kind", {"hello": "world"}, salience=0.4)
        await asyncio.sleep(1.0)
        assert len(received) == 1
        assert received[0].payload == {"hello": "world"}
        assert received[0].salience == 0.4
        assert received[0].id is not None
    finally:
        await bus.stop()


async def test_publish_transactional_notify_only_on_commit(db):
    bus = EventBus(db, source="test")
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("tx.kind", handler)
    await bus.start()
    try:
        try:
            async with db.acquire() as conn:
                async with conn.transaction():
                    await bus.publish("tx.kind", {"n": 1}, conn=conn)
                    await asyncio.sleep(0.3)
                    assert received == []
                    raise RuntimeError("rollback")
        except RuntimeError:
            pass
        await asyncio.sleep(0.6)
        assert received == []
        row = await db.fetchrow("SELECT COUNT(*) AS n FROM events WHERE kind = 'tx.kind'")
        assert row["n"] == 0
    finally:
        await bus.stop()


async def test_publish_inside_committed_transaction_notifies(db):
    bus = EventBus(db, source="test")
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("tx.kind.ok", handler)
    await bus.start()
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                await bus.publish("tx.kind.ok", {"n": 2}, conn=conn)
        await asyncio.sleep(1.0)
        assert len(received) == 1
        assert received[0].payload == {"n": 2}
    finally:
        await bus.stop()


async def test_drain_claims_events_per_consumer(db):
    bus_a = EventBus(db, source="a")
    bus_b = EventBus(db, source="b")
    await bus_a.publish("drain.kind", {"v": 1})
    claimed_a = await bus_a.drain("consumer-a", kinds=["drain.kind"])
    claimed_b = await bus_b.drain("consumer-b", kinds=["drain.kind"])
    again_a = await bus_a.drain("consumer-a", kinds=["drain.kind"])
    assert len(claimed_a) == 1
    assert len(claimed_b) == 1
    assert again_a == []
    assert claimed_a[0].payload == {"v": 1}


async def test_wildcard_subscription(db):
    bus = EventBus(db, source="test")
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe(None, handler)
    await bus.start()
    try:
        await bus.publish("first.kind", {})
        await bus.publish("second.kind", {})
        await asyncio.sleep(1.2)
        kinds = sorted(e.kind for e in received)
        assert kinds == ["first.kind", "second.kind"]
    finally:
        await bus.stop()
