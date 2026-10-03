from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from ethos.config import ProviderQuirks
from ethos.schemas.models import ModelRequest, NormalizedResponse
from ethos.schemas.tools import ToolSpec


class ProviderError(RuntimeError):
    def __init__(
        self,
        provider: str,
        status: int,
        message: str,
        retryable: bool,
        retry_after_s: float | None = None,
    ):
        super().__init__(f"[{provider}] HTTP {status}: {message}")
        self.provider = provider
        self.status = status
        self.retryable = retryable
        # What the provider asked for, in seconds, when it said. A 429 that
        # carries this is the provider saying exactly how long it needs and it
        # knows better than any backoff curve here, so it is carried up to the
        # retry loop rather than thrown away and reinvented.
        self.retry_after_s = retry_after_s

    @property
    def scope(self) -> str:
        """Whose problem this is, which decides which lever moves.

        The whole retry policy hangs off this one answer, and getting it wrong is
        what the outage this replaced was made of:

        * `model` -- the *credential or the endpoint instance* is at fault, and
          the request is not. A dead key, a revoked token, a quota spent for the
          month. Asking the same request again returns the same status, and
          sending it to the same endpoint returns the same status, so the only
          thing that helps is a different candidate model. This is what 401 and
          403 mean, and neither was being read this way: both are 4xx, both were
          classified "not worth retrying", and the retry loop raised straight
          through. A single-key deployment then returned that 401 to every
          request at that tier for as long as the key stayed dead -- which is
          correct -- while a deployment with a second candidate that would have
          answered never tried it, because the exception left the loop before
          `_next_entry` was ever reached.
        * `request` -- the request is at fault: an oversized body, an unsupported
          parameter, a prompt the endpoint will not take. Different wording
          fixes it; the same words do not.
        * `provider` -- the provider's mood: a 429, a timeout, a 5xx. It clears on
          its own, and time is the fix.
        """
        if self.status in _MODEL_SCOPED_STATUSES:
            return "model"
        if self.status in _REQUEST_SCOPED_STATUSES:
            return "request"
        return "provider"


# 401 and 403, and only these two.
#
# They are the statuses that say something about the credential rather than about
# the call: `invalid api key`, `authentication_error`, `permission denied`,
# `insufficient_quota`, `billing hard limit reached`. Every one of them is true
# of the next request exactly as it is of this one, on this endpoint, with this
# key, until a person changes something. They are also the two that took an
# agent down, because "a 4xx is not worth retrying" was true and insufficient:
# it said the retry budget was the wrong lever without noticing that the *model*
# was.
#
# 404 is deliberately not here. It usually means the model name is wrong rather
# than that the credential is, and a deployment with one base model misconfigured
# should be told its model does not exist rather than quietly routed around it.
_MODEL_SCOPED_STATUSES = frozenset({401, 403})

# 4xx that describe the request and will still describe it in five minutes. The
# bound is worth having on its own: a retry that only escalates `max_tokens` at
# a 400 spends money and never gets closer to the answer.
_REQUEST_SCOPED_STATUSES = frozenset({400, 413, 422})


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """`Retry-After` as seconds, or None if absent or not a number we can use.

    The header is defined as either a delay or an HTTP date. Only the delay is
    read: a date means "retry after some wall-clock moment", which by the time
    this runs is either already past or belongs to a clock that is not ours, and
    guessing at it produces a delay that is wrong in both directions.
    """
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


class Provider(ABC):
    name: str = "abstract"

    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        quirks: ProviderQuirks,
        http: httpx.AsyncClient,
        timeout_s: float = 120.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.quirks = quirks
        self.http = http
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return True

    @abstractmethod
    async def complete(self, request: ModelRequest, model: str) -> NormalizedResponse: ...

    async def post_json(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.http.post(url, headers=headers, json=payload, timeout=self.timeout_s)
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, 0, f"timeout: {exc}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, 0, f"transport: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise ProviderError(
                self.name,
                response.status_code,
                response.text[:2000],
                retryable=_is_retryable_status(response.status_code),
                retry_after_s=_retry_after_seconds(response),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(self.name, response.status_code, "invalid JSON body", retryable=False) from exc


# The statuses worth asking again about. 429 and 5xx are the obvious ones, and
# 408 is a server giving up on waiting for us rather than a statement about the
# request -- the same call a second later is fine. Everything else in the 4xx
# range is the provider describing something about the request that will still
# be true in five minutes, so asking again spends money to be told the same
# thing; a wrong key or an oversized prompt does not fix itself.
#
# Read `ProviderError.scope` before this: 401 and 403 are also "not retryable" in
# the sense that the same request against the same credential will fail the same
# way, and that is a fact about the *model*, not about this call. `retryable`
# answers "would this exact request work later?"; `scope` answers "who has to
# change for it to work?".
_RETRYABLE_STATUSES = frozenset({408, 429})


def _is_retryable_status(status: int) -> bool:
    return status in _RETRYABLE_STATUSES or status >= 500


def tool_name_map(tools: list[ToolSpec]) -> dict[str, str]:
    from ethos.gateway.normalizer import build_name_map

    return build_name_map(tools)
