"""Clio Agent 3.2: RIG edges, budgets, principals, memory scopes, workspace leases

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08

Adds the schema behind Clio Agent 3.2 (`ethos`): Recursive Intention Graphs,
EV-proportional budgets, federated resident principals, scoped institutional
memory, and transactional workspace leases. All changes are additive and
reversible: downgrade() drops exactly what upgrade() added.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"


def upgrade() -> None:
    # ---- intentions: budget + lease + assignment (Step 1.2) ----
    op.add_column("intentions", sa.Column("budget_allocated_usd", sa.Numeric(12, 4), nullable=True))
    op.add_column("intentions", sa.Column("budget_spent_usd", sa.Numeric(12, 4), nullable=True, server_default="0"))
    op.add_column("intentions", sa.Column("expected_value", sa.Float(), nullable=True))
    op.add_column("intentions", sa.Column("lease_id", sa.Text(), nullable=True))
    op.add_column("intentions", sa.Column("assigned_principal_id", sa.Text(), nullable=True))
    op.create_index("ix_intentions_lease", "intentions", ["lease_id"])
    op.create_index("ix_intentions_assignee", "intentions", ["assigned_principal_id"])

    # ---- intention_edges: the RIG (Step 1.1) ----
    op.create_table(
        "intention_edges",
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("child_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edge_type", sa.Text(), nullable=False),
        sa.Column("gate_policy", postgresql.JSONB(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("parent_id", "child_id", "edge_type"),
        sa.UniqueConstraint("parent_id", "child_id", "edge_type", name="uq_intention_edges"),
        sa.CheckConstraint("edge_type IN ('depends_on', 'verifies', 'synthesizes', 'informs')", name="ck_intention_edges_type"),
    )
    op.create_index("ix_intention_edges_parent", "intention_edges", ["parent_id"])
    op.create_index("ix_intention_edges_child", "intention_edges", ["child_id"])

    # ---- people: federated principals (Step 1.3) ----
    op.add_column("people", sa.Column("kind", sa.Text(), nullable=False, server_default="human"))
    op.add_column("people", sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("people", sa.Column("authority_scope", postgresql.JSONB(), nullable=False, server_default="{}"))
    op.add_column("people", sa.Column("endpoint_uri", sa.Text(), nullable=True))
    op.execute("ALTER TABLE people DROP CONSTRAINT IF EXISTS ck_people_kind")
    op.execute(
        "ALTER TABLE people ADD CONSTRAINT ck_people_kind "
        "CHECK (kind IN ('human', 'resident', 'service'))"
    )
    op.create_index("ix_people_kind", "people", ["kind"])

    # ---- memory scoping (Step 1.4) ----
    for table in ("episodes", "facts", "knowhow", "questions"):
        op.add_column(table, sa.Column("scope", sa.Text(), nullable=False, server_default="private"))
        op.add_column(table, sa.Column("namespace_id", sa.Text(), nullable=True))
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_scope"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_scope "
            f"CHECK (scope IN ('private', 'project', 'org'))"
        )
        op.create_index(f"ix_{table}_scope", table, ["scope"])
        op.create_index(f"ix_{table}_namespace", table, ["namespace_id"])

    # ---- workspace_leases (Step 1.5) ----
    op.create_table(
        "workspace_leases",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False, server_default="git_worktree"),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.Column("base_ref", sa.Text(), nullable=True),
        sa.Column("target_path", sa.Text(), nullable=True),
        sa.Column("budget_limit_usd", sa.Float(), nullable=True),
        sa.Column("allowed_paths", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind IN ('git_worktree', 'snapshot')", name="ck_workspace_leases_kind"),
        sa.CheckConstraint("status IN ('active', 'committed', 'rolled_back')", name="ck_workspace_leases_status"),
    )
    op.create_index("ix_workspace_leases_intention", "workspace_leases", ["intention_id"])
    op.create_index("ix_workspace_leases_status", "workspace_leases", ["status"])


def downgrade() -> None:
    op.drop_index("ix_workspace_leases_status", table_name="workspace_leases")
    op.drop_index("ix_workspace_leases_intention", table_name="workspace_leases")
    op.drop_table("workspace_leases")
    for table in ("questions", "knowhow", "facts", "episodes"):
        op.drop_index(f"ix_{table}_namespace", table_name=table)
        op.drop_index(f"ix_{table}_scope", table_name=table)
        op.drop_column(table, "namespace_id")
        op.drop_column(table, "scope")
    op.drop_index("ix_people_kind", table_name="people")
    op.drop_column("people", "endpoint_uri")
    op.drop_column("people", "authority_scope")
    op.drop_column("people", "capabilities")
    op.drop_column("people", "kind")
    op.drop_index("ix_intention_edges_child", table_name="intention_edges")
    op.drop_index("ix_intention_edges_parent", table_name="intention_edges")
    op.drop_table("intention_edges")
    op.drop_index("ix_intentions_assignee", table_name="intentions")
    op.drop_index("ix_intentions_lease", table_name="intentions")
    op.drop_column("intentions", "assigned_principal_id")
    op.drop_column("intentions", "lease_id")
    op.drop_column("intentions", "expected_value")
    op.drop_column("intentions", "budget_spent_usd")
    op.drop_column("intentions", "budget_allocated_usd")
