"""
Tests for retry_async additional edge cases.
Covers edge cases not covered in test_retry.py.
"""
import asyncio
import time
import pytest

from clio_agent_2.core.retry import (
    retry_async,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_DELAY,
    DEFAULT_BACKOFF,
)


def _run(coro):
    return asyncio.run(coro)


class TestRetryAsyncDefaults:
    """Tests for retry_async default parameters"""

    def test_default_constants(self):
        assert DEFAULT_MAX_ATTEMPTS == 5
        assert DEFAULT_BASE_DELAY == 1.0
        assert DEFAULT_MAX_DELAY == 30.0
        assert DEFAULT_BACKOFF == 2.0


class TestRetryAsyncBackoff:
    """Tests for retry_async backoff behavior"""

    def test_exponential_backoff_sleeps(self):
        """Retry should sleep with increasing delays"""
        sleep_times = []
        original_sleep = asyncio.sleep

        async def mock_sleep(delay):
            sleep_times.append(delay)
            await original_sleep(0)  # Don't actually sleep

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clio_agent_2.core.retry.asyncio.sleep", mock_sleep)

            call_count = 0

            async def action():
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise ValueError("fail")
                return "ok"

            result = _run(retry_async(
                action,
                max_attempts=5,
                retryable_exceptions=(ValueError,),
                base_delay=0.1,
                max_delay=1.0,
                backoff=2.0,
                label="test",
            ))

            assert result == "ok"
            assert len(sleep_times) == 2  # Two retries
            assert sleep_times[0] == 0.1
            assert sleep_times[1] == 0.2  # Doubled

    def test_max_delay_caps_backoff(self):
        """Backoff should not exceed max_delay"""
        sleep_times = []
        original_sleep = asyncio.sleep

        async def mock_sleep(delay):
            sleep_times.append(delay)
            await original_sleep(0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clio_agent_2.core.retry.asyncio.sleep", mock_sleep)

            call_count = 0

            async def action():
                nonlocal call_count
                call_count += 1
                if call_count < 5:
                    raise ValueError("fail")
                return "ok"

            _run(retry_async(
                action,
                max_attempts=10,
                retryable_exceptions=(ValueError,),
                base_delay=1.0,
                max_delay=2.0,
                backoff=10.0,
                label="test",
            ))

            # All sleeps should be capped at max_delay
            for t in sleep_times:
                assert t <= 2.0


class TestRetryAsyncDeadline:
    """Tests for retry_async deadline behavior"""

    def test_deadline_stops_retries(self):
        """When deadline is in the past, no retries should happen"""
        calls = []

        async def action():
            calls.append(1)
            raise ValueError("fail")

        try:
            _run(retry_async(
                action,
                max_attempts=5,
                retryable_exceptions=(ValueError,),
                base_delay=0.001,
                deadline=time.monotonic() - 1.0,
                label="test",
            ))
            assert False, "Expected ValueError"
        except ValueError:
            pass

        assert len(calls) == 1  # Only the first attempt, no retries

    def test_deadline_future_allows_retries(self):
        """Deadline far in future should allow normal retries"""
        calls = []

        async def action():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("fail")
            return "ok"

        result = _run(retry_async(
            action,
            max_attempts=5,
            retryable_exceptions=(ValueError,),
            base_delay=0.001,
            deadline=time.monotonic() + 60.0,
            label="test",
        ))

        assert result == "ok"
        assert len(calls) == 3


class TestRetryAsyncEdgeCases:
    """Tests for retry_async edge cases"""

    def test_max_attempts_must_be_positive(self):
        try:
            _run(retry_async(
                lambda: asyncio.sleep(0),
                max_attempts=0,
            ))
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "max_attempts must be >= 1" in str(e)

    def test_max_attempts_one_no_retry(self):
        """With max_attempts=1, failures should propagate immediately"""
        calls = []

        async def action():
            calls.append(1)
            raise ValueError("fail")

        try:
            _run(retry_async(
                action,
                max_attempts=1,
                retryable_exceptions=(ValueError,),
                base_delay=0,
                label="test",
            ))
            assert False, "Expected ValueError"
        except ValueError:
            pass

        assert len(calls) == 1

    def test_first_attempt_success_no_sleep(self):
        sleep_times = []

        async def mock_sleep(delay):
            sleep_times.append(delay)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clio_agent_2.core.retry.asyncio.sleep", mock_sleep)

            async def action():
                return "ok"

            result = _run(retry_async(
                action,
                max_attempts=5,
                base_delay=1.0,
                label="test",
            ))

            assert result == "ok"
            assert len(sleep_times) == 0  # No sleeps on success

    def test_multiple_exception_types(self):
        """All listed exception types should trigger retry"""
        calls = []

        async def action():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("first fail")
            if len(calls) == 2:
                raise KeyError("second fail")
            return "ok"

        result = _run(retry_async(
            action,
            max_attempts=5,
            retryable_exceptions=(ValueError, KeyError),
            base_delay=0,
            label="test",
        ))

        assert result == "ok"
        assert len(calls) == 3

    def test_unlisted_exception_no_retry(self):
        calls = []

        async def action():
            calls.append(1)
            raise TypeError("unlisted")

        try:
            _run(retry_async(
                action,
                max_attempts=5,
                retryable_exceptions=(ValueError,),
                base_delay=0,
                label="test",
            ))
        except TypeError:
            pass

        assert len(calls) == 1  # No retry

    def test_action_factory_called_each_attempt(self):
        """The action callable should be called fresh each attempt"""
        calls = []

        def action_factory():
            def make_action():
                async def action():
                    calls.append(1)
                    if len(calls) < 3:
                        raise ValueError("fail")
                    return "ok"
                return action
            return make_action()

        result = _run(retry_async(
            action_factory,
            max_attempts=5,
            retryable_exceptions=(ValueError,),
            base_delay=0,
            label="test",
        ))

        assert result == "ok"
        assert len(calls) == 3