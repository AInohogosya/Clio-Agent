from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from ethos.engine.life import Life, LifeDeps
from ethos.engine.reverie import Reverie
from ethos.engine.rhythm import RhythmController
from ethos.schemas.gsl import Decision, Focus, Outcome
from ethos.schemas.reverie import ReverieKind
from tests.conftest import ListAudit, ScriptedGateway, make_config

"""
An agent that can only answer is not an agent, it is a terminal.

The life loop always ran when nobody was talking to it, but everything it could
spend an idle mind on was something it was already on the hook for: a focus
re-derived from stored intentions, or a glance across a few random memories. With
no open commitments and no questions, `decide` returned `none` and the agent sat
there — not thinking, only not answering. These tests keep the two apart: that an
idle agent starts something of its own, that it is allowed to decide it is not
interested in anything, and that what it starts is durable rather than a line in
a log.
"""


class _Store:
    """Just enough memory store for the commit and the repeat check."""

    def __init__(self, titles: list[str] | None = None) -> None:
        self.titles = list(titles or [])
        self.intentions: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self.episodes: list[dict[str, Any]] = []
        self.db = SimpleNamespace(fetch=self._fetch)

    async def _fetch(self, *_: Any) -> list[Any]:
        return [{"title": t} for t in self.titles]

    async def random_memories(self, n: int) -> list[Any]:
        return [SimpleNamespace(summary=f"memory {i}") for i in range(n)]

    async def open_questions(self, limit: int = 20) -> list[dict[str, Any]]:
        return [{"text": "why does the scheduler skip RESTING"}][:limit]

    async def create_intention(self, title: str, desired_end_state: str = "", **kwargs: Any) -> UUID:
        self.intentions.append({"title": title, "desired_end_state": desired_end_state, **kwargs})
        self.titles.append(title)
        return uuid4()

    async def open_question(self, text: str, priority: float = 0.5, **_: Any) -> UUID:
        self.questions.append({"text": text, "priority": priority})
        return uuid4()

    async def record_episode(self, **kwargs: Any) -> str:
        self.episodes.append(kwargs)
        return f"episode-{len(self.episodes)}"


class _Nothing:
    def __getattr__(self, name: str) -> Any:
        async def _noop(*_: Any, **__: Any) -> Any:
            return None

        return _noop


def _config():
    config = make_config(Path("/tmp/ethos-test-reverie"))
    config.paths.ensure()
    return config


def _reverie(gateway: Any, store: Any = None, config: Any = None) -> Reverie:
    return Reverie(
        config or _config(), gateway=gateway, memory=store or _Store(), audit=ListAudit(),
    )


CHOOSES = (
    '{"kind": "venture", "subject": "learn what the scheduler does with RESTING", '
    '"why": "it has been skipping my rest windows", "first_step": "read rhythm.py", '
    '"interest": 0.7}'
)


# ----------------------------------------------------------------- the choice


async def test_an_idle_mind_takes_up_something_of_its_own(config):
    """The whole point: nothing was owed, and it started something anyway."""
    gateway = ScriptedGateway([CHOOSES])
    store = _Store()

    result = await _reverie(gateway, store, config).muse()

    assert result.refused == ""
    assert result.committed_kind == "venture"
    assert result.committed_id is not None
    title = store.intentions[0]["title"]
    assert "RESTING" in title
    assert store.intentions[0]["kind"] == "venture"
    assert store.intentions[0]["origin"] == "reverie", (
        "a subject the agent chose itself has to say so, or it cannot be told "
        "apart later from work somebody commissioned"
    )


async def test_a_question_is_filed_as_a_question(config):
    """A subject filed under the wrong kind raises the wrong drive.

    `creation_gain` counts ventures and `curiosity_gain` counts questions, so
    filing every idle thought as a venture would make curiosity unreachable and
    the agent would never wonder about anything again.
    """
    store = _Store()
    gateway = ScriptedGateway([
        '{"kind": "question", "subject": "why does RESTING skip a cycle", '
        '"why": "it looks like an off-by-one", "interest": 0.6}'
    ])

    result = await _reverie(gateway, store, config).muse()

    assert result.committed_kind == "question"
    assert store.questions and not store.intentions
    assert "RESTING" in store.questions[0]["text"]


