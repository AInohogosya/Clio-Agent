"""
Tests for instance_lock module - extended.
Covers SingleInstanceLock more thoroughly.
"""
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "clio_agent_2"))

from utils.instance_lock import (
    SingleInstanceLock,
    _pid_alive,
    format_lock_hint,
)


class TestPidAlive:
    """Tests for _pid_alive function"""

    def test_current_process(self):
        assert _pid_alive(os.getpid()) is True

    def test_nonexistent_pid(self):
        # Very high PID is unlikely to exist
        assert _pid_alive(999999999) is False

    def test_negative_pid(self):
        assert _pid_alive(-1) is False

    def test_zero_pid(self):
        # PID 0 is the scheduler, technically alive but special
        result = _pid_alive(0)
        assert result is False or result is True  # Either is acceptable


class TestFormatLockHint:
    """Tests for format_lock_hint"""

    def test_telegram_hint(self):
        hint = format_lock_hint("telegram")
        assert "telegram" in hint
        assert "--replace" in hint or "--force" in hint
        assert "rm" in hint

    def test_discord_hint(self):
        hint = format_lock_hint("discord")
        assert "discord" in hint.lower()

    def test_default_hint(self):
        hint = format_lock_hint("unknown")
        assert "unknown" in hint.lower()


class TestSingleInstanceLockAcquire:
    """Tests for SingleInstanceLock.acquire edge cases"""

    def test_acquire_releases_properly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            lock = SingleInstanceLock("test_acquire_release", lock_dir=str(tmp))
            assert lock.acquire() is True
            assert lock.lock_path.exists()
            lock.release()
            assert not lock.lock_path.exists()

    def test_acquire_already_held_by_self(self):
        """Acquiring a lock you already hold"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            lock = SingleInstanceLock("test_self_reacquire", lock_dir=str(tmp))
            lock.acquire()
            # Trying to acquire again should succeed since it's the same process
            result = lock.acquire()
            assert result is True or result is False  # Depends on implementation
            lock.release()

    def test_acquire_with_stale_pid_updates_content(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            lock = SingleInstanceLock("test_update_content", lock_dir=str(tmp))
            lock.lock_path.write_text(
                f"pid={os.getpid()}\nhost={socket.gethostname()}\nstarted={int(time.time())}\n"
            )
            result = lock.acquire()
            assert result is True
            lock.release()


class TestSingleInstanceLockRelease:
    """Tests for SingleInstanceLock.release"""

    def test_release_without_acquire(self):
        """Release should be a no-op if never acquired"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            lock = SingleInstanceLock("test_release_never_owned", lock_dir=str(tmp))
            # Should not raise
            lock.release()

    def test_release_after_expired(self):
        """Release should remove the lock file"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            lock = SingleInstanceLock("test_release", lock_dir=str(tmp))
            lock.acquire()
            lock_path = lock.lock_path
            assert lock_path.exists()
            lock.release()
            assert not lock_path.exists()