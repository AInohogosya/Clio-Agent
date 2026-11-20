from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from clio.config import ClioConfig
from clio.db import Database
from clio.observability.logger import get_logger

logger = get_logger("clio.guardian.integrity")


class IntegrityScanner:
    """Nightly defense-in-depth verification (L5): artifacts, trash, snapshots,
    audit chain. Findings are published to the dashboard and audit trail."""

    def __init__(self, config: ClioConfig, registry: Any, trash: Any, snapshots: Any, audit: Any, db: Database | None = None):
        self.config = config
        self.registry = registry
        self.trash = trash
        self.snapshots = snapshots
        self.audit = audit
        self.db = db

    async def run_nightly(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        report: dict[str, Any] = {
            "ran_at": now.isoformat(),
            "artifact_mismatches": [],
            "trash_issues": [],
            "snapshot_count": 0,
            "audit_chain_ok": True,
            "ok": True,
        }
        try:
            report["artifact_mismatches"] = await self.registry.verify_fingerprints()
        except Exception:
            logger.exception("integrity.artifacts_failed")
            report["artifact_mismatches"] = ["scan-failed"]
        for entry in await self.trash.list(limit=500):
            if not entry.path.exists():
                report["trash_issues"].append(entry.id)
        try:
            report["snapshot_count"] = len(await self.snapshots.list())
        except Exception:
            logger.exception("integrity.snapshots_failed")
        try:
            report["audit_chain_ok"] = await self.audit.verify_chain()
        except Exception:
            logger.exception("integrity.audit_failed")
            report["audit_chain_ok"] = False
        report["ok"] = (
            not report["artifact_mismatches"]
            and not report["trash_issues"]
            and report["audit_chain_ok"]
        )
        if self.db is not None:
            try:
                await self.db.upsert_env(
                    "integrity", "report", report, owner="guardian",
                    health="ok" if report["ok"] else "alert", update_health=True,
                )
            except Exception:
                logger.exception("integrity.db_record_failed")
        if not report["ok"]:
            await self.audit.append(
                actor="guardian", category="integrity",
                summary="nightly integrity scan found issues",
                details=report,
            )
        logger.info("integrity.done", ok=report["ok"])
        return report
