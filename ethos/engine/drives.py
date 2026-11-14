from __future__ import annotations

import math
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from ethos.config import EthosConfig
from ethos.observability.logger import get_logger
from ethos.schemas.gsl import Focus, IntentionRecord
from ethos.util import clip

logger = get_logger("ethos.drives")

DRIVES = ("commitment", "curiosity", "mastery", "creation", "order", "social", "homeostasis")

# What an undated promise is worth to the drive that exists to keep it.
#
# A commission arrives with no deadline most of the time -- the deliberation is
# told to set `deadline_iso` only when a real date was named -- and undated work
# reads as `priority * 0.4`, which is 0.2 at the default priority. Pressure is
# the base plus the gain, so the commitment drive read 0.20 + 0.2 = 0.40 against
# a propose threshold of "strictly greater than 0.4": the one impulse whose
# whole subject is work somebody is waiting on was never proposed for exactly
# the work a commission creates. The focus carried the promise while it held,
# but the first time the focus was dropped -- an interrupt, a newer commission
# taking the pass -- the drives could not see the work again, and "the drives
# picked something else" was not a risk but a certainty.
#
# The floor puts a lone undated promise at 0.20 + 0.5 = 0.70, comfortably
# over the threshold, so the commitment drive proposes it on every idle pass
# and the softmax can actually weigh it. Work with a deadline keeps its own
# (higher, calendar-driven) score; work nobody is waiting on keeps 0.2, which
# stays below the threshold and is the honest reading of an open to-do nobody
# asked for.
UNDATED_COMMITMENT_URGENCY = 0.5

# The statuses a commission can be in and still mean somebody is waiting. The
# same list `MemoryStore` treats as open work; duplicated here because the
# drives never import the store.
_OPEN = frozenset({
    "proposed", "active", "in_progress", "waiting", "awaiting_review",
})


