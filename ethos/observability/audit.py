from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from ethos.db import Database

logger = logging.getLogger(__name__)

GENESIS_HASH = b"\x00" * 32
AUDIT_ADVISORY_KEY = 918_273_645


def canonical_record(
    ts: datetime,
    actor: str,
    category: str,
    summary: str,
    details: dict[str, Any],
) -> bytes:
    ts_iso = ts.astimezone(UTC).isoformat()
    details_json = json.dumps(details or {}, sort_keys=True, separators=(",", ":"))
    return "\x1f".join([ts_iso, actor, category, summary, details_json]).encode("utf-8")


def compute_hash(prev_hash: bytes, record: bytes) -> bytes:
    return hashlib.sha256(prev_hash + record).digest()


class AuditEntry(BaseModel):
    seq: int
    ts: datetime
    actor: str
    category: str
    summary: str
    details: dict[str, Any]
    prev_hash: bytes
    hash: bytes


class AuditSink(Protocol):
    async def append(self, entry: AuditEntry) -> None: ...
    async def last(self) -> AuditEntry | None: ...
    async def entries(self, limit: int = 100) -> list[AuditEntry]: ...


class DbAuditSink:
    def __init__(self, db: Database):
        self.db = db

    async def append(self, entry: AuditEntry) -> None:
        async def _tx(conn: Any) -> None:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock($1)", AUDIT_ADVISORY_KEY)
                await conn.execute(
                    "INSERT INTO audit_log (ts, actor, category, summary, details, prev_hash, hash) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)",
                    entry.ts, entry.actor, entry.category, entry.summary,
                    json.dumps(entry.details, sort_keys=True), entry.prev_hash, entry.hash,
                )

        async with self.db.acquire() as conn:
            await _tx(conn)

    async def last(self) -> AuditEntry | None:
        row = await self.db.fetchrow(
            "SELECT seq, ts, actor, category, summary, details, prev_hash, hash "
            "FROM audit_log ORDER BY seq DESC LIMIT 1"
        )
        return self._to_entry(row) if row else None

    async def entries(self, limit: int = 100) -> list[AuditEntry]:
        rows = await self.db.fetch(
            "SELECT seq, ts, actor, category, summary, details, prev_hash, hash "
            "FROM audit_log ORDER BY seq DESC LIMIT $1", limit,
        )
        return [self._to_entry(r) for r in rows]

    @staticmethod
    def _to_entry(row: Any) -> AuditEntry:
        details = row["details"]
        if isinstance(details, str):
            details = json.loads(details)
        return AuditEntry(
            seq=row["seq"], ts=row["ts"], actor=row["actor"], category=row["category"],
            summary=row["summary"], details=dict(details or {}),
            prev_hash=bytes(row["prev_hash"]), hash=bytes(row["hash"]),
        )


class FileAuditSink:
    """Append-only JSONL hash chain for journal-local (no-DB) operation."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> list[AuditEntry]:
        if not self.path.exists():
            return []
        out: list[AuditEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            out.append(AuditEntry(
                seq=data["seq"], ts=datetime.fromisoformat(data["ts"]),
                actor=data["actor"], category=data["category"], summary=data["summary"],
                details=data["details"],
                prev_hash=bytes.fromhex(data["prev_hash"]), hash=bytes.fromhex(data["hash"]),
            ))
        return out

    async def append(self, entry: AuditEntry) -> None:
        line = json.dumps({
            "seq": entry.seq, "ts": entry.ts.isoformat(), "actor": entry.actor,
            "category": entry.category, "summary": entry.summary, "details": entry.details,
            "prev_hash": entry.prev_hash.hex(), "hash": entry.hash.hex(),
        })
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    async def last(self) -> AuditEntry | None:
        entries = self._read_all()
        return entries[-1] if entries else None

    async def entries(self, limit: int = 100) -> list[AuditEntry]:
        return self._read_all()[-limit:]


class AuditLog:
    """Cryptographically linked audit trail [D-33].

    Every entry's hash covers the previous hash, making silent tampering
    detectable; the OOB guardian anchors the chain head out-of-band.
    """

    def __init__(self, sink: AuditSink):
        self.sink = sink
        self._seq: int | None = None
        self._head: bytes = GENESIS_HASH

    async def _prime(self) -> None:
        if self._seq is not None:
            return
        last = await self.sink.last()
        if last is None:
            self._seq = 0
            self._head = GENESIS_HASH
        else:
            self._seq = last.seq
            self._head = last.hash

    async def append(
        self,
        actor: str,
        category: str,
        summary: str,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        await self._prime()
        ts = datetime.now(UTC)
        record = canonical_record(ts, actor, category, summary, details or {})
        prev = self._head
        digest = compute_hash(prev, record)
        self._seq = (self._seq or 0) + 1
        entry = AuditEntry(
            seq=self._seq, ts=ts, actor=actor, category=category,
            summary=summary, details=details or {}, prev_hash=prev, hash=digest,
        )
        await self.sink.append(entry)
        self._head = digest
        logger.debug("audit.appended seq=%s category=%s", entry.seq, category)
        return entry

    async def verify_chain(self, limit: int = 1000) -> bool:
        entries = await self.sink.entries(limit)
        entries = sorted(entries, key=lambda e: e.seq)
        if not entries:
            return True
        prev = entries[0].prev_hash if entries[0].seq > 1 else GENESIS_HASH
        for e in entries:
            record = canonical_record(e.ts, e.actor, e.category, e.summary, e.details)
            if compute_hash(prev, record) != e.hash:
                return False
            prev = e.hash
        return True

    async def anchor_state(self) -> dict[str, Any]:
        last = await self.sink.last()
        if last is None:
            return {"seq": 0, "hash": GENESIS_HASH.hex(), "ts": None}
        return {"seq": last.seq, "hash": last.hash.hex(), "ts": last.ts.isoformat()}

    @property
    def head(self) -> bytes:
        return self._head
