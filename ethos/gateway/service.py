from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Iterable
from typing import Any

import httpx

from ethos import PRODUCT_NAME, __version__
from ethos.config import EthosConfig, base_model_path, load_base_model
from ethos.db import Database
from ethos.gateway.cost import BudgetExceededError, CostCalculator, SpendGuard
from ethos.gateway.embeddings import EmbeddingProvider, build_role_embedder
from ethos.gateway.normalizer import estimate_tokens, was_truncated
from ethos.gateway.providers.anthropic_native import AnthropicNativeProvider
from ethos.gateway.providers.base import Provider, ProviderError
from ethos.gateway.providers.google_native import GoogleNativeProvider
from ethos.gateway.providers.openai_compat import OpenAICompatProvider
from ethos.gateway.providers.openai_native import OpenAINativeProvider
from ethos.gateway.ratelimit import RateLimiter
from ethos.gateway.router import ModelRouter, NoModelAvailable, entry_key
from ethos.observability import metrics
from ethos.observability.logger import get_logger
from ethos.platform import uvicorn_loop
from ethos.schemas.models import (
    ModelCallRecord,
    ModelMessage,
    ModelRequest,
    NormalizedResponse,
    Role,
    Tier,
    Usage,
)

logger = get_logger("ethos.gateway")

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "anthropic": AnthropicNativeProvider,
    "openai": OpenAINativeProvider,
    "google": GoogleNativeProvider,
    "openai_compat": OpenAICompatProvider,
}


class UnusableResponse(RuntimeError):
    """A 200 whose contents no caller could work with.

    Held apart from `ProviderError` because the two are not the same kind of
    event and are not treated the same way. A provider error is a model saying
    no; this is a model saying something that cannot be read. Both are worth
    another attempt, but only one of them is evidence that the model itself is
    unwell, and recording every one of them against its circuit breaker opens
    breakers for requests that were asking for too much in the first place.

    `request_scoped` is the second distinction, and it decides which lever moves
    on the retry: a failure about *what was asked for* is retried with a
    different request, because the identical one is the same bet again.
    Truncation is the case that matters — it is the reply that ran out of room,
    and a reasoning model that filled the room with thinking will fill it again.
    """

    def __init__(self, reason: str, counts_against_model: bool = True,
                 request_scoped: bool = False):
        super().__init__(reason)
        self.counts_against_model = counts_against_model
        self.request_scoped = request_scoped


# Which lever a failure moves. Read off the exception rather than off the retry
# loop's position in the sequence, because the same exception at attempt 4 is
# the same kind of event it was at attempt 0.
_REQUEST_SCOPED = "request"
_MODEL_SCOPED = "model"
_PROVIDER_SCOPED = "provider"


def failure_scope(exc: BaseException) -> str:
    """Whose problem a failed attempt was: the request, the model, or the provider.

    One function, called from exactly one place, so that "which retry policy
    applies to this error" cannot be answered differently by two callers.

    `UnusableResponse` knows its own answer (`request_scoped`, set at the raise
    site where the reason is known). `ProviderError` knows it from the status
    (401 and 403 are about the credential, not the call). Everything else is a
    provider's mood: a 429, a timeout, a 5xx, a connection error, and time is
    the only thing that has ever fixed any of them.
    """
    if isinstance(exc, UnusableResponse):
        return _REQUEST_SCOPED if exc.request_scoped else _PROVIDER_SCOPED
    if isinstance(exc, ProviderError):
        return exc.scope
    return _PROVIDER_SCOPED


def _credential_diagnosis(exc: ProviderError) -> str:
    """What a status means for the credential, in the words of a remedy.

    The four cases a health check most often has to tell apart, because they look
    identical in a config file and need opposite actions: a key that is not a key,
    an account that has run out of money, a model that is not on the account, and
    a provider having a bad moment. The provider's own message is quoted after
    this rather than instead of it, because it carries the specifics -- which
    limit was hit, which model -- that this cannot.
    """
    detail = str(exc).split(": ", 1)[-1][:160]
    if exc.status == 401:
        return f"key rejected or revoked ({detail})"
    if exc.status == 403:
        lowered = detail.lower()
        if "quota" in lowered or "billing" in lowered or "credit" in lowered:
            return f"quota or billing exhausted ({detail})"
        return f"no access to this model with this credential ({detail})"
    if exc.status == 429:
        return f"rate limited or out of quota ({detail})"
    if exc.status >= 500:
        return f"provider error, may be temporary ({detail})"
    if exc.status == 0:
        return f"could not be reached ({detail})"
    return f"the request was rejected ({detail})"


