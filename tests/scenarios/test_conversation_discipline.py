from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ethos.social.conversation import open_conversations, render_conversations
from ethos.social.etiquette import OutboundPipeline
from tests.conftest import make_config

pytestmark = pytest.mark.scenario

"""
A person can scroll back.

That is the whole claim these tests hold. Everywhere else the conversation is a
one-way window: the agent has what it is holding right now, in a buffer that
narrates itself into a single line and drops the words, and nothing on the way
out asks whether those words are already sitting in someone's chat history.

The two failures that follows from that are the ones that make an agent feel
broken rather than busy. It speaks last on a conversation, hears nothing back,
and then speaks again — about something else entirely — as though it were the
one beginning. And when it arrives at the same thought twice it says it twice,
because the record of having said it once is a line in a buffer it has already
narrated away. The person, holding the whole transcript, sees an agent that has
lost the thread and does not know it has.
"""

CONVERSATION = "web:owner"


async def _say(
    db: Any, key: str, direction: str, text: str, *, minutes_ago: int,
    person_id: str = "owner", channel: str = "web", delivery: str | None = None,
) -> None:
    """One message on a conversation, with a delivery status the test chose.

    `social_decision` is set on every row including the inbound ones, because
    `social_decision IS NULL` is exactly what marks a message as pending: an
    unseeded inbound here is picked up by whatever agent runs next and pulled off
    its own goal to answer it. These are history, not inbox.
    """
    block = None if delivery is None else json.dumps({"status": delivery, "urgency": "normal"})
    await db.execute(
        "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, "
        "text, social_decision, delivery) VALUES (gen_random_uuid(), "
        "now() + make_interval(mins => $1::int), $2, $3, $4, $5, $6, "
        "'\"handled\"'::jsonb, $7::jsonb)",
        minutes_ago, direction, channel, key,
        person_id if direction == "inbound" else "owner", text, block,
    )


@pytest.fixture(autouse=True)
async def clean_conversations(db):
    """This file's rows only, and on both sides of the run.

    `open_conversations` is not scoped to one conversation by design — it answers
    "what conversations is the agent in", which is the whole point of it — so a
    leftover conversation from another file appears in the block and in this
    file's assertions. Exact match rather than a `LIKE`, because `web:owner` is a
    prefix of keys other files use.
    """
    await db.execute("DELETE FROM messages WHERE conversation_key = $1", CONVERSATION)
    yield
    await db.execute("DELETE FROM messages WHERE conversation_key = $1", CONVERSATION)


def _pipeline(tmp_path: Path, db: Any, sender: Any) -> OutboundPipeline:
    config = make_config(tmp_path / "conversation-discipline")
    config.paths.ensure()
    # Quiet hours that are definitely not now, so a failure here means the gate
    # under test changed rather than that the clock crossed 22:00.
    config.permissions.interruption.quiet_hours = {"start": "03:00", "end": "03:01"}
    config.permissions.interruption.daily_budgets = {"owner": 50, "other": 50}
    return OutboundPipeline(config, db=db, bus=None, audit=None, relationships=None,
                            sender=sender)


class _Sender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": "sent"}


def _delivery(value: Any) -> dict[str, Any]:
    """`jsonb` comes back as text unless something asked for it decoded."""
    return json.loads(value) if isinstance(value, str) else dict(value or {})


# ------------------------------------------------------------------ awareness


async def test_a_cycle_reads_the_conversations_it_is_in(db, tmp_path: Path):
    """The transcript is in the workspace, not only in the reply path.

    Deliberation sees the conversation of the message it is answering, and always
    has. Everything else — a cycle choosing what to work on, a step taking an
    action, a reverie picking a subject — saw nothing at all, because the one
    thing carrying the agent's own outbound turns is a working buffer that
    paraphrases them, narrates twelve of them into one line and then drops them.

    So the agent was doing a great deal of work in a conversation it could no
    longer read, while the person it was working with could read every word of it.
    """
    from tests.scenarios.test_restart_continuity import build_life

    await _say(db, CONVERSATION, "inbound", "are we still shipping friday?",
               minutes_ago=-30)
    await _say(db, CONVERSATION, "outbound", "yes, barring surprises", minutes_ago=-29)

    life, deps = await build_life(db, tmp_path, "conversations-block")
    ws = await life._assemble_workspace([])

    rendered = ws.volatile_text()
    assert "are we still shipping friday?" in rendered, \
        "what the person said has to be somewhere the model reads"
    assert "yes, barring surprises" in rendered, \
        "and what the agent answered, which is the part it cannot reconstruct"
    assert "last word was mine" in rendered, \
        "the standing of the conversation is what stops it opening a new one"
    assert "web:owner" in ws.context_text(), \
        "conversations are the agent's carried context, so they belong with the thread"
    await deps.bus.stop()


