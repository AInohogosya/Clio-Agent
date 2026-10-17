from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from ethos.config import EthosConfig, ModelEntry
from ethos.gateway.cost import CostCalculator
from ethos.schemas.models import ModelRequest, Tier


class NoModelAvailable(RuntimeError):
    """Nothing can serve this request right now -- which is two different things.

    `transient` is the distinction, and it is the whole of it:

    * `False` -- nothing in the catalogue satisfies the request at all. The tier
      is not served by any entry, the quality floor is above every entry, the
      caller pinned a model that does not exist. Waiting changes nothing.
    * `True` -- an entry that *would* serve it is momentarily unable to: its
      circuit breaker is in cooldown, or its provider cannot be reached. `None`
      of the catalogue being eligible is a fact; all of it being in cooldown at
      once is a moment, and it ends on its own.

    These arrived as one exception, so a caller could not tell a deployment that
    is misconfigured from a model that will be back in thirty seconds, and the
    gateway treated both as final. That is the worse of the two to get wrong,
    because the transient one is the common one: one provider, one pinned base
    model, and a few consecutive failures is enough to open its breaker, which
    is a third of a minute during which *every* request at that tier fails
    instantly with no retry at all.
    """

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


# Consecutive failures after which a pinned model stops being asked first.
#
# One failure is a bad moment and the retry ladder is the right answer to a bad
# moment, so the pin survives it. By the third the model is not having a bad
# moment: it is not working, and a pin that keeps handing every request to a
# model that cannot answer is not a preference any more, it is an outage with a
# slower retry ladder in front of it. One success puts the pin straight back, so
# this is a demotion and not a demotion-plus-permanence.
#
# The number is deliberately low. The cost of being wrong in the safe direction
# -- demoting a model that would have worked -- is one request answered by a
# catalogue entry instead of the base model, which is what the pin is a
# preference *within*. The cost of being wrong the other way is every request
# paying six attempts and the whole backoff curve against the same model before
# anything that could answer is asked at all.
PIN_STREAK_LIMIT = 3


def entry_key(entry: ModelEntry) -> str:
    """The identity a failure is remembered under: provider and model.

    A string rather than the entry, because the entries are rebuilt every time
    the base model file changes -- a breaker keyed on object identity would
    quietly forget every failure it had recorded each time somebody saved a
    setting from the interface.
    """
    return f"{entry.provider}:{entry.model}"


class CircuitBreaker:
    """Opens after `max_failures` in `window_s` or error-rate > threshold over `min_calls`."""

    def __init__(self, window_s: float, max_failures: int, min_calls: int, error_rate: float, cooldown_s: float):
        self.window_s = window_s
        self.max_failures = max_failures
        self.min_calls = min_calls
        self.error_rate = error_rate
        self.cooldown_s = cooldown_s
        self._events: deque[tuple[float, bool]] = deque()
        self._opened_at: float | None = None

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def record(self, ok: bool) -> None:
        self._events.append((time.monotonic(), ok))
        self._prune()
        failures = sum(1 for _, good in self._events if not good)
        total = len(self._events)
        if failures >= self.max_failures or (
            total >= self.min_calls and failures / total >= self.error_rate
        ):
            self._opened_at = time.monotonic()

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.cooldown_s:
            self._opened_at = None
            self._events.clear()
            return False
        return True


@dataclass
class _BreakerState:
    breaker: CircuitBreaker = field(default_factory=lambda: CircuitBreaker(60, 5, 20, 0.30, 30))
    failures_consecutive: int = 0


