"""
Watchdog / Supervisor for Clio-Agent-1 AI Agent

A separate lightweight process that monitors the main agent process and
automatically restarts it if it crashes, hangs, or becomes unresponsive.

This is the LAST LINE OF DEFENSE — if everything else fails, the watchdog
brings the agent back to life.

Usage:
    from ai_agent.utils.watchdog import start_watchdog, stop_watchdog
    child_pid = start_watchdog(target_func, *args, **kwargs)
    # ... later ...
    stop_watchdog(child_pid)
"""

import os
import sys
import time
import subprocess
import threading
import json
import fcntl
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .logger import get_logger
from .platform_compat import (
    is_process_alive, kill_process, spawn_detached, is_windows,
)

logger = get_logger("watchdog")

# How long (seconds) the child must be "quiet" (no heartbeat) before we
# consider it hung and kill + restart it.
DEFAULT_HEARTBEAT_TIMEOUT = 600  # 10 minutes

# Interval (seconds) at which the child writes a heartbeat.
HEARTBEAT_WRITE_INTERVAL = 30

# Interval (seconds) at which the watchdog checks the heartbeat.
WATCHDOG_CHECK_INTERVAL = 15

# Maximum number of restarts within the cooldown window before the watchdog
# gives up and exits itself.
MAX_RESTARTS_PER_WINDOW = 10
RESTART_WINDOW_SECONDS = 3600  # 1 hour

# Minimum delay before first restart (prevents CPU spin on instant crashes)
MIN_RESTART_DELAY = 2.0

# Heartbeat file path
HEARTBEAT_FILE = Path(".context") / "watchdog_heartbeat.json"

# Restart counter file (file-locked for safe concurrent access)
_RESTART_COUNTER_FILE = Path(".context") / "watchdog_restarts.json"


def _write_heartbeat(pid: int, iteration: int, status: str = "alive") -> None:
    """Write a heartbeat file so the watchdog knows we are alive.

    Uses atomic write (write to .tmp then rename) to prevent partial reads.
    Cleans up any stale .tmp file first.
    """
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "pid": pid,
            "iteration": iteration,
            "status": status,
            "timestamp": time.time(),
        }
        tmp = HEARTBEAT_FILE.with_suffix(".tmp")
        # Clean up any stale .tmp file first
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(HEARTBEAT_FILE)
    except Exception:
        pass  # Heartbeat is best-effort


def _read_heartbeat() -> Optional[Dict[str, Any]]:
    """Read the heartbeat file. Returns None if missing or corrupt."""
    try:
        if HEARTBEAT_FILE.exists():
            with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _get_restart_count() -> Tuple[int, float]:
    """Read the restart counter file. Returns (count, window_start)."""
    counter_file = Path(".context") / "watchdog_restarts.json"
    try:
        if counter_file.exists():
            with open(counter_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("count", 0)), float(data.get("window_start", 0))
    except Exception:
        pass
    return 0, 0.0


def _set_restart_count(count: int, window_start: float) -> None:
    """Write the restart counter file."""
    counter_file = Path(".context") / "watchdog_restarts.json"
    try:
        counter_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"count": count, "window_start": window_start}
        tmp = counter_file.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        # Atomic replace; on Windows this may fail if target is locked
        try:
            tmp.replace(counter_file)
        except OSError:
            # Fallback: direct overwrite
            import shutil
            shutil.move(str(tmp), str(counter_file))
    except Exception:
        pass


