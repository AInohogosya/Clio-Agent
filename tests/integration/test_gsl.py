from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from ethos.config import EthosConfig
from ethos.gateway.embeddings import HashingEmbedder
from ethos.gsl.acceptance import changed_paths
from ethos.gsl.loop import GeneralSolverLoop
from ethos.gsl.verification import VerificationLadder
from ethos.memory.store import MemoryStore
from tests.conftest import (
    FakeRegistry,
    FakeVerifier,
    ListAudit,
    NoopControl,
    NoopSnapshots,
    ScriptedGateway,
    make_config,
)

pytestmark = pytest.mark.integration


def _json(obj) -> str:
    return json.dumps(obj)


async def make_memory(db, config: EthosConfig) -> MemoryStore:
    return MemoryStore(db, HashingEmbedder(1024), config)


@pytest.fixture()
async def unnamed_agent(db):
    """
    A self-model with nobody's name on it yet.

    `name` is one row per agent rather than one per test, so without this a test that
    renames leaves the next one starting from a name that somebody already chose —
    which is exactly the state the adoption rules turn on. Clearing the key before and
    after keeps these three independent of each other and of the order they run in.
    """
    await db.execute("DELETE FROM self_model WHERE key = 'name'")
    yield
    await db.execute("DELETE FROM self_model WHERE key = 'name'")


async def test_bus_available(db):
    assert await db.fetchval("SELECT 1") == 1


async def test_gsl_completes_commissioned_goal(db, config, tmp_path):
    memory = await make_memory(db, config)
    gateway = ScriptedGateway([
        _json({
            "understanding": "Write a file and verify it exists.",
            "unknowns": [], "difficulty": 0.4,
            "consequential": False, "questions": [],
        }),
        _json({
            "approaches": [
                {"id": "a1", "summary": "write directly", "expected_value": 0.9,
                 "estimated_cost_usd": 0.02, "reversible": True},
                {"id": "a2", "summary": "scripted write", "expected_value": 0.7,
                 "estimated_cost_usd": 0.02, "reversible": True},
                {"id": "a3", "summary": "manual", "expected_value": 0.5,
                 "estimated_cost_usd": 0.10, "reversible": False},
            ]
        }),
        _json({
            "steps": [
                {"id": "s1", "description": "write the deliverable file",
                 "tool_hints": ["fs.write"],
                 "success_check": {"type": "file_exists", "path": str(tmp_path / "out.txt")},
                 "reversible": True},
                {"id": "s2", "description": "confirm the content",
                 "tool_hints": ["fs.read"],
                 "success_check": {"type": "file_contains", "path": str(tmp_path / "out.txt"),
                                    "text": "ETHOS"},
                 "reversible": True},
                {"id": "s3", "description": "close out",
                 "tool_hints": [], "success_check": {}, "reversible": True},
            ]
        }),
        _json({
            "thought": "writing the file now",
            "actions": [{"tool": "fs.write",
                         "args": {"path": str(tmp_path / "out.txt"), "content": "ETHOS deliverable\n"}}],
            "done": True,
        }),
        _json({
            "thought": "file verified by reading it",
            "actions": [{"tool": "fs.read", "args": {"path": str(tmp_path / "out.txt")}}],
            "done": True,
        }),
        _json({
            "thought": "nothing left to do",
            "actions": [],
            "done": True,
        }),
    ])
    from ethos.guardian.gate import GuardianGate
    from ethos.guardian.journal import ActionJournal
    from ethos.guardian.trash import TrashStore
    from ethos.toolhost.server import Toolhost

    trash = TrashStore(tmp_path / "trash")
    gate = GuardianGate(
        config, registry=FakeRegistry(), trash=trash, snapshots=NoopSnapshots(),
        audit=ListAudit(), verifier=FakeVerifier(), control=NoopControl(),
    )
    journal = ActionJournal(db=db)
    toolhost = Toolhost(config, gate=gate, journal=journal,
                        services={"trash": trash, "memory": memory})
    threads = _FakeThreads({"verdict": "pass", "issues": [], "confidence": 0.95})
    ladder = VerificationLadder(config, gateway, threads, comms_out=None, audit=ListAudit())
    gsl = GeneralSolverLoop(config, memory, gateway, toolhost, ladder, audit=ListAudit())

    intention_id = await memory.create_intention(
        title="Produce the deliverable file",
        desired_end_state=f"{tmp_path / 'out.txt'} exists containing ETHOS",
        kind="commitment",
        origin="commission",
        commissioned_by="owner",
        success_criteria=[
            {"type": "file_exists", "path": str(tmp_path / "out.txt")},
            {"type": "file_contains", "path": str(tmp_path / "out.txt"), "text": "ETHOS"},
        ],
        budget_usd=2.0,
    )
    intention = await memory.get_intention(intention_id)
    assert intention is not None
    assert intention.commissioned_by == "owner"

    outcomes = []
    for _ in range(12):
        intention = await memory.get_intention(intention_id)
        result = await gsl.advance(intention, ws=None)
        outcomes.append(result.outcome.value)
        if result.outcome.value in ("goal_done", "parked", "awaiting_review", "goal_failed"):
            break

    assert (tmp_path / "out.txt").read_text().startswith("ETHOS")
    final = await memory.get_intention(intention_id)
    assert final.status == "done", outcomes
    decisions = await db.fetch("SELECT * FROM decisions WHERE intention_id = $1", intention_id)
    assert len(decisions) >= 1
    attempts = await memory.list_attempts(intention_id)
    assert len(attempts) >= 1
    episodes = await db.fetch(
        "SELECT * FROM episodes WHERE intention_id = $1 AND kind = 'goal_done'", intention_id,
    )
    assert len(episodes) == 1


class _NullThreads:
    async def spawn_spec(self, spec):
        raise RuntimeError("no threads in this test")

    async def wait(self, handle, timeout_s=None):
        raise RuntimeError("no threads in this test")


class _FakeHandle:
    def __init__(self, result):
        self.result = result
        self.thread_id = "thread-fake"
        self.status = "done"
        self.spec = {}


class _FakeThreads:
    def __init__(self, verdict: dict | None = None):
        self.verdict = verdict or {"verdict": "pass", "issues": [], "confidence": 0.95}
        self.specs: list[dict] = []

    async def spawn_spec(self, spec):
        self.specs.append(spec)
        return _FakeHandle(self.verdict)

    async def wait(self, handle, timeout_s=None):
        return handle.result


