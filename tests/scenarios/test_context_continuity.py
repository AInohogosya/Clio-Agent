from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ethos.memory.context import ContextLayer
from ethos.schemas.context import NarrativeLine, TurnKind
from tests.conftest import make_config
from tests.scenarios.test_restart_continuity import build_life

pytestmark = pytest.mark.scenario


async def _seed_messages(db, conversation_key: str, rows: list[tuple[str, int, str]]) -> None:
    """Seed conversation rows that no later test can mistake for live traffic.

    `social_decision IS NULL` is exactly what marks a message as pending, so an
    unseeded-fixture row here is picked up by whatever agent runs next and pulls
    it off its own goal to answer a stranger. These are history, not inbox.
    """
    for direction, minutes_ago, text in rows:
        await db.execute(
            "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
            "text, social_decision) VALUES (gen_random_uuid(), "
            "now() + make_interval(mins => $1::int), $2, 'web', $3, 'owner', $4, "
            "'\"handled\"'::jsonb)",
            minutes_ago, direction, conversation_key, text,
        )


async def test_context_survives_a_restart(db, tmp_path: Path):
    """Waking up must not be the same as starting over.

    The narrative and the newest turns are written into the continuity snapshot,
    so the second agent picks the thread up where the first one left it instead
    of reading its own history for the first time.
    """
    life1, deps1 = await build_life(db, tmp_path, "ctx-restart")
    await life1.context.append(
        TurnKind.inbound.value,
        "owner asked whether the migration had landed",
        detail={"text": "has the migration landed yet?"},
        conversation_key="web:owner",
    )
    await life1.context.append(
        TurnKind.outbound.value, "I said not yet", conversation_key="web:owner",
    )
    life1.context.narrative.append(
        NarrativeLine(text="I was mid-way through a migration the owner is asking about.",
                      turn_count=2)
    )

    task = asyncio.create_task(life1.life())
    try:
        for _ in range(200):
            if life1.cycles >= 2:
                break
            await asyncio.sleep(0.05)
        assert life1.cycles >= 2
    finally:
        deps1.control.stopped = True
        await asyncio.wait_for(task, timeout=10)

    life2, deps2 = await build_life(db, tmp_path, "ctx-restart")
    await life2.wake_up()

    assert life2.context.narrative, "the thread must outlive the process"
    assert "mid-way through a migration" in life2.context.render()
    assert any(t.kind == TurnKind.inbound.value for t in life2.context.turns), \
        "the newest raw turns must survive too, or waking resumes mid-sentence"

    await deps2.bus.stop()
    await deps1.bus.stop()


async def test_social_turns_reach_the_workspace(db, tmp_path: Path):
    """A message the agent read must be visible to the model on the next cycle.

    Assembling a block nobody reads is how this went missing in the first place,
    so this asserts the text the model would actually be sent.
    """
    life, deps = await build_life(db, tmp_path, "ctx-workspace")
    await life.context.append(
        TurnKind.inbound.value, "owner asked about the deploy",
        detail={"text": "did the deploy go out?"},
    )
    ws = await life._assemble_workspace([])
    assert "owner asked about the deploy" in ws.volatile_text()
    assert "did the deploy go out?" in ws.context_text()
    await deps.bus.stop()


