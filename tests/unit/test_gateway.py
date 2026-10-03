from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ethos.config import load_base_model, load_config
from ethos.gateway.client import GatewayClient
from ethos.gateway.cost import BudgetExceededError, CostCalculator
from ethos.gateway.normalizer import estimate_tokens
from ethos.gateway.providers.anthropic_native import AnthropicNativeProvider
from ethos.gateway.providers.base import ProviderError
from ethos.gateway.ratelimit import RateLimiter
from ethos.gateway.router import CircuitBreaker, ModelRouter, NoModelAvailable
from ethos.gateway.service import GatewayService, UnusableResponse
from ethos.schemas.models import NormalizedResponse, ThinkingBlock, Tier, Usage
from tests.conftest import FakeProvider, simple_request


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Nowhere in this file may reach a provider, and this is what enforces it.

    Every test here is about the gateway's decisions, and none of them needs a
    model. That is a property worth holding rather than intending: a test that
    accidentally resolves a real provider -- a catalogue that grew an entry, a
    fixture that stopped narrowing the model list -- does not fail. It sends a
    request, gets an authentication error back, and passes on an assertion about
    the error, which is the worst outcome a test can have.

    `httpx.AsyncHTTPTransport.handle_async_request` is patched rather than
    `AsyncClient.post`, because the transport is the last thing before a socket:
    `MockTransport`, which the two provider-level tests here use, is a different
    class and is untouched, so they keep working and nothing else can connect.
    """

    async def _refuse(self, request, **kwargs):
        raise AssertionError(
            f"this test tried to reach {request.url} -- the gateway's decisions "
            "must be tested with a fake provider"
        )

    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _refuse, raising=True,
    )


@pytest.fixture()
def full_config(tmp_path, monkeypatch):
    # An agent home of its own, so the config under test is the one in this
    # repository. `load_config` reads a base model out of the operator's own home
    # and puts it in front of the whole catalogue — which is right in production
    # and quietly fatal in a unit test: the routing table under test stopped being
    # the routing table, and on a machine with a local model running the three
    # tests below called it over HTTP instead of using the provider they were
    # handed, and failed.
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path / "home"))
    return load_config("config")


def test_router_respects_tier_floor(full_config):
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    request = simple_request(tier=Tier.T3)
    entry = router.select(request, is_available=lambda e: True)
    assert entry.quality >= full_config.models.tiers["T3"].floor


def test_router_never_picks_below_floor(full_config):
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    floor = full_config.models.tiers["T3"].floor
    request = simple_request(tier=Tier.T3, provider_hint="openai_compat")
    entry = router.select(request, is_available=lambda e: True)
    assert entry.quality >= floor
    assert entry.provider != "openai_compat" or entry.quality >= floor


def test_router_argmax_penalizes_cost_and_latency(full_config):
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    request = simple_request(tier=Tier.T2)
    entry = router.select(request, is_available=lambda e: True)
    scored = {
        e.model: e.quality
        - full_config.gateway.routing.mu * costs.estimate(e.model)
        - full_config.gateway.routing.nu * e.latency_s
        for e in full_config.models.models
        if "T2" in e.tiers
    }
    assert entry.model == max(scored, key=lambda m: scored[m])


def test_router_respects_provider_hint(full_config):
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    request = simple_request(tier=Tier.T3, provider_hint="google")
    entry = router.select(request, is_available=lambda e: True)
    assert entry.provider == "google"


def test_router_unavailable_predicates(full_config):
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    request = simple_request(tier=Tier.T3)
    with pytest.raises(NoModelAvailable):
        router.select(request, is_available=lambda e: False)


# ------------------------------------------------------- unavailable, or unservable


def test_an_all_unavailable_catalogue_is_reported_as_a_moment_not_a_verdict(full_config):
    """Two different things, and the difference is the caller's whole decision.

    `is_available` returning false for everything is a circuit breaker in
    cooldown, which ends on its own. The router reported it as `NoModelAvailable`
    with no way to tell it apart from "the catalogue holds nothing that can serve
    this tier", which is a fact about the deployment and does not change. A
    caller that cannot tell them has to pick one behaviour for both, and picking
    the permanent one turns thirty seconds of a provider's bad mood into a failed
    turn.
    """
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    with pytest.raises(NoModelAvailable) as raised:
        router.select(simple_request(tier=Tier.T3), is_available=lambda e: False)
    assert raised.value.transient is True, (
        "every model exists and every model is in cooldown: that is a moment"
    )


def test_a_catalogue_that_cannot_serve_the_tier_is_not_transient(full_config):
    """The other half, which must not be given the same treatment.

    Retrying this is the thing that was wrong: a request for a tier nothing
    serves would sit out the whole backoff curve, six attempts and about a minute,
    to arrive at the same answer every time. And the error has to say which it
    was, so a caller can log the two differently.
    """
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    everything = frozenset(f"{m.provider}:{m.model}" for m in full_config.models.models)
    with pytest.raises(NoModelAvailable) as raised:
        router.select(
            simple_request(tier=Tier.T2), is_available=lambda e: True, exclude=everything,
        )
    assert raised.value.transient is False


def test_a_tier_no_entry_declares_is_not_transient(full_config):
    """The permanent case at the tier itself: nothing here serves T3."""
    for entry in full_config.models.models:
        entry.tiers = ["T1", "T2"]
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    with pytest.raises(NoModelAvailable) as raised:
        router.select(simple_request(tier=Tier.T3), is_available=lambda e: True)
    assert raised.value.transient is False, (
        "waiting cannot make a model appear that does not serve the tier"
    )


def test_a_pinned_model_is_still_exempt_from_the_quality_floor(full_config):
    """The distinction must not have been bought by filtering on quality.

    The floors in `models.yaml` are opinions about the shipped catalogue, and the
    one model a deployment actually has is exempt from them by design: excluding
    it sends the agent back to a model with no key, which is worse than using a
    model below the floor.

    The `transient` split works by counting what survives *without* the
    availability check, so the tempting way to write it is to count without the
    floor as well -- and that would reintroduce the exemption's removal, for a
    pinned model below the floor, in a change whose name has nothing to do with
    it. So the floor is still decided where it always was, and this holds the
    line.
    """
    from ethos.config import BaseModelConfig

    base = BaseModelConfig(
        provider="openai_compat", model="pinned/below-the-floor",
        base_url="https://example.invalid/v1", api_key="k",
        quality=0.10, tiers=["T1", "T2", "T3"],
    )
    entry = base.entry()
    assert entry.pinned and entry.quality < full_config.models.tiers["T3"].floor
    full_config.models.models = [entry]
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)

    chosen = router.select(simple_request(tier=Tier.T3), is_available=lambda e: True)

    assert chosen.model == "pinned/below-the-floor", "the pin still wins over the floor"


async def test_a_catalogue_in_cooldown_is_waited_out_rather_than_failed(full_config):
    """The bug this is about, end to end through the gateway.

    The selection ran *before* the retry loop and raised out of the whole call,
    so a burst of provider failures opened the only model's breaker and turned
    the next thirty seconds into instant failures for every request at that tier:
    no attempt, no backoff, and `NoModelAvailable` to the caller as though no
    model could ever answer.

    With one model configured -- which is what a base model *is* -- "every
    eligible model" and "the only model" are the same set, so nothing was left to
    fail over to. And T3 is the tier consequential messages are deliberated at, so
    the questions that most needed answering were the ones that could not be
    asked. That is the reported symptom: a long, technically loaded message with
    a `$` in it, escalated to T3 by that `$`, failing for a reason that had
    nothing to do with the message.

    The timings are real rather than zeroed, because zero is what makes this pass
    for the wrong reason: with a zero cooldown the breaker is never open by the
    time anything asks, so the fixed path is never entered. The backoff is set
    just past the cooldown, which is the relationship the production settings
    already have -- `max_backoff_s` (30s) is longer than a cooldown -- and is the
    reason the curve outlasts a breaker instead of giving up inside it.
    """
    provider = FakeProvider(responses=[
        NormalizedResponse(text="answered", usage=Usage(input_tokens=10, output_tokens=2)),
    ])
    # Before the service, because `ModelRouter` reads the breaker settings when it
    # builds its breakers. A `max_failures` of one is what a burst of failures
    # looks like from the other side: five in a minute is a third of the window
    # this repository ships with, and a provider shedding load hits it easily.
    full_config.gateway.circuit_breaker.cooldown_s = 0.05
    full_config.gateway.circuit_breaker.max_failures = 1
    full_config.gateway.retry.backoff_s = 0.06
    full_config.gateway.retry.jitter = 0.0
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    entry = service.router.select(
        simple_request(tier=Tier.T3), is_available=lambda e: True,
    )
    service.router.record_failure(entry)

    # The precondition, asserted rather than assumed: if the breaker is not open
    # this test proves nothing about waiting.
    assert service.router.breaker_open(entry), "nothing is in cooldown to wait out"
    assert provider.calls == 0

    response = await service.complete(simple_request(tier=Tier.T3))

    assert response.text == "answered", "the request was waited out, not failed"
    assert provider.calls == 1, "and it went to the model that came back"
    assert response.model == entry.model


async def test_a_tier_nothing_serves_still_fails_without_waiting(full_config):
    """The permanent case must not inherit the wait.

    Six attempts and about a minute of backoff to arrive at the same answer, on
    a request that was never going to be served, is the cost of getting the
    distinction wrong in this direction.
    """
    for entry in full_config.models.models:
        entry.tiers = ["T1", "T2"]
    provider = FakeProvider()
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    with pytest.raises(NoModelAvailable) as raised:
        await service.complete(simple_request(tier=Tier.T3))
    assert raised.value.transient is False
    assert provider.calls == 0, "and it never went near a provider"


def test_circuit_breaker_opens_on_failures():
    breaker = CircuitBreaker(window_s=60, max_failures=5, min_calls=20, error_rate=0.30, cooldown_s=30)
    for _ in range(4):
        breaker.record(False)
    assert not breaker.is_open()
    breaker.record(False)
    assert breaker.is_open()


def test_circuit_breaker_error_rate():
    breaker = CircuitBreaker(window_s=60, max_failures=5, min_calls=20, error_rate=0.30, cooldown_s=30)
    for _ in range(20):
        breaker.record(True)
    for _ in range(6):
        breaker.record(False)
    assert breaker.is_open()


def test_circuit_breaker_cooldown():
    breaker = CircuitBreaker(window_s=60, max_failures=2, min_calls=2, error_rate=0.5, cooldown_s=0.05)
    breaker.record(False)
    breaker.record(False)
    assert breaker.is_open()
    import time

    time.sleep(0.08)
    assert not breaker.is_open()


async def test_rate_limiter_allows_and_throttles():
    limiter = RateLimiter()
    await limiter.acquire("p", "m", rpm=600, tpm=100000, est_tokens=10)
    bucket_small = RateLimiter()
    with pytest.raises(TimeoutError):
        await bucket_small.acquire("p", "m", rpm=1, tpm=1, est_tokens=1000)


def test_cost_calculation_matches_pricing(full_config):
    costs = CostCalculator(full_config)
    usage = Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=1_000_000)
    cost = costs.compute("claude-sonnet-4-5", usage)
    price = full_config.pricing.price_for("claude-sonnet-4-5")
    expected = price.input + price.cached_input + price.output
    assert cost == pytest.approx(expected, rel=1e-6)


def test_cost_uses_defaults_for_unknown_model(full_config):
    costs = CostCalculator(full_config)
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    cost = costs.compute("some-unknown-model", usage)
    expected = full_config.pricing.defaults.input + full_config.pricing.defaults.output
    assert cost == pytest.approx(expected, rel=1e-6)


def test_budget_exception_carries_details():
    exc = BudgetExceededError("daily cap exceeded", bucket="day", spent=19.0, cap=20.0)
    assert exc.bucket == "day"
    assert exc.spent == 19.0
    assert exc.cap == 20.0


def test_estimate_tokens_positive(full_config):
    request = simple_request()
    assert estimate_tokens(request) >= 64


async def test_gateway_service_selects_and_costs(full_config):
    provider = FakeProvider(responses=[
        NormalizedResponse(text="hi", usage=Usage(input_tokens=1000, cached_input_tokens=500, output_tokens=200)),
    ])
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    response = await service.complete(simple_request(tier=Tier.T3))
    assert response.text == "hi"
    assert response.model
    expected_input = 1000 * full_config.pricing.price_for(response.model).input / 1e6
    assert response.cost_usd >= expected_input * 0.9


async def test_gateway_rejects_t0():
    service = GatewayService(load_config("config"), db=None, providers={"anthropic": FakeProvider()})
    with pytest.raises(ValueError):
        await service.complete(simple_request(tier=Tier.T0))


async def test_gateway_retries_retryable_errors(full_config):
    from unittest.mock import MagicMock

    provider = FakeProvider(errors=[
        ProviderError("anthropic", 500, "boom", retryable=True),
        None,
    ])
    provider.responses = [NormalizedResponse(text="recovered", usage=Usage(input_tokens=50, output_tokens=5))]
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    full_config.gateway.retry.backoff_s = 0.01
    response = await service.complete(simple_request(tier=Tier.T3))
    assert response.text == "recovered"
    assert provider.calls == 2
    del MagicMock


async def test_gateway_no_retry_on_hard_errors(full_config):
    """A 400 is about the request, and no amount of asking again changes it.

    It is the other side of the credential case below, and the boundary is worth
    holding from both directions: a request the provider will never accept must
    fail on the first attempt rather than spend the whole retry budget arriving
    at the same rejection, because a wider ceiling does not make an unsupported
    parameter supported.
    """
    provider = FakeProvider(errors=[
        ProviderError("anthropic", 400, "unsupported parameter", retryable=False),
    ])
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    with pytest.raises(ProviderError):
        await service.complete(simple_request(tier=Tier.T3))
    assert provider.calls == 1


async def test_a_rejected_key_fails_over_instead_of_failing_the_request(full_config):
    """The bug, stated as the behaviour it should have had.

    A 401 or a 403 says the *credential* is finished -- a revoked key, a spent
    quota, a model the account is not entitled to -- and it says it identically
    to every future request on that endpoint until somebody changes something.
    So the answer is not "do not retry": it is "do not ask this model again", and
    the request is somebody else's now.

    It used to be raised straight through, which meant a deployment with two
    candidates and one dead key answered nobody -- because the exception left the
    retry loop before the failover had a chance to run. And with a single
    candidate the raise was right by accident: there was nowhere to go.
    """
    dead = FakeProvider(errors=[ProviderError("anthropic", 403, "insufficient_quota",
                                              retryable=False)])
    healthy = FakeProvider(responses=[
        NormalizedResponse(text="answered elsewhere", usage=Usage(input_tokens=10, output_tokens=2)),
    ])
    service = GatewayService(
        full_config, db=None, providers={"anthropic": dead, "openai": healthy},
    )
    _no_waiting(full_config)

    response = await service.complete(simple_request(tier=Tier.T2))

    assert dead.calls == 1, "the model with the dead key is asked once"
    assert healthy.calls == 1, "and the request is served by the model that could"
    assert response.text == "answered elsewhere"


async def test_a_dead_key_with_nowhere_to_go_is_reported_as_such(full_config):
    """Exhausted candidates must arrive as `NoModelAvailable`, not as a 401.

    The caller above this line knows how to handle one exception: it can try a
    different tier, wait, or tell a person the agent cannot think right now. It
    cannot handle an HTTP status number that means nothing outside the gateway,
    and it used to receive exactly that -- a provider exception raised out of the
    retry machinery, for a request that had already used its whole budget.

    `transient=False` is the important half. Every model in the catalogue was
    excluded by a credential failure, and none of that clears by itself, so a
    caller that waits out the backoff for this is waiting for a moment that will
    not come. The opposite mistake -- reporting it as transient -- turns a dead
    key into the same thirty-second window this file already has a test for.
    """
    for entry in full_config.models.models:
        entry.tiers = ["T2"]
    # Narrowed to the provider that actually holds a fake, because "nothing left
    # to try" has to mean nothing left *in this test*: an entry whose provider has
    # no fake would be reachable, and would be sent a real request. The catalogue
    # is not the thing under test -- exhaustion is.
    full_config.models.models = [
        e for e in full_config.models.models if e.provider == "anthropic"
    ]
    reachable = {f"{e.provider}:{e.model}" for e in full_config.models.models}
    budget = full_config.gateway.retry.max_retries + 1
    provider = FakeProvider(errors=[
        ProviderError("anthropic", 401, "invalid api key", retryable=False),
    ] * budget)
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)

    with pytest.raises(NoModelAvailable) as raised:
        await service.complete(simple_request(tier=Tier.T2))

    assert raised.value.transient is False
    assert provider.calls == len(reachable), (
        f"every model with that credential is asked once and then left out; "
        f"{provider.calls} calls for {len(reachable)} models"
    )
    assert provider.calls < budget, "and the retry budget is not spent on it"


async def test_a_rejected_key_remembers_the_model(full_config):
    """Not worth retrying, very much worth remembering.

    A 401 is the one failure where asking the same model again is the problem.
    Rotation handles the request; the breaker is what stops the *next* one from
    picking the same model at all, and until the configured count is reached it
    will -- because a single 403 does not prove a pattern.
    """
    entry = ModelRouter(full_config, CostCalculator(full_config)).select(
        simple_request(tier=Tier.T3), is_available=lambda e: True
    )
    attempts = full_config.gateway.circuit_breaker.max_failures
    provider = FakeProvider(errors=[
        ProviderError("anthropic", 401, "bad key", retryable=False),
    ] * attempts)
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    for _ in range(attempts):
        # With every other candidate excluded by the same credential failure,
        # this is what the caller now gets instead of the status itself.
        with pytest.raises(NoModelAvailable):
            await service.complete(simple_request(tier=Tier.T3))
    assert service.router.breaker_open(entry)


def _no_waiting(config) -> None:
    """Retry as many times as configured, immediately. Tests are about the count."""
    config.gateway.retry.backoff_s = 0.0
    config.gateway.retry.jitter = 0.0


async def test_gateway_retries_past_a_single_retry(full_config):
    """A provider that needs several tries in a row is a provider that answers.

    A budget of one retry is sized for a single bad moment. The complaint this
    answers is about runs of them -- a provider shedding load for a few seconds
    should not end a goal -- so the count is asserted rather than assumed.
    """
    retries = full_config.gateway.retry.max_retries
    assert retries >= 3, "one retry is not a retry budget"
    provider = FakeProvider(
        errors=[ProviderError("anthropic", 503, "overloaded", retryable=True)] * retries
    )
    provider.responses = [NormalizedResponse(text="finally", usage=Usage(input_tokens=10, output_tokens=2))]
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T3))
    assert response.text == "finally"
    assert provider.calls == retries + 1


async def test_gateway_stops_after_the_configured_attempts(full_config):
    retries = full_config.gateway.retry.max_retries
    provider = FakeProvider(
        errors=[ProviderError("anthropic", 500, "boom", retryable=True)] * (retries + 5)
    )
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    with pytest.raises(ProviderError):
        await service.complete(simple_request(tier=Tier.T3))
    assert provider.calls == retries + 1


async def test_gateway_retries_survive_with_exactly_one_model(full_config):
    """The single-model deployment is the one that most needs the retry count.

    With nowhere to fail over to, every retry goes back to the same endpoint, so
    the budget is the whole of the resilience. `fail_over` finding nothing must
    not shorten it.
    """
    retries = full_config.gateway.retry.max_retries
    provider = FakeProvider(
        errors=[ProviderError("anthropic", 500, "boom", retryable=True)] * retries
    )
    provider.responses = [NormalizedResponse(text="ok", usage=Usage(input_tokens=10, output_tokens=2))]
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T3))
    assert response.text == "ok"
    assert provider.calls == retries + 1


async def test_gateway_moves_to_another_model_when_one_fails(full_config):
    """A retry that re-sends to the model that just failed is a retry that fails the same way.

    Once a model has failed, the selection runs again without it, so the request
    lands somewhere else that can do the job instead of on the same broken
    endpoint for the rest of the budget.
    """
    broken = FakeProvider(errors=[ProviderError("anthropic", 500, "boom", retryable=True)])
    healthy = FakeProvider(responses=[
        NormalizedResponse(text="over here", usage=Usage(input_tokens=10, output_tokens=2)),
    ])
    service = GatewayService(
        full_config, db=None, providers={"anthropic": broken, "openai": healthy}
    )
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T2))
    assert broken.calls == 1
    assert healthy.calls == 1
    assert response.text == "over here"
    assert response.provider == "openai"


async def test_gateway_fail_over_can_be_turned_off(full_config):
    broken = FakeProvider(errors=[
        ProviderError("anthropic", 500, "boom", retryable=True),
        None,
    ])
    broken.responses = [NormalizedResponse(text="same model", usage=Usage(input_tokens=10, output_tokens=2))]
    healthy = FakeProvider()
    service = GatewayService(
        full_config, db=None, providers={"anthropic": broken, "openai": healthy}
    )
    full_config.gateway.retry.fail_over = False
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T2))
    assert broken.calls == 2
    assert healthy.calls == 0
    assert response.text == "same model"


async def test_gateway_retries_an_empty_reply(full_config):
    """An empty 200 is not an answer, and it is the shape a failed call takes.

    `parse_json_block("")` is `{}`, which is what every prompt in this system
    asks for, so an empty reply reaches a caller looking exactly like a decision
    the agent made. Retrying it is the difference between the two.
    """
    provider = FakeProvider(responses=[
        NormalizedResponse(text="   ", usage=Usage(input_tokens=10, output_tokens=0)),
        NormalizedResponse(text='{"ok": true}', usage=Usage(input_tokens=10, output_tokens=4)),
    ])
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T3))
    assert provider.calls == 2
    assert response.text == '{"ok": true}'


async def test_gateway_retries_a_reply_cut_off_mid_answer(full_config):
    """The dangerous one: a truncated reply is a valid-looking prefix of an object."""
    provider = FakeProvider(responses=[
        NormalizedResponse(text='{"steps": [{"description": "half a ste',
                    finish_reason="max_tokens", usage=Usage(input_tokens=10, output_tokens=9)),
        NormalizedResponse(text='{"steps": [{"description": "a whole step"}]}',
                    finish_reason="end_turn", usage=Usage(input_tokens=10, output_tokens=9)),
    ])
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T3))
    assert provider.calls == 2
    assert response.finish_reason == "end_turn"


async def test_gateway_reports_an_unusable_reply_when_the_budget_runs_out(full_config):
    retries = full_config.gateway.retry.max_retries
    provider = FakeProvider(responses=[
        NormalizedResponse(text="", usage=Usage(input_tokens=10, output_tokens=0))
    ] * (retries + 1))
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    with pytest.raises(UnusableResponse):
        await service.complete(simple_request(tier=Tier.T3))


async def test_truncation_is_not_the_models_fault(full_config):
    """A ceiling is a property of the request, so it must not trip a breaker.

    Every caller asking for more output than the ceiling allows would otherwise
    fail its way to an open breaker and get routed around a model that was
    working exactly as asked.
    """
    retries = full_config.gateway.retry.max_retries
    provider = FakeProvider(responses=[
        NormalizedResponse(text='{"a": 1', finish_reason="length",
                    usage=Usage(input_tokens=10, output_tokens=9))
    ] * (retries + 1))
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    with pytest.raises(UnusableResponse):
        await service.complete(simple_request(tier=Tier.T3))
    entry = full_config.models.models[0]
    assert not service.router.breaker_open(entry)


async def test_a_truncation_is_retried_with_a_different_request(full_config):
    """The same request again is the same bet again, and it is the outage.

    73% of plan calls in this system were ending on their ceiling rather than on
    a full answer. The retry loop re-sent the identical payload to the same model
    with a longer sleep between attempts, so all the budget bought was evidence
    that the model will think for as long as it is allowed to, which is the one
    thing it was allowed to do. The loop reported a failure, the planner counted
    nothing, and the goal sat there being retried forever.

    So a truncation moves the *request* on the next attempt, and the assertions
    are about the bytes rather than about the count: `max_tokens` rises, because
    the answer ran out of room, and reasoning is bounded first if the caller set
    any, because on the endpoints that bill reasoning inside `max_tokens` that is
    what ate the room. Every attempt differs from the one before it.
    """
    provider = FakeProvider(responses=[
        NormalizedResponse(text='{"steps": [{"description": "half a ste', finish_reason="max_tokens",
                    usage=Usage(input_tokens=10, output_tokens=900))
    ] * (full_config.gateway.retry.max_retries + 1))
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)

    with pytest.raises(UnusableResponse):
        await service.complete(simple_request(tier=Tier.T3, max_tokens=2000))

    sent = provider.requests
    assert len(sent) >= 2, "the truncated reply was retried"
    payloads = [r.model_dump(mode="json") for r in sent]
    for earlier, later in zip(payloads, payloads[1:], strict=False):
        assert earlier != later, (
            f"attempt n+1 was byte-identical to attempt n: {earlier}"
        )
    assert payloads[1]["max_tokens"] > payloads[0]["max_tokens"], (
        "the answer ran out of room, so the room is what changes"
    )


async def test_reasoning_is_bounded_before_the_ceiling_is_raised(full_config):
    """Order matters, and the order is the whole argument.

    On every endpoint that bills reasoning inside `max_tokens`, thinking is spent
    out of the same ceiling as the answer. A reply that ends `length` with no
    content is what a model that thought for the whole budget looks like, so the
    first thing a retry should change is how much it is allowed to think -- not
    how much room it has, which is what raising the ceiling gives a model that is
    about to fill it again with the same reasoning.
    """
    provider = FakeProvider(responses=[
        NormalizedResponse(text=None, finish_reason="length",
                    thinking_blocks=[ThinkingBlock(text="thought for a long time")],
                    usage=Usage(input_tokens=10, output_tokens=900)),
    ] * (full_config.gateway.retry.max_retries + 1))
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)

    with pytest.raises(UnusableResponse, match="reasoned without answering"):
        await service.complete(simple_request(tier=Tier.T3, reasoning_budget=400))

    budgets = [r.reasoning_budget for r in provider.requests]
    assert budgets[:3] == [400, 200, None], (
        f"thinking is halved, and then switched off rather than halved forever: {budgets}"
    )
    tokens = [r.max_tokens for r in provider.requests]
    assert len(set(tokens[:3])) == 1, (
        f"the ceiling must not move while thinking is what is being bounded: {tokens}"
    )
    assert tokens[-1] > tokens[0], (
        "and once thinking is out of the way, the room is the lever that remains"
    )


async def test_a_refusal_is_not_retried(full_config):
    """The model declining on purpose is an answer. Asking again spends to be told no."""
    provider = FakeProvider(responses=[
        NormalizedResponse(text="I won't do that.", finish_reason="refusal",
                    usage=Usage(input_tokens=10, output_tokens=5))
    ])
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T3))
    assert provider.calls == 1
    assert response.text == "I won't do that."


def test_backoff_grows_caps_and_never_synchronises(full_config):
    """Exponential, capped, and not the same number twice in a row.

    Without jitter every request that failed in the same window comes back in the
    same window, which turns a spread of retries into a second synchronized
    outage.
    """
    service = GatewayService(full_config, db=None)
    full_config.gateway.retry.jitter = 0.5
    full_config.gateway.retry.backoff_s = 1.0
    full_config.gateway.retry.max_backoff_s = 10.0

    first = service._delay_before_retry(0, None)
    fourth = service._delay_before_retry(3, None)
    fifth = service._delay_before_retry(4, None)
    assert 1.0 <= first <= 1.5
    assert first <= fourth <= 12.0
    assert fifth == 10.0, "the ceiling is not holding"
    draws = {service._delay_before_retry(0, None) for _ in range(20)}
    assert len(draws) > 1, "every retry is waiting the identical number of seconds"


def test_retry_after_outranks_the_backoff_curve(full_config):
    """The provider knows its own load; the curve does not."""
    service = GatewayService(full_config, db=None)
    full_config.gateway.retry.jitter = 0.0
    full_config.gateway.retry.backoff_s = 1.0
    full_config.gateway.retry.max_backoff_s = 30.0
    assert service._delay_before_retry(0, 9.0) == 9.0
    # A shorter Retry-After does not shorten the wait below the curve.
    assert service._delay_before_retry(4, 0.5) == 1.0 * 2**4
    # Nor is a two-minute request turned into a two-minute life cycle.
    assert service._delay_before_retry(0, 120.0) == 30.0


def test_retry_after_is_read_from_the_header(quirks):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        429, text="slow down", headers={"retry-after": "7"},
    ))
    async def check_retryable() -> ProviderError:
        provider = AnthropicNativeProvider(
            api_key="k", base_url="https://example.test", quirks=quirks,
            http=httpx.AsyncClient(transport=transport),
        )
        try:
            await provider.post_json("https://example.test/v1/messages", {}, {})
        except ProviderError as exc:
            return exc
        raise AssertionError("expected a ProviderError")

    exc = asyncio.run(check_retryable())
    assert exc.retryable is True
    assert exc.retry_after_s == 7.0


def test_a_request_timeout_is_worth_asking_again_about(quirks):
    """408 is a server that gave up waiting, not a statement about the request."""
    transport = httpx.MockTransport(lambda request: httpx.Response(408, text="gone"))
    async def check() -> ProviderError:
        provider = AnthropicNativeProvider(
            api_key="k", base_url="https://example.test", quirks=quirks,
            http=httpx.AsyncClient(transport=transport),
        )
        try:
            await provider.post_json("https://example.test/v1/messages", {}, {})
        except ProviderError as exc:
            return exc
        raise AssertionError("expected a ProviderError")

    exc = asyncio.run(check())
    assert exc.retryable is True
    assert exc.retry_after_s is None


async def test_gateway_honours_retry_after_before_sleeping(full_config, monkeypatch):
    slept: list[float] = []

    async def record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record)
    provider = FakeProvider(errors=[
        ProviderError("anthropic", 429, "slow down", retryable=True, retry_after_s=4.0),
        None,
    ])
    provider.responses = [NormalizedResponse(text="ok", usage=Usage(input_tokens=10, output_tokens=2))]
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    full_config.gateway.retry.jitter = 0.0
    full_config.gateway.retry.backoff_s = 0.1
    response = await service.complete(simple_request(tier=Tier.T3))
    assert response.text == "ok"
    assert slept == [4.0]


def test_router_can_be_asked_to_leave_a_model_out(full_config):
    """The exclusion the retry loop needs, including for the pinned base model.

    A pinned entry overrides the score, so an exclusion that did not reach it
    would make the one model most worth failing over from impossible to leave.
    """
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    request = simple_request(tier=Tier.T2)
    chosen = router.select(request, is_available=lambda e: True)
    assert chosen.provider == "anthropic"
    second = router.select(request, is_available=lambda e: True,
                           exclude=frozenset({f"{chosen.provider}:{chosen.model}"}))
    assert second.provider != chosen.provider


def test_router_exclusion_of_everything_has_no_answer(full_config):
    costs = CostCalculator(full_config)
    router = ModelRouter(full_config, costs)
    request = simple_request(tier=Tier.T2)
    everything = frozenset(f"{m.provider}:{m.model}" for m in full_config.models.models)
    with pytest.raises(NoModelAvailable):
        router.select(request, is_available=lambda e: True, exclude=everything)


def _client(full_config, transport):
    client = GatewayClient.__new__(GatewayClient)
    client.config = full_config
    client._http = httpx.AsyncClient(transport=transport, base_url="http://gateway.local")
    return client


async def test_socket_client_keeps_asking_while_the_gateway_is_absent(full_config):
    """The gateway restarting underneath a caller is a second away from fine.

    Nothing came back, so nothing has been paid for -- this is the one failure
    on this path where asking again is unambiguously the right move.
    """
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("no such file or directory")
        return httpx.Response(200, json={"text": "ok", "usage": {"input_tokens": 1}})

    _no_waiting(full_config)
    client = _client(full_config, httpx.MockTransport(handler))
    response = await client.complete(simple_request(tier=Tier.T2))
    assert response.text == "ok"
    assert attempts == 3


async def test_socket_client_does_not_repay_for_a_gateway_that_answered(full_config):
    """A 502 means the gateway already spent its own retry budget on the model.

    Repeating it here would buy the same answer twice, and nothing in the
    response says whether the failure was the provider or the gateway.
    """
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(502, text="[anthropic] HTTP 500: boom")

    _no_waiting(full_config)
    client = _client(full_config, httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="gateway error 502"):
        await client.complete(simple_request(tier=Tier.T2))
    assert attempts == 1


async def test_spends_are_ignored_without_db(full_config):
    provider = FakeProvider(responses=[
        NormalizedResponse(text="ok", usage=Usage(input_tokens=10, output_tokens=10)),
    ])
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    request = simple_request(tier=Tier.T3)
    response = await service.complete(request)
    assert response.cost_usd >= 0


def test_normalized_response_defaults():
    response = NormalizedResponse()
    assert response.text is None
    assert response.tool_calls == []
    assert response.thinking_blocks == []


async def test_a_reply_of_reasoning_and_nothing_else_is_not_an_answer(full_config):
    """A model that thought for the whole ceiling wrote nothing, and that is not a reply.

    This is the shape behind the reported silence: a reasoning model reached
    through an OpenAI-compatible endpoint spends the entire `max_tokens` budget
    thinking, returns `finish_reason: length` with thousands of characters of
    reasoning and no content at all. The gate used to pass anything carrying
    thinking, because a tool call needs no text -- so this got through, the
    caller parsed `{}` out of it, and a decision that had been paid for arrived
    as silence.
    """
    retries = full_config.gateway.retry.max_retries
    provider = FakeProvider(responses=[
        NormalizedResponse(text=None, finish_reason="length",
                           thinking_blocks=[ThinkingBlock(text="thinking for a long time")],
                           usage=Usage(input_tokens=10, output_tokens=2400))
    ] * (retries + 1))
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    with pytest.raises(UnusableResponse, match="reasoned without answering"):
        await service.complete(simple_request(tier=Tier.T3))
    entry = full_config.models.models[0]
    assert not service.router.breaker_open(entry), \
        "the request asked for more than the ceiling allowed, which is not the model's fault"


async def test_a_model_that_has_no_reasoning_parameter_stops_being_sent_it(full_config):
    """The bound on thinking is worth nothing if it breaks the models without one.

    `reasoning` is OpenRouter's parameter, not part of the OpenAI-compatible
    contract, so an endpoint may refuse it outright -- and OpenRouter refuses it
    per model, from one host that also serves models that do reason. That must
    cost one attempt and nothing else: the gateway already retries, and by the
    time it comes back the provider knows not to ask that model again -- while
    still asking the ones beside it.
    """
    from ethos.config import QuirksConfig
    from ethos.gateway.providers.openai_compat import OpenAICompatProvider

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if "reasoning" in body and body["model"] == "plain-model":
            return httpx.Response(400, json={
                "error": {"message": "Unsupported parameter: 'reasoning' is not supported"
                                        " with this model."},
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAICompatProvider(
            api_key=None, base_url="https://example.invalid/v1",
            quirks=QuirksConfig().openai_compat, http=http,
        )
        request = simple_request(tier=Tier.T2, provider_hint="openai_compat", reasoning_budget=400)

        with pytest.raises(ProviderError):
            await provider.complete(request, "plain-model")
        refused = await provider.complete(request, "plain-model")
        reasoning_model = await provider.complete(request, "thinking-model")

    assert refused.text == '{"ok": true}', \
        "so the request still works; the bound is simply not available for that model"
    assert "reasoning" in sent[0] and "reasoning" not in sent[1], \
        "and the parameter is remembered as unknown for it"
    assert "reasoning" in sent[2], \
        "without being taken away from a model beside it that does reason"
    assert reasoning_model.text == '{"ok": true}'


async def test_an_answer_in_prose_reaches_the_caller_whole(full_config):
    """Words the model wrote for the caller are not an unusable body.

    The gate throws away three things: nothing at all, and a reply the provider
    stopped mid-answer. Neither is this. A model answering a long message
    sometimes answers it in prose instead of in the object it was asked for, and
    those words are the answer -- they are the only reply in the response and
    they were chosen for the person reading them.

    The gateway cannot know whether its caller can use prose, which is the same
    reason it cannot know whether a caller can use a truncated object. What it
    can know is that a 200 carrying words is not a failed call, so it hands them
    on and leaves the shape of them to the caller, which is where the judgement
    about them belongs.
    """
    provider = FakeProvider(responses=[
        NormalizedResponse(text="Leave the current config as-is; nothing needs to change.",
                    finish_reason="stop", usage=Usage(input_tokens=900, output_tokens=60))
    ])
    service = GatewayService(full_config, db=None, providers={"anthropic": provider})
    _no_waiting(full_config)
    response = await service.complete(simple_request(tier=Tier.T3))
    assert provider.calls == 1, "and it is not retried: nothing about it is wrong"
    assert response.text == "Leave the current config as-is; nothing needs to change."


# ------------------------------------------------------- is the key any good?


class _ProbeProvider(FakeProvider):
    """A provider that answers the probe differently from a normal call."""

    def __init__(self, error: Exception | None = None, empty: bool = False,
                 available: bool = True):
        super().__init__(available=available)
        self.error = error
        self.empty = empty

    async def complete(self, request, model):
        self.requests.append(request)
        self.calls += 1
        if self.error is not None:
            raise self.error
        # One token was asked for, so a reply with no words in it is the *expected*
        # shape of a working provider, and is what a reasoning model returns.
        return NormalizedResponse(
            text=None if self.empty else "", finish_reason="length",
            usage=Usage(input_tokens=3, output_tokens=1),
        )


def _one_model_service(config, provider) -> GatewayService:
    """A gateway whose only reachable model is the fake one.

    Two things have to be true at once. The catalogue has to resolve to exactly
    one entry, or the probe walks the rest of it and builds real providers --
    which means real HTTP requests out of a unit test, including one aimed at a
    `localhost` vLLM in the shipped catalogue. And that entry has to be the
    configured *base* model, so the report can say which model the deployment is
    actually answering with.

    Both are arranged here rather than in each test, so no test can forget the
    half that keeps it offline.
    """
    entry = next(e for e in config.models.models if e.provider == "anthropic")
    config.models.models = [entry]
    (config.paths.home).mkdir(parents=True, exist_ok=True)
    (config.paths.home / "base_model.yaml").write_text(
        "provider: anthropic\n"
        f"model: {entry.model}\n"
        "base_url: https://example.invalid\n"
        "api_key_env: ANTHROPIC_API_KEY\n"
        "quality: 0.9\ntiers: [T1, T2, T3]\n",
        encoding="utf-8",
    )
    # Loaded here rather than by `load_config`, which already ran: the file does
    # not exist until this line, so the config still believes it has no base model
    # and the service would never look for one.
    config.models.base = load_base_model(config.paths.home)
    service = GatewayService(config, db=None, providers={"anthropic": provider})
    assert len(service.config.models.models) == 1, (
        "the probe must have exactly one model to reach, or this test is not offline"
    )
    return service


def _base_report(reports):
    """The row for the configured base model, which is the one that decides it.

    Looked up rather than indexed: the probe reports every distinct model, so
    position 0 is whatever the resolved catalogue happens to put first, and a
    test that asserted on position would be asserting on the routing table.
    """
    found = [r for r in reports if r.get("is_base")]
    assert len(found) == 1, f"exactly one base model is expected: {reports}"
    return found[0]


async def test_a_probe_answers_whether_the_key_works_not_whether_one_is_set(
    full_config, monkeypatch,
):
    """The distinction `doctor` could not make before.

    Everything this repository could report about a credential came out of
    configuration: is the variable set, does the entry declare the tiers. All of
    that stays true for a key revoked yesterday, a quota spent this morning, or a
    model the account was never entitled to -- so a health check built from it
    reported a working installation for a deployment that could not produce a
    token. That is the shape of the outage this whole change answers: configured,
    unusable, and nothing to tell the difference.

    So the two facts are reported separately and neither is inferred from the
    other. `key_present` is set here, deliberately, so the test cannot pass by
    accidentally reading "no key" instead of "cannot answer".
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    provider = _ProbeProvider(error=ProviderError(
        "anthropic", 401, "invalid x-api-key", retryable=False,
    ))
    service = _one_model_service(full_config, provider)

    reports = await service.probe_models()
    report = _base_report(reports)

    assert report["key_present"] is True, "a key really is configured"
    assert report["usable"] is False, "and it really does not work"
    assert report["status"] == "HTTP 401"
    assert "key rejected" in report["reason"], report["reason"]
    assert report["is_base"] is True, "and the health check can name which one it is"


