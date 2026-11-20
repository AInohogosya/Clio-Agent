"""ClioUI: typed declarative components, rendered natively or as fallbacks.

Every interactive surface the agent puts in front of a person is one of five
typed components, and each is a *description*, never markup: the native
clients (Web, Desktop, Mobile) render the type through `clio-api`, and every
text channel (Slack, Discord, Telegram, Teams, WhatsApp, LINE, email) gets
the fallback rendering from `fallback_text` — buttons become commands a
person can type, a card becomes an actionable message. The component is the
one source; how it looks is the surface's business.

The five components live where the interaction does:

- `DelegationCard` — an `instruction_spec` a person must act on: checkable
  prerequisites, an inline secret dropzone (a secret typed here goes straight
  to the encrypted vault; it never touches chat history), and a Mark Complete
  action wired to `resolves_human_node_id`.
- `ApprovalCard` — the Pre-Execution Approval: what will run (an exact
  command or network diff), the risk classification, and Approve Once /
  Approve for Project / Reject.
- `RigExplorer` — the live RIG: done nodes, active workers, human gates, and
  the `ProjectProgressLedger`'s spend and artifact counts.
- `UndoEntry` list — the timeline of journalled writes with pre-image diffs
  and one-click `/api/actions/:id/undo`.
- `StrategyCard` — the three devised candidates with EV and cost, and
  findings whose every claim carries a `basis` citation to a verified
  artifact or tool output (a finding without a basis is not rendered as one).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.ui.components")

#: Surfaces the native renderer serves.
NATIVE_SURFACES = frozenset({"web", "desktop", "mobile"})

#: Channel → the renderer profile its fallback text is written for.
CHANNEL_MARKUP = {
    "slack": "mrkdwn",
    "discord": "markdown",
    "telegram": "html",
    "teams": "markdown",
    "whatsapp": "plain",
    "line": "plain",
    "email": "plain",
    "cli": "plain",
    "web": "markdown",
}


@dataclass
class UIAction:
    """One thing a person can do from a component.

    `id` is the action's stable name (what `/api/components/:id/action`
    receives); `command` is the text spelling a fallback surface shows, so a
    person on WhatsApp clicks nothing and types `/complete abc123` — the same
    action, the same handler.
    """

    id: str
    label: str
    style: str = "default"       # default | primary | danger
    command: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "style": self.style,
                "command": self.command, "payload": self.payload}


@dataclass
class SecretDropzone:
    """Where a secret is typed so it never enters chat history.

    The native client posts directly to the vault endpoint; a text channel's
    fallback instructs the person to use the settings screen (typing a secret
    into a chat message would put it in the transcript, which is the one
    place it must not be).
    """

    key: str
    label: str
    vault_endpoint: str = "/api/secrets"

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "endpoint": self.vault_endpoint}


@dataclass
class DelegationCard:
    """An instruction_spec a person owes an action on."""

    card_id: str
    title: str
    node_id: str
    resolves_human_node_id: str
    steps: list[dict[str, Any]] = field(default_factory=list)   # {text, done}
    secrets: list[SecretDropzone] = field(default_factory=list)
    due: str | None = None
    context: str = ""

    kind = "delegation_card"

    def actions(self) -> list[UIAction]:
        return [
            UIAction(
                id=f"complete:{self.node_id}",
                label="Mark complete",
                style="primary",
                command=f"/complete {self.node_id}",
                payload={"resolves_human_node_id": self.resolves_human_node_id},
            ),
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "card_id": self.card_id, "title": self.title,
            "node_id": self.node_id,
            "resolves_human_node_id": self.resolves_human_node_id,
            "steps": list(self.steps), "secrets": [s.as_dict() for s in self.secrets],
            "due": self.due, "context": self.context,
            "actions": [a.as_dict() for a in self.actions()],
        }


@dataclass
class ApprovalCard:
    """A Pre-Execution Approval: exact effect, risk class, three outcomes."""

    request_id: str
    tool: str
    reason: str
    command_diff: str = ""
    network_diff: str = ""
    risk: str = "medium"          # low | medium | high
    project_id: str | None = None
    expires_at: str | None = None

    kind = "approval_card"

    def actions(self) -> list[UIAction]:
        payload = {"request_id": self.request_id, "tool": self.tool,
                   "project_id": self.project_id}
        return [
            UIAction(id=f"approve_once:{self.request_id}", label="Approve once",
                     style="primary", command=f"/approve {self.request_id}", payload=payload),
            UIAction(id=f"approve_project:{self.request_id}", label="Approve for project",
                     command=f"/approve-for-project {self.request_id}",
                     payload={**payload, "scope": "project"}),
            UIAction(id=f"reject:{self.request_id}", label="Reject", style="danger",
                     command=f"/deny {self.request_id}", payload=payload),
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "request_id": self.request_id, "tool": self.tool,
            "reason": self.reason, "command_diff": self.command_diff,
            "network_diff": self.network_diff, "risk": self.risk,
            "project_id": self.project_id, "expires_at": self.expires_at,
            "actions": [a.as_dict() for a in self.actions()],
        }


@dataclass
class RigExplorer:
    """The live RIG: nodes, workers, gates and the ledger's real numbers."""

    project_id: str
    title: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    # each node: {id, title, status, worker, is_human_gate}
    metrics: dict[str, Any] = field(default_factory=dict)
    # from ProjectProgressLedger: nodes_done/nodes_total/artifacts_touched/
    # recent_actions/blocking_human_tasks/budget_spent_usd

    kind = "rig_explorer"

    def as_dict(self) -> dict[str, Any]:
        active = [n for n in self.nodes if n.get("status") == "in_progress"]
        gates = [n for n in self.nodes if n.get("is_human_gate")]
        return {
            "kind": self.kind, "project_id": self.project_id, "title": self.title,
            "nodes": list(self.nodes), "metrics": dict(self.metrics),
            "active_workers": len({n.get("worker") for n in active if n.get("worker")}),
            "human_gates": len(gates),
            "actions": [],
        }


