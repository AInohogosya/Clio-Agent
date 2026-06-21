"""Unit tests for shell execution and output truncation tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from neuro_scaffold.config.settings import Settings
from neuro_scaffold.tools.shell import OutputTruncator, ShellExecutor


class TestOutputTruncator:
    def test_no_truncation_needed(self) -> None:
        truncator = OutputTruncator(head_lines=5, tail_lines=5)
        output = "\n".join(f"line {i}" for i in range(8))
        result, truncated = truncator.truncate(output)
        assert not truncated
        assert result == output

    def test_truncation_applied(self) -> None:
        truncator = OutputTruncator(head_lines=3, tail_lines=3)
        output = "\n".join(f"line {i}" for i in range(20))
        result, truncated = truncator.truncate(output)
        assert truncated
        assert "[14 lines omitted]" in result
        assert "line 0" in result
        assert "line 19" in result

    def test_exact_boundary(self) -> None:
        truncator = OutputTruncator(head_lines=5, tail_lines=5)
        output = "\n".join(f"line {i}" for i in range(10))
        result, truncated = truncator.truncate(output)
        assert not truncated

    def test_one_over_boundary(self) -> None:
        truncator = OutputTruncator(head_lines=5, tail_lines=5)
        output = "\n".join(f"line {i}" for i in range(11))
        result, truncated = truncator.truncate(output)
        assert truncated

    def test_empty_string(self) -> None:
        truncator = OutputTruncator(head_lines=5, tail_lines=5)
        result, truncated = truncator.truncate("")
        assert not truncated
        assert result == ""

    def test_truncate_bytes(self) -> None:
        truncator = OutputTruncator(head_lines=2, tail_lines=2)
        data = b"\n".join(f"line {i}".encode() for i in range(10))
        result, truncated = truncator.truncate_bytes(data)
        assert truncated
        assert "line 0" in result
        assert "line 9" in result

    def test_truncate_bytes_with_invalid_utf8(self) -> None:
        truncator = OutputTruncator(head_lines=2, tail_lines=2)
        data = bytes(range(256)) + bytes(range(256))
        result, truncated = truncator.truncate_bytes(data)
        assert isinstance(result, str)


class TestShellExecutor:
    @pytest.fixture
    def executor(self, test_settings: Settings) -> ShellExecutor:
        return ShellExecutor(test_settings)

    @pytest.mark.asyncio
    async def test_simple_command(self, executor: ShellExecutor) -> None:
        result = await executor.execute("echo hello")
        assert result["success"] is True
        assert "hello" in result["output"]
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_command_with_cwd(self, executor: ShellExecutor, tmp_path: Path) -> None:
        result = await executor.execute("pwd", cwd=str(tmp_path))
        assert result["success"] is True
        assert str(tmp_path) in result["output"]

    @pytest.mark.asyncio
    async def test_failing_command(self, executor: ShellExecutor) -> None:
        result = await executor.execute("false")
        assert result["success"] is False
        assert result["exit_code"] != 0

    @pytest.mark.asyncio
    async def test_dry_run(self, executor: ShellExecutor) -> None:
        result = await executor.execute("rm -rf /", dry_run=True)
        assert result["success"] is True
        assert "[DRY RUN]" in result["output"]

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked(self, executor: ShellExecutor) -> None:
        result = await executor.execute("rm -rf / --no-preserve-root")
        assert result["success"] is False
        assert "blocked" in result["error"].lower() or "Dangerous" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout(self, executor: ShellExecutor) -> None:
        result = await executor.execute("sleep 60", timeout=1)
        assert result["success"] is False
        assert "timed out" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_output_truncation(self, test_settings: Settings) -> None:
        test_settings.shell_truncate_head = 2
        test_settings.shell_truncate_tail = 2
        executor = ShellExecutor(test_settings)
        result = await executor.execute("seq 1 100")
        assert result["truncated"] is True
        assert "[96 lines omitted]" in result["output"]

    @pytest.mark.asyncio
    async def test_duration_tracked(self, executor: ShellExecutor) -> None:
        result = await executor.execute("echo test")
        assert result["duration_ms"] >= 0
