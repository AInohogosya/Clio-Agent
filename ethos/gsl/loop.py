from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ethos.config import EthosConfig
from ethos.engine.rhythm import backoff_seconds
from ethos.gateway.normalizer import was_truncated
from ethos.gsl.acceptance import changed_paths
from ethos.gsl.evaluator import ObjectiveChecker
from ethos.gsl.planner import PlanUnavailable, parse_json_block, pick_approach, understand
from ethos.gsl.verification import VerificationLadder
from ethos.observability.logger import get_logger
from ethos.paths import user_home
from ethos.prompts import (
    COMMISSIONED_ALREADY,
    DEGRADED_PLAN,
    EDIT_EXISTING_FILES,
    PLAN_PROMPT,
    REFLECT_PROMPT,
    STEP_PROMPT,
    SUCCESS_CHECKS_MATTER,
    dumps,
)
from ethos.schemas.context import TurnKind
from ethos.schemas.gsl import (
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

MAX_STRATEGIES = 3
STEP_FAILURES_BEFORE_SWITCH = 2

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
        if self.rhythm is not None:
            return float(self.rhythm.backoff_seconds(
                attempts, base_s=cfg.plan_backoff_s, max_s=cfg.plan_backoff_max_s,
                jitter=cfg.plan_backoff_jitter,
            ))
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
        park_at = max(1, self.config.gsl.plan_attempts_before_park)
        reason = f"{attempts} consecutive planning failures ({stage}): {exc}"

        if attempts >= park_at:
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
        degraded = self._planning_degraded(resume)
        resume[PLAN_DEGRADED] = degraded
        resume[PLAN_RETRY_AFTER] = (
            (datetime.now(UTC) + timedelta(seconds=wait_s)).isoformat() if wait_s > 0 else None
        )
        await self._save_resume(intention, resume)
        thoughts = [f"could not {stage} this goal (attempt {attempts}): {exc}"]
        if degraded:
            thoughts.append(
                f"the request has been degraded after {attempts} failures: thinking "
                "bounded, tier dropped, schema relaxed"
            )
        if wait_s > 0:
            thoughts.append(f"asking again in about {round(wait_s)}s rather than immediately")
        logger.warning(
            "gsl.plan_unavailable", intention=str(intention.id), attempts=attempts,
            stage=stage, kind=exc.kind, degraded=degraded,
            retry_in_s=round(wait_s, 2), reason=str(exc)[:300],
        )
        return SolverResult(
            outcome=SolverOutcome.step_failed, plan=plan, thoughts=thoughts,
        )

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
            memories = await self._recall(intention.title, k=10)
            data, c = await self._call(understand, intention, memories)
            cost += c
            resume.update(data)
            await self._save_resume(intention, resume)
            for question in resume.get("questions", [])[:3]:
                await self.memory.open_question(question, priority=0.4)
            return SolverResult(
                outcome=SolverOutcome.step_done,
                thoughts=[f"understood goal: {resume.get('understanding', '')[:200]}"],
                cost_usd=cost,
            )

        if "plan" not in resume:
            # The backoff is checked before the call, not after it. A goal whose
            # plan has failed three times has earned an interval, and spending a
            # model call to find out that the interval has not elapsed is the
            # exact behaviour the interval exists to stop.
            waiting = self._planning_waiting(resume)
            if waiting > 0:
                return SolverResult(
                    outcome=SolverOutcome.step_failed,
                    thoughts=[
                        f"planning is backing off; the last attempt failed and the next one "
                        f"is due in about {round(waiting)}s",
                        f"last failure: {(resume.get(PLAN_LAST_FAILURE) or {}).get('reason', 'unknown')}",
                    ],
                    cost_usd=cost,
                )
            try:
                plan, c = await self._strategy(
                    intention, resume,
                    strategy_number=int(resume.get("strategy_number", 1)),
                    plan_attempts=self._plan_attempts(resume),
                )
            except PlanUnavailable as exc:
                # No plan means no work, so this is a failure of the step rather
                # than an accomplishment of it. Reported as one so the intention
                # stays open and the next cycle asks again -- the alternative was
                # installing a fabricated plan and reporting the goal as planned.
                # Bounded, though: "the next cycle asks again" used to be the end
                # of the sentence, and it is now the first of three states.
                failed = await self._planning_failed(intention, resume, exc, stage="plan")
                failed.cost_usd += cost
                return failed
            cost += c
            resume["plan"] = plan.model_dump(mode="json")
            resume["step_attempts"] = {}
            resume["strategy_number"] = int(plan.strategy_number)
            # The count is about consecutive failures, and there is a plan again.
            resume[PLAN_ATTEMPTS] = 0
            resume[PLAN_RETRY_AFTER] = None
            resume.pop(PLAN_LAST_FAILURE, None)
            await self._save_resume(intention, resume)
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
            if strategy_number > MAX_STRATEGIES:
                await self._park(intention, f"{MAX_STRATEGIES} strategies failed on step {step.id}")
                return SolverResult(outcome=SolverOutcome.parked, cost_usd=cost,
                                    thoughts=[f"parked after {MAX_STRATEGIES} strategies"])
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
                    plan_attempts=self._plan_attempts(resume),
                )
            except PlanUnavailable as exc:
                # The existing plan stays in place. Replacing it with nothing, or
                # with invented steps, would discard the work already understood.
                failed = await self._planning_failed(
                    intention, resume, exc, plan=plan, stage="replan",
                )
                failed.cost_usd += cost
                return failed
            cost += c
            resume["plan"] = new_plan.model_dump(mode="json")
            resume["step_attempts"] = {}
            resume["strategy_number"] = strategy_number
            resume[PLAN_ATTEMPTS] = 0
            resume[PLAN_RETRY_AFTER] = None
            resume.pop(PLAN_LAST_FAILURE, None)
            await self._save_resume(intention, resume)
            return SolverResult(
                outcome=SolverOutcome.strategy_switch, plan=new_plan, cost_usd=cost,
                thoughts=[f"strategy switch #{strategy_number} after repeated failures"],
            )

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
        for kh in lessons_result.get("knowhow", [])[:2]:
            if isinstance(kh, dict) and kh.get("situation") and kh.get("advice"):
                await self.memory.record_knowhow(
                    situation=str(kh["situation"]), advice=str(kh["advice"]),
                    evidence={"intention_id": str(intention.id), "step": step.id},
                )
        for question in lessons_result.get("open_questions", [])[:2]:
            await self.memory.open_question(str(question), priority=0.6)
        return step_result

    async def _call(self, fn: Any, intention: IntentionRecord, memories: list[MemoryHit]) -> tuple[dict[str, Any], float]:
        data = await fn(self.gateway, self.config, intention, memories)
        return data, 0.0

    async def _strategy(self, intention: IntentionRecord, resume: dict[str, Any],
                        strategy_number: int, plan_attempts: int = 0) -> tuple[Plan, float]:
        cost = 0.0
        knowhow = await self.memory.find_knowhow(intention.title, k=5)
        attempts = await self.memory.list_attempts(intention.id, limit=5)
        lessons = [lesson for a in attempts for lesson in a.lessons]
        approaches, c1 = await self._devise_with_cost(intention, resume, knowhow, lessons)
        cost += c1
        approach = pick_approach(approaches)
        if approach is None:
            approach = {
                "id": f"approach-{strategy_number}",
                "summary": f"directly pursue: {intention.desired_end_state or intention.title}",
                "expected_value": 0.5, "estimated_cost_usd": 0.1, "reversible": True,
            }
        await self.memory.record_decision(
            _decision_record(intention, f"chose approach: {approach['summary']}",
                             f"expected value {approach.get('expected_value')} of 3 candidates"),
        )
        plan, c2 = await self._plan_with_cost(
            intention, approach, strategy_number, plan_attempts=plan_attempts,
        )
        cost += c2
        await self.memory.start_attempt(intention.id, approach["summary"])
        return plan, cost

    async def _devise_with_cost(self, intention: Any, resume: dict[str, Any],
                                knowhow: list[MemoryHit], lessons: list[str]) -> tuple[list[dict[str, Any]], float]:
        response = None
        try:
            from ethos.prompts import DEVISE_PROMPT

            request = ModelRequest(
                tier=Tier.T3 if resume.get("consequential") else Tier.T2,
                purpose="gsl.devise",
                budget_bucket="commitment" if intention.commissioned_by else "discretionary",
                messages=[
                    ModelMessage(role=Role.system, content=DEVISE_PROMPT.format(
                        name=self.config.self_name,
                        title=intention.title,
                        desired_end_state=intention.desired_end_state or "-",
                        understanding=resume.get("understanding", "-"),
                        knowhow="\n".join(f"- [{h.kind}] {h.summary}" for h in knowhow[:10]) or "(none)",
                        lessons="\n".join(f"- {lesson}" for lesson in lessons) or "(none)",
                    )),
                    ModelMessage(role=Role.user, content="Devise 3 approaches."),
                ],
                max_tokens=self.config.gsl.devise_tokens,
            )
            response = await self.gateway.complete(request)
            data = parse_json_block(response.text or "")
            approaches = [
                {
                    "id": str(a.get("id", f"a{i+1}")),
                    "summary": str(a.get("summary", ""))[:500],
                    "expected_value": max(0.0, min(1.0, float(a.get("expected_value", 0.5)))),
                    "estimated_cost_usd": max(0.0, float(a.get("estimated_cost_usd", 0.05))),
                    "reversible": bool(a.get("reversible", True)),
                }
                for i, a in enumerate(data.get("approaches", [])[:3])
                if isinstance(a, dict)
            ]
            return approaches[:3], response.cost_usd
        except Exception:
            logger.exception("gsl.devise_failed")
            return [], getattr(response, "cost_usd", 0.0)

    async def _plan_with_cost(self, intention: Any, approach: dict[str, Any],
                              strategy_number: int, plan_attempts: int = 0) -> tuple[Plan, float]:
        """A plan, or `PlanUnavailable`. The two are never the same thing.

        After `plan_attempts_before_degrade` consecutive failures the request is
        *changed* rather than repeated. Three levers, all of which attack the ways
        this call actually fails rather than the failure in general:

        * thinking is bounded by `reasoning_budget`. On the endpoints that bill
          reasoning inside `max_tokens`, a plan call is a bet on how long the
          model thinks first, and the shape that bet loses is the one seen here
          most often: `finish_reason: length`, thousands of characters of
          reasoning, no plan. Bounding it does not make the model reason less
          well; it stops the reasoning from eating the room the answer was for.
        * the tier is dropped to T1. The plan call asks for a list of steps, which
          is not the hardest thing this system asks a model for, and T1 is served
          by a wider set of entries than T2. A request that could not be placed at
          all cannot be made better by any change to its wording.
        * the schema ask is relaxed. `DEGRADED_PLAN` asks for the shortest honest
          plan and says plainly that a success check is optional. Every step that
          carries one is a step the model has to think a check out for before it
          can write the step down, and those are the tokens that do not fit.

        None of this weakens the no-padding invariant below, which is the thing
        that turns a failed call into work that looks real. A relaxed plan is
        still only the steps the model actually returned.
        """
        degraded = plan_attempts >= max(1, self.config.gsl.plan_attempts_before_degrade)
        try:
            request = ModelRequest(
                tier=Tier.T1 if degraded else Tier.T2,
                purpose="gsl.plan",
                budget_bucket="commitment" if intention.commissioned_by else "discretionary",
                reasoning_budget=self.config.gsl.degraded_reasoning_budget if degraded else None,
                messages=[
                    ModelMessage(role=Role.system, content=(
                        PLAN_PROMPT.format(
                            name=self.config.self_name,
                            commissioned=COMMISSIONED_ALREADY,
                            success_checks=SUCCESS_CHECKS_MATTER,
                            approach=approach.get("summary", ""),
                            title=intention.title,
                            desired_end_state=intention.desired_end_state or "-",
                        ) + (DEGRADED_PLAN if degraded else "")
                    )),
                    ModelMessage(role=Role.user, content="Produce the plan JSON."),
                ],
                max_tokens=self.config.gsl.plan_tokens,
            )
            response = await self.gateway.complete(request)
            if was_truncated(response.finish_reason):
                raise PlanUnavailable(
                    "plan response was cut off at "
                    f"{self.config.gsl.plan_tokens} tokens"
                )
            data = parse_json_block(response.text or "")
            steps = []
            for i, s in enumerate(data.get("steps", [])[:7]):
                if not isinstance(s, dict):
                    continue
                description = str(s.get("description", "")).strip()
                if not description:
                    continue
                steps.append({
                    "id": str(s.get("id", f"s{i+1}")),
                    "description": description[:1000],
                    "tool_hints": [str(t) for t in s.get("tool_hints", [])][:5],
                    "success_check": s.get("success_check") or {},
                    "reversible": bool(s.get("reversible", True)),
                    "status": "pending",
                    "attempts": 0,
                    "result": None,
                })
            # No padding. What the model returned is the plan; a short one is
            # short, and inventing the remainder from the approach summary is how
            # a goal ended up with three identical steps naming no tool and
            # carrying no success check -- a plan that looked real and could not
            # be worked. Failing here is recoverable: the step is retried under a
            # bounded, backing-off, eventually-parked state machine, and a
            # truncated call fails loudly instead of becoming a fake plan.
            if len(steps) < self.config.gsl.min_real_plan_steps:
                raise PlanUnavailable(
                    f"model returned {len(steps)} usable plan step(s); "
                    f"at least {self.config.gsl.min_real_plan_steps} required"
                )
            plan = Plan(
                approach_id=str(approach.get("id", f"approach-{strategy_number}")),
                approach=str(approach.get("summary", "")),
                steps=[_step_from(s) for s in steps[:7]],
                strategy_number=strategy_number,
            )
            return plan, response.cost_usd
        except PlanUnavailable:
            raise
        except Exception as exc:
            logger.exception("gsl.plan_failed")
            # "Nothing could serve this request" and "the answer was not a plan"
            # are different problems, and the state machine above treats them
            # differently: relaxing a request that no model could serve cannot
            # help, so the reason has to say which happened. It is not special
            # treatment for a dead key -- it is the reason a caller can tell a
            # deployment that needs its key fixed from a model being awkward.
            kind = "no_model" if _is_nothing_could_serve(exc) else "unusable"
            raise PlanUnavailable(
                f"plan request failed ({kind}): {exc}" if kind != "unusable" else
                "plan request failed; no plan to execute",
                kind=kind,
            ) from None

    async def _execute_step(self, intention: IntentionRecord, resume: dict[str, Any],
                           plan: Plan, step: Any, ws: Any) -> SolverResult:
        cost = 0.0
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
            budget_bucket="commitment" if intention.commissioned_by else "discretionary",
            cache_prefix_blocks=3,
            messages=[
                ModelMessage(role=Role.system, content=ws.stable_prefix_text() if ws is not None else ""),
                ModelMessage(role=Role.system, content=situation),
                ModelMessage(role=Role.system, content=STEP_PROMPT.format(
                    name=self.config.self_name,
                    commissioned=COMMISSIONED_ALREADY,
                    editing=EDIT_EXISTING_FILES,
                    title=intention.title,
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
        data, cost, truncated = await self._complete_with_cost(request)

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
            )

        executed: list[dict[str, Any]] = []
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
                idempotency_key=f"{intention.id}:{plan.strategy_number}:{step.id}:{len(executed)}",
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
        if all_ok and self.config.gsl.verify_step_checks:
            # The tools all reported success, which says the calls went through
            # and not that the step achieved anything. The plan's own check is
            # the only thing here that knows the difference, so it is asked
            # before the step is allowed to be a done step.
            check_result = await self._step_check(step)
            if check_result is not None and not check_result.get("passed", True):
                evidence = check_result.get("evidence") or {}
                detail = str(
                    evidence.get("stderr") or evidence.get("stdout")
                    or evidence.get("errors") or evidence.get("path") or ""
                ).strip()[-600:]
                note = (
                    f"the step's own success check ({step.success_check.get('type')}) "
                    f"did not pass, so this step is not finished"
                )
                logger.warning("gsl.step_check_failed_result", step=step.id,
                               check=step.success_check.get("type"))
                return SolverResult(
                    outcome=SolverOutcome.step_failed,
                    thoughts=[note, thought] + ([f"check said: {detail}"] if detail else []),
                    actions=executed,
                    plan=plan,
                    cost_usd=cost,
                )

        outcome = SolverOutcome.step_done if all_ok else SolverOutcome.step_failed
        return SolverResult(
            outcome=outcome,
            thoughts=[thought] if thought else [],
            actions=executed,
            plan=plan,
            cost_usd=cost,
        )

    async def _complete_with_cost(self, request: ModelRequest) -> tuple[dict[str, Any], float, bool]:
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
            return SolverResult(
                outcome=SolverOutcome.awaiting_review, verification=verification,
                thoughts=["awaiting human review of commissioned criteria"],
                cost_usd=cost,
            ), cost
        if verification.passed:
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
        if strategy_number > MAX_STRATEGIES:
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
            return failed, cost
        cost += c
        resume["plan"] = new_plan.model_dump(mode="json")
        resume["strategy_number"] = strategy_number
        resume["step_attempts"] = {}
        resume[PLAN_ATTEMPTS] = 0
        resume[PLAN_RETRY_AFTER] = None
        resume.pop(PLAN_LAST_FAILURE, None)
        await self._save_resume(intention, resume)
        return SolverResult(
            outcome=SolverOutcome.strategy_switch, plan=new_plan, verification=verification,
            thoughts=[f"verification failed; switching to strategy #{strategy_number}"],
            cost_usd=cost,
        ), cost

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

        A check that raises is a check that could not be run, and is returned as
        nothing: the step's work is real either way, and a malformed check in a
        model's plan is not a reason to throw away a completed step.
        """
        check = getattr(step, "success_check", None)
        if not isinstance(check, dict) or not check:
            return None
        if str(check.get("type", "none")) not in (
            "command", "file_exists", "file_contains", "syntax", "json_field",
        ):
            return None
        try:
            result = await ObjectiveChecker(user_home()).check(check)
        except Exception:
            logger.exception("gsl.step_check_failed", step=step.id)
            return None
        if not result.get("observed"):
            return None
        return result

    async def _reflect(self, intention: IntentionRecord, step: Any, result: SolverResult,
                      failures: int) -> dict[str, Any]:
        try:
            request = ModelRequest(
                tier=Tier.T2,
                purpose="gsl.reflect",
                budget_bucket="commitment" if intention.commissioned_by else "discretionary",
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
        await self.memory.update_intention(intention.id, resume_state=state)
        await self.memory.close_intention(intention.id, IntentionStatus.parked.value, reason)
        await self.memory.open_question(
            f"How should I approach '{intention.title}' next time? ({reason})", priority=0.7,
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


def _is_nothing_could_serve(exc: BaseException) -> bool:
    """Whether the failure was the deployment rather than the request.

    One question with a narrow answer, asked so that the planner's failure can be
    named rather than lumped together. The gateway raises `NoModelAvailable` when
    nothing in the catalogue could serve the request -- every candidate excluded,
    every provider key rejected, every quota spent -- and it has already waited
    out the cooldown and spent the retry budget by the time it does. A different
    wording of the same request cannot produce a different answer, so reporting
    this as "the model returned an unusable plan" would send the state machine
    down the degradation path for a problem that has no lever.

    Checked by class rather than by message because the message is a string in a
    module that does not own it.
    """
    from ethos.gateway.router import NoModelAvailable

    if isinstance(exc, NoModelAvailable):
        return True
    # `BudgetExceededError` is a spend guard rather than a routing fact, and it
    # does not clear by asking a different question, so it belongs on this side of
    # the line too: no plan is available and the reason is not the plan.
    from ethos.gateway.cost import BudgetExceededError

    return isinstance(exc, BudgetExceededError)


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
