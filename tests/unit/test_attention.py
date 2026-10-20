from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ethos.engine.attention import Attention, estimate_salience, focus_from_percept
from ethos.engine.drives import (
    DriveInputs,
    Impulse,
    propose_impulses,
    softmax_sample,
    utility,
)
from ethos.engine.states import State, StateMachine
from ethos.gateway.embeddings import HashingEmbedder
from ethos.schemas.events import Percept
from ethos.schemas.gsl import Focus
from ethos.schemas.messages import Message


def percept(sender="owner", urgency=0.3, summary="a message", kind="msg.inbound"):
    return Percept(kind=kind, source=f"web:{sender}", sender_class=sender,
                   sender_person=sender if sender != "unknown" else None,
                   urgency=urgency, summary=summary)


def test_salience_formula_exact_math(config):
    p = percept(sender="owner", urgency=0.8, summary="urgent thing")
    embedder = HashingEmbedder(1024)
    focus = Focus(title="unrelated focus", kind="task")
    sal, components = estimate_salience(
        p, focus, config.rhythm.attention.weights, config.rhythm.attention.sender,
        embedder, embedder.embed([focus.title])[0], None,
    )
    expected = (
        0.35 * 0.9 + 0.30 * 0.8 + 0.20 * components["rel"] + 0.15 * components["nov"]
    )
    assert sal == pytest.approx(min(1.0, expected))
    assert 0.0 <= sal <= 1.0


def test_salience_sender_weights_order(config):
    embedder = HashingEmbedder(1024)
    results = {}
    for sender in ("owner", "known", "unknown", "system"):
        p = percept(sender=sender, urgency=0.0, summary="zzz")
        sal, _ = estimate_salience(
            p, None, config.rhythm.attention.weights, config.rhythm.attention.sender,
            embedder, None, None,
        )
        results[sender] = sal
    assert results["owner"] > results["known"] > results["unknown"]


def test_salience_clip(config):
    p = percept(sender="owner", urgency=1.0, summary="super urgent")
    sal, _ = estimate_salience(
        p, None, config.rhythm.attention.weights, config.rhythm.attention.sender,
        None, None, None,
    )
    assert sal == pytest.approx(min(1.0, 0.35 * 0.9 + 0.30 * 1.0 + 0.20 * 0.0 + 0.15 * 0.5))


async def test_refinement_only_in_band(config):
    from tests.conftest import ScriptedGateway

    gateway = ScriptedGateway(['{"urgency": 0.9, "relevance": 0.8, "novelty": 0.2}'])
    attention = Attention(config, embedder=HashingEmbedder(1024), gateway=gateway)
    low = percept(urgency=0.05, summary="trivial", kind="percept")
    high = percept(sender="owner", urgency=0.99, summary="critical alert")
    focus = Focus(title="work", kind="task")
    appraisal = await attention.appraise(
        [low, high], focus,
        thresholds_by_state={"default": 0.65}, state_name="THINKING_FOCUSED",
    )
    assert len(gateway.requests) <= attention.max_refines_per_cycle
    for scored in appraisal.top:
        lo, hi = config.rhythm.attention.refine_band
        if scored.salience < lo or scored.salience > hi:
            continue
        assert scored.percept.kind in ("percept", "msg.inbound")


async def test_interrupt_thresholds_by_state(config):
    attention = Attention(config, embedder=HashingEmbedder(1024), gateway=None)
    interrupting = percept(sender="owner", urgency=0.95, summary="EMERGENCY now")
    focus = Focus(title="anything", kind="task")
    for state_name, expected in (
        ("THINKING_FOCUSED", True),
        ("THINKING_REFLECTING", True),
        ("RESTING", True),
    ):
        appraisal = await attention.appraise(
            [interrupting], focus,
            thresholds_by_state=config.rhythm.attention.interrupt_thresholds,
            state_name=state_name,
        )
        assert appraisal.interrupt is expected, state_name


