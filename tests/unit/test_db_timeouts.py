"""Tests: statement-scoped query classes and writes that survive a cancel.

The postgres log showed memory inserts dying as "canceling statement due
to user request": a stop cancels the cycle task wherever it happens to be,
and asyncpg escalates the cancellation of an in-flight INSERT into a
server-side cancel. These tests pin the two behaviors that fix that -
`execute_uncancelled` finishing its single write, and per-class
`timeout_s` staying scoped to its own transaction.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress

import asyncpg
import pytest

from clio.db import Database

TABLE = "_probe_query_classes"


@pytest.fixture()
async def probe(db: Database):
    await db.execute(
        f"CREATE TABLE {TABLE} (id int PRIMARY KEY, note text)")
    yield TABLE
    with suppress(Exception):
        await db.execute(f"DROP TABLE {TABLE}")


async def test_execute_uncancelled_finishes_the_write(db: Database, probe: str):
    """The cycle was cancelled mid-insert; the row must exist anyway."""
    task = asyncio.create_task(db.execute_uncancelled(
        f"INSERT INTO {probe} (id, note) VALUES (7, 'kept')"))
    await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    assert await db.fetchval(
        f"SELECT count(*) FROM {probe} WHERE id = 7") == 1


async def test_execute_uncancelled_returns_when_not_cancelled(
        db: Database, probe: str):
    out = await db.execute_uncancelled(
        f"INSERT INTO {probe} (id, note) VALUES (1, 'plain')")
    assert "INSERT" in out


async def test_timeout_s_bounds_one_query(db: Database, probe: str):
    """A slow statement in a bounded class is shed quickly, not after the
    pool-wide 30 s command timeout."""
    with pytest.raises(asyncpg.PostgresError):
        await db.fetch("SELECT pg_sleep(3)", timeout_s=0.2)
    # The bound was LOCAL: the connection is untouched for everyone else.
    assert await db.fetchval(f"SELECT count(*) FROM {probe}") == 0


async def test_timeout_s_passes_normal_queries(db: Database, probe: str):
    await db.execute(
        f"INSERT INTO {probe} (id, note) VALUES ($1, $2)", 2, "bounded")
    rows = await db.fetch(f"SELECT id FROM {probe} WHERE id = 2",
                          timeout_s=5.0)
    assert [r["id"] for r in rows] == [2]
