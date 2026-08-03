"""
Tests for the ThinkingTool and SayTool extended functionality.
"""
import asyncio
from unittest import mock

from clio_agent_2.tools.tool_registry import ThinkingTool, SayTool, ToolResult


def _run(coro):
    return asyncio.run(coro)


class TestThinkingToolExtended:
    """Extended tests for ThinkingTool"""

    def _make_tool(self):
        context_log = mock.AsyncMock()
        return ThinkingTool(context_log), context_log

    def test_think_canonical_keyword(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think(thought="My reasoning"))

        assert result.success is True
        assert "Thought recorded" in result.output
        context_log.add_thinking.assert_awaited_once_with("My reasoning")

    def test_think_text_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think(text="Via text alias"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Via text alias")

    def test_think_content_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think(content="Via content alias"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Via content alias")

    def test_think_context_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think(context="Via context alias"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Via context alias")

    def test_think_note_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think(note="Via note alias"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Via note alias")

    def test_think_message_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think(message="Via message alias"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Via message alias")

    def test_think_empty_returns_error(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think())

        assert result.success is False
        assert "Missing reasoning text" in result.error
        context_log.add_thinking.assert_not_awaited()

    def test_think_first_alias_wins(self):
        """If multiple aliases provided, canonical 'thought' should win"""
        tool, context_log = self._make_tool()
        result = _run(tool.think(thought="Canonical", content="Ignored"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Canonical")

    def test_think_text_wins_over_content(self):
        """If thought not provided, text should win over content"""
        tool, context_log = self._make_tool()
        result = _run(tool.think(text="Text wins", content="Content loses"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Text wins")

    def test_think_unicode(self):
        tool, context_log = self._make_tool()
        result = _run(tool.think(thought="Thinking in 世界 🌍"))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Thinking in 世界 🌍")

    def test_think_long_content(self):
        tool, context_log = self._make_tool()
        long_thought = "x" * 10000
        result = _run(tool.think(thought=long_thought))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with(long_thought)


class TestSayToolExtended:
    """Extended tests for SayTool"""

    def _make_tool(self, response_sink=None):
        context_log = mock.AsyncMock()
        return SayTool(context_log, response_sink), context_log

    def test_say_canonical_message(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say(message="Hello world"))

        assert result.success is True
        assert result.output == "Hello world"
        context_log.add_assistant_response.assert_awaited_once_with("Hello world")

    def test_say_text_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say(text="Via text"))

        assert result.success is True
        assert result.output == "Via text"

    def test_say_content_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say(content="Via content"))

        assert result.success is True
        assert result.output == "Via content"

    def test_say_say_alias(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say(say="Via say alias"))

        assert result.success is True
        assert result.output == "Via say alias"

    def test_say_empty_returns_error(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say())

        assert result.success is False
        assert "Missing message" in result.error

    def test_say_first_alias_wins(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say(message="Canonical", text="Ignored"))

        assert result.success is True
        assert result.output == "Canonical"

    def test_say_with_response_sink(self):
        sink = mock.AsyncMock()
        tool, context_log = self._make_tool(response_sink=sink)
        result = _run(tool.say(message="Delivered"))

        assert result.success is True
        sink.assert_awaited_once_with("Delivered")

    def test_say_response_sink_failure_ignored(self):
        sink = mock.AsyncMock(side_effect=Exception("Sink failed"))
        tool, context_log = self._make_tool(response_sink=sink)
        result = _run(tool.say(message="Still succeeds"))

        assert result.success is True
        assert result.output == "Still succeeds"

    def test_say_context_log_failure_ignored(self):
        tool, context_log = self._make_tool()
        context_log.add_assistant_response = mock.AsyncMock(side_effect=Exception("Log failed"))
        result = _run(tool.say(message="Ok"))

        assert result.success is True

    def test_say_strips_whitespace(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say(message="  Hello  "))

        assert result.success is True
        assert result.output == "Hello"

    def test_say_unicode(self):
        tool, context_log = self._make_tool()
        result = _run(tool.say(message="Hello 世界 🌍"))

        assert result.success is True
        assert result.output == "Hello 世界 🌍"

    def test_say_long_message(self):
        tool, context_log = self._make_tool()
        long_msg = "x" * 10000
        result = _run(tool.say(message=long_msg))

        assert result.success is True
        assert result.output == long_msg


class TestThinkingToolContextLogIntegration:
    """Integration tests for ThinkingTool with ContextLog"""

    def test_think_records_to_context(self):
        context_log = mock.AsyncMock()
        tool = ThinkingTool(context_log)

        _run(tool.think(thought="My thought"))

        context_log.add_thinking.assert_awaited_once()
        call_args = context_log.add_thinking.call_args
        assert call_args[0][0] == "My thought"

    def test_think_records_thinking_type(self):
        context_log = mock.AsyncMock()
        tool = ThinkingTool(context_log)

        _run(tool.think(thought="Test"))

        context_log.add_thinking.assert_called()


class TestSayToolContextLogIntegration:
    """Integration tests for SayTool with ContextLog"""

    def test_say_records_assistant_response(self):
        context_log = mock.AsyncMock()
        tool = SayTool(context_log, None)

        _run(tool.say(message="User message"))

        context_log.add_assistant_response.assert_awaited_once_with("User message")

    def test_say_without_context_log(self):
        """SayTool should work without context_log"""
        tool = SayTool(None, None)
        result = _run(tool.say(message="Test"))

        assert result.success is True
        assert result.output == "Test"