"""Clio Agent 3.3 project leadership: containers, focus lock, delegation,
governance fast-path and evergreen cycles.

Six behaviours, each exercised against the real object where one exists and a
narrow fake where the object needs the world (a gateway, a live loop). The
store-backed isolation and ledger tests use the suite's own PostgreSQL, the
same `db` fixture every database-backed test here uses; everything else runs
on fakes so the behaviours are provable without a cluster.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from ethos.engine.attention import Attention, project_preemption_allowed
from ethos.engine.drives import DriveInputs, propose_impulses
from ethos.engine.life import Life, LifeDeps
from ethos.engine.rhythm import RhythmController
from ethos.gateway.embeddings import HashingEmbedder
from ethos.gsl.loop import GeneralSolverLoop, GovernanceOutbox
from ethos.guardian.gate import GuardianGate
from ethos.guardian.journal import ActionJournal
from ethos.guardian.registry import ArtifactRegistry
from ethos.memory.store import MemoryStore, ProjectProgressLedger
from ethos.schemas.events import Percept
from ethos.schemas.gsl import Focus
from ethos.schemas.messages import SocialAction, SocialDecision
from ethos.social.deliberation import SocialCognition, _ExecutionState
from ethos.social.etiquette import OutboundPipeline
from ethos.threads.context import WorkspaceLeaseManager
from tests.conftest import ListAudit, make_config


def _make_config(tmp_path: Path):
    cfg = make_config(tmp_path)
    cfg.paths.ensure()
    return cfg


# ---------------------------------------------------------------------------
# Fakes: a store with just enough surface for the focus/switch/delegation paths
# ---------------------------------------------------------------------------


class _FakeDb:
    """The two ad-hoc queries the project paths run outside the store API."""

    def __init__(self) -> None:
        self.ev: dict[str, float] = {}
        self.remaining: dict[str, int] = {}
        self.running: dict[str, int] = {}
        self.failures: list[str] = []

    async def fetchrow(self, stmt: str, *args: Any) -> Any:
        if "AS ev" in stmt:
            pid = str(args[0])
            return {"ev": self.ev.get(pid, 1.0), "cost": 1.0}
        if "AS spent" in stmt:
            return {"spent": 0.0, "allocated": 0.0}
        return None

    async def fetch(self, stmt: str, *args: Any) -> list[Any]:
        if "COUNT(*) AS n" in stmt and "project_id" in stmt:
            pid = str(args[0])
            return [{"n": self.running.get(pid, 0)}]
        if "result_digest" in stmt:
            return [{"d": f} for f in self.failures]
        return []

    async def fetchval(self, stmt: str, *args: Any) -> Any:
        if "COUNT(*) FROM intentions" in stmt:
            return self.remaining.get(str(args[0]), 0)
        return 0

    async def execute(self, stmt: str, *args: Any) -> None:
        return None


class _FakeProjectStore:
    """A dictionary-backed stand-in for the store's project surface."""

    def __init__(self) -> None:
        self.projects: dict[str, dict[str, Any]] = {}
        self.intentions: dict[str, Any] = {}
        self.bindings: dict[str, str] = {}
        self.edges: list[tuple[str, str, str]] = []
        self.human_ready: list[Any] = []
        self.agent_ready: list[Any] = []
        self.milestones: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self.db = _FakeDb()
        self.updated_intentions: list[tuple[str, dict[str, Any]]] = []
        self.updated_projects: list[tuple[str, dict[str, Any]]] = []

    # -- projects --
    async def get_project(self, pid: Any) -> dict[str, Any] | None:
        return self.projects.get(str(pid))

    async def update_project(self, pid: Any, **fields: Any) -> None:
        project = self.projects.setdefault(str(pid), {"project_id": str(pid)})
        project.update(fields)
        self.updated_projects.append((str(pid), fields))

    async def list_projects(self, statuses: Any = None) -> list[dict[str, Any]]:
        if statuses is None:
            return list(self.projects.values())
        return [p for p in self.projects.values() if p.get("status") in statuses]

    async def create_project(self, title: str, owner_principal_id: str,
                             root_intention_id: Any = None, **kwargs: Any) -> dict[str, Any]:
        pid = str(uuid4())
        project = {
            "project_id": pid, "title": title, "owner_principal_id": owner_principal_id,
            "root_intention_id": str(root_intention_id) if root_intention_id else None,
            "workspace_lease_id": f"lease-{pid[:8]}", "lifecycle_mode": "finite",
            "status": "active", "checkpoint_json": {}, "last_active_at": datetime.now(UTC),
            "next_evolution_at": None, "focus_seconds_accumulated": 0.0,
        }
        project.update(kwargs)
        self.projects[pid] = project
        return project

    async def get_conversation_project(self, key: str) -> dict[str, Any] | None:
        pid = self.bindings.get(key)
        return self.projects.get(pid) if pid else None

    async def bind_conversation_project(self, key: str, pid: str | None) -> None:
        if pid:
            self.bindings[key] = str(pid)

    async def get_project_progress_ledger(
        self, project_id: str | None = None,
        current_focus_project_id: str | None = None,
    ) -> list[ProjectProgressLedger]:
        rows = (
            [self.projects[str(project_id)]] if project_id and str(project_id) in self.projects
            else [
                p for p in self.projects.values()
                if p.get("status") != "done"
            ]
        )
        out = []
        for project in rows:
            pid = project["project_id"]
            nodes = [i for i in self.intentions.values() if str(i.project_id) == pid]
            done = [i for i in nodes if i.status == "done"]
            out.append(ProjectProgressLedger(
                project_id=pid, title=project["title"],
                lifecycle_mode=project.get("lifecycle_mode", "finite"),
                status=project.get("status", "active"),
                is_current_focus=bool(
                    current_focus_project_id and pid == str(current_focus_project_id)
                ),
                nodes_done=len(done), nodes_total=len(nodes),
                verified_checks_passed=len(done),
                artifacts_touched=list(project.get("artifacts", [])),
                recent_actions=list(project.get("recent_actions", [])),
                blocking_human_tasks=list(project.get("blocking_human_tasks", [])),
                checkpoint_summary=MemoryStore.format_checkpoint_summary(
                    project.get("checkpoint_json") or {}
                ),
                budget_spent_usd=float(project.get("budget_spent_usd") or 0.0),
            ))
        return out

    # -- intentions --
    async def get_ready_intentions(self, limit: int = 50) -> list[Any]:
        return list(self.agent_ready)[:limit]

    async def get_ready_human_intentions(self, limit: int = 50) -> list[Any]:
        return list(self.human_ready)[:limit]

    async def get_intention(self, iid: Any) -> Any:
        return self.intentions.get(str(iid))

    async def update_intention(self, iid: Any, **fields: Any) -> None:
        node = self.intentions.get(str(iid))
        if node is not None:
            for key, value in fields.items():
                setattr(node, key, value)
        self.updated_intentions.append((str(iid), fields))

    async def create_intention(self, title: str, desired_end_state: str = "", **kwargs: Any) -> str:
        iid = str(uuid4())
        self.intentions[iid] = SimpleNamespace(
            id=iid, title=title, desired_end_state=desired_end_state,
            status="proposed", project_id=kwargs.get("project_id"),
            root_intention_id=kwargs.get("root_intention_id"),
            executor_kind=kwargs.get("executor_kind", "self"),
            instruction_spec=kwargs.get("instruction_spec"),
            kind=kwargs.get("kind", "task"), resume_state={},
        )
        return iid

    async def add_intention_edge(self, parent: Any, child: Any, edge_type: str = "depends_on", **kw: Any) -> None:
        self.edges.append((str(parent), str(child), edge_type))

    async def open_questions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.questions[:limit]

    # -- milestones --
    async def record_project_milestone(self, pid: Any, *, version_tag: str, summary: str,
                                       metrics: dict[str, Any] | None = None,
                                       git_commit_sha: str | None = None) -> str:
        mid = str(uuid4())
        self.milestones.append({
            "milestone_id": mid, "project_id": str(pid), "version_tag": version_tag,
            "summary": summary, "metrics": metrics or {}, "git_commit_sha": git_commit_sha,
        })
        return mid

    async def list_project_milestones(self, pid: Any) -> list[dict[str, Any]]:
        return [m for m in self.milestones if m["project_id"] == str(pid)]

    # -- episodes / recording --
    async def record_episode(self, **kwargs: Any) -> str:
        return str(uuid4())

    format_checkpoint_summary = staticmethod(MemoryStore.format_checkpoint_summary)


