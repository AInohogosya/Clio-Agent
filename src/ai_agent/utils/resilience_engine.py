"""
Resilience Engine for Clio-Agent-1 AI Agent

Provides:
1. Intelligent error classification with auto-recovery strategies
2. Provider failover with circuit breaker (integrated with ProviderFallbackManager)
3. Self-healing command execution (auto-fix common errors)
4. Telegram-aware error notification (always notifies user in Telegram mode)
5. Global exception hook (sys.excepthook / threading.excepthook)
6. Graceful degradation for all subsystems

This module is designed to be the SINGLE place where all error handling
policy lives.  Other modules should call into this engine rather than
implementing ad-hoc retry / recovery logic.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
import traceback as _traceback_module
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Sequence, Tuple, Type, Union

from .exceptions import (
    APIError,
    AIAgentException,
    ErrorCategory,
    ErrorContext,
    ErrorHandler,
    ExecutionError,
)
from .logger import get_logger

logger = get_logger("resilience_engine")


# ══════════════════════════════════════════════════════════════════════════════
#  Error classification helpers
# ══════════════════════════════════════════════════════════════════════════════

class ErrorSeverity(Enum):
    LOW = auto()        # Can be ignored, logged only
    MEDIUM = auto()     # Logged, may trigger retry
    HIGH = auto()       # Triggers retry + failover
    CRITICAL = auto()   # Triggers full recovery + user notification


@dataclass
class RecoveryAction:
    """A single recovery action that the engine can try."""
    name: str
    description: str
    func: Optional[Callable[..., Any]] = None
    func_args: Tuple = ()
    func_kwargs: Dict[str, Any] = field(default_factory=dict)
    max_retries: int = 1
    retry_delay: float = 1.0


# ══════════════════════════════════════════════════════════════════════════════
#  Self-healing command suggestions
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CommandFix:
    """Maps an error pattern to a suggested fix command."""
    pattern: re.Pattern
    description: str
    fix_commands: List[str]


# Common error -> fix mappings (extended over time)
_COMMAND_FIXES: List[CommandFix] = [
    CommandFix(
        pattern=re.compile(r"command not found:\s*(\S+)", re.IGNORECASE),
        description="Command not found — install the missing package",
        fix_commands=[],  # Filled dynamically below
    ),
    CommandFix(
        pattern=re.compile(r"permission denied", re.IGNORECASE),
        description="Permission denied — try with elevated permissions",
        fix_commands=["sudo {command}"],
    ),
    CommandFix(
        pattern=re.compile(r"no such file or directory:\s*(.+)", re.IGNORECASE),
        description="File not found — check path",
        fix_commands=[],  # No generic fix
    ),
    CommandFix(
        pattern=re.compile(r"could not resolve host|name or service not known", re.IGNORECASE),
        description="DNS / network error",
        fix_commands=[],  # Will trigger network check
    ),
    CommandFix(
        pattern=re.compile(r"connection refused", re.IGNORECASE),
        description="Connection refused — service not running",
        fix_commands=[],  # Filled dynamically per-service
    ),
    CommandFix(
        pattern=re.compile(r"ssl.*error|certificate verify failed", re.IGNORECASE),
        description="SSL / certificate error",
        fix_commands=["pip install --upgrade certifi"],
    ),
    CommandFix(
        pattern=re.compile(r"disk full|no space left", re.IGNORECASE),
        description="Disk full",
        fix_commands=[],  # User intervention required
    ),
    CommandFix(
        pattern=re.compile(r"pip.*ERROR.*(Could not find a version|No matching distribution)", re.IGNORECASE),
        description="Python package not found — wrong name or version",
        fix_commands=[],
    ),
    CommandFix(
        pattern=re.compile(r"docker.*permission denied", re.IGNORECASE),
        description="Docker permission denied",
        fix_commands=["sudo usermod -aG docker $USER"],
    ),
]


def classify_command_error(command: str, stderr: str, exit_code: int, system: str) -> Tuple[ErrorSeverity, List[RecoveryAction]]:
    """
    Classify a command execution error and suggest recovery actions.

    Returns (severity, list_of_recovery_actions).
    """
    combined = f"{command}\n{stderr}".lower()
    actions: List[RecoveryAction] = []

    # -- Command not found -----------------------------------------------
    m = re.search(r"command not found:\s*(\S+)", combined, re.IGNORECASE)
    if m:
        cmd_name = m.group(1)
        pkg = _guess_package_name(cmd_name, system)
        if pkg:
            if system == "linux":
                fix_cmd = f"sudo apt-get install -y {pkg}"
            elif system == "darwin":
                fix_cmd = f"brew install {pkg}"
            else:
                fix_cmd = f"# Install {cmd_name} manually"
            actions.append(RecoveryAction(
                name=f"install_{cmd_name}",
                description=f"Install missing command '{cmd_name}' ({fix_cmd})",
                func=_run_shell,
                func_args=(fix_cmd,),
                max_retries=1,
                retry_delay=0,
            ))
        return ErrorSeverity.MEDIUM, actions

    # -- Permission denied -----------------------------------------------
    if "permission denied" in combined:
        if "docker" in combined:
            actions.append(RecoveryAction(
                name="fix_docker_permission",
                description="Add user to docker group",
                func=_run_shell,
                func_args=("sudo usermod -aG docker $USER",),
                max_retries=1,
            ))
        elif command.strip().startswith("sudo"):
            # Already using sudo — no auto fix
            pass
        else:
            new_cmd = f"sudo {command}"
            actions.append(RecoveryAction(
                name="retry_with_sudo",
                description=f"Retry with sudo: {new_cmd}",
                func=None,  # Signal: retry the command with sudo
                max_retries=1,
            ))
        return ErrorSeverity.MEDIUM, actions

    # -- Network / DNS ---------------------------------------------------
    if any(kw in combined for kw in ["could not resolve host", "name or service not known"]):
        actions.append(RecoveryAction(
            name="check_network",
            description="Check network connectivity",
            func=_check_network,
            max_retries=1,
        ))
        return ErrorSeverity.HIGH, actions

    # -- Connection refused ----------------------------------------------
    if "connection refused" in combined:
        port = _extract_port(stderr + command)
        actions.append(RecoveryAction(
            name="check_service",
            description=f"Check if service is running on port {port}",
            func=_run_shell,
            func_args=(f"lsof -i :{port}",),
            max_retries=1,
        ))
        return ErrorSeverity.HIGH, actions

    # -- SSL -------------------------------------------------------------
    if any(kw in combined for kw in ["ssl", "certificate verify failed"]):
        actions.append(RecoveryAction(
            name="update_certifi",
            description="Update SSL certificates",
            func=_run_shell,
            func_args=("pip install --upgrade certifi",),
            max_retries=2,
        ))
        return ErrorSeverity.MEDIUM, actions

    # -- Disk full -------------------------------------------------------
    if any(kw in combined for kw in ["disk full", "no space left", "no space left on device"]):
        actions.append(RecoveryAction(
            name="check_disk",
            description="Check disk space",
            func=_run_shell,
            func_args=("df -h",),
            max_retries=1,
        ))
        return ErrorSeverity.CRITICAL, actions

    # -- Pip / package errors --------------------------------------------
    if "pip" in combined and "error" in combined and "no matching distribution" in combined:
        return ErrorSeverity.MEDIUM, []

    # -- Generic ----------------------------------------------------------
    if exit_code is not None and exit_code == 127:
        return ErrorSeverity.HIGH, actions

    if exit_code is not None and exit_code != 0 and exit_code > 128:
        return ErrorSeverity.HIGH, actions

    return ErrorSeverity.MEDIUM, actions


def _guess_package_name(cmd: str, system: str) -> str:
    """Map a command name to its likely installable package."""
    mapping = {
        "git": "git",
        "curl": "curl",
        "wget": "wget",
        "jq": "jq",
        "docker": "docker",
        "node": "nodejs",
        "npm": "npm",
        "yarn": "yarn",
        "pip": "python3-pip",
        "pip3": "python3-pip",
        "python": "python3",
        "python3": "python3",
        "g++": "build-essential",
        "gcc": "build-essential",
        "make": "build-essential",
        "ffmpeg": "ffmpeg",
        "imagemagick": "imagemagick",
        "socat": "socat",
        "tmux": "tmux",
        "htop": "htop",
        "unzip": "unzip",
        "tar": "tar",
        "rsync": "rsync",
        "ssh": "openssh-client",
        "scp": "openssh-client",
        "lsof": "lsof",
        "netcat": "netcat",
        "nats": "nats-server",
        "ollama": "ollama",
        "code": "code",
        "vim": "vim",
        "nano": "nano",
    }
    return mapping.get(cmd, cmd)


def _extract_port(text: str) -> int:
    """Try to extract a port number from text."""
    m = re.search(r":(\d{2,5})\b", text)
    if m:
        return int(m.group(1))
    return 0


def _check_network() -> bool:
    """Quick network connectivity check."""
    import platform as _plat
    try:
        if _plat.system().lower() == "windows":
            cmd = ["ping", "-n", "1", "-w", "3000", "8.8.8.8"]
        else:
            cmd = ["ping", "-c", "1", "-W", "3", "8.8.8.8"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _run_shell(cmd: str, timeout: float = 60.0) -> Tuple[bool, str]:
    """Run a shell command.  Returns (success, output)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)



