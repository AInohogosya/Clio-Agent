"""Unit tests for linter and syntax checker."""

from __future__ import annotations

from pathlib import Path

import pytest

from neuro_scaffold.tools.linter import LinterChecker


class TestLinterChecker:
    @pytest.fixture
    def checker(self) -> LinterChecker:
        return LinterChecker(default_timeout=10.0)

    @pytest.mark.asyncio
    async def test_valid_python(self, checker: LinterChecker, sample_python_file: Path) -> None:
        result = await checker.check_file(str(sample_python_file))
        assert result.has_errors is False
        assert result.files_checked == 1

    @pytest.mark.asyncio
    async def test_invalid_python(self, checker: LinterChecker, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(\n    pass\n")
        result = await checker.check_file(str(bad_file))
        assert result.has_errors is True
        assert result.error_count >= 1

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, checker: LinterChecker) -> None:
        result = await checker.check_file("/nonexistent/file.py")
        assert result.has_errors is True

    @pytest.mark.asyncio
    async def test_valid_json(self, checker: LinterChecker, tmp_path: Path) -> None:
        json_file = tmp_path / "test.json"
        json_file.write_text('{"key": "value"}\n')
        result = await checker.check_file(str(json_file))
        assert result.has_errors is False

    @pytest.mark.asyncio
    async def test_invalid_json(self, checker: LinterChecker, tmp_path: Path) -> None:
        json_file = tmp_path / "bad.json"
        json_file.write_text('{"key": value}')
        result = await checker.check_file(str(json_file))
        assert result.has_errors is True

    @pytest.mark.asyncio
    async def test_check_directory(self, checker: LinterChecker, sample_project: Path) -> None:
        result = await checker.check_directory(str(sample_project), extensions=[".py", ".json"])
        assert result.files_checked >= 3
        assert result.has_errors is False

    @pytest.mark.asyncio
    async def test_check_directory_with_errors(self, checker: LinterChecker, tmp_path: Path) -> None:
        (tmp_path / "good.py").write_text("x = 1\n")
        (tmp_path / "bad.py").write_text("def broken(\n")
        result = await checker.check_directory(str(tmp_path), extensions=[".py"])
        assert result.has_errors is True
        assert result.files_checked == 2

    @pytest.mark.asyncio
    async def test_check_non_directory(self, checker: LinterChecker) -> None:
        result = await checker.check_directory("/nonexistent/dir")
        assert result.has_errors is True

    @pytest.mark.asyncio
    async def test_duration_tracked(self, checker: LinterChecker, sample_python_file: Path) -> None:
        result = await checker.check_file(str(sample_python_file))
        assert result.duration_ms >= 0