async def test_the_block_says_whose_turn_it_is(db, tmp_path: Path):
    """The one thing the thread cannot hold: that the agent is the one waiting.

    Every turn here is outbound and unanswered, which is the state that used to
    be invisible — the buffer held four outbound turns, all of them true, and none
    of them carrying the fact that nobody had said anything back.
    """
    await _say(db, CONVERSATION, "inbound", "are we still shipping friday?",
               minutes_ago=-30)
    await _say(db, CONVERSATION, "outbound", "yes, barring surprises", minutes_ago=-29)

    states = await open_conversations(db, within_h=72.0)
    mine = [state for state in states if state.key == CONVERSATION]
    assert mine, "a conversation two turns old is not a conversation nobody is in"
    state = mine[0]
    assert state.awaiting_reply is True
    assert [e.direction for e in state.exchanges] == ["inbound", "outbound"]

    rendered = render_conversations(states)
    assert "last word was mine" in rendered
    assert "has not been answered" in rendered
    assert "  owner: are we still shipping friday?" in rendered
    assert "  me: yes, barring surprises" in rendered, \
        "the transcript reads in order, with the agent as the speaker it is"


async def test_a_delivered_only_conversation_is_a_conversation(db):
    """Rows nobody received are not the last word.

    The outbound path records what it suppressed, held and recognised as a
    repeat, all as `direction='outbound'` rows in the same table the conversation
    is read from. Counting those as spoken is how an agent that was refused three
    times ends up waiting forever for an answer to a question it never got to ask.
    """
    await _say(db, CONVERSATION, "inbound", "are you awake?", minutes_ago=-5)
    await _say(db, CONVERSATION, "outbound", "still working on the migration",
               minutes_ago=-4, delivery="suppressed")
    await _say(db, CONVERSATION, "outbound", "still working on the migration",
               minutes_ago=-3, delivery="held")
    await _say(db, CONVERSATION, "outbound", "still working on the migration",
               minutes_ago=-2, delivery="duplicate")

    states = await open_conversations(db, within_h=72.0)
    state = next(state for state in states if state.key == CONVERSATION)
    assert state.awaiting_reply is False, \
        "a message nobody was delivered is not the agent having spoken"
    assert state.last_text == "are you awake?"


# -------------------------------------------------------------------- holding