async def test_every_state_an_agent_can_be_in_is_reachable_by_a_percept(config):
    """There is no state that swallows an interruption.

    The agent used to have a `SLEEPING` state with an 0.85 interrupt threshold,
    which read as an hour in the morning when a person could not reach it at all.
    Every state the machine can now be in is a state it can be talked out of,
    and this is the test that fails if a future state is added without saying who
    has to be louder.
    """
    attention = Attention(config, embedder=HashingEmbedder(1024), gateway=None)
    interrupting = percept(sender="owner", urgency=0.95, summary="EMERGENCY now")
    focus = Focus(title="anything", kind="task")
    for state_name in config.rhythm.attention.interrupt_thresholds:
        if state_name == "default":
            continue
        appraisal = await attention.appraise(
            [interrupting], focus,
            thresholds_by_state=config.rhythm.attention.interrupt_thresholds,
            state_name=state_name,
        )
        assert appraisal.interrupt, f"{state_name} cannot be interrupted"


async def test_the_agent_is_never_in_a_state_that_ignores_a_critical_message(config):
    attention = Attention(config, embedder=None, gateway=None)
    critical = percept(sender="owner", urgency=1.0, summary="server down emergency")
    focus = Focus(title="server down emergency", kind="commitment")
    for state_name in config.rhythm.attention.interrupt_thresholds:
        appraisal = await attention.appraise(
            [critical], focus,
            thresholds_by_state=config.rhythm.attention.interrupt_thresholds,
            state_name=state_name,
        )
        assert appraisal.interrupt, state_name


async def test_no_interrupt_below_threshold(config):
    attention = Attention(config, embedder=HashingEmbedder(1024), gateway=None)
    boring = percept(sender="unknown", urgency=0.1, summary="meh")
    appraisal = await attention.appraise(
        [boring], None,
        thresholds_by_state=config.rhythm.attention.interrupt_thresholds,
        state_name="THINKING_FOCUSED",
    )
    assert not appraisal.interrupt


async def test_appraise_dedupes(config):
    attention = Attention(config, embedder=None, gateway=None)
    now = datetime.now(UTC)
    p1 = percept(summary="spam", kind="percept")
    p1 = p1.model_copy(update={"ts": now})
    p2 = p1.model_copy(update={"ts": now + timedelta(seconds=2)})
    appraisal = await attention.appraise([p1, p2], None)
    assert len(appraisal.top) == 1


def test_focus_from_inbound_message():
    message = Message(channel="telegram", person_id="tg:42", text="hello there")
    p = Attention.percept_from_message(message)
    assert p.sender_class == "known"
    assert p.urgency == 0.3
    focus = focus_from_percept(p)
    assert focus.kind == "social"
    assert "tg:42" in focus.title


def test_drive_pressures_formula(config):
    inputs = DriveInputs(
        intentions=[{"kind": "commitment", "status": "active", "priority": 0.9,
                     "deadline": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}],
        open_questions=5, failed_recent=2,
    )
    from ethos.engine.drives import commitment_gain, curiosity_gain

    assert commitment_gain(inputs) == pytest.approx(0.9 * 0.5 + 1.0 * 0.5)
    assert curiosity_gain(inputs) == pytest.approx(0.5 * 0.6)
    from ethos.engine.drives import pressures

    p = pressures(inputs, config.rhythm.drives.base)
    assert p["commitment"] == pytest.approx(min(1.0, config.rhythm.drives.base["commitment"] + 0.95))
    assert 0 <= p["curiosity"] <= 1


def test_impulse_utility_formula(config):
    cfg = config.rhythm.drives
    p = {"commitment": 0.8, "curiosity": 0.6, "mastery": 0.2, "creation": 0.2,
         "order": 0.2, "social": 0.2, "homeostasis": 0.2}
    impulse = Impulse(name="advance", description="d", drive="commitment",
                      relevance=0.9, value_hat=0.8, cost_hat=0.2)
    energy = 0.5
    productive = cfg.weights["commitment"] * p["commitment"] * 0.9
    drive_pull = sum(cfg.weights[d] * p[d] for d in p)
    expected = (productive + drive_pull * 0.15) * 0.8 - (cfg.lambda0 / max(energy, 0.1)) * 0.2
    assert utility(impulse, p, cfg.weights, energy, cfg.lambda0) == pytest.approx(expected)


