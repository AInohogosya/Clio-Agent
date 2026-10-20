"""A promise to say, and the record that it was kept.

Two reports, one cause. The agent said it had not done things it had done, and it
did not come and say when a stage arrived that it had promised to report. Both
are the same missing fact: nothing durable recorded that commissioned work had
reached a stage the person who commissioned it was waiting for.

So the record is `stage_notices`, and these tests hold both halves of it in place:

* the *evidence* half -- the deliberation can see that work finished, so a reply
  answering "did you do it?" is written from a record rather than from the absence
  of one;
* the *debt* half -- the loop has a pass that owes somebody the word, spends it,
  and keeps paying it until it is paid.

They are kept in one file because they are one mechanism, and a fix to either half
that broke the other would still pass a test file with one half in it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from ethos.engine.life import Life, LifeDeps
from ethos.engine.states import State
from ethos.prompts.deliberation import (
    execution_state_block,
    social_deliberation_messages,
    stage_notice_messages,
)
from ethos.schemas.gsl import STAGE_NOTIFIABLE, IntentionRecord, StageNotice
from ethos.schemas.messages import Message
from ethos.social.deliberation import SocialCognition
from ethos.social.etiquette import OutboundPipeline
from tests.conftest import ScriptedGateway, make_config


def _config(name: str):
    config = make_config(Path(f"/tmp/ethos-test-{name}"))
    config.paths.ensure()
    # Quiet hours that are definitely not now, so a failure is about a gate and
    # not about the clock crossing 22:00.
    config.permissions.interruption.quiet_hours = {"start": "03:00", "end": "03:01"}
    return config


def _notice(**over: Any) -> StageNotice:
    fields: dict[str, Any] = {
        "intention_id": uuid4(), "stage": "done", "title": "fix the parser",
        "detail": "verified at rung 2 on 3 checks", "person_id": "mira",
        "channel": "telegram", "conversation_key": "telegram:mira",
    }
    fields.update(over)
    return StageNotice(**fields)


# ---------------------------------------------------------------- the record


def test_the_stages_that_are_worth_a_word_are_the_ones_the_loop_reaches() -> None:
    """The policy list is the statuses the solver loop actually writes.

    `failed` and `cancelled` are in the schema and nothing assigns them — a goal
    that cannot be verified is parked with a reason — so a list naming them would
    read as coverage of a path that does not exist. That is the same mistake the
    blocked-status query made once already, and it is invisible from the prompt
    that consumes it.
    """
    assert STAGE_NOTIFIABLE == {"done", "parked", "awaiting_review"}
    assert "failed" not in STAGE_NOTIFIABLE
    assert "cancelled" not in STAGE_NOTIFIABLE


def test_a_notice_is_a_debt_until_it_is_delivered() -> None:
    """`delivered_at` is the whole difference between a record and an obligation.

    Before it is set, the loop keeps trying to pay it. After it is set, the same
    row is the durable evidence a reply draws on months later — which is why the
    two readings share a table rather than being two tables that can disagree.
    """
    owed = _notice()
    assert owed.owed is True
    settled = _notice(delivered_at=datetime.now(UTC))
    assert settled.owed is False


# ------------------------------------------------------- issue 1: the evidence


def test_finished_work_appears_in_the_execution_state_block() -> None:
    """The block a reply is written from has to carry what already happened.

    It carried the journal (a fifteen-minute window on what the agent is doing)
    and the blocked list (what it cannot do) and nothing at all about what it had
    finished, because a closed intention is in neither — every query filters on
    the open statuses. So a person asking about work completed an hour ago was
    answered from a block whose only contents were a quiet journal and an empty
    work list.
    """
    block = execution_state_block(
        "Clio", journaled=[], blocked=[], window_min=15.0,
        stages=["- fix the parser: finished (done); already told them"],
    )

    assert "already reached a stage" in block
    assert "fix the parser" in block
    assert "finished" in block


def test_an_empty_stage_list_is_not_read_as_nothing_happened() -> None:
    """The honest line, which is the whole fix for the false "I have not done it".

    The prompt says to treat this block as a record of what happened. A stage line
    that simply stopped when there was nothing recent would make "no journaled
    actions, no blocked work, no stages" read as *nothing has happened yet* — which
    is true of work in flight and false about everything finished before the window.
    So the empty case has to say what it does and does not cover.
    """
    block = execution_state_block("Clio", [], [], 15.0, stages=[])

    assert "no journaled" not in block.lower()
    assert "older work" in block, (
        "the empty stage line has to disclaim the window, or the block reads as a "
        "clean record of a life with nothing in it"
    )


def test_the_three_kinds_of_fact_are_rendered_under_different_headings() -> None:
    """Three questions, three lines.

    Merged into one heading, "nothing journaled and nothing blocked" reads as
    *nothing has happened*, which is the inference the block exists to prevent.
    The separation is the mechanism, so it is asserted rather than described.
    """
    block = execution_state_block(
        "Clio", journaled=["fs.write -> ok"], blocked=["[parked] deploy: stopped"],
        window_min=15.0, stages=["- fix the parser: finished (done)"],
    )

    assert "Journaled actions" in block
    assert "blocked, stalled or parked" in block
    assert "already reached a stage" in block
    assert "fix the parser" in block and "fs.write" in block and "deploy" in block


def test_a_reply_is_told_the_stage_line_outranks_the_quiet_journal() -> None:
    """The instruction, because the data alone does not fix a wrong inference.

    Both halves of the prompt have to agree. The block carries the finished work
    *and* the prompt has to say that a quiet journal next to it is not evidence
    the work was not done — otherwise the model resolves the ambiguity the way it
    did before, towards the line that looks like the current state.
    """
    messages = social_deliberation_messages(
        self_name="Clio",
        message=Message(person_id="mira", channel="telegram",
                        conversation_key="telegram:mira", text="did you finish the parser fix?"),
        focus="(none)", now=datetime.now(UTC).isoformat(), energy=0.9,
        commitments="(none)", recent="(none)",
        execution_state=execution_state_block(
            "Clio", [], [], 15.0, stages=["- fix the parser: finished (done)"],
        ),
    )
    system = messages[0]["content"]

    assert "Already reached a stage" in system
    assert "outranks anything inferred" in system
    # And the sentence that produced the complaint has to stop licensing it.
    assert "not a licence to report anything else as undone" in system


def test_unreadable_stages_do_not_disarm_the_underclaiming_guardrail() -> None:
    """A missing table is not a clean record, and must not be read as one.

    `_finished_work` degrades to empty on a failed query rather than marking the
    whole state unreadable. That is deliberate and asymmetric: the unreadable flag
    is what disarms `_withhold_unearned_progress`, so a stage table being briefly
    unreachable must not also switch off the overclaiming guard on the journal.
    """
    class _StagesUnreachable:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            self.queries.append(query)
            if "stage_notices" in query:
                raise RuntimeError("relation does not exist")
            return []

        async def fetchrow(self, query: str, *args: Any) -> Any:
            return None

        async def fetchval(self, query: str, *args: Any) -> Any:
            return 0

    config = _config("stages-unreachable")
    db = _StagesUnreachable()
    social = SocialCognition(config, db=db)

    state = asyncio.run(social._execution_state(None))

    assert state.readable is True, (
        "the journal was read, so the guardrail stays armed; one unreachable table "
        "must not switch off a different guard"
    )
    assert state.stages == []
    assert any("stage_notices" in q for q in db.queries)


# ------------------------------------------------------- issue 2: the debt


class _RecordingOut:
    def __init__(self, status: str = "sent") -> None:
        self.sent: list[dict[str, Any]] = []
        self.status = status

    async def send(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": self.status, "detail": {"note": "stub"}}


class _Store:
    """Just the stage-notice half of the memory store."""

    def __init__(self, notices: list[StageNotice] | None = None) -> None:
        self.notices = list(notices or [])
        self.settled: list[tuple[str, bool]] = []
        self.waits: list[Any] = []

    async def due_stage_notices(self, limit: int = 1) -> list[StageNotice]:
        return self.notices[:limit]

    async def mark_stage_notice(
        self, notice_id: Any, *, delivered: bool, delivery: Any = None,
        next_attempt_at: Any = None,
    ) -> None:
        self.settled.append((str(notice_id), delivered))
        if not delivered:
            self.waits.append(next_attempt_at)


class _Composing:
    """Stands where `SocialCognition` sits, writing words on request."""

    def __init__(self, text: str | None = "It's done — the parser fix is in.") -> None:
        self.text = text
        self.seen: list[Any] = []

    async def compose_stage_notice(self, notice: Any, preview: bool = False) -> str | None:
        self.seen.append(notice)
        return self.text

    async def pending_inbound(self, limit: int = 5) -> list[Any]:
        return []


class _Quiet:
    def __getattr__(self, name: str) -> Any:
        async def _noop(*_: Any, **__: Any) -> Any:
            return None

        return _noop


def _life(*, social: Any, out: Any, memory: Any, config: Any) -> Life:
    return Life(LifeDeps(
        config=config, control=_Quiet(), bus=None, memory=memory, self_model=_Quiet(),
        attention=None, motivation=None, rhythm=_Quiet(), gateway=None, gsl=None,
        social=social, comms_out=out, toolhost=None, audit=None,
        continuity=_Quiet(), consolidation=None, embedder=None, thread_id="self",
    ))


def test_the_loop_spends_a_pass_on_a_word_somebody_is_owed() -> None:
    """The pass that did not exist, and is the whole of the second complaint.

    Before this branch the loop had two ways to speak: answer a message that
    arrived, or be a solver step that happened to call `comm.send`. So a person
    who said "tell me when it's done" heard about it only if some step's model
    decided to mention it unprompted — which is what "sometimes" looks like from
    outside, and it is a coin the person never sees.
    """
    notice = _notice()
    config = _config("notify-branch")
    life = _life(social=_Composing(), out=_RecordingOut(),
                 memory=_Store([notice]), config=config)

    decision = asyncio.run(life.decide(SimpleNamespace()))

    assert decision.kind == "notify", (
        "a stage somebody asked to be told about has to be able to take a pass"
    )
    assert decision.stage is not None
    assert decision.stage["person_id"] == "mira"
    assert decision.stage["stage"] == "done"
    assert "fix the parser" in decision.rationale


def test_the_notice_pass_says_it_in_words_the_model_chose() -> None:
    """The text that goes out is the composed one, with nothing added around it.

    Everything this program sends to a person is a word the model chose. A canned
    line would arrive through the ordinary reply path and be indistinguishable in
    the transcript from a reply the agent had actually thought about, which is the
    reason the composed announcement was removed and the reason the debt it left
    behind had to be paid by composing again rather than by restoring the string.
    """
    notice = _notice()
    composing = _Composing(text="The parser fix is in — it handles the empty case now.")
    out = _RecordingOut()
    life = _life(social=composing, out=out, memory=_Store([notice]), config=_config("notify-send"))

    delivered = asyncio.run(life._notify_stage({
        "notice_id": str(notice.id), "intention_id": str(notice.intention_id),
        "stage": "done", "title": "fix the parser", "detail": "",
        "person_id": "mira", "channel": "telegram",
        "conversation_key": "telegram:mira", "attempts": 0,
    }))

    assert delivered is True
    assert [message["text"] for message in out.sent] == [
        "The parser fix is in — it handles the empty case now."
    ]
    assert out.sent[0]["person_id"] == "mira"
    assert out.sent[0]["channel"] == "telegram"
    assert out.sent[0]["conversation_key"] == "telegram:mira"


def test_the_promised_update_names_its_intention_so_it_is_not_an_interruption() -> None:
    """The send carries the intention, which is what the downstream gates read.

    The digest window and the unanswered check both ask whether the agent is
    *starting* a conversation. A note that names the work it is about is not one —
    it is the answer to something the agent already said — and `midtask_sends_immediate`
    keys off exactly this field. Omit it and the one message that was promised
    becomes the one message that is gated like an interruption.
    """
    notice = _notice()
    out = _RecordingOut()
    life = _life(social=_Composing(), out=out, memory=_Store([notice]), config=_config("notify-mid"))

    asyncio.run(life._notify_stage({
        "notice_id": str(notice.id), "intention_id": str(notice.intention_id),
        "stage": "done", "title": "fix the parser", "detail": "",
        "person_id": "mira", "channel": "telegram",
        "conversation_key": "telegram:mira", "attempts": 0,
    }))

    assert out.sent[0]["intention_id"] == str(notice.intention_id)
    assert out.sent[0]["in_reply_to"] is None, (
        "there is no message being answered; this is the agent opening up"
    )


def test_a_delivered_notice_clears_the_debt() -> None:
    """`sent` and `queued` both mean the words are on their way, so both settle.

    A queued entry is owned by the digest from that moment. Holding the notice
    owed because the message had not gone out yet would give the loop a second,
    competing claim on the same words and produce the duplicate this queue exists
    to prevent.
    """
    for status in ("sent", "queued"):
        notice = _notice()
        store = _Store([notice])
        life = _life(social=_Composing(), out=_RecordingOut(status=status),
                     memory=store, config=_config(f"notify-settled-{status}"))

        delivered = asyncio.run(life._notify_stage({
            "notice_id": str(notice.id), "intention_id": str(notice.intention_id),
            "stage": "done", "title": "fix the parser", "detail": "",
            "person_id": "mira", "channel": "telegram",
            "conversation_key": "telegram:mira", "attempts": 0,
        }))

        assert delivered is True, status
        assert store.settled == [(str(notice.id), True)], status
        assert store.waits == [], "a settled notice is not also waiting"


def test_a_send_that_was_held_leaves_the_debt_owed_and_schedules_a_retry() -> None:
    """A failed send is a retry, not a discharge.

    `_hold` records the text and drops it rather than deferring it, so a held
    promised update is the case where the person never hears at all. Leaving the
    notice owed with a wait set is what lets the next pass try again — and the
    wait is set rather than retried immediately so a permanently undeliverable
    channel cannot turn into a pass and a model call every few seconds.
    """
    notice = _notice()
    store = _Store([notice])
    life = _life(social=_Composing(), out=_RecordingOut(status="held"),
                 memory=store, config=_config("notify-held"))

    delivered = asyncio.run(life._notify_stage({
        "notice_id": str(notice.id), "intention_id": str(notice.intention_id),
        "stage": "done", "title": "fix the parser", "detail": "",
        "person_id": "mira", "channel": "telegram",
        "conversation_key": "telegram:mira", "attempts": 0,
    }))

    assert delivered is False
    assert store.settled == [(str(notice.id), False)], "still owed"
    assert len(store.waits) == 1 and store.waits[0] is not None, (
        "and not retried at the tick rate"
    )


def test_a_debt_that_will_not_pay_is_abandoned_rather_than_kept_forever() -> None:
    """Past the cap the loop stops, and says so in the row.

    The alternative is an unbounded retry loop, which is an outage wearing a
    notification's clothes: a pass and a model call every few seconds for as long
    as the fault lasts. Stopping is chosen over persistence deliberately, and the
    exhaustion is written into the notice so the choice is visible to an operator
    rather than being a row quietly incrementing forever.
    """
    config = _config("notify-abandoned")
    cap = int(config.social.stage_notice_max_attempts)
    notice = _notice()
    store = _Store([notice])
    life = _life(social=_Composing(), out=_RecordingOut(status="suppressed"),
                 memory=store, config=config)

    delivered = asyncio.run(life._notify_stage({
        "notice_id": str(notice.id), "intention_id": str(notice.intention_id),
        "stage": "done", "title": "fix the parser", "detail": "",
        "person_id": "mira", "channel": "telegram",
        "conversation_key": "telegram:mira", "attempts": cap - 1,
    }))

    assert delivered is False, "the person was not told, whatever the row says"
    assert store.settled == [(str(notice.id), True)], "but the loop stops asking"
    assert store.waits == [], "and does not keep booking another attempt"


def test_a_notice_with_nothing_worth_saying_is_not_sent_and_is_not_retried_forever() -> None:
    """`None` from the composer is an answer, and it settles the debt.

    The model declining to write is not a transport failure: it has been asked,
    it has declined, and asking it again on the next pass produces the same
    answer at the same cost. The loop stops, and the "nothing to say" is recorded.
    """
    notice = _notice()
    store = _Store([notice])
    life = _life(social=_Composing(text=None), out=_RecordingOut(),
                 memory=store, config=_config("notify-nothing"))

    delivered = asyncio.run(life._notify_stage({
        "notice_id": str(notice.id), "intention_id": str(notice.intention_id),
        "stage": "done", "title": "fix the parser", "detail": "",
        "person_id": "mira", "channel": "telegram",
        "conversation_key": "telegram:mira", "attempts": 0,
    }))

    assert delivered is False
    assert len(store.settled) == 1, "one decision, recorded"


def test_the_notify_pass_does_not_disarm_the_outbound_guards() -> None:
    """A pass that opens a conversation still has to enter `act`.

    `decide` returning a kind that `act` does not match is a cycle spent
    producing nothing, and the notice would stay owed while the loop believed it
    had been paid — which is the same silent drop this is meant to remove.
    """
    notice = _notice()
    out = _RecordingOut()
    life = _life(social=_Composing(), out=out, memory=_Store([notice]),
                 config=_config("notify-act"))

    decision = asyncio.run(life.decide(SimpleNamespace()))
    outcome = asyncio.run(life.act(decision))

    assert decision.kind == "notify"
    assert outcome.ok is True
    assert len(out.sent) == 1, "the decision reached an action"


def test_the_notify_pass_enters_the_state_that_exists_for_it() -> None:
    """`NOTIFYING` was a tick with nothing behind it until now.

    It is in the state machine, in the awake set and in the tick table — a rate of
    2 seconds, faster than a wait — and no cycle ever entered it, because nothing
    in the loop ever spoke first. Now something does.
    """
    notice = _notice()
    life = _life(social=_Composing(), out=_RecordingOut(),
                 memory=_Store([notice]), config=_config("notify-state"))

    asyncio.run(life.decide(SimpleNamespace()))

    assert life.states.state is State.NOTIFYING


class _Conversation:
    """One conversation, whose last word the test moves between turns.

    The gate being exercised is a comparison against whatever the agent last said,
    so a fake that answers the same thing to every query cannot show the case the
    gate exists for. This one can: `agent_spoke_last` is set by the test between
    the enqueue and the flush, which is exactly the race `flush_due_digests` asks
    about — a note queued while the person had the floor, flushed after the agent
    had answered something else.
    """

    def __init__(self, *, agent_spoke_last: bool) -> None:
        self.agent_spoke_last = agent_spoke_last
        self.rows: list[tuple[str, Any]] = []

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if not self.agent_spoke_last:
            return None
        return {"ts": datetime.now(UTC), "direction": "outbound",
                "person_id": "mira", "text": "on it"}

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if not self.agent_spoke_last:
            return []
        return [{"text": "on it"}]

    async def fetchval(self, query: str, *args: Any) -> int:
        return 0

    async def execute(self, query: str, *args: Any) -> None:
        # The messages row is written with the intention last, which is where the
        # delivered message is attributed to the work it is about.
        if "INSERT INTO messages" in query:
            self.rows.append((str(query), args[-2]))


class _Sender:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"status": "sent"}


def test_a_promised_update_queued_for_the_window_is_not_blocked_by_the_unanswered_gate() -> None:
    """The digest used to lose the one fact that made a queued update deliverable.

    A mid-task update naming its intention skips the unanswered check at enqueue,
    but quiet hours or a spent budget can still send it to the digest — and
    `_enqueue_digest` kept neither the intention nor the fact that there had been
    one. So by the time the flush asked whether the agent was speaking first on
    the conversation, a solver step's "it's done" was an anonymous queued string,
    and the longest-running entry in the system was exactly the one that could not
    go out: blocked for the `awaiting_reply_h` after the agent's own "I'll do it",
    and blocked again on every subsequent flush until the person happened to reply.
    """
    config = _config("digest-promised")
    # Quiet hours now, so the update is queued rather than delivered — which is the
    # only way a mid-task send reaches the digest at all.
    config.permissions.interruption.quiet_hours = {"start": "00:00", "end": "23:59"}
    sender = _Sender()
    # The agent spoke last and has not been answered: the state a commission
    # creates when its "I'll do it" goes out as a solicited reply.
    db = _Conversation(agent_spoke_last=True)
    pipe = OutboundPipeline(config, db=db, sender=sender)
    pipe.window_s = 0.0

    queued = asyncio.run(pipe.send(
        person_id="mira", channel="telegram", text="The parser fix is in.",
        urgency="normal", conversation_key="telegram:mira", in_reply_to=None,
        intention_id="7f3a-work",
    ))
    assert queued["status"] == "queued", "quiet hours queue it rather than lose it"
    assert pipe.digests["mira"]["intention_ids"] == ["7f3a-work"], (
        "the fact that names the work has to survive into the queue"
    )

    config.permissions.interruption.quiet_hours = {"start": "03:00", "end": "03:01"}
    flushed = asyncio.run(pipe.flush_due_digests())

    assert flushed == 1, (
        "a batch naming the work it is about is the answer to a promise, and the "
        "unanswered gate must not hold it"
    )
    assert [m["text"] for m in sender.sent] == ["The parser fix is in."]
    assert [a for _, a in db.rows if a][-1] == "7f3a-work", (
        "and the delivered row is attributed to the work it is about, rather than "
        "filed as a message with no subject"
    )


def test_an_ordinary_unsolicited_digest_is_still_gated() -> None:
    """The exemption above is narrow on purpose, and this is the shape it must not reach.

    Anything the agent volunteers with nobody waiting on it is an interruption, and
    the unanswered gate is the correct answer to one. If the gate had been lifted
    rather than exempted, an agent that said "on it" and then had nothing to report
    could interrupt that person indefinitely.

    The race is the one the flush documents: queued while the person held the
    floor, and due to go out after the agent had answered something else — so its
    own word was last with nothing back.
    """
    config = _config("digest-plain")
    sender = _Sender()
    db = _Conversation(agent_spoke_last=False)
    pipe = OutboundPipeline(config, db=db, sender=sender)
    pipe.window_s = 0.0

    queued = asyncio.run(pipe.send(
        person_id="mira", channel="telegram", text="Something else entirely.",
        urgency="normal", conversation_key="telegram:mira", in_reply_to=None,
    ))
    assert queued["status"] == "queued", "no promise is named, so nothing is held yet"

    db.agent_spoke_last = True
    flushed = asyncio.run(pipe.flush_due_digests())

    assert flushed == 0, "no promise is named, so the gate stands"
    assert sender.sent == []
    assert "mira" in pipe.digests, "and the entry stays queued rather than being lost"


# ---------------------------------------------------------- the prompt itself


def test_the_notice_prompt_writes_a_message_and_commissions_nothing() -> None:
    """A reply that opens a conversation cannot commission work.

    `deliberate` asks for `action`, `creates_intention` and `intention_spec`,
    because a reply that says "I'll do it" with nothing behind it is a promise
    with no future. None of that applies here: this is the agent reporting on work
    it has already finished, and a response carrying an intention from this prompt
    would start something the person never asked for.
    """
    messages = stage_notice_messages(
        "Clio", stage="done", title="fix the parser",
        detail="verified at rung 2 on 3 checks", who="mira",
    )
    system = messages[0]["content"]

    assert '"text"' in system
    assert "creates_intention" not in system
    assert "intention_spec" not in system, (
        "a reply that opens a conversation cannot commission work; a response "
        "carrying an intention from this prompt starts something nobody asked for"
    )
    assert "Nobody has asked you for this" in system


def test_the_notice_prompt_distinguishes_finished_from_stopped_short() -> None:
    """A person told a task is parked is owed the reason.

    "I could not do it" with nothing after it is worse than silence, because it
    is a dead end the reader has to guess at. Each of the three stages says what
    happened rather than reporting a status word, and the prompt forbids the
    machinery vocabulary that would turn the message into a status line.
    """
    done = stage_notice_messages("Clio", stage="done", title="t", detail="", who="mira")[0]
    parked = stage_notice_messages("Clio", stage="parked", title="t", detail="", who="mira")[0]
    review = stage_notice_messages(
        "Clio", stage="awaiting_review", title="t", detail="", who="mira")[0]

    assert "finished and passed its checks" in done["content"]
    assert "stopped short of finished" in parked["content"]
    assert "outstanding is a person" in review["content"]
    for rendered in (done, parked, review):
        assert "Do not narrate your own machinery" in rendered["content"]


def test_a_braced_work_title_does_not_break_the_notice_prompt() -> None:
    """A name may contain braces, and `str.format` on one would raise.

    The value is interpolated into the prompt rather than formatted through it, so
    a title like `fix the {parser}` renders as itself. This is the same reason
    `ethos/prompts/identity.py` uses f-strings throughout.
    """
    messages = stage_notice_messages(
        "Clio", stage="done", title="rename {legacy} to {new}", detail="", who="mira",
    )

    assert "rename {legacy} to {new}" in messages[0]["content"]


def test_composing_a_notice_reads_the_model_answer_and_nothing_else() -> None:
    """The composer is where the agent's words come from, so it is worth a test.

    One call, a small ceiling, no escalation and no tier ladder: a stage notice is
    two or three sentences about one thing that happened, and there is at most one
    owed word per stage. Anything more would be spending the pass that was going
    to say it.
    """
    config = _config("compose")
    gateway = ScriptedGateway(['{"text": "The parser fix is in."}'])
    social = SocialCognition(config, gateway=gateway, self_model=None)

    text = asyncio.run(social.compose_stage_notice(_notice()))

    assert text == "The parser fix is in."
    request = gateway.requests[0]
    assert request.max_tokens <= 1_000, "a ceiling for two sentences, not a deliberation"
    assert request.purpose == "social.stage_notice"


def test_a_model_that_writes_prose_instead_of_the_object_still_sends_its_words() -> None:
    """Prose is the model's own words; there is nothing to recover it from.

    A deliberation can discard prose and ask again because the object it wants is
    worth more than the words. Here the words *are* the whole answer, so they are
    used — the same policy as the deliberation's prose recovery, for the same
    reason, and asserted so the two do not drift apart.
    """
    config = _config("compose-prose")
    gateway = ScriptedGateway(["The parser fix is in — it handles the empty case now."])
    social = SocialCognition(config, gateway=gateway, self_model=None)

    text = asyncio.run(social.compose_stage_notice(_notice()))

    assert text is not None
    assert "parser fix is in" in text


def test_an_empty_object_means_there_is_nothing_to_send() -> None:
    """`{"text": ""}` is a deliberate no, and it is honoured rather than retried."""
    config = _config("compose-empty")
    gateway = ScriptedGateway(['{"text": ""}'])
    social = SocialCognition(config, gateway=gateway, self_model=None)

    assert asyncio.run(social.compose_stage_notice(_notice())) is None


def test_an_unreachable_model_is_not_a_message() -> None:
    """The call failing is a reason to leave the debt owed, not to invent words."""
    config = _config("compose-down")

    class _Down:
        async def complete(self, request: Any) -> Any:
            raise RuntimeError("no model available")

    social = SocialCognition(config, gateway=_Down(), self_model=None)

    assert asyncio.run(social.compose_stage_notice(_notice())) is None


# ------------------------------------------------------------- the store side


def test_a_notice_carries_the_address_it_was_written_with() -> None:
    """Resolution happens once, at write time, and travels with the row.

    Looked up later it would be a second thing to be wrong about, and it would go
    wrong silently: the conversation the person used last week is not the one they
    commissioned this on, and a notice delivered to the wrong thread reads as the
    agent having said something it never said.
    """
    recorded: dict[str, Any] = {}

    class _Db:
        async def fetchrow(self, query: str, *args: Any) -> Any:
            if "FROM messages" in query:
                return {"channel": "telegram", "conversation_key": "telegram:mira"}
            return None

        async def execute(self, query: str, *args: Any) -> None:
            recorded.update(
                intention_id=str(args[1]), stage=args[2], title=args[3], detail=args[4],
                person_id=args[5], channel=args[6], conversation_key=args[7],
            )

        async def fetch(self, query: str, *args: Any) -> list[Any]:
            return []

    from ethos.memory.store import MemoryStore

    store = MemoryStore.__new__(MemoryStore)
    store.db = _Db()
    store.bus = None
    notice = _notice()
    intention = IntentionRecord(
        id=notice.intention_id, title="fix the parser", commissioned_by="mira",
    )

    written = asyncio.run(store.record_stage_notice(intention, "done", "verified"))

    assert written is not None
    assert written.channel == "telegram"
    assert written.conversation_key == "telegram:mira"
    assert recorded["person_id"] == "mira"
    assert recorded["stage"] == "done"


def test_work_nobody_asked_for_is_not_a_debt() -> None:
    """There is nobody to owe a word to, so there is no row.

    A notice with no address would be a debt the loop keeps paying into a void,
    spending a pass and a model call on every idle cycle to tell nobody.
    """
    from ethos.memory.store import MemoryStore

    store = MemoryStore.__new__(MemoryStore)
    store.db = None
    store.bus = None
    intention = IntentionRecord(id=uuid4(), title="a venture of my own")

    assert asyncio.run(store.record_stage_notice(intention, "done")) is None
