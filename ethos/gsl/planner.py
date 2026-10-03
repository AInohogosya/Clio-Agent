from __future__ import annotations

import json
from typing import Any

from ethos.config import EthosConfig
from ethos.gateway.normalizer import was_truncated
from ethos.observability.logger import get_logger
from ethos.prompts import (
    COMMISSIONED_ALREADY,
    DEGRADED_PLAN,
    DEVISE_PROMPT,
    PLAN_PROMPT,
    SUCCESS_CHECKS_MATTER,
    UNDERSTAND_PROMPT,
    dumps,
)
from ethos.schemas.gsl import Plan, PlanStep
from ethos.schemas.memory import MemoryHit
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.gsl.planner")


class PlanUnavailable(RuntimeError):
    """The model did not return a usable plan, so there is nothing to execute.

    Raised instead of returning a plan anyway. The old padding invented steps
    from the approach summary to reach a minimum count, which turned a failed
    call into three content-free steps that read exactly like real ones: no tool
    named, no success check, and three copies of the same sentence. An agent
    given that plan cannot do the work and cannot tell it was never told how.

    Defined here rather than in `loop.py` because the planner is what raises it,
    and the loop is what counts it. Keeping it in the loop made the other half of
    the system -- `make_plan`, exported and callable -- unable to say the same
    thing, so the one function outside the solver loop could still invent a plan.

    `kind` is what happened, because the two are not the same problem and the
    loop's state machine above counts them differently:

    * `unusable` -- a model answered and the answer was not a plan: empty steps,
      or a reply the provider stopped mid-answer. The request asked for more
      than the ceiling allowed, or the model is being awkward. A different
      request fixes this, which is what degradation is.
    * `no_model` -- nothing could serve the request at all: a dead key, an
      exhausted quota, no eligible entry for the tier. Nothing about the request
      is wrong, so relaxing it cannot help, and the honest answer is to say so
      rather than to keep re-asking in a different voice.
    """

    def __init__(self, message: str, kind: str = "unusable"):
        super().__init__(message)
        self.kind = kind


def parse_json_block(text: str) -> dict[str, Any]:
    if not text:
        return {}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        try:
            import re

            fixed = re.sub(r",\s*([}\]])", r"\1", text[start:end + 1])
            return json.loads(fixed)
        except json.JSONDecodeError:
            return {}


def _memories_text(hits: list[MemoryHit]) -> str:
    return "\n".join(f"- [{h.kind}] {h.summary}" for h in hits[:15]) or "(none)"


async def _complete_json(gateway: Any, request: ModelRequest) -> dict[str, Any]:
    response = await gateway.complete(request)
    return parse_json_block(response.text or "")


async def understand(
    gateway: Any, config: EthosConfig, intention: Any, memories: list[MemoryHit],
) -> dict[str, Any]:
    """Phase 1: understand the goal. Consequential goals run at T3 [D-06]."""
    text_hint = json.dumps({
        "title": intention.title,
        "kind": intention.kind,
        "commissioned": intention.commissioned_by is not None,
        "constraints": intention.constraints,
    })
    consequential_hint = intention.commissioned_by is not None or intention.kind in ("venture", "commitment")
    tier = Tier.T3 if consequential_hint else Tier.T2
    data = await _complete_json(gateway, ModelRequest(
        tier=tier,
        purpose="gsl.understand",
        budget_bucket="commitment" if intention.commissioned_by else "discretionary",
        consequence_class="consequential" if consequential_hint else "routine",
        messages=[
            ModelMessage(role=Role.system, content=UNDERSTAND_PROMPT.format(
                name=config.self_name,
                title=intention.title,
                desired_end_state=intention.desired_end_state or "-",
                criteria=dumps(intention.success_criteria or []),
                constraints=dumps(intention.constraints or {}),
                origin=intention.origin,
                memories=_memories_text(memories),
            )),
            ModelMessage(role=Role.user, content=text_hint),
        ],
        max_tokens=config.gsl.understand_tokens,
    ))
    difficulty = _clip(float(data.get("difficulty", 0.5)), 0.0, 1.0)
    consequential = bool(data.get("consequential", consequential_hint)) or consequential_hint
    return {
        "understanding": str(data.get("understanding", ""))[:2000],
        "unknowns": [str(u) for u in data.get("unknowns", [])][:10],
        "difficulty": difficulty,
        "consequential": consequential,
        "questions": [str(q) for q in data.get("questions", [])][:5],
    }


