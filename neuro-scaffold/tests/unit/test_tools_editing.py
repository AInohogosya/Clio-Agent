"""Unit tests for smart editing tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from neuro_scaffold.tools.editing import DiffValidator, SmartEditor


class TestDiffValidator:
    def test_valid_unified_diff(self) -> None:
        validator = DiffValidator()
        patch = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,3 +1,3 @@\n"
            " line1\n"
            "-line2\n"
            "+line2_modified\n"
            " line3\n"
        )
        patches = validator.validate_patch(patch)
        assert len(patches) == 1
        assert patches[0].old_path == "a/file.py"
        assert patches[0].new_path == "b/file.py"
        assert len(patches[0].hunks) == 1

    def test_empty_patch_raises(self) -> None:
        validator = DiffValidator()
        with pytest.raises(Exception):
            validator.validate_patch("")

    def test_invalid_line_raises(self) -> None:
        validator = DiffValidator()
        patch = (
            "--- a/file.py\n"
            "+++ b/file.py\n"
            "@@ -1,2 +1,2 @@\n"
            " line1\n"
            "invalid_line\n"
        )
        with pytest.raises(Exception):
            validator.validate_patch(patch)

    def test_generate_unified_diff(self) -> None:
        validator = DiffValidator()
        old = "line1\nline2\nline3\n"
        new = "line1\nline2_modified\nline3\n"
        diff = validator.generate_unified_diff(old, new, "a.py", "b.py")
        assert "--- a.py" in diff
        assert "+++ b.py" in diff
        assert "-line2" in diff
        assert "+line2_modified" in diff

    def test_new_file_patch(self) -> None:
        validator = DiffValidator()
        patch = (
            "--- /dev/null\n"
            "+++ b/new_file.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+line1\n"
            "+line2\n"
        )
        patches = validator.validate_patch(patch)
        assert patches[0].is_new_file is True

    def test_deletion_patch(self) -> None:
        validator = DiffValidator()
        patch = (
            "--- a/old_file.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-line1\n"
            "-line2\n"
        )
        patches = validator.validate_patch(patch)
        assert patches[0].is_deletion is True


class TestSmartEditor:
    def test_read_file(self, sample_python_file: Path) -> None:
        editor = SmartEditor()
        result = editor.read_file(str(sample_python_file))
        assert result["success"] is True
        assert "def hello" in result["content"]

    def test_read_file_with_line_range(self, sample_python_file: Path) -> None:
        editor = SmartEditor()
        result = editor.read_file(str(sample_python_file), start_line=1, end_line=3)
        assert result["success"] is True
        lines = result["content"].splitlines()
        assert len(lines) == 3

    def test_read_nonexistent_file(self) -> None:
        editor = SmartEditor()
        result = editor.read_file("/nonexistent/file.py")
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_write_file(self, tmp_path: Path) -> None:
        editor = SmartEditor()
        file_path = tmp_path / "new_file.py"
        result = editor.write_file(str(file_path), "print('hello')\n")
        assert result["success"] is True
        assert file_path.read_text() == "print('hello')\n"

    def test_write_creates_directories(self, tmp_path: Path) -> None:
        editor = SmartEditor()
        file_path = tmp_path / "subdir" / "nested" / "file.py"
        result = editor.write_file(str(file_path), "# test\n")
        assert result["success"] is True
        assert file_path.exists()

    def test_search_replace_single(self, sample_python_file: Path) -> None:
        editor = SmartEditor()
        result = editor.search_replace(
            str(sample_python_file),
            'return f"Hello, {name}!"',
            'return f"Hi, {name}!"',
        )
        assert result["success"] is True
        assert result["replacements"] == 1
        content = sample_python_file.read_text()
        assert 'f"Hi, {name}!"' in content

    def test_search_replace_all(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.py"
        file_path.write_text("foo\nbar\nfoo\nbaz\nfoo\n")
        editor = SmartEditor()
        result = editor.search_replace(str(file_path), "foo", "qux", replace_all=True)
        assert result["success"] is True
        assert result["replacements"] == 3
        content = file_path.read_text()
        assert content.count("qux") == 3
        assert "foo" not in content

    def test_search_replace_not_found(self, sample_python_file: Path) -> None:
        editor = SmartEditor()
        result = editor.search_replace(
            str(sample_python_file),
            "nonexistent_string",
            "replacement",
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_search_replace_multiple_without_flag(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.py"
        file_path.write_text("foo\nfoo\n")
        editor = SmartEditor()
        result = editor.search_replace(str(file_path), "foo", "bar", replace_all=False)
        assert result["success"] is False
        assert "2" in result["error"]

    def test_apply_patch(self, sample_python_file: Path) -> None:
        editor = SmartEditor()
        patch = (
            f"--- {sample_python_file}\n"
            f"+++ {sample_python_file}\n"
            "@@ -1,3 +1,3 @@\n"
            " def hello(name: str) -> str:\n"
            '     """Say hello."""\n'
            '-    return f"Hello, {name}!"\n'
            '+    return f"Hi there, {name}!"\n'
        )
        result = editor.apply_patch(patch)
        assert result["success"] is True
        assert result["files_modified"] == 1
        content = sample_python_file.read_text()
        assert "Hi there" in content
