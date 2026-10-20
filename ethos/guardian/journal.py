from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ethos.db import Database
from ethos.observability.logger import get_logger

logger = get_logger("ethos.guardian.journal")


class DuplicateAction(Exception):
    """A prior attempt already owns this idempotency key.

    `prior` is that attempt's journal row. Whether the new call may proceed is
    not a property of the key but of the row's `status`, which is why the row
    travels with the exception: `replayable(prior)` is the only question a
    caller should have to ask.
    """

    def __init__(self, prior: dict[str, Any]):
        super().__init__("idempotency key already used")
        self.prior = prior


# The journal's terminal statuses. A row in any other state is an attempt whose
# fate is unknown -- it is still `running`, or a previous incarnation died and
# left it that way.
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_REJECTED = "rejected"
STATUS_INTERRUPTED = "interrupted"

#: Statuses that are a decided outcome: the attempt finished, and the answer is
#: whatever it finished with.
TERMINAL_STATUSES = frozenset({STATUS_OK, STATUS_FAILED, STATUS_TIMEOUT, STATUS_REJECTED})


def replayable(prior: dict[str, Any] | None) -> bool:
    """Whether `prior`'s outcome may be handed back in place of a re-execution.

    Only a completed success. This is the whole invariant the idempotency key
    exists to protect, and it is deliberately narrow:

    * `ok` -- the work demonstrably happened. Replaying it is the point: a
      duplicate call returns the first call's answer instead of doing it twice.
    * `failed` / `timeout` / `rejected` -- the attempt finished and did *not*
      do the work. These are retries, not duplicates. Replaying them would
      report a failure as though it were the original outcome, and -- because
      the caller reads `ok=True` off the envelope -- would convert "it broke"
      into "it worked", which is the one conversion nothing downstream can undo.
    * `running` / `interrupted` (or any unknown status) -- the attempt may or
      may not have taken effect. This is `STATUS_OUTCOME_UNKNOWN` in the
      sub-agent vocabulary, and it is not the same as either of the above: the
      work might be half-done. Reporting success would be a lie; reporting
      failure would invite a blind re-execution over a half-applied change.
      Neither is this layer's to decide, so it is not replayed at all.
    """
    return bool(prior) and prior.get("status") == STATUS_OK


