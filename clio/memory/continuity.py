from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from clio.bus import EventBus
from clio.config import ClioConfig
from clio.db import Database
from clio.observability.logger import get_logger

logger = get_logger("clio.continuity")


class ContinuityManager:
    """Continuity is state, not transcript [P3].

    A full continuity snapshot is written atomically every cycle; `wake_up()`
    reconciles the action journal, the bus gap, and the environment before
    the first thought of a new incarnation.
    """

    def __init__(self, config: ClioConfig, db: Database | None = None, bus: EventBus | None = None,
                 envelope: Any = None):
        self.config = config
        self.db = db
        self.bus = bus
        # The checkpoint table carries the full continuity state — what the
        # agent was doing, on which files, toward which goal — so with an
        # envelope configured it is sealed at rest like every other payload.
        self.envelope = envelope
        self.snapshot_path = config.paths.data_dir / "continuity.json"
        config.paths.data_dir.mkdir(parents=True, exist_ok=True)

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
                snapshot_json: Any = json.dumps(payload["state"], default=str)
                sealed: Any = None
                if self.envelope is not None:
                    snapshot_json = json.dumps(self.envelope.stub())
                    sealed = await self.envelope.seal(
                        json.dumps(payload["state"], default=str), "local", aad="checkpoint",
                    )
                await self.db.execute_uncancelled(
                    "INSERT INTO checkpoints (ts, thread_id, snapshot, sealed_payload) "
                    "VALUES (now(), $1, $2::jsonb, $3)",
                    state.get("thread_id", "self"),
                    snapshot_json,
                    sealed.encode("utf-8") if isinstance(sealed, str) else sealed,
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
            "interrupted_tools": [],
            "missed_events": 0,
            "resumed_focus": None,
            "snapshot": None,
            "env_checks": 0,
        }
        snapshot = self.load()
        if snapshot is not None:
            report["snapshot"] = snapshot
            report["resumed_focus"] = snapshot.get("focus")
            # NA-P2.3: the goal the interrupted focus serves, stated in the wake
            # report itself. The focus dict names the work; the goal beside it
            # says what the work was for, so an operator reading the wake report
            # -- and the agent restoring its own context -- sees the original
            # aim the resumption preserves, not just the row it points at.
            focus = snapshot.get("focus")
            if isinstance(focus, dict):
                report["resumed_goal"] = {
                    "title": focus.get("title"),
                    "intention_id": (
                        str(focus.get("intention_id"))
                        if focus.get("intention_id") else None
                    ),
                    "kind": focus.get("kind"),
                }
            report["stops"] = snapshot.get("stops") or []

        if self.db is not None:
            inflight = await self.db.fetch(
                "SELECT id, tool, status FROM action_journal "
                "WHERE ts_end IS NULL AND status = 'running' ORDER BY ts_start"
            )
            for row in inflight:
                # `COALESCE`, because `NULL || jsonb` is NULL in Postgres: an
                # attempt that died before writing a digest has exactly the NULL
                # this row has, so the concatenation silently discarded the note
                # in the one case the note exists to explain. The row was left
                # saying nothing at all about why it was interrupted.
                await self.db.execute(
                    "UPDATE action_journal SET ts_end = now(), status = 'interrupted', "
                    "result_digest = COALESCE(result_digest, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2",
                    json.dumps({
                        "note": "reconciled at wake",
                        "outcome": "unknown",
                        "detail": (
                            f"{row['tool']} was still running when the previous "
                            "incarnation ended; whether it took effect is unknown"
                        ),
                    }),
                    row["id"],
                )
                report["reconciled_actions"] += 1
                report.setdefault("interrupted_tools", []).append(str(row["tool"]))
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