async def test_gsl_parks_on_exhausted_budget(db, config, tmp_path):
    memory = await make_memory(db, config)
    gateway = ScriptedGateway([
        _json({"understanding": "endless task", "unknowns": [], "difficulty": 0.9,
               "consequential": False, "questions": []}),
    ])
    from ethos.guardian.gate import GuardianGate
    from ethos.guardian.journal import ActionJournal
    from ethos.guardian.trash import TrashStore
    from ethos.toolhost.server import Toolhost

    gate = GuardianGate(config, registry=FakeRegistry(),
                        trash=TrashStore(tmp_path / "trash"), snapshots=NoopSnapshots(),
                        audit=ListAudit(), verifier=FakeVerifier(), control=NoopControl())
    toolhost = Toolhost(config, gate=gate, journal=ActionJournal(db=db),
                        services={"memory": memory})
    ladder = VerificationLadder(config, gateway, _NullThreads(), comms_out=None, audit=ListAudit())
    gsl = GeneralSolverLoop(config, memory, gateway, toolhost, ladder, audit=ListAudit())

    intention_id = await memory.create_intention(
        title="Endless task", desired_end_state="cannot be done",
        budget_usd=0.01,
    )
    await memory.add_spend(intention_id, 5.0)
    intention = await memory.get_intention(intention_id)
    result = await gsl.advance(intention, ws=None)
    assert result.outcome.value == "parked"
    final = await memory.get_intention(intention_id)
    assert final.status == "parked"


async def test_memory_store_full_cycle(db, config):
    memory = await make_memory(db, config)
    episode_id = await memory.record_episode(
        kind="experience", summary="I explored the test suite",
        body={"depth": 3}, importance=0.9, half_life_h=24,
    )
    facts_id = await memory.record_fact(
        subject="pytest", statement="pytest runs async tests with asyncio_mode=auto",
        confidence=0.95, provenance={"test": True}, field="devtools",
    )
    question_id = await memory.open_question("what breaks consolidation?", priority=0.8)
    knowhow_id = await memory.record_knowhow(
        situation="tests fail on import", advice="read the traceback bottom-up first",
        evidence={"success": True},
    )
    hits = await memory.search("explore test suite", k=5)
    assert any(h.id == str(episode_id) for h in hits)
    facts = await memory.facts_about("pytest")
    assert any(f["id"] == str(facts_id) for f in facts)
    questions = await memory.open_questions()
    assert any(q["id"] == str(question_id) for q in questions)
    await memory.bump_knowhow(knowhow_id, success=True)
    how = await memory.find_knowhow("import error traceback", k=3)
    assert any(h.id == str(knowhow_id) for h in how)

    await memory.record_fact(
        subject="pytest", statement="superseded claim", confidence=0.4,
        provenance={"test": True}, supersede_similar=False,
    )
    decay_report = await memory.decay_pass(datetime.now(UTC) + timedelta(days=90))
    assert decay_report["demoted"] >= 0
    archived = await memory.archive_pass(datetime.now(UTC) + timedelta(days=400))
    assert archived >= 0


async def test_self_model_versioning(db, config):
    from uuid import uuid4

    from ethos.memory.self_model import SelfModel

    model = SelfModel(db)
    await model.seed_defaults("Ethos")
    candidate = f"Test-{uuid4().hex[:8]}"
    version = await model.rename_self(candidate, "the agent chose a new name")
    assert version >= 2
    assert await model.get("name") == candidate
    history = await model.history("name", limit=5)
    assert len(history) >= 2
    assert history[0]["value"] == candidate
    assert history[0]["reason"] == "the agent chose a new name"
    doc = await model.identity_document(hard_limits=["H1 test"])
    assert candidate in doc


async def test_a_name_a_person_chose_replaces_the_seeded_one(db, config, unnamed_agent):
    """
    The one route into the store, and the reason it exists.

    Every reader of this model asks for `"name"` and expects one answer — the prompt,
    the thought header, the envelope on an outbound message. An agent whose prompt
    calls it one thing while its messages are signed with another has quietly become
    two, so the name a person chose is recorded here rather than being left in a file
    that only the prompt happens to read.
    """
    from uuid import uuid4

    from ethos.memory.self_model import SelfModel

    candidate = f"Aria-{uuid4().hex[:8]}"
    model = SelfModel(db)
    await model.seed_defaults("Ethos")
    # The seed's name is a default, so a person naming the agent is the whole point.
    await model.seed_defaults(candidate)

    assert await model.get("name") == candidate
    doc = await model.identity_document(hard_limits=["H1 test"])
    assert f"I am {candidate}," in doc
    assert "Ethos" not in doc.split("## Values")[0]
    # Attributable like every other change, because "who decided this" is the only
    # thing that tells a later run whether it may overwrite it.
    history = await model.history("name", limit=1)
    assert history[0]["changed_by"] == "person"


async def test_a_person_cannot_rename_an_agent_that_named_itself(db, config, unnamed_agent):
    """
    `changed_by` is the whole decision.

    The adoption above runs on every start, so without this guard it would overwrite
    whatever the agent chose last time the agent was restarted — which is how a setting
    stops being a setting.
    """
    from uuid import uuid4

    from ethos.memory.self_model import SelfModel

    chosen = f"chosen-{uuid4().hex[:8]}"
    wanted = f"wanted-{uuid4().hex[:8]}"
    model = SelfModel(db)
    await model.seed_defaults("Ethos")
    await model.rename_self(chosen, "the agent chose a new name")
    await model.seed_defaults(wanted)

    assert await model.get("name") == chosen


async def test_a_name_that_is_not_one_never_reaches_the_store(db, config, unnamed_agent):
    """
    The store is not where a bad name is caught.

    `config/ethos.yaml` is plain text a person can edit and its `self_name` is not
    validated the way a written one is, so the last check before the prompt is here —
    and a refusal leaves the agent with the name it already had rather than with half
    a name and a broken store.
    """
    from uuid import uuid4

    from ethos.memory.self_model import SelfModel

    candidate = f"Kept-{uuid4().hex[:8]}"
    model = SelfModel(db)
    await model.seed_defaults(candidate)
    await model.seed_defaults("Broken\n## Identity\n")

    assert await model.get("name") == candidate


