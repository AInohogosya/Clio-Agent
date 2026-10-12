from __future__ import annotations

from typing import Any

from ethos.config import EthosConfig
from ethos.gsl.verification import run_model_verifier
from ethos.observability.logger import get_logger
from ethos.prompts import (
    COMMISSIONED_ALREADY,
    DEVISE_PROMPT,
    EDIT_EXISTING_FILES,
    PLAN_PROMPT,
    STEP_PROMPT,
    dumps,
)
from ethos.schemas.memory import MemoryHit
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.threads.worker")

# Roles that are whole other programs rather than frames of this one. They
# spawn through the same ThreadManager — same budget carve, same bus results,
# same intentions — but their worker dispatches to the sub-agent registry, and
# the program's own model calls are routed through this agent's gateway.
SUBAGENT_ROLES = ("foundry", "apex")


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
    try:
        result = await agent.run(goal, workdir=str(workdir))
    except Exception as exc:
        logger.exception("thread.subagent_failed", role=role)
        result_dict: dict[str, Any] = {
            "agent": role, "status": "failed", "summary": "sub-agent crashed",
            "error": str(exc), "duration_s": round(time.monotonic() - started, 1),
        }
    else:
        result_dict = result.to_dict()
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
    from ethos.gsl.planner import parse_json_block, pick_approach

    knowhow_text = "\n".join(f"- [{m.kind}] {m.summary}" for m in memories[:10]) or "(none)"
    try:
        devise_response = await gateway.complete(ModelRequest(
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
        ))
        cost += devise_response.cost_usd
        data = parse_json_block(devise_response.text or "")
        approach = pick_approach([
            {"id": a.get("id", f"a{i}"), "summary": a.get("summary", ""),
             "expected_value": a.get("expected_value", 0.5),
             "estimated_cost_usd": a.get("estimated_cost_usd", 0.05),
             "reversible": a.get("reversible", True)}
            for i, a in enumerate(data.get("approaches", [])[:3]) if isinstance(a, dict)
        ]) or {"id": "a1", "summary": goal}
        thoughts.append(f"approach: {approach.get('summary', '')[:150]}")

        plan_response = await gateway.complete(ModelRequest(
            tier=Tier.T2,
            purpose="thread.plan",
            thread_id=str(spec.get("id")),
            messages=[
                ModelMessage(role=Role.system, content=PLAN_PROMPT.format(
                    name=config.self_name, approach=approach.get("summary", ""),
                    commissioned=COMMISSIONED_ALREADY,
                    title=goal, desired_end_state=goal,
                )),
                ModelMessage(role=Role.user, content="Produce the plan JSON."),
            ],
            max_tokens=1200,
        ))
        cost += plan_response.cost_usd
        plan_data = parse_json_block(plan_response.text or "")

        for i, step in enumerate(plan_data.get("steps", [])[:5]):
            if not isinstance(step, dict) or not step.get("description"):
                continue
            step_response = await gateway.complete(ModelRequest(
                tier=Tier.T2,
                purpose="thread.step",
                thread_id=str(spec.get("id")),
                messages=[
                    ModelMessage(role=Role.system, content=STEP_PROMPT.format(
                        name=config.self_name, commissioned=COMMISSIONED_ALREADY,
                        editing=EDIT_EXISTING_FILES, title=goal,
                        step_id=step.get("id", f"s{i+1}"),
                        step_description=step.get("description", ""),
                        plan_state=dumps(plan_data.get("steps", [])[:5]),
                        memories=knowhow_text,
                        tools="(same tool catalog as the Self)",
                    )),
                    ModelMessage(role=Role.user, content="Decide the next concrete action."),
                ],
                max_tokens=1500,
            ))
            cost += step_response.cost_usd
            step_data_out = parse_json_block(step_response.text or "")
            thought = step_data_out.get("thought", "")
            if thought:
                thoughts.append(str(thought)[:200])
    except Exception as exc:
        logger.exception("thread.pursue_failed")
        return {"status": "failed", "goal": goal, "error": str(exc), "cost_usd": cost}

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
