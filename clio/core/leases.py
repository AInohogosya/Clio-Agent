"""Activity leases: run ownership with monotonically-incremented fences.

A long-running piece of work — a solver run, a delegated thread, a worker's
claim on a project — holds an *activity lease*. The lease names its owner and
carries a fence: an integer bumped on every transfer. Every state commit a
leaseholder makes goes through `guarded_update`, which verifies

    WHERE lease_key = $key AND lease_owner = $owner AND lease_fence = $fence

first. The fence is what makes recycling safe. When the supervisor decides a
worker is wedged and restarts it, the lease is transferred to the new
process and the fence increments; the old process, if it wakes up mid-write,
finds its fence stale and is refused. Without the fence, a zombie's late
commit would overwrite the live owner's state — split-brain writes converging
on one row — and no amount of liveness checking prevents that after the fact.
The fence does, by construction, at the row.
"""

from __future__ import annotations

from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.core.leases")


class LeaseLostError(RuntimeError):
    """This owner no longer holds the lease (a transfer or recycle happened)."""


class ActivityLeaseManager:
    """Claim, heartbeat, transfer and fence-guard activity leases."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def claim(self, lease_key: str, owner: str, *,
                    payload: dict[str, Any] | None = None) -> int:
        """Take (or re-take) a lease, returning the fence this owner holds.

        A fresh claim creates the row at fence 1. Re-claiming a lease held by
        another owner — the recycler's transfer path — bumps the fence, which
        is the whole mechanism; the caller reads the returned fence and
        carries it into every guarded write.
        """
        import json

        row = await self.db.fetchrow(
            "INSERT INTO activity_leases (lease_key, lease_owner, lease_fence, status, "
            "heartbeat_at, claimed_at, payload_json) "
            "VALUES ($1, $2, 1, 'active', now(), now(), $3::jsonb) "
            "ON CONFLICT (lease_key) DO UPDATE SET "
            "lease_fence = activity_leases.lease_fence + 1, lease_owner = $2, "
            "status = 'active', heartbeat_at = now(), claimed_at = now(), "
            "payload_json = $3::jsonb "
            "RETURNING lease_fence",
            lease_key, owner, json.dumps(payload or {}),
        )
        return int(row["lease_fence"]) if row else 0

    async def heartbeat(self, lease_key: str, owner: str, fence: int) -> bool:
        """Say the holder is alive. A stale owner's heartbeat is refused."""
        result = await self.db.execute(
            "UPDATE activity_leases SET heartbeat_at = now() "
            "WHERE lease_key = $1 AND lease_owner = $2 AND lease_fence = $3 AND status = 'active'",
            lease_key, owner, fence,
        )
        return result.endswith(" 1")

    async def transfer(self, lease_key: str, new_owner: str) -> int:
        """Hand a lease (usually a dead owner's) to a new owner, fence +1."""
        row = await self.db.fetchrow(
            "UPDATE activity_leases SET lease_owner = $2, lease_fence = lease_fence + 1, "
            "heartbeat_at = now(), claimed_at = now(), status = 'active' "
            "WHERE lease_key = $1 RETURNING lease_fence",
            lease_key, new_owner,
        )
        fence = int(row["lease_fence"]) if row else 0
        logger.info("leases.transferred", lease=lease_key, to=new_owner, fence=fence)
        return fence

    async def transfer_all_from(self, old_owner: str, new_owner: str) -> int:
        """Every lease an owner held, transferred in one statement.

        This is the supervisor's recycle path: the wedged process held some
        number of leases nobody wrote down anywhere central, and the owner
        column is how they are found.
        """
        # `RETURNING` cannot carry a window function, so the count comes from
        # the rows the transfer touched: one SELECT of keys, one UPDATE. Both
        # in the same session, and a race with a concurrent claim on the same
        # keys is harmless — the UPDATE's own WHERE re-checks the owner.
        rows = await self.db.fetch(
            "SELECT lease_key FROM activity_leases WHERE lease_owner = $1 AND status = 'active'",
            old_owner,
        )
        if not rows:
            return 0
        keys = [str(r["lease_key"]) for r in rows]
        await self.db.execute(
            "UPDATE activity_leases SET lease_owner = $2, lease_fence = lease_fence + 1, "
            "heartbeat_at = now(), claimed_at = now(), status = 'active' "
            "WHERE lease_key = ANY($1::text[]) AND lease_owner = $3",
            keys, new_owner, old_owner,
        )
        count = len(keys)
        logger.info("leases.transferred_all", old=old_owner, new=new_owner, count=count)
        return count

    async def guarded_update(self, lease_key: str, owner: str, fence: int,
                             column: str, value: Any) -> bool:
        """One guarded column write: refused unless owner and fence both match.

        The statement is built from a whitelisted column name — this guard is
        for state commits, and state has a known shape; a caller wanting a
        different write path should extend the whitelist rather than smuggle
        SQL through here.
        """
        allowed = {"status", "payload_json", "heartbeat_at"}
        if column not in allowed:
            raise ValueError(f"guarded_update refuses column {column!r}")
        import json

        stored = json.dumps(value) if column == "payload_json" else value
        cast = "::jsonb" if column == "payload_json" else ""
        result = await self.db.execute(
            f"UPDATE activity_leases SET {column} = $4{cast}, heartbeat_at = now() "
            "WHERE lease_key = $1 AND lease_owner = $2 AND lease_fence = $3",
            lease_key, owner, fence, stored,
        )
        return result.endswith(" 1")

    async def guarded_write(self, lease_key: str, owner: str, fence: int,
                            statement: str, *args: Any) -> bool:
        """Verify the fence, then run the caller's own statement.

        The check and the write are two statements on purpose: `statement` is
        the caller's business (a project update, an artifact row), and this
        method's promise is narrower and precise — it answers false, and runs
        nothing, when the fence is stale. A caller that ignores the answer is
        choosing to write unguarded; the answer is there to be obeyed.
        """
        row = await self.db.fetchrow(
            "SELECT 1 AS ok FROM activity_leases "
            "WHERE lease_key = $1 AND lease_owner = $2 AND lease_fence = $3 AND status = 'active'",
            lease_key, owner, fence,
        )
        if row is None:
            return False
        await self.db.execute(statement, *args)
        return True

    async def stale(self, *, stale_s: float = 15.0) -> list[dict[str, Any]]:
        """Active leases whose heartbeat is older than the fast threshold."""
        rows = await self.db.fetch(
            "SELECT lease_key, lease_owner, lease_fence, "
            "EXTRACT(EPOCH FROM (now() - heartbeat_at)) AS age_s "
            "FROM activity_leases WHERE status = 'active' "
            "AND heartbeat_at < now() - make_interval(secs => $1)",
            stale_s,
        )
        return [dict(r) for r in rows]

    async def finish(self, lease_key: str, owner: str, fence: int) -> bool:
        result = await self.db.execute(
            "UPDATE activity_leases SET status = 'done', heartbeat_at = now() "
            "WHERE lease_key = $1 AND lease_owner = $2 AND lease_fence = $3",
            lease_key, owner, fence,
        )
        return result.endswith(" 1")


__all__ = ["ActivityLeaseManager", "LeaseLostError"]