def test_utility_low_energy_penalizes_cost(config):
    cfg = config.rhythm.drives
    impulse = Impulse(name="x", description="d", drive="curiosity", relevance=1.0,
                      value_hat=0.5, cost_hat=0.5)
    p = {"curiosity": 1.0, "commitment": 0, "mastery": 0, "creation": 0,
         "order": 0, "social": 0, "homeostasis": 0}
    fresh = utility(impulse, p, cfg.weights, 1.0, cfg.lambda0)
    tired = utility(impulse, p, cfg.weights, 0.1, cfg.lambda0)
    assert tired < fresh


def test_softmax_sample_extremes():
    rng = random.Random(42)
    assert softmax_sample([], 0.15, rng) == -1
    pick = softmax_sample([10.0, -10.0], 0.15, rng)
    assert pick == 0
    counts = [0, 0]
    for _ in range(500):
        counts[softmax_sample([1.0, 1.0], 0.15, rng)] += 1
    assert 200 <= counts[0] <= 300


def test_propose_impulses_threshold(config):
    p = {"commitment": 0.9, "curiosity": 0.1, "mastery": 0.1, "creation": 0.1,
         "order": 0.1, "social": 0.1, "homeostasis": 0.9}
    impulses = propose_impulses(p, DriveInputs(), config.rhythm.drives.propose_threshold)
    names = {i.name for i in impulses}
    assert "advance_commitment" in names
    assert "explore_question" not in names
    assert "maintenance" in names


async def test_choose_focus_deadline_bypass(config):
    """The bypass has to hand back something the agent can act on.

    It used to return a `Focus` carrying a title and a kind and no
    `intention_id`, and `Life.decide` only returns `advance_goal` for a focus
    that has one. So the focus the agent built specifically to override its own
    arbitration, at the moment a deadline made acting urgent, was a focus it
    could not act on at all: `decide` fell through to `kind="none"`, the cycle
    accomplished nothing, and the next `choose_focus` re-fired the same bypass
    and produced the same nothing. A deadline was the one condition guaranteed
    to waste a cycle.
    """
    from ethos.engine.drives import Motivation

    motivation = Motivation(config, rng=random.Random(7))
    deadline_soon = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    intention_id = uuid4()
    inputs = DriveInputs(intentions=[
        {"id": str(intention_id), "title": "deliver the report", "kind": "commitment",
         "status": "in_progress", "priority": 0.9, "deadline": deadline_soon},
    ])
    created = []

    async def create_intention(**kwargs):
        created.append(kwargs)
        return uuid4()

    focus, _ = await motivation.choose_focus(inputs, energy=0.9,
                                             create_intention_fn=create_intention)
    assert focus is not None
    assert focus.kind == "commitment"
    assert "deadline" in focus.why
    assert created == []
    assert focus.intention_id == intention_id, \
        "the focus must name the commitment it is about, or nothing can act on it"


async def test_the_bypass_picks_the_most_urgent_deadline_not_the_first(config):
    """It says it takes the most urgent; it has to take the most urgent."""
    from ethos.engine.drives import Motivation

    motivation = Motivation(config, rng=random.Random(7))
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    later = (datetime.now(UTC) + timedelta(hours=20)).isoformat()
    urgent, slack = uuid4(), uuid4()
    inputs = DriveInputs(intentions=[
        # Listed first, least urgent: the order the database happened to use.
        {"id": str(slack), "title": "due in twenty hours", "kind": "commitment",
         "status": "proposed", "priority": 0.2, "deadline": later},
        {"id": str(urgent), "title": "due in an hour", "kind": "commitment",
         "status": "proposed", "priority": 0.2, "deadline": soon},
    ])

    focus, _ = await motivation.choose_focus(inputs, energy=0.9)

    assert focus.intention_id == urgent, "the closest deadline is the one that is due"


async def test_an_unreadable_deadline_does_not_stop_the_loop(config):
    """A deadline is written by a model, so it can be a word.

    This runs *before* arbitration -- it decides whether anything else gets
    considered -- so raising here meant the whole cycle died on a malformed
    optional field, through the same handler that had the agent frozen for
    1,524 cycles. A date nobody can read is not a date that has passed; it is
    arbitration's problem to handle, not a reason to hold still.
    """
    from ethos.engine.drives import Motivation

    motivation = Motivation(config, rng=random.Random(7))
    real = uuid4()
    inputs = DriveInputs(intentions=[
        {"id": str(uuid4()), "title": "when is it due?", "kind": "commitment",
         "status": "proposed", "priority": 0.9, "deadline": "next tuesday"},
        {"id": str(real), "title": "real work", "kind": "commitment",
         "status": "proposed", "priority": 0.9,
         "deadline": (datetime.now(UTC) + timedelta(hours=3)).isoformat()},
    ])

    focus, _ = await motivation.choose_focus(inputs, energy=0.9)

    assert focus is not None and focus.intention_id == real


