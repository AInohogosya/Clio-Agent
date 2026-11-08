from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from ethos.config import EthosConfig
from ethos.engine.rhythm import backoff_seconds
from ethos.gateway.normalizer import was_truncated
from ethos.gateway.service import format_wait, rate_limit_wait_for_attempt
from ethos.gsl.acceptance import changed_paths
from ethos.gsl.evaluator import ObjectiveChecker
from ethos.gsl.planner import (
    PlanUnavailable,
    budget_bucket_for,
    decompose_and_persist,
    devise,
    is_nothing_could_serve,
    is_rate_limited_failure,
    make_plan,
    needs_decomposition,
    parse_json_block,
    pick_approach,
    understand,
)
from ethos.gsl.verification import VerificationLadder, gate_blocks_completion, gate_children
from ethos.observability.logger import get_logger
from ethos.paths import user_home
from ethos.prompts import (
    EDIT_EXISTING_FILES,
    REFLECT_PROMPT,
    STEP_PROMPT,
    VISIBLE_DELIVERABLE,
    commissioned_note,
    delegation_note,
    dumps,
    render_constraints,
)
from ethos.schemas.context import TurnKind
from ethos.schemas.gsl import (
    STAGE_NOTIFIABLE,
    Bookmark,
    IntentionRecord,
    IntentionStatus,
    Plan,
    SolverOutcome,
    SolverResult,
)
from ethos.schemas.memory import MemoryHit
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.gsl")

# Two failures on one step hand the pass to a fresh approach. This is a
# recovery action, not a stop; how many approaches a goal may burn through
# is `gsl.max_strategies` (0 = no ceiling).
STEP_FAILURES_BEFORE_SWITCH = 2

# The check kinds a step will be failed on. A policy, not a capability list:
# `ObjectiveChecker` implements more, and the ones left out are the ones whose
# inputs a plan invents rather than observes -- a URL, a metric target. Run and
# failed honestly, they fail steps for reasons the step never controlled.
_RUNNABLE_CHECKS = frozenset({
    "command", "file_exists", "file_contains", "syntax", "json_field",
})

# A check that writes is not a check. Plans put the whole `cat > file` (or
# `mkdir`, or `pip install`) in `success_check` and left the step's own action
# as a no-op `ls`; the checker then performed the work and the step was
# recorded as verified by a command that was never the step. These are the
# obvious mutations, matched so a read-only command with `2>&1` still passes.
_MUTATING_CHECK = re.compile(
    r"(?:^|[;&|]\s*)(?:mkdir|touch|rm|mv|cp|tee|chmod|chown"
    r"|pip\s+install|python\s+-m\s+pip|npm\s+install|yarn\s+add"
    r"|git\s+(?:add|commit|push|checkout|reset|merge|stash))\b"
    r"|\b(?:cat|printf|echo|tee)\b[^\n]*>(?![&12])"
    r"|(?:^|[^0-9&])>>?\s*[^&|]",
)

def _describe_actions(actions: list[dict[str, Any]] | None, limit: int = 6) -> str:
    """What the step's own tool calls did, for the check-repair prompt.

    A repaired check has to be grounded in something that actually happened;
    asked only about a failing check, the model rewrites it from imagination
    (`file_exists` on a path the step never used) and the loop fails again for
    the same invented reason.
    """
    lines: list[str] = []
    for action in (actions or [])[:limit]:
        tool = str(action.get("tool", "?"))
        args = action.get("args") or {}
        target = (
            args.get("path") or args.get("command") or args.get("url")
            or args.get("app_id") or args.get("query") or ""
        )
        result = str(action.get("result", ""))
        outcome = "ok" if action.get("ok", True) else "failed"
        lines.append(
            f"- {tool}({str(target)[:160]}) -> {outcome}: {result[:160]}"
        )
    return "\n".join(lines) or "(none recorded)"


def _delivered_paths(resume: dict[str, Any], limit: int = 4) -> list[str]:
    """Where the work actually put files, for the message that reports it.

    The record already knows: every `fs.*` call is in `resume["artifacts"]` with
    the path it named. What was missing is the hand-off to the person. A reply
    that says the work is done but not where it is leaves them to search for it,
    which is the same complaint as a desktop icon that never appears. Only tools
    that write contribute -- a read is not a delivered file -- and nested paths
    collapse to the outermost directory, so one folder and the files inside it
    are one address rather than five.
    """
    written: list[str] = []
    for action in resume.get("artifacts") or []:
        if not isinstance(action, dict) or not action.get("ok", True):
            continue
        tool = str(action.get("tool") or "")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        path = ""
        if tool in ("fs.write", "fs.edit", "fs.mkdir"):
            path = str(args.get("path") or "")
        elif tool == "fs.move":
            path = str(args.get("to_path") or "")
        if path and path not in written:
            written.append(path)
    outer: list[str] = []
    for path in sorted(written, key=len):
        if any(path == top or path.startswith(top.rstrip("/") + "/") for top in outer):
            continue
        outer.append(path)
    return outer[:limit]


def _delivered_note(resume: dict[str, Any]) -> str:
    paths = _delivered_paths(resume)
    return ("; files: " + ", ".join(paths)) if paths else ""


def _same_check(candidate: dict[str, Any], original: dict[str, Any]) -> bool:
    """Whether two checks are the same claim, ignoring key order."""
    keys = {k: v for k, v in candidate.items() if v not in (None, "")}
    return keys == {k: v for k, v in original.items() if v not in (None, "")}


# Keys in `resume_state` that only this module reads. Named because the state
# machine has three of them and a dict literal with three string keys in it is
# how one of them ends up spelled two ways in two places.
PLAN_ATTEMPTS = "plan_attempts"
PLAN_RETRY_AFTER = "plan_retry_after"
PLAN_LAST_FAILURE = "plan_last_failure"
PLAN_DEGRADED = "plan_degraded"

# Statuses with nothing left to do. `awaiting_review` and `waiting` are
# deliberately absent: both are still open work, held for a reason that is not
# this loop's to resolve.
_TERMINAL_STATUSES = frozenset({
    IntentionStatus.done.value,
    IntentionStatus.parked.value,
    IntentionStatus.failed.value,
    IntentionStatus.cancelled.value,
})