class _RecordingComms:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": "sent"}


def _node(**kwargs: Any) -> Any:
    base = {
        "id": str(uuid4()), "title": "node", "status": "active", "kind": "task",
        "project_id": None, "root_intention_id": None, "executor_kind": "self",
        "instruction_spec": None, "resume_state": {}, "commissioned_by": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _life(config: Any, store: Any, comms: Any, gsl: Any) -> Life:
    deps = LifeDeps(
        config=config, control=SimpleNamespace(pause_actions=False, preview=False),
        bus=None, memory=store, self_model=None,
        attention=Attention(config, embedder=HashingEmbedder(1024)),
        motivation=None, rhythm=RhythmController(config), gateway=None,
        gsl=gsl, social=None, comms_out=comms, toolhost=None, audit=None,
        continuity=None, consolidation=None, embedder=None,
        context=SimpleNamespace(), thread_id="self",
    )
    life = Life(deps)
    return life


def _solver(config: Any, store: Any) -> GeneralSolverLoop:
    loop = GeneralSolverLoop(
        config, store, gateway=None, toolhost=None, verification=None,
    )
    return loop


# ===========================================================================
# 1. Workspace & journal isolation
# ===========================================================================


async def test_two_conversations_get_two_isolated_workspaces_and_bindings(tmp_path, db, embedder):
    """Project 1 and Project 2 never share a lease root, and each conversation
    binds to its own project."""
    cfg = _make_config(tmp_path)
    store = MemoryStore(db, embedder, cfg)
    leases = WorkspaceLeaseManager(cfg)
    root_one = await store.create_intention("Build the snake game", "a playable build")
    root_two = await store.create_intention("Migrate the billing service", "cut over cleanly")
    p1 = await store.create_project(
        "Snake game", "owner", root_one, leases=leases,
    )
    p2 = await store.create_project(
        "Billing migration", "owner", root_two, leases=leases, status="preempted",
    )
    assert p1["workspace_lease_id"] and p2["workspace_lease_id"]
    assert p1["workspace_lease_id"] != p2["workspace_lease_id"]
    assert p1["workspace_root"] != p2["workspace_root"]
    assert Path(p1["workspace_root"]).exists()
    assert Path(p2["workspace_root"]).exists()
    # The root intention carries the attribution the whole DAG will inherit.
    intention_one = await store.get_intention(root_one)
    assert str(intention_one.project_id) == p1["project_id"]
    assert str(intention_one.root_intention_id) == str(root_one)

    await store.bind_conversation_project("web:owner", p1["project_id"])
    await store.bind_conversation_project("tg:owner", p2["project_id"])
    bound_one = await store.get_conversation_project("web:owner")
    bound_two = await store.get_conversation_project("tg:owner")
    assert bound_one["title"] == "Snake game"
    assert bound_two["title"] == "Billing migration"
    assert bound_one["project_id"] != bound_two["project_id"]


async def test_tool_calls_stamp_project_on_journal_and_artifacts(tmp_path, db, embedder):
    """A tool call made while working on a project is journalled and registered
    against it — through the intention's own attribution when the context does
    not name the project explicitly."""
    cfg = _make_config(tmp_path)
    store = MemoryStore(db, embedder, cfg)
    leases = WorkspaceLeaseManager(cfg)
    root = await store.create_intention("Write the report", "report.md exists")
    project = await store.create_project("Report", "owner", root, leases=leases)

    # Journal attribution, explicit and inherited.
    journal = ActionJournal(db=db)
    explicit = await journal.begin(
        thread_id="self", intention_id=str(root), tool="fs.write",
        args_redacted={"path": "x"}, reason="test", idempotency_key=None,
        side_effects=["write"], project_id=project["project_id"],
        root_intention_id=str(root),
    )
    await journal.end(explicit, status="ok", result_digest={"summary": "wrote x"})
    row = await db.fetchrow(
        "SELECT project_id::text AS pid, root_intention_id::text AS rid "
        "FROM action_journal WHERE id = $1",
        explicit,
    )
    assert row["pid"] == project["project_id"]
    assert row["rid"] == str(root)
    inherited = await journal.begin(
        thread_id="self", intention_id=str(root), tool="fs.read",
        args_redacted={"path": "x"}, reason="test", idempotency_key=None,
        side_effects=["read"],
    )
    await journal.end(inherited, status="ok", result_digest={"summary": "read x"})
    row = await db.fetchrow(
        "SELECT project_id::text AS pid FROM action_journal WHERE id = $1", inherited,
    )
    assert row["pid"] == project["project_id"], (
        "a call on a project intention inherits the project without being told"
    )

    # Artifact attribution.
    registry = ArtifactRegistry(db)
    target = tmp_path / "report.md"
    target.write_text("done")
    await registry.register(str(target), origin="commission_work",
                            commissioned_by="owner", intention_id=str(root),
                            protection="commissioned", project_id=project["project_id"])
    row = await db.fetchrow(
        "SELECT project_id::text AS pid FROM artifacts WHERE uri = $1", str(target),
    )
    assert row["pid"] == project["project_id"]


async def test_guardian_enforces_the_projects_own_lease(tmp_path, monkeypatch):
    """A write outside the project's lease root is refused in strict mode —
    the gate resolves the fence by `workspace_lease_id`, not by accident."""
    cfg = _make_config(tmp_path)
    cfg.permissions.guardian.mode = "strict"
    monkeypatch.setenv("ETHOS_GUARDIAN_MODE", "strict")
    leases = WorkspaceLeaseManager(cfg)
    projects_root = cfg.paths.work / ".projects" / "p1"
    projects_root.mkdir(parents=True, exist_ok=True)
    lease = leases.acquire(target_path=projects_root, kind="snapshot", allowed_paths=[])
    lease.root_path = projects_root
    audit = ListAudit()
    from tests.conftest import FakeRegistry, FakeVerifier, NoopControl, NoopSnapshots

    gate = GuardianGate(
        cfg, registry=FakeRegistry(), trash=object(), snapshots=NoopSnapshots(),
        audit=audit, verifier=FakeVerifier(), control=NoopControl(), leases=leases,
    )
    inside = await gate.review(
        "fs.write", {"path": str(projects_root / "a.txt")}, reason="test",
        thread_id="self", project_id="p1", workspace_lease_id=lease.id,
    )
    assert inside.allowed
    outside = await gate.review(
        "fs.write", {"path": str(tmp_path / "elsewhere.txt")}, reason="test",
        thread_id="self", project_id="p1", workspace_lease_id=lease.id,
    )
    assert outside.allowed is False
    assert "lease" in outside.reason


# ===========================================================================
# 2. Focus quantum lock & switch governance
# ===========================================================================


def test_preemption_rule_is_pure_and_margin_bound():
    assert project_preemption_allowed(
        "p2", "p1", 0.9, lock_active=False, current_salience=0.65, margin=0.2,
    )
    assert not project_preemption_allowed(
        "p2", "p1", 0.70, lock_active=True, current_salience=0.65, margin=0.2,
    )
    assert project_preemption_allowed(
        "p2", "p1", 0.86, lock_active=True, current_salience=0.65, margin=0.2,
    )
    assert project_preemption_allowed(
        "p2", "p1", 0.10, lock_active=True, current_salience=0.65, margin=0.2,
        priority_override=True,
    )
    assert project_preemption_allowed(
        "p1", "p1", 0.10, lock_active=True, current_salience=0.65, margin=0.2,
    ), "the project's own work is never blocked by its own lock"


async def test_attention_holds_the_quantum_against_another_project(tmp_path):
    cfg = _make_config(tmp_path)
    attention = Attention(cfg, embedder=HashingEmbedder(1024))
    # A pressing-but-not-preemptive stimulus: above the state threshold (0.65)
    # but below threshold + margin (0.85).
    percept = Percept(
        kind="msg.inbound", summary="update on project two?",
        payload={"project_id": "p2"}, sender_class="owner", urgency=0.75,
    )
    appraisal = await attention.appraise(
        [percept], focus=Focus(title="Project one", kind="commitment"),
        thresholds_by_state={"default": 0.65}, state_name="THINKING_FOCUSED",
        active_project_id="p1", project_lock_active=True,
    )
    assert appraisal.interrupt is False, (
        "another project's stimulus cannot take the pass inside the quantum"
    )
    override = Percept(
        kind="msg.inbound", summary="update on project two?",
        payload={"project_id": "p2", "priority_override": True},
        sender_class="owner", urgency=0.75,
    )
    appraisal = await attention.appraise(
        [override], focus=Focus(title="Project one", kind="commitment"),
        thresholds_by_state={"default": 0.65}, state_name="THINKING_FOCUSED",
        active_project_id="p1", project_lock_active=True,
    )
    assert appraisal.interrupt is True, "an explicit override still interrupts"
    # And the same percept, with no lock, interrupts on its own salience.
    appraisal = await attention.appraise(
        [percept], focus=Focus(title="Project one", kind="commitment"),
        thresholds_by_state={"default": 0.65}, state_name="THINKING_FOCUSED",
        active_project_id=None, project_lock_active=False,
    )
    assert appraisal.interrupt is True


async def test_second_project_is_queued_and_kickoff_emitted(tmp_path):
    """A message in a second conversation assigning Project 2 while Project 1
    holds its quantum creates Project 2 as `preempted` (its plan is
    decomposed and a kickoff notice is queued) and keeps the focus on
    Project 1."""
    cfg = _make_config(tmp_path)
    store = _FakeProjectStore()
    comms = _RecordingComms()
    gsl = SimpleNamespace(governance=GovernanceOutbox())
    life = _life(cfg, store, comms, gsl)

    p1 = await store.create_project("Project One", "owner", root_intention_id=str(uuid4()))
    store.projects[p1["project_id"]]["status"] = "active"
    life.active_project_id = p1["project_id"]
    life.project_focus_started_at = time.monotonic()

    p2 = await store.create_project(
        "Project Two", "owner", root_intention_id=str(uuid4()), status="preempted",
    )
    await store.bind_conversation_project("web:owner", p2["project_id"])
    # The kickoff is what the solver emits once the DAG exists; here the DAG
    # emission is exercised through the outbox directly (the loop-side tests
    # below cover the decomposition hook).
    notice = gsl.governance.emit(
        "project_kickoff",
        {"project_title": "Project Two", "plan": ["Understand: Project Two"],
         "person_id": "owner"},
        dedupe_key=f"kickoff:{p2['project_id']}", person_id="owner",
        project_id=p2["project_id"],
    )
    assert notice is not None
    assert life.active_project_id == p1["project_id"], (
        "the new project is queued, not focused"
    )
    assert store.projects[p2["project_id"]]["status"] == "preempted"
    bound = await store.get_conversation_project("web:owner")
    assert bound["project_id"] == p2["project_id"]


async def test_switch_saves_checkpoint_and_emits_governance_notice(tmp_path):
    cfg = _make_config(tmp_path)
    store = _FakeProjectStore()
    comms = _RecordingComms()
    gsl = SimpleNamespace(governance=GovernanceOutbox())
    life = _life(cfg, store, comms, gsl)

    p1 = await store.create_project("Project One", "owner", root_intention_id=str(uuid4()))
    p2 = await store.create_project("Project Two", "owner", root_intention_id=str(uuid4()))
    # Some verified history on project one for the checkpoint to carry.
    done = _node(status="done", project_id=p1["project_id"])
    open_node = _node(status="active", project_id=p1["project_id"], title="finish the API")
    store.intentions[done.id] = done
    store.intentions[open_node.id] = open_node
    store.projects[p1["project_id"]]["artifacts"] = ["/tmp/out/a.py", "/tmp/out/b.py"]
    store.projects[p1["project_id"]]["recent_actions"] = ["fs.write -> ok: wrote a.py"]
    store.agent_ready = [_node(status="active", project_id=p1["project_id"], title="next step")]
    life.active_project_id = p1["project_id"]
    life.project_focus_started_at = time.monotonic() - 60.0
    life.focus = Focus(
        title="finish the API", kind="commitment", intention_id=open_node.id,
    )

    await life._switch_project_focus(
        p1["project_id"], p2["project_id"], "fair rotation after the quantum",
    )

    checkpoint = store.projects[p1["project_id"]]["checkpoint_json"]
    assert checkpoint["completed_nodes"] == 1
    assert checkpoint["total_nodes"] == 2
    assert checkpoint["active_node_id"] == open_node.id
    assert checkpoint["modified_files"] == ["/tmp/out/a.py", "/tmp/out/b.py"]
    assert checkpoint["next_planned_step"] == "next step"
    assert checkpoint["reason"] == "fair rotation after the quantum"
    assert store.projects[p1["project_id"]]["status"] == "preempted"
    assert store.projects[p2["project_id"]]["status"] == "active"
    assert life.active_project_id == p2["project_id"]
    assert life.project_focus_started_at is not None

    notices = [n for n in gsl.governance.pending if n.event_type == "focus_switched"]
    assert notices, "a switch without a notice is a silent handoff"
    notice = notices[0]
    assert notice.person_id == "owner"
    rendered = notice.render_text()
    assert "Switching focus from “Project One” to “Project Two”" in rendered
    assert "Why:" in rendered
    assert "next step" in rendered, "the checkpoint rides in the notice"
    assert notice.payload["resume_condition"], "the notice says how the project resumes"
    assert notice.payload["checkpoint_summary"]


async def test_rotation_scores_value_over_cost_with_idle_multiplier(tmp_path):
    cfg = _make_config(tmp_path)
    store = _FakeProjectStore()
    comms = _RecordingComms()
    gsl = SimpleNamespace(governance=GovernanceOutbox())
    life = _life(cfg, store, comms, gsl)

    p1 = await store.create_project("Busy project", "owner")
    p2 = await store.create_project("Waiting project", "owner", status="preempted")
    now = datetime.now(UTC)
    store.projects[p1["project_id"]]["last_active_at"] = now
    # The waiting project has been idle for an hour and carries the same value.
    store.projects[p2["project_id"]]["last_active_at"] = now - timedelta(hours=1)
    store.db.ev[p1["project_id"]] = 1.0
    store.db.ev[p2["project_id"]] = 1.0
    life.active_project_id = p1["project_id"]
    life.project_focus_started_at = time.monotonic() - (
        cfg.projects.project_min_focus_s + 1.0
    )
    life.focus = None

    await life._maybe_rotate_project_focus()

    assert life.active_project_id == p2["project_id"], (
        "the idle project with equal value per dollar wins the rotation"
    )


# ===========================================================================
# 3. Accurate multi-project progress reporting
# ===========================================================================


async def test_progress_ledger_preserves_preempted_projects_progress(tmp_path, db, embedder):
    cfg = _make_config(tmp_path)
    store = MemoryStore(db, embedder, cfg)
    leases = WorkspaceLeaseManager(cfg)
    root = await store.create_intention("Project two root", "done-ish")
    p2 = await store.create_project("Project Two", "owner", root, leases=leases)
    # Two done nodes, one outstanding; an artifact and an ok action all-time.
    done_a = await store.create_intention("step a", "", project_id=p2["project_id"], root_intention_id=root)
    done_b = await store.create_intention("step b", "", project_id=p2["project_id"], root_intention_id=root)
    await store.create_intention("step c", "", project_id=p2["project_id"], root_intention_id=root)
    await store.close_intention(done_a, "done", "verified")
    await store.close_intention(done_b, "done", "verified")
    registry = ArtifactRegistry(db)
    artifact = tmp_path / "delivered.py"
    artifact.write_text("print('done')")
    await registry.register(str(artifact), origin="commission_work",
                            commissioned_by="owner", intention_id=str(done_a),
                            protection="commissioned", project_id=p2["project_id"])
    journal = ActionJournal(db=db)
    action = await journal.begin(
        thread_id="self", intention_id=str(done_a), tool="fs.write",
        args_redacted={"path": str(artifact)}, reason="step", idempotency_key=None,
        side_effects=["write"], project_id=p2["project_id"],
    )
    await journal.end(action, status="ok", result_digest={"summary": "wrote delivered.py"})
    await store.update_project(p2["project_id"], status="preempted")

    ledgers = await store.get_project_progress_ledger(
        project_id=p2["project_id"], current_focus_project_id=None,
    )
    assert len(ledgers) == 1
    ledger = ledgers[0]
    # The root plus its three steps; two of them verified done.
    assert ledger.nodes_done == 2 and ledger.nodes_total == 4
    assert str(artifact) in ledger.artifacts_touched
    assert any("fs.write" in action for action in ledger.recent_actions)
    assert ledger.status == "preempted"

    # And the same facts keep the reply's progress claim from being stripped.
    cognition = SocialCognition(cfg, gateway=None, db=None)
    decision = SocialDecision(
        action=SocialAction.reply_now,
        reply_texts=["I'm working on Project Two — two of its three steps are already done."],
    )
    state = _ExecutionState(
        True, [], [], 15.0, projects=[ledger.as_dict()],
    )
    cognition._withhold_unearned_progress(decision, state)
    assert decision.reply_texts, (
        "a project with verified all-time progress must not be reported as zero"
    )

    # Without any evidence, the same claim is still withheld.
    decision_two = SocialDecision(
        action=SocialAction.reply_now,
        reply_texts=["I'm working on Project Two right now."],
    )
    cognition._withhold_unearned_progress(
        decision_two, _ExecutionState(True, [], [], 15.0, projects=[]),
    )
    assert decision_two.reply_texts == []


def test_workspace_renders_the_project_portfolio_below_the_focus_dag(config=None):
    """The commitments block keeps the focus DAG and adds every project's
    compact standing below it — with nodes, files, blockers and checkpoint."""
    from ethos.engine.workspace import render_commitments

    commitments = [
        {"status": "in_progress", "title": "Focus work", "priority": 0.8,
         "deadline": None, "commissioned_by": "owner", "budget_spent_usd": 1.0},
    ]
    ledgers = [
        {"project_id": "aaaaaaaa-1111", "title": "Focus work", "status": "active",
         "lifecycle_mode": "finite", "is_current_focus": True, "nodes_done": 2,
         "nodes_total": 5, "artifacts_touched": ["/tmp/a.py"], "recent_actions": [],
         "blocking_human_tasks": [], "checkpoint_summary": "", "budget_spent_usd": 1.0},
        {"project_id": "bbbbbbbb-2222", "title": "Queued work", "status": "preempted",
         "lifecycle_mode": "evergreen", "is_current_focus": False, "nodes_done": 1,
         "nodes_total": 6, "artifacts_touched": ["/tmp/beta.py"],
         "recent_actions": ["fs.write -> ok: wrote beta.py"],
         "blocking_human_tasks": [{
             "id": "n1", "title": "provide the key",
             "instruction_spec": {"action_required": "Create the production key"},
         }],
         "checkpoint_summary": "1/6 nodes done; next: ship v2", "budget_spent_usd": 3.25},
    ]
    text = render_commitments(commitments, focus_title="Focus work", project_ledger=ledgers)
    assert "### Focus detail" in text
    assert "## Project Portfolio Ledger" in text
    assert "Queued work [preempted" in text
    assert "nodes 1/6 done" in text
    assert "/tmp/beta.py" in text
    assert "waiting on you: Create the production key" in text
    assert "checkpoint: 1/6 nodes done" in text
    # The focused project sorts first even though it is rendered second.
    assert text.index("Focus work [active") < text.index("Queued work [preempted")


class _ContextStub:
    """The carried context, as a bare pair of methods."""

    def restore(self, snapshot: Any) -> None:
        self.restored = snapshot

    def snapshot(self) -> dict[str, Any]:
        return {}

    def render_blocks(self) -> tuple[str, str]:
        return "", ""


async def test_the_focus_quantum_survives_a_restart(tmp_path):
    """`active_project_id` and its quantum clock persist through continuity
    and come back on wake — a restart does not re-open the lock."""
    from ethos.memory.continuity import ContinuityManager
    from ethos.schemas.gsl import Decision, Outcome

    cfg = _make_config(tmp_path)
    continuity = ContinuityManager(cfg)
    store = _FakeProjectStore()
    started = time.monotonic() - 120.0

    first = _life(cfg, store, _RecordingComms(), SimpleNamespace(governance=GovernanceOutbox()))
    first.deps.continuity = continuity
    first.deps.audit = ListAudit()
    first.context = _ContextStub()
    first.active_project_id = "project-one"
    first.project_focus_started_at = started
    await first.record(None, Decision(), Outcome())

    second = _life(cfg, store, _RecordingComms(), SimpleNamespace(governance=GovernanceOutbox()))
    second.deps.continuity = continuity
    second.deps.audit = ListAudit()
    second.context = _ContextStub()
    assert second.active_project_id is None
    await second.wake_up()
    assert second.active_project_id == "project-one"
    assert second.project_focus_started_at == pytest.approx(started, abs=2.0)

    # A stale stamp from a previous boot is discarded rather than pinning the
    # quantum open or shut.
    stale = time.monotonic() + 10_000.0
    await continuity.save({
        "thread_id": "self",
        "projects": {"active_project_id": "project-one", "project_focus_started_at": stale},
    })
    third = _life(cfg, store, _RecordingComms(), SimpleNamespace(governance=GovernanceOutbox()))
    third.deps.continuity = continuity
    third.deps.audit = ListAudit()
    third.context = _ContextStub()
    await third.wake_up()
    assert third.active_project_id == "project-one"
    assert third.project_focus_started_at is None


async def test_deliberation_context_carries_bound_project_and_portfolio(tmp_path):
    cfg = _make_config(tmp_path)
    store = _FakeProjectStore()
    comms = _RecordingComms()
    life = _life(cfg, store, comms, SimpleNamespace(governance=GovernanceOutbox()))
    p1 = await store.create_project("Alpha", "owner")
    p2 = await store.create_project("Beta", "owner", status="preempted")
    store.projects[p2["project_id"]]["artifacts"] = ["/tmp/beta.py"]
    await store.bind_conversation_project("web:owner", p2["project_id"])
    life.active_project_id = p1["project_id"]
    message = SimpleNamespace(conversation_key="web:owner")

    bound, ledgers = await life._deliberation_project_context(message)
    assert bound is not None and bound["project_id"] == p2["project_id"]
    ids = {ledger["project_id"] for ledger in ledgers}
    assert {p1["project_id"], p2["project_id"]} <= ids
    beta = next(ledger for ledger in ledgers if ledger["project_id"] == p2["project_id"])
    assert beta["artifacts_touched"] == ["/tmp/beta.py"]
    assert beta["is_current_focus"] is False
    alpha = next(ledger for ledger in ledgers if ledger["project_id"] == p1["project_id"])
    assert alpha["is_current_focus"] is True


# ===========================================================================
# 4. Human delegation & unblocking
# ===========================================================================


def test_decomposition_marks_human_nodes_with_instruction_spec():
    from ethos.gsl.planner import decompose_into_children

    specs = decompose_into_children(
        "Deploy the service to production using the production credentials",
    )
    human = [s for s in specs if s["executor_kind"] == "human"]
    assert human, "credentials and production deploys are a person's to provide"
    spec = human[0]["instruction_spec"]
    assert set(spec) >= {
        "action_required", "rationale", "exact_steps", "acceptance_evidence",
    }
    assert spec["exact_steps"], "an ask without steps is not an instruction"
    agentic = decompose_into_children("Refactor the parser")
    assert all(s["executor_kind"] == "self" for s in agentic)


async def test_human_node_becomes_an_ask_and_pauses_only_when_nothing_else_runs(tmp_path):
    cfg = _make_config(tmp_path)
    store = _FakeProjectStore()
    loop = _solver(cfg, store)
    project = await store.create_project("Deploy project", "owner", root_intention_id=str(uuid4()))
    pid = project["project_id"]
    store.projects[pid]["status"] = "active"
    human_node = _node(
        title="Provide the production credentials", status="active", project_id=pid,
        executor_kind="human",
        instruction_spec={
            "action_required": "Create a production API key and say so here",
            "rationale": "only you can hold this credential",
            "exact_steps": ["Open the provider console", "Create the key", "Reply here"],
            "acceptance_evidence": "a short confirmation",
        },
    )
    store.human_ready = [human_node]
    store.intentions[human_node.id] = human_node
    agent_branch = _node(title="write the deploy script", status="active", project_id=pid)
    store.agent_ready = [agent_branch]
    store.intentions[agent_branch.id] = agent_branch

    await loop._advance_project_governance(human_node, store.projects[pid])

    assert human_node.status == "awaiting_human"
    notices = [n for n in loop.governance.pending if n.event_type == "human_action_required"]
    assert notices and notices[0].payload["instruction_spec"]["exact_steps"]
    assert store.projects[pid]["status"] == "active", (
        "an independent agent branch keeps the project running"
    )
    # Now the branches are gone: ready lists empty, nothing running.
    store.agent_ready = []
    store.human_ready = [human_node]
    await loop._advance_project_governance(human_node, store.projects[pid])
    assert store.projects[pid]["status"] == "paused_for_human"

    # The person answers: the node closes and the project resumes.
    class _Decision:
        resolves_human_node_id = human_node.id
        human_node_evidence = "created the key, it is in the vault"

    life = _life(cfg, store, _RecordingComms(), SimpleNamespace(governance=GovernanceOutbox()))
    resolved = await life._resolve_human_node(_Decision(), SimpleNamespace(person_id="owner"))
    assert resolved is True
    assert human_node.status == "done"
    assert human_node.instruction_spec["evidence"] == "created the key, it is in the vault"
    assert store.projects[pid]["status"] == "active", (
        "unblocking the human node resumes the project"
    )


async def test_stalled_verification_and_spend_raise_blocker_notices(tmp_path):
    cfg = _make_config(tmp_path)
    store = _FakeProjectStore()
    loop = _solver(cfg, store)
    project = await store.create_project("Risky project", "owner", root_intention_id=str(uuid4()))
    pid = project["project_id"]

    async def _spent(stmt: str, *args: Any) -> Any:
        return {"spent": 8.0, "allocated": 10.0}

    store.db.fetchrow = _spent  # type: ignore[assignment]
    node = _node(project_id=pid)
    store.intentions[node.id] = node
    await loop._advance_project_governance(node, store.projects[pid])
    blockers = [n for n in loop.governance.pending if n.event_type == "critical_risk_or_blocker"]
    assert blockers, "spend at 80% of the allocation is the owner's business"
    # And the same event is not raised twice inside the dedupe window.
    await loop._advance_project_governance(node, store.projects[pid])
    blockers_again = [n for n in loop.governance.pending if n.event_type == "critical_risk_or_blocker"]
    assert len(blockers_again) == len(blockers)


# ===========================================================================
# 5. Governance fast-path
# ===========================================================================


class _FakeRelationships:
    kind = "human"

    async def get(self, person_id: str):
        return {"id": person_id, "relation": "other", "kind": self.kind}

    async def note_contact(self, person_id: str, direction: str):
        return None


class _Sender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": "sent"}


async def test_governance_bypasses_digest_and_suppression_but_keeps_casual_caps(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.timezone = "UTC"
    sender = _Sender()
    audit = ListAudit()
    # 10:00 — outside quiet hours, so this is about the digest and the
    # awaiting-reply suppression.
    pipe = OutboundPipeline(
        cfg, db=None, bus=None, audit=audit,
        relationships=_FakeRelationships(), sender=sender,
        now_fn=lambda: datetime(2026, 11, 14, 10, 0, tzinfo=UTC),
    )
    casual = await pipe.send(person_id="human-1", channel="web", text="just thinking", urgency="normal")
    assert casual["status"] in ("queued", "duplicate"), "a casual note still batches"
    assert sender.sent == []

    governance = await pipe.send(
        person_id="human-1", channel="web",
        text="Kicking off “Snake game”. Plan: build, test.",
        urgency="normal", category="governance",
        event_type="project_kickoff", dedupe_key="kickoff:p1",
    )
    assert governance["status"] == "sent", "governance is not batched behind the digest"
    assert sender.sent and sender.sent[-1]["text"].startswith("Kicking off")

    repeat = await pipe.send(
        person_id="human-1", channel="web", text="Kicking off “Snake game”. Plan: build, test.",
        urgency="normal", category="governance",
        event_type="project_kickoff", dedupe_key="kickoff:p1",
    )
    assert repeat["status"] == "deduped", "the same notice inside 15 minutes is not sent twice"

    # Casual budgets stay the 6/3/1 shape they always were.
    assert pipe.daily_budget("owner") == 6
    assert pipe.daily_budget("collaborator") == 3
    assert pipe.daily_budget("other") == 1


async def test_quiet_hours_deliver_blockers_and_queue_good_news(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.timezone = "UTC"
    sender = _Sender()
    pipe = OutboundPipeline(
        cfg, db=None, bus=None, audit=ListAudit(),
        relationships=_FakeRelationships(), sender=sender,
        now_fn=lambda: datetime(2026, 11, 14, 2, 0, tzinfo=UTC),
    )
    blocked = await pipe.send(
        person_id="human-1", channel="web",
        text="“Deploy project” needs something only you can do: create the key.",
        urgency="high", category="governance",
        event_type="human_action_required", dedupe_key="human:n1",
    )
    assert blocked["status"] == "sent", "a human ask is delivered even at 02:00"
    queued = await pipe.send(
        person_id="human-1", channel="web",
        text="Milestone shipped on “Snake game”: v1.",
        urgency="normal", category="governance",
        event_type="milestone_completed", dedupe_key="ship:p1:v1",
    )
    assert queued["status"] == "queued", "good news keeps the night"
    # Morning: the queue delivers.
    pipe.now_fn = lambda: datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
    flushed = await pipe.flush_governance_queue()
    assert flushed == 1
    assert any("Milestone shipped" in m["text"] for m in sender.sent)


# ===========================================================================
# 6. Evergreen evolution cycle
# ===========================================================================


async def test_evergreen_project_ships_a_milestone_then_rests(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.projects.evolution_interval_s = 3600.0
    store = _FakeProjectStore()
    loop = _solver(cfg, store)
    project = await store.create_project(
        "Continuous tuner", "owner", root_intention_id=str(uuid4()),
    )
    pid = project["project_id"]
    store.projects[pid]["lifecycle_mode"] = "evergreen"
    store.projects[pid]["status"] = "active"
    store.db.remaining[pid] = 0  # every node is done

    class _Leases:
        def __init__(self) -> None:
            self.committed: list[str] = []

        def commit_lease(self, lease_id: str, **kwargs: Any) -> dict[str, Any]:
            self.committed.append(lease_id)
            return {"status": "committed", "lease_id": lease_id, "target": str(tmp_path)}

    leases = _Leases()
    loop._leases = leases
    node = _node(project_id=pid, title="tune the heuristic")
    store.intentions[node.id] = node
    verification = SimpleNamespace(rung_reached=3, passed=True)

    await loop._on_project_node_done(node, store.projects[pid], verification)

    assert leases.committed == [store.projects[pid]["workspace_lease_id"]]
    assert store.milestones and store.milestones[0]["version_tag"] == "v1"
    assert store.projects[pid]["status"] == "evolving_cooldown"
    next_at = store.projects[pid]["next_evolution_at"]
    assert next_at is not None and next_at > datetime.now(UTC), (
        "an evergreen project rests rather than closing"
    )
    shipped = [
        n for n in loop.governance.pending
        if n.event_type == "milestone_completed" and n.payload.get("version_tag") == "v1"
    ]
    assert shipped, "the ship is reported to the owner"

    # Once the cooldown elapses the impulse proposes the next cycle.
    from ethos.engine.drives import DRIVES

    pressures = {d: 0.9 for d in DRIVES}
    impulses = propose_impulses(
        pressures, DriveInputs(evolving_projects=1), threshold=0.4,
    )
    assert any(i.name == "evolve_project" for i in impulses)

    store.projects[pid]["next_evolution_at"] = datetime.now(UTC) - timedelta(seconds=1)
    candidates = await _life(
        cfg, store, _RecordingComms(), SimpleNamespace(governance=GovernanceOutbox()),
    )._evolution_candidates()
    assert candidates and candidates[0]["project_id"] == pid


async def test_propose_next_evolution_reopens_with_a_fresh_plan(tmp_path):
    cfg = _make_config(tmp_path)
    store = _FakeProjectStore()
    loop = _solver(cfg, store)
    root = await store.create_intention("Continuous tuner", "keeps improving")
    project = await store.create_project(
        "Continuous tuner", "owner", root_intention_id=root,
    )
    pid = project["project_id"]
    store.projects[pid]["lifecycle_mode"] = "evergreen"
    store.projects[pid]["status"] = "evolving_cooldown"
    store.questions = [{"text": "should the tuner learn from latency?"}]
    store.db.failures = ["table missing"]

    result = await loop.propose_next_evolution(store.projects[pid])

    assert result.outcome.value == "step_done"
    assert store.projects[pid]["status"] == "active"
    assert store.projects[pid]["next_evolution_at"] is None
    children = [i for i in store.intentions.values() if i.root_intention_id == pid or i.project_id == pid]
    assert children, "the next cycle has a concrete DAG"
    kickoffs = [n for n in loop.governance.pending if n.event_type == "project_kickoff"]
    assert kickoffs, "the new evolution cycle gets its own kickoff report"
    assert "latency" in str(result.thoughts) or "latency" in kickoffs[-1].render_text()
