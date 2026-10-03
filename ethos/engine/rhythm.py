from __future__ import annotations

import asyncio
import math
import random
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as dt_time

from ethos.config import EthosConfig, clock_timezone
from ethos.engine.states import State
from ethos.observability.logger import get_logger

logger = get_logger("ethos.rhythm")


def backoff_seconds(attempt: int, *, base_s: float, max_s: float, jitter: float = 0.25) -> float:
    """How long to wait before trying again, given how many tries have already failed.

    Exponential, capped, and jittered -- the same three properties the gateway's
    retry curve has, and for the same reasons: a wait that grows is what stops a
    failing thing from being retried at the tick rate forever, the cap is what
    keeps a generous attempt budget from becoming an outage of its own, and the
    jitter is what stops everything that failed in the same window from coming
    back together in the same window.

    A pure function of its arguments, and in particular not a sleep: the solver
    loop computes a wait from this and ends its cycle, because sleeping here
    would hold the life loop open for as long as the wait rather than letting it
    come round again afterwards. That is why it is on this class next to the
    other pacing decisions rather than beside the caller that needs it -- one
    place decides how long, and the caller decides whether to wait now or later.
    """
    if base_s <= 0 or attempt < 0:
        return 0.0
    delay = base_s * (2 ** min(attempt, 32))
    if jitter > 0:
        delay *= 1.0 + random.random() * jitter
    return min(delay, max(0.0, max_s))


def parse_hhmm(value: str) -> dt_time:
    parts = value.strip().split(":")
    return dt_time(int(parts[0]), int(parts[1]))