@dataclass
class UndoEntry:
    """One journalled write with its pre-image and its undo handle."""

    action_id: str
    tool: str
    summary: str
    when: str
    diff: str = ""
    undo_ref: str | None = None
    retention_days: int = 30

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "undo_entry", "action_id": self.action_id, "tool": self.tool,
            "summary": self.summary, "when": self.when, "diff": self.diff,
            "undo_ref": self.undo_ref, "retention_days": self.retention_days,
            "actions": ([UIAction(id=f"undo:{self.action_id}", label="Undo",
                                  style="danger", command=f"/undo {self.action_id}",
                                  payload={"action_id": self.action_id}).as_dict()]
                        if self.undo_ref else []),
        }


@dataclass
class StrategyOption:
    approach_id: str
    summary: str
    expected_value: float
    estimated_cost_usd: float

    @property
    def value_per_dollar(self) -> float:
        return self.expected_value / max(self.estimated_cost_usd, 0.01)

    def as_dict(self) -> dict[str, Any]:
        return {
            "approach_id": self.approach_id, "summary": self.summary,
            "expected_value": self.expected_value,
            "estimated_cost_usd": self.estimated_cost_usd,
            "value_per_dollar": round(self.value_per_dollar, 3),
        }


@dataclass
class Finding:
    """An analytical finding, carrying the citation that makes it one."""

    claim: str
    basis: list[dict[str, Any]] = field(default_factory=list)
    # each basis: {kind: artifact|tool_output|memory, ref: uri/id, quote?}

    def as_dict(self) -> dict[str, Any]:
        return {"claim": self.claim, "basis": list(self.basis),
                "cited": bool(self.basis)}


@dataclass
class StrategyCard:
    """The three devised candidates and the cited findings about them."""

    card_id: str
    title: str
    options: list[StrategyOption] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    recommended_id: str | None = None

    kind = "strategy_card"

    def as_dict(self) -> dict[str, Any]:
        # A finding without a basis is demoted, not rendered as a claim: the
        # whole point of `basis` is that nothing here is unfalsifiable.
        findings = [f.as_dict() for f in self.findings if f.basis]
        dropped = len(self.findings) - len(findings)
        if dropped:
            logger.warning("ui.findings_dropped_for_missing_basis", dropped=dropped)
        return {
            "kind": self.kind, "card_id": self.card_id, "title": self.title,
            "options": [o.as_dict() for o in self.options],
            "findings": findings,
            "findings_without_basis_dropped": dropped,
            "recommended_id": self.recommended_id,
            "actions": [],
        }


# ---------------------------------------------------------------------------
# Fallback rendering for text channels.


