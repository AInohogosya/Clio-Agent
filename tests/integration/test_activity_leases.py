"""Activity leases: claims, fences, transfer, and the refused stale write."""
from __future__ import annotations

import pytest

from clio.core.leases import ActivityLeaseManager

pytestmark = pytest.mark.integration


async def test_claim_heartbeat_and_finish(db):
    leases = ActivityLeaseManager(db)
    fence = await leases.claim("run:t1", "worker-a:1")
    assert fence >= 1
    assert await leases.heartbeat("run:t1", "worker-a:1", fence)
    assert await leases.finish("run:t1", "worker-a:1", fence)
    # A finished lease still refuses heartbeats from its old owner.
    assert not await leases.heartbeat("run:t1", "worker-a:1", fence)
    await db.execute("DELETE FROM activity_leases WHERE lease_key = 'run:t1'")


async def test_a_transfer_bumps_the_fence_and_fences_out_the_old_owner(db):
    leases = ActivityLeaseManager(db)
    fence_a = await leases.claim("run:t2", "worker-a:1")
    fence_b = await leases.transfer("run:t2", "worker-b:2")
    assert fence_b == fence_a + 1

    # The old owner's heartbeat and guarded write are both refused.
    assert not await leases.heartbeat("run:t2", "worker-a:1", fence_a)
    ok = await leases.guarded_write(
        "run:t2", "worker-a:1", fence_a,
        "SELECT 1",  # the statement never runs when the guard refuses
    )
    assert not ok, "a stale zombie's write must be refused at the fence"

    # The new owner works normally, in the same row.
    assert await leases.heartbeat("run:t2", "worker-b:2", fence_b)
    ok = await leases.guarded_update("run:t2", "worker-b:2", fence_b, "status", "active")
    assert ok
    await db.execute("DELETE FROM activity_leases WHERE lease_key = 'run:t2'")


async def test_transfer_all_from_moves_every_lease_a_dead_owner_held(db):
    leases = ActivityLeaseManager(db)
    for key in ("run:x1", "run:x2", "run:x3"):
        await leases.claim(key, "clio-core:999")
    moved = await leases.transfer_all_from("clio-core:999", "clio-core:1000")
    assert moved == 3
    rows = await db.fetch(
        "SELECT lease_owner, lease_fence FROM activity_leases "
        "WHERE lease_key = ANY($1::text[]) ORDER BY lease_key",
        ["run:x1", "run:x2", "run:x3"],
    )
    assert all(r["lease_owner"] == "clio-core:1000" for r in rows)
    assert all(int(r["lease_fence"]) == 2 for r in rows)
    await db.execute(
        "DELETE FROM activity_leases WHERE lease_key = ANY($1::text[])",
        ["run:x1", "run:x2", "run:x3"],
    )


async def test_stale_listing_finds_an_old_heartbeat(db):
    leases = ActivityLeaseManager(db)
    await leases.claim("run:w", "worker-c:3")
    await db.execute(
        "UPDATE activity_leases SET heartbeat_at = now() - interval '5 minutes' "
        "WHERE lease_key = 'run:w'",
    )
    stale = await leases.stale(stale_s=15.0)
    assert any(item["lease_key"] == "run:w" for item in stale)
    await db.execute("DELETE FROM activity_leases WHERE lease_key = 'run:w'")


async def test_guarded_update_refuses_unknown_columns(db):
    leases = ActivityLeaseManager(db)
    fence = await leases.claim("run:col", "worker-d:4")
    with pytest.raises(ValueError):
        await leases.guarded_update("run:col", "worker-d:4", fence, "lease_owner", "me")
    await db.execute("DELETE FROM activity_leases WHERE lease_key = 'run:col'")