async def test_being_drawn_to_nothing_is_an_answer(config):
    """Not being interested is a state, not a failure to parse.

    An idle mind that can only ever manufacture a subject is a machine keeping
    itself busy, and it is the reason this path is paced and floored rather than
    run on every idle tick.
    """
    gateway = ScriptedGateway(['{"kind": "none", "subject": "", "why": "tired"}'])
    store = _Store()

    result = await _reverie(gateway, store, config).muse()

    assert result.committed_id is None
    assert not store.intentions and not store.questions
    assert result.refused, "choosing nothing is recorded as a choice, not as silence"
    assert result.thought, "and it is still worth remembering that it was considered"


async def test_unreadable_answers_are_treated_as_choosing_nothing(config):
    """A model that answers in prose has not found a subject."""
    for text in ("I think you should look at the scheduler.", "", "{not json"):
        store = _Store()
        result = await _reverie(ScriptedGateway([text]), store, config).muse()
        assert result.committed_id is None
        assert not store.intentions


async def test_a_subject_it_does_not_want_is_passed_on(config):
    """Asking for the number is cheaper than unpicking a manufactured interest."""
    config = _config()
    config.rhythm.reverie.min_interest = 0.5
    store = _Store()
    gateway = ScriptedGateway([
        '{"kind": "venture", "subject": "reorganise every intention ever made", '
        '"interest": 0.2}'
    ])

    result = await _reverie(gateway, store, config).muse()

    assert result.committed_id is None
    assert "interest" in result.refused


async def test_the_same_thought_twice_is_refused(config):
    """`explore_question` had been invented ninety-seven times and abandoned.

    The guard exists because an agent that picks up a fresh-sounding interest
    every pass and lets each one rot looks busy and remembers nothing. It is
    checked only against subjects the agent chose for itself: a commissioned
    venture may legitimately share its subject with something the agent is
    already doing.
    """
    store = _Store(["learn what the scheduler does with RESTING"])
    gateway = ScriptedGateway([CHOOSES])

    result = await _reverie(gateway, store, config).muse()

    assert result.committed_id is None
    assert "already" in result.thought or "same thought" in result.refused
    assert not store.intentions, "the repeat is not filed a second time"


async def test_a_genuinely_different_subject_is_allowed(config):
    store = _Store(["tidy the trash and check the disk"])
    gateway = ScriptedGateway([CHOOSES])

    result = await _reverie(gateway, store, config).muse()

    assert result.committed_id is not None


# ------------------------------------------------------------------- the gate


def test_reverie_is_paced_and_not_every_tick(config):
    """Minutes, not seconds.

    A gate that fired every idle tick would be performing busyness. The first
    call after a process starts answers False on purpose: a freshly woken agent
    gets its idle thoughts on the ordinary clock, not as a startup flourish.
    """
    config = _config()
    config.rhythm.reverie.min_gap_min = 1.0
    config.rhythm.reverie.max_gap_min = 2.0
    now = datetime.now(UTC)
    clock = [now]
    rhythm = RhythmController(config, wall_clock=lambda: clock[0])

    assert rhythm.reverie_due() is False, "not in the first moment after waking"
    for _ in range(20):
        clock[0] += timedelta(seconds=30)
        if rhythm.reverie_due():
            break
    else:
        pytest.fail("reverie never came due: the idle mind is gated shut again")

    rhythm.last_reverie = clock[0] - timedelta(minutes=90)
    assert rhythm.reverie_due() is True
    assert rhythm.reverie_due() is False, "and then it waits again"


def test_reverie_can_be_switched_off(config):
    now = datetime.now(UTC)
    clock = [now]
    rhythm = RhythmController(config, wall_clock=lambda: clock[0])
    config.rhythm.reverie.enabled = False

    rhythm.last_reverie = now - timedelta(hours=3)
    assert rhythm.reverie_due() is False


# ------------------------------------------------- the focus it left behind


