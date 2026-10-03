from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# The longest sender name worth keeping.
#
# A display name is chosen by the sender and can be megabytes, and these two are
# interpolated into the deliberation prompt and shown on a timeline, so a cap is
# the only thing standing between "Alice" and a sender who has appended a
# thousand lines of instruction. Generous enough that every real name fits.
MAX_SENDER_NAME_LENGTH = 120


def _short(value: str | None) -> str | None:
    """A sender-supplied name, trimmed, truncated, and `None` when it was blank.

    Blank is the case that matters: every one of these channels can send an
    empty display name, and `" "` reaching the prompt as a name is worse than no
    name at all because it renders as a sender who exists and has no name.
    Control characters go too, since a name may hold newlines and this ends up
    on one line of a prompt.
    """
    if not value:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable())
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None
    if len(cleaned) > MAX_SENDER_NAME_LENGTH:
        cleaned = cleaned[:MAX_SENDER_NAME_LENGTH]
    return cleaned


class Direction(str, Enum):
    inbound = "inbound"
    outbound = "outbound"


class Urgency(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"
    critical = "critical"

    def weight(self) -> float:
        return {"low": 0.1, "normal": 0.3, "high": 0.7, "critical": 0.95}[self.value]


class Message(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    direction: str = Direction.inbound.value
    channel: str = "web"
    conversation_key: str = ""
    person_id: str | None = None
    text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    in_reply_to: UUID | None = None
    intention_id: UUID | None = None
    social_decision: dict[str, Any] | None = None
    delivery: dict[str, Any] | None = None
    # Who the words came from, as the sender's own platform spells their name.
    #
    # Separate from `person_id` on purpose. `person_id` is an address in the
    # channel's namespace — `tg:819012345678`, `wa:819012345678`, `slack:C1` — and
    # it has to stay exactly that, because the reply path sends to it and each
    # adapter refuses anything carrying a different prefix. It is also the only
    # half that every channel can supply: a bot's token can be asked for a user's
    # id but a stranger's *name* is not something any of these APIs promise to
    # hand over, and WhatsApp's Cloud API gives a display name with no handle at
    # all. So the name lives beside the address rather than replacing it, and
    # either half may be missing.
    sender_name: str | None = None
    sender_username: str | None = None

    def sender_who(self) -> str:
        """Whoever sent this, written for a person or a model to read."""
        return sender_label(self.sender_name, self.sender_username, self.person_id)


def sender_label(
    sender_name: str | None,
    sender_username: str | None,
    person_id: str | None,
) -> str:
    """Whoever sent a message, from its three addressable halves.

    Every channel here knows the sender's *address* and none of them are obliged
    to know their name, so this is a fallback chain rather than a field: a
    Telegram message reads as `Ada Lovelace (@ada, tg:819012345678)`, a WhatsApp
    one as `Alice Smith (wa:819012345678)`, a Slack one as `U03ABCDEF (slack:C1)`
    when the app has no `users:read` scope to learn better.

    A function taking three arguments rather than a `Message` because most of the
    places that need this answer are reading rows — the conversation history, the
    timeline query — and rebuilding a whole `Message` to ask a question about one
    field of it is how a second, subtly different answer to "who was this" ends
    up next to the first.

    The address is never dropped, even when a name is known, because it is what a
    reply has to be addressed to and it is the only half that survives a channel
    which hands over no name at all. Dropping it would make the transcript read
    well and the transcript unresolvable.
    """
    name = _short(sender_name)
    handle = _short(sender_username)
    handle = handle.lstrip("@") if handle else None
    # A handle is stored without its `@` — Telegram's own field has none — so it is
    # put back here rather than in five adapters that would each have to remember.
    # It is added back *beside* a name only when the two are different things: a
    # name that merely is the handle, which is what Discord's `username` is next to
    # a `global_name` of the same string, would otherwise be printed twice, and a
    # message that has nothing but a handle would read `ada (@ada, tg:1)`.
    who = name or handle or ""
    detail = f"@{handle}" if name and handle and handle.lower() != name.lower() else ""
    # The address last, in the same bracket as the handle, so the whole of what is
    # known about the sender reads as one thing: `Ada Lovelace (@ada, tg:819…)`. A
    # second bracket round the address alone would make it look like a separate
    # field of its own.
    if person_id:
        detail = f"{detail}, {person_id}" if detail else person_id
    if who and detail:
        return f"{who} ({detail})"
    return who or detail or "unknown"


class SocialAction(str, Enum):
    reply_now = "reply_now"
    reply_later = "reply_later"
    acknowledge = "acknowledge"
    act_silently = "act_silently"
    ask_clarification = "ask_clarification"
    ignore = "ignore"


class IgnoreReason(str, Enum):
    already_addressed = "already_addressed"
    no_response_needed = "no_response_needed"
    deliberate_disagreement = "deliberate_disagreement"
    boundary = "boundary"
    spam_or_untrusted = "spam_or_untrusted"


IGNORE_REASONS: list[str] = [r.value for r in IgnoreReason]


class SocialDecision(BaseModel):
    action: SocialAction
    reply_texts: list[str] = Field(default_factory=list)
    urgency: str = Urgency.normal.value
    reason: str = ""
    ignore_reason: str | None = None
    creates_intention: bool = False
    intention_spec: dict[str, Any] | None = None
    tier_used: str = "T2"
    # Whether the agent formed an opinion about this message at all.
    #
    # `act_silently` is the agent's own right [D-05] and the only thing a
    # surface can see when it reads that action: it means the agent heard the
    # question and chose not to answer it. A turn in which no model ever
    # answered is not that, and carrying it under the same action is how an
    # outage reached the person as "the agent chose not to reply". Every consumer
    # of a decision -- the silence the agent records about itself, the decline it
    # publishes, the message queue that retires the row -- reads this instead, so
    # "nothing was decided" stays distinguishable from "silence was chosen" all
    # the way to the surface.
    decided: bool = True
