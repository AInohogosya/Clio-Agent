"""
Tests for the cross-process single-instance lock used to prevent two Telegram
polling instances (and therefore ``telegram.error.Conflict`` 409 errors) from
running on the same machine.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "clio_agent_2"))

from utils.instance_lock import (  # noqa: E402
    SingleInstanceLock,
    _pid_alive,
    format_lock_hint,
)


def _spawn_sleeper():
    """Spawn a child that sleeps ~30s so we have a *live* PID to test against."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_acquire_and_release(tmp_path):
    lock = SingleInstanceLock("a", lock_dir=str(tmp_path))
    assert lock.acquire() is True
    assert lock.lock_path.exists()
    lock.release()
    assert not lock.lock_path.exists()


def test_second_instance_is_blocked(tmp_path):
    l1 = SingleInstanceLock("b", lock_dir=str(tmp_path))
    l2 = SingleInstanceLock("b", lock_dir=str(tmp_path))
    assert l1.acquire() is True
    # A second live instance must be refused.
    assert l2.acquire() is False
    # Once the first releases, the second can take over.
    l1.release()
    assert l2.acquire() is True
    l2.release()


def test_stale_lock_from_dead_process_is_reclaimed(tmp_path):
    lock = SingleInstanceLock("c", lock_dir=str(tmp_path))
    # A lock owned by a (very likely) non-existent PID is stale.
    lock.lock_path.write_text(
        f"pid=9999999\nhost={socket.gethostname()}\nstarted=0\n"
    )
    assert lock.acquire() is True
    lock.release()


def test_force_evicts_live_holder(tmp_path):
    child = _spawn_sleeper()
    try:
        assert _pid_alive(child.pid)
        lock = SingleInstanceLock("d", lock_dir=str(tmp_path))
        lock.lock_path.write_text(
            f"pid={child.pid}\nhost={socket.gethostname()}\nstarted=0\n"
        )
        # Without force it is blocked...
        blocker = SingleInstanceLock("d", lock_dir=str(tmp_path))
        assert blocker.acquire() is False
        # ...with force it terminates the holder and reclaims.
        assert lock.acquire(force=True) is True
        # The child should now be dead.
        assert child.poll() is not None
        lock.release()
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait()


def test_lock_from_another_host_is_not_treated_as_holder(tmp_path):
    lock = SingleInstanceLock("e", lock_dir=str(tmp_path))
    lock.lock_path.write_text("pid=12345\nhost=some-other-machine\nstarted=0\n")
    # Different host -> never considered held by *this* machine, so reclaimable.
    assert lock.acquire() is True
    lock.release()


def test_format_lock_hint_is_actionable():
    hint = format_lock_hint("telegram")
    assert "telegram" in hint
    assert "--replace" in hint
    assert "rm -f" in hint


def test_pid_alive_helpers():
    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(-1) is False
    assert _pid_alive(9999999) is False
