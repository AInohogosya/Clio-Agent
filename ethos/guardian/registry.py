from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Protocol

from ethos.db import Database
from ethos.observability.logger import get_logger

logger = get_logger("ethos.guardian.registry")


class RegistryProtocol(Protocol):
    async def is_protected(self, path: str | Path) -> dict[str, Any] | None: ...
    async def register(self, uri: str, *, kind: str, origin: str,
                       commissioned_by: str | None, intention_id: str | None,
                       protection: str, notes: str | None = None) -> str: ...


def fingerprint_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


class ArtifactRegistry:
    """Registry of commissioned/pinned artifacts [D-27].

    Anything produced under an active commission (intention.commissioned_by set)
    is auto-registered with protection='commissioned' and is subject to due
    process before any destructive operation touches it (H5).

    This is a list of things that were asked for, not a boundary of where the
    agent may go. It used to take a `roots` argument, and it read well as the
    roots the agent was confined to — which was the problem: it was stored and
    never consulted, so it widened nothing and narrowed nothing while implying to
    every reader that some confinement existed. It has been removed rather than
    documented, because a parameter whose name is a promise the code does not
    keep is worse than no parameter at all. An artifact outside `~/workspace`
    and `~/work` is registered and protected exactly as one inside them is;
    `is_protected` matches by path prefix and by ancestor, which is the whole of
    its logic.
    """

    def __init__(self, db: Database):
        self.db = db

    async def register(
        self,
        uri: str,
        *,
        kind: str = "file",
        origin: str = "unknown",
        commissioned_by: str | None = None,
        intention_id: str | None = None,
        protection: str = "internal",
        notes: str | None = None,
    ) -> str:
        artifact_id = uuid.uuid4()
        path = Path(uri).expanduser()
        fingerprint = None
        if path.is_file():
            fingerprint = fingerprint_file(path)
        await self.db.execute(
            "INSERT INTO artifacts (id, uri, kind, origin, commissioned_by, intention_id, "
            "protection, fingerprint, created_at, last_seen_at, notes) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), now(), $9)",
            artifact_id, str(path), kind, origin, commissioned_by, intention_id,
            protection, fingerprint, notes,
        )
        return str(artifact_id)

    async def is_protected(self, path: str | Path) -> dict[str, Any] | None:
        target = Path(path).expanduser()
        rows = await self.db.fetch(
            "SELECT id::text, uri, kind, origin, commissioned_by, intention_id::text, protection, "
            "fingerprint, notes FROM artifacts "
            "WHERE protection IN ('commissioned', 'pinned') AND uri LIKE $1 || '%' "
            "ORDER BY created_at DESC LIMIT 20",
            str(target),
        )
        for row in rows:
            uri = Path(row["uri"]).expanduser()
            if target == uri or uri in target.parents or target in uri.parents:
                return dict(row)
        return None

    async def auto_register_write(
        self,
        path: str | Path,
        *,
        commissioned_by: str | None,
        intention_id: str | None,
        kind: str = "file",
    ) -> str | None:
        if commissioned_by is None:
            return None
        target = Path(path).expanduser()
        if not target.exists():
            return None
        existing = await self.db.fetchrow(
            "SELECT id::text FROM artifacts WHERE uri = $1 AND intention_id::text = $2 AND "
            "protection = 'commissioned'",
            str(target), str(intention_id),
        )
        if existing is not None:
            await self.db.execute(
                "UPDATE artifacts SET last_seen_at = now(), fingerprint = $2 WHERE uri = $1 AND "
                "intention_id::text = $3",
                str(target), fingerprint_file(target), str(intention_id),
            )
            return existing["id"]
        return await self.register(
            str(target), kind=kind, origin="commission_work",
            commissioned_by=commissioned_by, intention_id=intention_id,
            protection="commissioned",
        )

    async def touch(self, path: str | Path) -> None:
        target = Path(path).expanduser()
        await self.db.execute(
            "UPDATE artifacts SET last_seen_at = now(), fingerprint = $2 WHERE uri = $1",
            str(target), fingerprint_file(target) if target.is_file() else None,
        )

    async def list(self, limit: int = 200, protection: str | None = None) -> list[dict[str, Any]]:
        if protection:
            rows = await self.db.fetch(
                "SELECT id::text, uri, kind, origin, commissioned_by, intention_id::text, protection, "
                "fingerprint, created_at::text, last_seen_at::text, notes FROM artifacts "
                "WHERE protection = $1 ORDER BY created_at DESC LIMIT $2", protection, limit,
            )
        else:
            rows = await self.db.fetch(
                "SELECT id::text, uri, kind, origin, commissioned_by, intention_id::text, protection, "
                "fingerprint, created_at::text, last_seen_at::text, notes FROM artifacts "
                "ORDER BY created_at DESC LIMIT $1", limit,
            )
        return [dict(r) for r in rows]

    async def verify_fingerprints(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT id::text, uri, fingerprint FROM artifacts WHERE fingerprint IS NOT NULL LIMIT $1", limit,
        )
        mismatches = []
        for row in rows:
            current = fingerprint_file(Path(row["uri"]).expanduser())
            if current is not None and current != row["fingerprint"]:
                mismatches.append({"artifact_id": row["id"], "uri": row["uri"]})
        return mismatches
