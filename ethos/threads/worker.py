from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from ethos.config import EthosConfig
from ethos.engine.rhythm import backoff_seconds
from ethos.gateway.service import retry_after_of
from ethos.gsl.planner import is_nothing_could_serve, is_rate_limited_failure
from ethos.gsl.verification import run_model_verifier
from ethos.observability.logger import get_logger
from ethos.parse import parse_json_block
from ethos.prompts import (
    COMMISSIONED_ALREADY,
    DEVISE_PROMPT,
    EDIT_EXISTING_FILES,
    PLAN_PROMPT,
    STEP_PROMPT,
    SUCCESS_CHECKS_MATTER,
    VISIBLE_DELIVERABLE,
    delegation_note,
    dumps,
    render_constraints,
    render_success_criteria,
)
from ethos.schemas.memory import MemoryHit
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.threads.worker")

# Roles that are whole other programs rather than frames of this one. They
# spawn through the same ThreadManager — same budget carve, same bus results,
# same intentions — but their worker dispatches to the sub-agent registry, and
# the program's own model calls are routed through this agent's gateway.
SUBAGENT_ROLES = ("foundry", "apex", "axiom")

# A failure that reaches this worker has already been through the gateway's
# own retry curve — failover, request widening, cooldown waits. The counts
# here are about the gaps the gateway cannot see: a 429 that outlasted its
# schedule, a provider that blinked between retries. They come from
# configuration (`threads.call_attempts`, `threads.subagent_attempts`) and
# zero — the default — means there is no ceiling: the work is retried on
# the backoffs below for as long as the thread runs, because a task stops
# only when the user asks it to or the goal itself cannot be met.
RATE_LIMIT_BASE_S = 30.0
RATE_LIMIT_MAX_S = 300.0
TRANSIENT_BASE_S = 5.0
TRANSIENT_MAX_S = 60.0


def _attempt_cap(config: Any, attr: str) -> int:
    """The configured retry ceiling for `attr`, or 0 when there is none."""
    threads = getattr(config, "threads", None)
    return max(0, int(getattr(threads, attr, 0) or 0))


class ThreadAnswerUnusable(Exception):
    """The model answered, and the answer is not the shape the caller asked for.

    Raised only after the repair turn has also failed, so a formatting slip
    costs one re-ask and not the task."""


async def _complete(gateway: Any, request: Any, config: Any = None) -> Any:
    """One model call, retried for as long as the configuration allows.

    The blanket `except` this replaces caught the first failure and ended the
    task: a single 429 or a dropped connection was the whole pursuit. The
    classification is the planner's, so the two failure kinds with different
    answers are told apart — a rate limit is time and is waited out on a
    bounded curve (honouring a `Retry-After` the provider named), while any
    other failure backs off doubling to sixty seconds.

    With no ceiling configured (`threads.call_attempts: 0`, the default) the
    loop runs for as long as the thread does — the pacing is the backoff, not
    a count. One failure kind still ends the task on the spot, unchanged:
    `is_nothing_could_serve` means no model on this deployment could ever
    answer — a dead key, an empty catalogue — and waiting cannot fix a
    missing engine. That is not the task being given up on: the envelope
    fails, the manager parks the goal, and the reopen sweep in the life loop
    brings it back for another attempt on its own cadence, so a key fixed
    an hour later is a goal that finishes an hour later.
    """
    cap = _attempt_cap(config, "call_attempts")
    attempt = 0
    last: BaseException | None = None
    while True:
        attempt += 1
        try:
            return await gateway.complete(request)
        except Exception as exc:
            last = exc
            if is_nothing_could_serve(exc):
                break
            if cap > 0 and attempt >= cap:
                break
            if is_rate_limited_failure(exc):
                asked = retry_after_of(exc)
                wait = (
                    min(asked, RATE_LIMIT_MAX_S) if asked is not None
                    else min(RATE_LIMIT_BASE_S * (2 ** (attempt - 1)), RATE_LIMIT_MAX_S)
                )
            else:
                wait = backoff_seconds(attempt - 1, base_s=TRANSIENT_BASE_S, max_s=TRANSIENT_MAX_S)
            logger.warning(
                "thread.call_retry", purpose=request.purpose, attempt=attempt,
                wait_s=round(wait, 1), error=f"{type(exc).__name__}: {exc}"[:200],
            )
            await asyncio.sleep(wait)
    assert last is not None
    raise last


