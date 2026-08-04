"""
Unit tests for FileSearchTool.list_directory.

These tests cover:
  - Bug 1: the ``path`` keyword alias works in addition to ``directory``.
  - Bug 2: a (mocked) PermissionError during enumeration is surfaced in the
    tool's string output instead of being silently swallowed.

Run with:
    python3 -m pytest tests/test_tool_registry.py -v

(pytest-asyncio is NOT required; tests drive the coroutines via asyncio.run.)
"""

import asyncio
import tempfile
from pathlib import Path
from unittest import mock

from clio_agent_2.tools.tool_registry import (
    FileSearchTool,
    SayTool,
    ShellCommandTool,
    ToolRegistry,
)


def _run(coro):
    """Run an async coroutine to completion without pytest-asyncio."""
    return asyncio.run(coro)


def test_path_alias_works_like_directory():
    """Bug 1: passing ``path=`` should behave identically to ``directory=``."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()

        # Using the new ``path`` alias
        result_via_path = _run(FileSearchTool.list_directory(path=str(tmp_path)))
        # Using the original ``directory`` argument
        result_via_dir = _run(FileSearchTool.list_directory(directory=str(tmp_path)))

        assert result_via_path.success is True
        assert result_via_dir.success is True
        # Both should list the same two entries and an explicit count.
        assert "2 entries" in result_via_path.output
        assert result_via_path.output == result_via_dir.output
        assert "a.txt" in result_via_path.output
        assert "[DIR] subdir" in result_via_path.output


def test_empty_directory_reports_zero_entries():
    """Bug 2 (part 1): an empty directory must report '0 entries' explicitly."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _run(FileSearchTool.list_directory(directory=str(Path(tmp))))
        assert result.success is True
        assert "0 entries" in result.output


def test_permission_error_is_surfaced_in_output():
    """Bug 2 (part 2): a PermissionError must appear in the output string."""
    with tempfile.TemporaryDirectory() as tmp:
        # Force os.scandir to raise PermissionError, simulating an
        # unreadable directory.
        with mock.patch(
            "clio_agent_2.tools.tool_registry.os.scandir",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            result = _run(FileSearchTool.list_directory(directory=str(Path(tmp))))

        assert result.success is False
        assert "ACCESS DENIED" in result.output
        assert "PermissionError" in result.output
        assert "Permission denied" in result.output


def test_os_error_is_surfaced_in_output():
    """Bug 2 (part 2): an OSError must also be surfaced in the output string."""
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch(
            "clio_agent_2.tools.tool_registry.os.scandir",
            side_effect=OSError(5, "Input/output error"),
        ):
            result = _run(FileSearchTool.list_directory(directory=str(Path(tmp))))

        assert result.success is False
        assert "ACCESS DENIED" in result.output
        assert "OSError" in result.output


def test_missing_argument_returns_clear_error():
    """Neither ``directory`` nor ``path`` provided -> clear, friendly error."""
    result = _run(FileSearchTool.list_directory())
    assert result.success is False
    assert "directory" in result.error.lower()
    assert "path" in result.error.lower()


def test_shell_command_is_registered_with_common_aliases():
    registry = ToolRegistry()

    assert "shell_command" in registry.list_tools()
    assert "run_shell_command" in registry.list_tools()
    assert "execute_shell_command" in registry.list_tools()


def test_shell_command_runs_successfully():
    result = _run(ShellCommandTool.run_command(command="echo hello"))

    assert result.success is True
    assert "Exit code: 0" in result.output
    assert "hello" in result.output


def test_shell_command_accepts_cmd_alias():
    result = _run(ShellCommandTool.run_command(cmd="echo alias"))

    assert result.success is True
    assert "alias" in result.output


def test_shell_command_surfaces_nonzero_exit_code():
    result = _run(ShellCommandTool.run_command(command="bash -c 'exit 7'"))

    assert result.success is False
    assert "Exit code: 7" in result.output
    assert "code 7" in result.error


def test_say_command_is_registered_as_a_tool():
    """The 'say' command must be a real, discoverable registered tool."""
    registry = ToolRegistry()

    assert "say" in registry.list_tools()


def test_say_tool_returns_the_user_facing_message():
    """SayTool.say unwraps the message text (canonical + alias keywords)."""
    ok = _run(SayTool().say(message="Status: all good."))
    assert ok.success is True
    assert ok.output == "Status: all good."

    via_alias = _run(SayTool().say(text="via the text alias"))
    assert via_alias.success is True
    assert via_alias.output == "via the text alias"


def test_say_tool_requires_a_message():
    """With no message text the Say tool reports a clear error."""
    missing = _run(SayTool().say())
    assert missing.success is False
    assert "message" in missing.error.lower()


def test_say_tool_delivers_through_response_sink():
    """Executing the `say` tool delivers the message via its response sink."""
    delivered = []
    sink = lambda m: delivered.append(m)  # noqa: E731
    result = _run(SayTool(response_sink=sink).say(message="hi there"))
    assert result.success is True
    assert delivered == ["hi there"]