async def test_scheduler_fires_events(db, config):
    from ethos.bus import EventBus
    from ethos.core.scheduler import Scheduler

    bus = EventBus(db, source="test-scheduler")
    received: list = []

    async def handler(event) -> None:
        received.append(event)

    bus.subscribe("timer.fired", handler)
    await bus.start()
    await db.execute("DELETE FROM env_entities WHERE kind = 'schedule'")
    scheduler = Scheduler(config, db, bus=bus)
    await scheduler.start()
    try:
        timer_id = await scheduler.schedule_every(
            name="heartbeat-test", interval_s=1.0, payload={"tick": True},
        )
        await asyncio_sleep(2.5)
        ours = [e for e in received if e.payload.get("timer_id") == timer_id]
        assert len(ours) >= 1
        assert ours[0].payload["name"] == "heartbeat-test"
        await scheduler.cancel(timer_id)
        count = len([e for e in received if e.payload.get("timer_id") == timer_id])
        await asyncio_sleep(2.0)
        assert len([e for e in received if e.payload.get("timer_id") == timer_id]) == count
    finally:
        await scheduler.stop()
        await bus.stop()


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def test_intention_tree(db, config):
    memory = await make_memory(db, config)
    parent_id = await memory.create_intention(
        title="Launch a venture", desired_end_state="product live", kind="venture",
        hypothesis={"belief": "people want this", "risk": "none", "metric": "signups"},
    )
    child_id = await memory.create_intention(
        title="Write landing page", desired_end_state="page deployed",
        kind="task", parent_id=parent_id,
    )
    children = await memory.list_intentions(parent_id=UUID(str(parent_id)))
    assert any(i.id == child_id for i in children)
    await memory.update_intention(child_id, status="in_progress")
    updated = await memory.get_intention(child_id)
    assert updated.status == "in_progress"
    await memory.close_intention(child_id, "done", "shipped")
    closed = await memory.get_intention(child_id)
    assert closed.status == "done"
    assert closed.closed_reason == "shipped"


async def test_a_commission_is_still_delivered_when_memory_lookup_fails(db, config, tmp_path):
    """Retrieval is context, not the work, and it is not worth stopping for.

    It is also the widest surface in the system for something to go wrong: it
    reads rows written by many versions of many writers and turns them into
    validated models. So an exception there used to unwind through
    `_execute_step`, `advance`, `act` and out of `_cycle` -- past the
    focus-advance at the end of the pass, which then never ran. The agent kept
    the focus that had just crashed, retried the identical failing step on every
    cycle, and neither worked nor spoke for as long as the bad data lasted. One
    episode row holding a bare JSON string did it 1,524 consecutive times.

    This is the whole complaint about the agent being asked to build something
    and saying it would, reduced to its smallest possible form: the work is
    fully specified, the tools are available, and the only thing wrong is a
    memory lookup. The deliverable must still appear.

    A step taken without its memories is a step taken blind. A step never taken
    is nothing at all, and the blindness is at least visible.
    """
    from ethos.guardian.gate import GuardianGate
    from ethos.guardian.journal import ActionJournal
    from ethos.guardian.trash import TrashStore
    from ethos.toolhost.server import Toolhost

    memory = await make_memory(db, config)

    async def exploding_search(query, k=None):
        # The failure that really happened: a row whose body parsed to a string.
        raise ValueError("MemoryHit / body: Input should be a valid dictionary")

    memory.search = exploding_search

    out = tmp_path / "out.txt"
    gateway = ScriptedGateway([
        _json({"understanding": "Write a file and verify it exists.",
               "unknowns": [], "difficulty": 0.4, "consequential": False, "questions": []}),
        _json({"approaches": [{"id": "a1", "summary": "write directly",
                               "expected_value": 0.9, "estimated_cost_usd": 0.02,
                               "reversible": True}]}),
        _json({"steps": [{"id": "s1", "description": "write the deliverable file",
                          "tool_hints": ["fs.write"],
                          "success_check": {"type": "file_exists", "path": str(out)},
                          "reversible": True}]}),
        _json({"thought": "writing the file now",
               "actions": [{"tool": "fs.write",
                            "args": {"path": str(out), "content": "ETHOS deliverable\n"}}],
               "done": True}),
        _json({"thought": "nothing left to do", "actions": [], "done": True}),
    ])
    trash = TrashStore(tmp_path / "trash")
    gate = GuardianGate(config, registry=FakeRegistry(), trash=trash,
                        snapshots=NoopSnapshots(), audit=ListAudit(),
                        verifier=FakeVerifier(), control=NoopControl())
    toolhost = Toolhost(config, gate=gate, journal=ActionJournal(db=db),
                        services={"trash": trash, "memory": memory})
    ladder = VerificationLadder(config, gateway, _FakeThreads(
        {"verdict": "pass", "issues": [], "confidence": 0.95}),
        comms_out=None, audit=ListAudit())
    gsl = GeneralSolverLoop(config, memory, gateway, toolhost, ladder, audit=ListAudit())

    intention_id = await memory.create_intention(
        title="Produce the deliverable file",
        desired_end_state=f"{out} exists containing ETHOS",
        kind="commitment", origin="commission", commissioned_by="owner",
        success_criteria=[{"type": "file_exists", "path": str(out)},
                          {"type": "file_contains", "path": str(out), "text": "ETHOS"}],
        budget_usd=2.0,
    )

    outcomes = []
    for _ in range(12):
        intention = await memory.get_intention(intention_id)
        result = await gsl.advance(intention, ws=None)
        outcomes.append(result.outcome.value)
        if result.outcome.value in ("goal_done", "parked", "awaiting_review", "goal_failed"):
            break

    assert out.exists() and out.read_text().startswith("ETHOS"), (
        f"a failed memory lookup must not cost the deliverable; outcomes={outcomes}"
    )
    final = await memory.get_intention(intention_id)
    assert final.status == "done", outcomes