# ==============================================================================
#  Disk-full / OOM self-healing
# ==============================================================================

def get_disk_usage(path: str = None) -> Dict[str, Any]:
    """Get disk usage information for a path."""
    try:
        import shutil
        if path is None:
            import platform
            path = "C:\\" if platform.system() == "Windows" else "/"
        usage = shutil.disk_usage(path)
        return {
            "total_gb": usage.total / (1024 ** 3),
            "used_gb": usage.used / (1024 ** 3),
            "free_gb": usage.free / (1024 ** 3),
            "percent_used": (usage.used / usage.total) * 100,
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent_used": 0}


def get_memory_usage() -> Dict[str, Any]:
    """Get system memory usage information."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "total_gb": mem.total / (1024 ** 3),
            "available_gb": mem.available / (1024 ** 3),
            "percent_used": mem.percent,
        }
    except Exception:
        return {"total_gb": 0, "available_gb": 0, "percent_used": 0}


def emergency_disk_cleanup(target_free_gb: float = 2.0) -> Tuple[bool, str]:
    """Emergency disk cleanup. Removes temp/cache files. Returns (success, msg)."""
    freed_mb = 0.0
    actions_taken = []

    cleanup_targets = [
        Path("/tmp"),
        Path.home() / ".cache",
        Path.home() / ".local" / "share" / "Trash",
        Path.home() / ".npm" / "_cacache",
        Path.home() / ".cache" / "pip",
    ]

    for target in cleanup_targets:
        if not target.exists():
            continue
        try:
            before = get_disk_usage(str(target.parent if target.is_dir() else target))
            if target == Path("/tmp"):
                import glob
                for f in glob.glob(str(target / "*")):
                    try:
                        p = Path(f)
                        if p.is_file() and (time.time() - p.stat().st_mtime) > 3600:
                            size = p.stat().st_size
                            p.unlink()
                            freed_mb += size / (1024 ** 2)
                        elif p.is_dir() and (time.time() - p.stat().st_mtime) > 3600:
                            import shutil as _sh
                            _sh.rmtree(f, ignore_errors=True)
                    except Exception:
                        pass
            else:
                import shutil as _sh
                for item in list(target.iterdir()):
                    try:
                        if item.is_file():
                            freed_mb += item.stat().st_size / (1024 ** 2)
                            item.unlink()
                        elif item.is_dir():
                            _sh.rmtree(str(item), ignore_errors=True)
                    except Exception:
                        pass
            after = get_disk_usage(str(target.parent if target.is_dir() else target))
            if after["free_gb"] > before.get("free_gb", 0):
                actions_taken.append(f"Cleaned {target}")
        except Exception:
            pass

    # Clean old terminal history files
    try:
        th_dir = Path("./peripherals/terminal_history")
        if th_dir.exists():
            for f in th_dir.glob("*.json"):
                if f.stat().st_mtime < time.time() - 86400 * 7:
                    f.unlink()
                    actions_taken.append(f"Removed old history: {f.name}")
    except Exception:
        pass

    # Clean old log files
    try:
        log_dir = Path("logs")
        if log_dir.exists():
            for f in log_dir.glob("*.log.*"):
                if f.stat().st_mtime < time.time() - 86400 * 3:
                    f.unlink()
                    actions_taken.append(f"Removed old log: {f.name}")
    except Exception:
        pass

    # Truncate oversized error log
    try:
        err_log = Path("logs/resilience_errors.jsonl")
        if err_log.exists() and err_log.stat().st_size > 10 * 1024 * 1024:
            with open(err_log, "r", encoding="utf-8") as fh:
                lines_local = fh.readlines()
            with open(err_log, "w", encoding="utf-8") as fh:
                fh.writelines(lines_local[-1000:])
            actions_taken.append("Truncated resilience error log")
    except Exception:
        pass

    current = get_disk_usage()
    if current["free_gb"] >= target_free_gb:
        msg = f"Freed ~{freed_mb:.0f}MB. Disk now has {current['free_gb']:.1f}GB free."
        if actions_taken:
            msg += " Actions: " + "; ".join(actions_taken[:5])
        return True, msg
    else:
        msg = (
            f"Disk still low after cleanup: {current['free_gb']:.1f}GB free "
            f"(target: {target_free_gb}GB). Manual intervention may be needed."
        )
        return False, msg


def check_system_resources() -> Dict[str, Any]:
    """Check system resources and return a health report."""
    disk = get_disk_usage()
    mem = get_memory_usage()
    issues = []

    if disk["free_gb"] < 1.0:
        issues.append(f"CRITICAL: Disk almost full ({disk['free_gb']:.1f}GB free)")
    elif disk["free_gb"] < 5.0:
        issues.append(f"WARNING: Disk low ({disk['free_gb']:.1f}GB free)")

    if mem["percent_used"] > 95:
        issues.append(f"CRITICAL: Memory almost full ({mem['percent_used']:.0f}% used)")
    elif mem["percent_used"] > 85:
        issues.append(f"WARNING: Memory high ({mem['percent_used']:.0f}% used)")

    return {
        "disk": disk,
        "memory": mem,
        "healthy": len(issues) == 0,
        "issues": issues,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  API error classification
# ══════════════════════════════════════════════════════════════════════════════

def classify_api_error(error: Exception) -> Tuple[ErrorSeverity, ErrorCategory, bool, float]:
    """
    Classify an API error.

    Returns (severity, category, is_retryable, suggested_delay_seconds).
    """
    msg = str(error).lower()
    error_type = type(error).__name__.lower()

    # Walk the exception chain so that wrapped exceptions like
    # "Max retries exceeded -> SSLEOFError" are classified by the
    # *root* cause, not just the outermost message.
    _cause = error
    for _ in range(5):
        _cause_msg = str(_cause).lower()
        _cause_type = type(_cause).__name__.lower()
        if _cause_msg != msg or _cause_type != error_type:
            msg = msg + " " + _cause_msg
            error_type = error_type + " " + _cause_type
        # Follow the chain: prefer __cause__ over __context__
        _next = getattr(_cause, '__cause__', None) or getattr(_cause, '__context__', None)
        if _next is not None and _next is not _cause:
            _cause = _next
        else:
            break

    # Rate limit
    if any(kw in msg for kw in ["429", "rate limit", "rate-limited", "too many requests", "throttl"]):
        return ErrorSeverity.HIGH, ErrorCategory.RATE_LIMIT, True, 30.0

    # Auth
    if any(kw in msg for kw in ["401", "403", "unauthorized", "forbidden", "api key", "credential", "authentication"]):
        return ErrorSeverity.CRITICAL, ErrorCategory.AUTHENTICATION, False, 0.0

    # Server error
    if any(kw in msg for kw in ["500", "502", "503", "504", "internal server error", "bad gateway", "service unavailable"]):
        return ErrorSeverity.HIGH, ErrorCategory.EXTERNAL, True, 5.0

    # Timeout
    if any(kw in msg for kw in ["timeout", "timed out", "deadline exceeded"]):
        return ErrorSeverity.HIGH, ErrorCategory.TIMEOUT, True, 3.0

    # SSL / EOF errors (transient network issues) — checked BEFORE generic
    # network so that SSLEOFError, unexpected EOF, etc. get a longer delay.
    if any(kw in msg for kw in ["ssl", "ssleoferror",
                                  "unexpected_eof", "eof occurred",
                                  "sslerror"]):
        return ErrorSeverity.HIGH, ErrorCategory.TRANSIENT, True, 5.0

    # Network
    if any(kw in msg for kw in [
        "connection", "network", "unreachable", "refused", "reset",
        "dns", "resolve", "socket", "certificate",
    ]):
        return ErrorSeverity.HIGH, ErrorCategory.TRANSIENT, True, 2.0

    # Output validation failure (empty model output) — transient, retryable
    if any(kw in msg for kw in ["output is empty", "output validation failed"]):
        return ErrorSeverity.MEDIUM, ErrorCategory.TRANSIENT, True, 2.0

    # Validation / bad request
    if any(kw in msg for kw in ["400", "bad request", "invalid", "validation"]):
        return ErrorSeverity.MEDIUM, ErrorCategory.VALIDATION, False, 0.0

    # Content filter / safety
    if any(kw in msg for kw in ["content_filter", "safety", "blocked", "harmful"]):
        return ErrorSeverity.MEDIUM, ErrorCategory.VALIDATION, False, 0.0

    # Context length
    if any(kw in msg for kw in ["context length", "max_tokens", "token limit", "too long"]):
        return ErrorSeverity.HIGH, ErrorCategory.RESOURCE, True, 0.0

    # Quota / billing
    if any(kw in msg for kw in ["quota", "billing", "payment", "insufficient funds", "limit exceeded"]):
        return ErrorSeverity.CRITICAL, ErrorCategory.RESOURCE, False, 0.0

    # Model not found
    if any(kw in msg for kw in ["model", "not found", "does not exist", "unknown model"]):
        return ErrorSeverity.HIGH, ErrorCategory.CONFIGURATION, False, 0.0

    # Default: transient, retryable
    return ErrorSeverity.MEDIUM, ErrorCategory.TRANSIENT, True, 1.0


# ══════════════════════════════════════════════════════════════════════════════
#  Resilience Engine — the main class
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ResilienceConfig:
    """Configuration for the resilience engine."""
    # Retry
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 120.0
    backoff_factor: float = 2.0

    # Circuit breaker
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0

    # Self-healing
    enable_self_healing: bool = True
    max_self_healing_attempts: int = 2

    # Telegram notification
    telegram_notify_on_error: bool = True
    telegram_notify_on_recovery: bool = True

    # Global exception hook
    install_global_hook: bool = True

    # Logging
    log_all_errors: bool = True
    error_log_path: Optional[str] = "logs/resilience_errors.jsonl"


class ResilienceEngine:
    """
    Central resilience engine.

    Usage:
        engine = ResilienceEngine()

        # Wrap any function with retry + failover:
        result = await engine.retry_async(some_async_func, arg1, arg2)

        # Classify and recover from errors:
        severity, actions = engine.classify_command_error(cmd, stderr, rc)

        # Notify Telegram on error:
        engine.notify_telegram("Something went wrong", is_error=True)
    """

    def __init__(self, config: Optional[ResilienceConfig] = None):
        self.config = config or ResilienceConfig()
        self._error_counts: Dict[str, int] = {}
        self._circuit_breakers: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._telegram_callback: Optional[Callable[[str], Any]] = None
        self._telegram_user_id: Optional[int] = None
        self._telegram_bot: Optional[Any] = None
        self._error_log_path: Optional[Path] = None

        if self.config.error_log_path:
            self._error_log_path = Path(self.config.error_log_path)
            self._error_log_path.parent.mkdir(parents=True, exist_ok=True)

        if self.config.install_global_hook:
            self._install_global_exception_hook()

    # -- Telegram integration ----------------------------------------------

    def set_telegram_callback(self, callback: Callable[[str], Any]) -> None:
        """Set a callback that sends a message to Telegram."""
        self._telegram_callback = callback

    def set_telegram_user_id(self, user_id: int) -> None:
        self._telegram_user_id = user_id

    def set_telegram_bot(self, bot: Any) -> None:
        """Set the TelegramBotManager instance for direct queue_message calls."""
        self._telegram_bot = bot

    def notify_telegram(self, message: str, is_error: bool = True) -> None:
        """Send a notification to Telegram (non-blocking, fire-and-forget)."""
        if not self.config.telegram_notify_on_error and is_error:
            return
        if not self.config.telegram_notify_on_recovery and not is_error:
            return

        # Try direct bot first
        if self._telegram_bot and self._telegram_user_id:
            try:
                self._telegram_bot.queue_message(self._telegram_user_id, message)
                return
            except Exception:
                pass

        # Try callback
        if self._telegram_callback:
            try:
                self._telegram_callback(message)
            except Exception:
                pass

    # -- Retry with exponential backoff -----------------------------------

    async def retry_async(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        context_label: str = "",
        **kwargs: Any,
    ) -> Any:
        """
        Call an async function with intelligent retry + circuit breaker.

        Automatically classifies errors and applies the right retry strategy.
        """
        if retryable_exceptions is None:
            retryable_exceptions = (Exception,)

        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except retryable_exceptions as e:
                last_error = e
                severity, category, is_retryable, suggested_delay = classify_api_error(e)

                if not is_retryable or attempt >= self.config.max_retries:
                    self._log_error(e, severity, category, context_label, attempt)
                    raise

                delay = min(
                    max(suggested_delay, self.config.base_delay * (self.config.backoff_factor ** attempt)),
                    self.config.max_delay,
                )

                logger.warning(
                    f"[retry] {context_label} attempt {attempt + 1}/{self.config.max_retries} "
                    f"failed ({category.value}): {e}. Retrying in {delay:.1f}s ..."
                )

                # Notify Telegram on high-severity errors
                if severity in (ErrorSeverity.HIGH, ErrorSeverity.CRITICAL):
                    self.notify_telegram(
                        f"\u26a0\ufe0f {context_label}: {type(e).__name__}: {e}\nRetrying in {int(delay)}s (attempt {attempt + 1}/{self.config.max_retries})"
                    )

                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]

    def retry_sync(
        self,
        func: Callable[..., Any],
        *args: Any,
        retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
        context_label: str = "",
        **kwargs: Any,
    ) -> Any:
        """Synchronous version of retry_async."""
        if retryable_exceptions is None:
            retryable_exceptions = (Exception,)

        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except retryable_exceptions as e:
                last_error = e
                severity, category, is_retryable, suggested_delay = classify_api_error(e)

                if not is_retryable or attempt >= self.config.max_retries:
                    self._log_error(e, severity, category, context_label, attempt)
                    raise

                delay = min(
                    max(suggested_delay, self.config.base_delay * (self.config.backoff_factor ** attempt)),
                    self.config.max_delay,
                )

                logger.warning(
                    f"[retry] {context_label} attempt {attempt + 1}/{self.config.max_retries} "
                    f"failed ({category.value}): {e}. Retrying in {delay:.1f}s ..."
                )

                if severity in (ErrorSeverity.HIGH, ErrorSeverity.CRITICAL):
                    self.notify_telegram(
                        f"\u26a0\ufe0f {context_label}: {type(e).__name__}: {e}\nRetrying in {int(delay)}s (attempt {attempt + 1}/{self.config.max_retries})"
                    )

                time.sleep(delay)

        raise last_error  # type: ignore[misc]

    # -- Self-healing command execution -----------------------------------

    async def execute_with_healing(
        self,
        command: str,
        timeout: float = 60.0,
        cancel_event: Optional[threading.Event] = None,
        max_healing_attempts: int = 2,
    ) -> Dict[str, Any]:
        """
        Execute a shell command with automatic self-healing.

        If the command fails, classify the error, apply fixes, and retry.
        Returns a dict with stdout, stderr, return_code, and healing info.
        """
        system = platform.system().lower()
        current_cmd = command
        healing_log: List[str] = []

        for healing_round in range(max_healing_attempts + 1):
            if cancel_event and cancel_event.is_set():
                return {
                    "stdout": "",
                    "stderr": "Cancelled",
                    "return_code": -1,
                    "success": False,
                    "healing_log": healing_log,
                }

            try:
                proc = await asyncio.create_subprocess_shell(
                    current_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    return {
                        "stdout": "",
                        "stderr": f"Command timed out after {timeout}s",
                        "return_code": -1,
                        "success": False,
                        "healing_log": healing_log,
                    }

                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                rc = proc.returncode

                if rc == 0:
                    return {
                        "stdout": stdout,
                        "stderr": stderr,
                        "return_code": 0,
                        "success": True,
                        "healing_log": healing_log,
                    }

                # Command failed — try to heal
                if not self.config.enable_self_healing or healing_round >= max_healing_attempts:
                    return {
                        "stdout": stdout,
                        "stderr": stderr,
                        "return_code": rc,
                        "success": False,
                        "healing_log": healing_log,
                    }

                severity, actions = classify_command_error(current_cmd, stderr, rc, system)

                if not actions:
                    return {
                        "stdout": stdout,
                        "stderr": stderr,
                        "return_code": rc,
                        "success": False,
                        "healing_log": healing_log,
                    }

                # Try the first applicable recovery action
                action = actions[0]
                healing_log.append(f"Round {healing_round + 1}: {action.description}")

                if action.name == "retry_with_sudo":
                    current_cmd = f"sudo {command}"
                    self.notify_telegram("\U0001f527 Permission denied — retrying with sudo: " + command)
                    continue

                if action.func:
                    if action.name == "install_missing":
                        # Run the fix command
                        fix_cmd = action.fix_commands[0] if action.fix_commands else ""
                        if fix_cmd:
                            fix_result = await asyncio.create_subprocess_shell(
                                fix_cmd,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            await asyncio.wait_for(fix_result.communicate(), timeout=120)
                            # Retry original command
                            continue

                    if action.name == "check_network":
                        if not _check_network():
                            self.notify_telegram("\U0001f310 Network appears to be down. Waiting 10s before retry ...")
                            await asyncio.sleep(10)
                        continue

                    if action.func:
                        # Generic: run the fix function
                        if asyncio.iscoroutinefunction(action.func):
                            await action.func(*action.func_args, **action.func_kwargs)
                        else:
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None, lambda: action.func(*action.func_args, **action.func_kwargs)
                            )
                        continue

                # No applicable fix found
                return {
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": rc,
                    "success": False,
                    "healing_log": healing_log,
                }

            except Exception as e:
                healing_log.append(f"Round {healing_round + 1}: Exception: {e}")
                return {
                    "stdout": "",
                    "stderr": str(e),
                    "return_code": -1,
                    "success": False,
                    "healing_log": healing_log,
                }

        # Should not reach here
        return {
            "stdout": "",
            "stderr": "Max healing attempts exceeded",
            "return_code": -1,
            "success": False,
            "healing_log": healing_log,
        }

    # -- Circuit breaker ---------------------------------------------------

    def is_circuit_open(self, key: str) -> bool:
        """Check if the circuit breaker for *key* is open."""
        with self._lock:
            cb = self._circuit_breakers.get(key)
            if not cb:
                return False
            if cb["open_until"] and time.time() > cb["open_until"]:
                cb["failures"] = 0
                cb["open_until"] = None
                return False
            return cb["open_until"] is not None

    def record_success(self, key: str) -> None:
        with self._lock:
            cb = self._circuit_breakers.setdefault(key, {"failures": 0, "open_until": None})
            cb["failures"] = 0
            cb["open_until"] = None

    def record_failure(self, key: str) -> bool:
        """
        Record a failure.  Returns True if the circuit just opened.
        """
        with self._lock:
            cb = self._circuit_breakers.setdefault(key, {"failures": 0, "open_until": None})
            cb["failures"] += 1
            if cb["failures"] >= self.config.circuit_breaker_threshold:
                cb["open_until"] = time.time() + self.config.circuit_breaker_timeout
                logger.warning(f"Circuit breaker OPEN for {key} (cooldown {self.config.circuit_breaker_timeout}s)")
                return True
            return False

    # -- Global exception hook ---------------------------------------------

    def _install_global_exception_hook(self) -> None:
        """Install sys.excepthook and threading.excepthook."""
        self._original_excepthook = sys.excepthook
        self._original_threading_excepthook = getattr(threading, "excepthook", None)
        self._threading_excepthook_available = hasattr(threading, "excepthook")

        engine = self

        def _handle_exception(
            exc_type: Type[BaseException],
            exc_value: BaseException,
            exc_tb: Any,
            thread: Optional[threading.Thread] = None,
        ) -> None:
            # Skip KeyboardInterrupt and SystemExit — these must propagate
            if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
                if engine._original_excepthook:
                    engine._original_excepthook(exc_type, exc_value, exc_tb)
                return

            tb_text = "".join(_traceback_module.format_exception(exc_type, exc_value, exc_tb))
            thread_name = thread.name if thread else "main"

            logger.critical(
                f"Uncaught exception in thread '{thread_name}': {exc_type.__name__}: {exc_value}\n{tb_text}"
            )

            engine._log_error(exc_value, ErrorSeverity.CRITICAL, ErrorCategory.UNKNOWN, "uncaught:" + thread_name, 0)

            # Notify Telegram
            short_tb = "\n".join(tb_text.strip().splitlines()[-5:])
            engine.notify_telegram(
                "\U0001f6a8 Uncaught error in " + thread_name + ":\n"
                + exc_type.__name__ + ": " + str(exc_value) + "\n"
                + "```\n" + short_tb + "\n```"
            )

        def _custom_excepthook(exc_type, exc_value, exc_tb):
            _handle_exception(exc_type, exc_value, exc_tb)

        def _custom_threading_excepthook(args):
            _handle_exception(args.exc_type, args.exc_value, args.exc_traceback, args.thread)

        sys.excepthook = _custom_excepthook
        if self._threading_excepthook_available:
            threading.excepthook = _custom_threading_excepthook

    # -- Error logging -----------------------------------------------------

    def _log_error(
        self,
        error: Exception,
        severity: ErrorSeverity,
        category: ErrorCategory,
        context_label: str,
        attempt: int,
    ) -> None:
        if not self.config.log_all_errors:
            return

        entry = {
            "timestamp": time.time(),
            "severity": severity.name,
            "category": category.value,
            "context": context_label,
            "attempt": attempt,
            "type": type(error).__name__,
            "message": str(error),
            "traceback": _traceback_module.format_exc(),
        }

        # Structured log
        logger.error(
            "[resilience] " + context_label + ": " + type(error).__name__ + ": " + str(error)
            + " (severity=" + severity.name + ", category=" + category.value + ", attempt=" + str(attempt) + ")"
        )

        # JSONL file
        if self._error_log_path:
            try:
                with open(self._error_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  Module-level singleton
# ══════════════════════════════════════════════════════════════════════════════

_resilience_engine: Optional[ResilienceEngine] = None


def get_resilience_engine(config: Optional[ResilienceConfig] = None) -> ResilienceEngine:
    """Get the global ResilienceEngine singleton."""
    global _resilience_engine
    if _resilience_engine is None:
        _resilience_engine = ResilienceEngine(config)
    return _resilience_engine


def reset_resilience_engine() -> None:
    """Reset the global singleton (for testing)."""
    global _resilience_engine
    _resilience_engine = None