async def test_the_commitment_impulse_advances_a_commitment_rather_than_another_one(
    config,
):
    """The bug this whole path existed to avoid.

    `advance_commitment` says "Advance the highest-urgency open commitment by
    one concrete step". It was implemented by taking that sentence's *name* as
    the title of a brand new intention. So the drive meant to spend the agent's
    turns on the commitments it owed spent them on an intention called
    `advance_commitment`, and the ones it owed sat at `proposed` with empty
    resume states while the database filled with placeholders named after drives.

    The user-visible symptom was an agent that says it will do the thing and
    then does not: the promise was recorded as an intention and no code path
    could ever select that intention for work.
    """
    from ethos.engine.drives import Motivation

    real_id = uuid4()
    inputs = DriveInputs(intentions=[
        {"id": str(real_id), "title": "Build the standalone HTML game",
         "kind": "commitment", "status": "proposed", "priority": 0.9, "deadline": None},
    ])
    created = []

    async def create_intention(**kwargs):
        created.append(kwargs)
        return uuid4()

    motivation = Motivation(config, rng=random.Random(11))
    # Force the commitment drive to win, so this tests resolution and not luck.
    motivation.arbitrate = lambda inputs, energy: (  # type: ignore[method-assign]
        Impulse(name="advance_commitment", drive="commitment", relevance=0.9,
                value_hat=0.8, cost_hat=0.15,
                description="Advance the highest-urgency open commitment."),
        motivation.drive_pressures(inputs),
    )

    focus, _ = await motivation.choose_focus(inputs, energy=0.9,
                                            create_intention_fn=create_intention)

    assert focus is not None
    assert focus.intention_id == real_id, "it must work the commitment it means to advance"
    assert focus.title == "Build the standalone HTML game"
    assert created == [], "and it must not mint a placeholder to advance instead"


async def test_the_venture_impulse_advances_a_venture(config):
    """`push_venture` has the same shape and the same defect."""
    from ethos.engine.drives import Motivation

    venture_id = uuid4()
    inputs = DriveInputs(intentions=[
        {"id": str(uuid4()), "title": "a commitment that is not a venture",
         "kind": "commitment", "status": "proposed", "priority": 0.9, "deadline": None},
        {"id": str(venture_id), "title": "teach the scheduler", "kind": "venture",
         "status": "proposed", "priority": 0.3, "deadline": None},
    ])

    motivation = Motivation(config, rng=random.Random(11))
    motivation.arbitrate = lambda inputs, energy: (  # type: ignore[method-assign]
        Impulse(name="push_venture", drive="creation", relevance=0.9,
                value_hat=0.75, cost_hat=0.2,
                description="Move the most promising venture one step."),
        motivation.drive_pressures(inputs),
    )

    focus, _ = await motivation.choose_focus(inputs, energy=0.9)

    assert focus is not None and focus.intention_id == venture_id


async def test_an_impulse_with_no_existing_subject_still_opens_its_own_work(config):
    """Tidying has no intention to tidy, and should still be able to happen.

    The resolver has to say "no target" rather than refuse, or `tidy`,
    `reach_out` and `maintenance` would silently stop being possible the moment
    the fix landed.
    """
    from ethos.engine.drives import Motivation

    created = []

    async def create_intention(**kwargs):
        created.append(kwargs)
        return uuid4()

    motivation = Motivation(config, rng=random.Random(11))
    motivation.arbitrate = lambda inputs, energy: (  # type: ignore[method-assign]
        Impulse(name="tidy", drive="order", relevance=0.9, value_hat=0.45,
                cost_hat=0.03, description="Triage the backlog."),
        motivation.drive_pressures(inputs),
    )

    focus, _ = await motivation.choose_focus(DriveInputs(), energy=0.9,
                                             create_intention_fn=create_intention)

    assert len(created) == 1, "an impulse with nothing to advance opens its own work"
    assert created[0]["title"] == "tidy"
    assert focus.title == "tidy"
    assert focus.intention_id is not None, "and the new work is bound to the focus"