def _life(config: Any, rhythm: Any, memory: Any = None, script: list[str] | None = None) -> Life:
    store = memory or _Store()
    return Life(LifeDeps(
        config=config, control=_Nothing(), bus=None, memory=store,
        self_model=_Nothing(), attention=None, motivation=None, rhythm=rhythm,
        gateway=None, gsl=None, social=None, comms_out=None, toolhost=None,
        audit=ListAudit(), continuity=_Nothing(), consolidation=None,
        embedder=None, context=None, thread_id="self",
        reverie=Reverie(
            config, gateway=ScriptedGateway(script or [CHOOSES]), memory=store,
        ),
    ))


async def test_a_venture_it_chose_becomes_what_it_does_next(config):
    """An interest the agent never returns to is a note about having had one."""
    rhythm = RhythmController(config)
    store = _Store()
    life = _life(config, rhythm, store)
    life.focus = None

    await life.act(Decision(kind="reverie", tier="T1", rationale="idle"))

    assert life.focus is not None, "the subject it picked up became the focus"
    assert life.focus.kind == "venture"
    assert life.focus.intention_id is not None, (
        "a focus with no intention behind it cannot be advanced, so it would "
        "sit there looking like work"
    )
    assert store.episodes, "the thought is kept as an episode, not just a focus"


async def test_choosing_nothing_leaves_the_focus_alone(config):
    rhythm = RhythmController(config)
    life = _life(config, rhythm, script=['{"kind": "none", "subject": "", "interest": 0.0}'])
    life.focus = None

    outcome = await life.act(Decision(kind="reverie", tier="T1", rationale="idle"))

    assert life.focus is None
    assert outcome.ok is False, "an idle mind that wanted nothing is not a failure"


# ------------------------------------------- the bug that made this unreachable


class _QuietSocial:
    """No message is waiting. That is the state the agent got stuck in."""

    async def pending_inbound(self, limit: int = 5) -> list[Any]:
        return []


async def test_after_answering_someone_it_can_think_again(config):
    """The sequence, end to end, because the bug was in the seam between passes.

    A social focus is taken out to answer one message and was never released, so
    every cycle afterwards took the "social focus without a pending message;
    nothing to do" branch in `decide` — which returns *before* the drives and
    before reverie. 2235 cycles were spent exactly there. An agent that had once
    answered a person could not think again until something restarted it, which
    is what it looked like from the outside: alive enough to publish a heartbeat,
    and incapable of having a thought.
    """
    config = _config()
    config.rhythm.reverie.min_gap_min = 1.0
    config.rhythm.reverie.max_gap_min = 2.0
    rhythm = RhythmController(config)
    life = _life(config, rhythm)
    life.deps.social = _QuietSocial()

    # The pass where it answered the message.
    life.focus = Focus(title="Respond to owner", kind="social")
    spent = life.next_focus(Decision(kind="social", tier="T2"), Outcome())
    life.focus = spent

    assert spent is None, "the focus outlived the message it was taken out for"

    # The next pass, minutes later, with nothing owed to anybody.
    rhythm.last_reverie = datetime.now(UTC) - timedelta(hours=1)
    decision = await life.decide(None)

    assert decision.kind == "reverie", (
        "an idle agent with no focus chose not to think about anything at all"
    )


def test_a_handled_message_releases_its_focus(config):
    """2235 cycles were spent here.

    A social focus exists to answer one message. It was never released, so every
    later cycle took the "social focus without a pending message; nothing to do"
    branch in `decide`, which returns *before* the drives and before reverie — and
    an agent that had once answered a person could not think again until
    something restarted it. That is the whole difference between an agent that is
    idle and one that is parked.
    """
    life = Life(LifeDeps(
        config=config, control=_Nothing(), bus=None, memory=_Store(),
        self_model=_Nothing(), attention=None, motivation=None, rhythm=_Nothing(),
        gateway=None, gsl=None, social=None, comms_out=None, toolhost=None,
        audit=ListAudit(), continuity=_Nothing(), consolidation=None, embedder=None,
    ))

    spent = SimpleNamespace(title="Respond to owner", kind="social")
    life.focus = spent

    assert life.next_focus(Decision(kind="social", tier="T2"), Outcome()) is None, (
        "the focus outlived the message it was taken out for"
    )
    assert life.next_focus(
        Decision(kind="social", tier="T2", social={"action": "reply_now"}), Outcome(),
    ) is None, "and outlived the reply that answered it"