async def _complete_json(gateway: Any, request: Any, config: Any = None, *,
                          label: str, schema_hint: str) -> tuple[dict[str, Any], Any]:
    """A model answer that must be a JSON object, with one repair turn.

    `parse_json_block` returns `{}` for prose, and the plain reading of an
    empty dict downstream is "the plan had no steps" — a formatting slip read
    as a fact about the work. The repair re-asks once with the model's own
    answer quoted back and the schema restated, the way the solver loop
    repairs a step answer.
    """
    response = await _complete(gateway, request, config)
    data = parse_json_block(response.text or "")
    if data:
        return data, response
    first = (response.text or "")[:1200]
    repair = ModelRequest(
        tier=request.tier,
        purpose=request.purpose,
        thread_id=request.thread_id,
        messages=[
            *request.messages,
            ModelMessage(role=Role.user, content=(
                "Your answer was not a usable JSON object. Your answer was: "
                f"{first}\n\nAnswer again with strict JSON only — {schema_hint} "
                "— with no prose around it."
            )),
        ],
        max_tokens=request.max_tokens,
    )
    response2 = await _complete(gateway, repair, config)
    data2 = parse_json_block(response2.text or "")
    if data2:
        return data2, response2
    raise ThreadAnswerUnusable(
        f"{label}: the model returned no usable JSON twice (first answer: {first[:200]})"
    )


async def run_thread(spec: dict[str, Any], config: EthosConfig, memory: Any, gateway: Any = None) -> Any:
    """A Thread is the same framework in a different frame: it gets a goal,
    a framing sentence, an isolated workdir, and the same memory + identity.
    A sub-agent role is the one exception: the frame belongs to another
    program, reached through the sub-agent registry."""
    from ethos.threads.context import setup_worktree

    thread_id = str(spec["id"])
    workdir = setup_worktree(config, thread_id)
    role = str(spec.get("role", "delegate"))

    if role == "verifier" and spec.get("verify_payload"):
        return await _run_verifier(spec, config, gateway)

    if role in SUBAGENT_ROLES:
        return await _run_subagent(role, spec, config, memory, gateway, workdir)

    goal = str(spec.get("goal", ""))
    intention_id = spec.get("intention_id")
    if not intention_id:
        return {"status": "nothing-to-do", "goal": goal}

    from uuid import UUID

    intention = await memory.get_intention(UUID(str(intention_id)))
    if intention is None:
        return {"status": "missing-intention", "goal": goal}

    memories = await memory.search(goal, k=8)
    summary = await _pursue(goal, intention, memories, spec, config, memory, gateway, workdir)
    return summary


async def _run_subagent(
    role: str,
    spec: dict[str, Any],
    config: EthosConfig,
    memory: Any,
    gateway: Any,
    workdir: Any,
) -> dict[str, Any]:
    """A sub-agent thread: the goal goes to another program, the bookkeeping
    stays here. The result is the program's outcome in this agent's own
    vocabulary, recorded as an episode against the thread's intention."""
    import time
    from uuid import UUID

    from ethos.subagents import SubagentRegistry

    started = time.monotonic()
    goal = str(spec.get("goal", ""))
    registry = SubagentRegistry(config, gateway=gateway)
    agent = registry.get(role)
    if agent is None:
        reason = registry.describe() or ["no sub-agents are enabled"]
        return {"status": "unavailable", "goal": goal, "error": f"sub-agent {role!r} unavailable",
                "available": reason}
    result: Any = None
    result_dict: dict[str, Any] | None = None
    # A sub-agent crash can be a deterministic property of the goal, which is
    # why the count used to be two. It is configuration now
    # (`threads.subagent_attempts`) and zero -- the default -- means the run
    # is retried for as long as the thread lives, with the short gap below
    # between attempts: enough for a port to open or a worktree to settle,
    # and, when the crash really is the goal, the pacing that keeps the
    # retries from being a hot loop. A positive number restores the old
    # honest failure after that many attempts.
    cap = _attempt_cap(config, "subagent_attempts")
    attempt = 0
    while True:
        attempt += 1
        try:
            result = await agent.run(goal, workdir=str(workdir))
            break
        except Exception as exc:
            if cap > 0 and attempt >= cap:
                logger.exception("thread.subagent_failed", role=role)
                result_dict = {
                    "agent": role, "status": "failed",
                    "summary": f"sub-agent crashed (attempt {attempt})",
                    "error": str(exc),
                    "duration_s": round(time.monotonic() - started, 1),
                }
                break
            logger.warning(
                "thread.subagent_retry", role=role, attempt=attempt,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )
            await asyncio.sleep(backoff_seconds(0, base_s=10.0, max_s=30.0))
    if result is not None:
        result_dict = result.to_dict()
    assert result_dict is not None
    result_dict["goal"] = goal
    try:
        await memory.record_episode(
            kind="thread_result",
            summary=f"sub-agent {role}: {result_dict.get('summary', '')[:200]}",
            body={"agent": role, "status": result_dict.get("status"),
                  "mode": result_dict.get("mode"), "workdir": str(workdir)},
            intention_id=UUID(str(spec["intention_id"])) if spec.get("intention_id") else None,
            thread_id=str(spec.get("id")),
            importance=0.6,
        )
    except Exception:
        logger.exception("thread.subagent_episode_failed")
    return result_dict