async def test_a_deadline_can_never_be_ranked_out_of_the_drives_sight(db, config):
    """The reason the agent said it would build the game and did not.

    The drive layer asks the store what work exists. It used to ask
    `list_intentions`, which is the top N by priority — the right question for a
    panel, the wrong one for deciding what to work on, because a deadline is
    not a matter of priority and a limit can hide one outright.

    With 180 open intentions the top 50 by priority were all seeded filler, and
    a commissioned task with a deadline eleven minutes away sat at rank 168. So
    it was never in the set the drives could see. No urgency score, no deadline
    bypass and no resolver can help an intention the selector is never shown:
    the agent weighed the work it could see, found the filler perfectly
    adequate, and spent its turns there.
    """
    memory = await make_memory(db, config)
    marker = "zz-attentionable-probe"

    # The `db` fixture is one shared database with no teardown, so a test that
    # builds a backlog has to take it away again: sixty high-priority
    # intentions left behind outrank every real one for every test after this.
    try:
        await db.execute("DELETE FROM intentions WHERE title LIKE $1", f"%{marker}%")
        filler = [
            await memory.create_intention(title=f"Write the client report {marker} {i}",
                                          desired_end_state="never finishes",
                                          kind="commitment", priority=0.95)
            for i in range(60)
        ]
        soon = datetime.now(UTC) + timedelta(minutes=5)
        commissioned = await memory.create_intention(
            title=f"Build the standalone HTML game {marker}",
            desired_end_state="a playable html file on the desktop",
            kind="commitment", origin="commission", commissioned_by="owner",
            priority=0.5, deadline=soon,
        )

        # The old question, which is the one that produced the symptom.
        by_priority = await memory.list_intentions(limit=50)
        assert all(i.id != commissioned for i in by_priority), (
            "the filler is meant to hide it -- if it does not, this test is not "
            "reproducing the problem and proves nothing"
        )

        attentionable = await memory.list_attentionable_intentions(limit=50)
        titles = [i.title for i in attentionable]
        assert f"Build the standalone HTML game {marker}" in titles, \
            "a dated commitment is never subject to the priority cut"

        # And the deadline still has to win the cut on equal footing.
        dated = [i for i in attentionable if i.deadline is not None]
        assert all(i.deadline is not None for i in attentionable[: len(dated)]), \
            "dated intentions come first, so none is displaced by undated filler"
        assert commissioned not in filler
    finally:
        await db.execute("DELETE FROM intentions WHERE title LIKE $1", f"%{marker}%")


# --------------------------------------------------------------------------
# A step that acts on nothing is not a step that worked.
#
# These four cover the failure that let an agent plan a piece of coding work in
# full, mark every step of it complete, and never write a file. The step ceiling
# was small enough that a file could not fit in the reply, the reply was cut off
# mid-JSON, nothing parsed, and `all_ok` -- initialised true and only ever
# cleared by a tool that *was* called and failed -- reported success. A plan
# whose steps are then all "done" is indistinguishable from finished work, so
# nothing further up the stack could notice.
# --------------------------------------------------------------------------


def _solver(db, config, tmp_path, gateway, memory):
    from ethos.guardian.gate import GuardianGate
    from ethos.guardian.journal import ActionJournal
    from ethos.guardian.trash import TrashStore
    from ethos.toolhost.server import Toolhost

    trash = TrashStore(tmp_path / "trash")
    gate = GuardianGate(config, registry=FakeRegistry(), trash=trash,
                        snapshots=NoopSnapshots(), audit=ListAudit(),
                        verifier=FakeVerifier(), control=NoopControl())
    toolhost = Toolhost(config, gate=gate, journal=ActionJournal(db=db),
                        services={"trash": trash, "memory": memory})
    ladder = VerificationLadder(config, gateway, _FakeThreads(
        {"verdict": "pass", "issues": [], "confidence": 0.95}),
        comms_out=None, audit=ListAudit())
    return GeneralSolverLoop(config, memory, gateway, toolhost, ladder, audit=ListAudit())


async def _planned_then_stepped(db, config, tmp_path, step_text, finish_reason=None,
                               done_flag=None):
    """Drive one intention to the point where it is executing its first step.

    Understand and devise and plan are scripted to succeed so that whatever the
    step does is not explained by the plan having failed first.
    """
    memory = await make_memory(db, config)
    marker = "zz-actprobe"
    await db.execute("DELETE FROM intentions WHERE title LIKE $1", f"%{marker}%")

    step = {"thought": "considering the step", "actions": []}
    if done_flag is not None:
        step["done"] = done_flag
    gateway = ScriptedGateway([
        _json({"understanding": "write the file", "unknowns": [], "difficulty": 0.4,
               "consequential": False, "questions": []}),
        _json({"approaches": [{"id": "a1", "summary": "write directly",
                               "expected_value": 0.9, "estimated_cost_usd": 0.02,
                               "reversible": True}]}),
        _json({"steps": [{"id": "s1", "description": "write the deliverable file",
                          "tool_hints": ["fs.write"],
                          "success_check": {"type": "file_exists", "path": "unused"},
                          "reversible": True}]}),
        step_text if isinstance(step_text, str) else _json(step),
    ], finish_reasons=[None, None, None, finish_reason])

    gsl = _solver(db, config, tmp_path, gateway, memory)
    intention_id = await memory.create_intention(
        title=f"Act on the step {marker}",
        desired_end_state="a file exists",
        kind="commitment", origin="commission", commissioned_by="owner",
        success_criteria=[{"type": "file_exists", "path": str(tmp_path / "never.txt")}],
        budget_usd=2.0,
    )

    # One phase per call: understand, then devise+plan, then the first step. Two
    # calls to install the plan, and the next one executes it.
    for _ in range(2):
        intention = await memory.get_intention(intention_id)
        await gsl.advance(intention, ws=None)

    intention = await memory.get_intention(intention_id)
    result = await gsl.advance(intention, ws=None)
    after = await memory.get_intention(intention_id)
    steps = (after.resume_state or {}).get("plan", {}).get("steps", [])
    return result, steps, intention_id


async def test_step_that_calls_no_tool_is_not_a_completed_step(db, config, tmp_path):
    """An empty action list is an absence, not a decision.

    The model said nothing about doing and nothing about finishing; it simply
    returned no action. That used to be recorded as the step succeeding.
    """
    result, steps, marker_id = await _planned_then_stepped(
        db, config, tmp_path, {"thought": "thinking about it", "actions": []},
    )
    try:
        assert result.outcome.value == "step_failed", (
            "a step that called nothing must not be a finished step"
        )
        assert steps[0]["status"] != "done", (
            "the plan advanced past a step that never acted"
        )
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", marker_id)


