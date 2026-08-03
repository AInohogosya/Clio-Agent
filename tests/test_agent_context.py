"""
Tests for ClioAgent context assembly and message building.
"""
import asyncio
from unittest import mock
from unittest.mock import MagicMock

from clio_agent_2.core.agent import ClioAgent, MAX_CONTEXT_TOKENS


def _run(coro):
    return asyncio.run(coro)


class TestSystemBlock:
    """Tests for ClioAgent._system_block"""

    def _make_agent(self):
        agent = mock.MagicMock(spec=ClioAgent)
        agent.context_log = mock.MagicMock()
        agent.context_log.working_summary = ""
        agent.tool_registry = mock.MagicMock()
        agent.tool_registry.list_tools = mock.MagicMock(return_value=[
            "read_file", "write_file", "say", "thinking"
        ])
        agent._cached_prompt = ""
        agent._cached_tools = ""
        agent._available_tools_text = ClioAgent._available_tools_text.__get__(agent, ClioAgent)
        return agent

    def test_system_block_has_system_role(self):
        agent = self._make_agent()
        block = ClioAgent._system_block(agent)
        assert block["role"] == "system"
        assert "content" in block

    def test_system_block_contains_agent_name(self):
        agent = self._make_agent()
        block = ClioAgent._system_block(agent)
        assert "Clio-Agent-2" in block["content"]

    def test_system_block_includes_tool_list(self):
        agent = self._make_agent()
        block = ClioAgent._system_block(agent)
        assert "read_file" in block["content"]
        assert "write_file" in block["content"]
        assert "say" in block["content"]

    def test_system_block_includes_say_guidance(self):
        agent = self._make_agent()
        block = ClioAgent._system_block(agent)
        assert "Say" in block["content"]
        assert "say" in block["content"].lower()

    def test_system_block_with_working_summary(self):
        agent = self._make_agent()
        agent.context_log.working_summary = "Previous summary content"

        block = ClioAgent._system_block(agent)
        assert "Rolling context summary" in block["content"]
        assert "Previous summary content" in block["content"]

    def test_system_block_no_summary_when_empty(self):
        agent = self._make_agent()
        agent.context_log.working_summary = ""

        block = ClioAgent._system_block(agent)
        assert "Rolling context summary" not in block["content"]

    def test_system_block_no_double_braces(self):
        """System prompt should not contain double braces (malformed JSON example)"""
        agent = self._make_agent()
        block = ClioAgent._system_block(agent)
        assert "{{" not in block["content"]
        assert "}}" not in block["content"]

    def test_system_block_caching(self):
        """System block should be cached when tool list hasn't changed"""
        agent = self._make_agent()
        block1 = ClioAgent._system_block(agent)
        cached_prompt = agent._cached_prompt

        block2 = ClioAgent._system_block(agent)
        assert agent._cached_prompt == cached_prompt

    def test_system_block_cache_invalidates_on_tool_change(self):
        agent = self._make_agent()
        block1 = ClioAgent._system_block(agent)

        # Change the tool list
        agent.tool_registry.list_tools = mock.MagicMock(return_value=["different_tool"])
        agent._cached_tools = ""  # Clear cache

        block2 = ClioAgent._system_block(agent)
        assert "different_tool" in block2["content"]
        assert "read_file" not in block2["content"]


