"""
Extended tests for ShellCommandTool.run_command.
Covers cwd, timeout, max_output_chars, error handling, non-timeout failures.
(existing test_retry.py covers the timeout retry behavior)
"""
import asyncio
import tempfile
from pathlib import Path
from unittest import mock

from clio_agent_2.tools.tool_registry import ShellCommandTool, ToolResult


def _run(coro):
    return asyncio.run(coro)


class TestShellCommandToolCwd:
    """Tests for ShellCommandTool with cwd parameter"""

    def test_run_command_with_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(ShellCommandTool.run_command(
                command="pwd",
                cwd=tmp
            ))
            assert result.success is True
            assert tmp in result.output

    def test_run_command_with_cwd_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "testdir").mkdir()

            result = _run(ShellCommandTool.run_command(
                command="pwd",
                cwd="testdir"
            ))
            # This will fail if the cwd doesn't exist at the test execution level
            # but the tool should resolve relative paths

    def test_run_command_nonexistent_cwd(self):
        result = _run(ShellCommandTool.run_command(
            command="pwd",
            cwd="/nonexistent/directory/that/does/not/exist"
        ))
        assert result.success is False
        assert "cwd does not exist" in result.error

    def test_run_command_cwd_is_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "file.txt"
            file_path.write_text("test")

            result = _run(ShellCommandTool.run_command(
                command="pwd",
                cwd=str(file_path)
            ))
            assert result.success is False
            assert "cwd is not a directory" in result.error

    def test_run_command_cwd_expands_user(self):
        result = _run(ShellCommandTool.run_command(
            command="pwd",
            cwd="~"
        ))
        # Should not crash even if home directory differs


class TestShellCommandToolMaxOutputChars:
    """Tests for ShellCommandTool max_output_chars parameter"""

    def test_output_truncation(self):
        result = _run(ShellCommandTool.run_command(
            command="printf '%0.sx' {1..10000}",
            max_output_chars=500
        ))
        assert result.success is True
        assert "truncated" in result.output.lower()

    def test_default_max_output_chars_does_not_truncate(self):
        # Small output should not be truncated
        result = _run(ShellCommandTool.run_command(
            command="printf 'short output'"
        ))
        assert result.success is True
        assert "truncated" not in result.output.lower()

    def test_custom_max_output_chars(self):
        result = _run(ShellCommandTool.run_command(
            command="printf '%0.sx' {1..2000}",
            max_output_chars=1000
        ))
        assert result.success is True
        assert "truncated" in result.output.lower()


class TestShellCommandToolErrorHandling:
    """Tests for ShellCommandTool error handling"""

    def test_non_timeout_failure_not_retried(self):
        """A command that fails with non-zero exit should not be retried"""
        result = _run(ShellCommandTool.run_command(command="exit 3"))

        assert result.success is False
        assert "Exit code: 3" in result.output
        assert "code 3" in result.error

    def test_command_with_stderr_output(self):
        result = _run(ShellCommandTool.run_command(
            command="echo 'to stderr' >&2; echo 'to stdout'"
        ))

        assert result.success is True
        assert "to stdout" in result.output
        assert "to stderr" in result.output
        assert "STDERR" in result.output

    def test_command_no_output(self):
        result = _run(ShellCommandTool.run_command(command="true"))

        assert result.success is True
        assert "Exit code: 0" in result.output
        assert "(no output)" in result.output

    def test_command_with_newlines_in_output(self):
        result = _run(ShellCommandTool.run_command(
            command="printf 'line1\nline2\nline3'"
        ))

        assert result.success is True
        assert "line1" in result.output
        assert "line2" in result.output
        assert "line3" in result.output


class TestShellCommandToolTimeout:
    """Tests for ShellCommandTool timeout behavior"""

    def test_timeout_parameter_is_applied(self):
        """Ensure the timeout parameter works (without triggering actual timeout)"""
        result = _run(ShellCommandTool.run_command(
            command="echo hello",
            timeout=30
        ))
        assert result.success is True
        assert "Exit code: 0" in result.output

    def test_invalid_timeout_type_raises(self):
        result = _run(ShellCommandTool.run_command(
            command="echo hello",
            timeout="invalid"
        ))
        assert result.success is False
        assert "must be integers" in result.error

    def test_none_timeout_raises(self):
        result = _run(ShellCommandTool.run_command(
            command="echo hello",
            timeout=None
        ))
        assert result.success is False
        assert "must be integers" in result.error


class TestShellCommandToolConstants:
    """Tests for ShellCommandTool constants"""

    def test_default_timeout(self):
        assert ShellCommandTool.DEFAULT_TIMEOUT == 30

    def test_default_max_output_chars(self):
        assert ShellCommandTool.DEFAULT_MAX_OUTPUT_CHARS == 12000

    def test_max_command_attempts(self):
        assert ShellCommandTool.MAX_COMMAND_ATTEMPTS == 5


class TestShellCommandToolCommandAlias:
    """Tests for command/cmd alias"""

    def test_cmd_alias(self):
        result = _run(ShellCommandTool.run_command(cmd="echo via_cmd_alias"))
        assert result.success is True
        assert "via_cmd_alias" in result.output

    def test_command_alias(self):
        result = _run(ShellCommandTool.run_command(command="echo via_command_alias"))
        assert result.success is True
        assert "via_command_alias" in result.output

    def test_missing_both_command_and_cmd(self):
        result = _run(ShellCommandTool.run_command())
        assert result.success is False
        assert "Missing required argument" in result.error

    def test_empty_command(self):
        result = _run(ShellCommandTool.run_command(command=""))
        assert result.success is False
        assert "Missing required argument" in result.error

    def test_whitespace_command(self):
        result = _run(ShellCommandTool.run_command(command="   "))
        assert result.success is False
        assert "Missing required argument" in result.error


class TestShellCommandToolMaxAttempts:
    """Tests for ShellCommandTool retry logic"""

    def test_max_attempts_constant(self):
        """Verify MAX_COMMAND_ATTEMPTS is a reasonable value"""
        assert ShellCommandTool.MAX_COMMAND_ATTEMPTS >= 1

    def test_timeout_error_mentions_retries(self):
        """When command times out after all retries, error should mention retries"""
        with mock.patch.object(ShellCommandTool, "MAX_COMMAND_ATTEMPTS", 2):
            # Mock to force timeout immediately
            original_create = asyncio.create_subprocess_shell

            class _FakeProcess:
                def kill(self):
                    pass
                async def communicate(self):
                    return b"", b""

            async def _timeout(coro, timeout):
                coro.close()
                raise asyncio.TimeoutError()

            async def _fake_create(*args, **kwargs):
                return _FakeProcess()

            asyncio.wait_for = _timeout
            asyncio.create_subprocess_shell = _fake_create

            try:
                result = _run(ShellCommandTool.run_command(command="sleep 999", timeout=1))
            finally:
                # Restore
                import asyncio as aio
                pass

            assert result.success is False
            assert "timed out" in result.error.lower()
            assert "retried" in result.error.lower()