async def test_gsl_step_receives_the_volatile_context(db, tmp_path: Path):
    """The severed wire: a step must see situation and thread, not just identity.

    `_execute_step` used to send only the cache-stable prefix, so six of nine
    blocks never reached the model however much work `Life` did assembling them.
    """
    from ethos.gsl.loop import GeneralSolverLoop, _step_from
    from ethos.gsl.verification import VerificationLadder
    from ethos.schemas.gsl import Plan
    from tests.conftest import ScriptedGateway

    config = make_config(tmp_path / "gsl-ctx")
    config.paths.ensure()
    gateway = ScriptedGateway(['{"thought": "acting", "actions": []}'])
    life, deps = await build_life(db, tmp_path, "gsl-ctx-life")
    context = ContextLayer(config, memory=deps.memory, gateway=gateway)
    life.context = context
    await context.append(TurnKind.failure.value, "the deploy script failed twice already")

    gsl = GeneralSolverLoop(
        config, deps.memory, gateway, deps.toolhost,
        VerificationLadder(config, gateway, None, comms_out=deps.comms_out, audit=deps.audit),
        audit=deps.audit, context=context,
    )
    ws = await life._assemble_workspace([])
    intention_id = await deps.memory.create_intention(
        title="Ship the deploy", desired_end_state="live",
    )
    intention = await deps.memory.get_intention(intention_id)

    plan = Plan(
        approach_id="a1", approach="just do it", strategy_number="1",
        steps=[_step_from({"id": "s1", "description": "run the deploy script",
                           "tool_hints": [], "success_check": {}, "reversible": True})],
    )
    await gsl._execute_step(intention, {}, plan, plan.steps[0], ws)

    sent = "\n".join(str(m.content) for m in gateway.requests[-1].messages)
    assert "the deploy script failed twice already" in sent, \
        "a step must know what already went wrong"
    assert "Ship the deploy" in sent
    await deps.bus.stop()


async def test_conversation_history_is_available_to_deliberation(db, tmp_path: Path):
    """Deliberation must see the conversation the message belongs to.

    The `recent` slot of the social prompt existed and was passed an empty
    string, so every reply was written as though it were the first message ever
    received on that conversation.
    """
    from ethos.schemas.messages import Message
    from ethos.social.deliberation import SocialCognition
    from tests.conftest import ScriptedGateway

    config = make_config(tmp_path / "conv-history")
    config.paths.ensure()
    gateway = ScriptedGateway(['{"action": "reply_now", "reply_texts": ["yes"], "reason": "ok"}'])
    social = SocialCognition(config, gateway=gateway, db=db)

    await _seed_messages(db, "web:history1", [
        ("inbound", -30, "are we still shipping friday?"),
        ("outbound", -29, "yes, barring surprises"),
        ("inbound", -28, "what about the friday date specifically"),
    ])
    latest = await db.fetchrow(
        "SELECT id FROM messages WHERE conversation_key = 'web:history1' "
        "ORDER BY ts DESC LIMIT 1",
    )
    message = Message(
        id=latest["id"], channel="web", conversation_key="web:history1", person_id="owner",
        text="what about the friday date specifically",
    )

    history = await social._recent_conversation(message)
    assert "are we still shipping friday?" in history, \
        "earlier in the conversation is what makes the latest message mean something"
    assert "yes, barring surprises" in history, \
        "what the agent already said is part of what it knows"
    assert "what about the friday date specifically" not in history, \
        "the message being answered is already in the prompt as the thing to respond to"
    # Oldest first, so it reads as a conversation rather than a pile.
    assert history.index("are we still shipping friday?") < history.index("yes, barring surprises")

    decision = await social.deliberate(message=message, energy=0.8, focus_title=None)
    assert decision.action.value == "reply_now"
    prompt = "\n".join(str(m.content) for m in gateway.requests[-1].messages)
    assert "are we still shipping friday?" in prompt, \
        "deliberation must carry the conversation it is answering into"


