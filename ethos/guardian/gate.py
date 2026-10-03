from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from ethos import DEFAULT_SELF_NAME
from ethos.config import EthosConfig
from ethos.guardian.classifier import (
    SideEffects,
    classify_tool,
)
from ethos.observability.logger import get_logger

logger = get_logger("ethos.guardian.gate")

DUE_PROCESS_PROMPT = """You are an independent reviewer inside {name}'s guardian subsystem. An agent
is about to move a COMMISSIONED artifact to trash. The claiming reason must be evaluated for
consistency: is the reason actually supported by the evidence? Respond with strict JSON only:
{{"supported": true|false, "why": "one sentence"}}

ARTIFACT: {uri}
CLAIMED REASON: {reason}
EVIDENCE (intention and recent activity):
{evidence}"""


class AuditProtocol(Protocol):
    async def append(self, actor: str, category: str, summary: str, details: dict | None = None) -> Any: ...


class DueProcessVerifier(Protocol):
    async def check_consistency(self, reason: str, evidence: dict[str, Any]) -> dict[str, Any]: ...


class ModelDueProcessVerifier:
    """Diverse-T2 reason-consistency check [D-26, D-28]."""

    def __init__(
        self,
        gateway: Any,
        self_name: str = DEFAULT_SELF_NAME,
        model_family: str | None = None,
    ):
        self.gateway = gateway
        self.self_name = self_name
        self.model_family = model_family

    async def check_consistency(self, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
        from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

        evidence_text = json.dumps(evidence, sort_keys=True, default=str)[:4000]
        request = ModelRequest(
            tier=Tier.T2,
            purpose="guardian.due_process",
            messages=[
                ModelMessage(role=Role.system, content=DUE_PROCESS_PROMPT.format(
                    name=self.self_name, uri=evidence.get("uri", "?"), reason=reason,
                    evidence=evidence_text,
                )),
                ModelMessage(role=Role.user, content="Evaluate the reason."),
            ],
            max_tokens=200,
            metadata={"diversity_family": self.model_family or "guardian"},
        )
        try:
            response = await self.gateway.complete(request)
            text = response.text or ""
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(text[start:end + 1])
                return {
                    "supported": bool(data.get("supported", False)),
                    "why": str(data.get("why", ""))[:500],
                }
        except Exception:
            logger.exception("due_process.verifier_failed")
        return {"supported": False, "why": "verifier unavailable; defaulting to protect the artifact"}


class Verdict:
    ALLOW = "allow"
    TRANSFORM = "transform"
    REJECT = "reject"


class GatePath:
    NORMAL = "normal"
    FAST = "fast"
    DUE_PROCESS = "due_process"
    BLOCKED = "blocked"


class GateDecision:
    def __init__(
        self,
        verdict: str,
        path: str,
        reason: str,
        transformed_tool: str | None = None,
        transformed_args: dict[str, Any] | None = None,
        undo_ref: str | None = None,
        notify: dict[str, Any] | None = None,
    ):
        self.verdict = verdict
        self.path = path
        self.reason = reason
        self.transformed_tool = transformed_tool
        self.transformed_args = transformed_args
        self.undo_ref = undo_ref
        self.notify = notify

    @property
    def allowed(self) -> bool:
        return self.verdict in (Verdict.ALLOW, Verdict.TRANSFORM)


class GuardianGate:
    """The guardian does not deny; it makes actions safe to undo [P5, D-26].

    Layer 1 of defense in depth: hard limits (H1-H6), artifact due process,
    and fast-path reversibility transforms, all journaled to the audit trail.
    """

    def __init__(
        self,
        config: EthosConfig,
        registry: RegistryLike,
        trash: Any,
        snapshots: Any,
        audit: AuditProtocol,
        verifier: DueProcessVerifier,
        control: Any = None,
        spend_guard: Any = None,
    ):
        self.config = config
        self.registry = registry
        self.trash = trash
        self.snapshots = snapshots
        self.audit = audit
        self.verifier = verifier
        self.control = control
        self.spend_guard = spend_guard

    async def review(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        reason: str,
        intention_id: str | None = None,
        commissioned_by: str | None = None,
        thread_id: str = "self",
    ) -> GateDecision:
        """
        There is no `home_dir` here, and its absence is the policy.

        It used to be a keyword argument that the toolhost dutifully passed on
        every call (`home_dir=self.config.paths.home`) and that this method
        threaded down into `_review_destructive` without ever reading. A reviewer
        reading `review(..., home_dir=...)` concludes there is a boundary to
        check paths against, then has to go looking for the check that was never
        written. The agent's reach is the host user's reach: the tools read and
        write any path that account can, and the guardian's job is to make what
        happens there recoverable — trash, snapshots, the journal — not to
        confine where it happens. Removing the argument removes the false promise
        rather than documenting a promise the code was not keeping.
        """
        if self.control is not None and getattr(self.control, "pause_actions", False):
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary="action blocked: actions paused by oversight (H1)",
                details={"tool": tool, "args": _redacted(args)},
            )
            return GateDecision(
                Verdict.REJECT, GatePath.BLOCKED,
                "actions are paused by oversight; nothing executes (H1)",
            )

        # Checked after the pause, not before it, so an agent somebody has
        # actually stopped keeps reporting the reason it was stopped. Both gate
        # every tool and neither is a fallback for the other: the pause is
        # oversight and says so, and this is a mode the owner chose and says so
        # in its own words. One flag quietly standing in for the other is how a
        # refusal ends up journalled as something it was not.
        if self.control is not None and getattr(self.control, "preview", False):
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary=f"action blocked: preview mode is on ({tool})",
                details={"tool": tool, "args": _redacted(args)},
            )
            return GateDecision(
                Verdict.REJECT, GatePath.BLOCKED,
                "preview mode is on: this agent may only talk, so no tool runs. "
                "Turn preview mode off in settings to let it act.",
            )

        effects = classify_tool(tool, args)
        if "blocked" in effects.categories:
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary=f"action blocked by hard limit: {tool}",
                details={"notes": effects.notes, "args": _redacted(args)},
            )
            return GateDecision(
                Verdict.REJECT, GatePath.BLOCKED,
                "; ".join(effects.notes) or "action violates a hard limit (H3/H6)",
            )

        money = effects.money_usd
        if money is not None and money > 0:
            decision = await self._check_money(money, tool, args, thread_id, intention_id)
            if decision is not None:
                return decision

        if effects.is_destructive():
            return await self._review_destructive(
                tool, args, effects, reason=reason, intention_id=intention_id,
                commissioned_by=commissioned_by, thread_id=thread_id,
            )

        if "write" in effects.categories or "move" in effects.categories:
            try:
                target = args.get("path") or args.get("to_path") or ""
                if target and commissioned_by:
                    await self.registry.auto_register_write(
                        target, commissioned_by=commissioned_by, intention_id=intention_id,
                    )
            except Exception:
                logger.exception("gate.auto_register_failed")

        return GateDecision(Verdict.ALLOW, GatePath.NORMAL, "no guardian concern")

    async def _check_money(
        self, money: float, tool: str, args: dict[str, Any],
        thread_id: str, intention_id: str | None,
    ) -> GateDecision | None:
        limits = self.config.permissions.hard_limits
        if money > limits.H2_capital_at_risk_usd:
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary="action blocked: capital-at-risk cap (H2)",
                details={"money_usd": money, "cap": limits.H2_capital_at_risk_usd},
            )
            return GateDecision(
                Verdict.REJECT, GatePath.BLOCKED,
                f"capital at risk ${money:.2f} exceeds cap ${limits.H2_capital_at_risk_usd:.2f} (H2)",
            )
        if money > limits.money_threshold_usd:
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary="consequential money action verified at T3 floor",
                details={"money_usd": money, "tool": tool, "args": _redacted(args)},
            )
        del intention_id
        return None

    async def _review_destructive(
        self,
        tool: str,
        args: dict[str, Any],
        effects: SideEffects,
        *,
        reason: str,
        intention_id: str | None,
        commissioned_by: str | None,
        thread_id: str,
    ) -> GateDecision:
        targets = [Path(p).expanduser() for p in effects.paths if p] or [
            Path(str(args.get("path", ""))).expanduser()
        ]
        targets = [t for t in targets if str(t)]
        protected = None
        for target in targets:
            try:
                protected = await self.registry.is_protected(target)
            except Exception:
                protected = None
            if protected is not None:
                break

        if protected is not None:
            verdict = await self.verifier.check_consistency(
                reason=reason,
                evidence={
                    "uri": protected["uri"],
                    "commissioned_by": protected["commissioned_by"],
                    "intention_id": protected.get("intention_id"),
                    "tool": tool,
                    "args": _redacted(args),
                    "notes": protected.get("notes"),
                    "protection": protected["protection"],
                },
            )
            if not verdict.get("supported", False):
                await self.audit.append(
                    actor=thread_id, category="guardian",
                    summary="due process REJECTED destruction of commissioned artifact",
                    details={"uri": protected["uri"], "reason": reason, "verdict": verdict},
                )
                # The rejection is a fact about the gate, and the audit entry
                # above is where it is kept. It used to also be announced: a
                # sentence composed here, first-person, telling the owner what
                # the agent had declined to do — sent as an ordinary outbound
                # message, so it landed in the transcript reading as the agent
                # having said it, in words it had not chosen. A gate refusing an
                # operation is not a conversational turn; it is a refusal, and
                # the record of it belongs with the other records of what the
                # gate did and why.
                return GateDecision(
                    Verdict.REJECT, GatePath.DUE_PROCESS,
                    f"reason not supported by evidence: {verdict.get('why', '')} — "
                    "commissioned artifacts are never destroyed without due process (H5)",
                )
            entry = await self.trash.move(
                targets[0] if targets else Path(str(args.get("path", ""))),
                kind="due_process", reason=reason, intention_id=intention_id,
            )
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary="due process PASSED; commissioned artifact moved to trash (180d retention)",
                details={"uri": protected["uri"], "trash_entry": entry.id, "verdict": verdict},
            )
            # Same as the rejection above: the move is real, the trash entry is
            # recorded, the audit entry above says why it was allowed, and the
            # owner can ask. What is not done is composing a first-person
            # sentence about it and sending it as though the agent had said so.
            return GateDecision(
                Verdict.TRANSFORM, GatePath.DUE_PROCESS,
                "due process satisfied; moved to trash with 180-day retention",
                transformed_tool="fs.read",
                transformed_args={"path": entry.to_dict()["path"]},
                undo_ref=entry.id,
            )

        if effects.is_destructive() and "delete" in effects.categories:
            if not targets or not targets[0]:
                return GateDecision(
                    Verdict.ALLOW, GatePath.FAST,
                    "destructive command with no concrete path target",
                )
            entry = await self.trash.move(
                targets[0], kind="fast_path",
                reason=reason, intention_id=intention_id, thread_id=thread_id,
            )
            try:
                await self.snapshots.create(f"pre-delete:{reason}")
            except Exception:
                logger.exception("gate.snapshot_failed")
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary="fast path: agent-owned destructive op moved to trash (30d)",
                details={"target": str(targets[0]), "trash_entry": entry.id, "tool": tool},
            )
            return GateDecision(
                Verdict.TRANSFORM, GatePath.FAST,
                "agent-owned file: moved to trash with 30-day retention + snapshot",
                transformed_tool="fs.read",
                transformed_args={"path": entry.to_dict()["path"]},
                undo_ref=entry.id,
            )

        await self.audit.append(
            actor=thread_id, category="guardian",
            summary="irreversible operation audited and allowed (owner machine)",
            details={"tool": tool, "notes": effects.notes, "targets": [str(t) for t in targets]},
        )
        return GateDecision(
            Verdict.ALLOW, GatePath.NORMAL,
            "irreversible op on agent-owned machine: allowed, journaled, snapshot advised",
        )


class RegistryLike(Protocol):
    async def is_protected(self, path: str | Path) -> dict[str, Any] | None: ...
    async def auto_register_write(self, path: str | Path, *, commissioned_by: str | None,
                                  intention_id: str | None, kind: str = "file") -> str | None: ...


def _redacted(args: dict[str, Any]) -> dict[str, Any]:
    from ethos.gateway.secrets import redact

    return redact(dict(args))


__all__ = [
    "AuditProtocol",
    "DueProcessVerifier",
    "GateDecision",
    "GatePath",
    "GuardianGate",
    "ModelDueProcessVerifier",
    "RegistryLike",
    "Verdict",
]