async def test_a_commitment_impulse_with_nothing_committed_still_acts(config):
    """Nothing committed is not a reason to do nothing.

    It is a reason to open something, which is what the impulse asked for.
    """
    from ethos.engine.drives import Motivation

    created = []

    async def create_intention(**kwargs):
        created.append(kwargs)
        return uuid4()

    motivation = Motivation(config, rng=random.Random(11))
    motivation.arbitrate = lambda inputs, energy: (  # type: ignore[method-assign]
        Impulse(name="advance_commitment", drive="commitment", relevance=0.9,
                value_hat=0.8, cost_hat=0.15,
                description="Advance the highest-urgency open commitment."),
        motivation.drive_pressures(inputs),
    )

    focus, _ = await motivation.choose_focus(
        DriveInputs(intentions=[{"id": str(uuid4()), "title": "a venture only",
                                 "kind": "venture", "status": "proposed",
                                 "priority": 0.5, "deadline": None}]),
        energy=0.9, create_intention_fn=create_intention)

    assert len(created) == 1
    assert focus is not None


def test_drive_inputs_carry_the_id_of_the_work_they_describe():
    """The projection used to carry no id at all.

    Kind, status, priority and deadline are enough to measure how much an
    intention wants doing and not enough to say which one it is. So the layer
    that chooses could not name its target, and a `commitment` focus came back
    holding a title with nothing behind it -- which is exactly what the focus
    dead end above was made of.
    """
    from ethos.engine.drives import DriveInputs as _Inputs
    from ethos.schemas.gsl import IntentionRecord

    intention = IntentionRecord(id=uuid4(), title="Build the game", kind="commitment")
    projected = _Inputs.from_intentions([intention])

    assert projected.intentions[0]["id"] == str(intention.id)
    assert projected.intentions[0]["title"] == "Build the game"


async def test_choose_focus_none_when_all_drives_low(config):
    from ethos.engine.drives import Motivation

    motivation = Motivation(config, rng=random.Random(7))
    inputs = DriveInputs(energy=1.0)
    focus, pressures = await motivation.choose_focus(inputs, energy=1.0)
    assert focus is None
    assert all(v <= 1.0 for v in pressures.values())


def test_state_machine_transitions():
    sm = StateMachine(initial=State.THINKING_REFLECTING)
    assert sm.can_transition(State.ACTING)
    assert sm.transition(State.ACTING, "act")
    assert sm.can_transition(State.RESTING)
    assert sm.transition(State.RESTING, "low energy")
    assert sm.can_transition(State.THINKING_FOCUSED)
    assert sm.transition(State.THINKING_FOCUSED, "focus acquired")
    assert sm.state == State.THINKING_FOCUSED


def test_there_is_no_state_the_agent_is_absent_in():
    """The vocabulary itself is the guarantee.

    A `SLEEPING` member, or anything like it, would be a state the agent could
    be in at an hour somebody was waiting for it — and the state machine would
    accept the transition, so nothing downstream would ever have to notice. The
    check is on the enum because that is where the possibility lives.
    """
    names = {state.value for state in State}
    assert "SLEEPING" not in names
    assert "ASLEEP" not in names
    assert "OFFLINE" not in names
    assert names == {
        "THINKING_FOCUSED", "THINKING_REFLECTING", "ACTING", "WAITING",
        "NOTIFYING", "RESTING", "PAUSED", "RESUMING",
    }


def test_state_machine_paused_only_resumes():
    sm = StateMachine(initial=State.PAUSED)
    for state in (State.THINKING_FOCUSED, State.RESTING, State.ACTING):
        assert not sm.can_transition(state)
    assert sm.can_transition(State.RESUMING)


def test_interrupt_threshold_lookup(config):
    sm = StateMachine(
        thresholds=config.rhythm.attention.interrupt_thresholds,
    )
    sm.transition(State.THINKING_FOCUSED, "focus")
    assert sm.interrupt_threshold() == pytest.approx(0.65)
    sm.transition(State.RESTING, "settling")
    assert sm.interrupt_threshold() == pytest.approx(0.50)
    sm.transition(State.THINKING_REFLECTING, "back to it")
    assert sm.interrupt_threshold() == pytest.approx(0.40)


