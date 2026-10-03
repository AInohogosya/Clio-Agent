from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ethos.config import EthosConfig
from ethos.db import Database
from ethos.observability.logger import get_logger

logger = get_logger("ethos.guardian.snapshots")


class SnapshotError(RuntimeError):
    pass


class SnapshotManager:
    """Layer-3/4 reversibility: filesystem snapshots.

    On the production VM this drives btrfs subvolume snapshots (optionally via
    snapper). On other filesystems it falls back to hardlink-tree snapshots so
    the guardian's reversibility contract holds everywhere [D-25, D-26].
    """

    def __init__(self, config: EthosConfig, db: Database | None = None):
        self.config = config
        self.db = db
        self.snapshots_dir = config.paths.snapshots.expanduser()
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.policy = config.permissions.guardian.snapshot_policy
        self.backend = self._detect_backend()

    def _detect_backend(self) -> str:
        try:
            result = subprocess.run(
                ["btrfs", "subvolume", "list", self.config.paths.home.expanduser().parent.as_posix()],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return "btrfs"
        except (OSError, subprocess.TimeoutExpired):
            pass
        return "tree"

    @staticmethod
    def _name(reason: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)[:40]
        return f"{stamp}-{safe}-{uuid.uuid4().hex[:6]}"

    async def create(self, reason: str, source: Path | None = None) -> dict[str, Any]:
        source = (source or self.config.paths.home).expanduser()
        name = self._name(reason)
        target = self.snapshots_dir / name
        if self.backend == "btrfs":
            cmd = ["sudo", "-n", "btrfs", "subvolume", "snapshot", "-r", str(source), str(target)]
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise SnapshotError(f"btrfs snapshot failed: {result.stderr[:300]}")
        else:
            await asyncio.to_thread(self._tree_snapshot, source, target)
        record = {
            "name": name, "backend": self.backend, "reason": reason,
            "source": str(source), "created_at": datetime.now(UTC).isoformat(),
        }
        if self.db is not None:
            try:
                await self.db.execute(
                    "INSERT INTO env_entities (id, kind, key, attrs, owner, last_verified_at, health) "
                    "VALUES ($1, 'snapshot', $2, $3::jsonb, 'guardian', now(), 'ok')",
                    uuid.uuid4(), name, json.dumps(record),
                )
            except Exception:
                logger.exception("snapshots.db_record_failed")
        logger.info("snapshots.created", name=name, backend=self.backend)
        await self._prune()
        return record

    def _tree_snapshot(self, source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=False)
        for root, dirs, files in os.walk(source):
            rel_root = Path(root).relative_to(source)
            (target / rel_root).mkdir(parents=True, exist_ok=True)
            for d in dirs:
                (target / rel_root / d).mkdir(exist_ok=True)
            for f in files:
                src = Path(root) / f
                dst = target / rel_root / f
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
        marker = target / ".ethos-snapshot.json"
        marker.write_text(
            json.dumps({"backend": "tree", "source": str(source)}), encoding="utf-8",
        )

    def _snapshot_age_hours(self, name: str) -> float:
        try:
            stamp = datetime.strptime(name.split("-")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            return (datetime.now(UTC) - stamp).total_seconds() / 3600.0
        except (ValueError, IndexError):
            return 1e9

    async def _prune(self) -> None:
        entries = await self.list(limit=1000)
        age_sorted = sorted(entries, key=lambda e: e["name"], reverse=True)
        recent = [e for e in age_sorted if self._snapshot_age_hours(e["name"]) <= 0.3]
        hourly = [e for e in age_sorted if 0.3 < self._snapshot_age_hours(e["name"]) <= 25]
        daily = [e for e in age_sorted if self._snapshot_age_hours(e["name"]) > 25]
        keep_recent = int(self.policy.get("minutes_15", 4))
        keep_hourly = int(self.policy.get("hourly", 24))
        keep_daily = int(self.policy.get("daily", 7))
        victims = recent[keep_recent:] + hourly[keep_hourly:] + daily[keep_daily:]
        for victim in victims:
            await asyncio.to_thread(shutil.rmtree, self.snapshots_dir / victim["name"], True)
            if self.db is not None:
                try:
                    await self.db.execute(
                        "DELETE FROM env_entities WHERE kind = 'snapshot' AND key = $1", victim["name"],
                    )
                except Exception:
                    pass

    async def list(self, limit: int = 100) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self.db is not None:
            try:
                rows = await self.db.fetch(
                    "SELECT attrs::text AS attrs FROM env_entities WHERE kind = 'snapshot' LIMIT $1", limit,
                )
                for row in rows:
                    data = json.loads(row["attrs"]) if isinstance(row["attrs"], str) else dict(row["attrs"])
                    out.append(data)
                if out:
                    return out
            except Exception:
                logger.exception("snapshots.db_list_failed")
        if self.snapshots_dir.exists():
            for child in sorted(self.snapshots_dir.iterdir(), reverse=True):
                if child.is_dir():
                    out.append({
                        "name": child.name, "backend": self.backend,
                        "created_at": datetime.now(UTC).isoformat(),
                    })
                    if len(out) >= limit:
                        break
        return out

    async def restore(self, name: str) -> Path:
        snapshot_dir = self.snapshots_dir / name
        if not snapshot_dir.exists():
            raise SnapshotError(f"snapshot {name} not found")
        if self.backend == "btrfs":
            raise SnapshotError("btrfs snapshot restore must be performed by the operator (L4)")
        restored = self.config.paths.work / f"restored-{name}"
        await asyncio.to_thread(shutil.copytree, snapshot_dir, restored, dirs_exist_ok=True)
        return restored
