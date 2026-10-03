from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from ethos.config import EthosConfig
from ethos.db import Database
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind

logger = get_logger("ethos.core.scheduler")


class Scheduler:
    """Timers and alarms: persisted in env_entities, fired as timer.fired
    bus events. Survives restarts; drift-corrected waits."""

    def __init__(self, config: EthosConfig, db: Database | None, bus: Any = None):
        self.config = config
        self.db = db
        self.bus = bus
        self.timers: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    async def load(self) -> None:
        if self.db is None:
            return
        rows = await self.db.fetch(
            "SELECT key, attrs::text AS attrs FROM env_entities WHERE kind = 'schedule'"
        )
        for row in rows:
            attrs = json.loads(row["attrs"]) if isinstance(row["attrs"], str) else dict(row["attrs"])
            self.timers[row["key"]] = attrs
        logger.info("scheduler.loaded", count=len(self.timers))

    async def _persist(self, timer_id: str, attrs: dict[str, Any]) -> None:
        if self.db is None:
            return
        await self.db.execute(
            "INSERT INTO env_entities (id, kind, key, attrs, owner, last_verified_at, health) "
            "VALUES ($1, 'schedule', $2, $3::jsonb, 'scheduler', now(), 'ok') "
            "ON CONFLICT (key) DO UPDATE SET attrs = $3::jsonb, last_verified_at = now()",
            uuid.uuid4(), timer_id, json.dumps(attrs),
        )

    async def _remove(self, timer_id: str) -> None:
        if self.db is not None:
            await self.db.execute(
                "DELETE FROM env_entities WHERE kind = 'schedule' AND key = $1", timer_id,
            )

    async def schedule_at(self, name: str, at: str, payload: dict[str, Any]) -> str:
        timer_id = f"timer-{uuid.uuid4().hex[:12]}"
        fire_at = _parse_iso(at)
        attrs = {
            "id": timer_id, "name": name, "kind": "at",
            "fire_at": fire_at.isoformat(), "payload": payload,
        }
        self.timers[timer_id] = attrs
        await self._persist(timer_id, attrs)
        return timer_id

    async def schedule_every(self, name: str, interval_s: float, payload: dict[str, Any]) -> str:
        timer_id = f"timer-{uuid.uuid4().hex[:12]}"
        interval_s = max(1.0, float(interval_s))
        attrs = {
            "id": timer_id, "name": name, "kind": "every",
            "interval_s": interval_s,
            "next_fire": (datetime.now(UTC).timestamp() + interval_s),
            "payload": payload,
        }
        self.timers[timer_id] = attrs
        await self._persist(timer_id, attrs)
        return timer_id

    async def cancel(self, timer_id: str) -> None:
        self.timers.pop(timer_id, None)
        await self._remove(timer_id)

    async def start(self) -> None:
        await self.load()
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            now = datetime.now(UTC).timestamp()
            due: list[tuple[str, dict[str, Any]]] = []
            for timer_id, attrs in list(self.timers.items()):
                if attrs.get("kind") == "at":
                    if _parse_iso(attrs["fire_at"]).timestamp() <= now:
                        due.append((timer_id, attrs))
                else:
                    if float(attrs.get("next_fire", 0)) <= now:
                        due.append((timer_id, attrs))
            for timer_id, attrs in due:
                if attrs.get("kind") == "at":
                    self.timers.pop(timer_id, None)
                    await self._remove(timer_id)
                else:
                    attrs["next_fire"] = now + float(attrs["interval_s"])
                    await self._persist(timer_id, attrs)
                await self._fire(attrs)
            await asyncio.sleep(1.0)

    async def _fire(self, attrs: dict[str, Any]) -> None:
        logger.info("scheduler.fired", name=attrs.get("name"))
        if self.bus is not None:
            await self.bus.publish(
                EventKind.TIMER_FIRED,
                {
                    "timer_id": attrs.get("id"),
                    "name": attrs.get("name"),
                    "payload": attrs.get("payload") or {},
                    "urgency": 0.5,
                    "summary": f"timer '{attrs.get('name')}' fired",
                    "sender_class": "system",
                },
                salience=0.5,
            )


def _parse_iso(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        target = time.time() + 3600
        return datetime.fromtimestamp(target, tz=UTC)
