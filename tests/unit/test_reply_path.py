from __future__ import annotations

import ast
import asyncio
import tokenize
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from ethos.engine.attention import Attention
from ethos.engine.drives import Motivation
from ethos.engine.life import Life, LifeDeps
from ethos.engine.rhythm import RhythmController
from ethos.gateway.embeddings import HashingEmbedder
from ethos.prompts.deliberation import social_deliberation_messages
from ethos.schemas.events import EventKind
from ethos.schemas.gsl import Decision, Outcome
from ethos.schemas.messages import Message
from ethos.social.deliberation import SocialCognition
from ethos.social.etiquette import OutboundPipeline
from ethos.social.relationships import RelationshipStore
from tests.conftest import ListAudit, ScriptedGateway, make_config

"""
The reply that was chosen, and the silence that was chosen.

A surface waiting on the agent can be failed by two very different things, and
the tests here keep them apart. One is a broken link: the agent is asked and
nothing comes back, for a reason that is not the agent's choice. The other is a
working agent that heard the question and answered it with nothing. Both used to
look identical from outside — a wait that ran out — and the second one is not a
failure at all, it is the agent exercising the right to decline [D-05].
"""


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": "sent"}


class _RecordingOut:
    """Stands where the outbound pipeline sits, recording what Life hands it."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": "sent"}


class _Quiet:
    """Nothing needs to be real: these tests are about what gets published."""

    def __getattr__(self, name: str) -> Any:
        async def _noop(*_: Any, **__: Any) -> Any:
            return None

        return _noop


class _CollectingBus:
    """A bus that keeps what was published, for asserting on the event stream."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, kind: str, payload: dict[str, Any] | None = None, **_: Any) -> int:
        self.published.append((kind, dict(payload or {})))
        return len(self.published)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [payload for published, payload in self.published if published == kind]


class _StubDb:
    """Just the three conversation queries the outbound gates ask of a db."""

    def __init__(self, *, last: dict[str, Any] | None = None,
                 said: list[str] | None = None, sent_today: int = 0) -> None:
        self.last = last
        self.said = said or []
        self.sent_today = sent_today

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self.last

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return [{"text": text} for text in self.said]

    async def fetchval(self, query: str, *args: Any) -> int:
        return self.sent_today


def _pipeline(sender: _RecordingSender, bus: _CollectingBus | None = None,
              db: Any = None) -> OutboundPipeline:
    config = make_config(Path("/tmp/ethos-test-etiquette"))
    config.paths.ensure()
    # Quiet hours that are definitely not now, so a test failure means the
    # batching gate changed rather than that the clock crossed 22:00.
    config.permissions.interruption.quiet_hours = {"start": "03:00", "end": "03:01"}
    return OutboundPipeline(
        config, db=db, bus=bus, audit=None, relationships=None, sender=sender,
    )


def test_a_reply_to_a_question_is_delivered_not_batched() -> None:
    """The bug this file exists for.

    Every normal-urgency reply was routed into the two-hour digest on its way out,
    so a `reply_now` deliberation produced a message row marked `queued` that was
    never handed to the channel. The agent had decided to answer; the answer went
    into a batching window meant for unsolicited updates, and the person who
    asked was told the agent had gone quiet.
    """
    sender = _RecordingSender()
    pipe = _pipeline(sender)

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text="The answer is 42.",
        urgency="normal", conversation_key="web:owner", in_reply_to=uuid4(),
    ))

    assert result["status"] == "sent", "a solicited reply must not sit in a digest"
    assert [message["text"] for message in sender.sent] == ["The answer is 42."]


def test_a_digest_flush_sends_what_the_agent_wrote_and_adds_nothing() -> None:
    """The batching window may join messages; it may not introduce one.

    When several of the agent's own messages accumulated in one window, the
    flush prepended "Updates since my last message:" and bullet-marked each
    entry. The framing is a sentence in the agent's voice that no deliberation
    produced, and it describes the bundle as a deliberate presentation —
    "updates since my last message" — when in fact it is a batching artefact
    of when the window happened to flush.

    So the texts go out as they were written, in order, separated by nothing
    the agent did not write. The batching is still batching: one delivery
    rather than four.
    """
    sender = _RecordingSender()
    pipe = _pipeline(sender)
    pipe.window_s = 0.0

    for text in ("The shortlist is narrowed to two.", "I read the second one last night."):
        asyncio.run(pipe.send(
            person_id="owner", channel="web", text=text, urgency="normal",
            conversation_key=None, in_reply_to=None,
        ))

    flushed = asyncio.run(pipe.flush_due_digests())

    assert flushed == 1, "one window, one delivery"
    assert len(sender.sent) == 1, "batched, not sent four times"
    delivered = sender.sent[0]["text"]
    assert delivered == (
        "The shortlist is narrowed to two.\n\nI read the second one last night."
    ), "what arrives is what the agent wrote, and nothing else"
    assert "Updates since my last message" not in delivered
    assert "•" not in delivered


def test_one_reply_leaves_as_one_message() -> None:
    """A turn is one message, and it used not to be.

    A deliberation writes its reply as a list of short texts, and every entry was
    handed to the outbound pipeline on its own. One question therefore went out as
    two or three deliveries: two or three rows in the transcript, two or three
    messages on the channel, for a reply the agent had chosen once. Nothing
    downstream can fold them back together — a surface's wait matches the *first*
    outbound message after the question — so the extra texts arrived as messages
    answering nothing, and from the other end of the conversation the agent looked
    like it was sending the same answer over and over.

    So the texts are joined into the single message the turn decided on: the whole
    reply, delivered once, naming the message it answers.
    """
    config = make_config(Path("/tmp/ethos-test-one-message"))
    config.paths.ensure()
    bus = _CollectingBus()
    out = _RecordingOut()
    message = Message(person_id="owner", channel="web",
                      conversation_key="web:owner", text="what did you make of it?")

    class _QueueSocial:
        def __init__(self) -> None:
            self.handled = False

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True

    social = _QueueSocial()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=out, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(life._act_social({
        "action": "reply_now",
        "reply_texts": ["You were right about the tempo.", "I took the second pass tonight."],
        "urgency": "normal", "reason": "the question was about the work",
        "ignore_reason": None, "creates_intention": False, "intention_spec": None,
        "tier_used": "T2",
    }))

    assert len(out.sent) == 1, "one turn is one message, however the reply was written"
    reply = out.sent[0]
    assert reply["text"] == (
        "You were right about the tempo.\n\nI took the second pass tonight."
    ), "nothing the agent chose to say is dropped on the way out"
    assert reply["in_reply_to"] == message.id, "the message names the question it answers"
    assert social.handled, "the question is finished with"
    assert len(bus.of(EventKind.MESSAGE_HANDLED)) == 0, "a reply that was sent is not a decline"


def test_a_reply_of_nothing_but_whitespace_is_not_sent() -> None:
    """An empty answer is nothing, on this path as much as on the review's.

    The reply texts are joined rather than sent one by one, and a list of blank
    strings joins to an empty message. Sending that would resolve somebody's wait
    with a message with nothing in it, which is the one thing this pipeline
    refuses to do — so the reply is treated as the silence it is: nothing goes out,
    and the question is declared.
    """
    config = make_config(Path("/tmp/ethos-test-blank-reply"))
    config.paths.ensure()
    bus = _CollectingBus()
    out = _RecordingOut()
    message = Message(person_id="owner", channel="web",
                      conversation_key="web:owner", text="are you still there?")

    class _QueueSocial:
        def __init__(self) -> None:
            self.handled = False

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True

    social = _QueueSocial()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=out, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(life._act_social({
        "action": "reply_now", "reply_texts": ["   ", "\n"], "urgency": "normal",
        "reason": "the model came back half-formed",
        "ignore_reason": None, "creates_intention": False, "intention_spec": None,
        "tier_used": "T2",
    }))

    assert out.sent == [], "a message with no words in it is not sent"
    assert social.handled, "and the question is finished with rather than retried forever"
    assert len(bus.of(EventKind.MESSAGE_HANDLED)) == 1, "standing silences are declared"


def test_a_deadline_the_model_wrote_as_a_word_does_not_cost_a_second_answer() -> None:
    """A malformed optional field must not leave the question in the queue.

    The social schema offers `"deadline_iso": "..."|null`, and models fill in the
    word rather than the JSON null often enough to matter. Read as a date, `"null"`
    is a `ValueError` — raised while carrying out a decision, which killed the act
    before the message was marked handled. It then sat at the head of the pending
    queue, was deliberated again next cycle, and the person who had already been
    answered was answered a second time and paid for twice.

    An unreadable deadline is the absence of one: the intention is still created,
    and the question is finished with exactly once.
    """
    config = make_config(Path("/tmp/ethos-test-deadline-word"))
    config.paths.ensure()
    out = _RecordingOut()
    message = Message(person_id="owner", channel="web",
                      conversation_key="web:owner", text="remind me Friday")

    class _QueueSocial:
        def __init__(self) -> None:
            self.handled = False

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True

    class _Memory:
        def __init__(self) -> None:
            self.created: list[dict[str, Any]] = []

        async def create_intention(self, **kwargs: Any) -> Any:
            self.created.append(kwargs)
            return SimpleNamespace(id=uuid4())

    social = _QueueSocial()
    memory = _Memory()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=_CollectingBus(), memory=memory,
        self_model=_Quiet(), attention=None, motivation=None, rhythm=_Quiet(),
        gateway=None, gsl=None, social=social, comms_out=out, toolhost=None,
        audit=ListAudit(), continuity=_Quiet(), consolidation=None,
        embedder=None, thread_id="self",
    ))

    for deadline in ("null", None, "not-a-date", "2030-01-02T09:00:00+00:00"):
        social.handled = False
        sent_before = len(out.sent)
        asyncio.run(life._act_social({
            "action": "reply_now", "reply_texts": ["Friday. Noted."], "urgency": "normal",
            "reason": "a deadline was named", "ignore_reason": None,
            "creates_intention": True,
            "intention_spec": {"title": "remind about Friday",
                               "desired_end_state": "reminded", "deadline_iso": deadline},
            "tier_used": "T2",
        }))
        assert social.handled, f"a deadline of {deadline!r} must not strand the question"
        assert len(out.sent) - sent_before == 1, \
            f"a deadline of {deadline!r} must not cost a second answer"
        assert len(memory.created) >= 1, "the intention is still created without a date"

    readable = [made["deadline"] for made in memory.created
                if isinstance(made.get("deadline"), datetime)]
    assert len(readable) == 1 and readable[0].year == 2030, \
        "a deadline that *is* readable is still honoured"


def test_a_reply_is_delivered_outside_quiet_hours_too() -> None:
    """Quiet hours bound waking someone up, not answering someone who is there.

    The gates below the reply check exist to stop the agent *starting* a
    conversation. A person who has just written is awake by definition, so the
    hour on the clock is not evidence about them.
    """
    config = make_config(Path("/tmp/ethos-test-quiet"))
    config.paths.ensure()
    config.permissions.interruption.quiet_hours = {"start": "00:00", "end": "23:59"}
    sender = _RecordingSender()
    pipe = OutboundPipeline(config, db=None, bus=None, audit=None,
                            relationships=None, sender=sender)

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text=" awake?",
        urgency="normal", in_reply_to=uuid4(),
    ))

    assert result["status"] == "sent"
    assert len(sender.sent) == 1


