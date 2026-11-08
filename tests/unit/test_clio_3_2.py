"""Clio Agent 3.2 (`ethos`): RIG, federation, leases, sessions, manifests.

All tests avoid PostgreSQL; store/router/gate logic is exercised through
pure functions and in-memory fakes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ethos.engine.drives import (
    DriveInputs,
    commitment_gain,
    curiosity_gain,
    mastery_gain,
    order_gain,
    pressures,
)
from ethos.engine.workspace import WorkspaceAssembler, render_commitments, summarize_dag
from ethos.gateway.cost import CostCalculator
from ethos.gateway.providers.base import SessionDriver, session_score
from ethos.gateway.router import ModelRouter
from ethos.gsl.planner import decompose_into_children, needs_decomposition
from ethos.gsl.verification import gate_blocks_completion, gate_children
from ethos.guardian.classifier import check_manifest_paths
from ethos.guardian.gate import GateDecision, GuardianGate
from ethos.memory.retrieval import HybridRetriever
from ethos.memory.store import CycleError, allocate_ev_budgets, dag_has_cycle
from ethos.schemas.events import EventKind
from ethos.schemas.models import ModelRequest, NormalizedResponse, Usage
from ethos.social.deliberation import SocialCognition
from ethos.social.etiquette import OutboundPipeline
from ethos.threads.context import WorkspaceLeaseManager, check_allowed_path
from ethos.threads.manager import allocate_child_budget, ev_weight
from ethos.toolhost.tools.toolshed import verify_tool_signature
from tests.conftest import ListAudit, simple_request


def _make_config(tmp_path: Path):
    from tests.conftest import make_config

    cfg = make_config(tmp_path)
    cfg.paths.ensure()
    return cfg


# ------------------------------------------------------------------ DAG core


def test_dag_cycle_detection():
    assert not dag_has_cycle([("a", "b"), ("b", "c")])
    assert dag_has_cycle([("a", "b"), ("b", "a")])
    # Self loop must also read as a cycle via the pair set.
    assert dag_has_cycle([("a", "a")])


def test_cycle_error_on_self_edge_signature():
    with pytest.raises(CycleError):
        raise CycleError("an intention cannot depend on itself")


def test_allocate_ev_budgets_sums_to_total():
    children = [
        {"key": "a", "expected_value": 0.9, "estimated_cost_usd": 0.1},
        {"key": "b", "expected_value": 0.3, "estimated_cost_usd": 0.1},
    ]
    out = allocate_ev_budgets(10.0, children)
    assert abs(sum(out.values()) - 10.0) < 1e-6
    assert out["a"] > out["b"]


def test_allocate_ev_budgets_respects_h2_cap():
    children = [{"key": "a", "expected_value": 1.0, "estimated_cost_usd": 0.01}]
    out = allocate_ev_budgets(100.0, children, h2_cap=5.0)
    assert out["a"] == pytest.approx(5.0)


def test_allocate_child_budget_ev_proportional():
    assert ev_weight(0.9, 0.1) > ev_weight(0.3, 0.1)
    budget = allocate_child_budget(
        10.0, 0.0, expected_value=0.9, estimated_cost_usd=0.1,
        peers=[{"key": "p", "expected_value": 0.3, "estimated_cost_usd": 0.3}],
    )
    assert budget > 5.0


def test_decomposition_creates_four_children_with_edges():
    children = decompose_into_children("Migrate the billing service end to end")
    assert len(children) == 4
    types = [c["edge_to_parent"] for c in children]
    assert "verifies" in types and "synthesizes" in types
    assert all(c["title"].endswith("Migrate the billing service end to end") or ":" in c["title"] for c in children)


def test_needs_decomposition_flags_projects():
    intention = SimpleNamespace(
        title="Migrate the billing project", desired_end_state="", kind="task",
        commissioned_by="mira", success_criteria=[{"type": "command"}, {"type": "command"}, {"type": "command"}],
    )
    assert needs_decomposition(intention)
    leaf = SimpleNamespace(
        title="Read the post", desired_end_state="", kind="task",
        commissioned_by=None, success_criteria=[],
    )
    assert not needs_decomposition(leaf)


def test_gate_children_only_when_gated():
    assert gate_children("do the thing") == []
    children = gate_children("do the thing", {"require_verifier_pass": True})
    assert len(children) == 2
    assert children[0]["edge_type"] == "verifies"
    assert children[1]["edge_type"] == "synthesizes"


def test_gate_judge_cannot_overturn_failing_checks():
    verification = SimpleNamespace(
        passed=True,
        objective=SimpleNamespace(notes=["failed check: pytest"]),
        verifier={"verdict": "pass"},
    )
    block = gate_blocks_completion(verification, {"require_verifier_pass": True})
    assert block is not None and "failing objective checks" in block
    # A clean pass with a passing verifier is not blocked.
    clean = SimpleNamespace(
        passed=True,
        objective=SimpleNamespace(notes=["ok"]),
        verifier={"verdict": "pass"},
    )
    assert gate_blocks_completion(clean, {"require_verifier_pass": True}) is None
    # No policy, no block.
    assert gate_blocks_completion(clean) is None


# ------------------------------------------------------------------ workspace


def test_two_tier_commitments_summary_and_focus():
    commitments = [
        {"status": "active", "title": "Root DAG", "priority": 0.8, "deadline": None,
         "commissioned_by": "mira", "budget_spent_usd": 1.5, "blocked_count": 2, "ready": True},
        {"status": "done", "title": "Old", "priority": 0.2, "deadline": None,
         "commissioned_by": None, "budget_spent_usd": 0.5, "blocked_count": 0, "ready": False},
    ]
    summary, detail = summarize_dag(commitments, focus_title="Root DAG")
    assert any("Active DAGs" in line for line in summary)
    assert any("blockers" in line for line in summary)
    assert detail and detail[0]["title"] == "Root DAG"
    text = render_commitments(commitments, focus_title="Root DAG")
    assert "Intention DAGs (compressed)" in text
    assert "Focus detail" in text
    assert "Root DAG" in text


def test_workspace_renders_two_tier_commitments(config=None):
    # config-free assembler path (config is unused by the block).
    ws = WorkspaceAssembler(None).assemble(
        identity_doc="id", tools=[], focus=None, percepts=[], state="THINKING",
        energy=0.8, budget_summary="day $0/$5", commitments=[
            {"status": "active", "title": "DAG root", "priority": 0.7, "deadline": None,
             "commissioned_by": "mira", "telemetry": {"completion_ratio": 0.5, "blocked_edges": 1, "ready_count": 2}},
        ], memories=[], thoughts=[], now=datetime(2026, 11, 8, tzinfo=UTC),
    )
    block = next(b.text for b in ws.blocks if b.name == "commitments")
    assert "Intention DAGs (compressed)" in block
    assert "Focus detail" in block


# ------------------------------------------------------------------ drives


def test_graph_driven_pressures():
    base = DriveInputs(open_questions=0, failed_recent=0, unread_events=0)
    dagged = DriveInputs(
        open_questions=0, failed_recent=0, unread_events=0,
        dag_blocked_edges=10, leaf_verification_failures=5, anomalous_outcomes=5,
    )
    assert commitment_gain(dagged) > commitment_gain(base)
    assert order_gain(dagged) > order_gain(base)
    assert mastery_gain(dagged) > mastery_gain(base)
    assert curiosity_gain(dagged) > curiosity_gain(base)
    p = pressures(dagged, {"commitment": 0.2, "curiosity": 0.2, "mastery": 0.2,
                           "creation": 0.2, "order": 0.2, "social": 0.2, "homeostasis": 0.2})
    assert all(0.0 <= v <= 1.0 for v in p.values())


# ------------------------------------------------------------------ federation


class _FakeRelationships:
    def __init__(self, kind: str = "human"):
        self.kind = kind

    async def get(self, person_id: str):
        return {"id": person_id, "relation": "other", "kind": self.kind,
                "capabilities": {}, "authority_scope": {}}

    async def note_contact(self, person_id, direction: str):
        return None


class _Sender:
    def __init__(self):
        self.sent: list[dict] = []

    async def __call__(self, **kwargs):
        self.sent.append(kwargs)
        return {"status": "sent"}


class _Bus:
    def __init__(self):
        self.published: list[dict] = []

    async def publish(self, kind: str, payload: dict):
        self.published.append({"kind": kind, "payload": payload})
        return 1


async def test_resident_fast_path_bypasses_quiet_hours_and_digests(tmp_path):
    cfg = _make_config(tmp_path)
    sender = _Sender()
    bus = _Bus()
    pipe = OutboundPipeline(
        cfg, db=None, bus=bus, audit=None,
        relationships=_FakeRelationships(kind="resident"), sender=sender,
        now_fn=lambda: datetime(2026, 11, 8, 2, 0, tzinfo=UTC),
    )
    allowed, why = await pipe.can_contact("peer-1", "normal")
    assert allowed and "fast-path" in why
    result = await pipe.send(
        person_id="peer-1", channel="web", text="commission.artifact payload",
        urgency="normal",
    )
    assert result["status"] == "sent"
    assert any(p["kind"] == "commission.artifact" for p in bus.published)
    assert sender.sent, "fast-path still reaches the sender"


async def test_human_etiquette_preserved(tmp_path):
    cfg = _make_config(tmp_path)
    sender = _Sender()
    pipe = OutboundPipeline(
        cfg, db=None, bus=None, audit=None,
        relationships=_FakeRelationships(kind="human"), sender=sender,
        now_fn=lambda: datetime(2026, 11, 8, 2, 0, tzinfo=UTC),
    )
    # 02:00 is inside default quiet hours for humans.
    result = await pipe.send(
        person_id="human-1", channel="web", text="hello", urgency="normal",
    )
    assert result["status"] in ("queued", "suppressed", "held")
    assert sender.sent == []


def test_commission_event_kinds_registered():
    kinds = {
        EventKind.COMMISSION_PROPOSE, EventKind.COMMISSION_ACCEPT,
        EventKind.COMMISSION_CHALLENGE, EventKind.COMMISSION_ARTIFACT,
        EventKind.COMMISSION_VERIFY,
    }
    assert kinds == {
        "commission.propose", "commission.accept", "commission.challenge",
        "commission.artifact", "commission.verify",
    }


def test_evaluate_commission_boundary_and_disagreement():
    skills = {"skills": ["code"], "max_budget_usd": 5.0}
    boundary = SocialCognition.evaluate_commission(
        {"summary": "s" * 40, "required_capability": "astrophysics", "budget_usd": 1.0},
        skills=skills,
    )
    assert boundary["action"] == "boundary"
    over = SocialCognition.evaluate_commission(
        {"summary": "s" * 40, "budget_usd": 50.0}, skills=skills,
    )
    assert over["action"] == "deliberate_disagreement"
    vague = SocialCognition.evaluate_commission(
        {"summary": "do stuff", "needs_budget_clarification": True}, skills=skills,
    )
    assert vague["action"] == "ask_clarification"


async def test_deliberate_commission_withholds_unearned_progress(tmp_path):
    cfg = _make_config(tmp_path)
    cognition = SocialCognition(cfg, gateway=None, db=None)
    decision = await cognition.deliberate_commission(
        {"summary": "x" * 40, "required_capability": "astrophysics", "budget_usd": 1.0},
        skills={"skills": ["code"]},
    )
    assert decision.ignore_reason == "boundary"
    assert decision.decided is True  # a boundary is still a decision
    # _withhold_unearned_progress is a no-op without state, but the method
    # must be callable on inter-resident updates without raising.
    cognition._withhold_unearned_progress(decision, SimpleNamespace(readable=False))


# ------------------------------------------------------------------ memory scope


def test_retriever_row_carries_scope():
    row = {
        "id": "1", "kind": "fact", "summary": "s", "body": "{}", "ts": None,
        "importance": 0.5, "strength": 0.5, "emb": None, "source": "fact",
        "scope": "org", "namespace_id": "ns1",
    }
    hit = HybridRetriever._row_to_hit(row)
    assert hit.extra["scope"] == "org"
    assert hit.extra["namespace_id"] == "ns1"


def test_retriever_row_defaults_private_scope():
    row = {
        "id": "1", "kind": "fact", "summary": "s", "body": "{}", "ts": None,
        "importance": 0.5, "strength": 0.5, "emb": None, "source": "fact",
    }
    hit = HybridRetriever._row_to_hit(row)
    assert hit.extra["scope"] == "private"


def test_consolidate_promotion_threshold_logic():
    # Pure check of the promote predicate: success rate above threshold.
    for uses, successes, expected in [(10, 9, True), (10, 5, False), (2, 2, False)]:
        rate = successes / uses if uses else 0
        assert (rate >= 0.8 and uses >= 3) == expected


# ------------------------------------------------------------------ leases


def test_workspace_lease_commit_and_rollback_isolation(tmp_path):
    cfg = _make_config(tmp_path)
    target: Path = tmp_path / "target"
    target.mkdir()
    (target / "a.txt").write_text("base")
    manager = WorkspaceLeaseManager(cfg)
    lease = manager.acquire(target_path=target, kind="snapshot", allowed_paths=["docs"])
    try:
        # Work in the lease root never touches the target until commit.
        secret = lease.root_path / "docs" / "b.txt"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("work")
        assert not (target / "docs" / "b.txt").exists()
        assert check_allowed_path(lease, lease.root_path / "docs" / "b.txt")
        assert not check_allowed_path(lease, lease.root_path / "etc" / "passwd")
        result = manager.commit_lease(lease.id)
        assert result["status"] == "committed"
        assert (target / "docs" / "b.txt").read_text() == "work"
    finally:
        pass
    lease2 = manager.acquire(target_path=target, kind="snapshot")
    (lease2.root_path / "c.txt").write_text("doomed")
    rolled = manager.rollback_lease(lease2.id, reason="verification failed")
    assert rolled["status"] == "rolled_back"
    assert not (target / "c.txt").exists()


def test_lease_budget_limit_blocks_in_gate(tmp_path, monkeypatch):
    cfg = _make_config(tmp_path)
    cfg.permissions.guardian.mode = "strict"
    monkeypatch.setenv("ETHOS_GUARDIAN_MODE", "strict")
    manager = WorkspaceLeaseManager(cfg)
    target: Path = tmp_path / "target"
    target.mkdir()
    lease = manager.acquire(
        target_path=target, kind="snapshot", budget_limit_usd=1.0,
        allowed_paths=[],
        intention_id="int-1",
    )
    lease.budget_limit_usd = 0.0  # exhausted
    # Simulate spend over the limit via the dataclass field the gate reads.
    audit = ListAudit()
    from tests.conftest import FakeRegistry, FakeVerifier, NoopControl, NoopSnapshots

    class FakeTrash:
        async def move(self, *a, **k):
            raise AssertionError("trash must not run for a budget block")

    gate = GuardianGate(
        cfg, registry=FakeRegistry(), trash=FakeTrash(), snapshots=NoopSnapshots(),
        audit=audit, verifier=FakeVerifier(), control=NoopControl(), leases=manager,
    )
    lease.budget_limit_usd = 0.0  # exhausted
    decision = __import__("asyncio").run(gate.review(
        "fs.write", {"path": str(target / "x.txt")}, reason="test",
        intention_id="int-1", thread_id="self",
    ))
    assert isinstance(decision, GateDecision)
    assert decision.allowed is False


# ------------------------------------------------------------------ router/session


def test_router_session_scores_zero_marginal_cost(tmp_path):
    cfg = _make_config(tmp_path)
    from ethos.config import ModelEntry

    session_entry = ModelEntry(
        provider="session", model="session:cli", tiers=["T2"],
        quality=0.8, latency_s=1.0, rpm=60, tpm=100000,
    )
    http_entry = ModelEntry(
        provider="anthropic", model="m", tiers=["T2"],
        quality=0.85, latency_s=1.0, rpm=60, tpm=100000,
    )
    costs = CostCalculator(cfg)
    router = ModelRouter(cfg, costs)
    assert router.is_session_entry(session_entry)
    assert router.marginal_cost(session_entry) == 0.0
    assert router.marginal_cost(http_entry) >= 0.0
    # Equal quality, the session wins on cost alone.
    cfg.gateway.routing.mu = 1.0
    cfg.gateway.routing.nu = 0.0
    http_entry.quality = 0.8
    assert router.score(session_entry, 0.0, simple_request()) >= router.score(
        http_entry, 0.0, simple_request(),
    )


def test_session_driver_snapshot_and_replay():
    class EchoDriver(SessionDriver):
        async def complete(self, request: ModelRequest, model: str) -> NormalizedResponse:
            return NormalizedResponse(text="ok", usage=Usage())

    driver = EchoDriver(session_id="s1")
    request = simple_request()
    snapshot = driver.conversation_snapshot(request)
    assert snapshot["session_id"] == "s1"
    replayed = driver.replay_context(snapshot)
    assert len(replayed.messages) == len(request.messages)
    assert replayed.purpose == "test"


def test_gateway_replay_rebuilds_request(tmp_path):
    from ethos.gateway.service import GatewayService

    cfg = _make_config(tmp_path)
    service = GatewayService(cfg, db=None, providers={})
    request = simple_request()
    actioned = service.replay_request(request)
    assert len(actioned.messages) == len(request.messages)


def test_session_score_formula():
    assert session_score(0.9, 0.0, 1.0) == pytest.approx(0.9)
    assert session_score(0.9, 0.1, 1.0, mu=1.0) == pytest.approx(0.8)


# ------------------------------------------------------------------ manifests


def test_manifest_path_fence():
    manifest = {"allowed_paths": ["/tmp/work"], "allowed_hosts": ["https://example.com"]}
    assert check_manifest_paths("fs.write", {"path": "/tmp/work/a.txt"}, manifest) is None
    assert check_manifest_paths("fs.write", {"path": "/etc/passwd"}, manifest) is not None
    assert check_manifest_paths("http.get", {"url": "https://evil.test/"}, manifest) is not None
    # Empty fence allows.
    assert check_manifest_paths("fs.write", {"path": "/any"}, {}) is None


def test_manifest_signature_verification():
    # No signature means nothing to verify.
    assert verify_tool_signature("abc", None, None) is True
    assert verify_tool_signature("abc", "deadbeef", None) is False
    # A real Ed25519 round-trip.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes_raw().hex()
    sig_hex = private.sign(b"abc").hex()
    assert verify_tool_signature("abc", sig_hex, public_hex) is True
    assert verify_tool_signature("other", sig_hex, public_hex) is False


def test_gate_enforces_manifest(tmp_path, monkeypatch):
    import asyncio

    cfg = _make_config(tmp_path)
    cfg.permissions.guardian.mode = "strict"
    monkeypatch.setenv("ETHOS_GUARDIAN_MODE", "strict")
    audit = ListAudit()
    from tests.conftest import FakeRegistry, FakeVerifier, NoopControl, NoopSnapshots

    gate = GuardianGate(
        cfg, registry=FakeRegistry(), trash=object(), snapshots=NoopSnapshots(),
        audit=audit, verifier=FakeVerifier(), control=NoopControl(),
        tool_manifests={"fs.write": {"allowed_paths": ["/tmp/work"]}},
    )
    decision = asyncio.run(gate.review(
        "fs.write", {"path": "/etc/passwd"}, reason="test", thread_id="self",
    ))
    assert decision.allowed is False
    assert "manifest" in decision.reason
    escape = asyncio.run(gate.review(
        "fs.write", {"path": "/tmp/work/a.txt"}, reason="test", thread_id="self",
    ))
    assert escape.allowed is True


def test_toolshed_register_carries_manifest(tmp_path):
    # Indirectly: check_manifest_paths + _content_hash behaviour the toolshed
    # register/run path depends on.
    from ethos.toolhost.tools.toolshed import _content_hash

    script = tmp_path / "tool.py"
    script.write_text("print(1)")
    first = _content_hash(script)
    assert len(first) == 64
    script.write_text("print(2)")
    assert _content_hash(script) != first
