"""
Smoke / regression tests for the ``thinking`` tool (``ThinkingTool.think``).

These cover the reported bug where calling the tool with ``content=`` or
``context=`` raised::

    ThinkingTool.think() got an unexpected keyword argument 'content'
    ThinkingTool.think() got an unexpected keyword argument 'context'

The tool must accept the canonical ``thought`` keyword as well as the common
aliases the agent/LLM may emit (``text``, ``content``, ``context``,
``note``, ``message``).

Run with:
    python3 -m pytest tests/test_thinking_tool.py -v

(pytest-asyncio is NOT required; tests drive the coroutines via asyncio.run.)
"""

import asyncio
from unittest import mock

from clio_agent_2.tools.tool_registry import ThinkingTool


def _run(coro):
    """Run an async coroutine to completion without pytest-asyncio."""
    return asyncio.run(coro)


def _make_tool():
    """Build a ThinkingTool backed by a mock context log."""
    context_log = mock.AsyncMock()
    return ThinkingTool(context_log), context_log


def test_canonical_thought_keyword_succeeds():
    """The preferred ``thought=`` argument is recorded successfully."""
    tool, context_log = _make_tool()
    result = _run(tool.think(thought="Step 1: examine the config"))

    assert result.success is True
    assert "Thought recorded" in result.output
    context_log.add_thinking.assert_awaited_once_with("Step 1: examine the config")


def test_content_alias_succeeds():
    """The previously-failing ``content=`` argument now works (no error)."""
    tool, context_log = _make_tool()
    # This used to raise: got an unexpected keyword argument 'content'
    result = _run(tool.think(content="We should refactor the loop"))

    assert result.success is True
    context_log.add_thinking.assert_awaited_once_with("We should refactor the loop")


def test_context_alias_succeeds():
    """The previously-failing ``context=`` argument now works (no error)."""
    tool, context_log = _make_tool()
    # This used to raise: got an unexpected keyword argument 'context'
    result = _run(tool.think(context="User wants a progress report"))

    assert result.success is True
    context_log.add_thinking.assert_awaited_once_with("User wants a progress report")


def test_all_aliases_accepted():
    """Every documented alias is routed to the context log as expected."""
    for key in ("text", "note", "message"):
        tool, context_log = _make_tool()
        value = f"reasoning via {key}"
        result = _run(tool.think(**{key: value}))
        assert result.success is True, f"alias '{key}' should succeed"
        context_log.add_thinking.assert_awaited_once_with(value)


def test_missing_reasoning_is_error_not_exception():
    """With no text at all, the tool returns a failure ToolResult
    (it must NOT raise 'unexpected keyword argument')."""
    tool, context_log = _make_tool()
    result = _run(tool.think())

    assert result.success is False
    assert result.error
    context_log.add_thinking.assert_not_awaited()


def test_first_alias_wins_on_conflict():
    """If multiple aliases are given, the canonical ``thought`` wins,
    then aliases are checked left-to-right."""
    tool, context_log = _make_tool()
    result = _run(tool.think(thought="canonical", content="ignored"))
    assert result.success is True
    context_log.add_thinking.assert_awaited_once_with("canonical")