def test_an_unsolicited_update_is_still_batched() -> None:
    """The batching the reply no longer goes through is still there for updates.

    Fixing the reply must not quietly remove the interruption budget: a nudge the
    agent invented on its own is exactly what the digest exists to hold back, and
    the agent's restraint there is as deliberate as its willingness to answer.
    """
    sender = _RecordingSender()
    pipe = _pipeline(sender)

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text="just thinking about chapter 3",
        urgency="normal", in_reply_to=None,
    ))

    assert result["status"] == "queued"
    assert sender.sent == [], "an unsolicited update must not be pushed straight out"


def test_a_progress_note_on_a_task_is_delivered_when_it_is_written() -> None:
    """The case the two gates below the reply check were swallowing whole.

    Commissioning work is itself a turn in the conversation. The agent's "I'll do
    it" is a solicited reply, so it was delivered at once and its own word was
    last and unanswered for the next `awaiting_reply_h` hours -- and every
    progress note after it met exactly that state and was held.

    Held, not queued: `_hold` writes the row and drops the text rather than
    deferring it. So the updates did not arrive late, they did not arrive at all,
    while the intention sat in the interface reading as work in progress and the
    agent composed a fresh note each step having been told nobody heard the last.

    A send that names the intention it is about is not the agent opening a
    conversation, so it skips the batching window and the unanswered check.
    """
    sender = _RecordingSender()
    pipe = _pipeline(sender, db=_StubDb(last={
        "ts": datetime.now(), "direction": "outbound",
        "person_id": "owner", "text": "I'll do it.",
    }))

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text="Three of five steps done.",
        urgency="normal", conversation_key="web:owner", in_reply_to=None,
        intention_id=uuid4(),
    ))

    assert result["status"] == "sent", "a note about work in flight is not an interruption"
    assert [message["text"] for message in sender.sent] == ["Three of five steps done."]


def test_a_progress_note_in_quiet_hours_waits_rather_than_vanishing() -> None:
    """Skipping the digest is not the same as skipping the hour on the clock.

    Immediate means when the note is written, not that the hour does not count.
    A send the day is not ready for goes to the digest and is flushed when the
    window is up, which is the difference between late and gone.
    """
    config = make_config(Path("/tmp/ethos-test-midtask-quiet"))
    config.paths.ensure()
    config.permissions.interruption.quiet_hours = {"start": "00:00", "end": "23:59"}
    sender = _RecordingSender()
    pipe = OutboundPipeline(config, db=None, bus=None, audit=None,
                            relationships=None, sender=sender)

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text="Step 2 of 5 done.",
        urgency="normal", conversation_key="web:owner", in_reply_to=None,
        intention_id=uuid4(),
    ))

    assert result["status"] == "queued", "quiet hours defer a note, they do not drop it"
    assert sender.sent == []


def test_a_progress_note_still_counts_against_the_daily_budget() -> None:
    """Immediate is about timing. The budget is about how often, and it stands.

    A step that reports on each of its actions spends four of the day's six
    messages. That should be a choice with a price, not a free side effect of
    making the send immediate.
    """
    sender = _RecordingSender()
    pipe = _pipeline(sender, db=_StubDb(sent_today=6))

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text="Step 3 of 5 done.",
        urgency="normal", conversation_key="web:owner", in_reply_to=None,
        intention_id=uuid4(),
    ))

    assert result["status"] == "queued", "the daily budget still defers rather than drops"
    assert sender.sent == []


def test_a_progress_note_is_never_delivered_twice() -> None:
    """The repeat window is the one gate that was doing real work here.

    Nothing stops a step deciding to report on the same milestone every cycle, so
    the same sentence must not go out each time. It is held rather than delivered,
    which tells the step it has already said this and it can stop composing.
    """
    sender = _RecordingSender()
    pipe = _pipeline(sender, db=_StubDb(said=["Three of five steps done."]))

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text="Three of five steps done.",
        urgency="normal", conversation_key="web:owner", in_reply_to=None,
        intention_id=uuid4(),
    ))

    assert result["status"] == "held"
    assert sender.sent == []


def test_midtask_batching_can_be_turned_back_on() -> None:
    """The exception is a configuration choice and reads as one.

    Someone who wants the agent silent until it has something finished can set
    this false and get the old behaviour, without the code holding two opinions
    about which default is correct.
    """
    config = make_config(Path("/tmp/ethos-test-midtask-off"))
    config.paths.ensure()
    config.permissions.interruption.midtask_sends_immediate = False
    sender = _RecordingSender()
    pipe = OutboundPipeline(config, db=None, bus=None, audit=None,
                            relationships=None, sender=sender)

    result = asyncio.run(pipe.send(
        person_id="owner", channel="web", text="Three of five steps done.",
        urgency="normal", conversation_key="web:owner", in_reply_to=None,
        intention_id=uuid4(),
    ))

    assert result["status"] == "queued"
    assert sender.sent == []


def test_a_recorded_reply_carries_the_time_it_was_written() -> None:
    """A pushed reply has to be able to say when it happened.

    The `message.new` event carried no timestamp, so a surface reading the event
    gave the message a time of zero and could never match it against the question
    it was holding. The reply was on the wire and still read as silence, and the
    only thing that ever rescued it was an unrelated full re-read.
    """
    bus = _CollectingBus()
    pipe = _pipeline(_RecordingSender(), bus=bus)

    asyncio.run(pipe.send(
        person_id="owner", channel="web", text="here you go",
        urgency="normal", in_reply_to=uuid4(),
    ))

    events = bus.of(EventKind.MESSAGE_NEW)
    assert len(events) == 1
    ts = events[0].get("ts")
    assert isinstance(ts, str) and ts, "message.new must carry a timestamp"
    from datetime import datetime

    assert datetime.fromisoformat(ts).timestamp() > 0


def test_a_recorded_reply_reports_its_delivery_truthfully() -> None:
    """`delivery` is read as the block it is.

    The event's own delivery block says `sent` or `queued`; reading `status` off
    the urgency field instead reported every message as sent with urgency
    `normal`, which made a batched reply look delivered and a delivered reply
    indistinguishable from a queued one.
    """
    bus = _CollectingBus()
    pipe = _pipeline(_RecordingSender(), bus=bus)

    asyncio.run(pipe.send(
        person_id="owner", channel="web", text="a reply", urgency="normal",
        in_reply_to=uuid4(),
    ))

    delivery = bus.of(EventKind.MESSAGE_NEW)[0].get("delivery")
    assert isinstance(delivery, dict)
    assert delivery.get("status") == "sent"
    assert delivery.get("urgency") == "normal"


def test_a_deliberate_silence_is_declared_with_the_message_it_answers() -> None:
    """The agent's right to answer with nothing has to be observable.

    A surface can only tell a declined question from a broken link if the decline
    names the question. The id is the whole mechanism: without it there is no way
    to know which turn the agent was silent about, and every wait has to run out
    before it can be reported as anything at all.
    """
    bus = _CollectingBus()
    config = make_config(Path("/tmp/ethos-test-decline"))
    config.paths.ensure()

    class _Social:
        def __init__(self) -> None:
            self.message = _Message()

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [self.message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.message.handled = True

        async def record_ignore(self, person_id: Any, reason: Any) -> dict[str, Any]:
            return {"count": 1, "review": False}

    class _Message:
        def __init__(self) -> None:
            self.id = uuid4()
            self.person_id = "owner"
            self.channel = "web"
            self.conversation_key = "web:owner"
            self.text = "are you there?"
            self.handled = False

    class _Quiet:
        """Nothing needs to be real: this test is about what gets published."""

        def __getattr__(self, name: str) -> Any:
            async def _noop(*_: Any, **__: Any) -> Any:
                return None

            return _noop

    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=Attention(config, embedder=HashingEmbedder(1024),
                            gateway=ScriptedGateway([]), self_model=_Quiet()),
        motivation=Motivation(config), rhythm=RhythmController(config),
        gateway=ScriptedGateway([]), gsl=_Quiet(), social=_Social(),
        comms_out=_Quiet(), toolhost=None, audit=ListAudit(), continuity=_Quiet(),
        consolidation=None, embedder=HashingEmbedder(1024), thread_id="self",
    ))

    asyncio.run(life._act_social({
        "action": "ignore", "reply_texts": [], "urgency": "normal",
        "reason": "nothing to add", "ignore_reason": "no_response_needed",
        "creates_intention": False, "intention_spec": None, "tier_used": "T2",
    }))

    declines = bus.of(EventKind.MESSAGE_HANDLED)
    assert len(declines) == 1, "a declined question must be declared, not just withheld"
    assert declines[0]["message_id"] == str(life.deps.social.message.id)
    assert declines[0]["action"] == "ignore"
    assert life.deps.social.message.handled, "the question is finished with, one way or another"


