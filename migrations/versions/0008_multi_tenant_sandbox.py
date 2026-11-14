"""Clio Agent 3.1: multi-tenant spaces, RLS, project sandboxes, steering, connectors

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-14

Adds the schema behind the contained-execution upgrade:

- `tenants` and `spaces`: the (tenant_id, space_id) isolation pair every
  project, memory, event, artifact and audit row carries from now on.
- Row-Level Security policies on those tables, so a compromised process or a
  mis-scoped query reads only the rows its space can see.
- `projects.space_id` / `projects.sandbox_id`: a project's memory is a space and
  its execution is a sandbox; both are named on the container.
- `sandbox_registry`: every sandbox a SandboxProvider provisioned, with the
  backend that holds it and the egress policy that governs it.
- `steering_messages`: mid-turn user guidance, claimed by the solver loop
  between steps and never dropped on the floor.
- `connector_accounts` + `imported_tools`: OAuth connector broker accounts and
  the OpenAPI/GraphQL tools imported from a schema.
- `space_deks`: the wrapped per-space data encryption keys envelope encryption
  uses; deleting the row is the cryptographic shredding of the space.
- `cel_policies`: GuardianGate pre-execution policy rules, versioned, with
  deny-before-allow evaluation.

All changes are additive and reversible: downgrade() drops exactly what
upgrade() added, policies included.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"


# Tables that carry the (tenant_id, space_id) isolation pair and get RLS.
# `events` and `audit_log` are append-heavy; they still carry the pair, because
# an event or an audit row is exactly the thing a foreign tenant must never
# read. Each entry is (table, primary-key column, has-created-at).
_RLS_TABLES: list[tuple[str, str, bool]] = [
    ("projects", "project_id", True),
    ("conversation_project_bindings", "conversation_key", False),
    ("project_milestones", "id", True),
    ("intentions", "id", True),
    ("intention_edges", "id", False),
    ("episodes", "id", True),
    ("facts", "id", True),
    ("knowhow", "id", True),
    ("questions", "id", True),
    ("decisions", "id", True),
    ("attempts", "id", True),
    ("artifacts", "id", True),
    ("action_journal", "id", True),
    ("cycles", "id", True),
    ("events", "id", True),
    ("messages", "id", True),
    ("audit_log", "id", True),
    ("checkpoints", "id", True),
]

# A default tenant/space for the rows that pre-date this migration: the
# single-owner personal deployment is tenant "local" and its one space is
# "primary". The names are stable identifiers, not settings.
DEFAULT_TENANT = "local"
DEFAULT_SPACE = "primary"


def upgrade() -> None:
    # ---- tenants and spaces: the isolation pair --------------------------------
    op.create_table(
        "tenants",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("auth_provider", sa.Text(), nullable=False, server_default="single_owner"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_table(
        "spaces",
        sa.Column("space_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), sa.ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="personal"),
        # RBAC: members map a principal to a role in this space. Stored inline
        # rather than in a join table because a space's membership is small and
        # always read whole; the JSONB is {principal_id: role}.
        sa.Column("members_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "tenant_id"),
        sa.CheckConstraint("kind IN ('personal', 'team', 'sandboxed')", name="ck_spaces_kind"),
    )

    # ---- the pair on every isolated table, with RLS ---------------------------
    for table, _pk, _has_created in _RLS_TABLES:
        op.add_column(table, sa.Column("space_id", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("tenant_id", sa.Text(), nullable=True))
        # Backfill: existing rows belong to the deployment's one space.
        op.execute(f"UPDATE {table} SET space_id = '{DEFAULT_SPACE}', tenant_id = '{DEFAULT_TENANT}' "
                   f"WHERE space_id IS NULL AND tenant_id IS NULL")
        op.alter_column(table, "space_id", nullable=False, existing_nullable=True,
                        existing_type=sa.Text(), server_default=DEFAULT_SPACE)
        op.alter_column(table, "tenant_id", nullable=False, existing_nullable=True,
                        existing_type=sa.Text(), server_default=DEFAULT_TENANT)
        op.create_index(f"ix_{table}_space_tenant", table, ["space_id", "tenant_id"])

    op.execute("INSERT INTO tenants (tenant_id, name) VALUES ('local', 'personal deployment') "
               "ON CONFLICT (tenant_id) DO NOTHING")
    op.execute("INSERT INTO spaces (space_id, tenant_id, name, kind) VALUES ('primary', 'local', 'primary', 'personal') "
               "ON CONFLICT (space_id, tenant_id) DO NOTHING")
    op.create_foreign_key(
        "fk_projects_space", "projects", "spaces",
        ["space_id", "tenant_id"], ["space_id", "tenant_id"], ondelete="RESTRICT",
    )

    # ---- row-level security ----------------------------------------------------
    # The application role is the one the agent connects as; the policies are
    # written so that a session which has NOT set `app.current_space` sees
    # nothing at all (fail closed), and a session that sets it sees only that
    # space's rows. The migration owner (usually the bootstrap superuser) is
    # exempted so migrations keep working.
    op.execute("ALTER TABLE spaces ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY spaces_admin ON spaces USING (true)")
    for table, pk, _has_created in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_space_isolation ON {table} "
            f"USING (tenant_id || ':' || space_id = current_setting('app.space_scope', true)) "
            f"WITH CHECK (tenant_id || ':' || space_id = current_setting('app.space_scope', true))"
        )
        op.execute(f"CREATE POLICY {table}_owner_bypass ON {table} USING (current_setting('app.space_scope', true) = '*')")

    # ---- projects: space + sandbox ---------------------------------------------
    op.add_column("projects", sa.Column("sandbox_id", sa.Text(), nullable=True))
    op.create_index("ix_projects_sandbox", "projects", ["sandbox_id"])

    # ---- sandbox registry -------------------------------------------------------
    op.create_table(
        "sandbox_registry",
        sa.Column("sandbox_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("space_id", sa.Text(), nullable=False, server_default=DEFAULT_SPACE),
        sa.Column("backend", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("egress_policy_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("display_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("lease_fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("sandbox_id"),
        sa.CheckConstraint(
            "backend IN ('filesystem', 'bubblewrap', 'seatbelt', 'rootless_docker', 'microvm', 'remote')",
            name="ck_sandbox_backend",
        ),
        sa.CheckConstraint(
            "status IN ('provisioning', 'running', 'frozen', 'destroyed')",
            name="ck_sandbox_status",
        ),
    )
    op.create_index("ix_sandbox_registry_status", "sandbox_registry", ["status"])

    # ---- steering messages -------------------------------------------------------
    op.create_table(
        "steering_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("conversation_key", sa.Text(), nullable=False),
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False, server_default="normal"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("claimed_by_step", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("priority IN ('normal', 'high')", name="ck_steering_priority"),
        sa.CheckConstraint("status IN ('pending', 'claimed', 'applied', 'expired')", name="ck_steering_status"),
    )
    op.create_index("ix_steering_pending", "steering_messages", ["status", "priority", "created_at"])

    # ---- connector accounts (OAuth broker) and imported tools -------------------
    op.create_table(
        "connector_accounts",
        sa.Column("connector_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("space_id", sa.Text(), nullable=False, server_default=DEFAULT_SPACE),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        # The refresh/access tokens are stored encrypted by the envelope layer;
        # what lives here is the sealed payload and the key that opens it.
        sa.Column("sealed_tokens", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("project_scope_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="connected"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("connector_id", "tenant_id", "space_id"),
        sa.CheckConstraint("status IN ('connected', 'expired', 'revoked')", name="ck_connector_status"),
    )
    op.create_table(
        "imported_tools",
        sa.Column("tool_key", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("space_id", sa.Text(), nullable=False, server_default=DEFAULT_SPACE),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("connector_id", sa.Text(), nullable=True),
        sa.Column("spec_json", postgresql.JSONB(), nullable=False),
        sa.Column("cel_policy_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("tool_key", "tenant_id", "space_id"),
        sa.CheckConstraint("source IN ('openapi', 'graphql', 'mcp', 'toolshed')", name="ck_imported_tool_source"),
    )

    # ---- wrapped data encryption keys ------------------------------------------
    op.create_table(
        "space_deks",
        sa.Column("space_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False, server_default=DEFAULT_TENANT),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("algorithm", sa.Text(), nullable=False, server_default="AES-256-GCM"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("space_id", "tenant_id", "key_version"),
    )

    # ---- CEL policy rules ---------------------------------------------------------
    op.create_table(
        "cel_policies",
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("effect", sa.Text(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("policy_id"),
        sa.CheckConstraint("effect IN ('allow', 'deny')", name="ck_cel_policy_effect"),
    )
    op.create_index("ix_cel_policies_priority", "cel_policies", ["priority"])

    # ---- events: out-of-band lane marker -----------------------------------------
    op.add_column("events", sa.Column("lane", sa.Text(), nullable=True))
    op.create_index("ix_events_lane_unconsumed", "events", ["lane"],
                    postgresql_where=sa.text("lane IS NOT NULL"))

    # ---- sealed payloads (envelope encryption at rest) ---------------------------
    # When an envelope is configured, the sensitive payload of each row moves
    # into `sealed_payload` and the plaintext column keeps a `{"sealed": true}`
    # stub. The columns are nullable so a deployment that opted out simply
    # never touches them.
    for table in ("episodes", "facts", "action_journal", "checkpoints"):
        op.add_column(table, sa.Column("sealed_payload", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    for table in ("episodes", "facts", "action_journal", "checkpoints"):
        op.drop_column(table, "sealed_payload")
    op.drop_index("ix_events_lane_unconsumed", table_name="events")
    op.drop_column("events", "lane")

    op.drop_index("ix_cel_policies_priority", table_name="cel_policies")
    op.drop_table("cel_policies")
    op.drop_table("space_deks")
    op.drop_table("imported_tools")
    op.drop_table("connector_accounts")
    op.drop_index("ix_steering_pending", table_name="steering_messages")
    op.drop_table("steering_messages")
    op.drop_index("ix_sandbox_registry_status", table_name="sandbox_registry")
    op.drop_table("sandbox_registry")
    op.drop_index("ix_projects_sandbox", table_name="projects")
    op.drop_column("projects", "sandbox_id")
    op.drop_constraint("fk_projects_space", "projects", type_="foreign")

    for table, _pk, _has_created in reversed(_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_owner_bypass ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_space_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_index(f"ix_{table}_space_tenant", table_name=table)
        op.drop_column(table, "tenant_id")
        op.drop_column(table, "space_id")
    op.execute("DROP POLICY IF EXISTS spaces_admin ON spaces")
    op.execute("ALTER TABLE spaces DISABLE ROW LEVEL SECURITY")
    op.drop_table("spaces")
    op.drop_table("tenants")