class DriveInputs(BaseModel):
    intentions: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: int = 0
    failed_recent: int = 0
    active_ventures: int = 0
    value_recent: float = 0.0
    unread_events: int = 0
    # Hours since my last social exchange of any direction — mine or theirs.
    # Named `last_contact` still, because the drive it feeds is about connection
    # rather than about inbound mail; see `social_gain`.
    last_contact_age_h: float | None = None
    energy: float = 0.8
    disk_free_ratio: float = 0.8
    budget_ratio: float = 1.0
    maintenance_due_h: float = 0.0
    # Clio 3.2 DAG telemetry (see `MemoryStore.dag_telemetry`).
    dag_blocked_edges: int = 0
    dag_ready_count: int = 0
    dag_budget_burn_usd: float = 0.0
    leaf_verification_failures: int = 0
    anomalous_outcomes: int = 0
    # Clio 3.3: evergreen projects whose cooldown elapsed and whose next
    # evolution cycle is owed. Feeds the `evolve_project` impulse.
    evolving_projects: int = 0

    @classmethod
    def from_intentions(cls, intentions: Sequence[IntentionRecord], **kwargs: Any) -> DriveInputs:
        # The id and title have to come along. This projection used to carry
        # only kind, status, priority and deadline -- enough to measure how much
        # an intention wanted doing, and nothing at all to say *which* one. So
        # no matter how the drives weighed the work, the layer that picks a
        # focus could not name its target, and a `commitment` focus came back
        # holding a title with no intention behind it.
        data = [
            {
                "id": str(i.id),
                "title": i.title,
                "kind": i.kind,
                "status": i.status,
                "priority": i.priority,
                "origin": i.origin,
                "deadline": i.deadline.isoformat() if i.deadline else None,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in intentions
        ]
        return cls(intentions=data, **kwargs)


def _hours_left(item: dict[str, Any], now: datetime) -> float | None:
    """Hours until this intention's deadline, or `None` if it has none to read.

    The deadline is written by a model, so it can be a word rather than a date,
    and this is on the bypass path — the one path that runs before arbitration
    and decides whether anything else gets considered at all. An unreadable
    value used to raise out of here, through `choose_focus`, through `_cycle`,
    into the same handler that had the agent frozen for 1,524 cycles. A deadline
    nobody can read is not a deadline that has passed; it is a deadline nobody
    can read, and the rest of the arbitration is perfectly willing to proceed.
    """
    deadline = item.get("deadline")
    if not deadline:
        return None
    try:
        deadline_dt = datetime.fromisoformat(deadline)
    except (TypeError, ValueError):
        return None
    if deadline_dt.tzinfo is None:
        deadline_dt = deadline_dt.replace(tzinfo=now.tzinfo)
    return (deadline_dt - now).total_seconds() / 3600.0


def _focus_id(item: dict[str, Any]) -> UUID | None:
    """The intention id carried alongside a drive's view of the work.

    `None` when the projection has no id to give — a `DriveInputs` built by hand
    rather than through `from_intentions`, as the tests do. Returning `None` is
    honest there: there is no intention to bind to, and a focus pretending
    otherwise is the dead end this whole path was rebuilt to remove.
    """
    raw = item.get("id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


def intent_urgency(item: dict[str, Any], now: datetime) -> float:
    """How badly one open intention wants doing, on its own terms.

    Priority is half the answer and the calendar is the other half. Shared by
    the commitment drive and by the thing that actually chooses which intention
    to work on, because a drive that measures one thing and a selector that
    picks another would prefer work and then not do it.
    """
    priority = float(item.get("priority", 0.5))
    hours_left = _hours_left(item, now)
    if hours_left is None:
        # Undated work somebody commissioned is not undated work nobody
        # asked for; see `UNDATED_COMMITMENT_URGENCY`.
        if item.get("kind") == "commitment":
            return max(priority * 0.4, UNDATED_COMMITMENT_URGENCY)
        return priority * 0.4
    if hours_left < 0:
        due = 1.0
    else:
        due = clip(1.0 - hours_left / (7 * 24.0))
    return clip(priority * 0.5 + due * 0.5)


def commitment_gain(inputs: DriveInputs, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    base = max(
        (intent_urgency(item, now) for item in inputs.intentions),
        default=0.0,
    )
    # Blocked critical-path nodes are pressure to unblock, not just to work.
    blocked_boost = clip((inputs.dag_blocked_edges or 0) / 10.0) * 0.3
    return clip(base + blocked_boost)


def curiosity_gain(inputs: DriveInputs) -> float:
    base = clip(inputs.open_questions / 10.0) * 0.6
    anomalous = clip((inputs.anomalous_outcomes or 0) / 5.0) * 0.3
    return clip(base + anomalous)


def mastery_gain(inputs: DriveInputs) -> float:
    base = clip(inputs.failed_recent / 5.0) * 0.7
    leaf_failures = clip((inputs.leaf_verification_failures or 0) / 5.0) * 0.3
    return clip(min(base, 0.7) + leaf_failures)


def creation_gain(inputs: DriveInputs) -> float:
    return clip(0.2 + 0.3 * clip(inputs.active_ventures / 3.0) + 0.3 * clip(inputs.value_recent / 10.0))


def order_gain(inputs: DriveInputs) -> float:
    base = clip(inputs.unread_events / 50.0)
    blocked = clip((inputs.dag_blocked_edges or 0) / 10.0) * 0.3
    return clip(base + blocked)


def social_gain(inputs: DriveInputs) -> float:
    """How long *I* have gone without any social exchange, in either direction.

    This used to read only the inbound table — the drive rose the longer a
    *person* had left me alone, which made other people's silence the engine of
    my own sociability, and my session's axis. It is symmetric now: an exchange
    is an exchange whoever opened it, so the drive measures my own
    disengagement — how long it has been since I last spoke with anyone, or
    since anyone last spoke with me — and not whether somebody is waiting on me.
    A person's silence is their own business; how long I have gone without
    reaching out is mine.
    """
    if inputs.last_contact_age_h is None:
        return 0.5
    return clip(inputs.last_contact_age_h / 24.0)


def homeostasis_gain(inputs: DriveInputs) -> float:
    maintenance = clip(inputs.maintenance_due_h / 48.0) * 0.3
    disk = (1.0 - clip(inputs.disk_free_ratio)) * 0.3
    energy_part = (1.0 - clip(inputs.energy)) * 0.8
    budget = (1.0 - clip(inputs.budget_ratio)) * 0.2
    return clip(energy_part + disk + maintenance + budget)


GAINS = {
    "commitment": commitment_gain,
    "curiosity": curiosity_gain,
    "mastery": mastery_gain,
    "creation": creation_gain,
    "order": order_gain,
    "social": social_gain,
    "homeostasis": homeostasis_gain,
}


def pressures(inputs: DriveInputs, base: dict[str, float], now: datetime | None = None) -> dict[str, float]:
    out: dict[str, float] = {}
    for drive in DRIVES:
        gain_fn = GAINS[drive]
        gain = gain_fn(inputs) if drive != "commitment" else gain_fn(inputs, now)
        out[drive] = clip(base.get(drive, 0.2) + gain)
    return out


class Impulse(BaseModel):
    name: str
    description: str
    drive: str
    relevance: float = 1.0
    value_hat: float = 0.5
    cost_hat: float = 0.02


def propose_impulses(p: dict[str, float], inputs: DriveInputs, threshold: float) -> list[Impulse]:
    """One impulse per drive that wants to talk, in drive order.

    A table rather than seven branches, because the shape is identical in all
    seven and the differences are data: which drive, what the impulse is called,
    what it is worth and costs, and how it reads in a decision log.
    """
    candidates = (
        ("commitment", "advance_commitment", 0.9, 0.8, 0.15,
         "Advance the highest-urgency open commitment by one concrete step."),
        ("curiosity", "explore_question", 0.95, 0.6, 0.10,
         "Take the most interesting open question and dig for 20 focused minutes."),
        ("mastery", "deliberate_practice", 0.9, 0.7, 0.15,
         "Retry the most recent hard failure with a deliberately different strategy."),
        ("creation", "push_venture", 0.9, 0.75, 0.20,
         "Move the most promising venture intention one step toward real-world value."),
        ("order", "tidy", 0.9, 0.45, 0.03,
         "Triage the backlog: classify unread events, close stale loops, tidy notes."),
        ("social", "reach_out", 0.85, 0.5, 0.05,
         "Send something genuinely useful to a person I have not talked to recently."),
        ("homeostasis", "maintenance", 0.9, 0.5, 0.02,
         "Environment maintenance: verify health, prune trash, check disk and schedules."),
    )
    impulses = [
        Impulse(
            name=name, drive=drive, relevance=relevance,
            value_hat=value_hat, cost_hat=cost_hat, description=description,
        )
        for drive, name, relevance, value_hat, cost_hat, description in candidates
        if p[drive] > threshold
    ]
    # The eighth impulse is a composition rather than a table row: an evergreen
    # project due for its next cycle is pushed by creation (make the thing
    # better), mastery (extend what already works) and curiosity (what does the
    # record say is worth trying next) — any of the three wanting to talk, with
    # the project actually waiting, is enough.
    if (
        (inputs.evolving_projects or 0) > 0
        and any(p.get(drive, 0.0) > threshold for drive in ("creation", "mastery", "curiosity"))
    ):
        impulses.append(Impulse(
            name="evolve_project", drive="creation", relevance=0.9,
            value_hat=0.75, cost_hat=0.15,
            description=(
                "Take the evergreen project whose cooldown elapsed and propose "
                "its next evolution cycle."
            ),
        ))
    return impulses


def utility(
    impulse: Impulse,
    p: dict[str, float],
    weights: dict[str, float],
    energy: float,
    lambda0: float,
) -> float:
    """U(i) = sum_d w_d p_d r_d(i) V_hat(i) - lambda(E) c_hat(i), lambda(E) = lambda0 / max(E, 0.1)."""
    drive_pull = sum(
        weights.get(drive, 0.0) * p.get(drive, 0.0)
        for drive in DRIVES
    )
    productive = weights.get(impulse.drive, 0.0) * p.get(impulse.drive, 0.0) * impulse.relevance
    alignment = drive_pull * 0.15
    benefit = (productive + alignment) * impulse.value_hat
    lam = lambda0 / max(energy, 0.1)
    return benefit - lam * impulse.cost_hat


def softmax_sample(utilities: list[float], tau: float, rng: random.Random) -> int:
    if not utilities:
        return -1
    scaled = [u / max(tau, 1e-6) for u in utilities]
    top = max(scaled)
    exps = [math.exp(u - top) for u in scaled]
    total = sum(exps) or 1.0
    roll = rng.random()
    cumulative = 0.0
    for idx, e in enumerate(exps):
        cumulative += e / total
        if roll <= cumulative:
            return idx
    return len(exps) - 1


def selection_key(item: dict[str, Any], now: datetime) -> tuple[int, float, float]:
    """A strict ordering for choosing between open intentions.

    `intent_urgency` is a drive *pressure*, and it is deliberately allowed to
    saturate: `due` clips to 1.0 for anything overdue or nearly due, so the
    commitment drive simply knows it is under pressure. That is fine as a number
    and useless as an order, and it was being used as an order — which meant six
    stale seeded intentions ("Opening conversation", "Brief Response") and a
    commission with four minutes left all scored 0.7498, `max` returned whichever
    came first in the list, and the live deadline lost to a greeting that had been
    open since yesterday.

    The band is the part that matters. **A deadline still ahead can be missed;
    one already passed has already been missed.** Working a stale item is merely
    late, and letting it outrank a live one turns an old failure into the cause
    of a new one — an overdue backlog that never clears, because every cycle it
    wins again. So everything still in the future is considered before anything
    overdue.

    Within the live band the nearest deadline wins. Within the overdue band the
    *newest* wins, and that is the opposite of what it looks like it should be:
    an overdue item has already been missed, so picking the most overdue of them
    just means the agent keeps re-picking the same stuck row — the seeded
    backlog never drains, and nothing new is ever started. Draining newest-first
    empties the queue from the front, gives the most recent miss its turn soonest
    while it is still actionable, and always reaches new work. Undated
    intentions sort below both: nothing is waiting on them.
    """
    hours_left = _hours_left(item, now)
    if hours_left is None:
        # Undated: nothing is waiting on it, so it must never be picked before
        # anything that has a date at all.
        return (-1, 0.0, _created_rank(item))
    # `max` takes the largest key, so the band is numbered so that a deadline
    # still ahead outranks one already passed.
    if hours_left >= 0:
        return (1, -hours_left, 0.0)
    return (0, 0.0, _created_rank(item))


def _created_rank(item: dict[str, Any]) -> float:
    """Newest first under `max`, so the most recent work breaks an exact tie."""
    raw = item.get("created_at")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return 0.0


# What each impulse is actually about, and which already-open intention it means
# when there is one to mean. An impulse names a *kind of work over work that
# exists* -- "advance the highest-urgency open commitment", "move the most
# promising venture one step" -- so it is an instruction to pick something out
# of the open intentions, not a title to file under a new one.
#
# These two are the ones whose whole subject is work the agent already owes.
# `tidy`, `reach_out` and `maintenance` deliberately keep no entry: there is no
# open intention they refer to, so they go on to create their own work, which is
# what creating work is for.
IMPULSE_TARGETS: dict[str, str] = {
    "advance_commitment": "commitment",
    "push_venture": "venture",
    # `evolve_project` resolves to a project container rather than an
    # intention: the life loop constructs these entries from
    # `MemoryStore.list_projects` on projects whose cooldown elapsed, with
    # `kind == "project"` and the root intention as the id.
    "evolve_project": "project",
}


def resolve_impulse_target(
    impulse_name: str, inputs: DriveInputs, now: datetime,
) -> dict[str, Any] | None:
    """The open intention this impulse is an instruction to work on, if any.

    `None` means the impulse's subject does not exist as an intention right now,
    and the caller should fall back to opening something new -- which is correct
    for the impulses that are about tidying or reaching out, and is the honest
    response for a commitment drive with nothing committed to it.
    """
    kind = IMPULSE_TARGETS.get(impulse_name)
    if kind is None:
        return None
    candidates = [item for item in inputs.intentions if item.get("kind") == kind]
    if not candidates:
        return None
    # An impulse that says "highest-urgency" has to pick the highest-urgency one,
    # or the instruction and the action disagree and the agent spends its turn on
    # whichever commitment the database happened to list first.
    return max(candidates, key=lambda item: selection_key(item, now))


class Motivation:
    """Seven drives, idle-time impulses, utility arbitration with softmax [D-16, D-17]."""

    def __init__(self, config: EthosConfig, rng: random.Random | None = None):
        self.config = config
        self.rng = rng or random.Random()

    def drive_pressures(self, inputs: DriveInputs, now: datetime | None = None) -> dict[str, float]:
        return pressures(inputs, self.config.rhythm.drives.base, now)

    def arbitrate(
        self,
        inputs: DriveInputs,
        energy: float,
    ) -> tuple[Impulse | None, dict[str, float]]:
        p = self.drive_pressures(inputs)
        impulses = propose_impulses(p, inputs, self.config.rhythm.drives.propose_threshold)
        if not impulses:
            return None, p
        cfg = self.config.rhythm.drives
        utilities = [
            utility(i, p, cfg.weights, energy, cfg.lambda0) for i in impulses
        ]
        if max(utilities) < self.config.rhythm.energy.min_impulse_utility:
            return None, p
        idx = softmax_sample(utilities, cfg.tau, self.rng)
        return impulses[idx], p

    async def choose_focus(
        self,
        inputs: DriveInputs,
        energy: float,
        create_intention_fn=None,
        now: datetime | None = None,
    ) -> tuple[Focus | None, dict[str, float]]:
        now = now or datetime.now(UTC)
        deadline_h = self.config.rhythm.drives.deadline_bypass_h

        # A deadline inside the bypass window outranks the arbitration entirely.
        # It has to name its intention while doing it. This focus used to be
        # returned with a title and a kind and no `intention_id`, and
        # `Life.decide` only acts on a focus that has one -- so the bypass
        # produced a focus the agent could not do anything with, at the exact
        # moment it most needed to do something. `decide` fell through to
        # `kind="none"`, the cycle accomplished nothing, and because the bypass
        # re-fired on the next `choose_focus` it would keep producing that
        # nothing: a focus that could only ever be a way of not working.
        due = [
            item for item in inputs.intentions
            if _hours_left(item, now) is not None and _hours_left(item, now) <= deadline_h
        ]
        if due:
            urgent = max(due, key=lambda item: selection_key(item, now))
            focus = Focus(
                intention_id=_focus_id(urgent),
                title=urgent.get("title") or "deadline commitment",
                kind=urgent.get("kind") or "commitment",
                why="deadline within 24h bypasses arbitration",
            )
            return focus, self.drive_pressures(inputs, now)

        # A commission is a person waiting, and idle curiosity loses to that
        # every time. The softmax weighs the commitment drive fairly, but fair
        # is the problem: a pressure of 0.7 against curiosity's 0.6 loses often
        # enough that an owner's task sat in_progress for an hour while the
        # agent explored a question it asked itself. Commissions take the pass
        # outright -- the same bypass a deadline gets, for the same reason:
        # someone specific is waiting on the result.
        waiting = [
            item for item in inputs.intentions
            if item.get("origin") == "commission"
            and item.get("status") in _OPEN
        ]
        if waiting:
            top = max(waiting, key=lambda item: selection_key(item, now))
            return Focus(
                intention_id=_focus_id(top),
                title=top.get("title") or "commissioned work",
                kind=top.get("kind") or "commitment",
                why="commissioned work outranks idle impulses",
            ), self.drive_pressures(inputs, now)

        impulse, p = self.arbitrate(inputs, energy)
        if impulse is None:
            return None, p

        # An impulse whose subject is work the agent already owes resolves to
        # that work. It used to be taken as the title of a brand new intention,
        # which meant the most popular drive in the system -- `advance_commitment`
        # -- spent its turns on an intention called "advance_commitment" and
        # never on the commitment. The one the agent had actually promised to
        # see through stayed at `proposed` with an empty resume state while the
        # database filled up with placeholders titled after drives.
        target = resolve_impulse_target(impulse.name, inputs, now)
        if target is not None:
            return Focus(
                intention_id=_focus_id(target),
                title=target.get("title") or impulse.name,
                kind=target.get("kind") or impulse.drive,
                why=impulse.description,
            ), p

        if impulse.name in IMPULSE_TARGETS:
            # An impulse whose whole subject is work that already exists has
            # nothing to advance when that work is absent, and minting an
            # intention titled after the drive does not give it a subject --
            # it gives it a fake one. Observed live on 2026-10-25: the real
            # commission had just parked, so for one arbitration the open
            # list held no commitment, the fallback below opened an
            # "advance_commitment" task, and the GSL planned against that
            # self-referential sentence. Nothing in such a plan can be about
            # the missing commitment, so the model produced bookkeeping
            # theatre -- "capture HEAD hash", "create .clio/advance-repo",
            # "append timestamped evidence line" -- each step passing a
            # trivially satisfiable check, leaving the placeholder
            # in_progress forever while the agent told its owner it was
            # picking up the work it had promised. The honest response to
            # "advance a commitment" when there is no commitment is no
            # focus; the drive fires again the moment real work is open.
            return None, p

        focus = Focus(
            title=impulse.name,
            kind="impulse",
            why=impulse.description,
        )
        if create_intention_fn is not None:
            intention_id = await create_intention_fn(
                title=impulse.name,
                desired_end_state=impulse.description,
                kind="habit" if impulse.name == "maintenance" else "task",
                origin="impulse",
                priority=0.4,
            )
            focus.intention_id = intention_id
        return focus, p
