from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ethos.bus import EventBus
from ethos.config import EthosConfig
from ethos.db import Database
from ethos.observability.logger import get_logger

logger = get_logger("ethos.continuity")


class ContinuityManager:
    """Continuity is state, not transcript [P3].

    A full continuity snapshot is written atomically every cycle; `wake_up()`
    reconciles the action journal, the bus gap, and the environment before
    the first thought of a new incarnation.
    """

    def __init__(self, config: EthosConfig, db: Database | None = None, bus: EventBus | None = None):
        self.config = config
        self.db = db
        self.bus = bus
        self.snapshot_path = config.paths.data_dir / "continuity.json"
        config.paths.data_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_path_for(self) -> Path:
        return self.snapshot_path

    async def save(self, state: dict[str, Any]) -> None:
        payload = {
            "version": 1,
            "saved_at": datetime.now(UTC).isoformat(),
            "state": state,
        }
        tmp = self.snapshot_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.snapshot_path)
        if self.db is not None:
            try:
                await self.db.execute(
                    "INSERT INTO checkpoints (ts, thread_id, snapshot) VALUES (now(), $1, $2::jsonb)",
                    state.get("thread_id", "self"),
                    json.dumps(payload["state"], default=str),
                )
            except Exception:
                logger.exception("continuity.checkpoint_write_failed")

    def load(self) -> dict[str, Any] | None:
        if not self.snapshot_path.exists():
            return None
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            return data.get("state")
        except (ValueError, OSError):
            logger.exception("continuity.load_failed")
            return None

    async def wake_up(self) -> dict[str, Any]:
        """Reconcile journal, bus gaps, environment. Returns a wake report."""
        report: dict[str, Any] = {
            "reconciled_actions": 0,
            "missed_events": 0,
            "resumed_focus": None,
            "snapshot": None,
            "env_checks": 0,
        }
        snapshot = self.load()
        if snapshot is not None:
            report["snapshot"] = snapshot
            report["resumed_focus"] = snapshot.get("focus")

        if self.db is not None:
            inflight = await self.db.fetch(
                "SELECT id, tool, status FROM action_journal "
                "WHERE ts_end IS NULL AND status = 'running' ORDER BY ts_start"
            )
            for row in inflight:
                await self.db.execute(
                    "UPDATE action_journal SET ts_end = now(), status = 'interrupted', "
                    "result_digest = result_digest || $1::jsonb WHERE id = $2",
                    json.dumps({"note": "reconciled at wake"}), row["id"],
                )
                report["reconciled_actions"] += 1
            rows = await self.db.fetch("SELECT COUNT(*) AS n FROM env_entities WHERE health = 'stale'")
            report["env_checks"] = int(rows[0]["n"]) if rows else 0
            await self.db.execute(
                "UPDATE env_entities SET health = 'unverified' WHERE health = 'stale'"
            )

        if self.bus is not None:
            missed = await self.bus.drain("core-wake")
            report["missed_events"] = len(missed)

        logger.info(
            "continuity.wake",
            reconciled_actions=report["reconciled_actions"],
            missed_events=report["missed_events"],
        )
        return report