async def _run_verifier(spec: dict[str, Any], config: EthosConfig, gateway: Any = None) -> dict[str, Any]:
    """Verifier threads see goal + artifact only — never producer reasoning.

    The model call itself is the ladder's (`gsl.verification.run_model_verifier`),
    which is where the diversity hint, the prompt and the refusal-to-guess
    default are decided once.
    """
    payload = spec.get("verify_payload") or {}
    if gateway is None:
        return {"verdict": "needs_human", "issues": ["no gateway available in verifier thread"],
                "confidence": 0.0}
    return await run_model_verifier(
        gateway, config,
        goal=str(payload.get("goal", "")),
        criteria=list(payload.get("criteria", [])),
        # The ladder already sliced the artifact once; this second bound is what
        # keeps a hand-built payload from carrying more than a judge can read.
        artifact_text=str(payload.get("artifact", ""))[:6000],
        producer_family=payload.get("producer_family"),
        purpose="thread.verify",
        thread_id=str(spec.get("id")),
    )


async def _pursue(
    goal: str,
    intention: Any,
    memories: list[MemoryHit],
    spec: dict[str, Any],
    config: EthosConfig,
    memory: Any,
    gateway: Any,
    workdir: Any,
) -> dict[str, Any]:
    import time

    started = time.monotonic()
    thoughts: list[str] = []
    cost = 0.0
    if gateway is None:
        return {"status": "no-gateway", "goal": goal, "thoughts": ["gateway unavailable"]}
    from ethos.gsl.planner import pick_approach

    knowhow_text = "\n".join(f"- [{m.kind}] {m.summary}" for m in memories[:10]) or "(none)"
    # `delegation_note` reads the work off an intention's fields, and this
    # worker has a goal string rather than an intention row — so the routing
    # detection reads the same fields off the shape it is given. Without it the
    # two plan call sites drift: one teaches delegation and the other does not,
    # and a thread is exactly where a named sub-agent's work lands.
    goal_as_intention = SimpleNamespace(
        title=goal, desired_end_state=goal, constraints=None,
    )

    # Which phase the pursuit is in, so a failure names it. A single outer
    # `except` reads the phase off nothing and reports every failure as the
    # pursuit as a whole; the stage is the difference between "the task
    # failed" and "the plan call failed", and the reason a person can act on.
    stage = "pursue"

    async def _fail(exc: BaseException) -> dict[str, Any]:
        logger.exception("thread.pursue_failed", stage=stage)
        return {
            "status": "failed", "goal": goal, "stage": stage,
            "error": f"{type(exc).__name__}: {exc}"[:400], "cost_usd": cost,
            "thoughts": thoughts[:10],
            "duration_s": round(time.monotonic() - started, 1),
        }

    try:
        stage = "devise"
        data, devise_response = await _complete_json(
            gateway, ModelRequest(
                tier=Tier.T2,
                purpose="thread.devise",
                thread_id=str(spec.get("id")),
                messages=[
                    ModelMessage(role=Role.system, content=DEVISE_PROMPT.format(
                        name=config.self_name, title=goal, desired_end_state=goal,
                        understanding=goal, knowhow=knowhow_text, lessons="(none)",
                    )),
                    ModelMessage(role=Role.user, content="Devise 3 approaches."),
                ],
                max_tokens=1200,
            ),
            config,
            label="devise",
            schema_hint='{"approaches": [{"id": "a1", "summary": "...", '
                        '"expected_value": 0.5, "estimated_cost_usd": 0.05, '
                        '"reversible": true}]}',
        )
        cost += devise_response.cost_usd
        approach = pick_approach([
            {"id": a.get("id", f"a{i}"), "summary": a.get("summary", ""),
             "expected_value": a.get("expected_value", 0.5),
             "estimated_cost_usd": a.get("estimated_cost_usd", 0.05),
             "reversible": a.get("reversible", True)}
            for i, a in enumerate(data.get("approaches", [])[:3]) if isinstance(a, dict)
        ]) or {"id": "a1", "summary": goal}
        thoughts.append(f"approach: {approach.get('summary', '')[:150]}")

        stage = "plan"
        plan_data, plan_response = await _complete_json(
            gateway, ModelRequest(
                tier=Tier.T2,
                purpose="thread.plan",
                thread_id=str(spec.get("id")),
                messages=[
                    ModelMessage(role=Role.system, content=PLAN_PROMPT.format(
                        name=config.self_name, approach=approach.get("summary", ""),
                        commissioned=COMMISSIONED_ALREADY,
                        success_checks=SUCCESS_CHECKS_MATTER,
                        deliverable=VISIBLE_DELIVERABLE,
                        delegation=delegation_note(goal_as_intention),
                        title=goal, desired_end_state=goal,
                        constraints=render_constraints(
                            getattr(goal_as_intention, "constraints", None)
                        ),
                        success_criteria=render_success_criteria(
                            getattr(goal_as_intention, "success_criteria", None)
                        ),
                    )),
                    ModelMessage(role=Role.user, content="Produce the plan JSON."),
                ],
                max_tokens=1200,
            ),
            config,
            label="plan",
            schema_hint='{"steps": [{"id": "s1", "description": "one concrete action"}]}',
        )
        cost += plan_response.cost_usd

        steps = [
            s for s in plan_data.get("steps", [])[:5]
            if isinstance(s, dict) and s.get("description")
        ]
        if not steps:
            raise ThreadAnswerUnusable(
                "plan: the plan carried no usable steps after the repair turn"
            )

        for i, step in enumerate(steps):
            step_id = str(step.get("id", f"s{i+1}"))
            stage = f"step {step_id}"
            step_messages = [
                ModelMessage(role=Role.system, content=STEP_PROMPT.format(
                    name=config.self_name, commissioned=COMMISSIONED_ALREADY,
                    editing=EDIT_EXISTING_FILES,
                    deliverable=VISIBLE_DELIVERABLE,
                    delegation=delegation_note(goal_as_intention),
                    title=goal,
                    intention_end_state=getattr(
                        goal_as_intention, "desired_end_state", ""
                    ) or "-",
                    constraints=render_constraints(
                        getattr(goal_as_intention, "constraints", None)
                    ),
                    step_id=step_id,
                    step_description=step.get("description", ""),
                    plan_state=dumps(plan_data.get("steps", [])[:5]),
                    memories=knowhow_text,
                    tools="(same tool catalog as the Self)",
                )),
                ModelMessage(role=Role.user, content="Decide the next concrete action."),
            ]
            step_response = await _complete(gateway, ModelRequest(
                tier=Tier.T2,
                purpose="thread.step",
                thread_id=str(spec.get("id")),
                messages=step_messages,
                max_tokens=1500,
            ), config)
            cost += step_response.cost_usd
            step_data_out = parse_json_block(step_response.text or "")
            if not step_data_out:
                # The step's own repair turn, before the step is given up on:
                # the same quoted-back re-ask the devise and plan calls get.
                step_response = await _complete(gateway, ModelRequest(
                    tier=Tier.T2,
                    purpose="thread.step",
                    thread_id=str(spec.get("id")),
                    messages=[
                        *step_messages,
                        ModelMessage(role=Role.user, content=(
                            "Your answer was not a usable JSON object. Your answer was: "
                            f"{(step_response.text or '')[:1200]}\n\nAnswer again with "
                            'strict JSON only — {"thought": "one line", "actions": '
                            '[{"tool": "...", "args": {...}}], "done": false} — with '
                            "no prose around it."
                        )),
                    ],
                    max_tokens=1500,
                ), config)
                cost += step_response.cost_usd
                step_data_out = parse_json_block(step_response.text or "")
                if not step_data_out:
                    return await _fail(
                        ThreadAnswerUnusable(
                            "the model returned no usable JSON twice for this step"
                        ),
                    )
            thought = step_data_out.get("thought", "")
            if thought:
                thoughts.append(str(thought)[:200])
    except Exception as exc:
        return await _fail(exc)

    await memory.record_episode(
        kind="thread_result",
        summary=f"thread pursued: {goal[:200]}",
        body={"thoughts": thoughts, "workdir": str(workdir)},
        intention_id=intention.id if intention else None,
        thread_id=str(spec.get("id")),
        importance=0.5,
    )
    return {
        "status": "done",
        "goal": goal,
        "thoughts": thoughts[:10],
        "cost_usd": round(cost, 6),
        "duration_s": round(time.monotonic() - started, 1),
        "workdir": str(workdir),
    }
