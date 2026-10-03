from __future__ import annotations

import time
from collections import deque
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class State(str, Enum):
    THINKING_FOCUSED = "THINKING_FOCUSED"
    THINKING_REFLECTING = "THINKING_REFLECTING"
    ACTING = "ACTING"
    WAITING = "WAITING"
    NOTIFYING = "NOTIFYING"
    RESTING = "RESTING"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"


STATE_GROUP: dict[State, str] = {
    State.THINKING_FOCUSED: "AWAKE",
    State.THINKING_REFLECTING: "AWAKE",
    State.ACTING: "AWAKE",
    State.WAITING: "AWAKE",
    State.NOTIFYING: "AWAKE",
    State.RESTING: "RESTING",
    State.PAUSED: "PAUSED",
    State.RESUMING: "AWAKE",
}

_AWAKE = {State.THINKING_FOCUSED, State.THINKING_REFLECTING, State.ACTING, State.WAITING, State.NOTIFYING}
_ALL = set(State)

ALLOWED_TRANSITIONS: dict[State, set[State]] = {}
for _s in _ALL:
    allowed = set(_AWAKE) | {State.RESTING, State.PAUSED, State.RESUMING}
    allowed.discard(_s)
    ALLOWED_TRANSITIONS[_s] = allowed
ALLOWED_TRANSITIONS[State.PAUSED] = {State.RESUMING}
ALLOWED_TRANSITIONS[State.RESTING] = _AWAKE | {State.PAUSED, State.RESTING}


class StateMachine:
    """Hierarchical vigilance states [D-13] with validated transitions.

    Every state here is a way of being *awake and working*. There is no state
    for being off, because there is no longer an hour of the day in which the
    agent is off: the smallest vocabulary that can hold "thinking", "acting",
    "resting" and "paused" is the one that cannot imply the hours are someone
    else's.
    """

    def __init__(self, initial: State = State.WAITING, thresholds: dict[str, float] | None = None):
        self.state = initial
        self.entered_at = time.monotonic()
        self.entered_wall = datetime.now(UTC)
        self.history: deque[dict[str, Any]] = deque(maxlen=200)
        self.thresholds = thresholds or {
            State.THINKING_FOCUSED.value: 0.65,
            State.THINKING_REFLECTING.value: 0.40,
            State.RESTING.value: 0.50,
        }
        self.history.append({"state": initial.value, "at": self.entered_wall.isoformat(), "reason": "init"})

    def interrupt_threshold(self, state: State | None = None) -> float:
        state = state or self.state
        return self.thresholds.get(
            state.value,
            self.thresholds.get("default", 0.65),
        )

    def can_transition(self, target: State) -> bool:
        return target in ALLOWED_TRANSITIONS[self.state] or target == self.state

    def transition(self, target: State, reason: str) -> bool:
        if target == self.state:
            return True
        if not self.can_transition(target):
            return False
        self.state = target
        self.entered_at = time.monotonic()
        self.entered_wall = datetime.now(UTC)
        self.history.append({"state": target.value, "at": self.entered_wall.isoformat(), "reason": reason})
        return True

    def time_in_state(self) -> float:
        return time.monotonic() - self.entered_at

    def group(self) -> str:
        return STATE_GROUP[self.state]

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "group": self.group(),
            "entered_at": self.entered_wall.isoformat(),
            "seconds_in_state": round(self.time_in_state(), 1),
        }
