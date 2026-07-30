"""
Tests for the macro-architectural refactor in Clio-Agent-2:

1. Multi-turn tool execution -- ``process_message`` keeps executing tool calls
   until the model stops requesting them (previously only ONE follow-up round
   was allowed; further tool calls were silently dropped).
2. Runaway guardrail -- ``MAX_TOOL_ITERATIONS`` caps the loop so a model that
   emits tool calls forever is stopped safely.
3. Circuit breaker -- the autonomous loop pauses and preserves context after
   repeated failures, instead of wiping memory.
"""

import asyncio
from unittest import mock

from clio_agent_2.core.agent import (
    MAX_TOOL_ITERATIONS,
    ClioAgent,
)
from clio_agent_2.tools.tool_registry import ToolResult


def _run(coro):
    return asyncio.run(coro)


def _make_agent(chat_side_effect, execute_result=None):
    """Build a ClioAgent whose dependencies are mocked but whose real logic runs."""
    agent = mock.MagicMock(spec=ClioAgent)
    agent.response_callbacks = []

    context_log = mock.MagicMock()
    context_log.get_entries_as_messages.return_value = []
    context_log.add_user_message = mock.AsyncMock()
    context_log.add_assistant_response = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
    context_log.working_summary = ""
    agent.context_log = context_log

    llm_router = mock.MagicMock()
    llm_router.chat = mock.AsyncMock(side_effect=chat_side_effect)
    agent.llm_router = llm_router

    tool_registry = mock.MagicMock()
    tool_registry.execute_tool = mock.AsyncMock(
        return_value=execute_result
        or ToolResult(False, "", "Unknown tool")
    )
    agent.tool_registry = tool_registry

    agent.BASE_SYSTEM_PROMPT = "system"

    # Bind the REAL instance methods under test (these take ``self``).
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

    # Static methods: assign the plain function (no ``self`` binding).
    for name in ("_is_valid_tool_call", "_extract_json_objects"):
        setattr(agent, name, getattr(ClioAgent, name))

    return agent


def test_multi_turn_tool_execution_runs_all_rounds():
    """Two consecutive tool-call rounds are executed, not dropped."""
    round1 = '{"tool": "read_file", "arguments": {"filepath": "a.txt"}}'
    round2 = '{"tool": "read_file", "arguments": {"filepath": "b.txt"}}'
    agent = _make_agent(
        chat_side_effect=[round1, round2, "All done."],
        execute_result=ToolResult(True, "file contents"),
    )
    result = _run(agent.process_message("read both"))
    # Both tool calls executed across two rounds.
    assert agent.tool_registry.execute_tool.await_count == 2
    # The reply system is removed: no natural-language reply is returned.
    assert result == ""


def test_runaway_guardrail_caps_iterations():
    """A model emitting tool calls forever is stopped at MAX_TOOL_ITERATIONS."""
    forever = '{"tool": "read_file", "arguments": {"filepath": "x"}}'
    # Always returns a tool call -> would loop infinitely without the guard.
    agent = _make_agent(
        chat_side_effect=[forever] * (MAX_TOOL_ITERATIONS + 3),
        execute_result=ToolResult(True, "ok"),
    )
    result = _run(agent.process_message("loop me"))
    # Exactly MAX_TOOL_ITERATIONS tool executions, then the loop breaks.
    assert agent.tool_registry.execute_tool.await_count == MAX_TOOL_ITERATIONS
    assert isinstance(result, str)


def test_circuit_breaker_pauses_without_wiping_context():
    """After N failed cycles the loop pauses and keeps its context log."""
    agent = _make_agent(chat_side_effect=[Exception("provider down")])
    agent.thinking_interval = 0.0

    delivered = []
    async def _capture(m):
        delivered.append(m)
    agent.response_callbacks.append(_capture)

    _run(agent.run_autonomous_loop())

    # Loop stopped and did NOT call context_log.clear().
    assert agent.is_running is False
    agent.context_log.clear.assert_not_called()
    # Operator was notified about the tripped circuit breaker.
    assert any("Circuit breaker tripped" in m for m in delivered)