class ActionJournal:
    """Write-ahead action journal [D-26, Section 9].

    `begin()` is written BEFORE execution (WAL): a crash mid-action leaves a
    `running` row that `wake_up()` reconciliation marks as interrupted.
    Idempotency keys make retries safe: the same key never executes twice.
    """

    def __init__(self, db: Database | None = None, file_path: Path | None = None):
        self.db = db
        self.file_path = Path(file_path) if file_path else None
        if self.file_path is not None:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)

    async def _file_all(self) -> list[dict[str, Any]]:
        if self.file_path is None or not self.file_path.exists():
            return []
        out = []
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    async def _file_append(self, record: dict[str, Any]) -> None:
        assert self.file_path is not None
        with open(self.file_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    async def _file_update(self, action_id: str, patch: dict[str, Any]) -> None:
        records = await self._file_all()
        for record in records:
            if record.get("id") == action_id:
                record.update(patch)
        tmp = self.file_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.file_path)

    async def find_by_idempotency(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        if self.db is not None:
            row = await self.db.fetchrow(
                "SELECT id, ts_start::text, ts_end::text, thread_id, tool, args_redacted::text, "
                "status, result_digest::text, undo_ref FROM action_journal WHERE idempotency_key = $1",
                key,
            )
            if row is not None:
                return dict(row)
            return None
        for record in await self._file_all():
            if record.get("idempotency_key") == key:
                return record
        return None

    async def _release(self, prior: dict[str, Any]) -> None:
        """Detach a decided non-success from its key so the retry can claim it.

        The column is `UNIQUE`, so a failed attempt left its key permanently
        unusable: every later attempt to do the same work collided with the
        corpse of the attempt that had already failed. Releasing sets the key
        to NULL rather than deleting the row -- the row is the record of what
        was tried and how it went, and a journal that drops it to make room for
        a retry is not a journal.

        An indeterminate row (`running`, `interrupted`) is never released here.
        Its attempt may still be in effect somewhere, and handing its key to
        another attempt is how two attempts come to own one piece of work.
        """
        action_id = prior.get("id") if isinstance(prior, dict) else None
        if not action_id:
            return
        if self.db is not None:
            await self.db.execute(
                "UPDATE action_journal SET idempotency_key = NULL WHERE id = $1",
                action_id,
            )
            return
        await self._file_update(action_id, {"idempotency_key": None})

    async def begin(
        self,
        *,
        thread_id: str,
        intention_id: str | None,
        tool: str,
        args_redacted: dict[str, Any],
        reason: str,
        idempotency_key: str | None,
        side_effects: list[str],
    ) -> str:
        action_id = uuid.uuid4().hex[:20]
        if idempotency_key:
            prior = await self.find_by_idempotency(idempotency_key)
            if prior is not None:
                if replayable(prior):
                    raise DuplicateAction(prior)
                if prior.get("status") in TERMINAL_STATUSES:
                    # A decided non-success: this is the retry it was blocking,
                    # so the key is released and the retry is journalled as its
                    # own attempt rather than answered with the old failure.
                    await self._release(prior)
                else:
                    # Indeterminate (`running`, `interrupted`). The attempt may
                    # still be in effect, so it is neither replayed nor
                    # released: re-executing here would put two attempts on one
                    # piece of work, and the key is UNIQUE so a second row
                    # could not be written anyway. The caller is handed the row
                    # so it can report an unknown outcome instead of a success.
                    raise DuplicateAction(prior)
        if self.db is not None:
            await self.db.execute(
                "INSERT INTO action_journal (id, ts_start, thread_id, intention_id, tool, "
                "args_redacted, reason, idempotency_key, side_effects, status) "
                "VALUES ($1, now(), $2, $3, $4, $5::jsonb, $6, $7, $8, 'running')",
                action_id, thread_id, intention_id, tool,
                json.dumps(args_redacted, default=str), reason, idempotency_key, side_effects,
            )
        else:
            await self._file_append({
                "id": action_id,
                "ts_start": datetime.now(UTC).isoformat(),
                "ts_end": None,
                "thread_id": thread_id,
                "intention_id": intention_id,
                "tool": tool,
                "args_redacted": args_redacted,
                "reason": reason,
                "idempotency_key": idempotency_key,
                "side_effects": side_effects,
                "status": "running",
                "result_digest": None,
                "undo_ref": None,
            })
        return action_id

    async def end(
        self,
        action_id: str,
        *,
        status: str,
        result_digest: dict[str, Any],
        undo_ref: str | None = None,
    ) -> None:
        if self.db is not None:
            await self.db.execute(
                "UPDATE action_journal SET ts_end = now(), status = $1, result_digest = $2::jsonb, "
                "undo_ref = $3 WHERE id = $4",
                status, json.dumps(result_digest, default=str), undo_ref, action_id,
            )
        else:
            await self._file_update(action_id, {
                "ts_end": datetime.now(UTC).isoformat(),
                "status": status,
                "result_digest": result_digest,
                "undo_ref": undo_ref,
            })

    async def get(self, action_id: str) -> dict[str, Any] | None:
        if self.db is not None:
            row = await self.db.fetchrow(
                "SELECT * FROM action_journal WHERE id = $1", action_id,
            )
            return dict(row) if row is not None else None
        for record in await self._file_all():
            if record.get("id") == action_id:
                return record
        return None

    async def list(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        if self.db is not None:
            if status:
                rows = await self.db.fetch(
                    "SELECT * FROM action_journal WHERE status = $1 ORDER BY ts_start DESC LIMIT $2",
                    status, limit,
                )
            else:
                rows = await self.db.fetch(
                    "SELECT * FROM action_journal ORDER BY ts_start DESC LIMIT $1", limit,
                )
            return [dict(r) for r in rows]
        records = await self._file_all()
        if status:
            records = [r for r in records if r.get("status") == status]
        return records[-limit:]
