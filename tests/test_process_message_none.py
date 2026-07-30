"""
Regression tests for ``ClioAgent.process_message``.

These cover the bug reported as::

    [Telegram] Error: object of type 'NoneType' has no len()

The agent's interfaces (Telegram, Discord, CLI) call ``len()`` on the value
returned by ``process_message``. When the configured LLM returns an empty
completion (``null`` content), ``process_message`` used to return ``None``,
which crashed those interfaces. ``process_message`` must therefore ALWAYS
return a string, never ``None``.
"""

import asyncio
from unittest import mock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.tools.tool_registry import ToolResult


def _run(coro):
    return asyncio.run(coro)


def _make_agent(chat_side_effect, execute_result=None):
    """Build a ClioAgent whose dependencies are mocked.

    ``chat_side_effect`` is passed straight to ``AsyncMock(side_effect=...)``
    so the test controls what ``llm_router.chat`` returns on each call.
    """
    agent = mock.MagicMock(spec=ClioAgent)
    agent.BASE_SYSTEM_PROMPT = "system"

    context_log = mock.MagicMock()
    context_log.get_entries_as_messages.return_value = []
    context_log.add_user_message = mock.AsyncMock()
    context_log.add_assistant_response = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
    agent.context_log = context_log

    llm_router = mock.MagicMock()
    llm_router.chat = mock.AsyncMock(side_effect=chat_side_effect)
    agent.llm_router = llm_router

    if execute_result is not None:
        tool_registry = mock.MagicMock()
        tool_registry.execute_tool = mock.AsyncMock(return_value=execute_result)
        agent.tool_registry = tool_registry

    # Bind the REAL methods so we exercise production code, not mocks.
    agent._parse_tool_calls = ClioAgent._parse_tool_calls.__get__(agent, ClioAgent)
    agent._extract_json_objects = ClioAgent._extract_json_objects
    agent._is_valid_tool_call = ClioAgent._is_valid_tool_call
    agent.process_message = ClioAgent.process_message.__get__(agent, ClioAgent)

    # Bind the new delegate methods introduced by the multi-turn refactor so the
    # real production logic runs end-to-end (not a MagicMock shim).
    for _m in (
        "_system_block",
        "_build_context_messages",
        "_execute_tool_round",
        "_run_agent_turn",
    ):
        setattr(agent, _m, getattr(ClioAgent, _m).__get__(agent, ClioAgent))
    return agent


def test_none_completion_returns_string_not_none():
    """A ``None`` completion must not be returned to the caller."""
    agent = _make_agent(chat_side_effect=[None])
    result = _run(agent.process_message("hello"))
    assert result is not None
    assert isinstance(result, str)


def test_normal_completion_is_returned_unchanged():
    """A plain completion is no longer surfaced as a reply (reply system
    removed); process_message returns an empty string and never None."""
    agent = _make_agent(chat_side_effect=["Hi there!"])
    result = _run(agent.process_message("hello"))
    assert result == ""
    assert isinstance(result, str)


def test_none_final_response_after_tool_call_returns_string():
    """After a tool call, an empty second completion must not surface ``None``.

    This reproduces the second crash site (``final_response[:500]`` on a
    ``None`` value) and verifies the guard returns a real string instead.
    """
    tool_call = '{"tool": "read_file", "arguments": {"filepath": "/tmp/x.txt"}}'
    agent = _make_agent(
        chat_side_effect=[tool_call, None],
        execute_result=ToolResult(True, "file contents"),
    )
    result = _run(agent.process_message("read the file"))
    assert result is not None
    assert isinstance(result, str)
