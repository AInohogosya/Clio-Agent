"""initial Clio Agent 3 schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01

All 20 tables from the Clio Agent 3 implementation specification (Section 5.1)
with HNSW and GIN indexes.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("salience", sa.Float(), nullable=True),
        sa.Column("consumed_by", sa.ARRAY(sa.Text()), server_default="{}", nullable=True),
        sa.Column("trust", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_kind_ts", "events", ["kind", "ts"])
    op.create_index("ix_events_salience", "events", ["salience"])
    op.create_index("ix_events_payload", "events", ["payload"], postgresql_using="gin",
                    postgresql_ops={"payload": "jsonb_path_ops"})

    op.create_table(
        "episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("body", postgresql.JSONB(), nullable=True),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("importance", sa.Float(), nullable=True),
        sa.Column("strength", sa.Float(), nullable=True),
        sa.Column("half_life_h", sa.Float(), nullable=True),
        sa.Column("last_access", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), server_default="0", nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE episodes ADD COLUMN embedding vector(1024)")
    op.create_index("ix_episodes_ts", "episodes", ["ts"])
    op.create_index("ix_episodes_intention", "episodes", ["intention_id"])
    op.create_index("ix_episodes_body", "episodes", ["body"], postgresql_using="gin",
                    postgresql_ops={"body": "jsonb_path_ops"})
    op.execute(
        "CREATE INDEX ix_episodes_summary_trgm ON episodes USING gin (summary gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_episodes_embedding_hnsw ON episodes USING hnsw "
        "(embedding vector_cosine_ops)"
    )

    op.create_table(
        "facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=True),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("strength", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE facts ADD COLUMN embedding vector(1024)")
    op.create_index("ix_facts_subject", "facts", ["subject"])
    op.execute(
        "CREATE INDEX ix_facts_statement_trgm ON facts USING gin (statement gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_facts_embedding_hnsw ON facts USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "knowhow",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("situation", sa.Text(), nullable=True),
        sa.Column("advice", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=True),
        sa.Column("successes", sa.Integer(), server_default="0", nullable=True),
        sa.Column("retired", sa.Boolean(), server_default=sa.text("false"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE knowhow ADD COLUMN embedding vector(1024)")
    op.create_index("ix_knowhow_situation_trgm", "knowhow", ["situation"], postgresql_using="gin",
                    postgresql_ops={"situation": "gin_trgm_ops"})
    op.execute(
        "CREATE INDEX ix_knowhow_embedding_hnsw ON knowhow USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("origin_episode", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("priority", sa.Float(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE questions ADD COLUMN embedding vector(1024)")
    op.create_index("ix_questions_text_trgm", "questions", ["text"], postgresql_using="gin",
                    postgresql_ops={"text": "gin_trgm_ops"})
    op.execute(
        "CREATE INDEX ix_questions_embedding_hnsw ON questions USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "intentions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("desired_end_state", sa.Text(), nullable=True),
        sa.Column("success_criteria", postgresql.JSONB(), nullable=True),
        sa.Column("constraints", postgresql.JSONB(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("commissioned_by", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="proposed", nullable=True),
        sa.Column("priority", sa.Float(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("budget_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("spent_usd", sa.Numeric(10, 2), server_default="0", nullable=True),
        sa.Column("resume_state", postgresql.JSONB(), nullable=True),
        sa.Column("hypothesis", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_intentions_parent", "intentions", ["parent_id"])
    op.create_index("ix_intentions_status", "intentions", ["status"])
    op.create_index("ix_intentions_deadline", "intentions", ["deadline"])

    op.create_table(
        "decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reversible", sa.Boolean(), server_default=sa.text("true"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decisions_intention", "decisions", ["intention_id"])

    op.create_table(
        "attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approach", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("evaluation", postgresql.JSONB(), nullable=True),
        sa.Column("lessons", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attempts_intention", "attempts", ["intention_id"])

    op.create_table(
        "env_entities",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("attrs", postgresql.JSONB(), nullable=True),
        sa.Column("owner", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("health", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_env_entities_kind", "env_entities", ["kind"])

    op.create_table(
        "self_model",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=True),
        sa.Column("changed_by", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", "version"),
    )

    op.create_table(
        "people",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("relation", sa.Text(), nullable=True),
        sa.Column("channels", postgresql.JSONB(), nullable=True),
        sa.Column("preferences", postgresql.JSONB(), nullable=True),
        sa.Column("trust", sa.Float(), nullable=True),
        sa.Column("last_contact", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "checkpoints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_checkpoints_ts", "checkpoints", ["ts"])

    op.create_table(
        "cycles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("focus", postgresql.JSONB(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("decision", postgresql.JSONB(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("workspace_digest", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cycles_ts", "cycles", ["ts"])

    op.create_table(
        "model_calls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("budget_bucket", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_calls_ts", "model_calls", ["ts"])
    op.create_index("ix_model_calls_tier", "model_calls", ["tier", "ts"])

    op.create_table(
        "action_journal",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("ts_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ts_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool", sa.Text(), nullable=True),
        sa.Column("args_redacted", postgresql.JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("side_effects", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("result_digest", postgresql.JSONB(), nullable=True),
        sa.Column("undo_ref", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_action_journal_ts", "action_journal", ["ts_start"])
    op.create_index("ix_action_journal_status", "action_journal", ["status"])

    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=True),
        sa.Column("origin", sa.Text(), nullable=True),
        sa.Column("commissioned_by", sa.Text(), nullable=True),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("protection", sa.Text(), server_default="internal", nullable=True),
        sa.Column("fingerprint", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_uri", "artifacts", ["uri"])
    op.create_index("ix_artifacts_protection", "artifacts", ["protection"])

    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("conversation_key", sa.Text(), nullable=True),
        sa.Column("person_id", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("attachments", postgresql.JSONB(), nullable=True),
        sa.Column("in_reply_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("social_decision", postgresql.JSONB(), nullable=True),
        sa.Column("delivery", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation", "messages", ["conversation_key", "ts"])
    op.create_index("ix_messages_direction", "messages", ["direction", "ts"])

    op.create_table(
        "audit_log",
        sa.Column("seq", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("prev_hash", sa.LargeBinary(), nullable=False),
        sa.Column("hash", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])

    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("hypothesis", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("observations", postgresql.JSONB(), nullable=True),
        sa.Column("conclusion", sa.Text(), nullable=True),
        sa.Column("lessons", sa.ARRAY(sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiments_intention", "experiments", ["intention_id"])

    op.create_table(
        "value_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metric", sa.Text(), nullable=True),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_value_ledger_metric", "value_ledger", ["metric", "ts"])
    op.create_index("ix_value_ledger_intention", "value_ledger", ["intention_id"])


def downgrade() -> None:
    op.drop_table("value_ledger")
    op.drop_table("experiments")
    op.drop_table("audit_log")
    op.drop_table("messages")
    op.drop_table("artifacts")
    op.drop_table("action_journal")
    op.drop_table("model_calls")
    op.drop_table("cycles")
    op.drop_table("checkpoints")
    op.drop_table("people")
    op.drop_table("self_model")
    op.drop_table("env_entities")
    op.drop_table("attempts")
    op.drop_table("decisions")
    op.drop_table("intentions")
    op.drop_table("questions")
    op.drop_table("knowhow")
    op.drop_table("facts")
    op.drop_table("episodes")
    op.drop_table("events")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS vector")
