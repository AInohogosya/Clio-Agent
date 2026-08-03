"""
Tests for the ContextLog class.
Covers add_entry, compression, export, persistence, archive, and truncation.
"""
import asyncio
import tempfile
import json
from pathlib import Path
from unittest import mock

from clio_agent_2.core.context_manager import ContextLog, ContextEntry


def _run(coro):
    return asyncio.run(coro)


class TestContextLogBasicOperations:
    """Tests for basic ContextLog operations"""

    def test_add_user_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            _run(log.add_user_message("Hello world"))

            entries = log.get_entries()
            assert len(entries) == 1
            assert entries[0].entry_type == "user_message"
            assert "Hello world" in entries[0].content

    def test_add_thinking(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            _run(log.add_thinking("Internal thought"))

            entries = log.get_entries()
            assert len(entries) == 1
            assert entries[0].entry_type == "thinking"
            assert "Internal thought" in entries[0].content

    def test_add_system_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            _run(log.add_system_message("System notification"))

            entries = log.get_entries()
            assert len(entries) == 1
            assert entries[0].entry_type == "system"
            assert "System notification" in entries[0].content

    def test_add_assistant_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            _run(log.add_assistant_response("Assistant reply"))

            entries = log.get_entries()
            assert len(entries) == 1
            assert entries[0].entry_type == "assistant_response"
            assert "Assistant reply" in entries[0].content

    def test_add_tool_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            _run(log.add_tool_execution("read_file", {"filepath": "test.txt"}, "file content"))

            entries = log.get_entries()
            assert len(entries) == 1
            assert entries[0].entry_type == "tool_execution"
            assert "read_file" in entries[0].content
            assert "test.txt" in entries[0].content
            assert entries[0].metadata.get("tool_name") == "read_file"

    def test_get_line_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            assert log.get_line_count() == 0

            _run(log.add_user_message("msg1"))
            assert log.get_line_count() == 1

            _run(log.add_user_message("msg2"))
            assert log.get_line_count() == 2

    def test_get_recent_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            for i in range(10):
                _run(log.add_user_message(f"msg{i}"))

            recent = log.get_recent_entries(3)
            assert len(recent) == 3
            assert recent[0].content == "User Message: msg7"
            assert recent[1].content == "User Message: msg8"
            assert recent[2].content == "User Message: msg9"


class TestContextLogWindowManagement:
    """Tests for working window and cold pending management"""

    def test_working_window_respects_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(window_size=3, persist_path=str(Path(tmp) / "context.json"))
            for i in range(5):
                _run(log.add_user_message(f"msg{i}"))

            entries = log.get_entries()
            assert len(entries) == 3
            assert entries[0].content == "User Message: msg2"
            assert entries[2].content == "User Message: msg4"

    def test_cold_pending_accumulates(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(window_size=2, cold_batch=10, persist_path=str(Path(tmp) / "context.json"))
            for i in range(5):
                _run(log.add_user_message(f"msg{i}"))

            assert len(log._cold_pending) == 3
            assert log._cold_pending[0].content == "User Message: msg0"


class TestContextLogPersistence:
    """Tests for save/load persistence"""

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist_path = Path(tmp) / "context.json"
            log = ContextLog(persist_path=str(persist_path))

            _run(log.add_user_message("message 1"))
            _run(log.add_thinking("thought 1"))
            log.working_summary = "Test summary"
            log.save()

            # Load into new instance
            new_log = ContextLog(persist_path=str(persist_path))
            loaded = new_log.load_from_file()

            assert loaded is True
            assert new_log.get_line_count() == 2
            assert new_log.working_summary == "Test summary"
            entries = new_log.get_entries()
            assert len(entries) == 2
            assert "message 1" in entries[0].content
            assert "thought 1" in entries[1].content

    def test_save_flushes_latest_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist_path = Path(tmp) / "context.json"
            log = ContextLog(persist_path=str(persist_path))

            _run(log.add_user_message("msg1"))
            _run(log.add_user_message("msg2"))
            log.save()

            new_log = ContextLog(persist_path=str(persist_path))
            new_log.load_from_file()
            assert new_log.get_line_count() == 2

    def test_backup_created_on_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist_path = Path(tmp) / "context.json"
            log = ContextLog(persist_path=str(persist_path))

            _run(log.add_user_message("msg1"))
            log.save()

            # Second save should create backup
            _run(log.add_user_message("msg2"))
            log.save()

            backup_path = persist_path.with_suffix(persist_path.suffix + ".bak")
            assert backup_path.exists()

    def test_load_fallback_to_backup_on_corrupt_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist_path = Path(tmp) / "context.json"
            log = ContextLog(persist_path=str(persist_path))

            _run(log.add_user_message("msg1"))
            _run(log.add_user_message("msg2"))
            log.save()

            _run(log.add_user_message("msg3"))
            log.save()

            # Corrupt primary
            persist_path.write_text("{ invalid json")

            new_log = ContextLog(persist_path=str(persist_path))
            loaded = new_log.load_from_file()

            assert loaded is True
            assert new_log.get_line_count() == 2  # Restored from backup


class TestContextLogArchive:
    """Tests for cold entry archival"""

    def test_archive_created_on_compression(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "archive.jsonl"
            log = ContextLog(
                window_size=2,
                cold_batch=2,
                compression_callback=lambda x: "summary",
                archive_path=str(archive_path),
                persist_path=str(Path(tmp) / "context.json")
            )

            for i in range(5):
                _run(log.add_user_message(f"msg{i}"))

            # Wait for compression
            import time
            time.sleep(0.1)

            assert archive_path.exists()
            content = archive_path.read_text()
            lines = content.strip().split("\n")
            assert len(lines) >= 2


class TestContextLogClearAndRestore:
    """Tests for clear and restore functionality"""

    def test_clear_creates_trash_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist_path = Path(tmp) / "context.json"
            log = ContextLog(persist_path=str(persist_path))

            _run(log.add_user_message("msg1"))
            _run(log.add_user_message("msg2"))
            log.save()

            log.clear()

            assert log.get_line_count() == 0
            assert log.working_summary == ""

            trash_path = persist_path.with_name(f"{persist_path.stem}.trash{persist_path.suffix}")
            assert trash_path.exists()

    def test_restore_backup_recovers_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist_path = Path(tmp) / "context.json"
            log = ContextLog(persist_path=str(persist_path))

            _run(log.add_user_message("msg1"))
            _run(log.add_user_message("msg2"))
            log.working_summary = "Original summary"
            log.save()

            log.clear()
            assert log.get_line_count() == 0

            restored = log.restore_backup()
            assert restored is True
            assert log.get_line_count() == 2
            assert log.working_summary == "Original summary"

    def test_restore_with_no_backup_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))
            restored = log.restore_backup()
            assert restored is False

    def test_clear_persists_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            persist_path = Path(tmp) / "context.json"
            log = ContextLog(persist_path=str(persist_path))

            _run(log.add_user_message("msg1"))
            log.save()
            log.clear()

            new_log = ContextLog(persist_path=str(persist_path))
            new_log.load_from_file()
            assert new_log.get_line_count() == 0


class TestContextLogCompression:
    """Tests for context compression"""

    def test_compression_callback_invoked(self):
        with tempfile.TemporaryDirectory() as tmp:
            compression_results = []

            async def mock_compress(entries):
                compression_results.append(len(entries))
                return "Compressed summary"

            log = ContextLog(
                window_size=2,
                cold_batch=2,
                compression_callback=mock_compress,
                persist_path=str(Path(tmp) / "context.json")
            )

            for i in range(5):
                _run(log.add_user_message(f"msg{i}"))

            import time
            time.sleep(0.2)

            assert len(compression_results) > 0
            assert log.working_summary == "Compressed summary"

    def test_compression_error_handling(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def failing_compress(entries):
                raise Exception("Compression failed")

            log = ContextLog(
                window_size=2,
                cold_batch=2,
                compression_callback=failing_compress,
                persist_path=str(Path(tmp) / "context.json")
            )

            for i in range(5):
                _run(log.add_user_message(f"msg{i}"))

            import time
            time.sleep(0.2)

            # Should not crash, summary should indicate failure
            assert "summary unavailable" in log.working_summary


class TestContextLogExport:
    """Tests for export_to_file"""

    def test_export_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            _run(log.add_user_message("Hello"))
            _run(log.add_thinking("Thinking..."))
            _run(log.add_system_message("System msg"))

            export_path = Path(tmp) / "export.log"
            log.export_to_file(str(export_path))

            assert export_path.exists()
            content = export_path.read_text()
            assert "Hello" in content
            assert "Thinking..." in content
            assert "System msg" in content


class TestContextLogEntriesAsMessages:
    """Tests for get_entries_as_messages"""

    def test_get_entries_as_messages_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            _run(log.add_user_message("User says hi"))
            _run(log.add_assistant_response("Assistant replies"))
            _run(log.add_thinking("Internal thought"))

            messages = log.get_entries_as_messages()
            assert len(messages) == 3
            assert messages[0]["role"] == "user"
            assert "User says hi" in messages[0]["content"]
            assert messages[1]["role"] == "assistant"
            assert "Assistant replies" in messages[1]["content"]
            assert messages[2]["role"] == "user"
            assert "Thinking: Internal thought" in messages[2]["content"]

    def test_get_entries_as_messages_token_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = ContextLog(persist_path=str(Path(tmp) / "context.json"))

            _run(log.add_user_message("Short"))
            _run(log.add_user_message("A" * 1000))
            _run(log.add_user_message("B" * 1000))

            messages = log.get_entries_as_messages(max_tokens=500)
            total_chars = sum(len(m["content"]) for m in messages)
            # Should truncate to fit token budget
            assert total_chars < 2500


class TestContextLogEntry:
    """Tests for ContextEntry class"""

    def test_context_entry_to_dict(self):
        entry = ContextEntry("user_message", "Hello", {"key": "value"})
        d = entry.to_dict()
        assert d["type"] == "user_message"
        assert d["content"] == "Hello"
        assert d["metadata"] == {"key": "value"}

    def test_context_entry_to_dict_empty_metadata(self):
        entry = ContextEntry("system", "Test")
        d = entry.to_dict()
        assert d["metadata"] == {}

    def test_context_entry_str_repr(self):
        entry = ContextEntry("user_message", "Hello world")
        assert str(entry) == "[USER_MESSAGE] Hello world"

    def test_context_entry_log_line(self):
        entry = ContextEntry("thinking", "Deep thought")
        line = entry.to_log_line()
        assert "thinking:" in line.lower()
        assert "Deep thought" in line