def fallback_text(component: Any, *, channel: str = "plain") -> str:
    """The component as actionable text for a channel without native rendering.

    The fallback is not a summary — it is the *same interaction* in the
    channel's own grammar: native buttons become commands, dropzones become a
    instruction to use the vault screen (never an invitation to type a secret
    into the transcript), and every action the native card offered is a line
    a person can send.
    """
    profile = CHANNEL_MARKUP.get(channel, "plain")
    bold = {"mrkdwn": "*{}*", "markdown": "**{}**", "html": "<b>{}</b>"}.get(
        profile, "{}"
    )
    data = component.as_dict()
    kind = data.get("kind")
    lines: list[str] = []
    if kind == "delegation_card":
        lines.append(bold.format(data["title"]))
        if data.get("context"):
            lines.append(data["context"])
        for step in data["steps"]:
            mark = "[x]" if step.get("done") else "[ ]"
            lines.append(f"{mark} {step.get('text', '')}")
        for dropzone in data["secrets"]:
            lines.append(
                f"Secret needed: {dropzone['label']} — add `{dropzone['key']}` from the "
                "settings screen, not here (a secret typed into chat ends up in the transcript)."
            )
        lines.append("When done, reply: " + " ".join(a["command"] for a in data["actions"]))
    elif kind == "approval_card":
        lines.append(bold.format(f"Approval needed: {data['tool']}"))
        lines.append(f"Risk: {data['risk']}. {data['reason']}")
        if data.get("command_diff"):
            lines.append(f"What will run:\n{data['command_diff']}")
        if data.get("network_diff"):
            lines.append(f"Network:\n{data['network_diff']}")
        options = " · ".join(f"{a['label']} → `{a['command']}`" for a in data["actions"])
        lines.append(options)
    elif kind == "rig_explorer":
        metrics = data.get("metrics") or {}
        lines.append(bold.format(f"{data['title']} — live status"))
        done = metrics.get("nodes_done", 0)
        total = metrics.get("nodes_total", 0)
        lines.append(f"Nodes: {done}/{total} done · workers active: {data['active_workers']} · human gates: {data['human_gates']}")
        if metrics.get("budget_spent_usd") is not None:
            lines.append(f"Spend: ${metrics['budget_spent_usd']:.2f}")
        if metrics.get("blocking_human_tasks"):
            lines.append("Waiting on you: " + ", ".join(str(t) for t in metrics["blocking_human_tasks"][:3]))
        for node in data["nodes"][:12]:
            flag = "hand" if node.get("is_human_gate") else "auto"
            lines.append(f"  [{node.get('status', '?')}/{flag}] {node.get('title', '')}")
    elif kind == "undo_entry":
        lines.append(bold.format(f"{data['tool']} — {data['summary']}"))
        lines.append(f"When: {data['when']}")
        if data.get("diff"):
            lines.append(data["diff"][:500])
        for action in data.get("actions", []):
            lines.append(f"Undo: `{action['command']}`")
    elif kind == "strategy_card":
        lines.append(bold.format(data["title"]))
        for option in data["options"]:
            marker = " (recommended)" if option["approach_id"] == data.get("recommended_id") else ""
            lines.append(
                f"- {option['summary']}{marker} — EV {option['expected_value']:.2f} "
                f"vs cost ${option['estimated_cost_usd']:.2f} "
                f"(value/$ {option['value_per_dollar']})"
            )
        for finding in data["findings"]:
            citations = ", ".join(str(b.get("ref")) for b in finding["basis"][:3])
            lines.append(f"- {finding['claim']} [basis: {citations}]")
    else:
        lines.append(json.dumps(data, default=str)[:2000])
    return "\n".join(lines)


def render(component: Any, *, surface: str) -> dict[str, Any]:
    """The component for one surface: native JSON or a text fallback."""
    if surface in NATIVE_SURFACES:
        return {"render": "native", "component": component.as_dict()}
    return {
        "render": "fallback",
        "channel_profile": CHANNEL_MARKUP.get(surface, "plain"),
        "text": fallback_text(component, channel=surface),
        "component": component.as_dict(),
    }


# ---------------------------------------------------------------------------
# Builders: system state in, cards out.