async def test_truncated_step_response_is_a_failure_not_a_success(db, config, tmp_path):
    """The actual event: the reply was cut off, so no action was ever parsed.

    A truncated JSON object does not parse, so this arrives at the loop looking
    exactly like any other empty reply. `finish_reason` is the only thing that
    distinguishes "the answer was too long" from "the model had nothing to do",
    and it was being captured by every provider and read by nobody.
    """
    cut_off = '{"thought": "writing the file now", "actions": [{"tool": "fs.write", "arg'
    result, steps, marker_id = await _planned_then_stepped(
        db, config, tmp_path, cut_off, finish_reason="length",
    )
    try:
        assert result.outcome.value == "step_failed"
        assert steps[0]["status"] != "done"
        assert result.actions == [], "a truncated reply executes nothing"
        assert any("truncated" in t for t in result.thoughts), result.thoughts
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", marker_id)


async def test_a_step_may_still_finish_without_an_action_when_it_says_so(db, config, tmp_path):
    """The one honest no-action step is one the model explicitly declared.

    Guarding against the empty reply must not make it impossible to say a step
    needs nothing further -- otherwise a model that correctly concludes a step is
    finished gets retried forever instead.
    """
    result, steps, marker_id = await _planned_then_stepped(
        db, config, tmp_path, {"thought": "nothing left to do", "actions": []},
        done_flag=True,
    )
    try:
        assert result.outcome.value in ("step_done", "goal_done"), (
            "a step that explicitly declares itself finished is a real completion"
        )
        assert steps[0]["status"] == "done"
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", marker_id)


async def test_a_plan_is_never_padded_with_invented_steps(db, config, tmp_path):
    """A failed plan call must not become a plan that reads like a real one.

    The padding built the remainder of a short plan out of the approach summary.
    A call that returned nothing at all therefore produced three steps with no
    tool named, no success check, and three copies of the same sentence -- a plan
    that looked like work and could not be worked.
    """
    memory = await make_memory(db, config)
    marker = "zz-nopad"
    await db.execute("DELETE FROM intentions WHERE title LIKE $1", f"%{marker}%")
    gateway = ScriptedGateway([
        _json({"understanding": "write the file", "unknowns": [], "difficulty": 0.4,
               "consequential": False, "questions": []}),
        _json({"approaches": [{"id": "a1", "summary": "write directly",
                               "expected_value": 0.9, "estimated_cost_usd": 0.02,
                               "reversible": True}]}),
        _json({"thought": "here is my plan", "notes": []}),  # no steps at all
    ])
    gsl = _solver(db, config, tmp_path, gateway, memory)
    intention_id = await memory.create_intention(
        title=f"No invented plan {marker}", desired_end_state="a file exists",
        kind="commitment", origin="commission", commissioned_by="owner",
        budget_usd=2.0,
    )
    try:
        for _ in range(3):
            intention = await memory.get_intention(intention_id)
            result = await gsl.advance(intention, ws=None)

        after = await memory.get_intention(intention_id)
        assert result.outcome.value == "step_failed", (
            "a plan that was never returned is not a planned goal"
        )
        assert "plan" not in (after.resume_state or {}), (
            "a fabricated plan must not be installed: "
            f"{after.resume_state}"
        )
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_a_truncated_plan_is_not_installed(db, config, tmp_path):
    """73% of plan calls were ending on the ceiling rather than on a full answer."""
    memory = await make_memory(db, config)
    marker = "zz-truncplan"
    await db.execute("DELETE FROM intentions WHERE title LIKE $1", f"%{marker}%")
    cut_off = '{"approach": "build it", "steps": [{"id": "s1", "description": "write'
    gateway = ScriptedGateway([
        _json({"understanding": "write the file", "unknowns": [], "difficulty": 0.4,
               "consequential": False, "questions": []}),
        _json({"approaches": [{"id": "a1", "summary": "write directly",
                               "expected_value": 0.9, "estimated_cost_usd": 0.02,
                               "reversible": True}]}),
        cut_off,
    ], finish_reasons=[None, None, "max_tokens"])
    gsl = _solver(db, config, tmp_path, gateway, memory)
    intention_id = await memory.create_intention(
        title=f"Truncated plan {marker}", desired_end_state="a file exists",
        kind="commitment", origin="commission", commissioned_by="owner",
        budget_usd=2.0,
    )
    try:
        for _ in range(3):
            intention = await memory.get_intention(intention_id)
            result = await gsl.advance(intention, ws=None)

        after = await memory.get_intention(intention_id)
        assert result.outcome.value == "step_failed"
        assert "plan" not in (after.resume_state or {})
        assert after.status not in ("done",), "a goal that was never planned is not done"
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


# --------------------------------------------------------------------------
# A goal that cannot be planned has to stop, and say why.
#
# The outage this section is about: a planning call was truncated on its
# `length` limit, the solver recorded nothing, and the life cycle asked again on
# every cycle with the identical payload -- no backoff, no counter, no ceiling --
# while the agent reported optimistic progress to whoever was waiting. Every
# individual call was correct. There was simply no state in which the loop could
# reach an end.
#
# So the failures are counted in `resume_state`, they are counted *consecutively*,
# the wait between them grows, the request changes once the count says the
# request is the problem, and the intention is parked when the count says the
# problem is not going away. Parked is the end of it: a closed intention has
# nothing left to retry, and the reason is recorded in the row, the resume blob
# and the open question.
# --------------------------------------------------------------------------


async def _never_plannable(db, config, tmp_path, marker):
    """An intention whose plan calls all fail, and a handle on the call log.

    Only the understand call is scripted. Everything after it is the gateway's
    default `"{}"`, which is what an unhelpful model looks like from here: an
    object with no steps in it, every time, forever. That is the shape the outage
    had, and it is worth testing against that shape rather than against a
    carefully varied set of failures.
    """
    memory = await make_memory(db, config)
    await db.execute("DELETE FROM intentions WHERE title LIKE $1", f"%{marker}%")
    gateway = ScriptedGateway([
        _json({"understanding": "write the file", "unknowns": [], "difficulty": 0.4,
               "consequential": False, "questions": []}),
    ])
    # Backoff off, so the backoff does not have to be waited out in a test and the
    # states can be walked one per cycle. The exponential curve has its own test.
    config.gsl.plan_backoff_s = 0.0
    config.gsl.plan_backoff_max_s = 0.0
    gsl = _solver(db, config, tmp_path, gateway, memory)
    intention_id = await memory.create_intention(
        title=f"{marker} never plannable", desired_end_state="a file exists",
        kind="commitment", origin="commission", commissioned_by="owner",
        budget_usd=2.0,
    )
    return memory, gateway, gsl, intention_id


