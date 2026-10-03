from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventKind:
    PERCEPT = "percept"
    INBOUND_MSG = "msg.inbound"
    OUTBOUND_MSG = "msg.outbound"
    TIMER_FIRED = "timer.fired"
    THREAD_REQUEST = "thread.request"
    THREAD_RESULT = "thread.result"
    CONTROL_UPDATE = "control.update"
    GUARDIAN_UNDO = "guardian.undo"
    THOUGHT_NEW = "thought.new"
    INTENTION_UPDATE = "intention.update"
    PRESENCE_UPDATE = "presence.update"
    MESSAGE_NEW = "message.new"
    MESSAGE_HANDLED = "message.handled"
    ACTION_UPDATE = "action.update"
    BUDGET_UPDATE = "budget.update"
    GUARDIAN_UPDATE = "guardian.update"
    SCHEDULE_SET = "schedule.set"
    COMMS_SEND = "comms.send"
    CONSOLIDATE_DONE = "consolidate.done"
    VERIFY_REQUEST = "verify.request"
    WAKE_NUDGE = "wake.nudge"


class Trust(str, Enum):
    internal = "internal"
    known = "known"
    untrusted = "untrusted"


class Event(BaseModel):
    id: int | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: str
    source: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    salience: float | None = None
    trust: str = Trust.internal.value
    consumed_by: list[str] = Field(default_factory=list)


class Percept(BaseModel):
    event_id: int | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: str
    source: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    sender_class: str = "system"
    sender_person: str | None = None
    urgency: float = 0.2
    summary: str = ""
    salience: float | None = None
    trust: str = Trust.internal.value
    is_untrusted: bool = False


class ScoredPercept(BaseModel):
    percept: Percept
    salience: float
    components: dict[str, float] = Field(default_factory=dict)


class Appraisal(BaseModel):
    interrupt: bool = False
    new_focus: dict[str, Any] | None = None
    top: list[ScoredPercept] = Field(default_factory=list)
    refined: bool = False


def percept_from_event(event: Event) -> Percept:
    p = dict(event.payload)
    sender_class = p.pop("sender_class", "system")
    sender_person = p.pop("sender_person", None)
    urgency = float(p.pop("urgency", 0.2))
    summary = str(p.pop("summary", event.kind))
    untrusted = str(event.trust) in (Trust.untrusted.value, "untrusted") or p.pop("is_untrusted", False)
    return Percept(
        event_id=event.id,
        ts=event.ts,
        kind=event.kind,
        source=event.source,
        payload=p,
        sender_class=sender_class,
        sender_person=sender_person,
        urgency=urgency,
        summary=summary,
        trust=event.trust,
        is_untrusted=bool(untrusted),
    )
