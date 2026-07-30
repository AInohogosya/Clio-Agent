"""
Validation tests for the revamped tool-call parsing + feedback logic.

These exercise the bugs reported in the bug report:
  - Single tool call is still parsed.
  - A JSON ARRAY of tool calls (previously silently dropped) is now parsed.
  - Multiple separate JSON objects in prose (previously silently dropped)
    are now parsed.
  - Free-form text with no tool call returns an empty list.

Run with:
    python3 -m pytest tests/test_tool_parsing.py -v
"""

import asyncio
import sys
from pathlib import Path
from unittest import mock

# Make the project importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.tools.tool_registry import ToolRegistry, ToolResult


def _make_agent():
    """Build a ClioAgent without needing a live config/LLM."""
    agent = mock.MagicMock(spec=ClioAgent)
    agent._extract_json_objects = ClioAgent._extract_json_objects
    agent._is_valid_tool_call = ClioAgent._is_valid_tool_call
    agent._parse_tool_calls = ClioAgent._parse_tool_calls.__get__(agent, ClioAgent)
    return agent


def test_single_tool_call():
    agent = _make_agent()
    resp = '{"tool": "read_file", "arguments": {"filepath": "/tmp/x.txt"}}'
    calls = agent._parse_tool_calls(resp)
    assert len(calls) == 1
    assert calls[0]["tool"] == "read_file"
    assert calls[0]["arguments"]["filepath"] == "/tmp/x.txt"


def test_array_of_tool_calls():
    agent = _make_agent()
    resp = (
        '[{"tool": "read_file", "arguments": {"filepath": "/tmp/a.txt"}},'
        ' {"tool": "write_file", "arguments": {"filepath": "/tmp/b.txt",'
        ' "content": "hi"}}]'
    )
    calls = agent._parse_tool_calls(resp)
    assert len(calls) == 2
    assert calls[0]["tool"] == "read_file"
    assert calls[1]["tool"] == "write_file"


def test_multiple_separate_objects_in_prose():
    agent = _make_agent()
    resp = (
        "I'll do two things:\n"
        '{"tool": "read_file", "arguments": {"filepath": "/tmp/a.txt"}}\n'
        "and then:\n"
        '{"tool": "list_directory", "arguments": {"directory": "/tmp"}}'
    )
    calls = agent._parse_tool_calls(resp)
    assert len(calls) == 2
    names = {c["tool"] for c in calls}
    assert names == {"read_file", "list_directory"}


def test_no_tool_call_returns_empty():
    agent = _make_agent()
    calls = agent._parse_tool_calls("Just a normal answer, no tools needed.")
    assert calls == []


def test_braces_inside_strings_are_ignored():
    agent = _make_agent()
    # The literal "}" inside the path string must not break extraction.
    resp = '{"tool": "read_file", "arguments": {"filepath": "a}b.txt"}}'
    calls = agent._parse_tool_calls(resp)
    assert len(calls) == 1
    assert calls[0]["arguments"]["filepath"] == "a}b.txt"


def test_start_autonomous_loop_if_enabled_respects_autonomous_mode():
    agent = _make_agent()
    agent.autonomous_mode = False
    called = False

    async def fake_start():
        nonlocal called
        called = True
        return True

    agent.start_autonomous_loop = fake_start
    agent.ensure_autonomous_loop = ClioAgent.ensure_autonomous_loop.__get__(
        agent, ClioAgent
    )
    agent.start_autonomous_loop_if_enabled = (
        ClioAgent.start_autonomous_loop_if_enabled.__get__(agent, ClioAgent)
    )

    # Should NOT start the loop when autonomous_mode is False
    assert _run(agent.start_autonomous_loop_if_enabled()) is False
    assert called is False


def _run(coro):
    return asyncio.run(coro)


async def _fake_execute_tool(tool_name, arguments):
    """Stand-in for ToolRegistry.execute_tool that signals success/failure."""
    if tool_name == "boom":
        return ToolResult(False, "", f"Simulated failure for {tool_name}")
    return ToolResult(True, f"ok: {tool_name}")


def test_feedback_surfaces_error_string():
    """
    Reproduces the process_message tool branch: a failed tool must expose its
    error (not an empty string) and a successful tool must expose its output.
    """
    async def scenario():
        registry = mock.MagicMock(spec=ToolRegistry)
        registry.execute_tool = _fake_execute_tool

        tool_calls = [
            {"tool": "read_file", "arguments": {"filepath": "/tmp/a.txt"}},
            {"tool": "boom", "arguments": {}},
        ]

        feedback_parts = []
        for tool_call in tool_calls:
            tool_name = tool_call.get("tool")
            arguments = tool_call.get("arguments", {})
            result = await registry.execute_tool(tool_name, arguments)
            if result.success:
                feedback_parts.append(f"[TOOL OK] {tool_name}\n{result.output}")
            else:
                error_detail = result.error or "Unknown error (no details provided)"
                feedback_parts.append(
                    f"[TOOL FAILED] {tool_name}\nError: {error_detail}"
                )
        return "\n\n".join(feedback_parts)

    combined = _run(scenario())
    assert "[TOOL OK] read_file\nok: read_file" in combined
    assert "[TOOL FAILED] boom" in combined
    assert "Simulated failure for boom" in combined
    # The failure message is NOT empty -> failure is distinguishable from success.
    assert combined.strip() != ""
