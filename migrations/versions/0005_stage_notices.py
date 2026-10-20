"""stage notices: work somebody asked about reached a stage they asked to hear about

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18

Two complaints shared one cause, and this table is the record that closes it.

The agent could not answer "did you finish it?" and could not say so itself. Both
came from the same gap: nothing durable recorded that a piece of commissioned
work had reached a stage the person who commissioned it was waiting for. The
evidence a reply could draw on was the `action_journal` inside a fifteen-minute
window and the list of *open* intentions -- so a closed intention appeared
nowhere, and the only way a person heard about a finished task was if some solver
step happened to compose a `comm.send` about it. That is a model decision rather
than a fact of the loop, which is why it happened sometimes and not always.

A stage notice is that missing fact, written once by the solver loop at the
moment the stage is reached and read afterwards by both halves of the fix: the
deliberation, so a reply can be written from evidence rather than from
assumption, and the life loop, so there is a pass that owes somebody the word.

Three columns carry the shape of it:

* `(intention_id, stage)` is unique. A stage arrives once, so a notice is not
  duplicated by a retry of the cycle that produced it, and the same stage is not
  announced twice however many times the loop re-enters the state that caused it.
* `person_id`, `channel` and `conversation_key` travel with the row. They are
  resolved from the messages table when the notice is written rather than looked
  up again later, so the notice stays correct across a restart and does not
  depend on whichever conversation that person happens to have used since.
* `delivered_at` is what turns the record into evidence. Until it is set the
  notice is a debt, and the loop keeps trying to pay it; once it is set it is
  the durable proof that the work finished, which is what a reply months later
  draws on.

`attempts` and `next_attempt_at` bound the paying. A send can fail for reasons
that never resolve -- the channel's credentials are wrong, the adapter refuses
the address -- and a debt retried at the tick rate is an outage wearing a
notification's clothes. So the wait grows, and `attempts` caps it: past the cap
the notice is settled with the failure recorded, which stops the loop and leaves
the debt visible in the row rather than silently retried forever.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"


def upgrade() -> None:
    op.create_table(
        "stage_notices",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("intention_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intentions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("person_id", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("conversation_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery", postgresql.JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("intention_id", "stage", name="uq_stage_notices_intention_stage"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The pending debts, oldest first: that is the order they are owed in, and a
    # partial index keeps the table's one hot path off a growing history of
    # delivered notices.
    op.create_index(
        "ix_stage_notices_pending",
        "stage_notices",
        ["next_attempt_at", "created_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_stage_notices_pending", table_name="stage_notices")
    op.drop_table("stage_notices")