async def test_a_spent_quota_is_named_as_a_quota_not_as_a_bad_key(full_config, monkeypatch):
    """The credential failures have different fixes, so they get different words.

    A 403 carrying `insufficient_quota` is a billing problem and a 403 carrying
    `permission denied` is an entitlement problem. Both arrive as "403", both used
    to arrive as "unavailable", and they are answered by different people doing
    different things -- one tops up an account, the other asks for access.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    provider = _ProbeProvider(error=ProviderError(
        "anthropic", 403, "Your credit balance is too low to access the API",
        retryable=False,
    ))
    service = _one_model_service(full_config, provider)

    report = _base_report(await service.probe_models())

    assert report["usable"] is False
    assert "quota" in report["reason"], report["reason"]


async def test_a_reply_with_no_words_in_it_is_still_a_working_key(full_config, monkeypatch):
    """The probe must not repeat the mistake it exists to catch, on the other side.

    `max_tokens=1` means a reasoning model answers with `finish_reason: length`
    and no content, every time. Reading that as an unusable model would report a
    perfect key as broken -- the same class of error as reading a dead key as
    present, and just as wrong. The probe asks whether the credential works and
    the provider is willing to serve it, and a 200 is that answer whatever is in
    the body.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    provider = _ProbeProvider(empty=True)
    service = _one_model_service(full_config, provider)

    reports = await service.probe_models()
    report = _base_report(reports)

    assert report["usable"] is True, report
    assert report["status"] == "ok"
    assert provider.calls == len(reports), (
        "and it costs exactly one request per distinct model, however many are "
        f"configured: {provider.calls} calls for {len(reports)} models"
    )