def delegation_card_from_spec(intention: Any, *, card_id: str | None = None) -> DelegationCard:
    """An intention's `instruction_spec`, as the card a person acts on."""
    spec = dict(getattr(intention, "instruction_spec", None) or {})
    steps = [
        {"text": str(s), "done": False}
        for s in (spec.get("prerequisites") or spec.get("steps") or [])
    ]
    secrets = [
        SecretDropzone(key=str(key), label=str(spec.get("secret_labels", {}).get(key, key)))
        for key in (spec.get("secrets_needed") or [])
    ]
    return DelegationCard(
        card_id=card_id or f"deleg-{getattr(intention, 'id', 'unknown')}",
        title=str(spec.get("title") or getattr(intention, "title", "Action needed")),
        node_id=str(getattr(intention, "id", "")),
        resolves_human_node_id=str(spec.get("resolves_human_node_id") or getattr(intention, "id", "")),
        steps=steps,
        secrets=secrets,
        context=str(spec.get("action_required") or ""),
    )


def approval_card_from_request(request: Any) -> ApprovalCard:
    """A pending ApprovalManager request, as the Pre-Execution Approval card."""
    args = dict(getattr(request, "args_redacted", None) or {})
    command = str(args.get("command") or "")
    url = str(args.get("url") or "")
    risk = "high" if getattr(request, "safety_level", 3) <= 2 else "medium"
    return ApprovalCard(
        request_id=str(getattr(request, "id", "")),
        tool=str(getattr(request, "tool", "")),
        reason=str(getattr(request, "reason", "")),
        command_diff=f"$ {command}" if command else "",
        network_diff=f"{args.get('method', 'GET')} {url}" if url else "",
        risk=risk,
        project_id=str(args.get("project_id")) if args.get("project_id") else None,
    )


def rig_explorer_from_state(project: dict[str, Any], nodes: list[dict[str, Any]],
                            ledger: Any = None) -> RigExplorer:
    """The live RIG card from the project row, its nodes and its ledger."""
    metrics: dict[str, Any] = {}
    if ledger is not None:
        as_dict = getattr(ledger, "as_dict", None)
        metrics = as_dict() if callable(as_dict) else dict(ledger)
    return RigExplorer(
        project_id=str(project.get("project_id") or ""),
        title=str(project.get("title") or "the project"),
        nodes=list(nodes),
        metrics=metrics,
    )


def undo_entries_from_journal(rows: list[dict[str, Any]], *,
                              due_retention_days: int = 30) -> list[UndoEntry]:
    """Journalled actions as the undo timeline."""
    entries: list[UndoEntry] = []
    for row in rows:
        digest = row.get("result_digest")
        if isinstance(digest, str):
            try:
                digest = json.loads(digest)
            except ValueError:
                digest = {}
        summary = ""
        if isinstance(digest, dict):
            summary = str(digest.get("summary") or digest.get("detail") or "")
        entries.append(UndoEntry(
            action_id=str(row.get("id") or ""),
            tool=str(row.get("tool") or ""),
            summary=summary or str(row.get("reason") or row.get("tool") or ""),
            when=str(row.get("ts_end") or row.get("ts_start") or ""),
            diff=str(row.get("args_redacted") or "")[:1000],
            undo_ref=row.get("undo_ref"),
            retention_days=due_retention_days,
        ))
    return entries


def strategy_card_from_candidates(card_id: str, title: str,
                                  candidates: list[dict[str, Any]],
                                  findings: list[dict[str, Any]] | None = None) -> StrategyCard:
    """The devised candidates (and cited findings) as the comparison card."""
    options = [
        StrategyOption(
            approach_id=str(c.get("approach_id") or c.get("id") or index),
            summary=str(c.get("summary") or c.get("approach") or ""),
            expected_value=float(c.get("expected_value") or c.get("ev") or 0.0),
            estimated_cost_usd=float(c.get("estimated_cost_usd") or c.get("cost") or 0.0),
        )
        for index, c in enumerate(candidates)
    ]
    recommended = max(options, key=lambda o: o.value_per_dollar, default=None)
    parsed_findings = [
        Finding(claim=str(f.get("claim") or ""), basis=list(f.get("basis") or []))
        for f in (findings or [])
    ]
    return StrategyCard(
        card_id=card_id, title=title, options=options,
        findings=parsed_findings,
        recommended_id=recommended.approach_id if recommended else None,
    )


__all__ = [
    "ApprovalCard",
    "CHANNEL_MARKUP",
    "DelegationCard",
    "Finding",
    "NATIVE_SURFACES",
    "RigExplorer",
    "SecretDropzone",
    "StrategyCard",
    "StrategyOption",
    "UIAction",
    "UndoEntry",
    "approval_card_from_request",
    "delegation_card_from_spec",
    "fallback_text",
    "render",
    "rig_explorer_from_state",
    "strategy_card_from_candidates",
    "undo_entries_from_journal",
]