class RhythmController:
    """Energy, daily rhythm, rest, focus sessions, mind-wandering, tick pacing [D-04, D-14].

    There is no sleep schedule here, and that is a decision rather than an
    omission. A window in which the agent stopped deliberating was a window in
    which anything due inside it simply did not happen until the window closed —
    a job at four in the morning, a promise made at midnight, an interruption
    that could not reach anybody. Removing the window meant removing what
    supported it too, and this class is the smaller afterwards: uptime no longer
    drains energy toward a floor it can never leave, so the cycle that answers a
    four-in-the-morning message is the same cycle as the one that answers a noon
    message.
    """

    def __init__(
        self,
        config: EthosConfig,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.config = config
        self.wall_clock = wall_clock
        self.rest_minutes_accumulated = 0.0
        self.focus_session_started: datetime | None = None
        self.last_mind_wander: datetime | None = None
        self.next_mind_wander_gap_min = self._draw_wander_gap_min()
        self.last_reverie: datetime | None = None
        self.next_reverie_gap_min = self._draw_reverie_gap_min()

    def _draw_wander_gap_min(self) -> float:
        cfg = self.config.rhythm.mind_wandering
        return random.uniform(cfg.min_gap_min, cfg.max_gap_min)

    def _draw_reverie_gap_min(self) -> float:
        cfg = self.config.rhythm.reverie
        return random.uniform(cfg.min_gap_min, cfg.max_gap_min)

    def mark_awake(self) -> None:
        self.focus_session_started = self.focus_session_started or self.wall_clock()

    def local_time(self, now: datetime | None = None) -> datetime:
        """The wall clock as the owner lives it, for anything a person wrote as a time of day."""
        return (now or self.wall_clock()).astimezone(
            clock_timezone(self.config.timezone),
        )

    def circadian(self, now: datetime | None = None) -> float:
        """How awake the hour is: full at `peak_hour`, `night_floor` opposite it.

        A smooth daily wave, not a window. The old term counted hours since
        waking, which is a countdown to a bed the agent no longer goes to, and an
        agent that has been up since morning reads as exhausted at lunchtime.
        This one is periodic, so the same clock an hour tomorrow is worth what it
        is worth now, and `night_floor` keeps the trough above `low_energy` — the
        agent gets less spontaneous at four in the morning, and it still thinks.
        """
        cfg = self.config.rhythm.energy
        clock = self.local_time(now)
        hour = clock.hour + clock.minute / 60.0
        phase = math.cos(2 * math.pi * (hour - cfg.peak_hour) / 24.0)
        floor = max(0.0, min(cfg.night_floor, cfg.cap))
        return floor + (cfg.cap - floor) * (1.0 + phase) / 2.0

    def energy(self, spent_discretionary_usd: float, rest_minutes: float = 0.0, now: datetime | None = None) -> float:
        """E = min(1 - s_d/B_d, circadian) + rho_rest, clipped to [0, cap]."""
        cfg = self.config.rhythm.energy
        budget = self.config.permissions.hard_limits.H2_daily_spend_usd * (1 - self.config.gateway.budget.commitment_reserve_pct)
        first = 1.0 - (spent_discretionary_usd / budget if budget > 0 else 0.0)
        second = self.circadian(now)
        rest_blocks = int((self.rest_minutes_accumulated + rest_minutes) // 30)
        rho = cfg.rest_boost_per_30min * rest_blocks
        e = min(first, second) + rho
        return max(0.0, min(cfg.cap, e))

    def should_rest(self, energy: float, max_utility: float) -> bool:
        cfg = self.config.rhythm.energy
        return energy < cfg.low_energy or max_utility < cfg.min_impulse_utility

    def add_rest(self, minutes: float) -> None:
        self.rest_minutes_accumulated += minutes

    def focus_session_expired(self, now: datetime | None = None) -> bool:
        if self.focus_session_started is None:
            return False
        now = now or self.wall_clock()
        minutes = (now - self.focus_session_started).total_seconds() / 60.0
        return minutes >= self.config.rhythm.focus.max_session_min

    def take_micro_break(self) -> float:
        self.focus_session_started = self.wall_clock()
        return self.config.rhythm.focus.micro_break_min

    def mind_wander_due(self) -> bool:
        now = self.wall_clock()
        if self.last_mind_wander is None:
            self.last_mind_wander = now
            self.next_mind_wander_gap_min = self._draw_wander_gap_min()
            return False
        minutes = (now - self.last_mind_wander).total_seconds() / 60.0
        if minutes >= self.next_mind_wander_gap_min:
            self.last_mind_wander = now
            self.next_mind_wander_gap_min = self._draw_wander_gap_min()
            return True
        return False

    def reverie_due(self) -> bool:
        """Whether an idle mind is due to pick something up on its own.

        The same shape as `mind_wander_due`, and for the same reason: the gap is
        drawn rather than fixed, because the interval at which a person is
        inclined to turn their attention to something is not a constant and a
        fixed one would read as a metronome. The first call after a process
        starts answers False on purpose — a freshly woken agent gets its idle
        thoughts on the same clock as any other, not as a startup flourish.
        """
        if not self.config.rhythm.reverie.enabled:
            return False
        now = self.wall_clock()
        if self.last_reverie is None:
            self.last_reverie = now
            self.next_reverie_gap_min = self._draw_reverie_gap_min()
            return False
        minutes = (now - self.last_reverie).total_seconds() / 60.0
        if minutes >= self.next_reverie_gap_min:
            self.last_reverie = now
            self.next_reverie_gap_min = self._draw_reverie_gap_min()
            return True
        return False

    async def after_cycle(self, outcome: dict | None = None) -> None:
        self.mark_awake()
        if self.focus_session_expired():
            self.take_micro_break()
            logger.info("rhythm.micro_break")

    def backoff_seconds(self, attempt: int, *, base_s: float, max_s: float,
                        jitter: float = 0.25) -> float:
        """The wait after `attempt` consecutive failures, from the shared curve."""
        return backoff_seconds(attempt, base_s=base_s, max_s=max_s, jitter=jitter)

    def tick_seconds(self, state: State) -> float:
        base = self.config.rhythm.ticks.get(state.value, 4.0)
        jitter = base * 0.15
        return max(0.2, base + random.uniform(-jitter, jitter))

    async def wait_until_next(self, state: State) -> None:
        await asyncio.sleep(self.tick_seconds(state))

    def due_daily(self, at: str, done: str | None, now: datetime | None = None) -> bool:
        """Whether a once-a-day job keyed to a time of day is due.

        `done` is whatever the caller wrote down the last time it ran — a date
        string here, so a restart inside the same day does not run it twice, and
        a night that never stops the agent does not defer it to the next
        evening. The time is read in the zone the owner configured, because that
        is the zone somebody wrote "04:00" in.
        """
        local = self.local_time(now)
        today = local.date().isoformat()
        if done == today:
            return False
        return local.time() >= parse_hhmm(at)
