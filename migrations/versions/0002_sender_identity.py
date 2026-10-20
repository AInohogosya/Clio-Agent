"""sender identity on messages

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-08

`messages.person_id` is an address in the channel's own namespace —
`tg:819012345678`, `wa:819012345678`, `slack:C1` — and has to stay exactly that,
because every outbound path sends to it and each adapter refuses anything
carrying another channel's prefix.

That left nowhere for a sender's *name* to go. Every one of these channels
carries one and none of them were keeping it: Telegram sends `effective_user`
with a `username` and a first and last name on every update, WhatsApp puts
`contacts[].profile.name` in the very webhook body the adapter was already
reading, Discord puts `author.username` on every dispatched message. A person
whose chat id is `tg:819012345678` was addressed to the agent as a string of
digits, which is why the transcript read the way it did and why the bot needed
an allowlist of numbers to recognise its own owner.

Two nullable columns rather than one, because the two are not always both
available and are not the same thing: WhatsApp's Cloud API gives a display name
and no handle at all, Telegram gives a handle and no guarantee of a real name,
and Slack gives neither unless the app holds `users:read`. Both are nullable,
so a channel that has neither keeps the row it always wrote.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, with no default and no backfill. Existing rows are messages the
    # agent has already answered, and nothing can be recovered about who sent
    # them — the provider payloads were never stored — so any value written here
    # would be a guess. A null is an honest "this row was recorded before the
    # agent knew", and every reader of these two columns already treats null as
    # "fall back to the address".
    op.add_column("messages", sa.Column("sender_name", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("sender_username", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "sender_username")
    op.drop_column("messages", "sender_name")