def test_a_life_loop_survives_a_cycle_that_raises() -> None:
    """The process does not exit [P1, 4.1].

    A provider that is out of keys, out of eligible models, or briefly refusing
    is an ordinary Tuesday, and it used to end the agent: the exception unwound
    the life loop, the process exited, and every surface waiting on it was told
    the agent had gone quiet with nothing anywhere recording that the agent had
    stopped. A failed cycle is a failed cycle.
    """
    config = make_config(Path("/tmp/ethos-test-survive"))
    config.paths.ensure()
    cycles = {"done": 0}

    class _Control:
        stopped = False
        paused = False

        async def refresh(self) -> None:
            return None

        async def wait_resume(self) -> None:
            await asyncio.sleep(0)

    class _Rhythm:
        def __init__(self) -> None:
            self.waits = 0

        async def wait_until_next(self, state: Any) -> None:
            self.waits += 1
            await asyncio.sleep(0)

    rhythm = _Rhythm()

    class _Life(Life):
        async def wake_up(self) -> None:
            return None

        async def _cycle(self) -> None:
            cycles["done"] += 1
            if cycles["done"] < 4:
                raise RuntimeError("no eligible model for tier=T2")
            self.control.stopped = True

    life = _Life(LifeDeps(
        config=config, control=_Control(), bus=None, memory=None, self_model=None,
        attention=None, motivation=None, rhythm=rhythm, gateway=None, gsl=None,
        social=None, comms_out=None, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(asyncio.wait_for(life.life(), timeout=10))

    assert cycles["done"] == 4, "the loop must keep going after a failed cycle"
    assert rhythm.waits >= 3, "a failed cycle must still yield its tick, or it spins"


def test_a_declined_question_is_not_re_deliberated_forever() -> None:
    """Declining has to close the question, or it is asked again every cycle.

    The pending queue is everything unanswered, so a decline that did not mark
    its message handled left the same question at the head of the queue: the
    agent deliberated on it again next cycle, declined again, and worked through
    nobody else's mail in the meantime. An ignored message is still a message
    that was dealt with.
    """
    config = make_config(Path("/tmp/ethos-test-queue"))
    config.paths.ensure()
    bus = _CollectingBus()

    class _Message:
        def __init__(self) -> None:
            self.id = uuid4()
            self.person_id = "owner"
            self.channel = "web"
            self.conversation_key = "web:owner"
            self.text = "hello"
            self.handled = False

    class _Social:
        def __init__(self) -> None:
            self.message = _Message()
            self.ignores = 0

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.message.handled else [self.message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.message.handled = True

        async def record_ignore(self, person_id: Any, reason: Any) -> dict[str, Any]:
            self.ignores += 1
            return {"count": self.ignores, "review": False}

    class _Quiet:
        def __getattr__(self, name: str) -> Any:
            async def _noop(*_: Any, **__: Any) -> Any:
                return None

            return _noop

    social = _Social()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=_Quiet(), toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    decline = {
        "action": "ignore", "reply_texts": [], "urgency": "normal",
        "reason": "", "ignore_reason": "no_response_needed",
        "creates_intention": False, "intention_spec": None, "tier_used": "T2",
    }
    asyncio.run(life._act_social(decline))
    assert social.message.handled
    assert await_pending(social) == [], "a declined message must leave the queue"
    assert len(bus.of(EventKind.MESSAGE_HANDLED)) == 1


def test_an_ignored_message_is_finished_with_exactly_once() -> None:
    """Carrying out an `ignore` used to call a method that did not exist.

    `_act_social` asked the social cognition to `record_ignore`, which only the
    relationship store had: the AttributeError failed the cycle before the
    message was marked handled and before the decline was announced. The
    question stayed at the head of the pending queue — re-deliberated and
    re-paid for on every cycle, blocking every message behind it — while the
    surface, told nothing, waited out its budget and reported a stall. One
    considered silence read as an agent that had gone quiet for good.

    The count also belonged to the deliberation, so a message that reached the
    act had already counted its own silence, and the act counted it again.
    Choosing to be quiet and being quiet are different moments; this pins the
    count to the second one, through the real cognition and the real streak
    counter rather than a stand-in for either.
    """
    config = make_config(Path("/tmp/ethos-test-count-once"))
    config.paths.ensure()
    bus = _CollectingBus()
    out = _RecordingOut()
    message = Message(person_id="owner", channel="web",
                      conversation_key="web:owner", text="hello?")
    gateway = ScriptedGateway([
        '{"action": "ignore", "reply_texts": [], "urgency": "normal", '
        '"reason": "nothing to add", "ignore_reason": "no_response_needed", '
        '"creates_intention": false, "intention_spec": null}',
    ])

    class _QueueSocial(SocialCognition):
        """The real cognition and the real streaks; only the queue and the
        handled mark are in memory, because a unit test has no database."""

        def __init__(self) -> None:
            super().__init__(config, gateway=gateway,
                             relationships=RelationshipStore(config))
            self.handled = False
            self.handled_decision: dict[str, Any] | None = None

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True
            self.handled_decision = decision

    social = _QueueSocial()
    decision = asyncio.run(social.deliberate(message))
    assert decision.action.value == "ignore"
    assert social.relationships._streaks == {}, "deliberating a silence is not yet being silent"

    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=out, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))
    asyncio.run(life._act_social(decision.model_dump(mode="json")))

    assert social.handled, "the declined question is finished with"
    assert social.handled_decision is not None, \
        "the row must record what was decided, not merely that something was"
    assert social.handled_decision.get("action") == "ignore"
    assert social.handled_decision.get("ignore_reason") == "no_response_needed"

    assert social.handled, "the declined question is finished with"
    assert await_pending(social) == [], "it must not sit for a second deliberation"
    declines = bus.of(EventKind.MESSAGE_HANDLED)
    assert len(declines) == 1 and declines[0]["action"] == "ignore"
    assert out.sent == [], "an upheld silence sends nothing"
    assert social.relationships._streaks["owner"]["count"] == 1, \
        "one message, one silence on the streak"


def test_a_silence_the_agent_overrules_is_answered_not_declared() -> None:
    """Three silences in a row buy the streak a second look [D-05], and the
    second look is allowed to break it.

    The review existed — the streak counter has reported `review: True` at the
    threshold all along — but nothing anywhere read the flag, so the one
    mechanism whose job is "don't clam up when it matters" never ran. Here the
    review finds the silence indefensible and says so with an answer, and the
    answer must actually reach the person: it goes out under the message's own
    id, as a reply, so it is delivered now rather than spending two hours in
    the digest. A question the review answered must not also be declared
    declined — the surface would take whichever arrived first.
    """
    config = make_config(Path("/tmp/ethos-test-overrule"))
    config.paths.ensure()
    bus = _CollectingBus()
    out = _RecordingOut()

    class _Message:
        def __init__(self) -> None:
            self.id = uuid4()
            self.person_id = "owner"
            self.channel = "web"
            self.conversation_key = "web:owner"
            self.text = "are you there? I need the deploy numbers before lunch"

    class _ReviewSocial:
        def __init__(self, message: Any, report: dict[str, Any], review: dict[str, Any]) -> None:
            self.message = message
            self.report = report
            self.review = review
            self.handled = False
            self.reviewed_with: dict[str, Any] | None = None

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [self.message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True

        async def record_ignore(self, person_id: Any, reason: Any) -> dict[str, Any]:
            return self.report

        async def streak_self_review(self, person_id: Any, reasons: list[str], n: int,
                                      latest_text: str = "") -> dict[str, Any]:
            self.reviewed_with = {"person_id": person_id, "reasons": list(reasons),
                                  "n": n, "latest_text": latest_text}
            return self.review

    message = _Message()
    social = _ReviewSocial(
        message,
        {"count": 3, "reasons": ["no_response_needed", "boundary", "no_response_needed"],
         "review": True},
        {"continue_ignoring": False, "should_message": True,
         "message": "You're right — three silences is too many. Pulling the numbers now.",
         "reflection": "the questions were ordinary and the deadline was named; "
                       "the silence was not defensible"},
    )
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=out, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(life._act_social({
        "action": "ignore", "reply_texts": [], "urgency": "normal",
        "reason": "nothing to add", "ignore_reason": "no_response_needed",
        "creates_intention": False, "intention_spec": None, "tier_used": "T2",
    }))

    assert social.handled, "the answered question is finished with"
    assert len(out.sent) == 1, "an overruled silence must be answered, not annotated"
    reply = out.sent[0]
    assert reply["in_reply_to"] == message.id, "the answer must name the question it answers"
    assert reply["text"].startswith("You're right")
    assert bus.of(EventKind.MESSAGE_HANDLED) == [], \
        "a question that was answered must not also be declared declined"
    assert social.reviewed_with is not None, "a review due must actually run"
    assert social.reviewed_with["n"] == 3
    assert social.reviewed_with["latest_text"] == message.text, \
        "the review must see the message it is about to be silent on"


def test_a_silence_the_review_upholds_is_still_declared() -> None:
    """The review is a second look, not a ban on silence.

    A boundary genuinely being tested is exactly what the streak counter is for
    finding, and the review upholding the silence is the system working. What
    must not regress is the person's side of it: the upheld silence is declared
    against the message it answers, so the wait ends as a decline with a reason
    rather than running out as a stall.
    """
    config = make_config(Path("/tmp/ethos-test-uphold"))
    config.paths.ensure()
    bus = _CollectingBus()
    out = _RecordingOut()

    class _Message:
        def __init__(self) -> None:
            self.id = uuid4()
            self.person_id = "owner"
            self.channel = "web"
            self.conversation_key = "web:owner"
            self.text = "you there? you there? you there?"

    class _ReviewSocial:
        def __init__(self) -> None:
            self.message = _Message()
            self.handled = False
            self.review_ran = False

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [self.message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True

        async def record_ignore(self, person_id: Any, reason: Any) -> dict[str, Any]:
            return {"count": 3, "reasons": ["boundary"] * 3, "review": True}

        async def streak_self_review(self, person_id: Any, reasons: list[str], n: int,
                                     latest_text: str = "") -> dict[str, Any]:
            self.review_ran = True
            return {"continue_ignoring": True, "should_message": False, "message": None,
                    "reflection": "a boundary is being tested; the silence is the point"}

    social = _ReviewSocial()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=out, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(life._act_social({
        "action": "ignore", "reply_texts": [], "urgency": "normal",
        "reason": "testing a boundary", "ignore_reason": "boundary",
        "creates_intention": False, "intention_spec": None, "tier_used": "T2",
    }))

    assert social.review_ran
    assert social.handled
    assert out.sent == [], "an upheld silence sends nothing"
    declines = bus.of(EventKind.MESSAGE_HANDLED)
    assert len(declines) == 1
    assert declines[0]["action"] == "ignore"
    assert declines[0]["reason"] == "boundary"


def test_a_review_that_promises_an_answer_and_has_none_changes_nothing() -> None:
    """`should_message` with no text is an answer that does not exist.

    The review is a model call, and a model can come back half-formed: willing
    to break the silence, offering nothing to break it with. Sending an empty
    reply is not an option — it resolves a wait with nothing, which is the one
    thing this whole pipeline refuses — so the silence stands and is declared,
    and the person is told why rather than left waiting on an answer that was
    never going to arrive.
    """
    config = make_config(Path("/tmp/ethos-test-empty-overrule"))
    config.paths.ensure()
    bus = _CollectingBus()
    out = _RecordingOut()

    class _Message:
        def __init__(self) -> None:
            self.id = uuid4()
            self.person_id = "owner"
            self.channel = "web"
            self.conversation_key = "web:owner"
            self.text = "hello?"

    class _ReviewSocial:
        def __init__(self) -> None:
            self.message = _Message()
            self.handled = False

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [self.message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True

        async def record_ignore(self, person_id: Any, reason: Any) -> dict[str, Any]:
            return {"count": 3, "reasons": ["no_response_needed"] * 3, "review": True}

        async def streak_self_review(self, person_id: Any, reasons: list[str], n: int,
                                     latest_text: str = "") -> dict[str, Any]:
            return {"continue_ignoring": False, "should_message": True, "message": None,
                    "reflection": "meant to say something; did not"}

    social = _ReviewSocial()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=out, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(life._act_social({
        "action": "ignore", "reply_texts": [], "urgency": "normal",
        "reason": "nothing to add", "ignore_reason": "no_response_needed",
        "creates_intention": False, "intention_spec": None, "tier_used": "T2",
    }))

    assert social.handled
    assert out.sent == [], "an empty answer is nothing, and nothing must not be sent"
    assert len(bus.of(EventKind.MESSAGE_HANDLED)) == 1, \
        "the silence stands, and standing silences are declared"


async def _pending(social: Any) -> list[Any]:
    return await social.pending_inbound(limit=5)


def await_pending(social: Any) -> list[Any]:
    return asyncio.run(_pending(social))


def test_a_paused_agent_keeps_reporting_that_it_is_there(monkeypatch: Any) -> None:
    """A pause is a state the agent is in, not an absence of one.

    Blocking on the resume event alone published nothing for the whole pause: no
    cycle, no presence, nothing. Every surface holding a question then read a
    deliberate pause as an agent that had stopped existing — the link had heard
    nothing for the whole budget, which is exactly what an absent agent sounds
    like — and reported the pause as a stall. So the parked wait is bounded, and
    each pass says the agent is still here and is waiting.
    """
    monkeypatch.setattr("ethos.engine.life.PARKED_HEARTBEAT_S", 0.01)
    config = make_config(Path("/tmp/ethos-test-parked"))
    config.paths.ensure()
    bus = _CollectingBus()

    class _Control:
        def __init__(self) -> None:
            self.stopped = False
            self.paused = True
            self.refreshes = 0

        async def refresh(self) -> None:
            self.refreshes += 1
            # The resume arrives from a surface, a few passes in — and only a
            # surface knows, because it is the surface that pressed pause.
            if self.refreshes >= 3:
                self.paused = False

        async def wait_resume(self) -> None:
            await asyncio.sleep(0)

    control = _Control()

    class _Life(Life):
        async def wake_up(self) -> None:
            return None

        async def _cycle(self) -> None:
            self.control.stopped = True

    life = _Life(LifeDeps(
        config=config, control=control, bus=bus, memory=None, self_model=None,
        attention=None, motivation=None, rhythm=RhythmController(config), gateway=None,
        gsl=None, social=None, comms_out=None, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(asyncio.wait_for(life.life(), timeout=10))

    parked = [p for p in bus.of(EventKind.PRESENCE_UPDATE) if p.get("state") == "PAUSED"]
    assert parked, "a paused agent must still say it is here"
    assert not control.paused, "the wait must end when the agent is resumed"


def test_the_kill_switch_is_re_read_by_the_running_agent(tmp_path: Path) -> None:
    """A stop file dropped mid-run stops the run [H1].

    `ControlPlane.refresh` is documented as called at the top of every cycle —
    files are truth, re-checked, undone only by removing them — and nothing
    called it. So the one switch H1 calls always effective was effective until the
    next restart: the host wrote the file, and an agent that was supposed to be
    dead went on living with the file on disk beside it.
    """
    from ethos.core.control import ControlPlane

    config = make_config(tmp_path / "home")
    config.paths.ensure()

    class _Control(ControlPlane):
        pass

    control = _Control(None, str(config.paths.run_dir))
    asyncio.run(control.load())

    class _Life(Life):
        async def wake_up(self) -> None:
            return None

        async def _cycle(self) -> None:
            # The host pulls the switch while the agent is mid-run.
            (config.paths.run_dir / "stop").write_text("")

    life = _Life(LifeDeps(
        config=config, control=control, bus=None, memory=None, self_model=None,
        attention=None, motivation=None, rhythm=RhythmController(config), gateway=None,
        gsl=None, social=None, comms_out=None, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))

    asyncio.run(asyncio.wait_for(life.life(), timeout=10))

    assert control.stopped, "the agent must obey a kill switch written while it runs"


# --------------------------------------------------------------------------
# A turn the agent never had an opinion for


def _question(text: str = "are you still there?") -> Message:
    return Message(person_id="owner", channel="web",
                   conversation_key="web:owner", text=text)


class _QueueSocial:
    """A pending question that the store hands over exactly once."""

    def __init__(self, message: Message) -> None:
        self.message = message
        self.handled = False
        self.decided: Any = None
        self.failed: list[dict[str, Any]] = []

    async def pending_inbound(self, limit: int = 5) -> list[Any]:
        return [] if self.handled else [self.message]

    async def mark_handled(self, message: Any, decision: Any = None) -> None:
        self.handled = True
        self.decided = decision

    async def record_failure(self, message: Any, decision: dict[str, Any]) -> dict[str, Any]:
        self.failed.append(decision)
        return decision


def _life_over(social: _QueueSocial, out: _RecordingOut, bus: _CollectingBus,
               config: Any) -> Life:
    return Life(LifeDeps(
        config=config, control=_Quiet(), bus=bus, memory=_Quiet(), self_model=_Quiet(),
        attention=None, motivation=None, rhythm=RhythmController(config), gateway=None,
        gsl=None, social=social, comms_out=out, toolhost=None, audit=ListAudit(),
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))


def _deliberation(gateway: Any, tmp: Path) -> Any:
    config = make_config(tmp / "home")
    config.paths.ensure()
    return asyncio.run(SocialCognition(config=config, gateway=gateway).deliberate(_question()))


class _RaisingGateway:
    async def complete(self, request: Any) -> Any:
        raise RuntimeError("no eligible models: every key is exhausted")


def test_a_deliberation_the_agent_never_had_is_not_answered_for_it() -> None:
    """No opinion formed means no sentence invented [D-05].

    Every failure below used to leave a fixed sentence standing where the
    agent's own words should have been: no gateway produced a bracketed
    acknowledgement, a provider that raised produced "I got your message and
    I'm on it", a response that could not be parsed as JSON produced the same
    thing, and an action that arrived without any text underneath it produced
    "I hear you."

    The reason that mattered is that nothing downstream could tell the
    difference. Each of those went out through the ordinary reply path, so it
    was stored as a real `direction='outbound'` row, published as
    `MESSAGE_NEW`, and rendered in the same agent bubble, under the same
    "assistant" label, as a considered reply. A person watching the agent talk
    to itself through an outage had no way to know it was talking at all, and
    the transcript was the one place that record would have been kept.

    So the agent says nothing it did not decide to say. A failure ends the
    turn in a recorded silence, which is already a state this pipeline knows
    how to hold and declare, and the reason travels with the decision so the
    breach is legible in the log and in the agent's own context.

    A response in prose is not in this list, and used to be. It was here on the
    reasoning that prose is not a deliberation, so nothing was weighed and
    there is nothing to have an opinion about -- which is true of the decision
    and false of the answer. Measured against the base model on a 2,600
    character message, the model answered in prose 5 times in 14 and the object
    the other 9; the prose was a finished, on-point reply in every one of them,
    and discarding it is what put "the model's answer could not be read; nothing
    said in its place" in front of a person who had been answered. Those words
    are the model's own and were written for that person, so they are used --
    by `test_an_answer_written_as_prose_is_the_reply_the_person_gets`, which is
    where the shape of that answer is now pinned.
    """
    for label, gateway in (
        ("no gateway at all", None),
        ("a provider that raised", _RaisingGateway()),
        ("truncated JSON", ScriptedGateway(['{"action": "reply_now", "reply_texts": ["I thi'])),
        ("an empty response", ScriptedGateway([""])),
        ("an answer that is one object and says nothing about the turn",
         ScriptedGateway(['{"note": "still deciding"}', '{"note": "still deciding"}'])),
    ):
        decision = _deliberation(gateway, Path("/tmp/ethos-test-no-opinion"))

        assert decision.reply_texts == [], (
            f"{label} must not put words in the agent's mouth, but it sent "
            f"{decision.reply_texts!r}"
        )
        assert decision.action.value == "act_silently", (
            f"{label} is not a choice the agent made; it must not be recorded as one"
        )
        assert decision.reason, (
            f"{label} has to say why the person is getting nothing, or the "
            f"silence is indistinguishable from a decision"
        )


def test_a_reply_action_with_no_text_under_it_ends_in_silence() -> None:
    """An action that promises words, delivered without any, sends nothing.

    This is the case that produced "I hear you." — the model wrote an action
    and the text underneath it went missing, and the missing text was filled in
    so the turn would still look answered. The fill-in is the bug: the
    stand-in is not marked anywhere, so it is read as the agent's own reply.
    """
    decision = _deliberation(
        ScriptedGateway(['{"action": "reply_now", "reason": "asked a direct question"}']),
        Path("/tmp/ethos-test-no-text"),
    )

    assert decision.reply_texts == [], "there was no reply to send, so none is sent"
    assert decision.action.value == "act_silently"
    assert "no text" in decision.reason, "the breach is recorded next to the decision"
    assert "asked a direct question" in decision.reason, \
        "what the model did say about itself is kept"


def test_a_null_reply_is_a_silence_rather_than_a_crash() -> None:
    """`"reply_texts": null` is an ordinary thing for a model to write.

    It was iterated straight into `[str(t) for t in data.get(...)]`, so an
    explicit null raised `TypeError` out of the deliberation and took the turn
    with it. A bare string in the same slot was worse than a crash: iterated
    character by character, it delivered a one-word answer to the person one
    letter at a time.
    """
    decision = _deliberation(
        ScriptedGateway(['{"action": "reply_now", "reply_texts": null}']),
        Path("/tmp/ethos-test-null-reply"),
    )

    assert decision.reply_texts == []

    decision = _deliberation(
        ScriptedGateway(['{"action": "reply_now", "reply_texts": "hello there"}']),
        Path("/tmp/ethos-test-string-reply"),
    )

    assert decision.reply_texts == [], "a string is not a list of replies"


def test_whitespace_and_overlong_replies_are_kept_or_dropped_not_invented() -> None:
    """The normalising of what the model wrote, and nothing beyond it."""
    config = make_config(Path("/tmp/ethos-test-reply-shape"))
    config.paths.ensure()

    decision = asyncio.run(SocialCognition(config=config, gateway=ScriptedGateway([
        '{"action": "reply_now", "reply_texts": ["  hi there  ", "   ", "\\n"]}'
    ])).deliberate(_question()))

    assert decision.reply_texts == ["hi there"], (
        "one real reply in, one real reply out; the blanks are not replies"
    )

    decision = asyncio.run(SocialCognition(config=config, gateway=ScriptedGateway([
        '{"action": "reply_now", "reply_texts": ["one", "two", "three", "four"]}'
    ])).deliberate(_question()))

    assert len(decision.reply_texts) == 3, "the reply stays as long as it was always allowed to be"


def test_a_deliberation_that_reads_well_still_answers_the_question() -> None:
    """The guard is on silence, not on replies.

    Worth stating outright, because a fix for "the agent says things it did
    not decide to say" is easy to write into "the agent says less": the turn
    that came back readable and complete goes out in full, with its own words,
    exactly as before.
    """
    out = _RecordingOut()
    bus = _CollectingBus()
    config = make_config(Path("/tmp/ethos-test-real-reply"))
    config.paths.ensure()
    message = _question("what did you make of the second pass?")
    social = _QueueSocial(message)
    life = _life_over(social, out, bus, config)

    reply = 'Yes — the tempo was right. I took the second pass tonight.'
    decision = asyncio.run(SocialCognition(
        config=config, gateway=ScriptedGateway([
            f'{{"action": "reply_now", "reply_texts": ["{reply}"], "reason": "asked directly"}}'
        ])).deliberate(message))

    assert decision.reply_texts == [reply]
    assert decision.action.value == "reply_now"

    asyncio.run(life._act_social(decision.model_dump(mode="json")))

    assert len(out.sent) == 1, "a readable deliberation is still answered"
    assert out.sent[0]["text"] == reply
    assert out.sent[0]["in_reply_to"] == message.id
    assert bus.of(EventKind.MESSAGE_HANDLED) == [], "an answered question is not a decline"


def test_a_failed_deliberation_is_not_reported_to_the_person_as_a_choice() -> None:
    """End to end, the whole turn: nothing invented, nothing lost, nothing claimed.

    The decision is not the end of the story — it still has to survive the
    reply path, and the person is still waiting. What they must not get is a
    sentence nobody chose. What they *should* get is the truth about which of two
    things happened, and this is where the difference is made.

    The turn used to be closed as a declared silence whatever had actually
    happened, so a model that never answered produced exactly the record and
    exactly the surface event a considered silence produces: the message retired,
    `message.handled` published for the id the person had just asked, and the
    client rendering "The agent chose not to reply. · the model's answer could not
    be read". That is the agent declining on every message it was sent, on the
    record, because a model had run out of room to think.

    So the failure is announced as a failure (`decided: false` travels with the
    event for a surface to read), and the message stays on the queue: nothing was
    decided, so the question has not been dealt with, and a model that recovers
    answers it on a later cycle without the person asking twice.
    """
    out = _RecordingOut()
    bus = _CollectingBus()
    config = make_config(Path("/tmp/ethos-test-failed-turn"))
    config.paths.ensure()
    message = _question("are you still there?")
    social = _QueueSocial(message)
    life = _life_over(social, out, bus, config)

    decision = _deliberation(_RaisingGateway(), Path("/tmp/ethos-test-failed-turn-2"))
    assert decision.decided is False, "no opinion was formed, and the decision says so"
    asyncio.run(life._act_social(decision.model_dump(mode="json")))

    assert out.sent == [], "a provider that raised must not produce a reply"
    assert not social.handled, (
        "nobody decided this question, so it is not dealt with; retiring the row "
        "is how a transient outage cost somebody their message permanently"
    )
    assert len(social.failed) == 1, "the attempt is counted and its reason recorded"
    assert social.failed[0]["decided"] is False
    assert social.failed[0]["reason"], "the reason is what the agent reads back later"
    declines = bus.of(EventKind.MESSAGE_HANDLED)
    assert len(declines) == 1, "the turn ends, so the wait ends"
    assert declines[0]["message_id"] == str(message.id), "it names the question it answers"
    assert declines[0]["decided"] is False, (
        "and says the agent could not answer, rather than choosing not to"
    )


def test_a_considered_silence_is_still_a_choice() -> None:
    """The other side of that flag, so it cannot quietly stop meaning anything.

    A silence the agent actually chose is [D-05] and is reported as one: the row
    is retired, the decline is published with `decided` true, and nothing about
    it reads as a failure that should be retried.
    """
    out = _RecordingOut()
    bus = _CollectingBus()
    config = make_config(Path("/tmp/ethos-test-chosen-silence"))
    config.paths.ensure()
    message = _question("are you still there?")
    social = _QueueSocial(message)
    life = _life_over(social, out, bus, config)

    asyncio.run(life._act_social({
        "action": "act_silently", "reply_texts": [], "urgency": "normal",
        "reason": "nothing to add yet", "ignore_reason": None,
        "creates_intention": False, "intention_spec": None, "tier_used": "T2",
        "decided": True,
    }))

    assert social.handled, "a chosen silence is finished with"
    assert social.failed == [], "and is never mistaken for one to try again"
    declines = bus.of(EventKind.MESSAGE_HANDLED)
    assert len(declines) == 1 and declines[0]["decided"] is True


# --------------------------------------------------------------------------
# An answer the provider ran out of room to finish


# Verbatim from a deliberation that hit the ceiling: the object stops after
# `"ignore_reason": [], "` with no closing brace, which is what a response cut
# off at `max_tokens` looks like from here. Everything the agent would have said
# to the person is in it.
_CUT_OFF_AFTER_THE_REPLY = (
    '{"action":"acknowledge","reply_texts":["Got it. One self-contained `snake.html`'
    '—no build step, no extra files."],"urgency":"normal","reason":"The request is '
    'clear, and a brief acknowledgment avoids further delay or overexplaining.",'
    '"ignore_reason":[],"'
)
_THE_REPLY = "Got it. One self-contained `snake.html`—no build step, no extra files."


def test_a_deliberation_cut_off_after_the_reply_is_still_sent() -> None:
    """The agent answered, and the ceiling is what made it look like it had not.

    The ceiling this call asked for was 900 tokens for a whole answer: the
    decision, the reply the person reads, and the bookkeeping around both. A
    model that thinks at length before emitting that object runs out of room
    partway through the trailing bookkeeping — after the words are written — and
    an object with no closing brace does not parse. The whole answer was then
    thrown away and the turn ended in `_undecided`, which records a silence,
    marks the message handled, and publishes a decline. From the outside that is
    indistinguishable from an agent that chose not to answer, and nothing
    retried it: three messages in the last four that reached this function.

    So a member that closed is a member the model wrote. It is used, and the
    reply reaches the person.
    """
    out = _RecordingOut()
    bus = _CollectingBus()
    config = make_config(Path("/tmp/ethos-test-cut-off"))
    config.paths.ensure()
    message = _question("just write me the snake game, please")
    social = _QueueSocial(message)
    life = _life_over(social, out, bus, config)

    decision = asyncio.run(SocialCognition(
        config=config,
        gateway=ScriptedGateway([_CUT_OFF_AFTER_THE_REPLY], finish_reasons=["length"]),
    ).deliberate(message))

    assert decision.reply_texts == [_THE_REPLY], (
        "the reply was written in full before the cut; discarding it sent the "
        "person nothing the agent had not already decided to say"
    )
    assert decision.action.value == "acknowledge"
    assert "cut off" in decision.reason, \
        "the record says the answer was incomplete, so it is not read as a whole one"

    asyncio.run(life._act_social(decision.model_dump(mode="json")))

    assert len(out.sent) == 1, "a reply that survived the cut still goes out"
    assert out.sent[0]["text"] == _THE_REPLY
    assert out.sent[0]["in_reply_to"] == message.id
    assert bus.of(EventKind.MESSAGE_HANDLED) == [], \
        "a question that was answered is not a decline, however the answer arrived"


def test_a_reply_the_ceiling_cut_in_half_is_never_delivered_as_a_fragment() -> None:
    """What the tolerant parse must not do is finish the sentence for it.

    The other half of the same failure: when the cut lands *inside* the reply,
    what survives is half a word. Completing it would put words in the agent's
    mouth that it never chose, which is the breach the canned-string fixes in
    this file were about. So the partial reply is dropped and the action that
    promised it ends as the silence it turned out to be — which is what the
    reply-action-without-text rule already says, reached here honestly.
    """
    decision = _deliberation(
        ScriptedGateway(
            ['{"action":"reply_now","reply_texts":["Yes — the tempo was right, I took the second'],
            finish_reasons=["length"],
        ),
        Path("/tmp/ethos-test-cut-mid-reply"),
    )

    assert decision.reply_texts == [], "half a sentence is not a reply"
    assert decision.action.value == "act_silently", \
        "an action that promised words and has none left is a silence, not a reply"
    assert decision.reason, "the person is owed an account of why they got nothing"


def test_a_deliberation_cut_off_before_it_decided_anything_is_still_a_silence() -> None:
    """Recovery cannot rescue an answer that had not decided yet.

    The ceiling is the fix, not the parse: when the cut lands before the first
    member closes there is no decision to recover, and the turn must still end
    as an unanswered question rather than a default action the model never
    chose. This is the same `_undecided` the unreadable-answer case gets, and
    for the same reason — nothing was formed.
    """
    for label, text in (
        ("cut inside the first member", '{"act'),
        ("cut after a key and before its value", '{"action":"reply_now","reply_texts":'),
    ):
        decision = _deliberation(
            ScriptedGateway([text], finish_reasons=["length"]),
            Path("/tmp/ethos-test-cut-nothing"),
        )

        assert decision.reply_texts == [], f"{label} must not produce a reply"
        assert decision.action.value == "act_silently", \
            f"{label} formed no opinion, and must not be recorded as one"


def test_the_deliberation_asks_for_enough_room_to_finish() -> None:
    """The ceiling itself, because the parse above is the second line of defence.

    `max_tokens` was 900 for an answer that has to carry a JSON envelope and the
    words a person reads, and the deliberation calls in `model_calls` finish at
    exactly 900 output tokens. Sized against what the model actually spends --
    complete answers came in under 900 -- rather than left at a round number
    that happens to cut the envelope in half.
    """
    config = make_config(Path("/tmp/ethos-test-ceiling"))
    config.paths.ensure()
    gateway = ScriptedGateway([
        '{"action": "reply_now", "reply_texts": ["hi"], "reason": "asked directly"}',
    ])

    asyncio.run(SocialCognition(config=config, gateway=gateway).deliberate(_question()))

    assert gateway.requests[0].max_tokens >= 1_200, (
        "a ceiling that leaves no room for the reply is not a saving, it is a "
        "silence with a token count attached"
    )


# --------------------------------------------------------------------------
# Nothing anywhere composes a message to send


def _ethos_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[2] / "ethos"
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _is_canned(node: ast.AST) -> bool:
    """True for a message written here rather than arrived at from elsewhere."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        # An empty string is not a sentence anybody can be credited with saying,
        # and passing a blank is a case this pipeline already refuses downstream.
        return bool(node.value.strip())
    # An f-string with nothing interpolated into it is a literal wearing a hat.
    if isinstance(node, ast.JoinedStr):
        return not any(isinstance(v, ast.FormattedValue) for v in node.values)
    return False


def _without_comments(path: Path) -> str:
    """The source with its comments taken out.

    Several of the fixes here are explained in the comments that replaced the
    strings, and those comments quote what used to be sent. A guard that cannot
    tell a note about a sentence from the sentence has to be told about the
    difference by whoever writes the next one, which is exactly the kind of
    silent coupling a guard exists to remove.
    """
    out: list[str] = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type == tokenize.COMMENT:
                continue
            out.append(token.string)
    return " ".join(out)


def test_no_module_anywhere_invents_a_message_to_send() -> None:
    """The general form of the bug, guarded against coming back.

    Every fix in this file was applied to one call site, and each of those sites
    had the same shape: a literal string passed as the `text` of something
    outbound. Nothing about that shape is disallowed by the type system or by
    any review rule — it reads like an ordinary constant — so it will keep
    being written. This is the check that says it is not allowed.

    Anything that wants to speak to a person has to have been given the words
    by a deliberation, a tool result, or a transport the user typed into, and
    `text=` must be one of those. A literal with words in it, or an f-string
    with nothing interpolated into it, is a sentence this codebase made up, and
    it would arrive in the transcript reading as the agent having said it.
    """
    offenders: list[str] = []
    for path in _ethos_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "text" and _is_canned(keyword.value):
                    offenders.append(f"{path.name}:{keyword.value.lineno}")
    assert not offenders, (
        "these compose a message instead of passing one through: "
        + ", ".join(sorted(offenders))
    )


def test_the_stand_in_sentences_are_gone_from_the_code() -> None:
    """The specific sentences, so the general guard has something to catch.

    A guard that only fails on the exact forms it already knows about is a
    description of the past. These are the strings that were sent, asserted
    against the code with its comments removed — the comments are allowed to
    go on explaining what used to happen, but nothing executable may hold one.
    """
    removed = (
        "I hear you",
        "I'm on it",
        "social cognition offline",
        "ready for your review",
        "Say 'approved'",
        "Updates since my last message",
        "declined to destroy the commissioned artifact",
        "Due process complete",
    )
    present: list[str] = []
    for path in _ethos_sources():
        code = _without_comments(path)
        for phrase in removed:
            if phrase in code:
                present.append(f"{phrase!r} in {path.name}")
    assert not present, "these were being sent to people: " + "; ".join(sorted(present))


def test_a_request_for_work_becomes_the_next_focus() -> None:
    """Saying "I will do it" has to be followed by doing it.

    The whole point of this file was the reply. This is the other half, and it is
    where a coding instruction goes to die: every surface funnels a person's message
    into `_act_social`, which answered the question and — if the deliberation had set
    `creates_intention` — created the intention behind the answer, then returned
    nothing. `next_focus` released the social focus, as it should, and what the next
    cycle worked on came from the drives instead of from the promise that had just
    been made out loud. On a live agent that is one piece of work among everything
    else; in practice the drives picked something else and the reply was the last
    anyone heard of it.

    So the commission travels out of the turn that made it and becomes that cycle's
    focus. Not arbitration's to discover — the agent already decided.
    """
    config = make_config(Path("/tmp/ethos-test-commission-focus"))
    config.paths.ensure()
    out = _RecordingOut()
    message = Message(
        person_id="owner", channel="web", conversation_key="web:owner",
        text="Add a slugify() helper to code.py and make the tests pass.",
    )

    class _QueueSocial:
        def __init__(self) -> None:
            self.handled = False

        async def pending_inbound(self, limit: int = 5) -> list[Any]:
            return [] if self.handled else [message]

        async def mark_handled(self, message: Any, decision: Any = None) -> None:
            self.handled = True

        async def record_ignore(self, *a: Any, **k: Any) -> dict[str, Any]:
            return {"count": 1, "review": False}

    class _Memory:
        def __init__(self) -> None:
            self.created: list[dict[str, Any]] = []

        async def create_intention(self, **kwargs: Any) -> Any:
            self.created.append(kwargs)
            # The real store hands back a UUID, and the focus is built from that
            # value, so the fake has to as well.
            return uuid4()

    social = _QueueSocial()
    memory = _Memory()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=_CollectingBus(), memory=memory,
        self_model=_Quiet(), attention=None, motivation=None, rhythm=_Quiet(),
        gateway=None, gsl=None, social=social, comms_out=out, toolhost=None,
        audit=ListAudit(), continuity=_Quiet(), consolidation=None,
        embedder=None, thread_id="self",
    ))

    commission = asyncio.run(life._act_social({
        "action": "reply_now",
        "reply_texts": ["On it — slugify() and the tests."],
        "urgency": "normal", "reason": "the owner asked for a code change",
        "ignore_reason": None, "creates_intention": True,
        "intention_spec": {"title": "add slugify() to code.py",
                           "desired_end_state": "slugify() exists and the tests pass",
                           "deadline_iso": None},
        "tier_used": "T2",
    }))

    assert len(out.sent) == 1, "the person is still answered in the turn that accepts the work"
    assert commission is not None, "the work that was promised is not lost with the turn"
    assert memory.created and memory.created[0]["kind"] == "commitment", \
        "it is a commitment, because somebody else is waiting on it"

    settled = life.next_focus(
        Decision(kind="social", tier="T2", social={"action": "reply_now"}),
        Outcome(commission=commission),
    )

    assert settled is not None, "the promise was made and the focus was dropped"
    assert str(settled.intention_id) == commission["intention_id"], \
        "and the focus is not bound to the work that was committed, so `decide` cannot act on it"
    assert settled.title == "add slugify() to code.py"


def test_a_message_that_asked_for_nothing_still_releases_its_focus() -> None:
    """The fix above is narrow on purpose.

    A social focus is spent the moment its message is handled — 2235 cycles were
    spent holding one, in `test_after_answering_someone_it_can_think_again`. Only
    work the turn actually committed to earns a successor, so ordinary conversation
    and a deliberate silence both end the focus exactly as they did.
    """
    config = make_config(Path("/tmp/ethos-test-no-commission"))
    config.paths.ensure()
    life = Life(LifeDeps(
        config=config, control=_Quiet(), bus=_CollectingBus(), memory=_Quiet(),
        self_model=_Quiet(), attention=None, motivation=None, rhythm=_Quiet(),
        gateway=None, gsl=None, social=None, comms_out=None, toolhost=None,
        audit=ListAudit(), continuity=_Quiet(), consolidation=None,
        embedder=None, thread_id="self",
    ))
    decision = Decision(kind="social", tier="T2")

    assert life.next_focus(decision, Outcome()) is None, \
        "nothing was committed, so nothing carries over"
    assert life.next_focus(decision, Outcome(commission=None)) is None, \
        "an empty commission is not work"
    assert life.next_focus(decision, Outcome(commission={"intention_id": ""})) is None, \
        "nor is one that names no intention"


def test_the_deliberation_is_told_that_answering_is_not_doing() -> None:
    """The cause, which is a sentence in the prompt rather than a branch in the code.

    Every one of the six actions this deliberation can choose is social, and the
    prompt said so — `creates_intention` appeared only as a key in the JSON schema
    with nothing written about when to set it. Asked what to do about "add slugify()
    to code.py", the only thing the prompt had invited was a reply, so the model
    replied: "sure, I'll do that", `creates_intention: false`, and no work anywhere.
    The rest of the system — plan, step, tools, verification — is sound and was never
    reached.

    The fix is the instruction, so it is asserted here rather than left to the next
    edit to the prompt to undo silently.
    """
    system = social_deliberation_messages(
        self_name="Clio",
        message=Message(person_id="owner", channel="web", conversation_key="web:owner",
                        text="add slugify() to code.py"),
        focus="(none)", now="2026-01-01T00:00:00+00:00", energy=0.8,
        commitments="(none)", recent="(none)",
    )[0]["content"]

    assert "creates_intention" in system, "the field has to be explained, not just offered"
    assert "asks for something to be done" in system, \
        "the prompt has to name the case that was going unanswered: a request for work"
    assert "false is a promise with nothing behind it" in system, \
        "replying 'I'll do it' without committing the work is the failure, and is named"
    assert "asks for something to be done" in system and "Do not ask permission" in system, \
        "and the agent is told not to ask for permission it was already given"
    assert "Do not create intentions out of conversation" in system, \
        "or every greeting becomes a task"


# --------------------------------------------------------------------------
# Finding the answer in an answer


_THE_ANSWER = '{"action": "reply_now", "reply_texts": ["Yes — I am here."], "reason": "asked"}'


def test_an_answer_written_after_its_own_thinking_is_still_the_answer() -> None:
    """The object is found in the response, not assumed to start it.

    This is the whole of it, and it is not a rare shape. The parse took the first
    `{` in the response through the last `}` in it, on the assumption that a model
    told to answer with strict JSON puts its object at the front and writes
    nothing else. Every model that thinks before answering breaks that
    assumption, and the ones that do it most are the ones that answer best: a
    preamble that weighs `{action}` against `{reply_texts}`, the schema echoed
    back before the answer, a fragment of code quoted out of the message. The
    slice then ran from a brace inside the preamble, `json.loads` refused it, and
    the complete answer was thrown away — action and the sentence the person was
    waiting for, both already written.

    What reached the person was silence, recorded as silence, on a message
    marked handled so nothing ever asked again. The reason on the row said the
    model's answer could not be read, and it could: it was sitting in the
    response, whole, in every one of these cases.
    """
    for label, response in (
        ("a preamble that names the fields", f"Let me weigh {{action}} against {{reply_texts}}.\n{_THE_ANSWER}"),
        ("the schema echoed before the answer", f'The shape is {{"action": "...", "reply_texts": ["..."]}}.\n{_THE_ANSWER}'),
        ("a fenced block", f"```json\n{_THE_ANSWER}\n```"),
        ("prose after it carrying a brace", f"{_THE_ANSWER}\n(I used {{}} as a placeholder.)"),
        ("a draft, then the answer it settled on",
         '{"action": "act_silently", "reply_texts": []}\nActually — they asked:\n' + _THE_ANSWER),
        ("a brace inside the reply itself",
         '{"action": "reply_now", "reply_texts": ["put {name} in the title"], "reason": "asked"}'),
    ):
        decision = _deliberation(
            ScriptedGateway([response]), Path("/tmp/ethos-test-where-the-object-is"),
        )

        assert decision.reply_texts, f"{label}: the answer was there and it was not read"
        assert decision.action.value in ("reply_now", "acknowledge"), (
            f"{label}: the action came back as {decision.action.value!r}"
        )


def test_an_answer_that_says_nothing_about_the_turn_is_not_a_silence_chosen() -> None:
    """What could not be read is not the same claim as what was declined.

    "Could not be read" was tested with `not data`, so only a parse that recovered
    *nothing at all* counted. An answer that parsed into something real and
    irrelevant — a fragment from a brace in the preamble, an object the ceiling
    cut off before it said anything about this turn — sailed through that check,
    took the default action, carried no text, and was then rewritten into a
    silence with a reason naming it.

    The damage is the reason. `act_silently` with a reason is the agent declining
    [D-05]: it is what a person reads as "it heard me and chose not to answer",
    and it is what the streak review and every surface take at face value. A
    parser producing that on the agent's behalf is the agent appearing to choose
    something on every message it was sent.
    """
    decision = _deliberation(
        ScriptedGateway(['{"note": "still deciding"}', '{"draft": {"x": 1}}']),
        Path("/tmp/ethos-test-answered-nothing"),
    )

    assert decision.action.value == "act_silently", "nothing goes out either way"
    assert "could not be read" in decision.reason, (
        "and it says the answer could not be read, which is what happened — not "
        "that the agent heard the question and declined to answer it"
    )


def test_the_answer_that_was_found_is_the_one_the_agent_wrote() -> None:
    """End to end: a preamble, and the reply the person reads anyway.

    The two tests above are about the parser and the record. This one is the
    symptom the parser caused, and it is the only one a person would have
    noticed: a question sent, an answer written, and nothing delivered — with the
    turn closed, so the next cycle did not ask either.
    """
    out = _RecordingOut()
    bus = _CollectingBus()
    config = make_config(Path("/tmp/ethos-test-thinking-then-answering"))
    config.paths.ensure()
    message = _question("are you still there?")
    social = _QueueSocial(message)
    life = _life_over(social, out, bus, config)

    decision = asyncio.run(SocialCognition(config=config, gateway=ScriptedGateway([
        f'The fields are {{"action", "reply_texts"}}; here is the answer.\n{_THE_ANSWER}',
    ])).deliberate(message))

    asyncio.run(life._act_social(decision.model_dump(mode="json")))

    assert len(out.sent) == 1, "the reply the model wrote is the reply the person gets"
    assert out.sent[0]["text"] == "Yes — I am here."
    assert out.sent[0]["in_reply_to"] == message.id
    assert bus.of(EventKind.MESSAGE_HANDLED) == [], \
        "a question that was answered is not a decline, however it was written"


# --------------------------------------------------------------------------
# A model that reasons where the answer was supposed to fit


def test_the_deliberation_bounds_thinking_so_the_answer_has_room() -> None:
    """`max_tokens` is not the answer's allowance on a reasoning model.

    Reasoning is billed inside `completion_tokens` and is spent out of the same
    ceiling, so a request that sets only `max_tokens` is bidding on how long the
    model thinks first. Measured against the agent's own base model, on the
    messages that produced the silences people were reporting, an answer failed
    to arrive 4 times in 6 at 900, about half the time at 2400, once in 12 at
    8000, and not once in 12 at 8000 with reasoning bounded.

    So the deliberation says how much room thinking may take, and separately how
    much the answer may take.
    """
    gateway = ScriptedGateway([_THE_ANSWER])
    decision = asyncio.run(SocialCognition(
        config=make_config(Path("/tmp/ethos-test-bounded-thinking")),
        gateway=gateway,
    ).deliberate(_question()))

    assert decision.reply_texts, "and the answer still arrives"
    sent = gateway.requests[0]
    assert sent.reasoning_budget, "thinking is told to stop somewhere"
    assert sent.reasoning_budget < sent.max_tokens, \
        "leaving the rest of the ceiling for the answer, which is what it is for"
    assert sent.max_tokens >= 8000, \
        "and the ceiling itself has room for a model that thinks at length"


def test_an_answer_that_never_arrives_is_asked_for_again_with_more_room() -> None:
    """The retry that is not the same request again.

    The gateway retries an empty reply, which helps when the model had a bad
    moment and does not help when the request was the problem: a model that
    spent the whole ceiling thinking will do it again, and asking the identical
    thing inherits the bet. The deliberation knows it needs one small object and
    knows a reasoning model may need room to reach one, so it asks for more --
    once, because two attempts is a habit and three is a bill.
    """
    gateway = ScriptedGateway(["", _THE_ANSWER])
    decision = asyncio.run(SocialCognition(
        config=make_config(Path("/tmp/ethos-test-asked-again")),
        gateway=gateway,
    ).deliberate(_question()))

    assert len(gateway.requests) == 2, "an answer that did not arrive is asked for again"
    assert gateway.requests[1].max_tokens > gateway.requests[0].max_tokens, \
        "and the second ask is a different question for the model, not the same one again"
    assert decision.reply_texts == ["Yes — I am here."], \
        "so the person gets the answer rather than the second silence"


def test_a_model_that_cannot_answer_at_all_is_not_asked_forever() -> None:
    """One escalation, and then the turn is recorded as what it was.

    The bound matters more than the retry: a model that can never answer this
    message must not be asked again every cycle, at two calls apiece, for as long
    as the agent runs.
    """
    gateway = ScriptedGateway(["", "", "", ""])
    decision = asyncio.run(SocialCognition(
        config=make_config(Path("/tmp/ethos-test-bounded-attempts")),
        gateway=gateway,
    ).deliberate(_question()))

    assert len(gateway.requests) == 2, "one ask and one escalation, and no more"
    assert decision.decided is False, "and it is never recorded as a choice"
    assert decision.reply_texts == [], "nothing is put in the agent's mouth"
    assert "cut off" not in decision.reason and "could not be read" not in decision.reason, (
        "an answer that never arrived is not an answer that arrived and could "
        f"not be read; the reason says what happened instead: {decision.reason!r}"
    )


def test_a_provider_that_is_down_is_not_asked_twice_for_room() -> None:
    """More room does not help an endpoint that is not there.

    The escalation exists for a model that thought too long, which is a property
    of the request. A provider that raised is a property of the endpoint, and
    asking again spends money to be told the same thing.
    """
    class _Down:
        def __init__(self) -> None:
            self.requests = 0

        async def complete(self, request: Any) -> Any:
            self.requests += 1
            raise RuntimeError("no eligible models: every key is exhausted")

    gateway = _Down()
    decision = asyncio.run(SocialCognition(
        config=make_config(Path("/tmp/ethos-test-provider-down")),
        gateway=gateway,
    ).deliberate(_question()))

    assert gateway.requests == 1, "one ask, and the reason names the endpoint"
    assert "could not be reached" in decision.reason
    assert decision.decided is False


# --------------------------------------------------------------------------
# An answer the model wrote as prose instead of as the object


# Verbatim from a deliberation on a long message that had produced this complaint:
# the object was never written, and the words below are the whole of what came
# back. They answer the message, they are the model's own, and they are the only
# answer there was.
_PROSE_ANSWER = (
    "The claim is sound: `edit: \"allow\"` already covers file creation and modification, "
    "and no project- or agent-level restriction is overriding it. I’d leave the global "
    "config unchanged for now; applying the proposed tightening would be a separate, "
    "optional change."
)


def test_an_answer_written_as_prose_is_the_reply_the_person_gets() -> None:
    """The words are used, and nothing is invented around them.

    This is the failure. A model answering a long, technically loaded message
    answers the message: prose, no object, and then a turn recorded as an
    answer that could not be read -- which reaches the person as "The agent
    chose not to respond · the model's answer could not be read", on a question
    it had in fact answered in full, twice over across the run of this prompt.
    Measured against the base model on the message that produced the report,
    five answers in fourteen came back this way and every one of them was a
    finished reply.

    So they are delivered. The words are not assembled here and not inferred
    from anything: they are the answer, they were written for that person, and
    the alternative is throwing away the only reply in the response.
    """
    out = _RecordingOut()
    bus = _CollectingBus()
    config = make_config(Path("/tmp/ethos-test-prose-answer"))
    config.paths.ensure()
    message = _question("Here is what the coding agent has said. Give it a try, or counter it.")
    social = _QueueSocial(message)
    life = _life_over(social, out, bus, config)

    decision = asyncio.run(SocialCognition(
        config=config, gateway=ScriptedGateway([_PROSE_ANSWER, _PROSE_ANSWER]),
    ).deliberate(message))

    assert decision.reply_texts == [_PROSE_ANSWER], (
        "the answer the model wrote is the answer the person gets"
    )
    assert decision.action.value == "reply_now"
    assert decision.decided is True, "an opinion was formed; it was not in the shape asked for"
    assert "prose" in decision.reason, (
        "and the row says the reply was recovered rather than read out of a "
        f"deliberation, so it is not later mistaken for one: {decision.reason!r}"
    )
    assert not decision.creates_intention, (
        "and nothing is commissioned out of an answer that carries no intention_spec"
    )

    asyncio.run(life._act_social(decision.model_dump(mode="json")))

    assert len(out.sent) == 1, "so it goes out rather than being recorded as a decline"
    assert out.sent[0]["text"] == _PROSE_ANSWER
    assert out.sent[0]["in_reply_to"] == message.id
    assert bus.of(EventKind.MESSAGE_HANDLED) == []


def test_the_object_is_asked_for_again_before_prose_is_used() -> None:
    """The reply is the floor, not the aim: the decision is what is actually wanted.

    Recovering prose is a last resort, and it is a lossy one. A reply recovered
    from prose carries no `creates_intention` and no `intention_spec`, so an "I'll
    do it" recovered this way is a promise with nothing behind it -- the one
    thing the deliberation prompt is built to prevent. So the object is asked for
    again first, and a model that produces one is believed over the prose it
    wrote a moment earlier.
    """
    gateway = ScriptedGateway([_PROSE_ANSWER, _THE_ANSWER])
    decision = asyncio.run(SocialCognition(
        config=make_config(Path("/tmp/ethos-test-object-preferred")),
        gateway=gateway,
    ).deliberate(_question()))

    assert decision.reply_texts == ["Yes — I am here."], (
        "a real deliberation beats the prose that failed to be one"
    )
    assert "prose" not in decision.reason, "and the recovered-reply note does not apply"


def test_the_second_ask_is_not_the_first_ask_again() -> None:
    """More room cannot fix an answer of the wrong shape, so the contract is restated.

    The escalation exists because a model that thought for the whole ceiling will
    do it again. An answer in prose is not short, and no ceiling would turn it
    into an object -- so the second ask ends with the contract again, where the
    person's message is not sitting between the instruction and the answer.
    """
    gateway = ScriptedGateway([_PROSE_ANSWER, _THE_ANSWER])
    asyncio.run(SocialCognition(
        config=make_config(Path("/tmp/ethos-test-ask-again-differs")),
        gateway=gateway,
    ).deliberate(_question()))

    first, second = gateway.requests[0], gateway.requests[1]
    assert second.messages[1].content.endswith('Answer with the object this time, and nothing else.'), (
        "the contract is in front of the answer, where the first ask put it above the message"
    )
    assert second.messages[1].content.startswith(first.messages[1].content), (
        "and it is the same question asked again, not a different one"
    )
    assert second.max_tokens > first.max_tokens, "with the room for a long think as well"


def test_the_contract_is_restated_after_the_message_it_is_about() -> None:
    """The prompt is not wrong; it is not the last thing read.

    The object and every field in it are set out at the top of the system turn,
    and the turn after that is the person's message. On a long one, which
    generally ends in a question addressed to the model, the instruction is a
    thousand tokens back and the message is right there -- so the model answers
    the message. Same prompt, contract repeated after the message: none in
    twenty-four runs, against five in fourteen without it.
    """
    prompts = social_deliberation_messages(
        self_name="Clio",
        message=Message(person_id="owner", channel="web", conversation_key="web:owner",
                        text="Which of these two should it do first?"),
        focus="(none)", now="2026-01-01T00:00:00+00:00", energy=0.8,
        commitments="(none)", recent="(none)",
    )
    system, user = prompts[0]["content"], prompts[1]["content"]

    assert user.index("Which of these two should it do first?") < user.index("Answer with that JSON object"), (
        "the message comes first and the contract is the last thing in front of the model"
    )
    assert "reply_texts" in user[user.index("Answer with that JSON object"):], (
        "and the field the words belong in is named where it can be used"
    )
    assert '"reply_texts"' in system, "the schema itself is untouched and still set out in full"


def test_a_cut_off_prose_answer_is_never_delivered_as_a_fragment() -> None:
    """Half a sentence is not an answer, whatever shape the answer was going to be.

    The other half of the prose recovery. The words that survive a ceiling are
    half of what the model was going to say, so recovering them puts words in its
    mouth that it did not finish choosing -- which is the breach this file is
    about, reached honestly this time rather than by filling a gap.
    """
    decision = _deliberation(
        ScriptedGateway(
            ['The claim is sound: `edit: "allow"` already covers file cre',
             'The claim is sound: `edit: "allow"` already covers file cre'],
            finish_reasons=["length", "length"],
        ),
        Path("/tmp/ethos-test-cut-off-prose"),
    )

    assert decision.reply_texts == [], "half a sentence is not a reply"
    assert decision.action.value == "act_silently"
    assert decision.decided is False, "and it is still not a decision the agent formed"
    assert "cut off" in decision.reason, f"the reason says what happened: {decision.reason!r}"


def test_an_answer_of_one_object_is_not_read_as_prose() -> None:
    """`{}` is an answer in the wrong shape, not an answer in the wrong language.

    The recovery reads the model's words when there is no object in the answer at
    all. An answer that is one object and nothing else did address the protocol
    and got it wrong, and lifting the object out of it would send a person a
    literal pair of braces as the agent's reply.
    """
    for label, response in (
        ("an empty object", "{}"),
        ("an object that names nothing about the turn", '{"note": "still deciding"}'),
    ):
        decision = _deliberation(
            ScriptedGateway([response, response]),
            Path("/tmp/ethos-test-object-not-prose"),
        )

        assert decision.reply_texts == [], f"{label} must not be delivered as a reply"
        assert decision.action.value == "act_silently"


# --------------------------------------------------------------------------
# A long, technically loaded message, and the three things that went wrong with it


# Verbatim from the message behind this file's comments: a 2 600-character
# question about an `opencode.json` permission block, pasted three times and
# unanswered three times. It is here for one reason: it is long, it is about
# code, and it contains JSON -- which is the combination every failure below
# needs, and which no short "are you still there?" reproduces.
_TECHNICAL_MESSAGE = """Here is what the coding agent has said. Please give it a try, or if you
wish to counter the claim, provide the prompt used.

No changes are needed -- the permission you're asking for is already in effect.

## What I found

There is **no project-level config** in `/Users/somebody/Desktop/ethos-project`, and
the worktree root is the project dir itself, so nothing at project scope is
restricting anything. The effective config comes from your global
`~/.config/opencode/opencode.jsonc`:

```jsonc
"permission": { "read": "allow", "edit": "allow", ... }
```

I've deliberately changed nothing. Want me to apply that tightening, or leave the
current global allow-list as-is? And separately, should I add a real
`permission: { edit: deny }` to `plan.md`?
"""


def test_a_technical_message_is_not_consequential_because_of_a_dollar_sign() -> None:
    """The tier is chosen by a substring test, and a `$` is a `$`.

    This message was deliberated at T3 because it contains `"$schema"` -- one
    JSON key. T3 has the highest floor in the catalogue, and on a deployment with
    one base model it is the tier most likely to have nothing able to serve it, so
    a character in a config key was choosing the tier for a question about config
    keys.

    Longer messages are more likely to contain a `$` and not less likely: `$PATH`,
    `${VAR}`, shell expansions, jQuery, price columns. The escalation is
    therefore near-certain on exactly the long, complex, technical prompts it was
    supposed to be sparing, and it pushes them into the most fragile tier. A `$`
    counts when it is in front of a number, which is the only shape that means
    money.
    """
    from ethos.social.deliberation import is_consequential_text

    assert not is_consequential_text(_TECHNICAL_MESSAGE)
    for technical in (
        'the config needs a "$schema" key',
        "expand $HOME and ${VAR} first",
        'use jQuery $("#row") to bind',
        "pass the request payload through the encoder",
        "redesign the layout and assign it a priority",
        "the contractor rewired the panel",
    ):
        assert not is_consequential_text(technical), technical


def test_spending_is_still_consequential() -> None:
    """The other direction, which is the one that must not regress.

    A trigger list narrowed into uselessness is a way of making the failure less
    loud rather than less frequent, and T3 is where consequential messages
    deliberate [P4].
    """
    from ethos.social.deliberation import is_consequential_text

    for real in (
        "pay the invoice",
        "wire the funds to the account",
        "please delete the old branch",
        "this is urgent, the deploy has to happen today",
        "the password is in the keychain",
        "it costs $1,200 to fix",
        "buy the domain",
    ):
        assert is_consequential_text(real), real


def test_the_life_loop_and_the_deliberation_agree_on_the_tier() -> None:
    """One rule, not two lists that can drift.

    `life` picked the tier for the deliberation out of its own list of trigger
    words and `SocialCognition` picked it again out of another, and the two
    answers did not have to agree. They were both substring tests over overlapping
    vocabularies, which is the arrangement where "the message is T3" and "the
    deliberation ran at T2" is a state the code permits.
    """
    from ethos.engine.life import Life
    from ethos.social.deliberation import is_consequential_text

    judge = Life.__dict__["_message_is_consequential"]
    for text in (
        _TECHNICAL_MESSAGE, "pay the invoice", "the config needs a $schema key",
        "are you still there?", "deploy by friday, it is urgent",
    ):
        assert judge(None, _question(text)) == is_consequential_text(text), text


def test_an_answer_keyed_wrongly_is_still_the_answer() -> None:
    """The third wrong shape, and the one a long technical message invites.

    A deliberation can come back as prose, cut off, or as one JSON value that is
    not the object asked for. The third was the only one with no recovery, and it
    ended the turn as "the model's answer could not be read" -- which is what the
    person was shown, on a question that had been answered in full.

    It is likelier the longer the message, and that is the part worth stating. A
    short prompt gives a model nothing to be tempted by; a long technical one is
    full of JSON, and the shape most likely to come back is the model holding the
    *subject* of the conversation rather than the decision. So the words are used,
    on the same terms as prose: they are the model's own, nothing is invented
    around them, and no work is commissioned out of an answer that carries no
    `intention_spec`.
    """
    words = "The permission is already in effect; nothing needs changing."
    for label, response in (
        ("a differently named field", f'{{"reply": "{words}"}}'),
        ("a list of them", f'{{"response": ["{words}"]}}'),
        ("one step down", f'{{"result": {{"answer": "{words}"}}}}'),
    ):
        decision = _deliberation(
            ScriptedGateway([response, response]),
            Path("/tmp/ethos-test-wrong-keys"),
        )

        assert decision.reply_texts == [words], f"{label}: the words were there"
        assert decision.action.value == "reply_now", f"{label}: an answer, not a silence"
        assert decision.decided is True, f"{label}: an opinion was formed"
        assert decision.creates_intention is False, (
            f"{label}: nothing is commissioned out of a recovered reply"
        )
        assert "reply_texts" in decision.reason, (
            f"{label}: and the row says where the reply came from, so it is not "
            f"later read as a decision formed in the shape asked for: {decision.reason!r}"
        )


def test_an_object_that_says_nothing_to_the_person_is_still_not_a_reply() -> None:
    """The line this recovery must not cross, and it is a real one.

    It would be easy to make the object case total -- read any string anywhere in
    it -- and then `{"note": "still deciding"}` delivers the agent's working
    instead of an answer, and `{"permission": {"edit": "allow"}}` delivers "allow"
    as a sentence, with the turn marked answered. A person given either is worse
    off than one told nothing arrived, so only names that mean *the words to send*
    qualify, and an object with none of them is still unreadable.
    """
    for label, response in (
        ("a note about the answer", '{"note": "still deciding"}'),
        ("a summary of one", '{"summary": "no changes needed"}'),
        ("the config being discussed", '{"permission": {"edit": "allow"}}'),
        ("a plan for the work", '{"steps": [{"id": "s1", "description": "run it"}]}'),
        ("nested too deep to be a naming slip",
         '{"a": {"b": {"reply": "far too deep"}}}'),
    ):
        decision = _deliberation(
            ScriptedGateway([response, response]),
            Path("/tmp/ethos-test-not-a-reply"),
        )

        assert decision.reply_texts == [], f"{label} must not be delivered as a reply"
        assert decision.action.value == "act_silently"
        assert decision.decided is False, f"{label}: and nothing was decided"


# --------------------------------------------------------------------------
# The agent claiming a limit it does not have


def test_the_deliberation_is_told_which_tools_the_agent_has() -> None:
    """The agent told its owner it could not write a file, four times.

    "I still don't have a filesystem-writing tool exposed in this chat, so I can't
    create Test.md" -- said by an agent with `fs.write`, `fs.edit` and `shell.run`
    registered, asked to create a file. The cause is one word in this prompt: it
    said only that the agent "has tools for files, shell, the browser and the
    network", and a deliberation is asked to write the words the person will read.
    With no tool named and a contract asking what to *say*, the model reasoned
    about the turn it was in rather than the agent it was speaking for, and
    reported its own prompt as its own capabilities.

    It is a bad failure in a way that is worth being precise about. It is false,
    it is confident, and it is in the only channel the person can see. And it is
    self-infirming: an owner told the agent cannot create a file stops asking it
    to, so the capability is never exercised and never found to work.

    The names are rendered from the toolhost rather than written into the prompt,
    because a list in a prompt is a second thing that can be wrong and this one
    already was.
    """
    system = social_deliberation_messages(
        self_name="Clio",
        message=Message(person_id="owner", channel="web", conversation_key="web:owner",
                        text="create a file named Test.md"),
        focus="(none)", now="2026-01-01T00:00:00+00:00", energy=0.8,
        commitments="(none)", recent="(none)",
        tools=[{"name": "fs.write"}, {"name": "fs.edit"}, {"name": "shell.run"}],
    )[0]["content"]

    for tool in ("fs.write", "fs.edit", "shell.run"):
        assert f"`{tool}`" in system, f"{tool} is named, not merely gestured at"
    assert "Never tell the person" in system, \
        "and the agent is told not to report a limit it does not have"
    assert "not by saying what tools" in system, \
        "the reply is written before the work happens, which is why this matters"
    assert "has tools for files, shell, the browser and the network" not in system, \
        "the vague sentence is what it was replaced by"


def test_the_tool_line_says_nothing_registered_rather_than_inventing_tools() -> None:
    """An agent told it has tools and finding none is worse than one told nothing."""
    system = social_deliberation_messages(
        self_name="Clio", message=_question(), focus="(none)",
        now="2026-01-01T00:00:00+00:00", energy=0.8, commitments="(none)", recent="(none)",
        tools=None,
    )[0]["content"]
    assert "(none registered)" in system


def test_the_life_loop_hands_the_deliberation_the_real_catalog() -> None:
    """The call site, not just the renderer.

    A prompt that renders the tools correctly and is never given any renders
    "(none registered)" forever, and the failure it was written for comes back --
    so the wiring is asserted here rather than left to the next reader to check.
    """

    class _Toolhost:
        def catalog(self):
            return [{"name": "fs.write", "description": "", "input_schema": {}}]

    life = _life_over(_QueueSocial(_question()), _RecordingOut(), _CollectingBus(),
                      make_config(Path("/tmp/ethos-test-tool-catalog")))
    life.deps.toolhost = _Toolhost()
    assert life._tool_names() == [{"name": "fs.write", "description": "", "input_schema": {}}]

    life.deps.toolhost = None
    assert life._tool_names() == [], "and no toolhost is no tools, not an error"
