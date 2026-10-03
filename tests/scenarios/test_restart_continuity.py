from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ethos.bus import EventBus
from ethos.core.control import ControlPlane
from ethos.engine.attention import Attention
from ethos.engine.drives import Motivation
from ethos.engine.life import Life, LifeDeps
from ethos.engine.rhythm import RhythmController
from ethos.gateway.embeddings import HashingEmbedder
from ethos.guardian.gate import GuardianGate
from ethos.guardian.journal import ActionJournal
from ethos.guardian.trash import TrashStore
from ethos.memory.continuity import ContinuityManager
from ethos.memory.self_model import SelfModel
from ethos.memory.store import MemoryStore
from ethos.observability.audit import AuditLog, DbAuditSink
from ethos.schemas.events import EventKind
from ethos.schemas.gsl import Focus, SolverOutcome, SolverResult
from ethos.social.deliberation import SocialCognition
from ethos.social.etiquette import OutboundPipeline
from ethos.social.relationships import RelationshipStore
from ethos.toolhost.server import Toolhost
from tests.conftest import FakeRegistry, FakeVerifier, ListAudit, NoopSnapshots, ScriptedGateway, make_config

pytestmark = pytest.mark.scenario


class _FakeGSL:
    def __init__(self):
        self.advances = 0

    async def advance(self, intention, ws):
        self.advances += 1
        return SolverResult(
            outcome=SolverOutcome.step_done,
            thoughts=[f"fake step {self.advances} on {intention.title}"],
            cost_usd=0.01,
        )


class _FakeConsolidation:
    def __init__(self):
        self.runs = 0

    async def run_nightly(self, now=None):
        self.runs += 1
        return {"ok": True}


async def build_life(db, tmp_path: Path, tag: str):
    config = make_config(tmp_path / tag)
    config.paths.ensure()
    bus = EventBus(db, source=f"core-{tag}")
    await bus.start()
    embedder = HashingEmbedder(1024)
    memory = MemoryStore(db, embedder, config, bus=bus)
    self_model = SelfModel(db, bus=bus)
    await self_model.seed_defaults("Ethos")
    gateway = ScriptedGateway([])
    audit = AuditLog(DbAuditSink(db))
    relationships = RelationshipStore(config, db=db)
    social = SocialCognition(config, gateway=gateway, relationships=relationships,
                             audit=audit, self_model=self_model, db=db)
    comms_out = OutboundPipeline(config, db=db, bus=bus, audit=audit,
                                 relationships=relationships,
                                 sender=_fake_sender)
    trash = TrashStore(config.paths.trash)
    journal = ActionJournal(file_path=config.paths.data_dir / "journal.jsonl")
    control = ControlPlane(db, str(config.paths.run_dir), bus=bus)
    await control.load()
    gate = GuardianGate(config, registry=FakeRegistry(), trash=trash,
                        snapshots=NoopSnapshots(), audit=ListAudit(),
                        verifier=FakeVerifier(), control=control)
    toolhost = Toolhost(config, gate=gate, journal=journal, bus=bus,
                        services={"memory": memory, "trash": trash})
    attention = Attention(config, embedder=embedder, gateway=gateway, self_model=self_model)
    motivation = Motivation(config)
    rhythm = RhythmController(config)
    consolidation = _FakeConsolidation()
    gsl = _FakeGSL()
    continuity = ContinuityManager(config, db=db, bus=bus)

    async def fast_wait(state):
        await asyncio.sleep(0.01)

    rhythm.wait_until_next = fast_wait

    deps = LifeDeps(
        config=config, control=control, bus=bus, memory=memory, self_model=self_model,
        attention=attention, motivation=motivation, rhythm=rhythm, gateway=gateway,
        gsl=gsl, social=social, comms_out=comms_out, toolhost=toolhost, audit=audit,
        continuity=continuity, consolidation=consolidation, embedder=embedder,
        thread_id="self",
    )
    life = Life(deps)
    life.attach_queue(asyncio.Queue())
    return life, deps


