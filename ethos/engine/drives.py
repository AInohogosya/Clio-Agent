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

logger = get_logger("ethos.drives")

DRIVES = ("commitment", "curiosity", "mastery", "creation", "order", "social", "homeostasis")


def clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class DriveInputs(BaseModel):
    intentions: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: int = 0
    failed_recent: int = 0
    active_ventures: int = 0
    value_recent: float = 0.0
    unread_events: int = 0
    last_contact_age_h: float | None = None
    energy: float = 0.8
    disk_free_ratio: float = 0.8
    budget_ratio: float = 1.0
    maintenance_due_h: float = 0.0

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
        return priority * 0.4
    if hours_left < 0:
        due = 1.0
    else:
        due = clip(1.0 - hours_left / (7 * 24.0))
    return clip(priority * 0.5 + due * 0.5)


def commitment_gain(inputs: DriveInputs, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    return max(
        (intent_urgency(item, now) for item in inputs.intentions),
        default=0.0,
    )


def curiosity_gain(inputs: DriveInputs) -> float:
    return clip(inputs.open_questions / 10.0) * 0.6


def mastery_gain(inputs: DriveInputs) -> float:
    return clip(inputs.failed_recent / 5.0) * 0.7


def creation_gain(inputs: DriveInputs) -> float:
    return clip(0.2 + 0.3 * clip(inputs.active_ventures / 3.0) + 0.3 * clip(inputs.value_recent / 10.0))


def order_gain(inputs: DriveInputs) -> float:
    return clip(inputs.unread_events / 50.0)


def social_gain(inputs: DriveInputs) -> float:
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
    impulses: list[Impulse] = []
    if p["commitment"] > threshold:
        impulses.append(Impulse(
            name="advance_commitment", drive="commitment", relevance=0.9, value_hat=0.8, cost_hat=0.15,
            description="Advance the highest-urgency open commitment by one concrete step.",
        ))
    if p["curiosity"] > threshold:
        impulses.append(Impulse(
            name="explore_question", drive="curiosity", relevance=0.95, value_hat=0.6, cost_hat=0.10,
            description="Take the most interesting open question and dig for 20 focused minutes.",
        ))
    if p["mastery"] > threshold:
        impulses.append(Impulse(
            name="deliberate_practice", drive="mastery", relevance=0.9, value_hat=0.7, cost_hat=0.15,
            description="Retry the most recent hard failure with a deliberately different strategy.",
        ))
    if p["creation"] > threshold:
        impulses.append(Impulse(
            name="push_venture", drive="creation", relevance=0.9, value_hat=0.75, cost_hat=0.20,
            description="Move the most promising venture intention one step toward real-world value.",
        ))
    if p["order"] > threshold:
        impulses.append(Impulse(
            name="tidy", drive="order", relevance=0.9, value_hat=0.45, cost_hat=0.03,
            description="Triage the backlog: classify unread events, close stale loops, tidy notes.",
        ))
    if p["social"] > threshold:
        impulses.append(Impulse(
            name="reach_out", drive="social", relevance=0.85, value_hat=0.5, cost_hat=0.05,
            description="Send something genuinely useful to a person I have not talked to recently.",
        ))
    if p["homeostasis"] > threshold:
        impulses.append(Impulse(
            name="maintenance", drive="homeostasis", relevance=0.9, value_hat=0.5, cost_hat=0.02,
            description="Environment maintenance: verify health, prune trash, check disk and schedules.",
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