async def test_an_unanswered_message_stops_the_next_one(db, tmp_path: Path):
    """The complaint, as a gate: unrelated news on top of an ignored question.

    The agent asked, got no answer, and went on to tell the person something else
    — which is what it does when it has no record of having spoken last. The
    person is holding both messages and the question between them, unanswered.
    """
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "inbound", "what happened to the migration?",
               minutes_ago=-90)
    await _say(db, CONVERSATION, "outbound", "running the dry run now", minutes_ago=-88)

    result = await pipe.send(
        person_id="owner", channel="web", text="Meanwhile I read chapter four.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )

    assert result["status"] == "held"
    assert "has not been answered" in result["reason"]
    assert sender.sent == [], "the person must not be told something else yet"


async def test_holding_reaches_the_agent_that_asked(db, tmp_path: Path):
    """A held message must not read as one that went out.

    The caller here is usually a tool the agent called itself, so what comes back
    is the only way it learns nobody heard anything: a step told `held` can stop
    composing the message, where a step told `sent` writes it again next cycle.
    That is the mechanism by which "repeats the same things over and over" grows.
    """
    pipe = _pipeline(tmp_path, db, _Sender())
    await _say(db, CONVERSATION, "inbound", "did the deploy land?", minutes_ago=-60)
    await _say(db, CONVERSATION, "outbound", "not yet", minutes_ago=-59)

    result = await pipe.send(
        person_id="owner", channel="web", text="New idea about the scheduler.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )

    row = await db.fetchrow(
        "SELECT delivery FROM messages WHERE text = $1 ORDER BY ts DESC LIMIT 1",
        "New idea about the scheduler.",
    )
    assert row is not None, "the attempt is a fact about the agent and belongs in the record"
    delivery = _delivery(row["delivery"])
    assert delivery["status"] == "held"
    assert delivery["detail"] == result["reason"]


async def test_a_person_who_answers_gets_the_next_message(db, tmp_path: Path):
    """The gate is about a ball in the air, not about having spoken before.

    Two messages ago is a different conversation from one the person has just
    picked up, and holding everything after the agent's first word to somebody
    would be a different failure with the same face.
    """
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "outbound", "did you see the log?", minutes_ago=-100)
    await _say(db, CONVERSATION, "inbound", "yes, looking now", minutes_ago=-2)

    result = await pipe.send(
        person_id="owner", channel="web", text="Chapter four is worth a look.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )

    assert result["status"] == "queued", result
    assert sender.sent == [], "still batched; holding is not the only thing that gates it"


async def test_an_old_unanswered_message_is_not_an_open_question_forever(db, tmp_path: Path):
    """Twelve hours is a person thinking. A day and a half is a different thing."""
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "inbound", "any luck with the invoice?", minutes_ago=-60 * 40)
    await _say(db, CONVERSATION, "outbound", "still trying the parser", minutes_ago=-60 * 39)

    result = await pipe.send(
        person_id="owner", channel="web", text="Unrelated: the roof needs looking at.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )

    assert result["status"] == "queued", result


# ------------------------------------------------------------- telling apart


def _flat(text: str) -> str:
    """The prompt with its line breaks collapsed.

    The rule is wrapped to a column, which puts a newline wherever the author put
    one — including through the middle of the one phrase these tests are about.
    Asserting on the wrapped form would make the test a statement about
    typesetting.
    """
    return " ".join(text.split())


async def test_a_stranger_is_told_they_do_not_know_the_others(db, tmp_path: Path):
    """The block says each person holds their own transcript, which implies the rest.

    Those two halves sit on one page: every conversation the agent is in, listed,
    with the note that each of them can scroll back through all of it. True of each
    conversation separately and a standing invitation to read across them. So the
    fact that the people listed do not know one another has to be stated, because
    nothing else on the page states it and the agent has no other way to know it —
    it is not a fact about any single transcript, it is a fact about the list.
    """
    from tests.scenarios.test_restart_continuity import build_life

    await _say(db, CONVERSATION, "inbound", "are we still shipping friday?", minutes_ago=-30)

    life, deps = await build_life(db, tmp_path, "cross-talk-open")
    rendered = _flat(await life._conversations_block())

    assert "## Conversations" in rendered
    assert "## Who knows what" in rendered, \
        "the rule belongs with the transcripts it is about, not only in the reply path"
    assert "do not know one another" in rendered
    assert "stays with them" in rendered
    await deps.bus.stop()


async def test_preview_mode_is_the_stricter_of_the_two(db, tmp_path: Path):
    """Nothing crosses at all, and the prompt says so in the present tense.

    Preview is somebody watching the agent before letting it touch anything, so the
    answer being looked for is the closed one. What is refused there is not only
    retelling: it is confirming that another conversation exists, which is the
    smaller leak that still tells a stranger they are being compared to someone.
    """
    from tests.scenarios.test_restart_continuity import build_life

    await _say(db, CONVERSATION, "inbound", "are we still shipping friday?", minutes_ago=-30)

    life, deps = await build_life(db, tmp_path, "cross-talk-preview")
    await deps.control.apply_command("preview_on", "test")

    rendered = _flat(await life._conversations_block())

    assert "Preview mode is on" in rendered, \
        "the mode has to be named in the text, or the strict half reads as the lenient one"
    assert "nothing crosses" in rendered
    assert "do not confirm it if I am asked" in rendered, \
        "denying the connection is the leak that outlives refusing to tell the story"
    assert "I know Mira too" not in rendered, \
        "acknowledging a mutual acquaintance is what preview mode takes away"
    await deps.bus.stop()


async def test_leaving_preview_mode_brings_the_looser_rule_back(db, tmp_path: Path):
    """Preview off must not leave the agent permanently unable to mention a friend.

    The strict half is a mode rather than a sentence in the kernel, so it has to be
    reversible in the same breath. An agent that read the closed rule once and kept
    it would refuse an honest, mutual connection for the rest of its life — and the
    only evidence that it had is the text in front of it, which changes.
    """
    from tests.scenarios.test_restart_continuity import build_life

    await _say(db, CONVERSATION, "inbound", "are we still shipping friday?", minutes_ago=-30)

    life, deps = await build_life(db, tmp_path, "cross-talk-relaxed")
    await deps.control.apply_command("preview_on", "test")
    assert "Preview mode is on" in _flat(await life._conversations_block())

    await deps.control.apply_command("preview_off", "test")
    rendered = _flat(await life._conversations_block())

    assert "Preview mode is on" not in rendered
    assert "I know Mira too" in rendered, \
        "a connection both people already know is honest to acknowledge, and refusing it is its own lie"
    assert "what one person actually said" in rendered, \
        "what stays closed outside preview is the content, not the connection"
    await deps.bus.stop()


async def test_the_reply_path_is_told_the_same_thing():
    """The deliberation writes the words the person reads, so it is told it too.

    Every other surface here is context. This one composes the outgoing text, and
    it is shown one message with no transcript attached — which is the shape most
    likely to produce a confident aside about somebody else, because the agent has
    the memory of every other conversation and nothing in the prompt asking what it
    may do with it.

    Not a database test: this is the prompt and nothing else, and the words it
    writes are the only place the rule can be got wrong in a way a person sees.
    """
    from datetime import UTC, datetime

    from ethos.prompts.deliberation import social_deliberation_messages
    from ethos.schemas.messages import Message

    inbound = Message(
        channel="telegram", person_id="tg:819012345678", text="any news on the launch?",
        ts=datetime.now(UTC),
    )
    now = datetime.now(UTC).isoformat()

    def system(preview: bool) -> str:
        return _flat(social_deliberation_messages(
            self_name="Clio", message=inbound, focus="launch", now=now, energy=0.9,
            commitments="(none)", recent="(none)", preview=preview,
        )[0]["content"])

    closed, opened = system(True), system(False)
    assert "## Who knows what" in closed
    assert "nothing crosses" in closed
    assert "I know Mira too" not in closed
    assert "I know Mira too" in opened, \
        "outside preview mode the honest connection is allowed, and the prompt has to allow it"


async def test_a_cycle_without_a_preview_flag_still_answers_ordinary_work(db, tmp_path: Path):
    """An unknown flag is the lenient half, not a failed cycle.

    `control` is a plain dependency and tests build it out of anything with the
    right shape, so this is the reading of a `Life` whose control knows nothing
    about preview. Refusing the agent's own ordinary conversation is the expensive
    mistake here; the strict half costs nothing but candour.
    """
    from tests.scenarios.test_restart_continuity import build_life

    await _say(db, CONVERSATION, "inbound", "are we still shipping friday?", minutes_ago=-30)

    life, deps = await build_life(db, tmp_path, "cross-talk-no-flag")
    del deps.control.preview

    assert life._preview() is False
    assert "Preview mode is on" not in _flat(await life._conversations_block())
    await deps.bus.stop()


# ------------------------------------------------------------------- repeating


async def test_the_same_words_are_not_said_twice(db, tmp_path: Path):
    """A thought that resolves to the same sentence every cycle.

    Nothing compared the text, so every cycle queued the same paragraph and the
    digest flushed it as four identical sentences joined by blank lines — from an
    agent that, to anyone reading, appears to have nothing else to say.
    """
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "inbound", "anything from you?", minutes_ago=-3)

    first = await pipe.send(
        person_id="owner", channel="web", text="Still thinking about the migration.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )
    second = await pipe.send(
        person_id="owner", channel="web", text="Still thinking about the migration.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )
    pipe.window_s = 0.0
    await pipe.flush_due_digests()

    assert first["status"] == "queued"
    assert second["status"] == "duplicate", second
    assert len(sender.sent) == 1, "one thought, one message"
    assert sender.sent[0]["text"] == "Still thinking about the migration."


async def test_a_repeat_is_recognised_through_whitespace_and_case(db, tmp_path: Path):
    """The repeat a person sees as a repeat is not always a byte-identical one."""
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "outbound", "Chapter four is worth a look.",
               minutes_ago=-20)

    result = await pipe.send(
        person_id="owner", channel="web", text="chapter four   is worth a LOOK.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )

    assert result["status"] == "held"
    assert "already said" in result["reason"]
    assert sender.sent == []


async def test_saying_the_same_thing_tomorrow_is_allowed(db, tmp_path: Path):
    """The gate has a shape, and a permanent one is silence rather than restraint.

    A reminder the agent genuinely owes again the next day is the same words, and
    refusing it would be the agent confusing repetition with recurrence.
    """
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "outbound", "Standup is at ten.", minutes_ago=-60 * 30)

    result = await pipe.send(
        person_id="owner", channel="web", text="Standup is at ten.",
        conversation_key=CONVERSATION, in_reply_to=None,
    )

    assert result["status"] == "queued", result


async def test_a_batch_waits_out_a_reply_sent_after_it_was_queued(db, tmp_path: Path):
    """The gate asked at enqueue can be stale by the time the window closes.

    A note is queued while the person still has the last word, waits out its two
    hours, and goes out behind a reply the agent sent in between — so the person
    gets the answer and an unrelated bundle, in that order, with the answer
    unasked. Asking again at delivery is the only point at which the question has
    a current answer.
    """
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "inbound", "anything from you?", minutes_ago=-30)

    assert (await pipe.send(
        person_id="owner", channel="web", text="Chapter four is worth a look.",
        conversation_key=CONVERSATION, in_reply_to=None,
    ))["status"] == "queued"

    # The person writes, and the agent answers: its own word is now last.
    await _say(db, CONVERSATION, "inbound", "the build is red", minutes_ago=-20)
    await _say(db, CONVERSATION, "outbound", "looking now", minutes_ago=-19)

    pipe.window_s = 0.0
    assert await pipe.flush_due_digests() == 0, \
        "a bundle does not arrive on top of a reply nobody has answered"
    assert sender.sent == []

    # And when the person comes back, the bundle is still there to go out.
    await _say(db, CONVERSATION, "inbound", "found it", minutes_ago=-2)
    assert await pipe.flush_due_digests() == 1
    assert sender.sent[0]["text"] == "Chapter four is worth a look."


async def test_a_reply_is_delivered_even_when_it_repeats(db, tmp_path: Path):
    """The guarantee the gates must not cost.

    The same words going out twice is worse than the words being wrong, but a
    person's wait running out is worse than both: the deliberation chose to answer,
    the surface is holding the question, and every gate below the reply check
    exists to keep the agent from *starting* conversations rather than answering
    them. So the repeat and the unanswered checks read the delivered record and
    do not apply to a reply at all.
    """
    sender = _Sender()
    pipe = _pipeline(tmp_path, db, sender)
    await _say(db, CONVERSATION, "outbound", "yes, friday works.", minutes_ago=-8)
    message_id = await db.fetchval("SELECT id FROM messages LIMIT 1")

    result = await pipe.send(
        person_id="owner", channel="web", text="yes, friday works.",
        conversation_key=CONVERSATION, in_reply_to=message_id,
    )

    assert result["status"] == "sent"
    assert len(sender.sent) == 1, "an answer to a question that was asked is delivered"
    assert sender.sent[0]["in_reply_to"] == message_id
