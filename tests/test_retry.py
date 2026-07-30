"""
Tests for the "retry the same thing up to N times" policy.

Covers the generic ``retry_async`` helper and its two production users:
``LLMRouter.chat`` (retries transient LLM failures) and
``ShellCommandTool.run_command`` (retries transient command timeouts).
"""
import asyncio

from clio_agent_2.core.llm_router import LLMRouter
from clio_agent_2.core.retry import retry_async
from clio_agent_2.tools.tool_registry import ShellCommandTool


def _run(coro):
    """Run an async coroutine to completion without pytest-asyncio."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# retry_async unit behaviour
# --------------------------------------------------------------------------- #
def test_retries_then_succeeds():
    state = {"n": 0}

    def make_action():
        async def action():
            state["n"] += 1
            if state["n"] < 3:
                raise ValueError("transient")
            return "ok"
        return action

    result = _run(
        retry_async(
            make_action(), max_attempts=5,
            retryable_exceptions=(ValueError,), base_delay=0, label="t",
        )
    )
    assert result == "ok"
    assert state["n"] == 3


def test_raises_after_max_attempts():
    state = {"n": 0}

    def make_action():
        async def action():
            state["n"] += 1
            raise ValueError("boom")
        return action

    try:
        _run(
            retry_async(
                make_action(), max_attempts=3,
                retryable_exceptions=(ValueError,), base_delay=0, label="t",
            )
        )
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert state["n"] == 3


def test_non_retryable_propagates_immediately():
    state = {"n": 0}

    def make_action():
        async def action():
            state["n"] += 1
            raise KeyError("nope")
        return action

    try:
        _run(
            retry_async(
                make_action(), max_attempts=5,
                retryable_exceptions=(ValueError,), base_delay=0, label="t",
            )
        )
        assert False, "expected KeyError"
    except KeyError:
        pass
    # Not in the retryable set -> attempted exactly once.
    assert state["n"] == 1


# --------------------------------------------------------------------------- #
# LLMRouter.chat retries transient failures (the "LLM error" / timeout case)
# --------------------------------------------------------------------------- #
class _StubProvider:
    name = "openai"

    def __init__(self, fail_times=0):
        self.n = 0
        self.fail_times = fail_times

    async def chat_completion(self, messages, model, **kwargs):
        self.n += 1
        if self.n <= self.fail_times:
            raise ConnectionError("transient network blip")
        return "success"

    async def stream_chat(self, messages, model, **kwargs):
        yield "x"

    async def list_models(self):
        return ["m"]


def _make_router():
    class _Cfg:
        default_llm_provider = "openai"
        current_model = ""
        openai_api_key = None
        google_api_key = None
        anthropic_api_key = None
        openrouter_api_key = None
        openrouter_http_referer = None
        openrouter_app_name = None
        grok_api_key = None
        deepseek_api_key = None

    router = LLMRouter(_Cfg())
    # The guardrail locks LLM settings by default; the retry tests only
    # exercise chat()/persistence, so unlock to allow the fixture to set
    # the model as before.
    router.unlock_llm_settings()
    stub = _StubProvider()
    router.providers["openai"] = stub
    router.current_model = "gpt-4o"
    return router, stub


def test_chat_retries_transient_then_succeeds():
    router, stub = _make_router()
    stub.fail_times = 1  # fail once, succeed on the 2nd attempt
    result = _run(router.chat([{"role": "user", "content": "hi"}]))
    assert result == "success"
    assert stub.n == 2  # 1 failure + 1 success


def test_chat_gives_up_after_max_attempts():
    router, stub = _make_router()
    router.max_chat_attempts = 2  # keep the test's sleeps short
    stub.fail_times = 99  # never succeed
    try:
        _run(router.chat([{"role": "user", "content": "hi"}]))
        assert False, "expected ConnectionError"
    except ConnectionError:
        pass
    assert stub.n == router.max_chat_attempts


# --------------------------------------------------------------------------- #
# ShellCommandTool.run_command retries a transient timeout
# --------------------------------------------------------------------------- #
def test_shell_command_retries_on_timeout():
    original_wait = asyncio.wait_for
    original_create = asyncio.create_subprocess_shell

    class _FakeProcess:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            return b"", b""

        def kill(self):
            pass

    state = {"n": 0}

    async def fake_wait_for(coro, timeout):
        state["n"] += 1
        if state["n"] < ShellCommandTool.MAX_COMMAND_ATTEMPTS:
            # Simulate a timeout: the communicate() coroutine never runs.
            coro.close()
            raise asyncio.TimeoutError()
        # On the final attempt, let the real communicate() coroutine run so we
        # don't leak an un-awaited coroutine.
        return await coro

    async def fake_create(*args, **kwargs):
        return _FakeProcess()

    asyncio.wait_for = fake_wait_for
    asyncio.create_subprocess_shell = fake_create
    try:
        result = _run(ShellCommandTool.run_command(command="sleep 999", timeout=1))
    finally:
        asyncio.wait_for = original_wait
        asyncio.create_subprocess_shell = original_create

    assert result.success is True
    assert state["n"] == ShellCommandTool.MAX_COMMAND_ATTEMPTS


def test_shell_command_timeout_eventually_fails():
    original_wait = asyncio.wait_for
    original_create = asyncio.create_subprocess_shell

    class _FakeProcess:
        def kill(self):
            pass

        async def communicate(self):
            return b"", b""

    async def always_timeout(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    async def fake_create(*args, **kwargs):
        return _FakeProcess()

    asyncio.wait_for = always_timeout
    asyncio.create_subprocess_shell = fake_create
    try:
        result = _run(ShellCommandTool.run_command(command="sleep 999", timeout=1))
    finally:
        asyncio.wait_for = original_wait
        asyncio.create_subprocess_shell = original_create

    assert result.success is False
    assert "timed out" in result.error.lower()
    assert "retried 5 times" in result.error
