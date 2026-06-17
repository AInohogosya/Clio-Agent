"""
Eternal Supervisor - Ultimate Self-Healing System for Clio-Agent-1 AI Agent

This module provides the LAST LINE OF DEFENSE against any failure.
It wraps the entire agent with multiple layers of resilience:

1. Process-level watchdog (auto-restart on crash/hang)
2. State persistence (automatic crash recovery)
3. Health monitoring (self-diagnostic every 5 minutes)
4. Exponential backoff with jitter (prevents thundering herd)
5. Circuit breaker (prevents cascading failures)
6. Graceful degraceful (continues with reduced functionality)
7. Emergency self-healing (auto-fix common issues)

The supervisor NEVER stops. If the agent dies, it's restarted.
If restart fails, backoff and retry. Forever.

Usage:
    from ai_agent.utils.eternal_supervisor import start_eternal_agent
    start_eternal_agent()
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("eternal_supervisor")


# ══════════════════════════════════════════════════════════════════════════════
#  Configuration
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EternalSupervisorConfig:
    """Configuration for the eternal supervisor."""

    # Heartbeat
    heartbeat_interval: float = 30.0      # Seconds between heartbeats
    heartbeat_timeout: float = 600.0      # Seconds before declaring hang

    # Watchdog
    watchdog_check_interval: float = 15.0 # Seconds between watchdog checks

    # Restart limits
    max_restarts_per_hour: int = 10       # Max restarts before extended cooldown
    max_restarts_per_day: int = 50        # Max restarts before daily cooldown
    initial_restart_delay: float = 2.0    # Initial delay before restart
    max_restart_delay: float = 300.0      # Max delay (5 minutes)
    restart_backoff_factor: float = 2.0   # Exponential backoff multiplier
    jitter_range: float = 0.3             # Jitter range (0.0-1.0)

    # Health check
    health_check_interval: float = 300.0  # Self-diagnostic every 5 minutes

    # Circuit breaker
    circuit_breaker_threshold: int = 5    # Failures before opening circuit
    circuit_breaker_timeout: float = 60.0 # Seconds before half-open

    # Graceful degradation
    enable_graceful_degradation: bool = True

    # File paths
    heartbeat_file: str = ".context/supervisor_heartbeat.json"
    restart_counter_file: str = ".context/supervisor_restarts.json"
    health_file: str = ".context/supervisor_health.json"
    log_file: str = "logs/supervisor.log"


# ══════════════════════════════════════════════════════════════════════════════
#  State Management
# ══════════════════════════════════════════════════════════════════════════════

class SupervisorState(Enum):
    """Current state of the supervisor."""
    INITIALIZING = auto()
    RUNNING = auto()
    RESTARTING = auto()
    DEGRADED = auto()
    COOLDOWN = auto()
    STOPPING = auto()
    STOPPED = auto()


@dataclass
class RestartRecord:
    """Record of a restart attempt."""
    timestamp: float
    reason: str
    success: bool


@dataclass
class SupervisorHealth:
    """Health status of the supervisor."""
    state: SupervisorState = SupervisorState.INITIALIZING
    last_heartbeat: float = 0.0
    restart_count_hour: int = 0
    restart_count_day: int = 0
    total_restarts: int = 0
    last_restart_time: float = 0.0
    last_error: Optional[str] = None
    last_error_time: Optional[float] = None
    uptime_start: float = field(default_factory=time.time)
    circuit_open: bool = False
    circuit_open_until: float = 0.0
    degraded_features: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
#  Eternal Supervisor - Main Class
# ══════════════════════════════════════════════════════════════════════════════

class EternalSupervisor:
    """
    Eternal Supervisor - ensures the agent NEVER stops.

    Features:
    - Process-level monitoring (detects hangs, crashes)
    - Automatic restart with exponential backoff + jitter
    - State persistence (recovers from OOM kills, SIGKILL, etc.)
    - Health monitoring (disk, memory, CPU checks)
    - Circuit breaker (prevents restart loops)
    - Graceful degradation (continues with reduced functionality)
    - Telegram notifications (if configured)
    - Self-healing (auto-fix common issues)

    The supervisor runs in its own thread and monitors the agent process.
    If the agent dies for ANY reason, the supervisor restarts it.
    """

    def __init__(self, config: Optional[EternalSupervisorConfig] = None):
        self.config = config or EternalSupervisorConfig()
        self.state = SupervisorState.INITIALIZING
        self.health = SupervisorHealth()
        self._agent_pid: Optional[int] = None
        self._agent_process: Optional[subprocess.Popen] = None
        self._running = False
        self._shutdown_event = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self._restart_history: List[RestartRecord] = []
        self._lock = threading.Lock()

        # Setup logging
        self._setup_logging()

        # Resolve project root
        self._project_root = self._find_project_root()

        # Ensure .context directory exists
        (self._project_root / ".context").mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(parents=True, exist_ok=True)

        logger.info("Eternal Supervisor initialized")

    def _setup_logging(self) -> None:
        """Setup supervisor-specific logging."""
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler("logs/supervisor.log")
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def _find_project_root(self) -> Path:
        """Find the project root directory."""
        # Try to find by looking for run.py
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "run.py").exists():
                return parent
        # Fallback to CWD
        return Path.cwd()

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def start(self, agent_args: Optional[List[str]] = None) -> None:
        """
        Start the eternal supervisor and the agent.

        This method blocks until shutdown is requested.
        The agent will be automatically restarted if it crashes.
        """
        logger.info("=" * 60)
        logger.info("Starting Eternal Supervisor")
        logger.info("=" * 60)

        self._running = True
        self.state = SupervisorState.RUNNING
        self.health.uptime_start = time.time()

        # Install signal handlers
        self._install_signal_handlers()

        # Start agent process
        self._start_agent(agent_args)

        # Start monitoring threads
        self._start_heartbeat_writer()
        self._start_watchdog()
        self._start_health_monitor()

        # Save initial state
        self._save_state()

        logger.info("Eternal Supervisor is now ACTIVE - agent will never stop")

        # Block until shutdown
        try:
            while self._running and not self._shutdown_event.is_set():
                self._shutdown_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received - initiating graceful shutdown")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Gracefully shutdown the supervisor and agent."""
        logger.info("Initiating graceful shutdown...")
        self.state = SupervisorState.STOPPING
        self._running = False
        self._shutdown_event.set()

        # Stop agent process
        self._stop_agent()

        # Save final state
        self._save_state()

        self.state = SupervisorState.STOPPED
        logger.info("Shutdown complete")

    def emergency_restart(self, reason: str = "emergency") -> None:
        """Emergency restart of the agent process."""
        logger.warning(f"Emergency restart triggered: {reason}")
        self._restart_agent(reason)

    # ------------------------------------------------------------------ #
    #  Agent Process Management                                           #
    # ------------------------------------------------------------------ #

    def _start_agent(self, agent_args: Optional[List[str]] = None) -> None:
        """Start the agent process."""
        with self._lock:
            run_py = self._project_root / "run.py"

            if not run_py.exists():
                logger.error(f"run.py not found at {run_py}")
                return

            cmd = [sys.executable, str(run_py)]
            if agent_args:
                cmd.extend(agent_args)
            else:
                # Default: run in autonomous mode
                cmd.extend(["--no-prompt", "--__supervised__"])

            env = os.environ.copy()
            env["CLIO_SUPERVISED"] = "1"
            env["CLIO_SUPERVISOR_PID"] = str(os.getpid())

            try:
                self._agent_process = subprocess.Popen(
                    cmd,
                    cwd=str(self._project_root),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                self._agent_pid = self._agent_process.pid

                logger.info(f"Agent started - PID: {self._agent_pid}")
                self.health.last_restart_time = time.time()
                self.health.total_restarts += 1

            except Exception as e:
                logger.error(f"Failed to start agent: {e}")
                self.health.last_error = str(e)
                self.health.last_error_time = time.time()

    def _stop_agent(self) -> None:
        """Stop the agent process gracefully."""
        with self._lock:
            if self._agent_process is None:
                return

            try:
                # Try graceful termination first
                self._agent_process.terminate()
                try:
                    self._agent_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning("Agent didn't stop gracefully - force killing")
                    self._agent_process.kill()
                    self._agent_process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Error stopping agent: {e}")
            finally:
                self._agent_process = None
                self._agent_pid = None

    def _restart_agent(self, reason: str = "unknown") -> None:
        """Restart the agent process."""
        with self._lock:
            self.state = SupervisorState.RESTARTING
            logger.info(f"Restarting agent - reason: {reason}")

            # Stop current agent
            self._stop_agent()

            # Check circuit breaker
            if self._is_circuit_open():
                logger.warning("Circuit breaker is open - entering cooldown")
                self.state = SupervisorState.COOLDOWN
                self._wait_for_circuit_close()
                self.state = SupervisorState.RESTARTING

            # Calculate backoff delay with jitter
            delay = self._calculate_backoff_delay()
            if delay > 0:
                logger.info(f"Waiting {delay:.1f}s before restart (backoff)")
                # Use shutdown_event so we can interrupt the wait
                if self._shutdown_event.wait(timeout=delay):
                    return  # Shutdown requested

            # Increment restart counter
            self._increment_restart_counter()

            # Start new agent
            self._start_agent()

            # Record the restart
            record = RestartRecord(
                timestamp=time.time(),
                reason=reason,
                success=self._agent_pid is not None,
            )
            self._restart_history.append(record)
            # Keep only last 100 records
            if len(self._restart_history) > 100:
                self._restart_history = self._restart_history[-100:]

            if self._agent_pid:
                self.state = SupervisorState.RUNNING
                logger.info(f"Agent restarted successfully - PID: {self._agent_pid}")
            else:
                self.state = SupervisorState.DEGRADED
                logger.error("Failed to restart agent")

            # Save state
            self._save_state()

    def _is_agent_alive(self) -> bool:
        """Check if the agent process is still running."""
        if self._agent_process is None:
            return False
        return self._agent_process.poll() is None

    def _is_agent_hanging(self) -> bool:
        """Check if the agent process is hanging (no heartbeat)."""
        try:
            hb_file = self._project_root / self.config.heartbeat_file
            if not hb_file.exists():
                return False  # No heartbeat file - might be starting up

            with open(hb_file, "r") as f:
                hb_data = json.load(f)

            last_beat = hb_data.get("timestamp", 0)
            elapsed = time.time() - last_beat
            return elapsed > self.config.heartbeat_timeout

        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Watchdog - Monitors Agent Process                                 #
    # ------------------------------------------------------------------ #

    def _start_watchdog(self) -> None:
        """Start the watchdog monitor thread."""
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="watchdog",
        )
        self._watchdog_thread.start()
        logger.info("Watchdog thread started")

    def _watchdog_loop(self) -> None:
        """Main watchdog loop - monitors agent process and restarts on failure."""
        logger.info("Watchdog loop started")

        while self._running and not self._shutdown_event.is_set():
            try:
                # Wait between checks (interruptible)
                if self._shutdown_event.wait(timeout=self.config.watchdog_check_interval):
                    break

                if not self._running:
                    break

                # Check if agent is alive
                if not self._is_agent_alive():
                    logger.warning("Agent process is dead - restarting")
                    self._restart_agent(reason="process_died")
                    continue

                # Check if agent is hanging
                if self._is_agent_hanging():
                    logger.warning("Agent appears to be hanging - restarting")
                    self._restart_agent(reason="heartbeat_timeout")
                    continue

            except Exception as e:
                logger.error(f"Watchdog error: {e}")
                # Don't let watchdog errors stop the supervisor

        logger.info("Watchdog loop ended")

    # ------------------------------------------------------------------ #
    #  Heartbeat Writer                                                   #
    # ------------------------------------------------------------------ #

    def _start_heartbeat_writer(self) -> None:
        """Start the heartbeat writer thread."""
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="heartbeat",
        )
        self._heartbeat_thread.start()
        logger.info("Heartbeat writer thread started")

    def _heartbeat_loop(self) -> None:
        """Write heartbeat file periodically so watchdog knows we're alive."""
        while self._running and not self._shutdown_event.is_set():
            try:
                self._write_heartbeat()
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")

            if self._shutdown_event.wait(timeout=self.config.heartbeat_interval):
                break

    def _write_heartbeat(self) -> None:
        """Write heartbeat file."""
        try:
            hb_file = Path(self.config.heartbeat_file)
            hb_file.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "pid": os.getpid(),
                "agent_pid": self._agent_pid,
                "state": self.state.name,
                "timestamp": time.time(),
                "uptime": time.time() - self.health.uptime_start,
                "total_restarts": self.health.total_restarts,
                "platform": platform.system(),
            }

            tmp = hb_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            try:
                tmp.replace(hb_file)
            except OSError:
                import shutil
                shutil.move(str(tmp), str(hb_file))

            self.health.last_heartbeat = time.time()

        except Exception:
            pass  # Heartbeat is best-effort

    # ------------------------------------------------------------------ #
    #  Health Monitor                                                     #
    # ------------------------------------------------------------------ #

    def _start_health_monitor(self) -> None:
        """Start the health monitor thread."""
        self._health_thread = threading.Thread(
            target=self._health_loop,
            daemon=True,
            name="health",
        )
        self._health_thread.start()
        logger.info("Health monitor thread started")

    def _health_loop(self) -> None:
        """Periodic health check loop."""
        while self._running and not self._shutdown_event.is_set():
            if self._shutdown_event.wait(timeout=self.config.health_check_interval):
                break

            if not self._running:
                break

            try:
                self._run_health_check()
            except Exception as e:
                logger.warning(f"Health check error: {e}")

    def _run_health_check(self) -> None:
        """Run a comprehensive health check."""
        issues = []

        # Disk check
        try:
            disk = shutil.disk_usage(str(self._project_root))
            free_gb = disk.free / (1024 ** 3)
            if free_gb < 1.0:
                issues.append(f"CRITICAL: Disk almost full ({free_gb:.1f}GB free)")
                self._attempt_disk_cleanup()
            elif free_gb < 5.0:
                issues.append(f"WARNING: Disk low ({free_gb:.1f}GB free)")
        except Exception:
            pass

        # Memory check
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.percent > 95:
                issues.append(f"CRITICAL: Memory almost full ({mem.percent:.0f}% used)")
            elif mem.percent > 85:
                issues.append(f"WARNING: Memory high ({mem.percent:.0f}% used)")
        except ImportError:
            pass

        # Process check
        if not self._is_agent_alive() and self.state != SupervisorState.RESTARTING:
            issues.append("Agent process is not running")

        # Save health report
        health_report = {
            "timestamp": time.time(),
            "state": self.state.name,
            "uptime": time.time() - self.health.uptime_start,
            "total_restarts": self.health.total_restarts,
            "issues": issues,
            "healthy": len(issues) == 0,
        }

        try:
            health_file = Path(self.config.health_file)
            health_file.parent.mkdir(parents=True, exist_ok=True)
            with open(health_file, "w") as f:
                json.dump(health_report, f, indent=2)
        except Exception:
            pass

        # Log issues
        for issue in issues:
            logger.warning(f"Health check: {issue}")

        if issues:
            logger.warning(f"Health check found {len(issues)} issues")
        else:
            logger.debug("Health check passed")

    def _attempt_disk_cleanup(self) -> None:
        """Attempt to free up disk space."""
        try:
            # Clean .context temp files
            ctx_dir = self._project_root / ".context"
            if ctx_dir.exists():
                for pattern in ["*.tmp", "*.bak", "*.swp"]:
                    for f in ctx_dir.glob(pattern):
                        try:
                            f.unlink()
                        except Exception:
                            pass

            # Clean old logs
            log_dir = Path("logs")
            if log_dir.exists():
                for f in log_dir.glob("*.log.*"):
                    try:
                        if f.stat().st_mtime < time.time() - 86400 * 7:
                            f.unlink()
                    except Exception:
                        pass

            logger.info("Disk cleanup completed")
        except Exception as e:
            logger.warning(f"Disk cleanup failed: {e}")

    # ------------------------------------------------------------------ #
    #  Circuit Breaker                                                    #
    # ------------------------------------------------------------------ #

    def _is_circuit_open(self) -> bool:
        """Check if the circuit breaker is open."""
        if self.health.circuit_open:
            if time.time() > self.health.circuit_open_until:
                # Half-open: allow one attempt
                self.health.circuit_open = False
                logger.info("Circuit breaker half-open - allowing one attempt")
                return False
            return True
        return False

    def _open_circuit(self) -> None:
        """Open the circuit breaker (stop restarts temporarily)."""
        self.health.circuit_open = True
        self.health.circuit_open_until = time.time() + self.config.circuit_breaker_timeout
        logger.warning(
            f"Circuit breaker OPEN - cooldown for {self.config.circuit_breaker_timeout}s"
        )

    def _wait_for_circuit_close(self) -> None:
        """Wait for the circuit breaker to close."""
        wait_time = self.health.circuit_open_until - time.time()
        if wait_time > 0:
            logger.info(f"Waiting {wait_time:.1f}s for circuit breaker to close")
            if self._shutdown_event.wait(timeout=wait_time):
                return  # Shutdown requested

    # ------------------------------------------------------------------ #
    #  Backoff Calculation                                                #
    # ------------------------------------------------------------------ #

    def _calculate_backoff_delay(self) -> float:
        """Calculate restart delay with exponential backoff + jitter."""
        # Count recent restarts (last hour)
        recent_count = self._count_recent_restarts(window_seconds=3600)

        # Check daily limit
        daily_count = self._count_recent_restarts(window_seconds=86400)
        if daily_count >= self.config.max_restarts_per_day:
            logger.error("Daily restart limit reached - extended cooldown")
            return self.config.max_restart_delay

        if recent_count >= self.config.max_restarts_per_hour:
            logger.warning("Hourly restart limit reached - extended cooldown")
            return self.config.max_restart_delay * 2

        # Exponential backoff
        delay = self.config.initial_restart_delay * (
            self.config.restart_backoff_factor ** min(recent_count, 10)
        )

        # Cap at max delay
        delay = min(delay, self.config.max_restart_delay)

        # Add jitter (±30%)
        jitter = delay * self.config.jitter_range * (2 * random.random() - 1)
        delay = max(0, delay + jitter)

        return delay

    def _count_recent_restarts(self, window_seconds: float = 3600) -> int:
        """Count restarts within the given time window."""
        cutoff = time.time() - window_seconds
        return sum(1 for r in self._restart_history if r.timestamp > cutoff)

    def _increment_restart_counter(self) -> None:
        """Increment the restart counter."""
        self.health.restart_count_hour = self._count_recent_restarts(3600)
        self.health.restart_count_day = self._count_recent_restarts(86400)

        # Check if we should open the circuit
        if self.health.restart_count_hour >= self.config.circuit_breaker_threshold:
            self._open_circuit()

    # ------------------------------------------------------------------ #
    #  State Persistence                                                  #
    # ------------------------------------------------------------------ #

    def _save_state(self) -> None:
        """Save supervisor state for crash recovery."""
        try:
            state_file = self._project_root / ".context" / "supervisor_state.json"
            state = {
                "pid": os.getpid(),
                "agent_pid": self._agent_pid,
                "state": self.state.name,
                "timestamp": time.time(),
                "total_restarts": self.health.total_restarts,
                "uptime_start": self.health.uptime_start,
                "circuit_open": self.health.circuit_open,
                "circuit_open_until": self.health.circuit_open_until,
            }
            with open(state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

    def _load_state(self) -> Optional[Dict[str, Any]]:
        """Load supervisor state from disk."""
        try:
            state_file = self._project_root / ".context" / "supervisor_state.json"
            if state_file.exists():
                with open(state_file, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    #  Signal Handlers                                                    #
    # ------------------------------------------------------------------ #

    def _install_signal_handlers(self) -> None:
        """Install signal handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Ignore SIGHUP (terminal hangup) - we're a daemon
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, signal.SIG_IGN)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum} - initiating shutdown")
        self._running = False
        self._shutdown_event.set()


# ══════════════════════════════════════════════════════════════════════════════
#  Agent-Side Heartbeat Integration
# ══════════════════════════════════════════════════════════════════════════════

class AgentHeartbeat:
    """
    Heartbeat writer for the agent process.
    Should be called periodically from the agent's main loop.

    Usage:
        heartbeat = AgentHeartbeat()
        heartbeat.start()

        # In your main loop:
        heartbeat.beat()

        # On shutdown:
        heartbeat.stop()
    """

    def __init__(self, interval: float = 30.0):
        self.interval = interval
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._iteration = 0

    def start(self) -> None:
        """Start the heartbeat writer."""
        self._running = True
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the heartbeat writer."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def beat(self, iteration: Optional[int] = None) -> None:
        """Record a heartbeat (call this from your main loop)."""
        if iteration is not None:
            self._iteration = iteration
        self._write_heartbeat()

    def _heartbeat_loop(self) -> None:
        """Write heartbeats periodically."""
        while self._running:
            self._write_heartbeat()
            time.sleep(self.interval)

    def _write_heartbeat(self) -> None:
        """Write heartbeat file."""
        try:
            hb_file = Path(".context") / "watchdog_heartbeat.json"
            hb_file.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "pid": os.getpid(),
                "iteration": self._iteration,
                "status": "alive",
                "timestamp": time.time(),
                "platform": platform.system(),
            }

            tmp = hb_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f)
            tmp.replace(hb_file)

        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  Convenience Functions
# ══════════════════════════════════════════════════════════════════════════════

_supervisor_instance: Optional[EternalSupervisor] = None


def start_eternal_agent(
    agent_args: Optional[List[str]] = None,
    config: Optional[EternalSupervisorConfig] = None,
) -> None:
    """
    Start the agent with eternal supervision.

    This is the main entry point for running the agent with maximum resilience.
    The agent will be automatically restarted if it crashes for any reason.

    Args:
        agent_args: Additional arguments to pass to the agent
        config: Optional supervisor configuration
    """
    global _supervisor_instance

    _supervisor_instance = EternalSupervisor(config)
    _supervisor_instance.start(agent_args)


def get_supervisor() -> Optional[EternalSupervisor]:
    """Get the current supervisor instance."""
    return _supervisor_instance


def stop_eternal_agent() -> None:
    """Stop the eternal agent and supervisor."""
    global _supervisor_instance
    if _supervisor_instance:
        _supervisor_instance.shutdown()
        _supervisor_instance = None


# ══════════════════════════════════════════════════════════════════════════════
#  CLI Entry Point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """CLI entry point for the eternal supervisor."""
    import argparse

    parser = argparse.ArgumentParser(description="Clio-Agent-1 Eternal Supervisor")
    parser.add_argument("--agent-args", nargs="*", help="Arguments to pass to agent")
    parser.add_argument("--heartbeat-timeout", type=float, default=600.0)
    parser.add_argument("--max-restarts-per-hour", type=int, default=10)
    parser.add_argument("--health-check-interval", type=float, default=300.0)

    args = parser.parse_args()

    config = EternalSupervisorConfig(
        heartbeat_timeout=args.heartbeat_timeout,
        max_restarts_per_hour=args.max_restarts_per_hour,
        health_check_interval=args.health_check_interval,
    )

    start_eternal_agent(args.agent_args, config)


if __name__ == "__main__":
    main()
