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
import signal
import subprocess
import threading
import json
import platform
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .logger import get_logger

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

# Heartbeat file path
HEARTBEAT_FILE = Path(".context") / "watchdog_heartbeat.json"


def _write_heartbeat(pid: int, iteration: int, status: str = "alive") -> None:
    """Write a heartbeat file so the watchdog knows we are alive."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "pid": pid,
            "iteration": iteration,
            "status": status,
            "timestamp": time.time(),
            "platform": platform.system(),
        }
        tmp = HEARTBEAT_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
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


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # Process exists but we can't signal it
        except Exception:
            return False


def _kill_process(pid: int) -> None:
    """Force-kill a process."""
    if platform.system() == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(2)
            if _is_process_alive(pid):
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


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
    """Increment the restart counter. Returns the new count."""
    count, window_start = _get_restart_count()
    now = time.time()
    if now - window_start > RESTART_WINDOW_SECONDS:
        count = 0
        window_start = now
    count += 1
    _set_restart_count(count, window_start)
    return count


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

            logger.info(f"Watchdog: spawning child process (restart #{restart_count})")

            # Spawn the child process
            child_pid = self._spawn_child()
            if child_pid is None:
                logger.error("Watchdog: failed to spawn child, retrying in 10s...")
                time.sleep(10)
                continue

            self._child_pid = child_pid

            # Monitor loop
            while self._running and _is_process_alive(child_pid):
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
                    _kill_process(child_pid)
                    break

            if not self._running:
                break

            # Child died — log and restart
            if _is_process_alive(child_pid):
                _kill_process(child_pid)

            logger.warning(f"Watchdog: child {child_pid} exited. Restarting in 5s...")
            time.sleep(5)

    def _spawn_child(self) -> Optional[int]:
        """
        Spawn the target function in a child process.
        Returns the child PID or None on failure.
        """
        try:
            # We use subprocess to run the target in a separate process.
            # The target_func must be picklable or we use run.py as entry.
            # For simplicity, we re-exec run.py with the same arguments.
            script_dir = Path(__file__).resolve().parents[2]  # project root
            run_py = script_dir / "run.py"

            if run_py.exists():
                # Re-run the current command
                env = os.environ.copy()
                env["CLIO_WATCHDOG_CHILD"] = "1"
                proc = subprocess.Popen(
                    [sys.executable, str(run_py), "--__watchdog_spawned__"] + sys.argv[1:],
                    cwd=str(script_dir),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return proc.pid
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
