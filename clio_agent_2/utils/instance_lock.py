"""
Cross-process single-instance lock for Clio-Agent-2.

Why this exists
---------------
A Telegram bot may have **only one** active ``getUpdates`` (polling) session at
a time. If two processes poll the same bot token, Telegram aborts *both* with::

    telegram.error.Conflict: terminated by other getUpdates request;
    make sure that only one bot instance is running

This used to happen in two recurring situations:

1. An orphaned/stale bot process left running on the deployment server (from a
   previous deploy, a crash that bypassed cleanup, or a ``nohup``'d session).
2. Launching ``run.py --telegram`` while ``--all`` (or another ``--telegram``)
   is already running on the same machine.

This module guarantees at most one polling instance per machine via a PID-file
lock. It:

* refuses to start a second instance while a *live* process holds the lock
  (so the launcher can bail out with a clear, actionable message instead of a
  noisy 409 crash), and
* automatically reclaims a *stale* lock left behind by a process that died
  without releasing it (crashed / SIGKILL'd).

Optionally (``force=True`` / ``--replace``) it can take over from a live
holder by terminating it first -- handy for deployments where an orphaned
process needs to be evicted automatically.

The lock lives in the system temp directory, so it is never committed to the
repository and is naturally per-user / per-machine.
"""

from __future__ import annotations

import logging
import os
import signal as _signal
import socket
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    """Raised by the context-manager form when another live instance holds the lock."""


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` currently exists.

    Uses ``os.kill(pid, 0)`` (POSIX + Windows), which raises
    ``ProcessLookupError`` for a dead PID and ``PermissionError`` when the PID
    exists but belongs to another user.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we cannot signal it -> treat as alive so we never
        # blindly steal a lock we cannot confirm is stale.
        return True
    except (OSError, ValueError):
        return False
    return True


class SingleInstanceLock:
    """A PID-file based single-instance lock.

    Usage::

        lock = SingleInstanceLock("telegram")
        if not lock.acquire():
            print(format_lock_hint("telegram"))
            return
        try:
            ... run ...
        finally:
            lock.release()

    Or as a context manager (raises ``AlreadyRunningError`` on contention)::

        with SingleInstanceLock("telegram"):
            ... run ...
    """

    def __init__(self, name: str, lock_dir: Optional[str] = None):
        self.name = name
        base = Path(lock_dir) if lock_dir else Path(tempfile.gettempdir())
        self.lock_path = base / f"clio_agent2_{name}.lock"
        self.hostname = socket.gethostname()
        self._acquired = False

    # -- internals ---------------------------------------------------------

    def _read(self) -> Optional[dict]:
        try:
            raw = self.lock_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return None
        if not raw:
            return None
        info: dict = {}
        for line in raw.splitlines():
            if line.startswith("pid="):
                try:
                    info["pid"] = int(line[len("pid="):])
                except ValueError:
                    info["pid"] = None
            elif line.startswith("host="):
                info["host"] = line[len("host="):]
        return info or None

    def _write(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            f"pid={os.getpid()}\n"
            f"host={self.hostname}\n"
            f"started={time.time():.0f}\n"
        )
        # Write to a temp file then rename for an atomic-ish replace.
        tmp = self.lock_path.with_name(self.lock_path.name + ".tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.lock_path)
        except OSError:
            # Fall back to a direct write if the temp-rename path is unusual.
            self.lock_path.write_text(payload, encoding="utf-8")


    def _is_live_holder(self, info: dict) -> bool:
        """True if the lock is held by a *live* process on *this* host."""
        pid = info.get("pid")
        host = info.get("host")
        if pid is None:
            return False
        # A lock from another machine shares no PID namespace with us, so it
        # can never be the source of *our* Conflict -- never treat it as held.
        if host and host != self.hostname:
            return False
        return _pid_alive(pid)

    def _terminate_holder(self, pid: int) -> bool:
        """Best-effort terminate a live holder. Returns True if it is now dead.

        Sends SIGTERM, waits up to ~10s, then escalates to SIGKILL. Returns
        False only when we are not permitted to signal the process (in which
        case reclaiming would be unsafe).
        """
        for sig in (_signal.SIGTERM, _signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return True  # Already gone.
            except PermissionError:
                logger.warning("No permission to terminate existing instance PID %s", pid)
                return False
            except (OSError, ValueError):
                return False
            # Wait (up to ~10s) for the process to die. If we are the holder's
            # parent, reap a zombie via waitpid so ``os.kill(pid, 0)`` reflects
            # reality (a zombie still answers kill(pid, 0) successfully).
            for _ in range(40):
                try:
                    os.waitpid(pid, os.WNOHANG)
                except (ChildProcessError, OSError):
                    # Not our child (e.g. an orphaned process whose parent is
                    # init) -- its real parent reaps it, so skip the reap.
                    pass
                if not _pid_alive(pid):
                    return True
                time.sleep(0.25)
        return not _pid_alive(pid)

    def _try_acquire(self, force: bool = False) -> bool:
        holder = self._read()
        if holder and self._is_live_holder(holder):
            if force:
                logger.warning(
                    "Taking over from existing instance PID %s (--replace)",
                    holder.get("pid"),
                )
                if not self._terminate_holder(holder["pid"]):
                    return False  # Couldn't evict safely; do not steal.
                # Fall through to reclaim below.
            else:
                return False
        # No live holder (or we evicted it) -> reclaim/overwrite the lock.
        self._write()
        self._acquired = True
        return True

    # -- public API --------------------------------------------------------

    def acquire(self, blocking: bool = False, timeout: float = 0.0,
                force: bool = False) -> bool:
        """Attempt to acquire the lock.

        Returns True on success. Returns False if a *live* process holds it and
        ``force`` is False. With ``force=True`` an existing live holder is
        terminated before reclaiming.
        """
        if not blocking:
            return self._try_acquire(force=force)
        deadline = time.time() + max(0.0, timeout)
        while True:
            if self._try_acquire(force=force):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.25)

    def is_held_by_other(self) -> bool:
        """True if a *live* process on this host currently holds the lock."""
        holder = self._read()
        return bool(holder) and self._is_live_holder(holder)

    def release(self) -> None:
        """Release the lock (only if it still belongs to this process)."""
        if not self._acquired:
            return
        try:
            info = self._read()
            if info and info.get("pid") == os.getpid():
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
        except OSError:
            pass
        self._acquired = False

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire(blocking=False):
            raise AlreadyRunningError(
                f"Another Clio-Agent-2 instance ({self.name}) is already "
                f"running on this machine."
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def format_lock_hint(name: str) -> str:
    """Human-friendly guidance shown when the lock cannot be acquired."""
    lock_path = Path(tempfile.gettempdir()) / f"clio_agent2_{name}.lock"
    return (
        f"❌ Another Clio-Agent-2 {name} instance is already running on this machine.\n"
        f"   Telegram allows only ONE polling session per bot token, so a second\n"
        f"   instance would immediately fail with a 'Conflict: terminated by\n"
        f"   other getUpdates request' error.\n"
        f"   Stop the existing instance first (Ctrl+C), or kill its process, then\n"
        f"   restart. To take over automatically, run with:  python3 run.py --{name} --replace\n"
        f"   If the other process already died, remove the stale lock and retry:\n"
        f"       rm -f {lock_path}"
    )

