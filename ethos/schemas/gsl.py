from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class IntentionStatus(str, Enum):
    proposed = "proposed"
    active = "active"
    in_progress = "in_progress"
    waiting = "waiting"
    awaiting_review = "awaiting_review"
    done = "done"
    parked = "parked"
    failed = "failed"
    cancelled = "cancelled"


class IntentionKind(str, Enum):
    goal = "goal"
    task = "task"
    commitment = "commitment"
    venture = "venture"
    habit = "habit"


class Focus(BaseModel):
    intention_id: UUID | None = None
    title: str
    kind: str = "task"
    why: str = ""
    bookmark: dict[str, Any] = Field(default_factory=dict)


class IntentionRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    parent_id: UUID | None = None
    kind: str = IntentionKind.task.value
    title: str
    desired_end_state: str = ""
    success_criteria: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    origin: str = "self"
    commissioned_by: str | None = None
    status: str = IntentionStatus.proposed.value
    priority: float = 0.5
    deadline: datetime | None = None
    budget_usd: float | None = None
    spent_usd: float = 0.0
    resume_state: dict[str, Any] = Field(default_factory=dict)
    hypothesis: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_reason: str | None = None

    def is_commissioned(self) -> bool:
        return self.commissioned_by is not None

    def is_open(self) -> bool:
        return self.status in (
            IntentionStatus.proposed.value,
            IntentionStatus.active.value,
            IntentionStatus.in_progress.value,
            IntentionStatus.waiting.value,
            IntentionStatus.awaiting_review.value,
        )


class Bookmark(BaseModel):
    bookmarked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    step_id: str | None = None
    approach_id: str | None = None
    last_action: dict[str, Any] | None = None
    partial_results: dict[str, Any] = Field(default_factory=dict)
    next_planned_step: str | None = None
    mental_context: str | None = None
    open_hypotheses: list[str] = Field(default_factory=list)


class DecisionRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    intention_id: UUID | None = None
    statement: str
    rationale: str
    decided_by: str = "self"
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    superseded_by: UUID | None = None
    reversible: bool = True


class AttemptRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    intention_id: UUID
    approach: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    outcome: str | None = None
    evaluation: dict[str, Any] = Field(default_factory=dict)
    lessons: list[str] = Field(default_factory=list)
    cost_usd: float = 0.0


class PlanStep(BaseModel):
    id: str
    description: str
    tool_hints: list[str] = Field(default_factory=list)
    success_check: dict[str, Any] = Field(default_factory=dict)
    reversible: bool = True
    status: str = "pending"
    attempts: int = 0
    result: dict[str, Any] | None = None


class Plan(BaseModel):
    approach_id: str = "approach-1"
    approach: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    strategy_number: int = 1

    def next_step(self) -> PlanStep | None:
        for s in self.steps:
            if s.status in ("pending", "in_progress"):
                return s
        return None


class EvaluationResult(BaseModel):
    passed: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    difficulty: float = 0.5
    notes: list[str] = Field(default_factory=list)
    # How many of the checks actually looked at something, and therefore whether
    # anything was verified at all.
    #
    # `passed` answers "did anything object?" and an empty list of checks is a
    # pass because there is nothing to object to. `verified` answers "was this
    # work checked?", which is a different question and the one a person asking
    # whether the task got done actually cares about. An intention with no
    # success criteria scored as `passed: True`, which is how a coding task
    # whose tests were never run could be closed as verified.
    checks_run: int = 0
    verified: bool = False


class VerificationResult(BaseModel):
    rung_reached: int = 0
    passed: bool = False
    objective: EvaluationResult | None = None
    verifier: dict[str, Any] | None = None
    human_pending: bool = False
    issues: list[str] = Field(default_factory=list)
    # Files this piece of work changed, and the checks derived from them. Kept on
    # the result because the evidence is the answer to "how do you know?", and a
    # verdict that arrives without it cannot be argued with by the person who
    # has to live with it.
    changed_files: list[str] = Field(default_factory=list)
    derived_checks: list[str] = Field(default_factory=list)
    # Changed code that no check was able to look at. Non-empty means the
    # verdict is "not verified" however it was scored.
    uncovered_files: list[str] = Field(default_factory=list)


class SolverOutcome(str, Enum):
    step_done = "step_done"
    step_failed = "step_failed"
    strategy_switch = "strategy_switch"
    goal_done = "goal_done"
    goal_failed = "goal_failed"
    awaiting_review = "awaiting_review"
    awaiting_input = "awaiting_input"
    parked = "parked"


class SolverResult(BaseModel):
    outcome: SolverOutcome
    thoughts: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    social: dict[str, Any] | None = None
    plan: Plan | None = None
    verification: VerificationResult | None = None
    cost_usd: float = 0.0
    lessons: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    kind: str = "none"
    tier: str = "T1"
    thoughts: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    social: dict[str, Any] | None = None
    focus_change: dict[str, Any] | None = None
    rest: bool = False
    sleep: bool = False
    rationale: str = ""


class Outcome(BaseModel):
    ok: bool = True
    results: list[dict[str, Any]] = Field(default_factory=list)
    cost_usd: float = 0.0
    duration_ms: int = 0
    # Work this turn committed to and queued, as {"intention_id", "title"}.
    #
    # It has to travel on the outcome rather than being set on `Life.focus` where
    # it was created: `_cycle` calls `next_focus` after `record`, and that call is
    # what decides what the next cycle works on. A commission that was announced in
    # this cycle's reply and dropped before then is a promise the agent walked away
    # from having made it.
    commission: dict[str, str] | None = None
