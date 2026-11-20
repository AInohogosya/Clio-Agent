"""Row-Level Security: the (tenant, space) pair is enforced by the database.

These tests force RLS on one table and connect through a non-owner role, which
is how a multi-user deployment's services connect. The suite's own role owns
the tables, so everywhere else the owner exemption keeps the deployment
working — that exemption is why these tests create their own role.
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from clio.db.row_security import (
    RLS_TABLES,
    create_app_role,
    operator_scope,
    policies_installed,
    space_scope,
)

pytestmark = pytest.mark.integration

ROLE = "clio_app_rls_test"


async def _drop_role(db) -> None:
    """Drop the test role, revoking the grants it holds first.

    A role with default privileges cannot be dropped while they stand, so the
    revokes run before the drop and both in the order the dependencies want.
    """
    await db.execute(f"REVOKE ALL ON SCHEMA public FROM {ROLE}")
    await db.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    await db.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {ROLE}")
    await db.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM " + ROLE
    )
    await db.execute("DROP ROLE IF EXISTS " + ROLE)


async def test_migration_installed_the_space_policies(db):
    installed = await policies_installed(db)
    missing = [t for t, ok in installed.items() if not ok]
    assert not missing, f"missing space-isolation policies: {missing}"
    assert "episodes" in installed and "audit_log" in installed


async def test_a_scoped_session_sees_only_its_space(db):
    marker = f"rls-{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO episodes (id, ts, kind, summary, scope) VALUES ($1, now(), 'test', $2, 'private')",
        uuid.uuid4(), marker,
    )
    app_dsn = str(db.dsn).replace("ethos:ethos", f"{ROLE}:{ROLE}")
    await db.execute("ALTER TABLE episodes FORCE ROW LEVEL SECURITY")
    try:
        await create_app_role(db, ROLE, ROLE)
        conn = await asyncpg.connect(app_dsn)

        # Fail closed: no scope set, nothing comes back — not even an error
        # that tells an attacker what the table looks like.
        rows = await conn.fetch("SELECT summary FROM episodes WHERE summary = $1", marker)
        assert rows == [], "an unscoped session must see nothing"

        # Another space: still nothing.
        async with space_scope(conn, "other", "other"):
            rows = await conn.fetch("SELECT summary FROM episodes WHERE summary = $1", marker)
            assert rows == [], "a foreign space must not read this row"

        # The owning space reads its own row.
        async with space_scope(conn, "local", "primary"):
            rows = await conn.fetch("SELECT summary FROM episodes WHERE summary = $1", marker)
            assert [r["summary"] for r in rows] == [marker]

        # A write is rejected when the row is tagged for a space the session
        # is not in: the WITH CHECK half of the policy is what stops a scoped
        # session from planting a row in somebody else's space.
        async with space_scope(conn, "other", "other"):
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await conn.execute(
                    "INSERT INTO episodes (id, ts, kind, summary, scope, space_id, tenant_id) "
                    "VALUES ($1, now(), 'test', 'smuggled', 'private', 'primary', 'local')",
                    uuid.uuid4(),
                )
        await conn.close()
    finally:
        await db.execute("ALTER TABLE episodes NO FORCE ROW LEVEL SECURITY")
        await db.execute("DELETE FROM episodes WHERE summary = $1", marker)
        await _drop_role(db)


async def test_the_operator_scope_sees_every_space(db):
    marker = f"rls-op-{uuid.uuid4().hex[:8]}"
    await db.execute(
        "INSERT INTO episodes (id, ts, kind, summary, scope) VALUES ($1, now(), 'test', $2, 'private')",
        uuid.uuid4(), marker,
    )
    await db.execute("ALTER TABLE episodes FORCE ROW LEVEL SECURITY")
    try:
        await create_app_role(db, ROLE, ROLE)
        app_dsn = str(db.dsn).replace("ethos:ethos", f"{ROLE}:{ROLE}")
        conn = await asyncpg.connect(app_dsn)
        async with operator_scope(conn):
            rows = await conn.fetch("SELECT summary FROM episodes WHERE summary = $1", marker)
            assert [r["summary"] for r in rows] == [marker]
        await conn.close()
    finally:
        await db.execute("ALTER TABLE episodes NO FORCE ROW LEVEL SECURITY")
        await db.execute("DELETE FROM episodes WHERE summary = $1", marker)
        await _drop_role(db)


async def test_the_isolation_pair_is_on_every_isolated_table(db):
    for table in RLS_TABLES:
        cols = await db.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name IN ('space_id', 'tenant_id')",
            table,
        )
        assert {c["column_name"] for c in cols} == {"space_id", "tenant_id"}, table
