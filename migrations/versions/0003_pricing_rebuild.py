"""per-model token prices, and cache-write tokens on the ledger

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17

Two schema changes for the pricing rebuild, both additive.

`cache_write_tokens` joins the four counters on `model_calls`. Storing a
prompt prefix is a separate billable event from reading one back -- Anthropic
charges 1.25x the input rate for a 5-minute TTL and 2x for one hour -- and
`cache_creation_input_tokens` was being parsed off the wire and then thrown
away, so those tokens were priced at nothing.

`price_source` records where each row's `cost_usd` came from: `provider` when
the vendor reported the figure, `config` or `live` when a rate card priced it,
`unknown` when a high-water mark stood in. Without it a wrong total is
indistinguishable from a right one, and the whole point of reworking the
calculator was that a wrong total could be traced.

The indexes on `model` and `budget_bucket` are what the budget pane reads --
"today by model", "today by bucket" -- and both were full scans over every
call ever made.

`cost_usd` keeps its `NUMERIC(10,6)`. Six decimals of a dollar is a
microdollar, which is finer than any vendor invoice resolves, and widening it
is not what is needed: the defect was the value, not the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_calls",
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "model_calls",
        sa.Column("price_source", sa.Text(), nullable=True),
    )
    op.execute("UPDATE model_calls SET cache_write_tokens = 0 WHERE cache_write_tokens IS NULL")
    op.execute("UPDATE model_calls SET price_source = 'legacy' WHERE price_source IS NULL")
    op.create_index("ix_model_calls_model", "model_calls", ["model", "ts"])
    op.create_index(
        "ix_model_calls_bucket", "model_calls", ["budget_bucket", "ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_calls_bucket", table_name="model_calls")
    op.drop_index("ix_model_calls_model", table_name="model_calls")
    op.drop_column("model_calls", "price_source")
    op.drop_column("model_calls", "cache_write_tokens")
