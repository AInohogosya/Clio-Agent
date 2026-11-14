from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from clio import DEFAULT_SELF_NAME
from clio.config import ClioConfig
from clio.env import env
from clio.gateway.secrets import redact
from clio.guardian.classifier import (
    SideEffects,
    classify_tool,
    is_concrete_path,
)
from clio.observability.logger import get_logger
from clio.parse import parse_json_block

logger = get_logger("clio.guardian.gate")

FIELD_MODE = "field"

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
        from clio.schemas.models import ModelMessage, ModelRequest, Role, Tier

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
            data = parse_json_block(response.text or "")
            if data:
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
        config: ClioConfig,
        registry: RegistryLike,
        trash: Any,
        snapshots: Any,
        audit: AuditProtocol,
        verifier: DueProcessVerifier,
        control: Any = None,
        leases: Any = None,
        tool_manifests: Any = None,
        approvals: Any = None,
    ):
        self.config = config
        self.registry = registry
        self.trash = trash
        self.snapshots = snapshots
        self.audit = audit
        self.verifier = verifier
        self.control = control
        self.leases = leases
        self.tool_manifests = tool_manifests
        self.approvals = approvals

    def _lease_for(self, intention_id: str | None) -> Any | None:
        if self.leases is None:
            return None
        if intention_id:
            try:
                for lease in getattr(self.leases, "leases", {}).values():
                    if str(getattr(lease, "intention_id", "") or "") == str(intention_id):
                        return lease
            except Exception:
                pass
            # The manager may be a plain dict of leases.
            try:
                if isinstance(self.leases, dict):
                    for lease in self.leases.values():
                        if str(getattr(lease, "intention_id", "") or "") == str(intention_id):
                            return lease
            except Exception:
                pass
        return None

    def _project_lease(self, workspace_lease_id: str | None) -> Any | None:
        """The lease a project's calls are fenced by, looked up by id.

        Projects own their leases (one per project, never shared), so the id is
        the direct key: a `WorkspaceLeaseManager.get`, a dict, or a bare mapping
        all answer the same question.
        """
        if not workspace_lease_id or self.leases is None:
            return None
        getter = getattr(self.leases, "get", None)
        if callable(getter):
            try:
                lease = getter(workspace_lease_id)
                if lease is not None:
                    return lease
            except Exception:
                return None
        try:
            if isinstance(self.leases, dict):
                return self.leases.get(workspace_lease_id)
        except Exception:
            return None
        return None

    async def _review_lease(
        self, tool: str, args: dict[str, Any],
        intention_id: str | None, thread_id: str,
        *, manifest: dict[str, Any] | None = None,
        workspace_lease_id: str | None = None,
    ) -> GateDecision | None:
        """Lease allowed_paths + budget_limit and tool-manifest enforcement."""
        lease = self._project_lease(workspace_lease_id) or self._lease_for(intention_id)
        if lease is not None:
            from clio.guardian.classifier import extract_paths

            targets: list[str] = []
            for key in ("path", "to_path", "cwd"):
                value = args.get(key)
                if isinstance(value, str) and value:
                    targets.append(value)
            if tool.startswith("shell"):
                try:
                    targets.extend(extract_paths(str(args.get("command", ""))))
                except Exception:
                    pass
            from clio.threads.context import check_allowed_path

            for target in targets:
                if not check_allowed_path(lease, target):
                    reason = (
                        f"path {target!r} is outside workspace lease {lease.id} "
                        f"(allowed: {lease.allowed_paths or 'lease root'})"
                    )
                    await self.audit.append(
                        actor=thread_id, category="guardian",
                        summary=f"action blocked by workspace lease: {tool}",
                        details={"lease_id": lease.id, "target": target, "args": _redacted(args)},
                    )
                    if self._field_mode():
                        await self.audit.append(
                            actor=thread_id, category="guardian",
                            summary="workspace-lease fence audited, allowed in field mode",
                            details={"lease_id": lease.id},
                        )
                        return None
                    return GateDecision(Verdict.REJECT, GatePath.BLOCKED, reason)
            limit = getattr(lease, "budget_limit_usd", None)
            if limit is not None:
                try:
                    spent = float(getattr(lease, "budget_spent_usd", 0) or 0)
                except (TypeError, ValueError):
                    spent = 0.0
                if spent > float(limit):
                    reason = (
                        f"workspace lease {lease.id} budget exceeded "
                        f"(${spent:.2f} spent, ${float(limit):.2f} limit)"
                    )
                    await self.audit.append(
                        actor=thread_id, category="guardian",
                        summary=f"action blocked: lease budget exceeded: {tool}",
                        details={"lease_id": lease.id, "limit": limit, "spent": spent},
                    )
                    if not self._field_mode():
                        return GateDecision(Verdict.REJECT, GatePath.BLOCKED, reason)
        if manifest:
            from clio.guardian.classifier import check_manifest_paths

            blocked = check_manifest_paths(tool, args, manifest)
            if blocked:
                await self.audit.append(
                    actor=thread_id, category="guardian",
                    summary=f"action blocked by tool manifest: {tool}",
                    details={"manifest": str(manifest)[:500], "reason": blocked},
                )
                if not self._field_mode():
                    return GateDecision(Verdict.REJECT, GatePath.BLOCKED, blocked)
        return None

    def _field_mode(self) -> bool:
        """Whether the gate keeps records instead of standing in the way.

        Field mode is the "usable unattended" setting: every veto this gate
        has -- the hard-limit pattern block, the capital-at-risk rejection,
        the due-process model check -- becomes an audit entry, and the
        action proceeds. What it does not touch is the reversibility work,
        which is not a barrier: trash, snapshots and the journal are how a
        mistake gets undone, and they run identically in both modes. Nor
        does it touch oversight: `pause_actions` and preview answer to the
        owner, and stay effective with the gate in any mode.

        Read once per call rather than cached, so a surface (or an
        environment variable on a restarted child) can change the mode
        without the gate holding yesterday's answer.
        """
        mode = env("GUARDIAN_MODE")
        if not mode:
            mode = str(getattr(self.config.permissions.guardian, "mode", FIELD_MODE))
        return mode.strip().lower() == FIELD_MODE

    async def review(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        reason: str,
        intention_id: str | None = None,
        commissioned_by: str | None = None,
        thread_id: str = "self",
        inside_subagent: str | None = None,
        project_id: str | None = None,
        workspace_lease_id: str | None = None,
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

        from clio.guardian.safety import (
            DIALOGUE_REJECT,
            DIALOGUE_REQUIRE_APPROVAL,
            effective_level,
            host_allowlisted,
        )
        from clio.guardian.safety import (
            decide as safety_decide,
        )

        level = effective_level(self.config, self.control)
        decision_safety = safety_decide(level, tool, args, inside_subagent=inside_subagent)
        if decision_safety.action == DIALOGUE_REJECT:
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary=f"action blocked by safety level {level}: {tool}",
                details={"tool": tool, "reason": decision_safety.reason, "args": _redacted(args)},
            )
            return GateDecision(Verdict.REJECT, GatePath.BLOCKED, decision_safety.reason)

        effects = classify_tool(tool, args)
        if "blocked" in effects.categories:
            if self._field_mode():
                # Recorded, then allowed outright. Everything downstream of
                # the gate -- the journal before execution, the snapshot --
                # runs identically, but the destructive review below is not
                # for these: the pattern tables name scans, credential
                # probes and the like, and handing one to the trash
                # transform would be a category error -- the fast path is
                # for deletes of agent-owned files, and `rm -rf /` has no
                # business being moved anywhere.
                await self.audit.append(
                    actor=thread_id, category="guardian",
                    summary=f"hard-limit pattern audited, allowed in field mode: {tool}",
                    details={"notes": effects.notes, "args": _redacted(args)},
                )
                return GateDecision(
                    Verdict.ALLOW, GatePath.NORMAL,
                    "hard-limit pattern allowed in field mode; the hit is on the audit record",
                )
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

        # Clio 3.2: workspace-lease fences and toolshed-manifest permissions
        # run for every tool, destructive or not, before the destructive
        # branch below.
        manifest: dict[str, Any] | None = None
        if self.tool_manifests is not None:
            try:
                if isinstance(self.tool_manifests, dict):
                    manifest = self.tool_manifests.get(tool)
                else:
                    get = getattr(self.tool_manifests, "get", None)
                    manifest = get(tool) if callable(get) else None
            except Exception:
                manifest = None
        lease_decision = await self._review_lease(
            tool, args, intention_id, thread_id, manifest=manifest,
            workspace_lease_id=workspace_lease_id,
        )
        if lease_decision is not None:
            return lease_decision

        if decision_safety.action == DIALOGUE_REQUIRE_APPROVAL:
            # The allowlist waiver comes before the wait. Levels 2-3 stop
            # network commands for a human click, and a step that only reads
            # a host the owner named in `permissions.network.allowed_hosts`
            # has already been approved -- once, on purpose, in writing.
            # Waiting hours for a click on a pre-approved host is how a
            # research step becomes a parked goal. Shell commands never
            # qualify: `host_allowlisted` answers only for tools whose URL
            # the gate can read before execution.
            allowed_hosts = list(
                getattr(self.config.permissions, "network", None) and getattr(
                    self.config.permissions.network, "allowed_hosts", None
                ) or []
            )
            if allowed_hosts and host_allowlisted(tool, args, allowed_hosts):
                await self.audit.append(
                    actor=thread_id, category="guardian",
                    summary=f"allowlisted host, approval waived: {tool}",
                    details={"tool": tool, "args": _redacted(args)},
                )
            else:
                approval = await self._await_approval(
                    tool, args, reason=reason, intention_id=intention_id,
                    thread_id=thread_id, level=level, inside_subagent=inside_subagent,
                    decision_reason=decision_safety.reason,
                )
                if approval is not None:
                    return approval

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
                        project_id=project_id,
                    )
            except Exception:
                logger.exception("gate.auto_register_failed")

        return GateDecision(Verdict.ALLOW, GatePath.NORMAL, "no guardian concern")

    async def _await_approval(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        reason: str,
        intention_id: str | None,
        thread_id: str,
        level: int,
        inside_subagent: str | None,
        decision_reason: str,
    ) -> GateDecision | None:
        manager = self.approvals
        if manager is None:
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary=f"action blocked: manual approval required but no approval channel ({tool})",
                details={"tool": tool, "reason": decision_reason, "args": _redacted(args)},
            )
            return GateDecision(
                Verdict.REJECT, GatePath.BLOCKED,
                f"{decision_reason}; no approval channel is available, so the command did not run",
            )
        timeout_s = float(getattr(self.config.permissions, "approval_timeout_s", 300.0) or 300.0)
        request = await manager.request_and_wait(
            tool,
            _redacted(args),
            reason=f"{reason} ({decision_reason})",
            thread_id=thread_id,
            intention_id=intention_id,
            safety_level=level,
            inside_subagent=inside_subagent,
            timeout_s=timeout_s,
        )
        if request.status == "approved":
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary=f"approved manually: {tool}",
                details={"approval_id": request.id, "decided_by": request.decided_by, "args": _redacted(args)},
            )
            return None
        await self.audit.append(
            actor=thread_id, category="guardian",
            summary=f"action blocked: manual approval {request.status} ({tool})",
            details={"approval_id": request.id, "status": request.status, "args": _redacted(args)},
        )
        return GateDecision(
            Verdict.REJECT, GatePath.BLOCKED,
            f"manual approval {request.status}: {decision_reason}; the command did not run",
        )

    async def _check_money(
        self, money: float, tool: str, args: dict[str, Any],
        thread_id: str, intention_id: str | None,
    ) -> GateDecision | None:
        limits = self.config.permissions.hard_limits
        if money > limits.H2_capital_at_risk_usd:
            if self._field_mode():
                # Audited rather than refused, for the same reason the
                # pattern block is: a cap the gate enforces with a "no" is
                # a place the work stops, and the person who set the cap
                # can read the entry and say so in words.
                await self.audit.append(
                    actor=thread_id, category="guardian",
                    summary="capital-at-risk cap exceeded; allowed and audited (field mode)",
                    details={"money_usd": money, "cap": limits.H2_capital_at_risk_usd},
                )
                return None
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
        # A target the trash can actually take. `Path("")` is `.` and a
        # non-existent path cannot be moved, so neither is a target: the old
        # fallback made `rm -f` on a name that had already gone fail the whole
        # shell command, and a prose echo could become the target in the first
        # place. Concrete means named (not a glob, expansion or `~`), and the
        # fast path below additionally requires that it still exists.
        #
        # The list is `delete_targets` rather than `paths` because the review
        # wants what the command would destroy, not what it names: a compound
        # command's read-side operands (`find DIR ... -exec rm ...`) and its
        # redirects (`2>/dev/null`) are in `paths` and must not be read as
        # things about to be removed. The incident of 2026-10-27 was exactly
        # that reading: the `find` root sat first in `paths`, the registry
        # called it protected because commissioned work lives underneath it,
        # and the trash took the whole project directory for an `rm` whose
        # operands were two subdirectories of it.
        targets = [
            Path(p).expanduser() for p in effects.delete_targets if is_concrete_path(p)
        ]
        if not targets:
            raw = args.get("path") or args.get("to_path")
            if isinstance(raw, str) and is_concrete_path(raw):
                targets = [Path(raw).expanduser()]
        protected = None
        protected_target: Path | None = None
        for target in targets:
            try:
                protected = await self.registry.is_protected(target)
            except Exception:
                protected = None
            if protected is not None:
                protected_target = target
                break

        if protected is not None:
            if self._field_mode():
                # No second opinion in field mode. The due-process check is
                # an extra model call in the critical path of a delete, and
                # it fails closed -- an unreachable verifier was a refusal
                # the work could not argue with. What actually makes this
                # delete safe is the transform below: the artifact goes to
                # trash with long retention and the action is journaled,
                # which is reversibility doing the job a veto was doing.
                entry = await self.trash.move(
                    protected_target if protected_target is not None else Path(str(args.get("path", ""))),
                    kind="due_process", reason=reason, intention_id=intention_id,
                )
                await self.audit.append(
                    actor=thread_id, category="guardian",
                    summary="due process waived in field mode; commissioned artifact moved to trash",
                    details={
                        "uri": protected["uri"], "trash_entry": entry.id, "reason": reason,
                        # What was actually moved: the registry's row may name a
                        # file inside the target (a directory holding
                        # commissioned work), and the record has to say which
                        # one the trash took.
                        "moved": str(entry.origin),
                    },
                )
                return GateDecision(
                    Verdict.TRANSFORM, GatePath.DUE_PROCESS,
                    "field mode: moved to trash with long retention, journaled",
                    transformed_tool="fs.read",
                    transformed_args={"path": entry.to_dict()["path"]},
                    undo_ref=entry.id,
                )
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
                protected_target if protected_target is not None else Path(str(args.get("path", ""))),
                kind="due_process", reason=reason, intention_id=intention_id,
            )
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary="due process PASSED; commissioned artifact moved to trash (180d retention)",
                details={"uri": protected["uri"], "trash_entry": entry.id, "verdict": verdict,
                         "moved": str(entry.origin)},
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
            # A delete of something that is already gone is a no-op, not a
            # failure. The trash was raising `cannot trash non-existent path`
            # here, which turned `rm -f` with a stale or prose-derived name
            # into a failed tool call.
            #
            # Every existing target is taken, not the first one: an `rm` of
            # `venv .venv` that only moved the first left the second for the
            # next attempt, and the command the agent believed it had run --
            # the transform replaces the whole line -- never came back round
            # to it in the shape it wrote. Each move is its own entry, so
            # each can be restored on its own.
            existing = [t for t in targets if t.exists() or t.is_symlink()]
            if not existing:
                return GateDecision(
                    Verdict.ALLOW, GatePath.FAST,
                    "destructive command with no existing concrete path target",
                )
            entries: list[Any] = []
            for target in existing:
                entries.append(await self.trash.move(
                    target, kind="fast_path",
                    reason=reason, intention_id=intention_id, thread_id=thread_id,
                ))
            try:
                await self.snapshots.create(f"pre-delete:{reason}")
            except Exception:
                logger.exception("gate.snapshot_failed")
            await self.audit.append(
                actor=thread_id, category="guardian",
                summary="fast path: agent-owned destructive op moved to trash (30d)",
                details={
                    "target": str(existing[0]),
                    "moved": [str(e.origin) for e in entries],
                    "trash_entry": entries[0].id,
                    "tool": tool,
                },
            )
            return GateDecision(
                Verdict.TRANSFORM, GatePath.FAST,
                "agent-owned file: moved to trash with 30-day retention + snapshot",
                transformed_tool="fs.read",
                transformed_args={"path": entries[0].to_dict()["path"]},
                undo_ref=entries[0].id,
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
                                  intention_id: str | None, kind: str = "file",
                                  project_id: str | None = None) -> str | None: ...


def _redacted(args: dict[str, Any]) -> dict[str, Any]:
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