class ModelRouter:
    """m* = argmax over eligible models of [ q_m(class) - mu*c_hat - nu*lat_hat ] [D-21]."""

    def __init__(self, config: EthosConfig, cost_calculator: CostCalculator):
        self.config = config
        self.costs = cost_calculator
        self.breakers: dict[str, _BreakerState] = {
            entry_key(m): _BreakerState(
                breaker=CircuitBreaker(
                    config.gateway.circuit_breaker.window_s,
                    config.gateway.circuit_breaker.max_failures,
                    config.gateway.circuit_breaker.min_calls,
                    config.gateway.circuit_breaker.error_rate,
                    config.gateway.circuit_breaker.cooldown_s,
                )
            )
            for m in config.models.models
        }
        self._providers_available: set[str] = set()

    def mark_provider_available(self, provider: str, available: bool) -> None:
        if available:
            self._providers_available.add(provider)
        else:
            self._providers_available.discard(provider)

    def breaker_open(self, entry: ModelEntry) -> bool:
        state = self.breakers.get(entry_key(entry))
        return state is not None and state.breaker.is_open()

    def record_success(self, entry: ModelEntry) -> None:
        state = self.breakers.setdefault(entry_key(entry), _BreakerState())
        state.breaker.record(True)
        state.failures_consecutive = 0

    def record_failure(self, entry: ModelEntry, exc: Exception | None = None) -> None:
        state = self.breakers.setdefault(entry_key(entry), _BreakerState())
        state.breaker.record(False)
        state.failures_consecutive += 1
        del exc

    def failing_hard(self, entry: ModelEntry) -> bool:
        """Whether this entry is on a losing streak rather than having a bad moment.

        Read by `select` for the pinned entry alone. The circuit breaker is the
        wrong instrument for the job: it is keyed to a *window*, so a model
        failing once every thirty seconds opens it for thirty seconds and closes
        it again, and every request arriving in between is routed to that model
        as if nothing were wrong. A streak is not windowed, so it survives the
        gaps that a model failing steadily produces, and one success clears it.
        """
        state = self.breakers.get(entry_key(entry))
        return state is not None and state.failures_consecutive >= PIN_STREAK_LIMIT

    def quality_floor(self, tier: Tier) -> float:
        floor_cfg = self.config.models.tiers.get(tier.value)
        return floor_cfg.floor if floor_cfg else 0.0

    def score(self, entry: ModelEntry, quality_floor: float) -> float:
        if entry.quality < quality_floor:
            return float("-inf")
        c_hat = self.costs.estimate(entry.model)
        mu = self.config.gateway.routing.mu
        nu = self.config.gateway.routing.nu
        return entry.quality - mu * c_hat - nu * entry.latency_s

    def select(
        self,
        request: ModelRequest,
        is_available=None,
        require_family_different_from: str | None = None,
        exclude: frozenset[str] = frozenset(),
    ) -> ModelEntry:
        tier = request.tier
        floor = self.quality_floor(tier)
        # Split in two so the two reasons the list can come out empty stay
        # distinguishable. Everything above `is_available` is a property of the
        # request and the catalogue and is true for good; `is_available` is
        # whether a model can be used *right now*, which is a circuit breaker in
        # cooldown and clears by itself. Kept as a separate count rather than a
        # flag so a request that is both permanently ineligible and currently
        # unavailable is still reported as the permanent thing it is -- waiting
        # would not help.
        permanent: list[ModelEntry] = []
        candidates: list[ModelEntry] = []
        for entry in self.config.models.models:
            if tier.value not in entry.tiers:
                continue
            if request.provider_hint and entry.provider != request.provider_hint:
                continue
            if request.model_hint and entry.model != request.model_hint:
                continue
            if require_family_different_from and entry.diversity_family == require_family_different_from:
                continue
            if request.tools and not entry.supports_tools:
                continue
            if request.thinking_budget and not entry.supports_thinking:
                continue
            # The quality floor is deliberately *not* applied here. A pinned base
            # model is exempt from it by design -- the floors in `models.yaml` are
            # opinions about the shipped catalogue, and excluding the one model a
            # deployment actually has would send the agent back to a model with no
            # key -- and that exemption is decided below, where `pinned` is
            # honoured, not by quietly filtering on quality in two places.
            #
            # `exclude` is applied last, and not to the pinned rule below: this
            # exists so a retry can leave a model that just failed, and a pin that
            # overrode it would make leaving impossible for exactly the model most
            # worth leaving -- the base model somebody configured, which is the one
            # whose failure is likeliest to be about the provider rather than
            # the request. Everything else still filters it out first.
            if entry_key(entry) in exclude:
                continue
            # Past every filter that is a fact about the request, so this is the
            # point at which "there is a model for this" and "there is a model
            # for this *right now*" come apart.
            permanent.append(entry)
            if is_available is not None and not is_available(entry):
                continue
            candidates.append(entry)
        if not candidates:
            if request.provider_hint or request.model_hint:
                request = request.model_copy(update={"provider_hint": None, "model_hint": None})
                return self.select(
                    request, is_available, require_family_different_from, exclude
                )
            raise NoModelAvailable(
                f"no eligible model for tier={tier.value} floor={floor}",
                # Something could serve this and nothing can serve it *now*.
                transient=bool(permanent),
            )
        # A base model somebody configured is the answer whenever it is eligible.
        # The score exists to choose between models a deployment happens to have
        # keys for; a pin is a decision made on purpose, and letting a catalogue
        # entry out-score it would quietly turn the setting into a suggestion.
        # It is checked after the filters and not before, so a pinned model that
        # cannot do what was asked is still skipped rather than used wrongly.
        #
        # "Eligible" is the word doing the work, and it used to mean only the
        # filters above. A model that answers nothing passes every one of them --
        # it has a key, it declares the tier, its breaker is between cooldowns --
        # and so was asked first, forever, for every purpose including the T3
        # deliberation that writes a person's reply. That is where the silence
        # came from: one base model could not answer, and because the pin outranked
        # the catalogue rather than merely leading it, every reply paid the whole
        # retry ladder before anything that could answer was tried, and a ladder
        # that runs out retires the message as a deliberate silence.
        #
        # So the pin is conditional on the model holding up its end.
        pinned = [entry for entry in candidates if entry.pinned]
        if pinned and not self.failing_hard(pinned[0]):
            return pinned[0]
        # Demoted, so the base model goes to the back of the queue rather than
        # merely losing its shortcut: `BaseModelConfig.quality` defaults to 1.0
        # to mean "this is the model I chose", so scoring a pinned entry alongside
        # the catalogue hands it the top score anyway and the demotion would only
        # have been a rename.
        ranked = [entry for entry in candidates if not entry.pinned]
        if not ranked:
            # Nothing to demote it behind. A deployment with one model in this
            # tier has opted into having nowhere else to go, and a second attempt
            # at the only model is a better answer than no answer.
            return pinned[0]
        scored = sorted(
            ((self.score(e, floor), e) for e in ranked),
            key=lambda pair: pair[0],
            reverse=True,
        )
        best_score, best = scored[0]
        if best_score == float("-inf"):
            # Nothing left clears the floor, and a pinned entry is exempt from it
            # by design -- exemption from that floor is the very thing that made
            # it the base model's way into this tier. So demotion, which is a
            # preference to try something else first, must not be what turns a
            # tier the agent can answer into one it cannot.
            if pinned:
                return pinned[0]
            raise NoModelAvailable(f"quality floor {floor} unsatisfiable for tier {tier.value}")
        return best
