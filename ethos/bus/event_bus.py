from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from ethos.db import Database
from ethos.schemas.events import Event

logger = logging.getLogger(__name__)

BUS_CHANNEL = "ethos_events"

# How many recently-seen event ids the dedupe set remembers.
#
# This set exists to make one event one event: `LISTEN/NOTIFY` on a connection
# that reconnects re-delivers, and a NOTIFY is not a promise of exactly one
# delivery, so the same id can arrive twice and enqueueing it twice means the
# agent deliberates on the same message and answers it twice.
#
# It was a plain set cleared wholesale at 100,000. Clearing is the wrong shape
# here for two reasons, and both of them are about the clearing itself. It throws
# away the dedupe window the instant it fills — so at id 100,001 every id the
# bus had already delivered is deliverable again — and it does it in one
# statement, which is a latency spike on a process that was about to be
# answering a phone.
#
# So the window is the unit and it is bounded, and the oldest ids leave one at a
# time. 16,384 is comfortably larger than the burst a reconnecting connection can
# replay while still being a few hundred kilobytes rather than the 8 MB a set of
# 100,000 ints occupies.
SEEN_ID_WINDOW = 16_384


class EventBus:
    """Transactional event bus backed by the `events` table + LISTEN/NOTIFY.

    publish(..., conn=...) participates in the caller's transaction; the
    NOTIFY piggybacked on the same connection is only delivered on commit,
    which makes bus publishes atomic with the data they describe [D-10].
    """

    def __init__(self, db: Database, source: str = "core"):
        self.db = db
        self.source = source
        self._subscribers: defaultdict[str, list[Callable[[Event], Awaitable[None] | None]]] = defaultdict(list)
        self._wildcard: list[Callable[[Event], Awaitable[None] | None]] = []
        self._listener_conn: asyncpg.Connection | None = None
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._dispatch_task: asyncio.Task[None] | None = None
        self._running = False
        self._seen_ids: set[int] = set()
        # Insertion order for `_seen_ids`, so the oldest id is known without
        # sorting the whole set on every event. `maxlen` is the backstop: it caps
        # this deque at the same window the eviction below maintains, and it caps
        # it even if `_seen_add` were somehow not called.
        self._seen_order: deque[int] = deque(maxlen=SEEN_ID_WINDOW)

    async def start(self) -> None:
        self._listener_conn = await self.db.new_listener_connection()
        await self._listener_conn.add_listener(BUS_CHANNEL, self._on_notify)
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("bus.started source=%s", self.source)

    async def stop(self) -> None:
        self._running = False
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._listener_conn is not None:
            try:
                await self._listener_conn.close()
            except Exception:
                pass
            self._listener_conn = None

    def _on_notify(self, _conn: asyncpg.Connection, _pid: int, _channel: str, payload: str) -> None:
        try:
            data = json.loads(payload)
            event_id = int(data["id"])
        except Exception:
            logger.exception("bus.bad_notify payload=%r", payload)
            return
        asyncio.get_running_loop().create_task(self._fetch_and_enqueue(event_id))

    async def _fetch_and_enqueue(self, event_id: int) -> None:
        if event_id in self._seen_ids:
            return
        try:
            row = await self.db.fetchrow(
                "SELECT id, ts, kind, source, payload, salience, trust, consumed_by FROM events WHERE id = $1",
                event_id,
            )
        except Exception:
            logger.exception("bus.fetch_failed id=%s", event_id)
            return
        if row is None:
            return
        event = self._row_to_event(row)
        self._seen_add(event_id)
        await self._queue.put(event)

    def _seen_add(self, event_id: int) -> None:
        """Remember an id, evicting the oldest once the window is full.

        One id in, one id out. `drain` below also records ids, and it goes
        through here too so both paths share one window — an id claimed by the
        startup gap recovery must still be recognised as seen by the NOTIFY
        arriving for the same insert.
        """
        if event_id in self._seen_ids:
            return
        self._seen_ids.add(event_id)
        if len(self._seen_order) >= self._seen_order.maxlen:
            self._seen_ids.discard(self._seen_order[0])
        self._seen_order.append(event_id)

    @staticmethod
    def _row_to_event(row: asyncpg.Record) -> Event:
        return Event(
            id=row["id"],
            ts=row["ts"],
            kind=row["kind"],
            source=row["source"],
            payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"]),
            salience=row["salience"],
            trust=row["trust"] or "internal",
            consumed_by=list(row["consumed_by"] or []),
        )

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            await self._dispatch(event)

    async def _dispatch(self, event: Event) -> None:
        handlers = list(self._subscribers.get(event.kind, [])) + list(self._wildcard)
        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("bus.handler_failed kind=%s", event.kind)

    def subscribe(self, kind: str | None, handler: Callable[[Event], Awaitable[None] | None]) -> None:
        if kind is None:
            self._wildcard.append(handler)
        else:
            self._subscribers[kind].append(handler)

    def unsubscribe(self, kind: str | None, handler: Callable[[Event], Awaitable[None] | None]) -> None:
        target = self._wildcard if kind is None else self._subscribers.get(kind, [])
        if handler in target:
            target.remove(handler)

    async def publish(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str | None = None,
        salience: float | None = None,
        trust: str = "internal",
        conn: asyncpg.Connection | None = None,
    ) -> int:
        body = json.dumps(payload or {})
        notify_body = json.dumps({"id": 0})

        async def _do(c: asyncpg.Connection) -> int:
            row = await c.fetchrow(
                "INSERT INTO events (ts, kind, source, payload, salience, trust) "
                "VALUES (now(), $1, $2, $3::jsonb, $4, $5) RETURNING id",
                kind, source or self.source, body, salience, trust,
            )
            event_id = int(row["id"])
            await c.execute("SELECT pg_notify($1, $2)", BUS_CHANNEL, json.dumps({"id": event_id}))
            return event_id

        if conn is not None:
            event_id = await _do(conn)
        else:
            async with self.db.acquire() as c:
                async with c.transaction():
                    event_id = await _do(c)
        del notify_body
        return event_id

    async def drain(self, consumer: str, kinds: list[str] | None = None, limit: int = 500) -> list[Event]:
        """Claim events not yet consumed by `consumer` (startup gap recovery)."""
        events: list[Event] = []
        rows = await self.db.fetch(
            "SELECT id, ts, kind, source, payload, salience, trust, consumed_by FROM events "
            "WHERE NOT ($1::text = ANY(consumed_by)) AND ($2::text[] IS NULL OR kind = ANY($2)) "
            "ORDER BY id LIMIT $3",
            consumer, kinds, limit,
        )
        for row in rows:
            await self.db.execute(
                "UPDATE events SET consumed_by = array_append(consumed_by, $1) WHERE id = $2",
                consumer, row["id"],
            )
            events.append(self._row_to_event(row))
            self._seen_add(int(row["id"]))
        return events