def _increment_restart_count() -> int:
    """Increment the restart counter with file locking. Returns the new count.

    Uses advisory file locking to prevent TOCTOU races when multiple
    watchdog instances or concurrent processes update the counter.
    """
    counter_file = Path(".context") / "watchdog_restarts.json"
    try:
        counter_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    try:
        # Open (or create) the counter file and acquire an exclusive lock
        with open(counter_file, "a+", encoding="utf-8") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX)
            except (AttributeError, OSError):
                # fcntl not available on Windows — fall back to unlocked
                pass
            try:
                f.seek(0)
                raw = f.read()
                data = json.loads(raw) if raw.strip() else {}
                count = int(data.get("count", 0))
                window_start = float(data.get("window_start", 0))
            except Exception:
                count = 0
                window_start = 0.0

            now = time.time()
            if now - window_start > RESTART_WINDOW_SECONDS:
                count = 0
                window_start = now
            count += 1

            # Write back
            f.seek(0)
            f.truncate()
            json.dump({"count": count, "window_start": window_start}, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except (AttributeError, OSError):
                pass
            return count
    except Exception:
        return 1  # conservative: assume at least 1 restart


class WatchdogSupervisor:
    """
    Runs the target function in a child process and monitors it.
    If the child dies or hangs, restart it automatically.
    """

    def __init__(
        self,
        target_func: Callable,
        args: Tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT,
        max_restarts_per_window: int = MAX_RESTARTS_PER_WINDOW,
    ):
        self.target_func = target_func
        self.args = args
        self.kwargs = kwargs or {}
        self.heartbeat_timeout = heartbeat_timeout
        self.max_restarts_per_window = max_restarts_per_window
        self._child_pid: Optional[int] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the watchdog supervisor (non-blocking)."""
        self._running = True
        self._thread = threading.Thread(target=self._supervisor_loop, daemon=True)
        self._thread.start()
        logger.info("Watchdog supervisor started")

    def stop(self) -> None:
        """Stop the watchdog and its child process."""
        self._running = False
        if self._child_pid and _is_process_alive(self._child_pid):
            _kill_process(self._child_pid)
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Watchdog supervisor stopped")

    def _supervisor_loop(self) -> None:
        """Main supervisor loop: spawn child, monitor, restart on failure."""
        while self._running:
            # Check restart budget
            restart_count = _increment_restart_count()
            if restart_count > self.max_restarts_per_window:
                logger.critical(
                    f"Watchdog: too many restarts ({restart_count}) in window. "
                    "Giving up to prevent restart loops."
                )
                self._running = False
                break

            # Minimum delay before first restart to prevent CPU spin
            if restart_count <= 1:
                time.sleep(MIN_RESTART_DELAY)

            logger.info(f"Watchdog: spawning child process (restart #{restart_count})")

            # Spawn the child process
            child_pid = self._spawn_child()
            if child_pid is None:
                logger.error("Watchdog: failed to spawn child, retrying in 10s...")
                time.sleep(10)
                continue

            self._child_pid = child_pid

            # Monitor loop
            while self._running and is_process_alive(child_pid):
                time.sleep(WATCHDOG_CHECK_INTERVAL)

                # Check heartbeat
                hb = _read_heartbeat()
                if hb is None:
                    # No heartbeat file yet — might be early startup
                    continue

                last_beat = hb.get("timestamp", 0)
                elapsed = time.time() - last_beat
                if elapsed > self.heartbeat_timeout:
                    logger.warning(
                        f"Watchdog: child {child_pid} heartbeat stale "
                        f"({elapsed:.0f}s > {self.heartbeat_timeout}s). Killing."
                    )
                    kill_process(child_pid)
                    break

            if not self._running:
                break

            # Child died — log and restart
            if is_process_alive(child_pid):
                kill_process(child_pid)

            logger.warning(f"Watchdog: child {child_pid} exited. Restarting in 5s...")
            time.sleep(5)

    def _spawn_child(self) -> Optional[int]:
        """
        Spawn the target function in a child process.
        Returns the child PID or None on failure.
        """
        try:
            script_dir = Path(__file__).resolve().parents[2]  # project root
            run_py = script_dir / "run.py"

            if run_py.exists():
                env = os.environ.copy()
                env["CLIO_WATCHDOG_CHILD"] = "1"
                cmd = [sys.executable, str(run_py), "--__watchdog_spawned__"] + sys.argv[1:]
                return spawn_detached(cmd, cwd=str(script_dir), env=env)
            else:
                logger.error(f"Watchdog: run.py not found at {run_py}")
                return None
        except Exception as e:
            logger.error(f"Watchdog: spawn failed: {e}")
            return None


def start_watchdog(
    target_func: Optional[Callable] = None,
    *args: Any,
    **kwargs: Any,
) -> Optional[int]:
    """
    Start the watchdog supervisor. Returns the supervisor thread's PID
    (or None if watchdog is disabled).
    """
    # Allow disabling via env
    if os.getenv("CLIO_WATCHDOG_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("Watchdog disabled via CLIO_WATCHDOG_DISABLED")
        return None

    heartbeat_timeout = float(
        os.getenv("CLIO_WATCHDOG_TIMEOUT", DEFAULT_HEARTBEAT_TIMEOUT)
    )

    supervisor = WatchdogSupervisor(
        target_func=target_func or _default_target,
        args=args,
        kwargs=kwargs,
        heartbeat_timeout=heartbeat_timeout,
    )
    supervisor.start()
    return os.getpid()


def stop_watchdog(child_pid: Optional[int] = None) -> None:
    """Stop the watchdog. (Placeholder — in practice the supervisor is daemon.)"""
    pass


def _default_target() -> None:
    """Default target: re-run main() from run.py."""
    try:
        from run import main
        main()
    except Exception:
        pass


def install_heartbeat_writer(iteration_getter: Callable[[], int]) -> None:
    """
    Install a background thread that writes heartbeats so the watchdog
    knows the agent is alive.

    Args:
        iteration_getter: callable that returns the current iteration count.
    """
    def _heartbeat_loop():
        while True:
            try:
                _write_heartbeat(
                    pid=os.getpid(),
                    iteration=iteration_getter(),
                    status="alive",
                )
            except Exception:
                pass
            time.sleep(HEARTBEAT_WRITE_INTERVAL)

    t = threading.Thread(target=_heartbeat_loop, daemon=True)
    t.start()
    logger.info("Heartbeat writer installed")