async def devise(
    gateway: Any, config: EthosConfig, intention: Any, understanding: str,
    knowhow: list[MemoryHit], lessons: list[str],
) -> list[dict[str, Any]]:
    """Phase 2: exactly 3 candidate approaches [6.1]."""
    data = await _complete_json(gateway, ModelRequest(
        tier=Tier.T2,
        purpose="gsl.devise",
        budget_bucket="commitment" if intention.commissioned_by else "discretionary",
        messages=[
            ModelMessage(role=Role.system, content=DEVISE_PROMPT.format(
                name=config.self_name,
                title=intention.title,
                desired_end_state=intention.desired_end_state or "-",
                understanding=understanding or "-",
                knowhow=_memories_text(knowhow),
                lessons="\n".join(f"- {lesson}" for lesson in lessons) or "(none)",
            )),
            ModelMessage(role=Role.user, content="Devise 3 approaches."),
        ],
        max_tokens=config.gsl.devise_tokens,
    ))
    approaches = [
        {
            "id": str(a.get("id", f"a{i+1}")),
            "summary": str(a.get("summary", ""))[:500],
            "expected_value": _clip(float(a.get("expected_value", 0.5)), 0.0, 1.0),
            "estimated_cost_usd": max(0.0, float(a.get("estimated_cost_usd", 0.05))),
            "reversible": bool(a.get("reversible", True)),
        }
        for i, a in enumerate(data.get("approaches", [])[:3])
        if isinstance(a, dict)
    ]
    return approaches[:3]


def pick_approach(approaches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not approaches:
        return None
    return max(
        approaches,
        key=lambda a: a.get("expected_value", 0.5) - 0.05 * a.get("estimated_cost_usd", 0.05),
    )


async def make_plan(
    gateway: Any, config: EthosConfig, intention: Any, approach: dict[str, Any],
    strategy_number: int = 1, plan_attempts: int = 0,
) -> Plan:
    """Phase 3: a short-horizon plan, and `PlanUnavailable` when there isn't one.

    No padding, and that is the invariant this function exists to hold from the
    outside the solver loop as well as inside it. It used to top a short answer
    up to three steps with copies of the approach summary, so a call that
    returned nothing at all produced a plan with no tool named, no success check
    and three identical sentences -- indistinguishable, to everything downstream,
    from a plan that could actually be worked.

    After `plan_attempts_before_degrade` failures the request is made under
    reduced demands, exactly as `GeneralSolverLoop._plan_with_cost` does it: the
    tier drops, thinking is bounded, and the schema ask is relaxed. What it never
    does is lower the bar for what counts as steps, so a degraded request that
    returns nothing still fails.
    """
    degraded = plan_attempts >= max(1, config.gsl.plan_attempts_before_degrade)
    request = ModelRequest(
        tier=Tier.T1 if degraded else Tier.T2,
        purpose="gsl.plan",
        budget_bucket="commitment" if intention.commissioned_by else "discretionary",
        reasoning_budget=config.gsl.degraded_reasoning_budget if degraded else None,
        messages=[
            ModelMessage(role=Role.system, content=PLAN_PROMPT.format(
                name=config.self_name,
                commissioned=COMMISSIONED_ALREADY,
                success_checks=SUCCESS_CHECKS_MATTER,
                approach=approach.get("summary", ""),
                title=intention.title,
                desired_end_state=intention.desired_end_state or "-",
            ) + (DEGRADED_PLAN if degraded else "")),
            ModelMessage(role=Role.user, content="Produce the plan JSON."),
        ],
        max_tokens=config.gsl.plan_tokens,
    )
    response = await gateway.complete(request)
    if was_truncated(response.finish_reason):
        raise PlanUnavailable(
            f"plan response was cut off at {config.gsl.plan_tokens} tokens"
        )
    data = parse_json_block(response.text or "")
    raw_steps = data.get("steps", [])[:7]
    steps: list[PlanStep] = []
    for i, s in enumerate(raw_steps):
        if not isinstance(s, dict):
            continue
        description = str(s.get("description", "")).strip()
        if not description:
            continue
        steps.append(PlanStep(
            id=str(s.get("id", f"s{i+1}")),
            description=description[:1000],
            tool_hints=[str(t) for t in s.get("tool_hints", [])][:5],
            success_check=s.get("success_check") or {},
            reversible=bool(s.get("reversible", True)),
        ))
    if len(steps) < config.gsl.min_real_plan_steps:
        raise PlanUnavailable(
            f"model returned {len(steps)} usable plan step(s); "
            f"at least {config.gsl.min_real_plan_steps} required"
        )
    return Plan(
        approach_id=str(approach.get("id", "approach-1")),
        approach=str(approach.get("summary", "")),
        steps=steps[:7],
        strategy_number=strategy_number,
    )


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
