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
    # A leaf whose `depends_on` predecessors are all done and whose executor is
    # a person: the work is ready, and the thing it is waiting on is a reply.
    # Not terminal and not open-to-the-agent — `get_ready_intentions` filters it
    # out so the solver never picks it up; the reply path closes it.
    awaiting_human = "awaiting_human"
    done = "done"
    parked = "parked"
    failed = "failed"
    cancelled = "cancelled"


# How a node of a DAG is executed. `self` is the agent's own solver loop; the
# others name who actually does the work, and `human` is the one the agent
# cannot do for itself — it can only ask, wait, and check the answer.
EXECUTOR_KINDS = frozenset({"self", "thread", "subagent", "resident", "human"})


class IntentionEdgeRecord(BaseModel):
    """One directed edge in a Recursive Intention Graph.

    `parent_id` -> `child_id` with `edge_type` in
    ``depends_on | verifies | synthesizes | informs``. `gate_policy` carries
    the optional verification gate on the edge (e.g. a T3 judge requirement);
    an empty policy means the edge is a plain ordering constraint.
    """

    parent_id: UUID
    child_id: UUID
    edge_type: str = "depends_on"
    gate_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


EDGE_TYPES = frozenset({"depends_on", "verifies", "synthesizes", "informs"})


class WorkspaceLeaseRecord(BaseModel):
    """A transactional workspace lease: an isolated worktree/snapshot per
    intention with a budget ceiling and an allowed-paths fence."""

    id: str
    intention_id: UUID | None = None
    kind: str = "git_worktree"
    root_path: str = ""
    base_ref: str | None = None
    target_path: str | None = None
    budget_limit_usd: float | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    # Clio 3.2 (clio): EV-proportional allocation + lease/assignment.
    # `budget_allocated_usd` mirrors `budget_usd` when the allocator set it;
    # `budget_spent_usd` is the ledger the lease gate enforces against.
    budget_allocated_usd: float | None = None
    budget_spent_usd: float = 0.0
    expected_value: float | None = None
    lease_id: str | None = None
    assigned_principal_id: str | None = None
    # Clio Agent 3.3 project leadership: which container this node belongs to,
    # which root it hangs from, and who is supposed to execute it. `project_id`
    # is what every journal row, artifact and progress report is attributed
    # by; `executor_kind == "human"` marks work only a person can do.
    project_id: UUID | None = None
    root_intention_id: UUID | None = None
    executor_kind: str = "self"
    instruction_spec: dict[str, Any] | None = None
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
            IntentionStatus.awaiting_human.value,
        )


#: The stages of commissioned work that somebody is waiting to hear about.
#:
#: A policy rather than a list of statuses. `done`, `parked` and `awaiting_review`
#: are the three the solver loop reaches when work stops moving, and all three are
#: points at which a person who asked for the work is owed an answer -- one way or
#: the other. `failed` and `cancelled` are not here because nothing in the loop
#: writes them: a goal that cannot be verified is parked with a reason rather than
#: failed, so a list that named them would read as coverage of a path that does
#: not exist, which is the same mistake the blocked-status query made.
STAGE_NOTIFIABLE = frozenset({
    IntentionStatus.done.value,
    IntentionStatus.parked.value,
    IntentionStatus.awaiting_review.value,
})


class StageNotice(BaseModel):
    """Work somebody commissioned reached a stage they are owed an answer about.

    A record of a fact, and -- until `delivered_at` is set -- a debt. It exists
    because the two facts it carries were both missing: that the work finished,
    which is what a reply answering "did you do it?" has to draw on rather than
    infer; and that the word is still owed, which is what gives the loop a pass to
    spend saying it.

    The words themselves are not here, and deliberately. Everything this program
    sends to a person is a word the model chose, so a notice says *that* the agent
    owes somebody something and *about what*, and the composing happens where every
    other reply is composed.
    """

    id: UUID = Field(default_factory=uuid4)
    intention_id: UUID
    stage: str
    title: str = ""
    detail: str = ""
    person_id: str | None = None
    channel: str | None = None
    conversation_key: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    delivered_at: datetime | None = None
    delivery: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    next_attempt_at: datetime | None = None

    @property
    def owed(self) -> bool:
        return self.delivered_at is None


class Bookmark(BaseModel):
    bookmarked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    step_id: str | None = None
    approach_id: str | None = None
    last_action: dict[str, Any] | None = None
    partial_results: dict[str, Any] = Field(default_factory=dict)
    next_planned_step: str | None = None
    mental_context: str | None = None
    open_hypotheses: list[str] = Field(default_factory=list)
    # The goal this bookmark resumes, as `{"title", "desired_end_state", "status"}`.
    #
    # The focus carries an intention id, and a restart that loses the intention
    # row -- or a bookmark read back before the store is reachable -- would
    # otherwise hold a pointer to work whose substance it cannot state. The
    # original goal is what NA-P2.3 says a resumed task preserves, so it is
    # written into the bookmark itself: an interruption or a restart picks the
    # work back up with its aim attached, not just its last step.
    goal: dict[str, Any] = Field(default_factory=dict)


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
    # What failed inside this result, structured rather than only narrated.
    #
    # The thoughts already carry the words, but a thought is for the model to
    # read next cycle; the stops ledger (NA-P3.2) needs the failure as a fact
    # with a kind -- a model call that raised, a tool that raised, a rate limit
    # being waited out -- so the reason a person is eventually told comes from
    # the record rather than from a parse of the prose. Each entry is
    # `{"kind": "error"|"limit_reached", "what": str, "detail": str}`.
    failures: list[dict[str, Any]] = Field(default_factory=list)


class Decision(BaseModel):
    kind: str = "none"
    tier: str = "T1"
    thoughts: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    social: dict[str, Any] | None = None
    # The stage notice this pass owes somebody an answer about. It travels on the
    # decision rather than being re-read in `act` for the same reason `social`
    # does: `decide` chose which debt to pay on this pass, and `act` must pay that
    # one rather than whichever row the next query happens to return first.
    stage: dict[str, Any] | None = None
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
    # Values are `Any`, not `str`, because the commission also carries
    # `sequenced` (bool): whether the message sequenced this work behind work
    # in hand. A `str` value coerces that flag to a string or rejects it.
    commission: dict[str, Any] | None = None
