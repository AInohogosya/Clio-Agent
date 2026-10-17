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
        # `end`, not `stopped`: a stop is a hold the loop parks in, and this is
        # the test standing in for a process that ends.
        deps1.control.end("test")
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


async def _pending_message(db, key: str, text: str) -> str:
    """One unanswered inbound message on a conversation key of its own."""
    from uuid import uuid4 as _uuid4

    message_id = _uuid4()
    await db.execute(
        "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
        "text, social_decision) VALUES ($1, now(), 'inbound', 'web', $2, "
        "'owner', $3, NULL)",
        message_id, key, text,
    )
    return str(message_id)


async def test_an_unanswered_queue_cannot_starve_a_goal_forever(db, tmp_path: Path):
    """A backlog of messages defers a goal; an unbounded backlog does not.

    The salience gate that once arbitrated here was removed on purpose, because a
    short "are you OK?" scored 0.38 against a 0.65 focused threshold and waited
    about an hour -- which reads as the agent ignoring its owner. Removing it
    made every unanswered message take the pass unconditionally, and with no
    bound on that, an agent whose inbox fills faster than it drains never
    reaches the `advance_goal` branch at all.

    Every such cycle answers somebody, so nothing looks wrong: the drives keep
    proposing the task, the queue keeps having a head, and the task stays exactly
    where it was. It is the same shape as the planning backoff before it had a
    bound -- the work is not failing, it is simply never being reached.

    Asserted on the count, because the count is the mechanism: the goal runs on
    the first `inbound_defers_goal_max + 1` consecutive passes, and the message
    is still at the head of the queue afterwards, because deferring is not
    discarding.
    """
    key = f"web:starve-{tmp_path.name}"
    life, deps = await build_life(db, tmp_path, "starve")
    intention_id = await deps.memory.create_intention(
        title="Write the quarterly summary",
        desired_end_state="the summary is written", kind="commitment",
        origin="commission", commissioned_by="owner", priority=0.9,
    )
    message_id = await _pending_message(db, key, "are you there?")
    life.focus = Focus(
        intention_id=intention_id, title="Write the quarterly summary",
        kind="commitment", why="commissioned by owner",
    )
    limit = max(0, int(life.config.rhythm.focus.inbound_defers_goal_max))
    try:
        kinds = []
        for _ in range(limit + 3):
            decision = await life.decide(ws=None)
            kinds.append(decision.kind)
            # The messages are never actually answered here -- `decide` is being
            # asked what it would do, and answering needs a model. So the queue
            # is modelled as permanently non-empty, which is the situation the
            # bound exists for.
            if decision.kind == "advance_goal":
                break

        assert "advance_goal" in kinds, (
            f"a goal was starved by {len(kinds)} unanswered messages and never "
            f"reached: {kinds}"
        )
        # The message defers the goal exactly `limit` times and the next pass
        # works it: index `limit` is pass `limit + 1`.
        assert kinds.index("advance_goal") == limit, (
            f"expected the first {limit} passes to go to the message and the "
            f"next to the goal, got {kinds}"
        )
        # The messages are still queued. Deferred, not dropped.
        still = await deps.social.pending_inbound(limit=50)
        assert str(message_id) in [str(m.id) for m in still], (
            "the message was discarded rather than deferred"
        )
    finally:
        await db.execute("DELETE FROM intentions WHERE id = $1", intention_id)
        await db.execute("DELETE FROM messages WHERE id = $1", message_id)
        await deps.bus.stop()


async def test_a_social_focus_with_no_message_is_released_rather_than_held(
    db, tmp_path: Path,
):
    """A social focus exists to answer one message, and is spent when it has.

    With no message pending, `decide` used to return a social decision carrying
    `social=None`, which matches no branch in `act` -- so the cycle did nothing
    at all, and because that branch sits above the drives and the reverie, the
    idle work below it was unreachable for as long as the focus lasted. 2235
    cycles were once spent exactly that way.

    `next_focus` now drops the focus as well, so it costs one dead cycle rather
    than thousands; this asserts the cheaper half, that the spent focus is
    released inside `decide` so the pass it wastes is the last one.
    """
    life, deps = await build_life(db, tmp_path, "socialfocus")
    life.focus = Focus(
        title="a conversation that has already been answered",
        kind="social", why="answered last pass",
    )
    try:
        decision = await life.decide(ws=None)
        assert decision.kind != "social" or decision.social is not None, (
            "a spent social focus produced a decision that matches no branch in act"
        )
        assert life.focus is None, (
            f"the spent focus is still held: {life.focus!r}"
        )
    finally:
        await deps.bus.stop()
