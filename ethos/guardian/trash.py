from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ethos.db import Database
from ethos.observability.logger import get_logger

logger = get_logger("ethos.guardian.trash")


class TrashError(RuntimeError):
    pass


class TrashEntry:
    def __init__(self, data: dict[str, Any]):
        self.id: str = data["id"]
        self.origin: str = data["origin"]
        self.name: str = data["name"]
        self.trashed_at: datetime = data["trashed_at"]
        self.retention_until: datetime = data["retention_until"]
        self.kind: str = data["kind"]
        self.reason: str = data["reason"]
        self.fingerprint: str | None = data.get("fingerprint")
        self.intention_id: str | None = data.get("intention_id")
        self.path: Path = Path(data["path"])
        self.is_dir: bool = data.get("is_dir", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "origin": self.origin,
            "name": self.name,
            "trashed_at": self.trashed_at.isoformat(),
            "retention_until": self.retention_until.isoformat(),
            "kind": self.kind,
            "reason": self.reason,
            "fingerprint": self.fingerprint,
            "intention_id": self.intention_id,
            "path": str(self.path),
            "is_dir": self.is_dir,
        }


def _fingerprint_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


class TrashStore:
    """Reversible deletion: fast path 30-day retention, due-process 180-day [D-26, D-28].

    Files are never destroyed by the agent's own will; they are moved, hashed,
    and manifested so any trash action is a single `restore()` away from undone.
    """

    def __init__(
        self,
        trash_dir: Path,
        db: Database | None = None,
        fast_retention_days: int = 30,
        due_retention_days: int = 180,
    ):
        self.trash_dir = Path(trash_dir).expanduser()
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.fast_retention_days = fast_retention_days
        self.due_retention_days = due_retention_days

    def retention_for(self, kind: str) -> int:
        return self.due_retention_days if kind == "due_process" else self.fast_retention_days

    async def move(
        self,
        target: Path,
        *,
        kind: str,
        reason: str,
        intention_id: str | None = None,
        thread_id: str | None = None,
    ) -> TrashEntry:
        target = Path(target).expanduser()
        if not target.exists() and not target.is_symlink():
            raise TrashError(f"cannot trash non-existent path: {target}")
        entry_id = uuid.uuid4().hex[:16]
        dest_dir = self.trash_dir / entry_id
        dest_dir.mkdir(parents=True, exist_ok=False)
        moved = dest_dir / target.name
        await asyncio.to_thread(shutil.move, str(target), str(moved))
        trashed_at = datetime.now(UTC)
        retention_until = trashed_at + timedelta(days=self.retention_for(kind))
        fingerprint = None
        is_dir = moved.is_dir()
        if not is_dir:
            fingerprint = await asyncio.to_thread(_fingerprint_file, moved)
        entry = TrashEntry({
            "id": entry_id,
            "origin": str(target),
            "name": target.name,
            "trashed_at": trashed_at,
            "retention_until": retention_until,
            "kind": kind,
            "reason": reason,
            "fingerprint": fingerprint,
            "intention_id": intention_id,
            "path": str(moved),
            "is_dir": is_dir,
        })
        manifest_path = dest_dir / "manifest.json"
        await asyncio.to_thread(
            manifest_path.write_text,
            json.dumps(entry.to_dict(), indent=2),
        )
        if self.db is not None:
            try:
                await self.db.execute(
                    "INSERT INTO env_entities (id, kind, key, attrs, owner, last_verified_at, health) "
                    "VALUES ($1, 'trash', $2, $3::jsonb, 'guardian', now(), 'ok') "
                    "ON CONFLICT (key) DO UPDATE SET attrs = $3::jsonb, last_verified_at = now()",
                    uuid.uuid4(), entry_id, json.dumps(entry.to_dict()),
                )
            except Exception:
                logger.exception("trash.db_record_failed")
        del thread_id
        logger.info("trash.moved", origin=str(target), kind=kind, entry=entry_id)
        return entry

    async def restore(self, entry_id: str) -> Path:
        entry = await self.get(entry_id)
        if entry is None:
            raise TrashError(f"unknown trash entry: {entry_id}")
        origin = Path(entry.origin).expanduser()
        origin.parent.mkdir(parents=True, exist_ok=True)
        final = origin
        if origin.exists():
            final = origin.with_name(origin.name + f".restored-{uuid.uuid4().hex[:6]}")
        await asyncio.to_thread(shutil.move, str(entry.path), str(final))
        manifest = entry.path.parent / "manifest.json"
        if manifest.exists():
            await asyncio.to_thread(os.remove, manifest)
        try:
            await asyncio.to_thread(os.rmdir, entry.path.parent)
        except OSError:
            pass
        if self.db is not None:
            try:
                await self.db.execute("DELETE FROM env_entities WHERE kind = 'trash' AND key = $1", entry_id)
            except Exception:
                logger.exception("trash.db_delete_failed")
        logger.info("trash.restored", entry=entry_id, target=str(final))
        return final

    async def get(self, entry_id: str) -> TrashEntry | None:
        if self.db is not None:
            row = await self.db.fetchrow(
                "SELECT attrs::text AS attrs FROM env_entities WHERE kind = 'trash' AND key = $1", entry_id,
            )
            if row is not None:
                data = json.loads(row["attrs"]) if isinstance(row["attrs"], str) else dict(row["attrs"])
                data["trashed_at"] = datetime.fromisoformat(data["trashed_at"])
                data["retention_until"] = datetime.fromisoformat(data["retention_until"])
                return TrashEntry(data)
        manifest = self.trash_dir / entry_id / "manifest.json"
        if not manifest.exists():
            return None
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["trashed_at"] = datetime.fromisoformat(data["trashed_at"])
        data["retention_until"] = datetime.fromisoformat(data["retention_until"])
        return TrashEntry(data)

    async def list(self, limit: int = 100) -> list[TrashEntry]:
        entries: list[TrashEntry] = []
        if self.db is not None:
            try:
                rows = await self.db.fetch(
                    "SELECT attrs::text AS attrs FROM env_entities WHERE kind = 'trash' ORDER BY key LIMIT $1",
                    limit,
                )
                for row in rows:
                    data = json.loads(row["attrs"]) if isinstance(row["attrs"], str) else dict(row["attrs"])
                    data["trashed_at"] = datetime.fromisoformat(data["trashed_at"])
                    data["retention_until"] = datetime.fromisoformat(data["retention_until"])
                    entries.append(TrashEntry(data))
                if entries:
                    return entries
            except Exception:
                logger.exception("trash.db_list_failed")
        if self.trash_dir.exists():
            for child in sorted(self.trash_dir.iterdir()):
                manifest = child / "manifest.json"
                if manifest.exists():
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                    data["trashed_at"] = datetime.fromisoformat(data["trashed_at"])
                    data["retention_until"] = datetime.fromisoformat(data["retention_until"])
                    entries.append(TrashEntry(data))
                    if len(entries) >= limit:
                        break
        return entries

    async def purge_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        purged = 0
        for entry in await self.list(limit=1000):
            if entry.retention_until <= now:
                await asyncio.to_thread(shutil.rmtree, entry.path.parent, True)
                if self.db is not None:
                    try:
                        await self.db.execute(
                            "DELETE FROM env_entities WHERE kind = 'trash' AND key = $1", entry.id,
                        )
                    except Exception:
                        pass
                purged += 1
        return purged
