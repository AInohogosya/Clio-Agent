"""
Regression tests for the Telegram "Timed out" fix.

telegram.error.TimedOut surfaces with the literal message "Timed out". Historically
that exception was never imported or handled in the Telegram interface, so it fell
through to the generic `except Exception` and was shown to the user as
"⚠️ Error: Timed out". These tests lock in the new behaviour: transient
TimedOut / NetworkError on a send are retried, and only the underlying
exception is raised after the retries are exhausted.

They also lock in the deadline-aware retry in ``retry_async`` (used by the LLM
router) so a flaky model fails cleanly instead of being cut off mid-retry by the
per-message watchdog.
"""

import asyncio
import time

import pytest
from telegram.error import NetworkError, TimedOut

from clio_agent_2.core.retry import retry_async
from clio_agent_2.interfaces.telegram import _retry_bot_request


def _run(coro):
    """Run an async coroutine to completion without pytest-asyncio."""
    return asyncio.run(coro)


def test_retry_succeeds_after_transient_timedout():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimedOut("Timed out")
        return "ok"

    result = _run(
        _retry_bot_request(lambda: flaky(), max_retries=3, base_delay=0.001)
    )
    assert result == "ok"
    assert calls["n"] == 2


def test_retry_succeeds_after_network_error():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise NetworkError("network down")
        return "ok"

    result = _run(
        _retry_bot_request(lambda: flaky(), max_retries=3, base_delay=0.001)
    )
    assert result == "ok"
    assert calls["n"] == 2


def test_retry_raises_after_exhausting_attempts():
    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise TimedOut("Timed out")

    with pytest.raises(TimedOut):
        _run(
            _retry_bot_request(lambda: always_fail(), max_retries=3, base_delay=0.001)
        )
    # 1 initial attempt + 2 retries = 3 total.
    assert calls["n"] == 3


def test_retry_async_stops_at_deadline():
    """retry_async must stop retrying once a (past) deadline is reached."""
    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise TimedOut("Timed out")

    with pytest.raises(TimedOut):
        _run(
            retry_async(
                lambda: always_fail(),
                max_attempts=5,
                retryable_exceptions=(TimedOut,),
                base_delay=0.001,
                deadline=time.monotonic() - 1.0,  # already in the past
                label="test",
            )
        )
    # Must NOT keep retrying just because the deadline has passed.
    assert calls["n"] == 1


def test_retry_async_honours_deadline_before_max_attempts():
    """With a near-future deadline, retries stop before max_attempts are used."""
    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise TimedOut("Timed out")

    with pytest.raises(TimedOut):
        _run(
            retry_async(
                lambda: always_fail(),
                max_attempts=5,
                retryable_exceptions=(TimedOut,),
                base_delay=0.05,
                deadline=time.monotonic() + 0.06,  # ~1 sleep worth of budget
                label="test",
            )
        )
    assert calls["n"] < 5