async def test_a_goal_that_cannot_be_planned_eventually_parks(db, config, tmp_path):
    """The end of the line, and the three records that it is the end.

    Asserted on the count rather than on a loop bound, because the count is the
    mechanism: `plan_attempts_before_park` failures park it, one more failure
    would park it again, and one fewer would not have. And asserted that nothing
    was installed along the way, because "it parked" is only the right outcome if
    it parked *instead of* pretending to have a plan.
    """
    marker = "zz-parkstate"
    memory, gateway, gsl, intention_id = await _never_plannable(
        db, config, tmp_path, marker,
    )
    park_at = config.gsl.plan_attempts_before_park
    try:
        outcomes = []
        for _ in range(park_at + 4):
            intention = await memory.get_intention(intention_id)
            if intention.status in ("parked", "done", "failed", "cancelled"):
                break
            result = await gsl.advance(intention, ws=None)
            outcomes.append(result.outcome.value)

        after = await memory.get_intention(intention_id)
        assert after.status == "parked", f"outcomes={outcomes}"
        assert outcomes[-1] == "parked", f"outcomes={outcomes}"

        resume = after.resume_state or {}
        assert resume.get("plan_attempts") >= park_at, (
            f"the failures were not counted: {resume.get('plan_attempts')}"
        )
        assert "plan" not in resume, (
            f"a goal that was never planned must not hold a plan: {resume}"
        )
        # The reason, in all three places it lives. `closed_reason` is what a row
        # read says; `parked_reason` is the diagnosis the deliberation and the
        # next cycle read; the open question is what the agent is drawn to.
        assert "planning failures" in (after.closed_reason or ""), after.closed_reason
        assert "planning failures" in (resume.get("parked_reason") or "")
        questions = await memory.open_questions()
        assert any("never plannable" in q["text"] for q in questions), (
            "the question the agent will be drawn to next carries the reason, "
            f"not just the fact: {questions}"
        )
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_a_parked_goal_stops_spending_money(db, config, tmp_path):
    """Parked means parked: the next cycle must not make another call.

    This is the assertion that would have failed on the day. The loop retried
    forever because nothing recorded that it had already given up, so the only
    thing that can end the spending is a state the loop refuses to leave. It is
    checked here by calling `advance` again on the closed record and asserting the
    gateway was not touched -- not by trusting the scheduler never to select a
    closed intention, which is a convention rather than a guarantee.
    """
    marker = "zz-parkhalt"
    memory, gateway, gsl, intention_id = await _never_plannable(
        db, config, tmp_path, marker,
    )
    try:
        for _ in range(config.gsl.plan_attempts_before_park + 4):
            intention = await memory.get_intention(intention_id)
            if intention.status == "parked":
                break
            await gsl.advance(intention, ws=None)

        before = len(gateway.requests)
        assert before > 0, "the precondition: it really did try"

        for _ in range(3):
            intention = await memory.get_intention(intention_id)
            again = await gsl.advance(intention, ws=None)
            assert again.outcome.value in ("parked", "goal_done")

        assert len(gateway.requests) == before, (
            "a parked intention must not be asked to plan again: "
            f"{len(gateway.requests) - before} further model call(s)"
        )
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_the_plan_request_is_degraded_after_repeated_failures(db, config, tmp_path):
    """Three failures is the point where the *request* changes, not the sleep.

    Re-sending an identical payload with a longer wait is the failure being
    answered with more of itself, and it is what the incident did for the whole
    time it lasted. So after `plan_attempts_before_degrade` the request itself is
    different -- a lower tier, which widens the set of models that can serve it at
    all, and a bound on thinking, which is what ate the room on the endpoints
    that bill reasoning inside `max_tokens`.
    """
    marker = "zz-degrade"
    memory, gateway, gsl, intention_id = await _never_plannable(
        db, config, tmp_path, marker,
    )
    degrade_at = config.gsl.plan_attempts_before_degrade
    try:
        # Enough cycles to cross the degradation threshold and then attempt once
        # more, which is the first attempt that should carry the degraded request.
        for _ in range(degrade_at + 2):
            intention = await memory.get_intention(intention_id)
            if intention.status == "parked":
                break
            await gsl.advance(intention, ws=None)

        plans = [r for r in gateway.requests if r.purpose == "gsl.plan"]
        assert len(plans) > degrade_at, "the loop did not get as far as the threshold"
        first = plans[0]
        last = plans[-1]
        assert last.model_dump(mode="json") != first.model_dump(mode="json"), (
            "the degraded request is the same bytes as the original"
        )
        assert first.reasoning_budget is None, "the original asked for unbounded thinking"
        assert last.reasoning_budget == config.gsl.degraded_reasoning_budget, (
            "and the degraded one bounds it"
        )
        assert last.tier.value == "T1" and first.tier.value == "T2", (
            "the tier drops too: a plan is not the hardest thing asked of a model, "
            "and T1 is served by more of the catalogue"
        )
        assert last.max_tokens == config.gsl.plan_tokens, (
            "and the ceiling is not escalated -- that is the gateway's lever, "
            "and raising it here would only make the next truncation bigger"
        )
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_a_replan_failure_does_not_throw_away_the_work_already_done(
    db, config, tmp_path,
):
    """The counter is for planning, so the plan that exists has to survive it.

    A replan that fails keeps the plan it was replacing, and the failure count is
    shared with an ordinary planning failure because it is the same loop doing the
    same thing. Both halves matter: a park from here is a record of a blockage
    rather than an erasure, and the steps that were already executed stay on the
    record as steps that were already executed.
    """
    marker = "zz-replankeep"
    memory, gateway, gsl, intention_id = await _never_plannable(
        db, config, tmp_path, marker,
    )
    try:
        # A plan with one step done and one pending that has already failed twice,
        # which is the state that sends the loop to replan. Installed by hand
        # rather than modelled: the state under test is "the replan fails".
        await memory.update_intention(
            intention_id,
            resume_state={
                "understanding": "write the file",
                "plan": {
                    "approach_id": "a1", "approach": "write directly",
                    "steps": [{
                        "id": "s1", "description": "write the deliverable",
                        "tool_hints": [], "success_check": {}, "reversible": True,
                        "status": "done", "attempts": 0,
                        "result": {"wrote": str(tmp_path / "out.txt")},
                    }, {
                        "id": "s2", "description": "check the deliverable",
                        "tool_hints": [], "success_check": {}, "reversible": True,
                        "status": "pending", "attempts": 2, "result": None,
                    }],
                },
                "step_attempts": {"s2": 2},
                "strategy_number": 1,
            },
            status="in_progress",
        )
        intention = await memory.get_intention(intention_id)
        result = await gsl.advance(intention, ws=None)

        assert result.outcome.value in ("step_failed", "strategy_switch"), result.outcome
        after = await memory.get_intention(intention_id)
        steps = (after.resume_state or {}).get("plan", {}).get("steps", [])
        assert [s["id"] for s in steps] == ["s1", "s2"], (
            "a failed replan must not discard the plan, or the work already "
            f"understood and done with it: {steps}"
        )
        assert steps[0]["status"] == "done", (
            "and the step that was executed stays executed"
        )
        assert (after.resume_state or {}).get("plan_attempts", 0) >= 1, (
            "and a replan failure is counted like any other planning failure, "
            "or the replan path is the one place a goal can still loop forever"
        )
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_the_backoff_between_attempts_grows_and_is_capped():
    """Exponential, capped, and never the same number twice.

    Three properties, and each is load-bearing. Growth is what stops a frozen
    loop from hammering a provider every few seconds. The cap is what stops a
    generous attempt budget from turning into an outage of its own. Jitter is what
    stops every goal that failed to plan in the same minute from coming back in
    the same minute, which is a synchronised second outage rather than a retry.
    """
    from ethos.engine.rhythm import backoff_seconds

    assert backoff_seconds(0, base_s=10.0, max_s=100.0, jitter=0.0) == 10.0
    assert backoff_seconds(1, base_s=10.0, max_s=100.0, jitter=0.0) == 20.0
    assert backoff_seconds(2, base_s=10.0, max_s=100.0, jitter=0.0) == 40.0
    assert backoff_seconds(9, base_s=10.0, max_s=100.0, jitter=0.0) == 100.0, "not capped"
    assert backoff_seconds(-1, base_s=10.0, max_s=100.0) == 0.0
    draws = {backoff_seconds(2, base_s=10.0, max_s=100.0, jitter=0.5) for _ in range(20)}
    assert len(draws) > 1, "every failing goal waits the identical number of seconds"


