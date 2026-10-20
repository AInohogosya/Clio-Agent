from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class TurnKind(str, Enum):
    """What a unit of working memory is about.

    The kinds exist so the buffer can be rolled up unevenly. A person speaking
    and a tool returning 4KB of output are not the same weight of experience:
    social turns are narratable as a unit, tool output is evidence, and the two
    compress differently.
    """

    inbound = "inbound"
    outbound = "outbound"
    action = "action"
    result = "result"
    failure = "failure"
    thought = "thought"
    decision = "decision"
    event = "event"


# Kinds that describe the agent talking to a person. A conversation is only
# continuous if both directions are in the same buffer; keeping the inbound half
# out is how an agent ends up answering a question nobody can see it received.
SOCIAL_KINDS: frozenset[str] = frozenset({TurnKind.inbound.value, TurnKind.outbound.value})


class ContextTurn(BaseModel):
    """One thing that happened, held in working memory until it is narrated.

    `summary` is what the model will read, so it is written short and to be read
    aloud to itself. `detail` keeps the evidence — the tool output, the message
    body — and is rendered only when a turn earns the budget to carry it, so a
    long result cannot quietly consume the whole context.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:16])
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: str
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    salience: float = 0.5
    intention_id: str | None = None
    conversation_key: str | None = None

    @property
    def is_social(self) -> bool:
        return self.kind in SOCIAL_KINDS


class NarrativeLine(BaseModel):
    """What the agent wrote about a stretch of turns it has already let go of.

    This is the difference between keeping a transcript and keeping a thread. The
    raw turns roll off the buffer because there is no room for them; the line
    written about them is what survives, in the agent's own words, so what it
    lost is a detail rather than an episode.
    """

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    text: str
    turn_count: int = 0
    kinds: list[str] = Field(default_factory=list)


class ContextSnapshot(BaseModel):
    """The part of the context that outlives the process.

    Continuity is state, not transcript [P3], so what is written down here is the
    thread as written, plus only as many of the newest raw turns as are needed to
    pick the thread back up mid-sentence. Everything older has already been
    narrated, or was never going to be read.
    """

    narrative: list[NarrativeLine] = Field(default_factory=list)
    turns: list[ContextTurn] = Field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe by construction.

        A snapshot is the one thing the agent cannot rebuild from its own memory,
        and it is written on the way through `ContinuityManager.save` at the end
        of every cycle. So a turn carrying something pydantic cannot serialise --
        a raw object out of a tool result, a socket handle -- must not be
        allowed to take the whole thread down with it at the end of a cycle. The
        summary is what matters here, so an unserialisable detail is dropped and
        the turn kept.
        """
        return {
            "narrative": [_dump(line) for line in self.narrative],
            "turns": [_dump(turn) for turn in self.turns],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ContextSnapshot:
        """Rebuild from a snapshot, skipping anything unreadable.

        A snapshot is the one thing an agent cannot reconstruct from its own
        memory, so a single bad row must not cost it the whole thread — nor stop
        it waking up. Partial is strictly better than refusing to start.
        """
        if not isinstance(payload, dict):
            return cls()
        return cls(
            narrative=_validated_list(NarrativeLine, payload.get("narrative")),
            turns=_validated_list(ContextTurn, payload.get("turns")),
        )


def _dump(model: BaseModel) -> dict[str, Any]:
    try:
        return model.model_dump(mode="json")
    except Exception:
        # Keep the turn, lose only the part that will not serialise.
        try:
            return {
                "ts": model.ts.isoformat(),
                "kind": getattr(model, "kind", "event"),
                "summary": getattr(model, "summary", "") or getattr(model, "text", ""),
                "detail": {},
                "salience": float(getattr(model, "salience", 0.5) or 0.5),
                "id": getattr(model, "id", ""),
            }
        except Exception:
            return {}


def _validated_list(model: type[BaseModel], value: Any) -> list[BaseModel]:
    if not isinstance(value, list):
        return []
    out: list[BaseModel] = []
    for item in value:
        try:
            out.append(model.model_validate(item))
        except Exception:
            continue
    return out


class ConversationExchange(BaseModel):
    """One turn of a real conversation, in the order it was actually said."""

    ts: datetime
    direction: str
    person_id: str | None = None
    text: str
    channel: str = ""


class ConversationState(BaseModel):
    """One conversation as a person is living it, not as the agent recorded it.

    A conversation is not the same thing as a thread. The thread is what the
    agent has been *doing*: tool calls, failures, half-formed ideas, compressed
    into a line every few dozen turns and then dropped. This is the other half —
    what has actually been said, on one door, to one person, and in particular
    whose turn it is to speak.

    That last part is what an agent cannot reconstruct for itself. A person can
    scroll back and read every message the agent has sent them this week; the
    agent had a buffer of its own turns, summarised and then narrated away, and
    nothing anywhere recorded that its last word on this conversation was still
    hanging in the air. So it starts the next thing anyway, and the next thing
    after that — a conversation read as though each turn were the first.
    """

    key: str
    person_id: str | None = None
    channel: str = ""
    last_ts: datetime
    last_direction: str = ""
    last_text: str = ""
    exchanges: list[ConversationExchange] = Field(default_factory=list)

    @property
    def awaiting_reply(self) -> bool:
        """Whether the agent spoke last and has not been answered."""
        return self.last_direction == "outbound"

    def age_h(self, now: datetime) -> float:
        return max(0.0, (now - self.last_ts).total_seconds() / 3600.0)
