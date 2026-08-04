"""
Tests for the FileEditTool class.
Covers read_file, write_file, append_file, and edit_file methods.
"""
import asyncio
import tempfile
import pytest
from pathlib import Path
from unittest import mock

from clio_agent_2.tools.tool_registry import FileEditTool, ToolResult


@pytest.fixture(autouse=True)
def _reset_file_sandbox():
    FileEditTool.sandbox_root = None
    yield


def _run(coro):
    return asyncio.run(coro)


class TestFileEditToolReadFile:
    """Tests for FileEditTool.read_file"""

    def test_read_file_with_filepath(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Hello, World!\nLine 2\nLine 3")

            result = _run(FileEditTool.read_file(filepath=str(file_path)))
            assert result.success is True
            assert "Hello, World!" in result.output
            assert "Line 2" in result.output
            assert "Line 3" in result.output

    def test_read_file_with_path_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Content via path alias")

            result = _run(FileEditTool.read_file(path=str(file_path)))
            assert result.success is True
            assert "Content via path alias" in result.output

    def test_read_file_missing_argument(self):
        result = _run(FileEditTool.read_file())
        assert result.success is False
        assert "Missing required argument" in result.error
        assert "filepath" in result.error.lower() or "path" in result.error.lower()

    def test_read_file_nonexistent(self):
        result = _run(FileEditTool.read_file(filepath="/nonexistent/path.txt"))
        assert result.success is False
        assert "File not found" in result.error

    def test_read_file_not_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(FileEditTool.read_file(filepath=tmp))
            assert result.success is False
            assert "Not a file" in result.error

    def test_read_file_max_lines_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("\n".join(f"Line {i}" for i in range(200)))

            result = _run(FileEditTool.read_file(filepath=str(file_path), max_lines=5))
            assert result.success is True
            lines = result.output.split("\n")
            assert len(lines) <= 6  # 5 lines + truncation message
            assert "Line 0" in result.output
            assert "5 lines shown" in result.output


class TestFileEditToolWriteFile:
    """Tests for FileEditTool.write_file"""

    def test_write_file_with_filepath(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "new_file.txt"
            result = _run(FileEditTool.write_file(filepath=str(file_path), content="New content"))

            assert result.success is True
            assert "Successfully wrote" in result.output
            assert file_path.read_text() == "New content"

    def test_write_file_with_path_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "new_file.txt"
            result = _run(FileEditTool.write_file(path=str(file_path), content="Via path alias"))

            assert result.success is True
            assert file_path.read_text() == "Via path alias"

    def test_write_file_missing_argument(self):
        result = _run(FileEditTool.write_file(content="test"))
        assert result.success is False
        assert "Missing required argument" in result.error

    def test_write_file_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "subdir" / "nested" / "file.txt"
            result = _run(FileEditTool.write_file(filepath=str(file_path), content="Nested"))

            assert result.success is True
            assert file_path.read_text() == "Nested"

    def test_write_file_empty_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "empty.txt"
            result = _run(FileEditTool.write_file(filepath=str(file_path), content=""))

            assert result.success is True
            assert file_path.read_text() == ""


class TestFileEditToolAppendFile:
    """Tests for FileEditTool.append_file"""

    def test_append_file_with_filepath(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Original\n")

            result = _run(FileEditTool.append_file(filepath=str(file_path), content="Appended\n"))

            assert result.success is True
            assert "Successfully appended" in result.output
            assert file_path.read_text() == "Original\nAppended\n"

    def test_append_file_with_path_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Start")

            result = _run(FileEditTool.append_file(path=str(file_path), content="End"))

            assert result.success is True
            assert file_path.read_text() == "StartEnd"

    def test_append_file_missing_argument(self):
        result = _run(FileEditTool.append_file(content="test"))
        assert result.success is False
        assert "Missing required argument" in result.error

    def test_append_file_creates_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "new.txt"
            result = _run(FileEditTool.append_file(filepath=str(file_path), content="First line\n"))

            assert result.success is True
            assert file_path.read_text() == "First line\n"


class TestFileEditToolEditFile:
    """Tests for FileEditTool.edit_file"""

    def test_edit_file_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Hello old world\n")

            result = _run(FileEditTool.edit_file(
                filepath=str(file_path),
                old_str="old",
                new_str="new"
            ))

            assert result.success is True
            assert "Successfully replaced" in result.output
            assert file_path.read_text() == "Hello new world\n"

    def test_edit_file_with_path_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("foo bar baz")

            result = _run(FileEditTool.edit_file(
                path=str(file_path),
                old_str="bar",
                new_str="BAR"
            ))

            assert result.success is True
            assert file_path.read_text() == "foo BAR baz"

    def test_edit_file_missing_argument(self):
        result = _run(FileEditTool.edit_file(old_str="a", new_str="b"))
        assert result.success is False
        assert "Missing required argument" in result.error

    def test_edit_file_string_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Hello world")

            result = _run(FileEditTool.edit_file(
                filepath=str(file_path),
                old_str="missing",
                new_str="found"
            ))

            assert result.success is False
            assert "String to replace not found" in result.error
            assert file_path.read_text() == "Hello world"

    def test_edit_file_ambiguous_multiple_occurrences(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("foo bar foo baz")

            result = _run(FileEditTool.edit_file(
                filepath=str(file_path),
                old_str="foo",
                new_str="FOO"
            ))

            assert result.success is False
            assert "appears 2 times" in result.error
            assert "refusing ambiguous edit" in result.error
            assert file_path.read_text() == "foo bar foo baz"

    def test_edit_file_nonexistent_file(self):
        result = _run(FileEditTool.edit_file(
            filepath="/nonexistent.txt",
            old_str="a",
            new_str="b"
        ))
        assert result.success is False
        assert "File not found" in result.error

    def test_edit_file_exact_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("The quick brown fox\n")

            result = _run(FileEditTool.edit_file(
                filepath=str(file_path),
                old_str="The quick brown fox\n",
                new_str="A quick brown fox\n"
            ))

            assert result.success is True
            assert file_path.read_text() == "A quick brown fox\n"


class TestFileEditToolEdgeCases:
    """Edge case tests for FileEditTool"""

    def test_read_file_unicode_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "unicode.txt"
            file_path.write_text("Hello 世界 🌍\n日本語")

            result = _run(FileEditTool.read_file(filepath=str(file_path)))
            assert result.success is True
            assert "世界" in result.output
            assert "🌍" in result.output
            assert "日本語" in result.output

    def test_write_file_unicode_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "unicode.txt"
            content = "Hello 世界 🌍\n日本語"

            result = _run(FileEditTool.write_file(filepath=str(file_path), content=content))
            assert result.success is True
            assert file_path.read_text() == content

    def test_edit_file_multiline_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Line 1\nLine 2\nLine 3\n")

            result = _run(FileEditTool.edit_file(
                filepath=str(file_path),
                old_str="Line 1\nLine 2\n",
                new_str="New Line 1\nNew Line 2\n"
            ))

            assert result.success is True
            assert file_path.read_text() == "New Line 1\nNew Line 2\nLine 3\n"

    def test_read_write_append_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "roundtrip.txt"

            # Write
            result = _run(FileEditTool.write_file(filepath=str(file_path), content="Initial\n"))
            assert result.success is True

            # Read
            result = _run(FileEditTool.read_file(filepath=str(file_path)))
            assert result.success is True
            assert result.output == "Initial\n"

            # Append
            result = _run(FileEditTool.append_file(filepath=str(file_path), content="Appended\n"))
            assert result.success is True

            # Read again
            result = _run(FileEditTool.read_file(filepath=str(file_path)))
            assert result.success is True
            assert result.output == "Initial\nAppended\n"

            # Edit
            result = _run(FileEditTool.edit_file(
                filepath=str(file_path),
                old_str="Initial\n",
                new_str="Edited\n"
            ))
            assert result.success is True

            # Final read
            result = _run(FileEditTool.read_file(filepath=str(file_path)))
            assert result.success is True
            assert result.output == "Edited\nAppended\n"

    def test_edit_file_no_newline_at_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "test.txt"
            file_path.write_text("Hello world")

            result = _run(FileEditTool.edit_file(
                filepath=str(file_path),
                old_str="world",
                new_str="universe"
            ))

            assert result.success is True
            assert file_path.read_text() == "Hello universe"