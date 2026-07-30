"""
Tests that ``autonomous_think`` delivers Say-command messages to the user.

The autonomous loop calls ``autonomous_think`` on a timer (every
``thinking_interval`` seconds). Before this wiring, ``send_response`` was
never invoked, so a model that emitted a Say command each cycle produced
nothing user-visible. Now each cycle that explicitly addresses the user via
the Say command pushes one message out through the registered response
callbacks, so multiple user-facing messages can accumulate over time (one
per cycle).
"""

import asyncio
from unittest import mock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.tools.tool_registry import SayTool, ToolResult


def _run(coro):
    return asyncio.run(coro)


def _make_agent(chat_side_effect, execute_result=None):
    """Build a ClioAgent whose dependencies are mocked but whose real logic runs."""
    agent = mock.MagicMock(spec=ClioAgent)

    # Real, mutable list of response callbacks (send_response iterates this).
    agent.response_callbacks = []
    agent.send_response = ClioAgent.send_response.__get__(agent, ClioAgent)

    context_log = mock.MagicMock()
    context_log.get_entries_as_messages.return_value = []
    context_log.add_user_message = mock.AsyncMock()
    context_log.add_assistant_response = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
    context_log.add_thinking = mock.AsyncMock()
    agent.context_log = context_log

    llm_router = mock.MagicMock()
    llm_router.chat = mock.AsyncMock(side_effect=chat_side_effect)
    agent.llm_router = llm_router

    # The `say` tool runs for real (delivered via send_response); any other tool
    # name returns the provided `execute_result` (or an unknown-tool result).
    say_tool = SayTool(agent.context_log, agent.send_response)
    tool_calls_seen = []

    async def _execute(name, args):
        tool_calls_seen.append(name)
        if name == "say":
            return await say_tool.say(**args)
        return execute_result or ToolResult(False, "", f"Unknown tool: {name}")

    tool_registry = mock.MagicMock()
    tool_registry.execute_tool = mock.AsyncMock(side_effect=_execute)
    agent.tool_registry = tool_registry
    agent._tool_calls_seen = tool_calls_seen

    # Bind the REAL instance methods under test (these take ``self``).
    for name in (
        "_parse_tool_calls",
        "send_response",
        "autonomous_think",
    ):
        method = getattr(ClioAgent, name)
        setattr(agent, name, method.__get__(agent, ClioAgent))

    # Static methods: assign the plain function. MagicMock returns it as-is,
    # and it must NOT receive ``self`` (matching test_say_command.py).
    for name in ("_is_valid_tool_call", "_extract_json_objects"):
        setattr(agent, name, getattr(ClioAgent, name))


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


def test_autonomous_think_delivers_say_to_user():
    agent = _make_agent(
        chat_side_effect=[
            '{"tool": "say", "arguments": {"message": "Hello from the loop"}}',
            "",
        ]
    )

    delivered = []
    async def _capture(m):
        delivered.append(m)
    agent.response_callbacks.append(_capture)

    result = _run(agent.autonomous_think())

    # No natural-language reply is returned; the say was delivered via the
    # response channel (see `delivered` below).
    assert result == ""
    # The Say message reached the user through send_response.
    assert delivered == ["Hello from the loop"]
    # The Say command is executed as a normal tool (not special-cased/skipped).
    assert "say" in agent._tool_calls_seen


def test_autonomous_think_does_not_deliver_plain_thought():
    agent = _make_agent(chat_side_effect=["Just thinking out loud."])

    delivered = []
    async def _capture(m):
        delivered.append(m)
    agent.response_callbacks.append(_capture)

    result = _run(agent.autonomous_think())

    # Internal monologue is recorded, never broadcast.
    assert delivered == []
    # The reply system is removed: a plain thought is not returned.
    assert result == ""


def test_autonomous_think_delivers_say_and_executes_real_tool():
    agent = _make_agent(
        chat_side_effect=[
            '{"tool": "say", "arguments": {"message": "Starting now"}}'
            '\n{"tool": "read_file", "arguments": {"filepath": "x.txt"}}',
            "Done reading.",
        ],
        execute_result=ToolResult(True, "file contents"),
    )

    delivered = []
    async def _capture(m):
        delivered.append(m)
    agent.response_callbacks.append(_capture)

    result = _run(agent.autonomous_think())

    # The Say message is delivered even though a real tool also ran.
    assert delivered == ["Starting now"]
    # Both `say` and the real tool were executed through the tool path.
    assert "say" in agent._tool_calls_seen
    assert "read_file" in agent._tool_calls_seen
    agent.tool_registry.execute_tool.assert_awaited()
