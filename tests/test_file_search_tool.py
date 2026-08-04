"""
Tests for the FileSearchTool class - search_files and search_content methods.
(list_directory is already tested in test_tool_registry.py)
"""
import asyncio
import tempfile
from pathlib import Path

from clio_agent_2.tools.tool_registry import FileSearchTool, ToolResult


def _run(coro):
    return asyncio.run(coro)


class TestFileSearchToolSearchFiles:
    """Tests for FileSearchTool.search_files"""

    def test_search_files_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file1.txt").write_text("content1")
            (tmp_path / "file2.py").write_text("content2")
            (tmp_path / "file3.md").write_text("content3")
            (tmp_path / "subdir").mkdir()
            (tmp_path / "subdir" / "nested.txt").write_text("nested")

            result = _run(FileSearchTool.search_files(directory=tmp, pattern="*.txt"))

            assert result.success is True
            assert "file1.txt" in result.output
            assert "subdir/nested.txt" in result.output
            assert "file2.py" not in result.output
            assert "file3.md" not in result.output

    def test_search_files_recursive_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file1.txt").write_text("content1")
            (tmp_path / "subdir").mkdir()
            (tmp_path / "subdir" / "nested.txt").write_text("nested")

            result = _run(FileSearchTool.search_files(
                directory=tmp, pattern="*.txt", recursive=False
            ))

            assert result.success is True
            assert "file1.txt" in result.output
            assert "nested.txt" not in result.output

    def test_search_files_no_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file1.py").write_text("content1")

            result = _run(FileSearchTool.search_files(directory=tmp, pattern="*.txt"))

            assert result.success is True
            assert "No files matching" in result.output
            assert "*.txt" in result.output

    def test_search_files_nonexistent_directory(self):
        result = _run(FileSearchTool.search_files(directory="/nonexistent", pattern="*.txt"))

        assert result.success is False
        assert "Directory not found" in result.error

    def test_search_files_not_a_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "file.txt"
            file_path.write_text("content")

            result = _run(FileSearchTool.search_files(directory=str(file_path), pattern="*.txt"))

            assert result.success is False
            assert "Not a directory" in result.error

    def test_search_files_max_results_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for i in range(60):
                (tmp_path / f"file{i}.txt").write_text(f"content{i}")

            result = _run(FileSearchTool.search_files(
                directory=tmp, pattern="*.txt", max_results=10
            ))

            assert result.success is True
            assert "10 results shown" in result.output

    def test_search_files_complex_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "test_file.py").write_text("content")
            (tmp_path / "test_file.txt").write_text("content")
            (tmp_path / "other.py").write_text("content")
            (tmp_path / "test").mkdir()
            (tmp_path / "test" / "test_module.py").write_text("content")

            result = _run(FileSearchTool.search_files(directory=tmp, pattern="test*.py"))

            assert result.success is True
            assert "test_file.py" in result.output
            assert "test/test_module.py" in result.output
            assert "other.py" not in result.output


class TestFileSearchToolSearchContent:
    """Tests for FileSearchTool.search_content"""

    def test_search_content_basic(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file1.txt").write_text("hello world\nsecond line")
            (tmp_path / "file2.py").write_text("def hello():\n    pass")
            (tmp_path / "file3.md").write_text("# Title\nNo match here")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="hello"
            ))

            assert result.success is True
            assert "hello world" in result.output
            assert "def hello():" in result.output
            assert "file1.txt" in result.output
            assert "file2.py" in result.output
            assert "file3.md" not in result.output

    def test_search_content_case_sensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file.txt").write_text("Hello World\nhello world")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="Hello", case_sensitive=True
            ))

            assert result.success is True
            assert "Hello World" in result.output
            assert "hello world" not in result.output

    def test_search_content_case_insensitive_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file.txt").write_text("Hello World\nhello world")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="hello"
            ))

            assert result.success is True
            assert "Hello World" in result.output
            assert "hello world" in result.output

    def test_search_content_file_pattern_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "code.py").write_text("def hello(): pass")
            (tmp_path / "script.js").write_text("function hello() {}")
            (tmp_path / "readme.txt").write_text("hello world")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="hello", file_pattern="*.py"
            ))

            assert result.success is True
            assert "code.py" in result.output
            assert "script.js" not in result.output
            assert "readme.txt" not in result.output

    def test_search_content_no_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file1.txt").write_text("content one")
            (tmp_path / "file2.py").write_text("content two")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="xyz123"
            ))

            assert result.success is True
            assert "No content matching" in result.output
            assert "xyz123" in result.output
            assert "2 files" in result.output

    def test_search_content_nonexistent_directory(self):
        result = _run(FileSearchTool.search_content(
            directory="/nonexistent", search_term="test"
        ))

        assert result.success is False
        assert "Directory not found" in result.error

    def test_search_content_max_results_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for i in range(30):
                (tmp_path / f"file{i}.txt").write_text(f"match {i}")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="match", max_results=5
            ))

            assert result.success is True
            lines = result.output.split("\n")
            match_lines = [l for l in lines if "match" in l and ":" in l and "Found" not in l]
            assert len(match_lines) <= 5

    def test_search_content_skips_binary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "text.txt").write_text("hello world")
            # Create a binary file
            (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\x03hello")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="hello"
            ))

            assert result.success is True
            assert "text.txt" in result.output

    def test_search_content_permission_error_handling(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file.txt").write_text("hello")

            # Mock open to raise PermissionError for one file
            original_open = open
            def mock_open(*args, **kwargs):
                if "file.txt" in str(args[0]):
                    raise PermissionError("Permission denied")
                return original_open(*args, **kwargs)

            import builtins
            builtins.open = mock_open
            try:
                result = _run(FileSearchTool.search_content(
                    directory=tmp, search_term="hello"
                ))
                assert result.success is True  # Should not crash
            finally:
                builtins.open = original_open

    def test_search_content_unicode_handling(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "unicode.txt").write_text("Hello 世界 🌍\n日本語テスト")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="世界"
            ))

            assert result.success is True
            assert "世界" in result.output


class TestFileSearchToolEdgeCases:
    """Edge case tests for FileSearchTool"""

    def test_search_files_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(FileSearchTool.search_files(directory=tmp, pattern="*"))
            assert result.success is True
            assert "No files matching" in result.output

    def test_search_content_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(FileSearchTool.search_content(directory=tmp, search_term="test"))
            assert result.success is True
            assert "No content matching" in result.output
            assert "0 files" in result.output

    def test_search_files_with_special_characters_in_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "file with spaces.txt").write_text("content")
            (tmp_path / "file-with-dashes.txt").write_text("content")
            (tmp_path / "file_with_underscores.txt").write_text("content")

            result = _run(FileSearchTool.search_files(directory=tmp, pattern="*.txt"))

            assert result.success is True
            assert "file with spaces.txt" in result.output
            assert "file-with-dashes.txt" in result.output
            assert "file_with_underscores.txt" in result.output

    def test_search_content_multiline_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "multiline.txt").write_text("Line 1\nLine 2 with match\nLine 3")

            result = _run(FileSearchTool.search_content(
                directory=tmp, search_term="match"
            ))

            assert result.success is True
            assert "Line 2 with match" in result.output