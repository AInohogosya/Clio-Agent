"""
Tests for durable context persistence in ``ContextLog``.

These guard the guarantee that the agent's context survives a program restart:

1. ``save`` + ``load_from_file`` round-trip the hot state (entries, summary).
2. A ``.bak`` backup is written before each overwrite, and a corrupt primary
   file falls back to that backup on load.
3. An explicit ``clear`` backs the state up to a ``.trash`` file, and
   ``restore_backup`` recovers it (so /clear_context is no longer irreversible).
4. ``save`` flushes the latest entries even when the throttle has not ticked,
   so a restart just after new activity loses nothing.
"""

import asyncio
import json
from pathlib import Path

from clio_agent_2.core.context_manager import ContextLog


def _make_log(tmp_path: Path, **kw) -> ContextLog:
    persist = tmp_path / "context.json"
    return ContextLog(persist_path=str(persist), **kw)


def _write(log: ContextLog, n: int):
    async def _go():
        for i in range(n):
            await log.add_entry("user_message", f"msg {i}")
    asyncio.run(_go())


def test_save_load_roundtrip(tmp_path):
    log = _make_log(tmp_path)
    asyncio.run(log.add_entry("user_message", "hello"))
    asyncio.run(log.add_entry("thinking", "thinking..."))
    log.working_summary = "a summary"
    log.save()

    reopened = _make_log(tmp_path)
    assert reopened.load_from_file() is True
    assert [e.content for e in reopened.get_entries()] == ["hello", "thinking..."]
    assert reopened.working_summary == "a summary"


def test_save_flushes_latest_entries(tmp_path):
    """save() persists immediately even when the 10-entry throttle has not ticked."""
    log = _make_log(tmp_path)
    _write(log, 3)  # fewer than the throttle boundary
    log.save()

    reopened = _make_log(tmp_path)
    assert reopened.load_from_file() is True
    assert log.get_line_count() == 3
    assert [e.content for e in reopened.get_entries()] == [
        "msg 0", "msg 1", "msg 2"
    ]


def test_bak_fallback_on_corrupt_primary(tmp_path):
    log = _make_log(tmp_path)
    _write(log, 2)
    log.save()  # first save: primary created (no .bak yet)
    _write(log, 3)  # total 5 now
    log.save()  # second save: .bak holds the previous 2-entry state

    # Corrupt the primary file; load must recover from the .bak.
    primary = log.persist_path
    primary.write_text("{ this is not valid json", encoding="utf-8")

    reopened = _make_log(tmp_path)
    assert reopened.load_from_file() is True
    # Recovered from the 2-entry backup, not the corrupt 5-entry primary.
    assert reopened.get_line_count() == 2


def test_bak_rotates_previous_good_state(tmp_path):
    log = _make_log(tmp_path)
    _write(log, 2)
    log.save()  # first save: primary only
    _write(log, 3)  # total 5 now
    log.save()  # second save: .bak holds the previous 2-entry state

    first_bak = log.persist_path.with_suffix(log.persist_path.suffix + ".bak")
    assert first_bak.exists()
    first_count = json.loads(first_bak.read_text())["entries"]
    assert len(first_count) == 2

    _write(log, 2)  # total 7 now
    log.save()  # .bak now holds the previous 5-entry state
    # The .bak should have rotated to the *previous* (5-entry) good state.
    bak_entries = json.loads(first_bak.read_text())["entries"]
    assert len(bak_entries) == 5


def test_clear_backs_up_and_restore_recovers(tmp_path):
    log = _make_log(tmp_path)
    _write(log, 4)
    log.save()

    log.clear()  # wipes in-memory + persists empty state + writes .trash
    assert log.get_line_count() == 0

    trash = log.persist_path.with_name(
        f"{log.persist_path.stem}.trash{log.persist_path.suffix}"
    )
    assert trash.exists()

    assert log.restore_backup() is True
    assert log.get_line_count() == 4
    assert [e.content for e in log.get_entries()] == [
        "msg 0", "msg 1", "msg 2", "msg 3"
    ]


def test_restore_with_no_backup_returns_false(tmp_path):
    log = _make_log(tmp_path)
    assert log.restore_backup() is False


def test_clear_persists_empty_state(tmp_path):
    log = _make_log(tmp_path)
    _write(log, 2)
    log.save()
    log.clear()

    reopened = _make_log(tmp_path)
    assert reopened.load_from_file() is True
    assert reopened.get_line_count() == 0


def test_sidecar_files_use_stem(tmp_path):
    log = _make_log(tmp_path)
    _write(log, 1)
    log.save()       # first save -> primary only
    _write(log, 1)
    log.save()       # second save -> .bak rotated in
    assert log.persist_path.with_suffix(".json.bak").exists()

    log.clear()       # writes the .trash sidecar
    trash = log.persist_path.with_name(
        f"{log.persist_path.stem}.trash{log.persist_path.suffix}"
    )
    assert trash.exists()