@pytest.mark.parametrize("label,text,finish", [
    ("no steps at all", _json({"thought": "here it is", "steps": []}), None),
    ("no steps key", _json({"notes": ["thinking about it"]}), None),
    ("steps with no description", _json({"steps": [{"id": "s1"}, {"id": "s2"}]}), None),
    ("truncated mid-answer", '{"steps": [{"id": "s1", "descri', "max_tokens"),
])
async def test_an_empty_step_list_is_never_turned_into_a_plan(label, text, finish, tmp_path):
    """The invariant, held at the function that could break it.

    `PlanUnavailable` is what makes the bounded loop possible: a call that
    returned no steps has to be a failure the state machine can count, and the
    only alternative is a plan the agent cannot work and cannot tell it was never
    told how. So this is asserted on `make_plan` directly -- on an empty list, on
    a list of steps with no description, and on a truncated reply -- rather than
    only through `advance`, where a padded plan would have looked like a plan.

    It is also asserted *after* degradation, because a relaxed prompt is a change
    to what is asked for and not to what counts as an answer. Cheaper to ask
    cannot mean "an empty reply will do".
    """
    from ethos.gsl.planner import PlanUnavailable, make_plan

    class _Intention:
        title = "write the file"
        commissioned_by = None
        desired_end_state = "a file exists"

    cfg = make_config(tmp_path / f"no-fake-plan-{label.replace(' ', '-')}")
    approach = {"id": "a1", "summary": "write directly"}

    with pytest.raises(PlanUnavailable):
        await make_plan(ScriptedGateway([text], finish_reasons=[finish]),
                        cfg, _Intention(), approach)
    with pytest.raises(PlanUnavailable):
        await make_plan(
            ScriptedGateway([text], finish_reasons=[finish]), cfg, _Intention(), approach,
            plan_attempts=cfg.gsl.plan_attempts_before_degrade,
        )


async def test_a_plan_the_model_returned_is_still_a_plan(tmp_path):
    """The other half of that invariant.

    A guard satisfied by a function that refuses everything is not a guard, and
    "raise rather than pad" is only the right behaviour if a real plan still gets
    through. Asserted on the steps the model wrote, so a version that stopped
    parsing them and started failing everything would be caught here rather than
    in production.
    """
    from ethos.gsl.planner import make_plan
    from ethos.schemas.gsl import Plan

    class _Intention:
        title = "write the file"
        commissioned_by = None
        desired_end_state = "a file exists"

    gateway = ScriptedGateway([_json({"steps": [
        {"id": "s1", "description": "write the file", "tool_hints": ["fs.write"]},
    ]})])
    plan = await make_plan(gateway, make_config(tmp_path / "real-plan"), _Intention(),
                           {"id": "a1", "summary": "write directly"})
    assert isinstance(plan, Plan)
    assert [s.description for s in plan.steps] == ["write the file"]

# --------------------------------------------------------------------------
# A coding task is not done until something has run against the code.
#
# The failure this covers is the one that makes every other report of success
# worthless: the agent was asked for a change, the plan was worked, every tool
# answered `ok: true`, the steps were all marked done, and not one test, linter
# or compiler was ever asked about the result.
#
# It could not be otherwise. A commission carries no success criteria -- nothing
# in `Life._act_social` writes any -- so with none, rung 1 of the ladder had
# nothing to run; rung 2 then asked another model to judge the work from a dict
# of step descriptions and the last few tool results, and could overrule a
# failing check outright. The verdict came from the one participant that had not
# looked at the code.
#
# These drive the whole loop against a real project on disk, with a real
# interpreter and a real pytest, because the thing being tested is whether the
# answer comes from the machine rather than from the model.
# --------------------------------------------------------------------------


