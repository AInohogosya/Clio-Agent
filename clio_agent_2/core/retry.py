"""
Generic async retry helper for Clio-Agent-2.

Retries a *transient* async operation ``max_attempts`` times: whenever the
operation raises one of ``retryable_exceptions``, the *exact same* call is
re-attempted after a short exponential back-off. The first attempt that returns
without raising wins and its result is returned. If every attempt fails, the
last exception is re-raised.

This is the single mechanism behind the agent's "never give up on a transient
failure -- retry the same thing up to N times" behaviour. It is currently used
by the LLM router (to absorb network blips / timeouts / provider overload) and
by the shell command tool (to absorb transient command timeouts).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Tuple, Type

logger = logging.getLogger(__name__)

# Sensible defaults. The "5 times" requirement maps to ``max_attempts``.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0
DEFAULT_BACKOFF = 2.0


async def retry_async(
    action: Callable[[], Awaitable[Any]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retryable_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    backoff: float = DEFAULT_BACKOFF,
    label: str = "operation",
    deadline: Optional[float] = None,
) -> Any:
    """Run ``action()`` (which returns an awaitable) until it succeeds.

    Args:
        action: Zero-argument callable that returns a *fresh* awaitable on each
            call. It is invoked once per attempt, so it must be safe to call
            repeatedly (e.g. a closure that performs the real call).
        max_attempts: Total number of attempts (first try + retries). Must be
            ``>= 1``. The operation is retried at most ``max_attempts - 1``
            times.
        retryable_exceptions: Exception types that should trigger a retry. Any
            other exception propagates immediately without retrying.
        base_delay: Delay (seconds) before the first retry.
        max_delay: Upper bound (seconds) for the back-off between retries.
        backoff: Multiplier applied to the delay after each failed attempt.
        label: Human-readable name used in log messages.

    Returns:
        The value returned by ``action()`` on its first successful attempt.

    Raises:
        The last exception if all attempts fail, or a non-retryable exception
        immediately if it is raised by any attempt.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    delay = float(base_delay)
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await action()
        except retryable_exceptions as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_attempts:
                logger.error(
                    "%s failed after %d attempts: %s",
                    label, max_attempts, exc,
                )
                break
            # If a deadline was supplied (e.g. the per-message watchdog in an
            # interface), stop retrying once we are out of time budget. This lets
            # a flaky model fail cleanly with the caller's error message instead of
            # being cut off mid-retry by an enclosing ``asyncio.wait_for``.
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(
                    "%s out of time budget (deadline reached); not retrying further",
                    label,
                )
                break
            wait = min(delay, max_delay)
            logger.warning(
                "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                label, attempt, max_attempts, exc, wait,
            )
            await asyncio.sleep(wait)
            delay *= backoff
        # Any non-retryable exception falls through and is re-raised here.

    assert last_exc is not None, "retry_async exited with no exception recorded"
    raise last_exc