async def test_open_commitments_reach_deliberation(db, tmp_path: Path):
    """Saying yes to something already owed is how an agent double-books itself."""
    from ethos.schemas.messages import Message
    from ethos.social.deliberation import SocialCognition
    from tests.conftest import ScriptedGateway

    config = make_config(tmp_path / "commitments-ctx")
    config.paths.ensure()
    gateway = ScriptedGateway(['{"action": "reply_now", "reply_texts": ["ok"], "reason": "ok"}'])
    social = SocialCognition(config, gateway=gateway, db=db)
    from uuid import uuid4

    await db.execute(
        "INSERT INTO intentions (id, title, desired_end_state, kind, origin, status, "
        "priority, created_at, updated_at) "
        "VALUES ($1, 'Send the quarterly report', 'owner has it', 'commitment', "
        "'commission', 'active', 0.9, now(), now())",
        uuid4(),
    )
    await _seed_messages(db, "web:owner2", [("inbound", 0, "can you also do the invoice?")])
    row = await db.fetchrow(
        "SELECT id FROM messages WHERE conversation_key = 'web:owner2' ORDER BY ts DESC LIMIT 1",
    )
    message = Message(id=row["id"], channel="web", conversation_key="web:owner2",
                      person_id="owner", text="can you also do the invoice?")

    await social.deliberate(message=message, energy=0.8)
    prompt = "\n".join(str(m.content) for m in gateway.requests[-1].messages)
    assert "Send the quarterly report" in prompt, \
        "the agent must see what it is already on the hook for"


async def test_context_rolls_into_narrative_during_a_life(db, tmp_path: Path):
    """The buffer is bounded, but nothing is lost: it becomes a line."""
    config = make_config(tmp_path / "roll-live")
    config.paths.ensure()
    config.memory.context.narrate_every = 3
    context = ContextLayer(config, gateway=None)
    life, deps = await build_life(db, tmp_path, "roll-live-life")
    life.context = context

    await life._append_turn(TurnKind.inbound.value, "owner asked a question")
    await life._append_turn(TurnKind.failure.value, "the tool failed")
    await life._append_turn(TurnKind.action.value, "retried with different args")
    await context.roll("fix the tool")

    assert len(context.turns) == 0, "the batch should have been consumed"
    assert len(context.narrative) == 1, "and become a line"
    assert "What I have been doing" in context.render_thread()
    await deps.bus.stop()


async def test_focus_change_writes_down_what_was_being_worked_on(db, tmp_path: Path):
    """A change of focus is the natural seam: the work just finished, so it gets
    written down now rather than whenever the buffer happens to fill."""
    from ethos.schemas.gsl import Focus

    life, deps = await build_life(db, tmp_path, "focus-roll")
    life.context.cfg.narrate_every = 1
    await life.context.append(TurnKind.action.value, "was mid-way through chapter 3")

    before = Focus(intention_id=None, title="Translate the book", kind="task", why="commissioned")
    after = Focus(intention_id=None, title="Fix the deploy", kind="task", why="urgent")
    await life._roll_on_focus_change(before, after)

    assert life.context.narrative, "a focus change should have written the thread down"
    rendered = life.context.render()
    assert "Fix the deploy" in rendered
    await deps.bus.stop()


async def test_silence_is_kept_as_a_choice(db, tmp_path: Path):
    """Silence leaves no outbound trace, so nothing else can tell it from a
    message that never arrived."""
    from uuid import uuid4

    from ethos.schemas.messages import IgnoreReason, Message, SocialAction, SocialDecision

    life, deps = await build_life(db, tmp_path, "silence")
    message = Message(
        id=uuid4(), person_id="noisy", channel="telegram",
        conversation_key="telegram:noisy", text="you still there",
    )
    decision = SocialDecision(
        action=SocialAction.ignore, ignore_reason=IgnoreReason.boundary.value,
        reason="not now",
    )

    await life._record_silence_turn(message, decision)
    rendered = life.context.render()
    assert "noisy" in rendered
    assert "boundary" in rendered
    await deps.bus.stop()


async def test_snapshot_context_is_json_safe(db, tmp_path: Path):
    """The snapshot is written to disk as JSON, so a turn carrying something
    unserialisable would cost the agent its whole thread as it woke up."""
    life, deps = await build_life(db, tmp_path, "json-safe")
    await life.context.append(
        TurnKind.result.value,
        "ran a tool",
        detail={"result": {"nested": {"weird": object()}}},
    )
    payload: dict[str, Any] = life.context.snapshot()
    assert json.loads(json.dumps(payload, default=str))
    await deps.bus.stop()
