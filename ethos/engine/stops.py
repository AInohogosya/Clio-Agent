"""The record of how work ended, kept where the agent can read it back.

NA-P3 asks that every task termination -- success, stop, error -- and every
model or tool failure carries a distinguishable reason, and that the reason
reaches the person through the agent's own words rather than through a template
this program composed. This module is the first half of that: a ledger of stops,
each with a kind that says *what kind of ending it was*, and a rendering of the
recent ones into the workspace so the deliberation and the goal loop both see
them.

The kinds are the five distinguishable endings:

* ``completed`` -- the work reached its goal and was verified.
* ``stopped`` -- the work was ended on purpose: the person asked, or the goal
  was judged impossible and the judgment recorded.
* ``model_stop`` -- the model finished or declined; the provider said it was
  done rather than that something broke.
* ``limit_reached`` -- a ceiling ended the work or the attempt: a rate limit, a
  budget, a token ceiling, a tool timeout.
* ``error`` -- something raised or returned unusable; the work did not end by
  anyone's decision.

The second half -- the telling -- is deliberately not here. Everything this
program sends to a person is a word the model chose, so the ledger only states
the facts; the disposition prompts (NA-P3.3) tell the agent to read this block
and report the ending in its own sentences. No template lives anywhere in the
path from a stop to a person.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StopKind(str, Enum):
    completed = "completed"
    stopped = "stopped"
    model_stop = "model_stop"
    limit_reached = "limit_reached"
    error = "error"


#: How many stops the ledger keeps. Both readers read a tail -- the workspace
#: block renders the newest few, and the continuity snapshot persists them all
#: across a restart -- so the bound is a bound on memory, not on usefulness.
LEDGER_SIZE = 24

#: How many the workspace block renders. Older entries stay in the ledger for
#: the snapshot but a page showing two dozen endings is a log, not a context.
RENDER_LIMIT = 6


class StopRecord(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: StopKind = StopKind.error
    subject: str = ""
    reason: str = ""
    # What produced the stop, where it is not the task itself: "model_call",
    # "tool:fs.edit", "cycle", "planning". The task-level kinds leave this
    # empty -- the subject is the whole story there.
    what: str = ""
    intention_id: str | None = None

    def line(self) -> str:
        ts = self.ts.astimezone(UTC).strftime("%H:%M")
        text = f"[{ts}] {self.kind.value}: {self.subject}" if self.subject else f"[{ts}] {self.kind.value}"
        if self.what:
            text += f" ({self.what})"
        if self.reason:
            text += f" -- {self.reason}"
        return text


class StopsLedger:
    """A bounded ring of `StopRecord`s, renderable and persistable."""

    def __init__(self, size: int = LEDGER_SIZE, entries: list[StopRecord] | None = None):
        self._entries: deque[StopRecord] = deque(maxlen=max(1, size))
        for entry in entries or []:
            self._entries.append(entry)

    def record(
        self,
        kind: StopKind | str,
        subject: str = "",
        reason: str = "",
        what: str = "",
        intention_id: str | None = None,
        ts: datetime | None = None,
    ) -> StopRecord:
        if isinstance(kind, str):
            try:
                kind = StopKind(kind)
            except ValueError:
                kind = StopKind.error
        entry = StopRecord(
            kind=kind, subject=subject, reason=reason, what=what,
            intention_id=intention_id, ts=ts or datetime.now(UTC),
        )
        self._entries.append(entry)
        return entry

    def entries(self) -> list[StopRecord]:
        return list(self._entries)

    def render(self) -> str:
        """The block for the workspace, or empty when nothing has stopped.

        The framing sentences live in the identity kernel's stop-disposition
        note; this block carries only the facts and the one thing the agent
        must not do with them -- nothing at all. The instruction that the
        agent reports these in its own words is stated here too, because the
        deliberation prompt is not the only reader.
        """
        if not self._entries:
            return ""
        lines = [
            "## How recent work ended",
            "Each line is a stop with its reason, as recorded. When somebody asks "
            "about work here, report the state and the reason in your own words -- "
            "never as a status template, and never a reason the line does not carry.",
        ]
        for entry in list(self._entries)[-RENDER_LIMIT:]:
            lines.append(f"- {entry.line()}")
        return "\n".join(lines)

    def snapshot(self) -> list[dict[str, Any]]:
        return [e.model_dump(mode="json") for e in self._entries]

    @classmethod
    def restore(cls, payload: Any) -> StopsLedger:
        if not isinstance(payload, list):
            return cls()
        entries: list[StopRecord] = []
        for raw in payload:
            try:
                entries.append(StopRecord.model_validate(raw))
            except Exception:
                continue
        return cls(entries=entries)


def disposition_from_exception(exc: BaseException) -> StopKind:
    """What kind of ending a raised failure is.

    A budget cap and a rate limit are limits, not malfunctions: the machine said
    "not now" or "not this much", which is a different truth from "something
    broke". Reading them as errors made every report of a pause read as a crash.
    Everything else -- a timeout on a provider, a connection refused, a parse
    that would not parse -- is an error in the ordinary sense.
    """
    from ethos.gateway.cost import BudgetExceededError
    from ethos.gateway.providers.base import ProviderError
    from ethos.gateway.router import NoModelAvailable

    if isinstance(exc, BudgetExceededError):
        return StopKind.limit_reached
    if isinstance(exc, NoModelAvailable):
        return StopKind.limit_reached
    if isinstance(exc, ProviderError) and exc.status == 429:
        return StopKind.limit_reached
    return StopKind.error
