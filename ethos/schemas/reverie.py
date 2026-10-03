from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ReverieKind(str, Enum):
    """What the agent decided to do with the thing it noticed.

    `none` is a first-class outcome and not a failure to parse. A person is not
    interested in something every time they have a spare moment, and an idle
    mind that can only ever manufacture a subject is a machine keeping itself
    busy. Honouring "nothing caught me" is what keeps the rest of this honest.
    """

    venture = "venture"
    question = "question"
    none = "none"


class ReverieSubject(BaseModel):
    """One thing the agent chose to be drawn to, and why it caught."""

    kind: ReverieKind = ReverieKind.none
    subject: str = ""
    why: str = ""
    first_step: str = ""
    interest: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_something(self) -> bool:
        return self.kind != ReverieKind.none and bool(self.subject.strip())


class ReverieResult(BaseModel):
    """What one idle pass of thought actually left behind.

    `committed_id` is the point of the whole mechanism: a subject that was not
    written down somewhere durable is a sentence in a log, forgotten at the next
    restart. What it became — a venture intention, an open question — is what
    makes the agent the sort of thing that can have interests.
    """

    subject: ReverieSubject = ReverieSubject()
    committed_id: UUID | None = None
    committed_kind: str = ""
    refused: str = ""
    cost_usd: float = 0.0
    thought: str = ""
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