class GatewayService:
    def __init__(
        self,
        config: EthosConfig,
        db: Database | None = None,
        embedder: EmbeddingProvider | None = None,
        http: httpx.AsyncClient | None = None,
        providers: dict[str, Provider] | None = None,
    ):
        self.config = config
        self.db = db
        self.costs = CostCalculator(config)
        self.router = ModelRouter(config, self.costs)
        self.limiter = RateLimiter()
        self.spend = SpendGuard(db, config)
        self.http = http or httpx.AsyncClient()
        self._owns_http = http is None
        self.embedder = embedder or build_role_embedder("gateway", config)
        self.providers: dict[Any, Provider] = providers if providers is not None else {}
        # The catalogue is the shipped file and does not move under a running
        # agent, so it is held apart from the resolved list: `sync_base_model`
        # rebuilds that list on every change, and folding the result back in
        # would accumulate another copy of the base model each time one is
        # written. Pinned rows are dropped rather than assumed absent, because
        # `load_config` has already put one at the front of `models` by now.
        self._catalogue = [entry for entry in config.models.models if not entry.pinned]
        self._base = config.models.base
        self._base_stamp = self._stamp_base_model()

    def _stamp_base_model(self) -> tuple[int, int] | None:
        """
        A cheap "has this file changed" token: its size and mtime, or nothing.

        `sync_base_model` runs on the way into every completion, and a YAML parse
        per model call to discover there is no change is work spent on a path that
        is hot. A stat is not a substitute for reading when the file really has
        changed, so the read still happens then — this only skips the parse when
        the answer is already known to be "no".
        """
        try:
            info = base_model_path(self.config.paths.home).stat()
        except OSError:
            return None
        return (info.st_size, info.st_mtime_ns)

    def sync_base_model(self) -> None:
        """
        Picks up a base model written while the agent was already running.

        The interface can write one at any moment, and a setting that needed a
        restart to take effect would be a setting that cannot be changed from the
        page somebody is looking at when they discover it is wrong. Only the base
        model moves: the catalogue behind it is unchanged, and the provider
        objects are dropped because each one holds the key it was built with.
        """
        stamp = self._stamp_base_model()
        if stamp == self._base_stamp:
            return
        self._base_stamp = stamp
        base = load_base_model(self.config.paths.home)
        if base == self._base:
            return
        self._base = base
        self.config.models.base = base
        self.config.models.models = (
            [base.entry(), *self._catalogue] if base is not None else list(self._catalogue)
        )
        self.providers.clear()

    def _api_key_for(self, entry: Any) -> str:
        """
        The credential for one model: the environment first, then the base model.

        The environment wins, so a deployment can override a key somebody typed
        in without editing what they typed — a variable set before the process
        starts is a more deliberate thing than a field in a file, and the file
        exists for the machine that has no shell set up to hand.
        """
        if entry.api_key_env:
            key = os.environ.get(entry.api_key_env, "")
            if key:
                return key
        base = self._base
        if base is not None and entry.pinned:
            return base.api_key
        return ""

    def _provider_for(self, entry: Any) -> Provider:
        key = (entry.provider, entry.base_url or "")
        if key in self.providers:
            return self.providers[key]
        if entry.provider in self.providers and entry.provider != "openai_compat":
            return self.providers[entry.provider]
        cls = PROVIDER_CLASSES.get(entry.provider)
        if cls is None:
            raise RuntimeError(f"unknown provider {entry.provider}")
        api_key = self._api_key_for(entry)
        default_base = {
            "anthropic": "https://api.anthropic.com",
            "openai": "https://api.openai.com/v1",
            "google": "https://generativelanguage.googleapis.com",
        }.get(entry.provider, entry.base_url or "")
        instance = cls(
            api_key=api_key or None,
            base_url=entry.base_url or default_base,
            quirks=getattr(self.config.quirks, entry.provider),
            http=self.http,
        )
        self.providers[key] = instance
        return instance

    def _is_entry_available(self, entry: Any) -> bool:
        if self.router.breaker_open(entry):
            return False
        try:
            return self._provider_for(entry).available()
        except Exception:
            return False

    def available_providers(self) -> set[str]:
        """
        The providers this process can actually spend money with right now.

        Asked of the provider objects rather than of the environment, so the
        answer cannot drift from what `select` would do: a provider with no key
        is not one a request can be sent to, and reporting it as available is
        the kind of optimistic answer that turns into `NoModelAvailable` further
        up with nothing in between explaining it.
        """
        return {e.provider for e in self.config.models.models if self._is_entry_available(e)}

    def model_status(self) -> dict[str, Any]:
        """What the agent would think with, and whether it could."""
        base = self._base
        # Resolved the way a request would resolve it, rather than by looking at
        # the file: a base model may name the variable to read its key from and
        # hold no key at all, and reporting that as "no key" would say a working
        # install cannot think. The variable is named, the value never is.
        key_present = bool(base and self._api_key_for(base.entry()))
        return {
            "base": {
                "configured": base is not None,
                "provider": base.provider if base else "",
                "model": base.model if base else "",
                "base_url": base.base_url if base else "",
                "key_present": key_present,
                "key_env": base.api_key_env if base else "",
                "tiers": list(base.tiers) if base else [],
            },
            "catalogue": len(self._catalogue),
            "usable": bool(self.available_providers()),
        }

    def _readable(self, response: NormalizedResponse) -> None:
        """
        Raises `UnusableResponse` unless the reply carries something a caller can use.

        A 200 is not an answer. Empty, whitespace-only, and answers the provider
        itself marked as cut off all come back as success codes, and each of them
        reaches a caller as a decision the agent appears to have made when in fact
        nothing arrived: `parse_json_block("")` is `{}`, and `{}` is what every
        prompt in this system asks for. So a caller cannot tell "the model chose
        nothing" from "the reply never landed", and reads the first one.

        Truncation is the one that bites, because it produces a valid-looking
        prefix of a JSON object. It is also the one a second attempt genuinely
        fixes, sometimes because the model got unlucky and sometimes because
        `fail_over` moves the request to a model that fits the answer in the room
        it has.

        A refusal is deliberately left alone. That is the model declining on
        purpose, and asking again spends money to be declined again.

        Reasoning on its own is not an answer either, and used to be mistaken
        for one here: the check below used to pass anything that came back with
        thinking attached, which is right for a tool call and wrong for
        everything else. A reasoning model that thinks for the whole of
        `max_tokens` returns exactly that -- a body of reasoning, no content at
        all, `finish_reason: length` -- so the gate read it as a reply, the
        caller parsed `{}` out of it, and a decision that had been paid for came
        back as a recorded silence. A tool call still needs no text; thinking
        alone does not.
        """
        if response.tool_calls:
            return
        if not (response.text or "").strip():
            if response.thinking_blocks:
                raise UnusableResponse(
                    "the model reasoned without answering", counts_against_model=False,
                    # The room was spent on thinking. More room is the wrong
                    # answer to that, so the request lever moves first: less
                    # thinking, and the answer gets what was left.
                    request_scoped=True,
                )
            raise UnusableResponse("empty response body")
        if was_truncated(response.finish_reason):
            raise UnusableResponse(
                f"response cut off mid-answer ({response.finish_reason})",
                counts_against_model=False,
                request_scoped=True,
            )

    def _delay_before_retry(self, attempt: int, retry_after_s: float | None) -> float:
        """
        Exponential, capped, and jittered.

        The cap is what stops a generous retry budget from turning into minutes
        of a caller held open, which is a different failure from the one being
        retried. The jitter is what stops everything that failed in the same
        window from coming back in the same window -- a backoff curve with no
        randomness in it is a mechanism for synchronising retries rather than
        spreading them.

        A `Retry-After` the provider sent is honoured when it is longer than the
        curve would have waited, because it knows something about its own load
        that no curve here does. It is still capped: a provider asking for two
        minutes is asking a life cycle to stop for two minutes, and moving on to
        another model is the better answer.

        The ceiling is applied last, so `max_backoff_s` is the longest this can
        ever be rather than the longest it is before jitter.
        """
        retry = self.config.gateway.retry
        delay = retry.backoff_s * (2**attempt)
        if retry.jitter > 0:
            delay *= 1.0 + random.random() * retry.jitter
        delay = min(delay, retry.max_backoff_s)
        if retry_after_s is not None:
            delay = max(delay, min(retry_after_s, retry.max_backoff_s))
        return delay

    def _next_entry(
        self, request: ModelRequest, failed: Any, tried: set[str], *, model_scoped: bool,
    ) -> Any | None:
        """
        Where the next attempt goes, or `None` when there is nowhere left to go.

        Re-sending the same request to the model that just failed is worth it
        when the failure is the provider's mood -- a 429, a timeout, a 500 --
        because that clears on its own. It is worth much less when the model is
        failing outright, where five more attempts at it is five more answers
        nobody wants. So once a model has failed, the selection runs again
        without it and the request goes somewhere else that can do the job.

        Staying on the failed model when nothing else is eligible is deliberate,
        and it is *only* deliberate for a failure that might clear. A provider's
        mood is one of those, so a caller that named one model, or a deployment
        with only one installed, is better served by a second attempt than by a
        `NoModelAvailable` raised by the retry machinery rather than by the
        request.

        A credential failure is not one of those, and the difference is the whole
        reason this takes a flag. A dead key returns 401 on the next request
        exactly as it returned it on this one, so staying on it converts a
        failover the catalogue could have made into an outage. So for
        `model_scoped`, staying is not an option: the entry is excluded, and if
        the selection comes back empty the answer is `None` and the caller raises
        `NoModelAvailable` -- which is the truth. "No model here can serve this,
        and not this one" is what happened, and the caller gets told it in the
        exception it already knows how to handle, instead of an HTTP status that
        means nothing outside this function.
        """
        if not self.config.gateway.retry.fail_over:
            return failed if not model_scoped else None
        tried.add(entry_key(failed))
        try:
            return self.router.select(
                request, is_available=self._is_entry_available, exclude=frozenset(tried)
            )
        except NoModelAvailable:
            return None if model_scoped else failed

    def _widen_request(self, request: ModelRequest) -> tuple[ModelRequest, list[str]]:
        """A different request for the next attempt, and what was changed.

        Two levers on the request itself, in this order, and the order is the
        reasoning:

        1. **Think less.** On every endpoint that bills reasoning inside
           `max_tokens`, thinking is spent out of the same ceiling as the answer.
           A reply that stopped at the ceiling with no content is the signature
           of a model that spent all of it thinking, so the first thing to change
           is how much it is allowed to think. Halved rather than removed: some
           of it is what gets to the answer, and a bound of zero is a different
           model, not a smaller version of this one. Once it is down to the floor
           it goes off, which is the second step of the same lever and not a
           separate decision.
        2. **Think it has more room.** Only once thinking is bounded or off, and
           only up to a cap. `max_tokens` is a ceiling and not a reservation, so
           raising it costs nothing when the answer is short -- which is the
           common case, and the reason this can be done without a budget
           argument. But it does bound the damage of a retry loop that would
           otherwise escalate on every attempt.

        `None` in place of a request is returned as the *unchanged* request with
        an empty change list once there is nothing left to change, and the caller
        is expected to notice. That matters because the alternative -- silently
        re-sending the same bytes -- is the behaviour this whole method exists to
        remove, and a caller that cannot see whether the request changed cannot
        assert that it did.
        """
        retry = self.config.gateway.retry
        changes: list[str] = []
        budget = request.reasoning_budget
        thinking = request.thinking_budget
        if budget:
            if budget > retry.reasoning_budget_floor:
                next_budget = max(retry.reasoning_budget_floor, budget // 2)
            else:
                next_budget = None  # off: the floor has been reached
            changes.append(f"reasoning_budget {budget} -> {next_budget}")
            request = request.model_copy(update={"reasoning_budget": next_budget})
        if thinking:
            next_thinking = max(0, thinking // 2)
            changes.append(f"thinking_budget {thinking} -> {next_thinking}")
            request = request.model_copy(update={"thinking_budget": next_thinking})
        if not budget and not thinking:
            # Nothing to bound, so room is the only lever left.
            if request.max_tokens < retry.max_tokens_cap:
                grown = min(
                    retry.max_tokens_cap,
                    max(int(request.max_tokens * retry.escalate_max_tokens_by),
                        request.max_tokens + 1),
                )
                changes.append(f"max_tokens {request.max_tokens} -> {grown}")
                request = request.model_copy(update={"max_tokens": grown})
        return request, changes

    async def _wait_out_a_cooldown(
        self, request: ModelRequest, attempt: int, exc: NoModelAvailable,
    ) -> None:
        """
        Sits out a circuit breaker's cooldown rather than failing the request.

        Every model that could have answered is in its breaker at once, and a
        breaker is a *timed* state: `cooldown_s` and it is over. That was
        reported as `NoModelAvailable` and propagated, because the selection ran
        before the retry loop and raised out of the whole call. So a burst of
        failures -- a flaky uplink, a provider shedding load, a key that briefly
        stopped working -- turned into a window in which every request at that
        tier failed instantly, with no attempt and no backoff, and the caller was
        told no model could answer when one could have thirty seconds later.

        Which is a bad thing to do to a tier with one model in it, because "all
        eligible models" and "the only model" are the same set there. And it is
        the tier consequential messages are deliberated at, so the messages that
        most needed answering were exactly the ones that could not be.

        The backoff curve is the right length on purpose: `max_backoff_s` is
        larger than a cooldown, so the later attempts wait past one. The
        permanent case does not come here at all -- nothing that could serve the
        request exists, and waiting would change nothing -- so the distinction
        the router now makes is doing real work rather than being available.
        """
        delay = self._delay_before_retry(attempt, None)
        logger.warning(
            "gateway.waiting_for_model",
            attempt=attempt + 1,
            of=1 + max(0, self.config.gateway.retry.max_retries),
            purpose=request.purpose,
            tier=request.tier.value,
            reason=str(exc)[:200],
            wait_in_s=round(delay, 2),
        )
        await asyncio.sleep(delay)

    async def _entry_for(self, request: ModelRequest, max_attempts: int) -> Any:
        """
        A model to try, having waited out a cooldown if that is what it took.

        The one case this handles is a request that arrived while every eligible
        model was in its circuit breaker. The selection is otherwise a pure
        function of the request and the catalogue, and it is worth retrying only
        in that case -- and only because a breaker is a *timed* state, so the
        wait is not a hope but an interval with a known end.

        This used to raise on the first selection, before the retry loop, so a
        burst of provider failures turned into a window in which every request at
        that tier failed instantly: no attempt, no backoff, and a caller told no
        model could answer when one could have thirty seconds later. A tier with
        a single model in it -- which is every deployment that configured a base
        model, and therefore every deployment deliberating consequential messages
        at T3 -- has no other model to fail over to, so "all of them" and "the
        only one" are the same set and that window covers everything.

        The permanent case never reaches this: nothing that could serve the
        request exists, so there is no cooldown to sit out and waiting would
        change nothing.
        """
        for attempt in range(max_attempts):
            try:
                return self.router.select(request, is_available=self._is_entry_available)
            except NoModelAvailable as exc:
                if not exc.transient:
                    raise
                if attempt == max_attempts - 1:
                    raise
                await self._wait_out_a_cooldown(request, attempt, exc)
        raise RuntimeError("unreachable: _entry_for exhausted without returning or raising")

    async def probe_models(
        self, *, timeout_s: float = 25.0, max_tokens: int = 1,
    ) -> list[dict[str, Any]]:
        """
        Ask every configured model whether it can actually answer, and report why not.

        `available_providers` and `model_status` answer a different and much
        weaker question: is there a key in the environment, and does an entry
        declare the tiers. That is a claim about configuration, and configuration
        is exactly what a dead key, a revoked token, an exhausted monthly quota
        or a model the account has no access to all look perfectly healthy
        through. So a health check built from those two reported a working
        installation for a deployment that could not produce a single token,
        which is the failure this incident had: everything configured, nothing
        usable, and nothing to tell the difference.

        So this sends one request per model and reports the answer. One token of
        output, no retry, and a timeout, because a health check that costs a
        cent a model and can hang is not one anybody runs.

        The two facts are reported separately and never inferred from one
        another, because they have different fixes: `key_present` is a line in a
        file or an environment variable, and `usable` is whatever the provider
        says about that credential.
        """
        self.sync_base_model()
        base_key = entry_key(self._base.entry()) if self._base is not None else None
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in self.config.models.models:
            key = entry_key(entry)
            if key in seen:
                continue
            seen.add(key)
            report = await self._probe_one(entry, timeout_s=timeout_s,
                                           max_tokens=max_tokens)
            # Marked here rather than by the caller, because "which model is the
            # configured base model" is a fact about the resolved configuration
            # and the only thing that knows it is the service. A health check
            # that had to reach in for it would be reaching into a private field
            # to decide whether an installation is broken.
            report["is_base"] = key == base_key
            results.append(report)
        return results

    async def _probe_one(self, entry: Any, *, timeout_s: float, max_tokens: int) -> dict[str, Any]:
        report: dict[str, Any] = {
            "provider": entry.provider,
            "model": entry.model,
            "tiers": list(entry.tiers),
            "base_url": entry.base_url or "",
            "key_present": False,
            "usable": False,
            "status": "not probed",
            "reason": "",
        }
        report["key_present"] = bool(self._api_key_for(entry))
        if self.router.breaker_open(entry):
            report["status"] = "in cooldown"
            report["reason"] = "its circuit breaker is open; it has been failing"
            return report
        try:
            provider = self._provider_for(entry)
        except Exception as exc:
            report["status"] = "misconfigured"
            report["reason"] = f"{type(exc).__name__}: {exc}"[:200]
            return report
        request = ModelRequest(
            tier=Tier.T1,
            purpose="doctor.probe",
            budget_bucket="discretionary",
            messages=[ModelMessage(role=Role.user, content="hi")],
            max_tokens=max_tokens,
        )
        try:
            response = await asyncio.wait_for(
                provider.complete(request, entry.model), timeout=timeout_s,
            )
        except TimeoutError:
            report["status"] = "timeout"
            report["reason"] = f"no answer within {timeout_s:g}s"
            return report
        except ProviderError as exc:
            # The status is the diagnosis, and each of these has its own remedy,
            # so they are named rather than lumped together as "unavailable".
            report["status"] = f"HTTP {exc.status}"
            report["reason"] = _credential_diagnosis(exc)
            report["usable"] = False
            return report
        except Exception as exc:
            report["status"] = "unreachable"
            report["reason"] = f"{type(exc).__name__}: {exc}"[:200]
            return report
        # A reply that says nothing is still a reply: this call is asking whether
        # the credential works and the provider is willing to serve it, and
        # `max_tokens=1` on a reasoning model is guaranteed to produce nothing.
        # Reading emptiness here would report a working key as a broken one,
        # which is the same class of mistake as the one being fixed.
        report["usable"] = True
        report["status"] = "ok"
        report["reason"] = ""
        _ = response
        return report

    async def complete(self, request: ModelRequest) -> NormalizedResponse:
        if request.tier == Tier.T0:
            raise ValueError("T0 (reflex) cognition must not call the model gateway")
        self.sync_base_model()
        if not self.config.models.models:
            raise RuntimeError("gateway has no models configured")

        await self.spend.check(request.budget_bucket, 0.05)

        retry = self.config.gateway.retry
        max_attempts = 1 + max(0, retry.max_retries)
        tried: set[str] = set()
        entry = await self._entry_for(request, max_attempts)
        last_error: Exception | None = None
        # The request the next attempt will send. Rebound whenever a failure is
        # about the request rather than the provider, so that attempt n+1 is not
        # the same bytes as attempt n.
        attempt_request = request

        for attempt in range(max_attempts):
            # Re-read per attempt rather than once: after a failure the entry can
            # change, and each model has its own price and its own rate limits.
            provider = self._provider_for(entry)
            await self.spend.check(attempt_request.budget_bucket, self.costs.estimate(entry.model))
            await self.limiter.acquire(
                entry.provider, entry.model, entry.rpm, entry.tpm, estimate_tokens(attempt_request)
            )

            start = time.monotonic()
            response: NormalizedResponse | None = None
            try:
                response = await provider.complete(attempt_request, entry.model)
                self._readable(response)
            except BudgetExceededError:
                # Spending more is not a thing a retry fixes, and the spend guard
                # is the last line of the H2 caps rather than a provider's mood.
                raise
            except Exception as exc:
                scope = failure_scope(exc)
                # Recorded before the decision to retry, not after. A key that
                # does not work is not worth retrying but is very much worth
                # remembering: that model is out until somebody fixes the key,
                # and every request that keeps picking it is a request that will
                # keep failing the same way. A credential failure is always
                # recorded, whatever the exception's own opinion of itself --
                # `counts_against_model` was written for replies that were not
                # answers, and a 401 is not that.
                if scope == _MODEL_SCOPED or getattr(exc, "counts_against_model", True):
                    self.router.record_failure(entry, exc)
                await self._note_failure(attempt_request, entry, exc, response)
                last_error = exc
                # "Not retryable" is still fatal, with one exception: a
                # credential failure is not retryable *as a retry*, but it is
                # routable, because the request can be served by a different
                # model. Raising straight through here is what turned a dead key
                # on one candidate into a failure of every request at the tier,
                # including on the deployments that had somewhere else to go.
                if isinstance(exc, ProviderError) and not exc.retryable and scope != _MODEL_SCOPED:
                    raise
                if attempt == max_attempts - 1:
                    raise
                changes: list[str] = []
                if isinstance(exc, UnusableResponse) and exc.request_scoped:
                    # The lever that moves for a failure about the request is the
                    # request. Not for a 400: a wider ceiling does not make an
                    # unsupported parameter supported, and escalating on one
                    # spends the budget to arrive at the same 400.
                    attempt_request, changes = self._widen_request(attempt_request)
                delay = self._delay_before_retry(attempt, getattr(exc, "retry_after_s", None))
                following = self._next_entry(
                    attempt_request, entry, tried, model_scoped=scope == _MODEL_SCOPED,
                )
                if following is None:
                    # Every model that could serve this one has now been excluded,
                    # so there is nothing to wait for and nothing to re-ask. This
                    # is the end of the road rather than another failure of it,
                    # and it is reported as such: a 401 turned into
                    # `NoModelAvailable` is a fact about the deployment, and the
                    # caller can act on that where it could not act on an HTTP
                    # status that meant nothing outside this module.
                    logger.warning(
                        "gateway.no_model_available",
                        purpose=attempt_request.purpose,
                        tier=attempt_request.tier.value,
                        excluded=sorted(tried),
                        cause=str(exc)[:200],
                    )
                    raise NoModelAvailable(
                        f"no eligible model could serve purpose={attempt_request.purpose} "
                        f"tier={attempt_request.tier.value} after excluding "
                        f"{len(tried)} model(s): {exc}"
                    ) from exc
                logger.warning(
                    "gateway.retry",
                    attempt=attempt + 1,
                    of=max_attempts,
                    provider=entry.provider,
                    model=entry.model,
                    purpose=attempt_request.purpose,
                    scope=scope,
                    changed_model=entry_key(following) != entry_key(entry),
                    request_changes=changes,
                    next_provider=following.provider,
                    next_model=following.model,
                    retry_in_s=round(delay, 2),
                    error=str(exc)[:300],
                )
                # Counted here and not at the next call, so a retry that moved no
                # lever at all is visible as its own series rather than inferred
                # from a gap between two successes.
                metrics.observe_retry(
                    scope, attempt_request.purpose,
                    changed_request=bool(changes),
                    changed_model=entry_key(following) != entry_key(entry),
                )
                await asyncio.sleep(delay)
                entry = following
                continue

            latency_ms = int((time.monotonic() - start) * 1000)
            response.provider = entry.provider
            response.model = entry.model
            response.latency_ms = latency_ms
            response.cost_usd = self.costs.compute(entry.model, response.usage)
            self.router.record_success(entry)
            await self._record_success(request, entry, response)
            metrics.observe_model_call(
                entry.provider, entry.model, request.tier.value, "ok",
                response.cost_usd, request.budget_bucket, latency_ms / 1000.0,
                purpose=request.purpose,
            )
            return response

        raise last_error or RuntimeError("gateway complete failed without error")

    async def _note_failure(
        self, request: ModelRequest, entry: Any, exc: Exception,
        response: NormalizedResponse | None = None,
    ) -> None:
        """
        Account for an attempt that did not produce an answer.

        The tokens are still counted when there was a response to count them
        from. An empty or truncated reply cost exactly as much as a good one and
        a row saying it cost nothing is a row the next person debugging this
        cannot trust.

        `scope` goes onto the metric as well as into the row. The row is read
        when somebody is already looking at one failure; the counter is what a
        dashboard reads while the agent is failing, and the whole difference
        between "planning is looping" and "the key is dead" was in a column the
        counter did not have.
        """
        usage = response.usage if response is not None else Usage()
        cost = self.costs.compute(entry.model, usage) if response is not None else 0.0
        metrics.observe_model_call(
            entry.provider, entry.model, request.tier.value, "error", cost,
            request.budget_bucket, 0.0, purpose=request.purpose,
            scope=failure_scope(exc),
        )
        record = ModelCallRecord(
            provider=entry.provider, model=entry.model, tier=request.tier.value,
            purpose=request.purpose, thread_id=request.thread_id,
            intention_id=request.intention_id, budget_bucket=request.budget_bucket,
            input_tokens=usage.input_tokens, cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens, reasoning_tokens=usage.reasoning_tokens,
            cost_usd=cost, latency_ms=0, status="error", error=str(exc)[:500],
        )
        await self.spend.record(record)

    async def _record_success(self, request: ModelRequest, entry: Any, response: NormalizedResponse) -> None:
        record = ModelCallRecord(
            provider=entry.provider, model=entry.model, tier=request.tier.value,
            purpose=request.purpose, thread_id=request.thread_id,
            intention_id=request.intention_id, budget_bucket=request.budget_bucket,
            input_tokens=response.usage.input_tokens,
            cached_input_tokens=response.usage.cached_input_tokens,
            output_tokens=response.usage.output_tokens,
            reasoning_tokens=response.usage.reasoning_tokens,
            cost_usd=response.cost_usd, latency_ms=response.latency_ms, status="ok",
        )
        await self.spend.record(record)

    def embed(self, texts: Iterable[str]) -> list[list[float]]:
        return self.embedder.embed(list(texts))

    async def budget_state(self) -> dict[str, Any]:
        spend = await self.spend.spend()
        return {
            **spend,
            "daily_cap": self.spend.daily_cap,
            "monthly_cap": self.spend.monthly_cap,
            "commitment_cap": self.spend.commitment_cap,
            "discretionary_cap": self.spend.discretionary_cap,
        }

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()


def create_gateway_app(service: GatewayService):
    from fastapi import FastAPI, HTTPException
    from prometheus_client import make_asgi_app

    app = FastAPI(title=f"{PRODUCT_NAME} gateway", version=__version__)

    @app.post("/v1/complete")
    async def complete(request: ModelRequest) -> NormalizedResponse:
        try:
            return await service.complete(request)
        except BudgetExceededError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/embed")
    async def embed(body: dict[str, Any]) -> dict[str, Any]:
        texts = body.get("texts", [])
        vectors = service.embed(texts)
        return {"vectors": vectors, "dim": service.embedder.dim}

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        service.sync_base_model()
        return {
            "status": "ok",
            "providers": sorted(service.available_providers()),
            "models": len(service.config.models.models),
            "base_model": service.model_status()["base"],
        }

    @app.get("/budget")
    async def budget() -> dict[str, Any]:
        return await service.budget_state()

    app.mount("/metrics", make_asgi_app())
    return app


async def serve(config: EthosConfig, db: Database | None = None) -> None:
    import uvicorn

    service = GatewayService(config, db=db)
    app = create_gateway_app(service)
    socket_path = config.gateway.socket_path
    use_socket = os.name == "posix" and os.access(os.path.dirname(socket_path) or "/", os.W_OK)
    # `uvicorn_loop()` rather than the literal "uvloop" in both branches. uvicorn
    # resolves the name with `importlib` inside `Server.serve()`, so a loop that
    # is not installed fails *there*: after the process has bound its address and
    # written its "serving" line, which is the worst place to learn that the string
    # was wrong. `uvloop` is not a Windows wheel at all — `pyproject.toml` marks it
    # `sys_platform != 'win32'` — so on Windows this was an unconditional
    # `ModuleNotFoundError` in the gateway, with the socket already bound.
    #
    # Both branches ask, and the TCP one most of all: that is the branch Windows
    # takes, and the one place a hardcoded loop name would have taken the machine
    # down on the path it was guaranteed to take.
    if use_socket:
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(socket_path), exist_ok=True)
        config_server = uvicorn.Config(
            app, uds=socket_path, log_level="warning", loop=uvicorn_loop(),
        )
    else:
        config_server = uvicorn.Config(
            app, host=config.gateway.host, port=config.gateway.port, log_level="warning",
            loop=uvicorn_loop(),
        )
    server = uvicorn.Server(config_server)
    logger.info("gateway.serving", socket=socket_path if use_socket else f"{config.gateway.host}:{config.gateway.port}")
    await server.serve()
