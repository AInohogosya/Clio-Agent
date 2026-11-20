"""Row-Level Security: every row belongs to a (tenant, space) and stays there.

The isolation pair (`tenant_id`, `space_id`) was added to every project,
memory, event, artifact and audit table by migration 0008, along with a policy
per table: a session reads and writes only rows whose scope matches
`app.space_scope`, the session variable this module sets. The two things worth
stating plainly:

- **Fail closed.** A session that never sets `app.space_scope` sees *no* rows.
  An unset setting reads as NULL in the policy, NULL never equals a scope
  string, and so a mis-scoped query returns nothing rather than everything.
- **Owners are exempt by default.** PostgreSQL lets a table's owner bypass its
  own RLS unless the table says `FORCE ROW LEVEL SECURITY`. The agent's
  application role is deliberately the migration owner in the single-owner
  personal mode (that is what makes `clio up` possible without a second
  role), so the personal deployment is not RLS-protected against its own
  process — it does not need to be; there is one principal. Multi-user
  deployments connect through a non-owner role (`clio_app`), and this module
  is how their sessions are scoped. `force_rls` turns the exemption off for
  deployments that want the property even against their own role.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

# The wildcard scope: policies treat `*` as "this session is the operator and
# sees every space". Only the supervisor and the migration tooling set it.
WILDCARD_SCOPE = "*"


def scope_value(tenant_id: str, space_id: str) -> str:
    """The string a policy compares `app.space_scope` against."""
    return f"{tenant_id}:{space_id}"


@asynccontextmanager
async def space_scope(conn: Any, tenant_id: str, space_id: str):
    """Run a block with the connection scoped to one space.

    `SET` is session-level on purpose: asyncpg pools return the same physical
    connection, and a `SET LOCAL` outside an explicit transaction block is
    silently lost. The previous value is restored on the way out, so a pooled
    connection carries the scope of nobody.
    """
    previous = await conn.fetchval("SELECT current_setting('app.space_scope', true)")
    await conn.execute("SELECT set_config('app.space_scope', $1, false)", scope_value(tenant_id, space_id))
    try:
        yield conn
    finally:
        if previous is None:
            await conn.execute("SELECT set_config('app.space_scope', NULL, false)")
        else:
            await conn.execute("SELECT set_config('app.space_scope', $1, false)", previous)


@asynccontextmanager
async def operator_scope(conn: Any):
    """Run a block seeing every space (supervision, migration, admin tooling)."""
    previous = await conn.fetchval("SELECT current_setting('app.space_scope', true)")
    await conn.execute("SELECT set_config('app.space_scope', $1, false)", WILDCARD_SCOPE)
    try:
        yield conn
    finally:
        if previous is None:
            await conn.execute("SELECT set_config('app.space_scope', NULL, false)")
        else:
            await conn.execute("SELECT set_config('app.space_scope', $1, false)", previous)


async def create_app_role(db: Any, role: str, password: str) -> None:
    """The non-owner role a multi-user deployment's services connect as."""
    await db.execute(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
        f"CREATE ROLE {role} LOGIN PASSWORD '{password}'; "
        "END IF; END $$;",
    )
    await db.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    await db.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO " + role
    )
    await db.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE "
        f"ON TABLES TO {role}"
    )


RLS_TABLES: tuple[str, ...] = (
    "projects", "conversation_project_bindings", "project_milestones",
    "intentions", "intention_edges", "episodes", "facts", "knowhow", "questions",
    "decisions", "attempts", "artifacts", "action_journal", "cycles",
    "events", "messages", "audit_log", "checkpoints", "spaces",
)


async def force_rls(db: Any, table: str) -> None:
    """Make even the table's owner subject to its RLS policies.

    The default exemption is what keeps a single-owner deployment working
    without a second role; a deployment that wants the property enforced
    against its own application role calls this per table.
    """
    await db.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


async def unforce_rls(db: Any, table: str) -> None:
    await db.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")


async def policies_installed(db: Any) -> dict[str, bool]:
    """Which isolated tables have their space-isolation policy. Doctor reads this."""
    rows = await db.fetch(
        "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public' "
        "AND policyname LIKE '%_space_isolation'"
    )
    have = {r["tablename"] for r in rows}
    return {table: table in have for table in RLS_TABLES if table != "spaces"}
