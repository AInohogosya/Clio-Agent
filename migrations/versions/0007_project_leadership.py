"""Clio Agent 3.3: project containers, conversation bindings, milestones, attribution

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-13

Adds the schema behind project leadership (`clio`): a `projects` container with
its own lifecycle, per-conversation project bindings, shipped milestones, and
project attribution columns on `intentions`, `action_journal`, `artifacts` and
`cycles`. All changes are additive and reversible: downgrade() drops exactly
what upgrade() added.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"


def upgrade() -> None:
    # ---- projects: the container a lead architect works inside (Phase 1.1) ----
    op.create_table(
        "projects",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("owner_principal_id", sa.Text(), nullable=False),
        sa.Column("root_intention_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="SET NULL"), nullable=True),
        # `workspace_leases.id` is Text (`lease-<hex12>`, migration 0006), so
        # the foreign key is Text too: the referential guarantee is what
        # matters — a project's lease either exists or is NULL — and it only
        # holds if the two columns agree on type.
        sa.Column("workspace_lease_id", sa.Text(), sa.ForeignKey("workspace_leases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lifecycle_mode", sa.Text(), nullable=False, server_default="finite"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("focus_seconds_accumulated", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("checkpoint_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("evolution_config_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("next_evolution_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("project_id"),
        sa.CheckConstraint(
            "lifecycle_mode IN ('finite', 'evergreen')", name="ck_projects_lifecycle_mode"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused_for_human', 'preempted', 'evolving_cooldown', 'done')",
            name="ck_projects_status",
        ),
    )
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_owner", "projects", ["owner_principal_id"])
    op.create_index("ix_projects_root_intention", "projects", ["root_intention_id"])

    # ---- conversation_project_bindings: one active project per conversation ----
    op.create_table(
        "conversation_project_bindings",
        sa.Column("conversation_key", sa.Text(), nullable=False),
        sa.Column("active_project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("conversation_key"),
    )
    op.create_index(
        "ix_conversation_project_bindings_project",
        "conversation_project_bindings", ["active_project_id"],
    )

    # ---- project_milestones: shipped, verified versions of a project ----
    op.create_table(
        "project_milestones",
        sa.Column("milestone_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_tag", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("git_commit_sha", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("milestone_id"),
    )
    op.create_index("ix_project_milestones_project", "project_milestones", ["project_id"])

    # ---- intentions: project attribution + executor routing (Phase 1.1.4) ----
    op.add_column("intentions", sa.Column("project_id", postgresql.UUID(as_uuid=True),
                                          sa.ForeignKey("projects.project_id", ondelete="SET NULL"), nullable=True))
    op.add_column("intentions", sa.Column("root_intention_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("intentions", sa.Column("executor_kind", sa.Text(), nullable=False, server_default="self"))
    op.add_column("intentions", sa.Column("instruction_spec", postgresql.JSONB(), nullable=True))
    op.execute("ALTER TABLE intentions DROP CONSTRAINT IF EXISTS ck_intentions_executor_kind")
    op.execute(
        "ALTER TABLE intentions ADD CONSTRAINT ck_intentions_executor_kind "
        "CHECK (executor_kind IN ('self', 'thread', 'subagent', 'resident', 'human'))"
    )
    op.create_index("ix_intentions_project", "intentions", ["project_id"])
    op.create_index("ix_intentions_root_intention", "intentions", ["root_intention_id"])
    op.create_index("ix_intentions_executor_kind", "intentions", ["executor_kind"])

    # ---- attribution on the journals and renders (Phase 1.1.4) ----
    for table in ("action_journal", "artifacts", "cycles"):
        op.add_column(table, sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.add_column(table, sa.Column("root_intention_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_index(f"ix_{table}_project", table, ["project_id"])
        op.create_index(f"ix_{table}_root_intention", table, ["root_intention_id"])


def downgrade() -> None:
    for table in ("cycles", "artifacts", "action_journal"):
        op.drop_index(f"ix_{table}_root_intention", table_name=table)
        op.drop_index(f"ix_{table}_project", table_name=table)
        op.drop_column(table, "root_intention_id")
        op.drop_column(table, "project_id")
    op.drop_index("ix_intentions_executor_kind", table_name="intentions")
    op.drop_index("ix_intentions_root_intention", table_name="intentions")
    op.drop_index("ix_intentions_project", table_name="intentions")
    op.drop_column("intentions", "instruction_spec")
    op.drop_column("intentions", "executor_kind")
    op.drop_column("intentions", "root_intention_id")
    op.drop_column("intentions", "project_id")
    op.drop_index("ix_project_milestones_project", table_name="project_milestones")
    op.drop_table("project_milestones")
    op.drop_index(
        "ix_conversation_project_bindings_project", table_name="conversation_project_bindings"
    )
    op.drop_table("conversation_project_bindings")
    op.drop_index("ix_projects_root_intention", table_name="projects")
    op.drop_index("ix_projects_owner", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_table("projects")