class GeneralSolverLoop:
    """One general framework for every goal: understand -> devise (3
    candidates) -> plan (3-7 steps) -> try_step -> evaluate -> verify ->
    reflect/revise. Strategy switching is enforced after 2 failed attempts [6.1]."""

    def __init__(
        self,
        config: EthosConfig,
        memory: Any,
        gateway: Any,
        toolhost: Any,
        verification: VerificationLadder,
        audit: Any = None,
        context: Any = None,
        thread_id: str = "self",
        rhythm: Any = None,
    ):
        self.config = config
        self.memory = memory
        self.gateway = gateway
        self.toolhost = toolhost
        self.verification = verification
        self.audit = audit
        self.context = context
        self.thread_id = thread_id
        # Optional because two callers legitimately have none -- the thread
        # worker builds a loop to run a delegated goal and is not running a life
        # loop -- and because the pacing decisions below must be answerable
        # without one. When it is wired the curve is read from the controller
        # that paces everything else; when it is not, from the same function
        # that controller calls. There is no second curve.
        self.rhythm = rhythm

    async def _recall(self, query: str, k: int) -> list[Any]:
        """Memories for a step, and no failure if there are none to be had.

        Retrieval is context, not the work. It is also the widest surface in the
        whole system for something to go wrong — it reads rows written by many
        versions of many writers and turns them into validated models — so an
        exception here used to be the most expensive kind of mistake available:
        it unwound through `_execute_step`, `advance`, `act` and out of `_cycle`,
        which meant the focus-advance at the end of the pass never ran. The agent
        kept the focus that had just crashed, retried the identical failing step
        on every cycle, and neither worked nor spoke for as long as the bad data
        lasted. A whole afternoon, lost to one row.

        `Life._assemble_workspace` already treats a failed lookup as no memories,
        because that is what it is, and this is the same trade at the same
        boundary. A step taken without its memories is a step taken blind. A step
        never taken is nothing at all, and the blindness is at least visible.
        """
        try:
            return await self.memory.search(query, k=k)
        except Exception:
            logger.exception("gsl.recall_failed", query=query[:120])
            return []

    # ---------------------------------------------------------- planning state
    #
    # The bounded part of the loop, and the part that used not to exist. Every
    # path that can fail to produce a plan goes through `_planning_failed`, which
    # is the only thing in this class that counts anything. A loop with three
    # branches and no counter is three ways to retry forever.

    def _plan_attempts(self, resume: dict[str, Any]) -> int:
        """Consecutive planning failures recorded for this intention.

        Consecutive on purpose, and reset the moment a plan is installed: a goal
        that planned once, worked for a while, and then failed to replan has not
        failed to plan six times, it has failed to replan once, and treating the
        two alike would park work that is mostly done.
        """
        try:
            return max(0, int(resume.get(PLAN_ATTEMPTS, 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _planning_degraded(self, resume: dict[str, Any]) -> bool:
        """Whether the plan request has to be made under reduced demands."""
        return self._plan_attempts(resume) >= max(1, self.config.gsl.plan_attempts_before_degrade)

    def _max_strategies(self) -> int:
        """The strategy ceiling, or 0 when there is none.

        Read per call so a settings change or a restart is honoured without
        rebuilding the loop. 0 is the field default: a step that keeps
        failing keeps earning a fresh approach for as long as the goal is
        open, and the pacing below is what keeps that from being a hot
        loop rather than a ceiling.
        """
        return max(0, int(self.config.gsl.max_strategies))

    def _planning_waiting(self, resume: dict[str, Any], now: datetime | None = None) -> float:
        """Seconds left on the backoff, or zero. Never negative.

        Read from the resume blob rather than from a sleep, because the wait has
        to survive the process: a goal whose plan failed five times and then hit a
        restart should come back after the interval it had already earned, not at
        the tick rate. And because holding the cycle open for the length of a
        four-minute backoff is a longer outage than the one being waited out.
        """
        raw = resume.get(PLAN_RETRY_AFTER)
        if not raw:
            return 0.0
        try:
            deadline = datetime.fromisoformat(str(raw))
        except ValueError:
            return 0.0
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return max(0.0, (deadline - (now or datetime.now(UTC))).total_seconds())

    def _plan_backoff_s(self, attempts: int) -> float:
        """The wait after `attempts` consecutive failures, from the shared curve."""
        cfg = self.config.gsl
        # `rhythm.backoff_seconds` is a pass-through to the same module function,
        # so the question "is there a controller wired?" has one answer either way.
        return backoff_seconds(
            attempts, base_s=cfg.plan_backoff_s, max_s=cfg.plan_backoff_max_s,
            jitter=cfg.plan_backoff_jitter,
        )

    async def _planning_failed(
        self,
        intention: IntentionRecord,
        resume: dict[str, Any],
        exc: PlanUnavailable,
        *,
        plan: Plan | None = None,
        stage: str = "plan",
    ) -> SolverResult:
        """The one exit for every failed plan call: count it, back off, park.

        Three states, reached in this order, and the order is the fix:

        1. Under `plan_attempts_before_degrade`, ask again after a backoff that
           grows with the count. The request is unchanged, because nothing yet
           says the request is what is wrong.
        2. From the degradation threshold, the *request* changes -- thinking is
           bounded, the tier is dropped, the schema ask is relaxed. Re-sending
           the identical payload with a longer sleep is the failure being fixed
           with more of it.
        3. At `plan_attempts_before_park`, `_park`. The intention is closed, so
           the next cycle has nothing to retry; the reason names what happened,
           so the blockage is a thing that can be reported instead of inferred
           from silence.

        `plan` is carried through untouched. A replan that failed keeps the plan
        it was replacing: the work already understood is worth more than the
        chance to appear to start again, and a parked intention is a record of
        the blockage rather than an erasure of the work.
        """
        attempts = self._plan_attempts(resume) + 1
        resume[PLAN_ATTEMPTS] = attempts
        resume[PLAN_LAST_FAILURE] = {"stage": stage, "kind": exc.kind, "reason": str(exc)[:300]}
        park_at = max(0, self.config.gsl.plan_attempts_before_park)
        reason = f"{attempts} consecutive planning failures ({stage}): {exc}"

        # A provider that is down, out of room or rate limiting is not the goal
        # being impossible (NA-P2.1). v3.0 counted those failures toward the
        # park threshold like any other, so a provider outage outlasted the
        # budget and the work was closed as parked -- a task dropped because the
        # world hiccupped, which is exactly the silent drop NA-P2.1 forbids: a
        # task stops only when the user asks it to or the goal itself cannot be
        # be met. Those two kinds now back off on the escalation curve and stay
        # open indefinitely; the cap on the wait (a day) keeps the retry alive.
        #
        # `park_at: 0` -- the field default -- extends the same mercy to every
        # other failure kind too: the request keeps being degraded and re-asked
        # on the backoff below for as long as the goal is open, and a positive
        # `park_at` restores the ceiling that parks instead.
        if park_at > 0 and attempts >= park_at and exc.kind not in ("rate_limited", "no_model"):
            # Parked with the count attached, so the row that says why the work
            # stopped also says how many attempts earned that conclusion. No
            # separate `_save_resume` first: `_park` writes this blob, and writing
            # it twice would be a second chance to lose one of the fields.
            await self._park(intention, reason, resume=resume)
            logger.warning(
                "gsl.plan_parked", intention=str(intention.id), attempts=attempts,
                stage=stage, kind=exc.kind, reason=str(exc)[:300],
            )
            return SolverResult(
                outcome=SolverOutcome.parked,
                plan=plan,
                thoughts=[
                    f"parked after {attempts} consecutive attempts to {stage} this goal",
                    f"last failure: {exc}",
                    "nothing has been done on it and nothing will be retried until it is reopened",
                ],
                cost_usd=0.0,
            )

        wait_s = self._plan_backoff_s(attempts - 1)
        if exc.kind == "rate_limited":
            # A 429 is the provider asking for time: five minutes after the
            # first one, then an hour, five hours, a day, staying at a day.
            # The provider's own `Retry-After` lengthens the rung when it asks
            # for longer. Anything else keeps the short configured backoff,
            # because a different failure is not one waiting fixes.
            wait_s = rate_limit_wait_for_attempt(
                attempts, getattr(exc, "retry_after_s", None),
            )
        degraded = self._planning_degraded(resume)
        resume[PLAN_DEGRADED] = degraded
        resume[PLAN_RETRY_AFTER] = (
            (datetime.now(UTC) + timedelta(seconds=wait_s)).isoformat() if wait_s > 0 else None
        )
        await self._save_resume(intention, resume)
        thoughts = [f"could not {stage} this goal (attempt {attempts}): {exc}"]
        if exc.kind == "rate_limited":
            thoughts.append(
                f"rate limited (429); waiting {format_wait(wait_s)} before asking "
                "again rather than immediately"
            )
        if degraded:
            thoughts.append(
                f"the request has been degraded after {attempts} failures: thinking "
                "bounded, tier dropped, schema relaxed"
            )
        if wait_s > 0 and exc.kind != "rate_limited":
            thoughts.append(f"asking again in about {round(wait_s)}s rather than immediately")
        logger.warning(
            "gsl.plan_unavailable", intention=str(intention.id), attempts=attempts,
            stage=stage, kind=exc.kind, degraded=degraded,
            retry_in_s=round(wait_s, 2), waiting=exc.kind == "rate_limited",
            reason=str(exc)[:300],
        )
        return SolverResult(
            outcome=SolverOutcome.step_failed, plan=plan, thoughts=thoughts,
        )

    def _backing_off(self, resume: dict[str, Any], what: str) -> SolverResult | None:
        """The shared answer to "a planning backoff is still running".

        Two branches of the state machine — understanding and planning — can be
        asked to proceed while a previous failure's backoff has not elapsed, and
        both give the same reply, so the wording lives here: what is backing
        off, when it asks again, and what failed last time. The outcome is
        always `step_failed`; a wait is not an accomplishment.
        """
        waiting = self._planning_waiting(resume)
        if waiting <= 0:
            return None
        return SolverResult(
            outcome=SolverOutcome.step_failed,
            thoughts=[
                f"{what} is backing off; the last attempt failed and the next "
                f"one is due in about {round(waiting)}s",
                f"last failure: {(resume.get(PLAN_LAST_FAILURE) or {}).get('reason', 'unknown')}",
            ],
            cost_usd=0.0,
        )

    async def _install_plan(self, intention: IntentionRecord, resume: dict[str, Any],
                            plan: Plan, *, strategy_number: int) -> None:
        """Put a plan into the resume blob and clear the failure state it answers.

        The count is about consecutive failures, and there is a plan again —
        so the attempts, the retry deadline and the recorded failure all go at
        once, in the same write that installs the plan.

        Steps already marked "done" in the previous plan are carried over into
        the new one, matched by step id. A strategy switch replaces the
        approach, not the work already finished -- otherwise every step failure
        throws away completed steps and the agent replans from zero forever.
        """
        plan_dict = plan.model_dump(mode="json")
        old_plan = resume.get("plan")
        done_by_id: dict[str, dict[str, Any]] = {}
        if isinstance(old_plan, dict):
            for s in old_plan.get("steps", []):
                if isinstance(s, dict) and s.get("status") == "done" and s.get("id"):
                    done_by_id[str(s["id"])] = s
        for s in plan_dict.get("steps", []):
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id"))
            if sid in done_by_id:
                s.clear()
                s.update(done_by_id[sid])
        resume["plan"] = plan_dict
        resume["step_attempts"] = {}
        resume["strategy_number"] = strategy_number
        resume[PLAN_ATTEMPTS] = 0
        resume[PLAN_RETRY_AFTER] = None
        resume.pop(PLAN_LAST_FAILURE, None)
        await self._save_resume(intention, resume)

    async def advance(self, intention: IntentionRecord, ws: Any) -> SolverResult:
        started = time.monotonic()
        cost = 0.0
        resume: dict[str, Any] = dict(intention.resume_state or {})

        # A closed intention has nothing to advance, and asking again is how the
        # freeze used to survive its own fix. Parking is the state this loop puts
        # an intention into precisely so that the next cycle has nothing to retry
        # -- but that is only true if coming back here is impossible, and nothing
        # in `advance` said so. The scheduler does not select a closed intention
        # and the drives do not either, so in practice it did not happen; "in
        # practice" is the whole of the guarantee, and a caller that holds an
        # intention record and calls this directly gets an infinite loop instead of
        # the answer it was told. So it is decided here.
        closed = intention.status in _TERMINAL_STATUSES
        if closed:
            logger.info(
                "gsl.advance_skipped", intention=str(intention.id), status=intention.status,
            )
            return SolverResult(
                outcome=(
                    SolverOutcome.goal_done
                    if intention.status == IntentionStatus.done.value
                    else SolverOutcome.parked
                ),
                thoughts=[
                    f"{intention.status}: this intention is closed and will not be retried"
                    + (
                        f"; why: {intention.closed_reason}"
                        if intention.closed_reason else ""
                    )
                ],
                cost_usd=cost,
            )

        if intention.status == IntentionStatus.proposed.value:
            await self.memory.update_intention(intention.id, status=IntentionStatus.active.value)
            intention.status = IntentionStatus.active.value

        budget = intention.budget_usd
        if budget is not None and intention.spent_usd >= budget:
            await self._park(intention, f"budget exhausted (${intention.spent_usd:.2f} of ${budget:.2f})")
            return SolverResult(outcome=SolverOutcome.parked,
                                thoughts=[f"parked: budget exhausted for {intention.title}"])

        if "understanding" not in resume:
            # The backoff is honoured here exactly as the plan branch honours it:
            # a failed understand is a planning failure (this branch is the first
            # thing on the way to a plan), so it counts, backs off, and parks
            # through the same bounded state machine rather than unwinding the
            # cycle and retrying invisibly on the next one.
            waiting = self._backing_off(resume, "understanding")
            if waiting is not None:
                return waiting
            memories = await self._recall(intention.title, k=10)
            # The spy belongs to this branch rather than to a helper, so the
            # failed attempt can still report what it cost. A gateway that
            # retried three times and then raised has spent money, and that money
            # has to reach the intention's budget or a goal whose planning calls
            # never succeed spends against a ceiling it never approaches.
            spy = _CostSpy(self.gateway)
            try:
                data = await understand(spy, self.config, intention, memories, context_prefix=(ws.stable_prefix_text() if ws is not None else ""))
            except Exception as exc:
                if is_rate_limited_failure(exc):
                    kind = "rate_limited"
                else:
                    kind = "no_model" if is_nothing_could_serve(exc) else "unusable"
                failed = await self._planning_failed(
                    intention, resume,
                    PlanUnavailable(
                        f"understanding request failed ({kind}): {exc}", kind=kind,
                        retry_after_s=getattr(exc, "retry_after_s", None),
                    ),
                    stage="understand",
                )
                failed.cost_usd += cost + spy.cost_usd
                return failed
            cost += spy.cost_usd
            resume.update(data)
            await self._save_resume(intention, resume)
            for question in resume.get("questions", [])[:3]:
                await self.memory.open_question(question, priority=0.4)
            # Recursive decomposition: a complex goal becomes child intentions
            # linked by depends_on/verifies/synthesizes edges, while the leaf
            # plan itself stays at most 7 steps (make_plan caps it).
            children = await self._maybe_decompose(intention, resume)
            if children:
                return SolverResult(
                    outcome=SolverOutcome.step_done,
                    thoughts=[
                        f"understood goal: {resume.get('understanding', '')[:200]}",
                        f"decomposed into {len(children)} child intention(s) (RIG)",
                    ],
                    cost_usd=cost,
                )
            return SolverResult(
                outcome=SolverOutcome.step_done,
                thoughts=[f"understood goal: {resume.get('understanding', '')[:200]}"],
                cost_usd=cost,
            )

        if not resume.get("plan"):
            # The backoff is checked before the call, not after it. A goal whose
            # plan has failed three times has earned an interval, and spending a
            # model call to find out that the interval has not elapsed is the
            # exact behaviour the interval exists to stop.
            waiting = self._backing_off(resume, "planning")
            if waiting is not None:
                return waiting
            try:
                plan, c = await self._strategy(
                    intention, resume,
                    strategy_number=int(resume.get("strategy_number", 1)),
                    plan_attempts=self._plan_attempts(resume), ws=ws,
                )
            except PlanUnavailable as exc:
                # No plan means no work, so this is a failure of the step rather
                # than an accomplishment of it. Reported as one so the intention
                # stays open and the next cycle asks again -- the alternative was
                # installing a fabricated plan and reporting the goal as planned.
                # Bounded, though: "the next cycle asks again" used to be the end
                # of the sentence, and it is now the first of three states.
                failed = await self._planning_failed(intention, resume, exc, stage="plan")
                failed.cost_usd += cost + exc.cost_usd
                return failed
            cost += c
            await self._install_plan(intention, resume, plan,
                                     strategy_number=int(plan.strategy_number))
            await self.memory.update_intention(intention.id, status=IntentionStatus.in_progress.value)
            return SolverResult(
                outcome=SolverOutcome.step_done,
                thoughts=[f"approach chosen: {plan.approach[:200]}; plan has {len(plan.steps)} steps"],
                plan=plan, cost_usd=cost,
            )

        plan = Plan.model_validate(resume["plan"])
        step = plan.next_step()
        if step is None:
            result, c = await self._finish_goal(intention, resume, plan)
            cost += c
            return result

        step_attempts = dict(resume.get("step_attempts", {}))
        failures = int(step_attempts.get(step.id, 0))
        if failures >= STEP_FAILURES_BEFORE_SWITCH:
            strategy_number = int(resume.get("strategy_number", 1)) + 1
            max_strategies = self._max_strategies()
            if max_strategies > 0 and strategy_number > max_strategies:
                await self._park(intention, f"{max_strategies} strategies failed on step {step.id}")
                return SolverResult(outcome=SolverOutcome.parked, cost_usd=cost,
                                    thoughts=[f"parked after {max_strategies} strategies"])
            waiting = self._planning_waiting(resume)
            if waiting > 0:
                return SolverResult(
                    outcome=SolverOutcome.step_failed, plan=plan, cost_usd=cost,
                    thoughts=[f"strategy #{strategy_number} is waiting out a planning backoff "
                             f"of about {round(waiting)}s"],
                )
            try:
                new_plan, c = await self._strategy(
                    intention, resume, strategy_number=strategy_number,
                    plan_attempts=self._plan_attempts(resume), ws=ws,
                )
            except PlanUnavailable as exc:
                # The existing plan stays in place. Replacing it with nothing, or
                # with invented steps, would discard the work already understood.
                failed = await self._planning_failed(
                    intention, resume, exc, plan=plan, stage="replan",
                )
                failed.cost_usd += cost + exc.cost_usd
                return failed
            cost += c
            await self._install_plan(intention, resume, new_plan, strategy_number=strategy_number)
            await self._pace_strategy_switch(intention, resume, strategy_number)
            return SolverResult(
                outcome=SolverOutcome.strategy_switch, plan=new_plan, cost_usd=cost,
                thoughts=[f"strategy switch #{strategy_number} after repeated failures"],
            )

        # A step is decided by a model call, and a model call can be rate
        # limited: without this gate a 429 would be re-asked at the tick rate,
        # every cycle burning a full retry budget to be told the same thing.
        # The gate is set by the rate-limited branch of `_execute_step` below
        # (and by planning backoffs, which are equally about not calling yet),
        # and it reads as a named wait on screen rather than as silence.
        waiting = self._backing_off(resume, f"step {step.id}")
        if waiting is not None:
            return waiting
        step_result = await self._execute_step(intention, resume, plan, step, ws)
        cost += step_result.cost_usd
        step_result.cost_usd += cost
        _ = time.monotonic() - started

        steps_map = {s["id"]: s for s in resume["plan"]["steps"]}
        step_attempts = dict(resume.get("step_attempts", {}))
        if step_result.outcome == SolverOutcome.step_done:
            step_attempts.pop(step.id, None)
            steps_map[step.id]["status"] = "done"
            steps_map[step.id]["result"] = step_result.actions[-1] if step_result.actions else None
            resume["plan"]["steps"] = list(steps_map.values())
            resume["step_attempts"] = step_attempts
            resume.setdefault("artifacts", []).extend(
                a for a in step_result.actions if isinstance(a, dict)
            )
            resume["artifacts"] = (resume.get("artifacts") or [])[-20:]
            resume["last_action"] = step_result.actions[-1] if step_result.actions else {}
            resume["next_planned_step"] = plan.next_step().description if plan.next_step() else "finish"
            await self._save_resume(intention, resume)
            if plan.next_step() is None:
                final, c2 = await self._finish_goal(intention, resume, plan)
                final.cost_usd += cost
                return final
            return step_result

        step_attempts[step.id] = failures + 1
        resume["step_attempts"] = step_attempts
        await self._save_resume(intention, resume)
        lessons_result = await self._reflect(intention, step, step_result, failures + 1)
        step_result.lessons.extend(lessons_result.get("lessons", []))
        # Held on the attempt rather than written to it here: this approach may
        # survive to another step, and the attempt closes when the approach is
        # given up, not every time a step stumbles. The save above happened
        # before reflection ran, so this write has to persist them itself.
        held = list(resume.get("attempt_lessons") or [])
        for lesson in lessons_result.get("lessons", []):
            text = str(lesson).strip()
            if text and text not in held:
                held.append(text[:400])
        resume["attempt_lessons"] = held[-10:]
        await self._save_resume(intention, resume)
        for kh in lessons_result.get("knowhow", [])[:2]:
            if isinstance(kh, dict) and kh.get("situation") and kh.get("advice"):
                await self.memory.record_knowhow(
                    situation=str(kh["situation"]), advice=str(kh["advice"]),
                    evidence={"intention_id": str(intention.id), "step": step.id},
                )
        for question in lessons_result.get("open_questions", [])[:2]:
            await self.memory.open_question(str(question), priority=0.6)
        return step_result

    async def _close_attempt(
        self, intention: IntentionRecord, resume: dict[str, Any], outcome: str,
        cost_usd: float = 0.0,
    ) -> None:
        """Close the attempt this intention has open, if it has one.

        `start_attempt` was called on every successful plan install and
        `end_attempt` was called from nowhere. Every attempt row the database
        accumulated therefore had `ended_at`, `outcome` and `lessons` null, and
        stayed that way.

        That is worse than untidy bookkeeping, because `_strategy` reads those
        rows back: `list_attempts` supplies the lessons that are shown to the
        next `devise` call, so this is the loop's only mechanism for learning
        from its own failures. With the write missing, the agent was given an
        ever-growing list of attempts that had all apparently just started and
        none of which had ever concluded, and no lesson from any of them ever
        reached the prompt that decides what to try next. It repeated the same
        failed approach at the same goal indefinitely, with nothing in its
        context saying it had tried that.

        Lessons accumulate in `resume` as reflection produces them, because the
        moment an attempt closes is not the moment its lessons are written: the
        attempt closes when the approach is abandoned, and the step that taught
        the lesson may be several steps earlier. So they are held here and
        written once, on the attempt that was actually in flight when the
        approach stopped being tried.

        The id is popped whether or not the write succeeds. Closing an attempt
        twice would overwrite its outcome with a later one, and the second
        writer has strictly less to say than the first.
        """
        attempt_id = resume.pop("attempt_id", None)
        lessons = resume.pop("attempt_lessons", None) or []
        if not attempt_id:
            return
        try:
            await self.memory.end_attempt(
                UUID(str(attempt_id)), outcome,
                {"intention_id": str(intention.id), "title": intention.title[:200]},
                [str(lesson)[:400] for lesson in lessons][:10],
                float(cost_usd),
            )
        except Exception:
            logger.warning(
                "gsl.attempt_close_failed", intention=str(intention.id),
                attempt=str(attempt_id),
            )

    async def _strategy(self, intention: IntentionRecord, resume: dict[str, Any],
                        strategy_number: int, plan_attempts: int = 0, ws: Any = None) -> tuple[Plan, float]:
        """Choose an approach and plan from it, or raise `PlanUnavailable`.

        Every way of failing here raises, which is the point. This used to
        fabricate an approach when the devise call returned nothing, and then
        build a real plan out of it -- so a goal whose planning calls were all
        failing still arrived at `in_progress` holding a plan, and the only
        trace of the failure was a log line nobody was reading.

        The attempt is opened here and its id is kept in the resume blob, so
        `advance` can close it. `start_attempt` used to be called and nothing
        ever called `end_attempt`: every attempt row in the database had a null
        outcome and no lessons, forever. That is the channel the loop's own
        lessons travel back through -- `_strategy` reads `list_attempts` and
        feeds their lessons into the next devise call -- so a closed loop of
        lessons was never actually closed. The agent could learn nothing from
        having failed, because the record of the failure was never written.
        """
        cost = 0.0
        # An attempt ends exactly where the next one begins. Closing it here, and
        # only here, means every attempt that was ever opened is closed by
        # whichever of the three things happened to it: this approach was
        # abandoned, the goal was verified, or the goal was parked.
        await self._close_attempt(intention, resume, "failed")
        knowhow = await self.memory.find_knowhow(intention.title, k=5)
        attempts = await self.memory.list_attempts(intention.id, limit=5)
        lessons = [lesson for a in attempts for lesson in a.lessons]
        try:
            approaches, c1 = await self._devise_with_cost(intention, resume, knowhow, lessons, context_prefix=(ws.stable_prefix_text() if ws is not None else ""))
        except PlanUnavailable as exc:
            exc.cost_usd += cost
            raise
        cost += c1
        approach = pick_approach(approaches)
        if approach is None:
            # `_devise_with_cost` already refuses an empty candidate list, so
            # this is unreachable rather than load-bearing. It raises instead of
            # inventing, because the whole reason it is here at all is that the
            # inventing was the bug.
            raise PlanUnavailable(
                "no approach could be chosen from the candidates devise returned",
                kind="unusable", cost_usd=cost,
            )
        await self.memory.record_decision(
            _decision_record(intention, f"chose approach: {approach['summary']}",
                             f"expected value {approach.get('expected_value')} of 3 candidates"),
        )
        try:
            plan, c2 = await self._plan_with_cost(
                intention, approach, strategy_number, plan_attempts=plan_attempts,
                context_prefix=(ws.stable_prefix_text() if ws is not None else ""),
            )
        except PlanUnavailable as exc:
            exc.cost_usd += cost
            raise
        cost += c2
        resume["attempt_id"] = str(
            await self.memory.start_attempt(intention.id, approach["summary"])
        )
        return plan, cost

    async def _devise_with_cost(self, intention: Any, resume: dict[str, Any],
                                knowhow: list[MemoryHit], lessons: list[str], context_prefix: str = "") -> tuple[list[dict[str, Any]], float]:
        """Candidate approaches, or `PlanUnavailable`. Never an invented one.

        This used to swallow its failure and return `[]`, and an empty list was
        then read by `_strategy` as "no candidate was good enough" when it had
        meant "the call never came back". `pick_approach` returned `None`, and
        the fallback above it invented an approach -- "directly pursue: <the
        title>" -- out of the goal's own description. The plan that followed was
        then built on that invention, reported as a chosen approach, and the
        intention went to `in_progress` looking like a goal that had been
        thought about.

        The failure was invisible by construction: it was logged and then
        discarded, so nothing counted it. No attempt was recorded, no backoff was
        scheduled, no degradation was triggered, and the next cycle did exactly
        the same thing. A provider that was down produced an agent that appeared
        to be planning for as long as it was asked, on approaches it had made
        up.

        Both ways of coming back with nothing are now the same thing --
        `PlanUnavailable`, which the caller already knows how to count, back off,
        degrade and park. An empty *successful* answer is in the same class as
        the empty plan `make_plan` already refuses: the model was asked for
        candidates and returned none, and the answer is not to invent one.

        Devise is the first of the two planning calls, so its failures are
        attributed to the `devise` stage and its cost is reported to the caller
        even when the call is the thing that failed, so the budget ledger shows
        what the attempt cost.
        """
        spy = _CostSpy(self.gateway)
        try:
            # The same degradation `make_plan` applies from the same counter.
            # It used to be computed only in the failure handler and reported
            # in the thoughts as "tier dropped" while this call kept asking a
            # consequential goal at T3, so a deployment whose only T3 model was
            # rate-limited retried it until the five-hour rung instead of
            # trying the T2 models that were still answering.
            degraded = self._planning_degraded(resume)
            approaches = await devise(
                spy, self.config, intention, resume.get("understanding", "-"),
                knowhow, lessons,
                tier=(
                    Tier.T2 if degraded or not resume.get("consequential")
                    else Tier.T3
                ),
                knowhow_limit=10, **({"context_prefix": context_prefix} if context_prefix else {}),
            )
        except Exception as exc:
            logger.exception("gsl.devise_failed")
            if is_rate_limited_failure(exc):
                kind = "rate_limited"
            else:
                kind = "no_model" if is_nothing_could_serve(exc) else "unusable"
            raise PlanUnavailable(
                f"devise request failed ({kind}): {exc}", kind=kind,
                retry_after_s=getattr(exc, "retry_after_s", None),
                cost_usd=spy.cost_usd,
            ) from None
        if not approaches:
            logger.warning("gsl.devise_empty", intention=str(intention.id))
            raise PlanUnavailable(
                "devise returned no usable approach; the model was asked for "
                "three candidates and answered with none", kind="unusable",
            )
        return approaches, spy.cost_usd

    async def _plan_with_cost(self, intention: Any, approach: dict[str, Any],
                              strategy_number: int, plan_attempts: int = 0, context_prefix: str = "") -> tuple[Plan, float]:
        """A plan, or `PlanUnavailable`. The two are never the same thing.

        The request itself, its degradation levers, and the no-padding invariant
        live in `planner.make_plan`, which is the one implementation of "ask for
        a plan" this system has -- the loop only accounts for what the call cost
        and lets `PlanUnavailable` carry the diagnosis upward. After
        `plan_attempts_before_degrade` consecutive failures the request is
        *changed* rather than repeated (thinking bounded, tier dropped, schema
        ask relaxed), because re-sending an identical payload with a longer
        sleep is the failure being fixed with more of it.

        The cost of a plan call that fails is attached to the exception rather
        than returned, because the caller learns the cost from the exception's
        way out and would otherwise never learn it at all. A plan call that is
        made, billed and then rejected is still a plan call that was made, and a
        goal that is parked after six of them has spent six plans' worth of its
        budget on the record that it gave up.
        """
        spy = _CostSpy(self.gateway)
        try:
            plan = await make_plan(spy, self.config, intention, approach,
                                   strategy_number, plan_attempts, context_prefix=context_prefix)
        except PlanUnavailable as exc:
            exc.cost_usd += spy.cost_usd
            raise
        return plan, spy.cost_usd

    async def _execute_step(self, intention: IntentionRecord, resume: dict[str, Any],
                           plan: Plan, step: Any, ws: Any) -> SolverResult:
        cost = 0.0
        attempt_index = int((resume.get("step_attempts") or {}).get(step.id, 0))
        # Situation and carried context are what make a step the *next* step
        # rather than the first. They were assembled every cycle by
        # `Life._assemble_workspace` and then dropped here, so a step could only
        # see its own identity and a fresh retrieval scoped to its own text --
        # never the plan it is in, the failure it is retrying after, or the
        # message it is answering.
        situation = ws.volatile_text() if ws is not None else ""
        memories = await self._recall(f"{intention.title} {step.description}", k=6)
        plan_state = [
            {"id": s.id, "description": s.description, "status": s.status}
            for s in plan.steps
        ]
        tool_catalog = await self._tool_catalog()
        request = ModelRequest(
            tier=Tier.T3 if resume.get("consequential") else Tier.T2,
            purpose="gsl.step",
            model_hint=self.config.gsl.model_hint or None,
            budget_bucket=budget_bucket_for(intention),
            reasoning_budget=self.config.gsl.reasoning_budget,
            cache_prefix_blocks=3,
            messages=[
                ModelMessage(role=Role.system, content=ws.stable_prefix_text() if ws is not None else ""),
                ModelMessage(role=Role.system, content=situation),
                ModelMessage(role=Role.system, content=STEP_PROMPT.format(
                    name=self.config.self_name,
                    commissioned=commissioned_note(intention.origin),
                    editing=EDIT_EXISTING_FILES,
                    deliverable=VISIBLE_DELIVERABLE,
                    delegation=delegation_note(intention),
                    title=intention.title,
                    intention_end_state=intention.desired_end_state or "-",
                    constraints=render_constraints(
                        getattr(intention, "constraints", None)
                    ),
                    step_id=step.id,
                    step_description=step.description,
                    plan_state=dumps(plan_state),
                    memories="\n".join(f"- [{m.kind}] {m.summary}" for m in memories) or "(none)",
                    tools=tool_catalog,
                )),
                ModelMessage(role=Role.user, content="Decide the next concrete action."),
            ],
            max_tokens=self.config.gsl.step_tokens,
        )
        try:
            data, cost, truncated = await self._complete_with_cost(request)
            # One repair turn. A small model answering a step prompt sometimes
            # returns prose, or a `thought` with no `actions` array, and the
            # plain reading of that is "no action" -- which fails the step and
            # burns an attempt on what was only a formatting slip. Re-ask once
            # with its own answer quoted back and the schema restated, the way
            # the plan path already repairs itself.
            first = dumps(data)[:1200]
            if (
                not truncated and not self._usable_actions(data)
                and data.get("done") is not True
                and not self.config.gsl.allow_empty_step
            ):
                repair = ModelRequest(
                    tier=request.tier,
                    purpose=request.purpose,
                    budget_bucket=request.budget_bucket,
                    model_hint=request.model_hint,
                    reasoning_budget=request.reasoning_budget,
                    cache_prefix_blocks=request.cache_prefix_blocks,
                    messages=[
                        *request.messages,
                        ModelMessage(role=Role.user, content=(
                            "Your answer was not a usable action for this "
                            "step: it carried no `actions` entries a tool can "
                            f"run. Your answer was: {first}\n\n"
                            "Answer again with strict JSON only — "
                            '{"thought": "...", "actions": [{"tool": "...", '
                            '"args": {...}}], "done": false} — using a tool '
                            "name from the Tools block. Use `fs.write` with "
                            "`path` and `content` to create a new file; use "
                            "`shell.run` with `command` to run something."
                        )),
                    ],
                    max_tokens=request.max_tokens,
                )
                data2, cost2, truncated2 = await self._complete_with_cost(repair)
                cost += cost2
                if data2.get("actions") or data2.get("done") is True:
                    data, truncated = data2, truncated2
        except Exception as exc:
            # A tool that raises is recorded as a tool that failed (below); the
            # model call this step is decided by raised the same way and took the
            # whole cycle with it — out of `_execute_step`, `advance`, `act` and
            # `_cycle`, so nothing was counted, nothing was reflected on, nothing
            # was said, and the identical failing call was retried on every cycle
            # after that for as long as the provider stayed down. Returned as a
            # failed step instead: the error becomes the thought the next cycle
            # reads, and the step's own failure machinery does what it does for
            # any other unsuccessful action.
            logger.exception("gsl.step_call_failed", step=step.id)
            if is_rate_limited_failure(exc):
                # A 429 is time, not a wrong step: wait it out on the same
                # escalation (five minutes, an hour, five hours, a day) instead
                # of re-asking at the tick rate. The gate above reads this
                # timestamp back, so the wait survives restarts and reads on
                # screen as a wait rather than as a stall.
                step_counts = resume.setdefault("step_attempts", {})
                step_failures = int((step_counts or {}).get(step.id, 0)) + 1
                step_counts[step.id] = step_failures
                wait_s = rate_limit_wait_for_attempt(
                    step_failures, getattr(exc, "retry_after_s", None),
                )
                resume[PLAN_RETRY_AFTER] = (
                    datetime.now(UTC) + timedelta(seconds=wait_s)
                ).isoformat()
                await self._save_resume(intention, resume)
                return SolverResult(
                    outcome=SolverOutcome.step_failed,
                    thoughts=[
                        f"rate limited (429); waiting {format_wait(wait_s)} before "
                        f"retrying this step (attempt {step_failures}): "
                        f"{type(exc).__name__}: {exc}"[:300],
                    ],
                    actions=[],
                    plan=plan,
                    cost_usd=cost,
                    failures=[{
                        "kind": "limit_reached", "what": "model_call",
                        "detail": f"rate limited on step {step.id}: "
                                  f"{type(exc).__name__}: {exc}"[:300],
                    }],
                )
            return SolverResult(
                outcome=SolverOutcome.step_failed,
                thoughts=[
                    f"the model call for this step failed before returning: "
                    f"{type(exc).__name__}: {exc}"[:300],
                ],
                actions=[],
                plan=plan,
                cost_usd=cost,
                failures=[{
                    "kind": "error", "what": "model_call",
                    "detail": f"the model call for step {step.id} failed: "
                              f"{type(exc).__name__}: {exc}"[:300],
                }],
            )

        if truncated:
            # Being cut off is the reason this step produced nothing, and it is
            # not the same as a step that needs nothing. `step_tokens` is sized to
            # hold a real file, so reaching the ceiling now means the answer was
            # larger than the work allows -- but either way it is a failure to
            # produce an action, and recording it as a finished step is how an
            # agent could plan a piece of work in full and never begin it.
            thought = str(data.get("thought", ""))[:300]
            note = (
                f"response truncated at {self.config.gsl.step_tokens} tokens; "
                "no action taken"
            )
            logger.warning("gsl.step_truncated", step=step.id,
                           max_tokens=self.config.gsl.step_tokens)
            return SolverResult(
                outcome=SolverOutcome.step_failed,
                thoughts=[note] + ([thought] if thought else []),
                actions=[],
                plan=plan,
                cost_usd=cost,
                failures=[{
                    "kind": "limit_reached", "what": "model_call",
                    "detail": f"the answer to step {step.id} was cut off at "
                              f"{self.config.gsl.step_tokens} tokens",
                }],
            )

        executed: list[dict[str, Any]] = []
        # Structured failures for the stops ledger: everything that raised below
        # lands here as a fact with a kind, so the reason reaches the record
        # without anybody parsing the prose back out of the thoughts.
        step_failures: list[dict[str, Any]] = []
        all_ok = True
        for action in (data.get("actions") or [])[:6]:
            if not isinstance(action, dict) or not action.get("tool"):
                continue
            from ethos.toolhost.server import ToolContext

            ctx = ToolContext(
                thread_id=self.thread_id,
                intention_id=str(intention.id),
                reason=f"step {step.id}: {step.description[:120]}",
                commissioned_by=intention.commissioned_by,
                # The attempt belongs in the key. Replaying the *same* call is
                # what the journal is for, but a retry after a failed attempt
                # is a different action with different intent: with only
                # strategy/step/index in the key, every retry of a step
                # replayed the first attempt's result instead of doing the
                # work again, so the check failed against a file the replayed
                # call never wrote.
                idempotency_key=(
                    f"{intention.id}:{plan.strategy_number}:{step.id}:"
                    f"{attempt_index}:{len(executed)}"
                ),
            )
            # A tool is asked for its opinion on whether it worked, and it
            # answers with `ok=False` and an error. A tool that *raises* is a
            # different event: a bug in the tool layer, a classifier that
            # cannot read its own input, a journal that raised. Letting that
            # escape did not fail the step -- it unwound `advance`, `_advance_goal`,
            # `act` and `_cycle`, so one malformed call took down the whole life
            # loop rather than one action in it. The agent kept the focus it
            # crashed on and re-ran the identical failing step on every cycle
            # after that, indefinitely, producing neither work nor words, which
            # from the outside is indistinguishable from an agent that cannot
            # do the thing it promised.
            #
            # So a raised tool is recorded as a tool that failed, with the error
            # as its result, and the step's own failure machinery does what it
            # does for any other unsuccessful action: count the attempt, reflect,
            # and change strategy if it keeps failing. One bad call is now one
            # bad call.
            try:
                result = await self.toolhost.execute(
                    str(action["tool"]), dict(action.get("args") or {}), ctx,
                )
            except Exception as exc:
                logger.exception(
                    "gsl.tool_raised", tool=str(action["tool"]), step=step.id,
                )
                executed.append({
                    "tool": action["tool"], "args": action.get("args") or {},
                    "ok": False,
                    "result": f"the tool raised before returning: {type(exc).__name__}: {exc}"[:300],
                    "undo_ref": None, "journal_id": None,
                })
                step_failures.append({
                    "kind": "error", "what": f"tool:{action['tool']}",
                    "detail": f"{action['tool']} raised before returning: "
                              f"{type(exc).__name__}: {exc}"[:300],
                })
                all_ok = False
                continue
            executed.append({
                "tool": action["tool"], "args": action.get("args") or {},
                "ok": result.ok, "result": result.content if result.ok else result.error,
                "undo_ref": result.undo_ref, "journal_id": result.journal_id,
            })
            if not result.ok:
                all_ok = False

        # `all_ok` starts true and is only ever cleared by a tool that was called
        # and failed, so a step that called nothing at all also came out "ok" and
        # was marked done. Nothing downstream could tell that from real work: the
        # plan advanced, the step was recorded complete, and no file was ever
        # written. The one case where no action is the right answer is the model
        # saying so explicitly, which is a decision rather than an absence.
        if not executed and not self.config.gsl.allow_empty_step:
            declared_done = data.get("done") is True
            if declared_done:
                thought = str(data.get("thought", ""))[:300]
                return SolverResult(
                    outcome=SolverOutcome.step_done,
                    thoughts=[thought] if thought else [],
                    actions=[],
                    plan=plan,
                    cost_usd=cost,
                )
            thought = str(data.get("thought", ""))[:300]
            note = (
                "model returned no usable action for a step that needed one; "
                "treated as a failure rather than a completed step"
            )
            logger.warning("gsl.step_no_action", step=step.id)
            return SolverResult(
                outcome=SolverOutcome.step_failed,
                thoughts=[note] + ([thought] if thought else []),
                actions=[],
                plan=plan,
                cost_usd=cost,
            )

        if self.context is not None and executed:
            # Close the loop. The outcome of this step is the one thing the next
            # step most needs and previously had no way to see: it was written
            # only into the intention's resume blob, unread by anything.
            try:
                await self.context.append(
                    TurnKind.action.value,
                    f"step {step.id} ({step.description[:80]}) -> "
                    f"{'ok' if all_ok else 'had failures'}",
                    salience=self.context.cfg.action_salience,
                    intention_id=intention.id,
                )
                await self.context.note_actions(
                    executed, intention_id=intention.id, focus=intention.title,
                )
            except Exception:
                logger.warning("gsl.step_context_failed")

        thought = str(data.get("thought", ""))[:300]
        check_result: dict[str, Any] | None = None
        if all_ok and self.config.gsl.verify_step_checks:
            # The tools all reported success, which says the calls went through
            # and not that the step achieved anything. The plan's own check is
            # the only thing here that knows the difference, so it is asked
            # before the step is allowed to be a done step.
            check_result = await self._step_check(step)
            # `observed` is what separates a check that looked and found the
            # work wanting -- a real failure, worth failing the step for --
            # from one that could not look at all: an empty command, a check
            # the loop declined. The second says nothing about the work, and
            # failing on it used to be one step away from a bug here: the
            # unobserved result was dropped entirely rather than returned, so
            # the step passed without its check ever being consulted.
            if (
                check_result is not None
                and not check_result.get("passed", True)
                and check_result.get("observed", False)
            ):
                evidence = check_result.get("evidence") or {}
                detail = str(
                    evidence.get("error") or evidence.get("stderr")
                    or evidence.get("stdout") or evidence.get("errors")
                    or evidence.get("path") or ""
                ).strip()[-600:]
                note = (
                    f"the step's own success check ({step.success_check.get('type')}) "
                    f"did not pass, so this step is not finished"
                )
                logger.warning("gsl.step_check_failed_result", step=step.id,
                               check=step.success_check.get("type"),
                               # The evidence is what turns a run of these
                               # warnings into a diagnosis: a check that failed
                               # with "command not found" is a bad check, and one
                               # that failed with a real diff is a bad step.
                               detail=detail[-200:] or None)
                # The tools all succeeded and the check is what failed. Twice
                # now the pattern has been the check, not the work: a plan
                # invented `~/snake_rl`, expected `~/Downloads/snake_dqn.pth`
                # before any download happened, ran `npm test` on a Python
                # repo. Each of those burned both step attempts, a strategy
                # switch and a fresh plan -- on work that was actually done.
                # So the check itself is repaired first: one rewrite per
                # failure, capped, with the evidence in front of the model.
                repaired = await self._repair_step_check(
                    intention, resume, step, detail, actions=executed,
                )
                if repaired is not None:
                    note = (
                        f"the step's success check did not pass; the check "
                        f"itself was rewritten to {dumps(repaired)[:200]} and "
                        f"the step runs again"
                    )
                    logger.info("gsl.step_check_repaired", step=step.id)
                    return SolverResult(
                        outcome=SolverOutcome.step_failed,
                        thoughts=[note, thought],
                        actions=executed,
                        plan=plan,
                        cost_usd=cost,
                    )
                return SolverResult(
                    outcome=SolverOutcome.step_failed,
                    thoughts=[note, thought] + ([f"check said: {detail}"] if detail else []),
                    actions=executed,
                    plan=plan,
                    cost_usd=cost,
                )

        outcome = SolverOutcome.step_done if all_ok else SolverOutcome.step_failed
        # A step whose own check asked a question and observed nothing is a
        # completed step, but not a *verified* one, and the two used to be
        # indistinguishable in the record: the check was simply not consulted,
        # so a step written off as `step_done` read exactly like one whose test
        # suite had been run and passed. Stating it costs nothing and is the
        # only thing that tells the two apart later.
        thoughts = [thought] if thought else []
        if (
            all_ok
            and self.config.gsl.verify_step_checks
            and check_result is not None
            and not check_result.get("observed", True)
        ):
            why = str(
                (check_result.get("evidence") or {}).get("note")
                or (check_result.get("evidence") or {}).get("error") or ""
            ).strip()[:200]
            thoughts.append(
                f"this step's own success check ({step.success_check.get('type')}) "
                f"could not observe anything, so it is unverified"
                + (f"; it said: {why}" if why else "")
            )
            logger.info("gsl.step_done_unverified", step=step.id,
                        check=str(step.success_check.get("type")))
        return SolverResult(
            outcome=outcome,
            thoughts=thoughts,
            actions=executed,
            plan=plan,
            cost_usd=cost,
            failures=step_failures,
        )

    async def _complete_with_cost(
        self, request: ModelRequest,
    ) -> tuple[dict[str, Any], float, bool]:
        """Parsed JSON, its cost, and whether the model was cut off mid-answer.

        The third value is the one that used to be thrown away. `finish_reason` was
        already captured by every normalizer and read by nobody, so a response
        that stopped because it ran out of room was indistinguishable from a
        complete one -- and an incomplete JSON object does not parse, which is how
        a step that called nothing came to be recorded as a step that succeeded.
        """
        response = await self.gateway.complete(request)
        return (
            parse_json_block(response.text or ""),
            response.cost_usd,
            was_truncated(response.finish_reason),
        )

    @staticmethod
    def _usable_actions(data: dict[str, Any]) -> list[dict[str, Any]]:
        """The actions a step could actually run: dicts that name a tool.

        A model that returns `"actions": ["write the file"]` has returned
        nothing a toolhost can execute, and treating that list as present is
        how a step failed twice on a formatting slip before its repair path
        ever got a turn.
        """
        return [
            a for a in (data.get("actions") or [])
            if isinstance(a, dict) and a.get("tool")
        ]

    async def _repair_step_check(
        self, intention: IntentionRecord, resume: dict[str, Any], step: Any,
        evidence: str, actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Rewrite a failing success check, or `None` when that is not allowed.

        The work succeeded and the check failed, so the thing being tested is
        the plan's idea of done, not the step. The model sees the step, the
        check it wrote, and what the check observed, and answers with one
        replacement check -- same schema, same honesty bar: it may narrow the
        claim, never drop it. Two rewrites per step, tracked in the resume
        blob, because an unbounded repair loop is just a quieter way of
        ignoring a failing step.
        """
        counts = resume.setdefault("check_repairs", {})
        try:
            done = int(counts.get(step.id, 0))
        except (TypeError, ValueError):
            done = 0
        if done >= 2:
            return None
        counts[step.id] = done + 1
        request = ModelRequest(
            tier=Tier.T2,
            purpose="gsl.check_repair",
            budget_bucket=budget_bucket_for(intention),
            model_hint=self.config.gsl.model_hint or None,
            # The solver's other calls bound thinking for exactly this
            # reason: without it a reasoning model spends the whole
            # ceiling thinking and answers with nothing, and the
            # gateway's widening lever has no thinking budget to halve --
            # it can only grow the ceiling and stay on the same model.
            reasoning_budget=self.config.gsl.reasoning_budget,
            messages=[
                ModelMessage(role=Role.system, content=(
                    "A plan step's success_check failed while the step's own "
                    "tool calls all succeeded. Rewrite the check so it "
                    "verifies what the step actually did, on this machine. "
                    "Allowed shapes: "
                    '{"type": "file_exists", "path": "..."}; '
                    '{"type": "file_contains", "path": "...", "text": "..."}; '
                    '{"type": "command", "command": "...", "expect": "exit_zero"}; '
                    '{"type": "syntax", "path": "..."}; '
                    '{"type": "json_field", "path": "...", "field": "...", "expect": "..."}; '
                    '{"type": "value_check", "field": "...", "expect": "..."}. '
                    "A command check must be read-only: no mkdir, no writing "
                    "files, no installs, no clones. Paths are real paths on "
                    "this Mac; when unsure, use one that appeared in the "
                    "evidence. Answer with the JSON check object only."
                )),
                ModelMessage(role=Role.user, content=(
                    f"step: {step.description}\n"
                    f"failing check: {dumps(step.success_check)}\n"
                    f"what the check observed: {evidence[:600] or '(no detail)'}\n"
                    f"tool calls that succeeded for this step:\n{_describe_actions(actions)}\n"
                    "The replacement must verify the same deliverable the step "
                    "describes, using paths and commands that appear above. "
                    "Returning the failing check unchanged is not a repair."
                )),
            ],
            max_tokens=600,
        )
        try:
            data, _c, truncated = await self._complete_with_cost(request)
        except Exception:
            logger.warning("gsl.check_repair_call_failed", step=step.id)
            # A model that never answered is not a repair that was spent:
            # counting it against the two-rewrite budget meant two failed
            # calls exhausted the budget and no check was ever rewritten.
            counts[step.id] = done
            return None
        if truncated or not isinstance(data, dict) or not data.get("type"):
            counts[step.id] = done
            return None
        check = dict(data)
        if check.get("type") not in _RUNNABLE_CHECKS:
            counts[step.id] = done
            return None
        if _same_check(check, step.success_check):
            # The model answered with the check that just failed. Re-running
            # it changes nothing and costs an attempt, so this is not a
            # repair: give the budget back and let the step fail honestly.
            counts[step.id] = done
            return None
        if check.get("type") == "command":
            command = str(check.get("command", ""))
            if not command.strip() or _MUTATING_CHECK.search(command):
                counts[step.id] = done
                return None
        for entry in resume["plan"]["steps"]:
            if entry.get("id") == step.id:
                entry["success_check"] = check
                break
        await self._save_resume(intention, resume)
        return check

    async def _tool_catalog(self) -> str:
        """Where the tool catalog is, which is not in this prompt.

        This used to render a second copy of the catalog into `STEP_PROMPT`, one
        list entry per tool and no parameters at all — so a step was shown the full
        registry twice, once properly in the cached stable prefix and once here,
        uncached and worse, after the cache breakpoint. The agent could act on either.

        It is a pointer now. The rendered catalog is the second block of the stable
        prefix this same request already carries, with every parameter, its type and
        whether it is required, so there is nothing for a second copy to add; and a
        tool registered after this request was built cannot appear in it either way.
        The only case that ever reached here without one is the thread worker, which
        passes its own line and executes nothing.
        """
        return (
            "In the `## Tools` block of my context above: every tool, its parameters, "
            "their types and which are required. Call only names and arguments listed there."
        )

    async def _unlock_gate_children(
        self, intention: IntentionRecord, resume: dict[str, Any],
        gate_policy: dict[str, Any],
    ) -> list[Any]:
        """Create the verifies + synthesizes children once output verifies.

        Idempotent on `gate_children_created`: the verifies child is created
        first, and the synthesizes child only exists once verification passed,
        so a re-entered finish does not duplicate the pair.
        """
        if not gate_policy or resume.get("gate_children_created"):
            return []
        specs = gate_children(str(intention.title), gate_policy)
        if not specs:
            return []
        created: list[Any] = []
        previous_id = None
        for spec in specs:
            try:
                child_id = await self.memory.create_intention(
                    title=spec["title"],
                    desired_end_state=spec["desired_end_state"],
                    kind="task",
                    parent_id=intention.id,
                    origin="gate",
                    priority=0.7,
                )
                await self.memory.add_intention_edge(
                    intention.id, child_id, spec["edge_type"], spec.get("gate_policy") or {},
                )
                if previous_id is not None:
                    await self.memory.add_intention_edge(previous_id, child_id, "depends_on")
                previous_id = child_id
                created.append(child_id)
            except Exception:
                logger.exception("gsl.gate_child_failed", intention=str(intention.id))
        if created:
            resume["gate_children_created"] = True
            await self._save_resume(intention, resume)
        return created

    async def _maybe_decompose(
        self, intention: IntentionRecord, resume: dict[str, Any],
    ) -> list[Any]:
        """Decompose a complex goal into child intentions on first planning."""
        if resume.get("decomposed"):
            return []
        if not needs_decomposition(intention, difficulty=resume.get("difficulty")):
            return []
        try:
            children = await decompose_and_persist(
                self.memory, intention, {"summary": str(intention.title), "expected_value": 0.8},
            )
        except Exception:
            logger.exception("gsl.decompose_failed", intention=str(intention.id))
            return []
        if children:
            resume["decomposed"] = True
            await self._save_resume(intention, resume)
        return children

    async def _finish_goal(self, intention: IntentionRecord, resume: dict[str, Any],
                           plan: Plan) -> tuple[SolverResult, float]:
        cost = 0.0
        artifact = self._artifact(resume, plan)
        changed = changed_paths(resume.get("artifacts") or [])
        verification = await self.verification.verify(
            intention=intention,
            criteria=list(intention.success_criteria or []),
            artifact=artifact,
            evidence={"plan": plan.model_dump(mode="json")},
            producer_family=None,
            cwd=user_home(),
            changed_paths=changed,
        )
        if verification.human_pending:
            await self.memory.update_intention(
                intention.id, status=IntentionStatus.awaiting_review.value,
            )
            # A rung-3 review is a stage somebody is owed an answer about: this
            # program could not confirm the work meets the goal, and the thing
            # outstanding is a person. It is not "finished" -- calling it that is
            # what told somebody a build was done when only its source files
            # existed. Not announced -- the sentence is not this loop's to
            # write -- but owed, so the record carries who, why, and where the
            # files actually are.
            detail = "; ".join(verification.issues or [])[:300] or (
                "the work could not be verified and is waiting on a person to look at it"
            )
            await self._notice_stage(
                intention, IntentionStatus.awaiting_review.value,
                (detail + _delivered_note(resume))[:800],
            )
            return SolverResult(
                outcome=SolverOutcome.awaiting_review, verification=verification,
                thoughts=["awaiting human review of commissioned criteria"],
                cost_usd=cost,
            ), cost
        if verification.passed:
            gate_policy = resume.get("gate_policy") or (intention.constraints or {}).get("gate_policy") or {}
            gate_block = gate_blocks_completion(verification, gate_policy)
            if gate_block is not None:
                verification.passed = False
                verification.issues.append(gate_block)
                for issue in [gate_block][:3]:
                    await self.memory.record_episode(
                        kind="verification_failed", summary=issue[:300],
                        intention_id=intention.id, importance=0.7,
                    )
                await self.memory.update_intention(
                    intention.id, status=IntentionStatus.in_progress.value,
                )
                return SolverResult(
                    outcome=SolverOutcome.step_failed, verification=verification,
                    thoughts=[gate_block],
                    cost_usd=cost,
                ), cost
            created_gate_children = await self._unlock_gate_children(
                intention, resume, gate_policy,
            )
            if created_gate_children:
                return SolverResult(
                    outcome=SolverOutcome.step_done, verification=verification,
                    thoughts=[
                        f"gate opened: {len(created_gate_children)} verification/synthesis "
                        f"child(ren) created for {intention.title}",
                    ],
                    cost_usd=cost,
                ), cost
            await self._close_attempt(intention, resume, "succeeded", cost)
            await self.memory.close_intention(intention.id, IntentionStatus.done.value,
                                              "verification passed")
            await self.memory.record_episode(
                kind="goal_done", summary=f"completed: {intention.title}",
                intention_id=intention.id, importance=0.8,
            )
            # The completion is a fact about the work, and it is already
            # recorded as one: the intention is closed, and the episode log has
            # it. What used to also go out was a line composed here — "Done:
            # <title>. <end state>" — written to the person who commissioned
            # the work, with no deliberation behind it and no words of the
            # agent's own in it. That is the agent speaking in a voice it was
            # handed rather than one it chose, and it arrived through the
            # ordinary reply path, so it was indistinguishable in the
            # transcript from a reply the agent had actually thought about.
            # Nothing is announced; the record is there to be asked about.
            #
            # The debt is a separate thing from the sentence, and dropping the
            # sentence did not drop it. Removing the canned line left nothing behind
            # to say a person who asked to be told had been told, and nothing to
            # give the loop a reason to speak -- so a verified goal ended in a
            # table row and the person waited. The notice carries no words: it
            # records that the work reached `done`, who is owed the saying of it,
            # and that nobody has said it yet.
            await self._notice_stage(
                intention, IntentionStatus.done.value,
                f"verified at rung {verification.rung_reached} on "
                f"{verification.objective.checks_run if verification.objective else 0} check(s)"
                + _delivered_note(resume),
            )
            ran = (verification.objective.checks_run if verification.objective else 0)
            return SolverResult(
                outcome=SolverOutcome.goal_done, verification=verification,
                thoughts=[
                    f"goal verified at rung {verification.rung_reached} "
                    f"on {ran} check(s): {intention.title}",
                ],
                cost_usd=cost,
            ), cost
        await self.memory.update_intention(intention.id, status=IntentionStatus.in_progress.value)
        # Why it did not pass, in the thoughts. A verification that fails and
        # replans without saying so leaves the next cycle with a strategy number
        # and no reason, and the loop's own state machine will then try three
        # strategies at a goal whose real problem was a test that fails.
        for issue in (verification.issues or ["verification did not pass"])[:3]:
            await self.memory.record_episode(
                kind="verification_failed", summary=issue[:300],
                intention_id=intention.id, importance=0.6,
            )
        strategy_number = int(resume.get("strategy_number", 1)) + 1
        max_strategies = self._max_strategies()
        if max_strategies > 0 and strategy_number > max_strategies:
            await self._park(intention, f"verification failed after all strategies: "
                                        f"{'; '.join(verification.issues[:2])[:200]}")
            return SolverResult(
                outcome=SolverOutcome.parked, verification=verification, cost_usd=cost,
                thoughts=["parked: verification failed"],
            ), cost
        waiting = self._planning_waiting(resume)
        if waiting > 0:
            return SolverResult(
                outcome=SolverOutcome.step_failed, verification=verification, plan=plan,
                thoughts=[f"verification failed; replanning is waiting out a backoff of "
                          f"about {round(waiting)}s"],
                cost_usd=cost,
            ), cost
        try:
            new_plan, c = await self._strategy(
                intention, resume, strategy_number,
                plan_attempts=self._plan_attempts(resume),
            )
        except PlanUnavailable as exc:
            # The plan that failed verification is kept, so the work and the reason
            # it was rejected both survive into the next cycle -- and if this is
            # what finally parks the intention, the record of why is in the same
            # place as the record of what was built.
            failed = await self._planning_failed(
                intention, resume, exc, plan=plan, stage="replan",
            )
            failed.verification = verification
            failed.cost_usd += cost + exc.cost_usd
            return failed, cost + exc.cost_usd
        cost += c
        await self._install_plan(intention, resume, new_plan, strategy_number=strategy_number)
        await self._pace_strategy_switch(intention, resume, strategy_number)
        return SolverResult(
            outcome=SolverOutcome.strategy_switch, plan=new_plan, verification=verification,
            thoughts=[f"verification failed; switching to strategy #{strategy_number}"],
            cost_usd=cost,
        ), cost

    async def _pace_strategy_switch(
        self, intention: IntentionRecord, resume: dict[str, Any], strategy_number: int,
    ) -> None:
        """When strategies are unbounded, the switch schedules its own wait.

        A ceiling used to be the only thing standing between a hopeless step
        and an infinite ladder of fresh approaches. With no ceiling, this is
        the replacement: every installed strategy carries a backoff that
        grows with its number and caps at `plan_backoff_max_s`, so a goal
        the model cannot solve is retried every few minutes rather than
        every tick -- an unbounded *number* of attempts, never an
        unbounded *rate* of them. The wait rides `PLAN_RETRY_AFTER`, which
        the step gate already knows how to read and name.
        """
        if self._max_strategies() > 0:
            return
        wait_s = self._plan_backoff_s(min(max(1, strategy_number), 8))
        resume[PLAN_RETRY_AFTER] = (
            datetime.now(UTC) + timedelta(seconds=wait_s)
        ).isoformat()
        await self._save_resume(intention, resume)

    def _artifact(self, resume: dict[str, Any], plan: Plan) -> dict[str, Any]:
        """What the work actually is, for the rung that judges it.

        It used to be a list of step descriptions and the last few tool results.
        That is a description *of* the work, and a judge handed a description
        rules on the description: the sentence "s1: write the deliverable file"
        and a result string ending in `ok: true` read as a finished piece of
        software to every verifier this has run, and none of them could see the
        code that had been written or a single test that had been run.

        So the code goes in. The `fs.edit` response already carries a diff of
        exactly what it changed and `fs.write` carries what it replaced, so the
        artifact is the diff rather than a description of one, and the changed
        paths are named. What a judge is asked is the same question a person is
        asking when they say "did it do what you said it would do", and it is
        now answerable from the record rather than from the promise.
        """
        actions = [a for a in (resume.get("artifacts") or []) if isinstance(a, dict)]
        diffs: list[dict[str, Any]] = []
        edited: list[str] = []
        for action in actions:
            tool = str(action.get("tool") or "")
            result = action.get("result")
            if tool not in ("fs.edit", "fs.write") or not isinstance(result, dict):
                continue
            diff = result.get("diff_preview") or result.get("replaced_diff_preview")
            if not diff:
                continue
            path = str((action.get("args") or {}).get("path") or "")
            if path:
                edited.append(path)
            diffs.append({"path": path, "tool": tool, "diff": str(diff)[:4000]})
        return {
            "steps": [
                {"id": s.id, "description": s.description, "result": s.result}
                for s in plan.steps
            ],
            # Kept whole rather than sliced to eight: the slice kept the most
            # recent actions, which for a multi-step change is the last file
            # touched and never the first.
            "changed_files": sorted({
                str(p) for p in changed_paths(actions)
            }),
            "diffs": diffs[-6:],
            "last_results": actions[-8:],
        }

    async def _step_check(self, step: Any) -> dict[str, Any] | None:
        """Run the success check the plan itself wrote for this step.

        Every plan has carried a `success_check` per step since the schema
        existed, and the loop has never run one. The plan prompt asks for it in
        every step, the field is parsed, stored in the resume blob, shown to
        nobody and checked by nothing -- so a step that set the whole test suite
        red was recorded as a completed step, and the failure surfaced several
        steps later, attributed to whatever step happened to touch the code
        next.

        Running it here is the difference between a change that breaks something
        and the next step knowing it did. The failure carries the check's own
        output into the thoughts, so the model is told what failed rather than
        only that something did.

        Three things are deliberately distinguished here, because collapsing them
        is how a check stops being evidence:

        * No check was asked for (`none`, `human`, absent). Nothing to say, so
          nothing is said -- the caller treats this as "no verdict", not as
          "unverified".
        * A check was asked for and the checker could not observe anything --
          an empty command, a non-numeric metric target. The result is
          *returned*, with the checker's own note, rather than dropped. It used
          to be dropped, and dropping it is what let a step be recorded as
          verified on the strength of a check that had never looked at anything.
          The caller turns `observed: False` into an explicit "this step was not
          objectively verified" on the result, so the record says so rather than
          being silent about it -- and never into a failed step, because a check
          that could not run has said nothing about the work either way.
        * A check that raised. Also returned as an absence, with the exception
          logged: a malformed check in a model's plan is not a reason to throw
          away a step whose work is real.

        The kinds of check this loop will *run* are the deterministic ones, and
        the list is a policy rather than an omission. `_RUNNABLE_CHECKS` holds
        it. `ObjectiveChecker` also implements `http_status` and `metric_gte`,
        and running them here was tried: a plan-invented URL against a port
        nothing listens on fails honestly (`observed: True`) and fails the step,
        which sends a plan that is otherwise working into three strategies of
        guessing at an environment the step never controlled. So those kinds are
        declined -- recorded as unverified rather than run -- and the record says
        that a check was asked for and the loop declined it, which is the part
        that used to be silent: the old allowlist skipped them without a word,
        so a plan declaring a metric threshold looked exactly as verified as one
        whose tests had actually been run.
        """
        check = getattr(step, "success_check", None)
        if not isinstance(check, dict) or not check:
            return None
        kind = str(check.get("type", "none"))
        if kind in ("none", "human", ""):
            # Nothing was asked for. Distinct from asking and getting nothing.
            return None
        if kind not in _RUNNABLE_CHECKS:
            logger.info("gsl.step_check_declined", step=step.id, check=kind)
            return {
                "passed": True, "observed": False,
                "evidence": {
                    "note": f"the loop does not run {kind!r} checks, so this "
                            "step was not objectively verified",
                },
            }
        if kind == "command":
            command = str(check.get("command", ""))
            if _MUTATING_CHECK.search(command):
                logger.warning("gsl.step_check_mutates", step=step.id)
                return {
                    "passed": False, "observed": True,
                    "evidence": {
                        "command": command[:600],
                        "error": (
                            "the success check performs the work instead of "
                            "verifying it; put the write/mkdir/install in the "
                            "step's action and make the check read-only"
                        ),
                    },
                }
        try:
            result = await ObjectiveChecker(user_home()).check(check)
        except Exception:
            logger.exception("gsl.step_check_failed", step=step.id)
            return None
        if not result.get("observed"):
            logger.warning(
                "gsl.step_check_unobserved", step=step.id, check=kind,
                note=str((result.get("evidence") or {}).get("note")
                         or (result.get("evidence") or {}).get("error") or "")[:200],
            )
        return result

    async def _reflect(self, intention: IntentionRecord, step: Any, result: SolverResult,
                      failures: int) -> dict[str, Any]:
        try:
            request = ModelRequest(
                tier=Tier.T2,
                purpose="gsl.reflect",
                model_hint=self.config.gsl.model_hint or None,
                reasoning_budget=self.config.gsl.reasoning_budget,
                budget_bucket=budget_bucket_for(intention),
                messages=[
                    ModelMessage(role=Role.system, content=REFLECT_PROMPT.format(
                        name=self.config.self_name,
                        outcome="failed",
                        title=intention.title,
                        step_description=step.description,
                        evidence=dumps(result.actions)[:3000],
                        failures=failures,
                    )),
                    ModelMessage(role=Role.user, content="Extract lessons."),
                ],
                max_tokens=self.config.gsl.reflect_tokens,
            )
            data, _, _truncated = await self._complete_with_cost(request)
            return data
        except Exception:
            logger.exception("gsl.reflect_failed")
            return {"lessons": [], "knowhow": [], "open_questions": []}

    async def _park(self, intention: IntentionRecord, reason: str,
                      resume: dict[str, Any] | None = None) -> None:
        """Close the intention as parked, and say why in three places.

        `closed_reason` is the row. `resume_state` is what the loop and the
        deliberation read back, and it is the only one of the three that carries
        the diagnosis rather than a sentence: a parked goal whose record says
        "parked" and nothing else cannot be told apart from a goal the agent
        quietly lost interest in, which is the difference between a blockage
        worth reporting and one worth ignoring.

        The caller passes its `resume` when it has one, because writing the
        intention's loaded copy back instead would undo the save that records the
        failure that caused the parking -- the diagnosis would replace the
        evidence for it, and the row would say only that something stopped.

        The open question is the third. It is what the agent will be drawn to
        next, and a question carrying the reason is a question it can actually
        answer rather than one that only records that there was a question.
        """
        state = resume if resume is not None else dict(intention.resume_state or {})
        state["parked_reason"] = reason[:500]
        # When the parking happened, for the reopen sweep: parked is a pause
        # with a reason, not a verdict on the goal, and the sweep in the life
        # loop needs to know how long the pause has been running to decide
        # when the work is owed another chance.
        state["parked_at_epoch"] = time.time()
        await self.memory.update_intention(intention.id, resume_state=state)
        await self.memory.close_intention(intention.id, IntentionStatus.parked.value, reason)
        await self.memory.open_question(
            f"How should I approach '{intention.title}' next time? ({reason})", priority=0.7,
        )
        await self._notice_stage(
            intention, IntentionStatus.parked.value,
            (reason + _delivered_note(state))[:800],
        )

    async def _notice_stage(
        self, intention: IntentionRecord, stage: str, detail: str = "",
    ) -> None:
        """Write down that commissioned work reached a stage its commissioner is owed.

        The row is the only thing in this system that knows both of the facts a
        person needs: that the work stopped where it did, and that they have not
        been told. `closed_reason` holds the first and `stage_notices` the second,
        and they were not joined anywhere -- which is why a task that had finished
        for hours was invisible to the reply path, and why nothing ever gave the
        agent a reason to open the conversation and say so.

        So this is called at each of the three places work stops: verified `done`,
        `parked`, and `awaiting_review`. It is deliberately not the place a
        sentence is composed. Everything this program sends to a person is a word
        the model chose, so what is written here is a debt and its subject; the
        words are composed downstream, where every other reply is composed.

        Failures are logged and stepped over rather than raised: a notice that
        could not be written must not be the reason a goal cannot be closed.
        """
        if stage not in STAGE_NOTIFIABLE:
            return
        try:
            notice = await self.memory.record_stage_notice(intention, stage, detail)
        except Exception:
            logger.warning("gsl.stage_notice_failed", intention=str(intention.id), stage=stage)
            return
        if notice is not None:
            logger.info(
                "gsl.stage_reached", intention=str(intention.id), stage=stage,
                person=notice.person_id, delivered=notice.delivered_at is not None,
            )

    async def _save_resume(self, intention: IntentionRecord, resume: dict[str, Any]) -> None:
        bookmark = Bookmark(
            step_id=resume.get("next_planned_step"),
            approach_id=(resume.get("plan") or {}).get("approach_id"),
            last_action=resume.get("last_action"),
            next_planned_step=resume.get("next_planned_step"),
            mental_context=resume.get("understanding", "")[:400],
            partial_results={"artifacts": (resume.get("artifacts") or [])[-8:]},
        )
        resume["bookmarked_at"] = bookmark.bookmarked_at.isoformat()
        await self.memory.update_intention(intention.id, resume_state=resume)


class _CostSpy:
    """A gateway that remembers what its calls cost, so the loop can account.

    The planner functions answer with data and raise on failure; the loop is the
    part that reports spend, and it wraps the gateway rather than asking the
    planner to return a second value nobody else wants. What a call cost is on
    the response, so this is arithmetic at one boundary instead of a cost
    parameter threaded through every planner signature.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self.cost_usd = 0.0

    async def complete(self, request: ModelRequest) -> Any:
        response = await self._gateway.complete(request)
        self.cost_usd += getattr(response, "cost_usd", 0.0)
        return response


def _step_from(data: dict[str, Any]) -> Any:
    from ethos.schemas.gsl import PlanStep

    return PlanStep(
        id=str(data.get("id", "s1")),
        description=str(data.get("description", "")),
        tool_hints=list(data.get("tool_hints") or []),
        success_check=dict(data.get("success_check") or {}),
        reversible=bool(data.get("reversible", True)),
        status=str(data.get("status", "pending")),
        attempts=int(data.get("attempts", 0)),
        result=data.get("result"),
    )


def _decision_record(intention: IntentionRecord, statement: str, rationale: str) -> Any:
    from ethos.schemas.gsl import DecisionRecord

    return DecisionRecord(
        intention_id=intention.id, statement=statement, rationale=rationale,
        decided_by="gsl",
    )
