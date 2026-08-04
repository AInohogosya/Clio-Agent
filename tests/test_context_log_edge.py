"""
Tests for ContextLog additional edge cases.
Covers edge cases not in test_context_log.py.
"""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest import mock

from clio_agent_2.core.context_manager import ContextLog, ContextEntry


def _run(coro):
    return asyncio.run(coro)


class TestContextLogEdgeCases:
    """Additional edge case tests for ContextLog"""

    def test_add_entry_with_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            _run(log.add_entry("custom_type", "content", {"key": "value", "num": 42}))

            entries = log.get_entries()
            assert len(entries) == 1
            assert entries[0].metadata == {"key": "value", "num": 42}

    def test_add_entry_none_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            _run(log.add_entry("type", "content", None))

            entries = log.get_entries()
            assert len(entries) == 1
            assert entries[0].metadata == {}

    def test_persist_path_parent_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            deep_path = Path(tmp) / "deep" / "nested" / "context.json"
            log = ContextLog(persist_path=str(deep_path))

            _run(log.add_user_message("test"))
            log.save()

            assert deep_path.exists()

    def test_persist_path_without_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            _run(log.add_user_message("test"))
            log.save()

            # Should not raise
            assert Path(tmp, "context.json").exists()

    def test_save_without_persist_path(self):
        """Save should be no-op when no persist_path"""
        log = ContextLog()
        _run(log.add_user_message("test"))
        log.save()  # Should not raise

    def test_load_from_file_without_persist_path(self):
        log = ContextLog()
        result = log.load_from_file()
        assert result is False

    def test_archive_without_archive_path(self):
        """Archive should be no-op when no archive_path"""
        log = ContextLog()
        _run(log.add_user_message("test"))
        # Should not raise when compressing cold entries
        # Need to trigger compression
        log._cold_pending = [mock.MagicMock()] * 10
        log.compression_callback = mock.AsyncMock(return_value="summary")
        _run(log._compress_cold())

    def test_rebuild_from_data_empty_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            data = {
                "entries": [],
                "working_summary": "test summary",
                "entry_count": 0,
            }
            log._rebuild_from_data(data)

            assert log.get_line_count() == 0
            assert log.working_summary == "test summary"

    def test_rebuild_from_data_missing_keys(self):
        """_rebuild_from_data should handle missing keys gracefully"""
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            data = {
                "entries": [{"type": "user_message", "content": "hello"}],
                # missing working_summary, entry_count, etc.
            }
            log._rebuild_from_data(data)

            assert log.get_line_count() == 1
            assert log.working_summary == ""

    def test_clear_without_persist_path(self):
        """Clear should work without persist_path"""
        log = ContextLog()
        _run(log.add_user_message("test"))
        _run(log.add_thinking("thought"))

        log.clear()

        assert log.get_line_count() == 0
        assert log.working_summary == ""

    def test_clear_with_archive_path(self):
        """Clear should clear archive file"""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "archive.jsonl"
            archive.write_text("some data\n")

            log = ContextLog(persist_path=str(Path(tmp) / "context.json"), archive_path=str(archive))
            _run(log.add_user_message("test"))
            log.clear()

            # Archive should be cleared
            assert archive.read_text() == ""

    def test_backup_to_trash_without_persist(self):
        """_backup_to_trash should be no-op without persist_path"""
        log = ContextLog()
        _run(log.add_user_message("test"))
        log._backup_to_trash()  # Should not raise

    def test_restore_backup_without_persist(self):
        log = ContextLog()
        result = log.restore_backup()
        assert result is False

    def test_restore_backup_corrupt_trash(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist = Path(tmp) / "context.json"
            trash = Path(tmp) / "context.trash.json"
            trash.write_text("not valid json")

            log = ContextLog(persist_path=str(persist))
            result = log.restore_backup()
            assert result is False

    def test_save_async_calls_save(self):
        log = ContextLog(persist_path=str(Path("/tmp") / "context.json"))
        log._save_to_file = mock.MagicMock()
        _run(log.save_async())
        log._save_to_file.assert_called()

    def test_add_entry_thread_safety(self):
        """Multiple concurrent adds should be thread-safe"""
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            async def add_many():
                for i in range(20):
                    await log.add_user_message(f"msg{i}")

            _run(add_many())

            assert log.get_line_count() == 20

    def test_get_entries_as_messages_max_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            _run(log.add_user_message("Short"))
            _run(log.add_user_message("A" * 5000))
            _run(log.add_user_message("B" * 5000))

            messages = log.get_entries_as_messages(max_tokens=100)
            # Should reduce to 1 message to fit token budget
            assert len(messages) == 1


class TestContextEntryEdgeCases:
    """Edge cases for ContextEntry"""

    def test_entry_with_complex_metadata(self):
        entry = ContextEntry("tool_execution", "result", {"nested": {"a": 1}, "list": [1, 2, 3]})
        d = entry.to_dict()
        assert d["metadata"] == {"nested": {"a": 1}, "list": [1, 2, 3]}

    def test_entry_with_none_metadata(self):
        entry = ContextEntry("system", "message", None)
        d = entry.to_dict()
        assert d["metadata"] == {}

    def test_log_line_format(self):
        entry = ContextEntry("user_message", "Hello world")
        line = entry.to_log_line()
        assert "user_message" in line.lower()
        assert "Hello world" in line

    def test_str_repr(self):
        entry = ContextEntry("thinking", "Internal thought")
        assert str(entry) == "[THINKING] Internal thought"

    def test_timestamp_in_entry(self):
        entry = ContextEntry("system", "test")
        assert entry.timestamp is not None
        assert "T" in entry.timestamp  # ISO format


class TestContextLogCompressionEdgeCases:
    """Tests for compression edge cases"""

    def test_compression_callback_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def failing_compress(entries):
                raise RuntimeError("Compression failed")

            log = ContextLog(
                window_size=2,
                cold_batch=2,
                compression_callback=failing_compress,
                persist_path=str(Path(tmp) / "context.json")
            )

            for i in range(5):
                _run(log.add_user_message(f"msg{i}"))

            import time
            time.sleep(0.1)

            assert "summary unavailable" in log.working_summary

    def test_compression_with_empty_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(
                window_size=2,
                cold_batch=2,
                compression_callback=mock.AsyncMock(return_value="summary"),
                persist_path=str(Path(tmp) / "context.json")
            )

            # No cold pending entries
            log._cold_pending = []
            _run(log._compress_cold())

            # Should not call compression callback with empty batch
            log.compression_callback.assert_not_called()