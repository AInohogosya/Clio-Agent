from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryKind(str, Enum):
    episode = "episode"
    fact = "fact"
    knowhow = "knowhow"
    question = "question"


class MemoryHit(BaseModel):
    id: str
    kind: str
    summary: str
    body: dict[str, Any] | None = None
    ts: datetime | None = None
    importance: float = 0.0
    strength: float = 0.0
    score: float = 0.0
    components: dict[str, float] = Field(default_factory=dict)
    embedding: list[float] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class FactRecord(BaseModel):
    id: UUID
    subject: str
    statement: str
    confidence: float
    provenance: dict[str, Any]
    field: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    superseded_by: UUID | None = None
    strength: float = 0.5


class KnowHowRecord(BaseModel):
    id: UUID
    situation: str
    advice: str
    evidence: dict[str, Any]
    uses: int = 0
    successes: int = 0
    retired: bool = False


class QuestionRecord(BaseModel):
    id: UUID
    text: str
    origin_episode: UUID | None = None
    priority: float = 0.5
    status: str = "open"
