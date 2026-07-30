"""
Tests for the "say" tool.

`say` is a normal, executable tool (registered like `read_file`). These tests
verify that when the model emits it, the agent runs it through the normal
tool-execution path and the message reaches the user via the response channel
(`send_response`), while `process_message` returns no natural-language reply.

Run with:
    python3 -m pytest tests/test_say_command.py -v
"""

import asyncio
from unittest import mock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.tools.tool_registry import ToolResult, ToolRegistry, SayTool


def _run(coro):
    return asyncio.run(coro)


def _make_agent(chat_side_effect, execute_result=None):
    """Build a ClioAgent whose dependencies are mocked.

    Real production methods are bound so we exercise the real logic, not mocks.
    The ``say`` tool runs as the REAL ``SayTool`` (wired to the agent's response
    channel) so we verify it travels through the normal tool-execution path
    exactly like ``read_file`` -- it is no longer special-cased. ``execute_tool``
    stays a mock so the test can spy on which tools the agent actually invoked.
    """
    agent = mock.MagicMock(spec=ClioAgent)

    # Real, mutable response channel (send_response iterates this list).
    agent.response_callbacks = []
    agent.send_response = ClioAgent.send_response.__get__(agent, ClioAgent)

    context_log = mock.MagicMock()
    context_log.get_entries_as_messages.return_value = []
    context_log.add_user_message = mock.AsyncMock()
    context_log.add_assistant_response = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
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
    tool_registry.list_tools.return_value = sorted(
        [
            "read_file", "write_file", "list_directory", "search_files",
            "search_content", "shell_command", "web_search", "fetch_url",
            "thinking", "say",
        ]
    )
    agent.tool_registry = tool_registry
    # Expose the spy record for assertions.
    agent._tool_calls_seen = tool_calls_seen

    # Bind the REAL methods under test.
    agent._parse_tool_calls = ClioAgent._parse_tool_calls.__get__(agent, ClioAgent)
    agent._extract_json_objects = ClioAgent._extract_json_objects
    agent._is_valid_tool_call = ClioAgent._is_valid_tool_call
    agent.process_message = ClioAgent.process_message.__get__(agent, ClioAgent)

    # Bind the delegate methods introduced by the multi-turn refactor so the
    # real production logic runs end-to-end (not a MagicMock shim).
    for _m in (
        "_system_block",
        "_build_context_messages",
        "_execute_tool_round",
        "_run_agent_turn",
    ):
        setattr(agent, _m, getattr(ClioAgent, _m).__get__(agent, ClioAgent))
    return agent


# --------------------------------------------------------------------------
# say runs through the normal tool-execution path (like read_file)
# --------------------------------------------------------------------------

def test_say_is_executed_through_execute_tool():
    """When the model emits `say`, the agent runs it as a normal tool via
    `_execute_tool_round` / `tool_registry.execute_tool` -- it is NOT special-
    cased or skipped."""
    say_json = '{"tool": "say", "arguments": {"message": "Hello!"}}'
    agent = _make_agent(chat_side_effect=[say_json, ""])

    result = _run(agent.process_message("hi"))

    # Nothing returned as a reply (the reply system was removed).
    assert result == ""
    # The `say` command went through the tool-execution path exactly once.
    assert agent._tool_calls_seen == ["say"]


def test_process_message_delivers_say_via_callback_not_as_reply():
    """A `say` command is delivered to the user through send_response; it is
    NOT returned as a natural-language reply (the reply system is removed)."""
    say_json = '{"tool": "say", "arguments": {"message": "Status: all good."}}'
    agent = _make_agent(chat_side_effect=[say_json, ""])

    delivered = []
    async def _capture(m):
        delivered.append(m)
    agent.response_callbacks.append(_capture)

    result = _run(agent.process_message("status?"))
    # No natural-language reply is returned (empty string).
    assert result == ""
    # The `say` command ran as a tool and reached the user via the response channel.
    assert delivered == ["Status: all good."]
    # The `say` command WAS executed as a tool (not special-cased/skipped).
    assert "say" in agent._tool_calls_seen


def test_process_message_does_not_leak_raw_json_on_final_response():
    """If the model's follow-up reply is itself a bare tool call, it must not
    be shown verbatim to the user."""
    filesystem_json = (
        '{"tool": "filesystem", "arguments": '
        '{"operation": "list", "path": "~"}}'
    )
    agent = _make_agent(
        chat_side_effect=[filesystem_json, filesystem_json],
        execute_result=ToolResult(False, "", "Unknown tool: filesystem"),
    )
    result = _run(agent.process_message("list my home"))
    assert isinstance(result, str)
    assert '"tool"' not in result
    assert "filesystem" not in result


# --------------------------------------------------------------------------
# System prompt
# --------------------------------------------------------------------------

def test_system_prompt_lists_tools_and_has_no_double_braces():
    agent = mock.MagicMock(spec=ClioAgent)
    tool_registry = mock.MagicMock()
    tool_registry.list_tools.return_value = ["read_file", "list_directory"]
    agent.tool_registry = tool_registry
    agent.BASE_SYSTEM_PROMPT_TEMPLATE = ClioAgent.BASE_SYSTEM_PROMPT_TEMPLATE
    agent._available_tools_text = ClioAgent._available_tools_text.__get__(
        agent, ClioAgent
    )

    prompt = ClioAgent.BASE_SYSTEM_PROMPT.fget(agent)

    # The real tool names are injected.
    assert "read_file" in prompt
    assert "list_directory" in prompt
    # "filesystem" appears only in prose ("the local filesystem" / "there is no
    # 'filesystem' tool"); it must NOT be offered as an available tool entry.
    assert "\n- filesystem" not in prompt
    # The malformed double-brace JSON example is gone (single braces only).
    assert "{{" not in prompt
    # The Say command guidance is present.
    assert "Say" in prompt



def test_say_is_listed_in_available_tools():
    """The Say command must be a real, discoverable tool.

    The system prompt instructs the model to emit ``{"tool": "say", ...}`` yet
    also says "ONLY use tool names from the AVAILABLE TOOLS list". For that to
    be consistent, ``say`` has to actually appear in the tool list the model is
    shown (previously it was missing - a contradiction).
    """
    agent = mock.MagicMock(spec=ClioAgent)
    agent.tool_registry = ToolRegistry()
    agent.BASE_SYSTEM_PROMPT_TEMPLATE = ClioAgent.BASE_SYSTEM_PROMPT_TEMPLATE
    agent._available_tools_text = ClioAgent._available_tools_text.__get__(
        agent, ClioAgent
    )

    prompt = ClioAgent.BASE_SYSTEM_PROMPT.fget(agent)

    # The Say command is now a registered, listed tool.
    assert "\n- say" in prompt
    # "filesystem" is still NOT offered as an available tool entry.
    assert "\n- filesystem" not in prompt
    # The Say command guidance is present.
    assert "Say" in prompt