async def _fake_sender(**kwargs):
    return {"status": "sent"}


async def test_restart_continuity_across_two_lives(db, tmp_path: Path):
    from uuid import uuid4

    stale_id = f"stale-{uuid4().hex[:8]}"
    life1, deps1 = await build_life(db, tmp_path, "life1")
    intention_id = await deps1.memory.create_intention(
        title="Finish the long translation",
        desired_end_state="the document is fully translated",
        kind="commitment",
        origin="commission",
        commissioned_by="owner",
        priority=0.9,
    )
    life1.focus = Focus(
        intention_id=intention_id, title="Finish the long translation",
        kind="commitment", why="commissioned by owner",
        bookmark={"next_planned_step": "translate chapter 3"},
    )

    snapshot_path = deps1.config.paths.data_dir / "continuity.json"

    task = asyncio.create_task(life1.life())
    try:
        for _ in range(200):
            if life1.cycles >= 3:
                break
            await asyncio.sleep(0.05)
        assert life1.cycles >= 3
        # A running agent's own snapshot must not claim it has stopped. The
        # stop block used to sit at the tail of every cycle, so the durable
        # state said "stopped" from the first cycle on — and a process that
        # then died mid-flight was indistinguishable from one that had shut
        # down cleanly, because the last pass had already said so.
        snapshot_mid = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert snapshot_mid["state"].get("state") != "stopped", \
            "the snapshot must describe a running agent while it runs"
    finally:
        deps1.control.stopped = True
        await asyncio.wait_for(task, timeout=10)

    assert deps1.gsl.advances >= 3
    assert snapshot_path.exists()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["state"]["thread_id"] == "self"
    # And once the loop has left by control, the stop it records is the truth.
    assert snapshot["state"].get("state") == "stopped"
    saved_focus = json.loads(snapshot["state"]["focus"])
    assert saved_focus["intention_id"] == str(intention_id)

    cycle_rows = await db.fetch(
        "SELECT state, focus::text AS focus, tier FROM cycles WHERE thread_id = 'self' "
        "ORDER BY id DESC LIMIT 10"
    )
    assert len(cycle_rows) >= 3

    await db.execute(
        "INSERT INTO action_journal (id, ts_start, thread_id, tool, args_redacted, reason, "
        "status) VALUES ($1, now() - interval '2 hours', 'self', 'shell.run', "
        "'{}'::jsonb, 'crashed mid-flight', 'running')",
        stale_id,
    )
    await deps1.bus.publish(EventKind.PERCEPT, {"summary": "unprocessed percept", "urgency": 0.6})

    life2, deps2 = await build_life(db, tmp_path, "life1")
    report = await deps2.continuity.wake_up()
    assert report["reconciled_actions"] >= 1
    assert report["missed_events"] >= 1
    row = await db.fetchrow("SELECT status FROM action_journal WHERE id = $1", stale_id)
    assert row["status"] == "interrupted"

    await life2.wake_up()
    assert life2.focus is not None
    assert life2.focus.intention_id == intention_id
    assert life2.focus.title == "Finish the long translation"
    intention = await deps2.memory.get_intention(intention_id)
    assert intention is not None and intention.is_open()

    await deps2.bus.stop()
    await deps1.bus.stop()


async def test_bookmark_written_to_resume_state(db, tmp_path: Path):
    life, deps = await build_life(db, tmp_path, "life3")
    intention_id = await deps.memory.create_intention(
        title="Bookmark me", desired_end_state="done",
    )
    life.focus = Focus(intention_id=intention_id, title="Bookmark me", kind="task",
                       why="test", bookmark={"next_planned_step": "step 7",
                                             "mental_context": "deep in thought"})
    await life.bookmark(life.focus)
    intention = await deps.memory.get_intention(intention_id)
    assert intention.resume_state["next_planned_step"] == "step 7"
    assert intention.resume_state["mental_context"] == "deep in thought"
    await deps.bus.stop()