def test_reverie_kind_is_not_treated_as_a_goal(config):
    """A reverie that chose nothing must not resurrect the focus it had."""
    life = Life(LifeDeps(
        config=config, control=_Nothing(), bus=None, memory=_Store(),
        self_model=_Nothing(), attention=None, motivation=None, rhythm=_Nothing(),
        gateway=None, gsl=None, social=None, comms_out=None, toolhost=None,
        audit=ListAudit(), continuity=_Nothing(), consolidation=None, embedder=None,
    ))
    life.focus = None

    assert life.next_focus(Decision(kind="reverie", tier="T1"), Outcome()) is None


def test_the_two_kinds_of_idle_thought_are_distinct():
    """Reverie picks a subject and commits to it; a glance only drifts.

    They answer different halves of being idle, and conflating them would either
    turn a cheap associative glance into a manufactured hobby or turn a real
    choice into a string.
    """
    assert ReverieKind.venture.value == "venture"
    assert ReverieKind.question.value == "question"
    assert ReverieKind.none.value == "none"


# ------------------------------------------------------- it does not sleep


class _Memory:
    """A memory store with nothing in it: nothing owed, nothing to return to."""

    def __init__(self) -> None:
        self.db = SimpleNamespace(fetchrow=self._fetchrow, fetch=self._fetch)

    async def _fetchrow(self, *_: Any) -> Any:
        return {"last": None}

    async def _fetch(self, *_: Any) -> list[Any]:
        return []

    async def list_attentionable_intentions(self, limit: int = 50) -> list[Any]:
        return []

    async def list_intentions(self, limit: int = 12) -> list[Any]:
        return []

    async def list_attempts(self, intention_id: Any, limit: int = 5) -> list[Any]:
        return []

    async def open_questions(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    async def random_memories(self, n: int) -> list[Any]:
        return []

    async def search(self, query: str, k: int = 12) -> list[Any]:
        return []

    async def record_episode(self, **_: Any) -> str:
        return "episode"

    async def get_intention(self, _: Any) -> Any:
        return None


class _SelfModel:
    async def get(self, key: str, default: Any = None) -> Any:
        return "Aria" if key == "name" else default

    async def identity_document(self, **_: Any) -> str:
        return "# Identity\n\nI am Aria, an AI agent based on Clio Agent 3 Beta 1."


class _NoMessageWaiting:
    async def pending_inbound(self, limit: int = 5) -> list[Any]:
        return []


class _Consolidation:
    def __init__(self) -> None:
        self.runs = 0

    async def run_nightly(self, now: Any = None) -> dict[str, Any]:
        self.runs += 1
        return {"ok": True}


def _life_that_can_run_a_whole_cycle(config: Any, hour: int, consolidation: Any = None) -> Life:
    """A Life with real attention, drives and rhythm, and a clock pinned to `hour`.

    Everything below the loop is a stub because the question is what the loop does
    with the hour it is given, not what a model would say about it.
    """
    from ethos.engine.attention import Attention
    from ethos.engine.drives import Motivation
    from ethos.gateway.embeddings import HashingEmbedder

    config.timezone = "UTC"
    at_night = datetime(2026, 1, 1, hour, 0, tzinfo=UTC)
    rhythm = RhythmController(config, wall_clock=lambda: at_night)

    async def _no_waiting(state: Any) -> None:
        return None

    rhythm.wait_until_next = _no_waiting  # type: ignore[method-assign]
    embedder = HashingEmbedder(1024)
    memory = _Memory()
    self_model = _SelfModel()
    gateway = ScriptedGateway([])
    return Life(LifeDeps(
        config=config, control=_Nothing(), bus=None, memory=memory,
        self_model=self_model,
        attention=Attention(config, embedder=embedder, gateway=gateway, self_model=self_model),
        motivation=Motivation(config), rhythm=rhythm, gateway=gateway,
        gsl=_Nothing(), social=_NoMessageWaiting(), comms_out=_Nothing(),
        toolhost=None, audit=ListAudit(), continuity=_Nothing(),
        consolidation=consolidation, embedder=embedder, thread_id="self",
        reverie=Reverie(config, gateway=gateway, memory=memory, self_model=self_model),
    ))


@pytest.mark.parametrize("hour", [0, 4, 6, 13, 23])
async def test_the_agent_thinks_at_every_hour_of_the_day(hour: int):
    """A whole cycle at 4am, which is what a job due at 4am needs.

    The agent used to enter a sleep window from 03:00 and record nothing, decide
    nothing and answer nothing until 06:30 — not by policy about rest, but by the
    clock. Anything due inside those three and a half hours was silently not done,
    which is the one thing a continuously existing agent cannot be allowed to get
    wrong. So this is asserted at every hour rather than at one: the absence of a
    window is the property, and an hour that happened to be fine would prove
    nothing.
    """
    config = _config()
    life = _life_that_can_run_a_whole_cycle(config, hour)

    await life._cycle()

    assert life.cycles == 1, f"no cycle was recorded at {hour:02d}:00"
    assert life.states.group() != "SLEEPING"


async def test_the_small_hours_are_not_a_silent_stretch():
    """At 4am the agent decides something, and writes it down.

    A cycle that records itself but decides nothing is the shape of an agent that
    is on but absent, so `decide` is checked for the thing it is there to produce.
    """
    config = _config()
    config.rhythm.reverie.min_gap_min = 0.0
    config.rhythm.reverie.max_gap_min = 0.0
    life = _life_that_can_run_a_whole_cycle(config, 4)
    rhythm = life.deps.rhythm
    rhythm.last_reverie = datetime(2026, 1, 1, 3, 0, tzinfo=UTC)

    decision = await life.decide(await life._assemble_workspace([]))

    assert decision.kind in ("reverie", "mind_wander"), (
        f"an idle agent at 4am decided {decision.kind!r} instead of thinking"
    )


async def test_consolidation_is_keyed_to_its_hour_rather_than_to_a_bedtime():
    """It used to run on the way into the sleep window, so removing the window
    would have removed the only pass the agent's own memory ever got."""
    config = _config()
    consolidation = _Consolidation()
    life = _life_that_can_run_a_whole_cycle(config, 3, consolidation=consolidation)
    await life._pace()
    assert consolidation.runs == 0, "not yet"

    due = _life_that_can_run_a_whole_cycle(config, 5, consolidation=consolidation)
    due._consolidation_done_tonight = "2026-01-01"
    await due._pace()
    assert consolidation.runs == 0, "already done today"

    fresh = _Consolidation()
    later = _life_that_can_run_a_whole_cycle(config, 5, consolidation=fresh)
    await later._pace()
    assert fresh.runs == 1, "the daily pass still happens, at its own hour"
    await later._pace()
    assert fresh.runs == 1, "and once a day is once a day"


def test_the_prompt_offers_a_way_out():
    """The prompt has to permit `none`, or the floor is the only thing stopping it."""
    from ethos.prompts.reverie import REVERIE_PROMPT

    assert "none" in REVERIE_PROMPT
    assert "allowed" in REVERIE_PROMPT
    for field in ('"kind"', '"subject"', '"why"', '"first_step"', '"interest"'):
        assert field in REVERIE_PROMPT, f"the schema in the prompt is missing {field}"


def test_interest_is_a_number_and_not_a_word(config):
    """A model that answers `interest: "high"` has not told us it is interested."""
    reverie = _reverie(ScriptedGateway(['{"kind": "venture", "subject": "x", "interest": "high"}']), config=config)

    subject = reverie._subject_from('{"kind": "venture", "subject": "x", "interest": "high"}')

    assert subject.interest == 0.0
    assert subject.kind is ReverieKind.venture


if __name__ == "__main__":
    asyncio.run(test_an_idle_mind_takes_up_something_of_its_own(_config()))