async def test_the_probe_does_not_spend_the_retry_budget(full_config, monkeypatch):
    """One request per model, no matter what.

    A health check that ran the whole retry policy would spend six requests per
    model on a machine that is already broken, and take a minute per model to say
    what one line could say. The retry budget belongs to a call somebody asked
    for; the probe answers a different question.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    provider = _ProbeProvider(error=ProviderError(
        "anthropic", 500, "boom", retryable=True,
    ))
    service = _one_model_service(full_config, provider)

    reports = await service.probe_models()

    assert provider.calls == len(reports), "one attempt per model, not one budget each"
    report = _base_report(reports)
    assert report["usable"] is False
    assert "temporary" in report["reason"], report["reason"]


async def test_a_model_in_cooldown_is_reported_without_being_asked(full_config, monkeypatch):
    """A breaker is a known answer, and re-asking is how a known answer gets worse.

    The one case where the probe has nothing left to find out, because the gateway
    has already been told. Reporting it is still worth it -- "in cooldown" and
    "unusable" are different diagnoses with different fixes -- but it costs
    nothing to say, because it is read rather than measured.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    provider = _ProbeProvider()
    service = _one_model_service(full_config, provider)
    # The configured base model and the catalogue entry are the same model, so the
    # breaker key is the same: one failure against it is one against both.
    only = service.config.models.models[0]
    for _ in range(service.config.gateway.circuit_breaker.max_failures):
        service.router.record_failure(only)

    reports = await service.probe_models()
    report = _base_report(reports)

    assert report["status"] == "in cooldown"
    assert provider.calls == len(reports) - 1, "and it did not spend a request to be told so"