async def test_a_stale_backlog_cannot_starve_a_deadline_that_can_still_be_met(config):
    """Urgency saturates, so it cannot be used to *choose*.

    `intent_urgency` clips `due` to 1.0 for anything overdue or nearly due, and
    that is right for a drive pressure — the commitment drive just needs to know
    it is under pressure. It is useless as an order, and it was being used as
    one: six stale seeded intentions and a commission four minutes from due all
    scored 0.7498, so `max` returned whichever came first in the list and the
    live deadline lost to a greeting that had been open since yesterday.

    The rule that fixes it is not "pick the highest score" but this: a deadline
    still ahead can be missed, while one already passed has already been missed.
    Letting stale work outrank live work turns an old failure into the cause of
    a new one, because the overdue item wins again on every single cycle.
    """
    from ethos.engine.drives import Motivation

    now = datetime.now(UTC)
    live = uuid4()
    stale = [uuid4() for _ in range(6)]
    inputs = DriveInputs(intentions=[
        {"id": str(live), "title": "Build the standalone HTML game", "kind": "commitment",
         "status": "proposed", "priority": 0.5,
         "deadline": (now + timedelta(minutes=4)).isoformat()},
        *[
            {"id": str(s), "title": f"stale seeded task {i}", "kind": "commitment",
             "status": "proposed", "priority": 0.5,
             "deadline": (now - timedelta(days=3, minutes=i)).isoformat()}
            for i, s in enumerate(stale)
        ],
    ])

    motivation = Motivation(config, rng=random.Random(5))
    focus, _ = await motivation.choose_focus(inputs, energy=0.9)

    assert focus is not None and focus.intention_id == live, (
        "the work that can still be delivered must be reachable"
    )


async def test_an_overdue_backlog_drains_newest_first_and_never_livelocks(config):
    """Nothing is left permanently un-workable, and nothing new is starved.

    With no live deadline in play the bypass still has to pick something. Picking
    the *most* overdue looks obvious and is a trap: an overdue item has already
    been missed, so the most-overdue rule keeps re-picking the same stuck row —
    the seeded backlog never drains, no cycle ever starts anything new, and the
    agent looks busy while doing nothing new. That is the same shape as the
    original complaint.

    Newest-first empties the queue from the front, gives the most recent miss its
    turn soonest while it is still actionable, and always reaches new work.
    """
    from ethos.engine.drives import Motivation

    now = datetime.now(UTC)
    older, newer = uuid4(), uuid4()
    inputs = DriveInputs(intentions=[
        {"id": str(older), "title": "stuck for a week", "kind": "commitment",
         "status": "proposed", "priority": 0.5,
         "deadline": (now - timedelta(days=7)).isoformat(),
         "created_at": (now - timedelta(days=8)).isoformat()},
        {"id": str(newer), "title": "missed an hour ago", "kind": "commitment",
         "status": "proposed", "priority": 0.5,
         "deadline": (now - timedelta(hours=1)).isoformat(),
         "created_at": (now - timedelta(days=1)).isoformat()},
    ])

    motivation = Motivation(config, rng=random.Random(5))
    focus, _ = await motivation.choose_focus(inputs, energy=0.9)

    assert focus is not None and focus.intention_id == newer, \
        "the recent miss must not wait behind the one that never gets done"


async def test_an_undated_intention_never_outranks_a_dated_one(config):
    """Priority on its own is not a deadline."""
    from ethos.engine.drives import Motivation

    dated = uuid4()
    inputs = DriveInputs(intentions=[
        {"id": str(uuid4()), "title": "high priority, no deadline", "kind": "commitment",
         "status": "proposed", "priority": 0.99, "deadline": None},
        {"id": str(dated), "title": "low priority, due today", "kind": "commitment",
         "status": "proposed", "priority": 0.1,
         "deadline": (datetime.now(UTC) + timedelta(hours=2)).isoformat()},
    ])

    motivation = Motivation(config, rng=random.Random(5))
    focus, _ = await motivation.choose_focus(inputs, energy=0.9)

    assert focus is not None and focus.intention_id == dated
