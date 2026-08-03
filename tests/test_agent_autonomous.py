"""
Tests for ClioAgent autonomous loop behavior.
Covers run_autonomous_loop, circuit breaker, exponential backoff, autonomous_think.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

from clio_agent_2.core.agent import ClioAgent, CIRCUIT_BREAKER_THRESHOLD, MAX_TOOL_ITERATIONS


def _run(coro):
    return asyncio.run(coro)


def _make_mock_agent(chat_side_effect=None, execute_result=None):
    """Build a ClioAgent with mocked dependencies but real logic."""
    from clio_agent_2.tools.tool_registry import ToolResult

    agent = mock.MagicMock(spec=ClioAgent)
    agent.response_callbacks = []

    context_log = mock.MagicMock()
    context_log.get_entries_as_messages.return_value = []
    context_log.add_user_message = mock.AsyncMock()
    context_log.add_assistant_response = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
    context_log.add_thinking = mock.AsyncMock()
    context_log.working_summary = ""
    agent.context_log = context_log

    llm_router = mock.MagicMock()
    llm_router.chat = mock.AsyncMock(side_effect=chat_side_effect if chat_side_effect else ["default"])
    agent.llm_router = llm_router

    tool_registry = mock.MagicMock()
    tool_registry.execute_tool = mock.AsyncMock(
        return_value=execute_result or ToolResult(True, "ok")
    )
    tool_registry.list_tools = mock.MagicMock(return_value=[
        "read_file", "write_file", "shell_command", "say", "thinking"
    ])
    agent.tool_registry = tool_registry

    agent.BASE_SYSTEM_PROMPT = "system"
    agent.name = "TestAgent"
    agent.is_running = False
    agent._consecutive_failures = 0
    agent._circuit_open = False
    agent.autonomous_mode = True
    agent.thinking_interval = 0.1

    for name in (
        "_parse_tool_calls",
        "_system_block",
        "_build_context_messages",
        "_execute_tool_round",
        "_run_agent_turn",
        "process_message",
        "run_autonomous_loop",
        "start_autonomous_loop",
        "send_response",
        "autonomous_think",
    ):
        method = getattr(ClioAgent, name)
        setattr(agent, name, method.__get__(agent, ClioAgent))

    for name in ("_is_valid_tool_call", "_extract_json_objects", "_available_tools_text"):
        setattr(agent, name, getattr(ClioAgent, name))

    return agent


class TestRunAutonomousLoop:
    """Tests for ClioAgent.run_autonomous_loop"""

    def test_autonomous_loop_runs_and_stops(self):
        """Loop should run cycles and stop when is_running is False"""
        agent = _make_mock_agent(chat_side_effect=[
            '{"tool": "say", "arguments": {"message": "Hello"}}',
            "",  # no more tool calls after
        ])
        agent.thinking_interval = 0.001

        # Patch autonomous_think to return "" (success) and then set is_running=False
        call_count = 0
        async def mock_think():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                agent.is_running = False
            return ""

        agent.autonomous_think = mock_think

        _run(agent.run_autonomous_loop())

        assert call_count >= 2
        assert agent.is_running is False
        agent.context_log.add_system_message.assert_called()

    def test_autonomous_loop_circuit_breaker(self):
        """Circuit breaker triggers after threshold failures"""
        agent = _make_mock_agent()
        agent.thinking_interval = 0.001

        # autonomous_think returns None (failure)
        agent.autonomous_think = AsyncMock(return_value=None)

        # Run until circuit breaker trips
        _run(agent.run_autonomous_loop())

        assert agent._circuit_open is True
        assert agent.is_running is False
        agent.context_log.add_system_message.assert_called()
        # Check circuit breaker message was logged
        system_msgs = [call.args[0] for call in agent.context_log.add_system_message.call_args_list]
        circuit_msgs = [m for m in system_msgs if "Circuit breaker tripped" in m]
        assert len(circuit_msgs) > 0

    def test_autonomous_loop_exponential_backoff(self):
        """Failed cycles should have increasing backoff"""
        agent = _make_mock_agent()
        agent.thinking_interval = 0.01

        fail_count = 0
        async def mock_think():
            nonlocal fail_count
            fail_count += 1
            return None  # Always fail

        agent.autonomous_think = mock_think

        # Should trip after threshold failures
        _run(agent.run_autonomous_loop())
        assert fail_count == CIRCUIT_BREAKER_THRESHOLD

    def test_autonomous_loop_success_resets_failures(self):
        """A successful cycle should reset the failure counter"""
        agent = _make_mock_agent()
        agent.thinking_interval = 0.001

        call_count = 0
        async def mock_think():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                agent.is_running = False
            if call_count == 2:
                return ""  # Success on 2nd try
            return None  # Fail first time

        agent.autonomous_think = mock_think

        _run(agent.run_autonomous_loop())

        assert agent._consecutive_failures == 0


class TestAutonomousThink:
    """Tests for ClioAgent.autonomous_think"""

    def test_autonomous_think_returns_empty_string_on_success(self):
        agent = _make_mock_agent(chat_side_effect=[
            '{"tool": "say", "arguments": {"message": "Thinking..."}}',
            "",
        ])

        delivered = []
        async def capture(msg):
            delivered.append(msg)
        agent.response_callbacks.append(capture)

        result = _run(agent.autonomous_think())
        assert result == ""
        assert "Thinking..." in delivered

    def test_autonomous_think_returns_empty_on_no_tools(self):
        agent = _make_mock_agent(chat_side_effect=["Just thinking..."])

        result = _run(agent.autonomous_think())
        assert result == ""

    def test_autonomous_think_returns_none_on_exception(self):
        agent = _make_mock_agent()
        agent._build_context_messages = mock.MagicMock(side_effect=Exception("Config error"))

        result = _run(agent.autonomous_think())
        assert result is None
        agent.context_log.add_system_message.assert_called_with(mock.ANY)

    def test_autonomous_think_delivers_say_via_response(self):
        from clio_agent_2.tools.tool_registry import SayTool

        agent = _make_mock_agent()

        say_tool = SayTool(agent.context_log, agent.send_response)
        tool_calls_seen = []

        async def _execute(name, args):
            tool_calls_seen.append(name)
            if name == "say":
                return await say_tool.say(**args)
            from clio_agent_2.tools.tool_registry import ToolResult
            return ToolResult(False, "", f"Unknown tool: {name}")

        agent.tool_registry.execute_tool = mock.AsyncMock(side_effect=_execute)
        agent._tool_calls_seen = tool_calls_seen

        agent.response_callbacks = [lambda m: None]  # Suppress output

        agent.llm_router.chat = mock.AsyncMock(
            return_value='{"tool": "say", "arguments": {"message": "Hi there"}}'
        )

        result = _run(agent.autonomous_think())
        assert result == ""


class TestStartAutonomousLoop:
    """Tests for ClioAgent.start_autonomous_loop lifecycle"""

    def test_start_autonomous_loop_creates_task(self):
        agent = _make_mock_agent()
        agent._autonomous_task = None

        # Start the loop
        result = _run(agent.start_autonomous_loop())
        assert result is True
        assert agent.is_running is True

    def test_start_autonomous_loop_already_running(self):
        agent = _make_mock_agent()
        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        agent._autonomous_task = mock_task

        # Reset is_running since start_autonomous_loop sets it
        result = _run(agent.start_autonomous_loop())
        assert result is True
        # Should not create a new task

    def test_start_autonomous_loop_no_model(self):
        agent = _make_mock_agent()
        agent.llm_router.current_model = ""

        result = _run(agent.start_autonomous_loop())
        assert result is False
        agent.context_log.add_system_message.assert_called()


class TestEnsureAutonomousLoop:
    """Tests for ensure_autonomous_loop and start_autonomous_loop_if_enabled"""

    def test_ensure_autonomous_loop_disabled(self):
        agent = _make_mock_agent()
        agent.autonomous_mode = False

        result = _run(agent.ensure_autonomous_loop())
        assert result is False

    def test_ensure_autonomous_loop_enabled(self):
        agent = _make_mock_agent()
        agent.autonomous_mode = True
        agent.start_autonomous_loop = AsyncMock(return_value=True)

        result = _run(agent.ensure_autonomous_loop())
        assert result is True

    def test_start_autonomous_loop_if_enabled_disabled(self):
        agent = _make_mock_agent()
        agent.autonomous_mode = False

        result = _run(agent.start_autonomous_loop_if_enabled())
        assert result is False

    def test_start_autonomous_loop_if_enabled_enabled(self):
        agent = _make_mock_agent()
        agent.autonomous_mode = True
        agent.start_autonomous_loop = AsyncMock(return_value=True)

        result = _run(agent.start_autonomous_loop_if_enabled())
        assert result is True


class TestStopAutonomousLoop:
    """Tests for stop_autonomous_loop"""

    def test_stop_no_task(self):
        agent = _make_mock_agent()
        agent._autonomous_task = None

        _run(agent.stop_autonomous_loop())
        assert agent.is_running is False

    def test_stop_with_running_task(self):
        agent = _make_mock_agent()
        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=False)
        mock_task.cancel = MagicMock()
        agent._autonomous_task = mock_task

        # Mock the await behavior
        async def mock_task_await():
            raise asyncio.CancelledError()

        mock_task.__await__ = lambda: mock_task_await().__await__()

        _run(agent.stop_autonomous_loop())
        assert agent._autonomous_task is None


class TestRunAgentTurn:
    """Tests for _run_agent_turn internal method"""

    def test_turn_completes_no_tools(self):
        """A response with no tool calls returns empty string"""
        from clio_agent_2.tools.tool_registry import ToolResult

        agent = _make_mock_agent()
        agent.llm_router.chat = AsyncMock(return_value="Just a normal response")

        messages = [{"role": "user", "content": "Hello"}]
        result = _run(agent._run_agent_turn(messages))

        assert result == ""

    def test_turn_handles_llm_error(self):
        """An LLM exception returns None (failure signal)"""
        agent = _make_mock_agent()
        agent.llm_router.chat = AsyncMock(side_effect=Exception("Provider down"))

        messages = [{"role": "user", "content": "Hello"}]
        result = _run(agent._run_agent_turn(messages))

        assert result is None
        agent.context_log.add_system_message.assert_called()

    def test_turn_max_iterations_guardrail(self):
        """Loop should stop at MAX_TOOL_ITERATIONS"""
        from clio_agent_2.tools.tool_registry import ToolResult

        agent = _make_mock_agent()
        # Always return a tool call - would loop infinitely without the guard
        tool_call = '{"tool": "read_file", "arguments": {"filepath": "test.txt"}}'
        agent.llm_router.chat = AsyncMock(return_value=tool_call)
        agent.tool_registry.execute_tool = AsyncMock(
            return_value=ToolResult(True, "ok")
        )

        messages = [{"role": "user", "content": "Read file"}]
        result = _run(agent._run_agent_turn(messages))

        assert result == ""
        assert agent.tool_registry.execute_tool.await_count == MAX_TOOL_ITERATIONS