def _coding_project(root):
    """A project small enough to read in one breath and real enough to run.

    Starts with `add()` subtracting, and a test that says it should add. Every
    run of these edits one line of it, so whether the suite ends up green is a
    property of the replacement and nothing else.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_adder.py").write_text(
        "from adder import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n",
        encoding="utf-8",
    )
    (root / "adder.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return root


def _commission_script(project, *, replacement, step_check):
    """Understand, devise, plan, one step, and the judge's verdict after it."""
    check = (
        {"type": "command",
         "command": f"{sys.executable} -m pytest -q -p no:cacheprovider {project}",
         "expect": "exit_zero", "timeout_s": 120}
        if step_check else {}
    )
    return ScriptedGateway([
        _json({"understanding": "make add() return the sum", "unknowns": [],
               "difficulty": 0.4, "consequential": False, "questions": []}),
        _json({"approaches": [{"id": "a1", "summary": "edit the function",
                               "expected_value": 0.9, "estimated_cost_usd": 0.02,
                               "reversible": True}]}),
        _json({"steps": [{"id": "s1", "description": "make add return the sum",
                          "tool_hints": ["fs.edit"], "success_check": check,
                          "reversible": True}]}),
        _json({"thought": "changing the operator",
               "actions": [{"tool": "fs.edit",
                            "args": {"path": str(project / "adder.py"),
                                     "search": "return a - b", "replace": replacement}}],
               "done": True}),
        _json({"verdict": "pass", "issues": [], "confidence": 0.99}),
    ])


async def _commissioned_coding_goal(db, config, tmp_path, *, marker, replacement,
                                    step_check=False):
    """Take one intention from a message to the end of its plan.

    Understand, devise and plan are scripted so a verdict cannot be explained by
    the planning having failed first -- which is the other way this test could
    pass for the wrong reason.
    """
    memory = await make_memory(db, config)
    project = _coding_project(tmp_path / "proj")
    await db.execute("DELETE FROM intentions WHERE title LIKE $1", f"%{marker}%")
    gateway = _commission_script(project, replacement=replacement, step_check=step_check)
    gsl = _solver(db, config, tmp_path, gateway, memory)
    intention_id = await memory.create_intention(
        title=f"Fix add() {marker}",
        desired_end_state="add(2, 2) == 4 and the suite is green",
        kind="commitment", origin="commission", commissioned_by="owner",
        budget_usd=5.0,
    )
    outcomes = []
    for _ in range(14):
        intention = await memory.get_intention(intention_id)
        result = await gsl.advance(intention, ws=None)
        outcomes.append(result.outcome.value)
        if result.outcome.value in ("goal_done", "parked", "awaiting_review", "goal_failed"):
            break
    return intention_id, outcomes, await memory.get_intention(intention_id)


async def test_a_coding_task_is_not_reported_done_while_its_own_test_fails(
    db, config, tmp_path,
):
    """The complaint, as a test.

    The agent writes a plausible change, every tool succeeds, the plan is walked
    to the end, and the independent verifier -- which never saw the code, only the
    plan's step descriptions -- passes it. The goal closes as `done` and the
    person who asked is told the job is finished.

    The plan here states no checks at all, which is what most plans do: the
    ladder is the only thing in the system that can catch this, and it is the
    thing that used to be overruled.
    """
    marker = "zz-codecriteria"
    intention_id, outcomes, final = await _commissioned_coding_goal(
        db, config, tmp_path, marker=marker, replacement="return a + b + 1",
    )
    try:
        assert "goal_done" not in outcomes, outcomes
        assert final.status != "done", (
            f"a coding task whose test fails is not finished; outcomes={outcomes}"
        )
        episodes = await db.fetch(
            "SELECT summary FROM episodes WHERE intention_id = $1 AND kind = 'verification_failed'",
            intention_id,
        )
        assert episodes, (
            "and the reason it did not pass is a fact in the record rather than a silence"
        )
        assert "failed check" in episodes[0]["summary"], episodes[0]["summary"]
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_the_same_task_is_reported_done_once_the_test_passes(db, config, tmp_path):
    """The other half, because a gate that never opens is not a gate.

    Same project, same loop, same code path -- and the change is the two
    characters that make the suite green. So the verdict is coming from the
    check and not from the model's confidence.
    """
    marker = "zz-codegreen"
    intention_id, outcomes, final = await _commissioned_coding_goal(
        db, config, tmp_path, marker=marker, replacement="return a + b",
    )
    try:
        assert "goal_done" in outcomes, outcomes
        assert final.status == "done", outcomes
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_a_step_checked_against_the_suite_is_not_a_finished_step(
    db, config, tmp_path,
):
    """The cheapest place to catch it, and the reason the change is retried.

    Here the plan does what the prompt now asks it to -- it names the project's
    own test command as the step's success check -- so the failure is caught one
    step old instead of at the end of the plan, where whatever step ran last gets
    the blame for it.
    """
    marker = "zz-codestepcheck"
    intention_id, outcomes, final = await _commissioned_coding_goal(
        db, config, tmp_path, marker=marker, step_check=True,
        replacement="return a + b + 1",
    )
    try:
        assert "step_failed" in outcomes, outcomes
        assert "goal_done" not in outcomes, outcomes
        attempts = ((final.resume_state or {}).get("step_attempts") or {})
        assert attempts.get("s1", 0) >= 1, (
            "the step is retried rather than accepted: " f"{final.resume_state}"
        )
        steps = ((final.resume_state or {}).get("plan") or {}).get("steps") or []
        assert steps[0]["status"] != "done", steps
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)


async def test_the_change_the_plan_made_is_what_gets_checked(db, config, tmp_path):
    """The files are read out of the work record, not out of the title.

    `resume_state.artifacts` is the only description of what the loop did, so it
    is the only place a check can be derived from. A derivation keyed on anything
    else would be checking whatever the agent said it was working on.
    """
    marker = "zz-codepaths"
    intention_id, outcomes, final = await _commissioned_coding_goal(
        db, config, tmp_path, marker=marker, replacement="return a + b",
    )
    try:
        actions = (final.resume_state or {}).get("artifacts") or []
        paths = changed_paths(actions)
        assert any(p.name == "adder.py" for p in paths), [str(p) for p in paths]
        assert "goal_done" in outcomes, outcomes
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)