class TestBuildContextMessages:
    """Tests for ClioAgent._build_context_messages"""

    def _make_agent(self):
        agent = mock.MagicMock(spec=ClioAgent)
        agent.context_log = mock.MagicMock()
        agent.context_log.working_summary = ""
        agent.context_log.get_entries_as_messages = mock.MagicMock(return_value=[])
        agent.tool_registry = mock.MagicMock()
        agent.tool_registry.list_tools = mock.MagicMock(return_value=["read_file", "say"])
        agent._cached_prompt = ""
        agent._cached_tools = ""
        agent._available_tools_text = ClioAgent._available_tools_text.__get__(agent, ClioAgent)
        agent._system_block = ClioAgent._system_block.__get__(agent, ClioAgent)
        # Fix: BASE_SYSTEM_PROMPT is a property, so we mock it as a real string
        type(agent).BASE_SYSTEM_PROMPT = mock.PropertyMock(return_value="Clio-Agent-2 system prompt")
        return agent

    def test_minimal_messages_includes_system_and_user(self):
        agent = self._make_agent()
        messages = ClioAgent._build_context_messages(agent, "User message")
        assert len(messages) >= 2
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"

    def test_user_message_preserved(self):
        agent = self._make_agent()
        user_text = "Please read the file at /tmp/test.txt"
        messages = ClioAgent._build_context_messages(agent, user_text)
        assert messages[-1]["content"] == user_text

    def test_system_block_included(self):
        agent = self._make_agent()
        messages = ClioAgent._build_context_messages(agent, "test")
        assert messages[0]["role"] == "system"
        assert "Clio-Agent-2" in messages[0]["content"]

    def test_hot_entries_included(self):
        agent = self._make_agent()
        agent.context_log.get_entries_as_messages = mock.MagicMock(
            return_value=[
                {"role": "user", "content": "Hot entry 1"},
                {"role": "assistant", "content": "Hot entry 2"},
            ]
        )
        messages = ClioAgent._build_context_messages(agent, "new turn")
        assert len(messages) == 4
        assert messages[1]["content"] == "Hot entry 1"
        assert messages[2]["content"] == "Hot entry 2"


class TestExecuteToolRound:
    """Tests for ClioAgent._execute_tool_round"""

    def _make_agent(self):
        agent = mock.MagicMock(spec=ClioAgent)
        agent.tool_registry = mock.MagicMock()
        agent.tool_registry.execute_tool = mock.AsyncMock()
        return agent

    def test_successful_tool_execution(self):
        from clio_agent_2.tools.tool_registry import ToolResult
        agent = self._make_agent()
        agent.tool_registry.execute_tool = mock.AsyncMock(
            return_value=ToolResult(True, "File contents")
        )

        tool_calls = [{"tool": "read_file", "arguments": {"filepath": "test.txt"}}]
        feedback = _run(agent._execute_tool_round(tool_calls))

        assert "[TOOL OK]" in feedback
        assert "read_file" in feedback
        assert "File contents" in feedback

    def test_failed_tool_execution(self):
        from clio_agent_2.tools.tool_registry import ToolResult
        agent = self._make_agent()
        agent.tool_registry.execute_tool = mock.AsyncMock(
            return_value=ToolResult(False, "", "File not found")
        )

        tool_calls = [{"tool": "read_file", "arguments": {"filepath": "missing.txt"}}]
        feedback = _run(agent._execute_tool_round(tool_calls))

        assert "[TOOL FAILED]" in feedback
        assert "read_file" in feedback
        assert "File not found" in feedback

    def test_tool_exception_caught(self):
        agent = self._make_agent()
        agent.tool_registry.execute_tool = mock.AsyncMock(side_effect=ValueError("Unexpected error"))

        tool_calls = [{"tool": "read_file", "arguments": {"filepath": "test.txt"}}]
        feedback = _run(agent._execute_tool_round(tool_calls))

        assert "[TOOL FAILED]" in feedback
        assert "Unexpected error" in feedback

    def test_multiple_tool_calls_in_one_round(self):
        from clio_agent_2.tools.tool_registry import ToolResult
        agent = self._make_agent()
        agent.tool_registry.execute_tool = mock.AsyncMock(
            return_value=ToolResult(True, "result")
        )

        tool_calls = [
            {"tool": "read_file", "arguments": {"filepath": "a.txt"}},
            {"tool": "write_file", "arguments": {"filepath": "b.txt", "content": "x"}},
        ]
        feedback = _run(agent._execute_tool_round(tool_calls))

        assert "[TOOL OK] read_file" in feedback
        assert "[TOOL OK] write_file" in feedback
        assert agent.tool_registry.execute_tool.await_count == 2

    def test_failed_tool_with_empty_error(self):
        from clio_agent_2.tools.tool_registry import ToolResult
        agent = self._make_agent()
        agent.tool_registry.execute_tool = mock.AsyncMock(
            return_value=ToolResult(False, "", None)
        )

        tool_calls = [{"tool": "read_file", "arguments": {"filepath": "test.txt"}}]
        feedback = _run(agent._execute_tool_round(tool_calls))

        assert "[TOOL FAILED]" in feedback
        assert "Unknown error" in feedback