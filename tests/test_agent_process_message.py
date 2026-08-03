"""
Tests for the agent process_message edge cases and tool execution flow.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.tools.tool_registry import ToolResult


def _run(coro):
    return asyncio.run(coro)


def _make_agent(chat_side_effect=None, execute_result=None):
    """Build a ClioAgent with mocked dependencies."""
    agent = mock.MagicMock(spec=ClioAgent)
    agent.BASE_SYSTEM_PROMPT = "system"
    agent.response_callbacks = []

    context_log = mock.MagicMock()
    context_log.get_entries_as_messages.return_value = []
    context_log.add_user_message = mock.AsyncMock()
    context_log.add_assistant_response = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
    context_log.add_thinking = mock.AsyncMock()
    agent.context_log = context_log

    llm_router = mock.MagicMock()
    llm_router.chat = mock.AsyncMock(side_effect=chat_side_effect if chat_side_effect else [""])
    agent.llm_router = llm_router

    tool_registry = mock.MagicMock()
    tool_registry.execute_tool = mock.AsyncMock(
        return_value=execute_result or ToolResult(True, "ok")
    )
    tool_registry.list_tools = mock.MagicMock(return_value=["read_file", "say", "thinking"])
    agent.tool_registry = tool_registry

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

    for name in ("_is_valid_tool_call", "_extract_json_objects"):
        setattr(agent, name, getattr(ClioAgent, name))

    return agent


class TestProcessMessageEdgeCases:
    """Tests for process_message edge cases"""

    def test_process_message_returns_string_not_none(self):
        """process_message should always return a string, never None"""
        agent = _make_agent(chat_side_effect=[None])
        result = _run(agent.process_message("test"))
        assert isinstance(result, str)

    def test_process_message_empty_completion(self):
        """Empty completion from LLM should return empty string"""
        agent = _make_agent(chat_side_effect=[""])
        result = _run(agent.process_message("test"))
        assert result == ""

    def test_process_message_with_tool_call(self):
        """Tool call should be executed and return empty string"""
        agent = _make_agent(chat_side_effect=[
            '{"tool": "read_file", "arguments": {"filepath": "test.txt"}}',
            "",
        ], execute_result=ToolResult(True, "file content"))

        result = _run(agent.process_message("read the file"))
        assert result == ""
        agent.tool_registry.execute_tool.assert_called()

    def test_process_message_multiple_tool_rounds(self):
        """Multiple tool rounds should all execute"""
        agent = _make_agent(chat_side_effect=[
            '{"tool": "read_file", "arguments": {"filepath": "a.txt"}}',
            '{"tool": "write_file", "arguments": {"filepath": "b.txt", "content": "data"}}',
            "",
        ], execute_result=ToolResult(True, "ok"))

        result = _run(agent.process_message("read and write"))
        assert result == ""
        assert agent.tool_registry.execute_tool.await_count == 2

    def test_process_message_tool_failure(self):
        """Tool failure should be handled gracefully"""
        agent = _make_agent(chat_side_effect=[
            '{"tool": "read_file", "arguments": {"filepath": "missing.txt"}}',
            "",
        ], execute_result=ToolResult(False, "", "File not found"))

        result = _run(agent.process_message("read missing"))
        assert result == ""
        agent.tool_registry.execute_tool.assert_called()

    def test_process_message_llm_exception(self):
        """LLM exception should be caught and return empty string"""
        agent = _make_agent(chat_side_effect=Exception("Provider down"))
        result = _run(agent.process_message("test"))
        assert result == ""
        agent.context_log.add_system_message.assert_called()

    def test_process_message_tool_exception(self):
        """Exception from tool execution should be caught"""
        agent = _make_agent(chat_side_effect=[
            '{"tool": "read_file", "arguments": {"filepath": "test.txt"}}',
            "",
        ])
        agent.tool_registry.execute_tool = mock.AsyncMock(
            side_effect=RuntimeError("Tool crashed")
        )

        result = _run(agent.process_message("test"))
        assert result == ""

    def test_process_message_no_context_log_add(self):
        """Message should be added to context log"""
        agent = _make_agent(chat_side_effect=[""])

        _run(agent.process_message("User message"))

        agent.context_log.add_user_message.assert_awaited_once_with("User message")


class TestExecuteToolRoundEdgeCases:
    """Tests for _execute_tool_round edge cases"""

    def test_execute_tool_round_say_tool(self):
        """Say tool should execute and deliver message"""
        from clio_agent_2.tools.tool_registry import SayTool

        agent = _make_agent()
        say_tool = SayTool(agent.context_log, agent.send_response)

        async def execute(name, args):
            if name == "say":
                return await say_tool.say(**args)
            return ToolResult(False, "", f"Unknown: {name}")

        agent.tool_registry.execute_tool = mock.AsyncMock(side_effect=execute)
        agent._tool_calls_seen = []

        tool_calls = [{"tool": "say", "arguments": {"message": "Hello user"}}]
        feedback = _run(agent._execute_tool_round(tool_calls))

        assert "[TOOL OK] say" in feedback
        assert "Hello user" in feedback

    def test_execute_tool_round_unknown_tool(self):
        agent = _make_agent()

        tool_calls = [{"tool": "nonexistent", "arguments": {}}]
        feedback = _run(agent._execute_tool_round(tool_calls))

        assert "[TOOL FAILED] nonexistent" in feedback
        assert "Unknown tool" in feedback

    def test_execute_tool_round_empty_list(self):
        agent = _make_agent()

        feedback = _run(agent._execute_tool_round([]))
        assert feedback == ""


class TestRunAgentTurnEdgeCases:
    """Tests for _run_agent_turn edge cases"""

    def test_run_agent_turn_none_response(self):
        """None response from LLM should be handled"""
        agent = _make_agent(chat_side_effect=[None])

        messages = [{"role": "user", "content": "test"}]
        result = _run(agent._run_agent_turn(messages))

        assert result == ""

    def test_run_agent_turn_no_tool_calls(self):
        """Response without tool calls returns empty string"""
        agent = _make_agent(chat_side_effect=["Just a normal response"])

        messages = [{"role": "user", "content": "test"}]
        result = _run(agent._run_agent_turn(messages))

        assert result == ""

    def test_run_agent_turn_max_iterations(self):
        """Should stop at MAX_TOOL_ITERATIONS"""
        from clio_agent_2.core.agent import MAX_TOOL_ITERATIONS
        from clio_agent_2.tools.tool_registry import ToolResult

        agent = _make_agent()
        tool_call = '{"tool": "read_file", "arguments": {"filepath": "test.txt"}}'
        agent.llm_router.chat = mock.AsyncMock(return_value=tool_call)
        agent.tool_registry.execute_tool = mock.AsyncMock(
            return_value=ToolResult(True, "ok")
        )

        messages = [{"role": "user", "content": "test"}]
        result = _run(agent._run_agent_turn(messages))

        assert result == ""
        assert agent.tool_registry.execute_tool.await_count == MAX_TOOL_ITERATIONS

    def test_run_agent_turn_deadline_passed(self):
        """Deadline should be passed to LLM chat"""
        agent = _make_agent(chat_side_effect=[""])
        deadline = 9999999999.0

        messages = [{"role": "user", "content": "test"}]
        _run(agent._run_agent_turn(messages, deadline=deadline))

        # Check deadline was passed to chat
        call_args = agent.llm_router.chat.call_args
        assert call_args.kwargs.get("deadline") == deadline


class TestSendResponseEdgeCases:
    """Tests for send_response edge cases"""

    def test_send_response_no_callbacks(self):
        agent = _make_agent()
        agent.response_callbacks = []

        _run(agent.send_response("test message"))
        # Should not raise

    def test_send_response_callback_exception(self):
        agent = _make_agent()

        async def bad_callback(msg):
            raise ValueError("Callback error")

        async def good_callback(msg):
            pass

        agent.response_callbacks = [bad_callback, good_callback]

        _run(agent.send_response("test"))
        # Should not raise, exception should be ignored

    def test_send_response_multiple_callbacks(self):
        agent = _make_agent()

        delivered = []
        async def callback(msg):
            delivered.append(msg)

        agent.response_callbacks = [callback, callback]

        _run(agent.send_response("hello"))
        assert delivered == ["hello", "hello"]


class TestRegisterResponseCallback:
    """Tests for register_response_callback"""

    def test_register_callback(self):
        agent = _make_agent()
        agent.response_callbacks = []

        cb = mock.MagicMock()
        agent.register_response_callback(cb)

        assert len(agent.response_callbacks) == 1
        assert agent.response_callbacks[0] is cb

    def test_register_multiple(self):
        agent = _make_agent()
        agent.response_callbacks = []

        cb1 = mock.MagicMock()
        cb2 = mock.MagicMock()
        agent.register_response_callback(cb1)
        agent.register_response_callback(cb2)

        assert len(agent.response_callbacks) == 2