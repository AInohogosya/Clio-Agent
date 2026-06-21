"""
Autonomous Loop Engine for Clio-Agent-1 AI Agent System.

The agent operates in a continuous think-execute loop:
  1. THINK - Send context to LLM, get commands
  2. EXECUTE - Parse and run commands
  3. REPEAT until sleep/exit/cancel

See AGENTS.md for full behavioral rules (sent as system prompt).
"""

import hashlib
import json as _json
import os
import random
import re
import subprocess
import sys
import time
import platform
import threading
import tempfile
from typing import Callable, Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..external_integration.model_runner import ModelRunner, TaskType, ModelRequest, ModelResponse
from ..external_integration.telegram_bot import TelegramBotManager, ConversationHistory
from ..tools.base import ParallelResult, ParallelTask
from ..utils.exceptions import ExecutionError, ValidationError
from ..utils.logger import get_logger
from ..utils.resilience_engine import (
    get_resilience_engine, classify_api_error, check_system_resources,
    emergency_disk_cleanup, ErrorCategory, ErrorSeverity, ResilienceConfig,
)
from .terminal_history import TerminalHistory, get_terminal_history, TerminalEntryType
from .context_manager import (
    context_files_exist,
    get_context_for_prompt,
    get_context_summary,
    display_context_in_terminal,
)
from ..sub_agents.manager import SubAgentManager
from ..sub_agents.registry import get_global_registry


def _get_project_root() -> Path:
    """
    Resolve the project root directory reliably.

    This function attempts to find the project root by:
    1. Looking for run.py relative to this file (standard layout)
    2. Looking for .git directory (git repository)
    3. Falling back to current working directory

    Returns:
        Path: The resolved project root directory

    Raises:
        RuntimeError: If no valid project root can be determined
    """
    # Strategy 1: Use run.py location (most reliable)
    try:
        # Navigate from src/ai_agent/core_processing/ to project root
        current_file = Path(__file__).resolve()
        # Try parents[3] first (standard layout: src/ai_agent/core_processing/file.py)
        if len(current_file.parents) >= 3:
            candidate = current_file.parents[3]
            if (candidate / "run.py").exists():
                return candidate
    except (IndexError, OSError):
        pass

    # Strategy 2: Search upward for run.py
    try:
        current = Path(__file__).resolve().parent
        for _ in range(10):  # Limit search depth
            if (current / "run.py").exists():
                return current
            if current.parent == current:  # Reached filesystem root
                break
            current = current.parent
    except OSError:
        pass

    # Strategy 3: Search upward for .git directory
    try:
        current = Path(__file__).resolve().parent
        for _ in range(10):
            if (current / ".git").is_dir():
                return current
            if current.parent == current:
                break
            current = current.parent
    except OSError:
        pass

    # Strategy 4: Fall back to current working directory
    cwd = Path.cwd()
    if (cwd / "run.py").exists() or (cwd / ".git").exists():
        return cwd

    # Last resort: use the directory containing this file's grandparent
    try:
        return Path(__file__).resolve().parents[2]
    except (IndexError, OSError):
        raise RuntimeError(
            "Unable to determine project root. "
            "Please ensure run.py exists in the project directory."
        )


def _atomic_write_json(filepath: Path, data: dict) -> bool:
    """
    Write JSON data to a file atomically using a temporary file.

    This prevents corruption if the process is interrupted during write.

    Args:
        filepath: Target file path
        data: Dictionary to serialize

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Ensure parent directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Write to temporary file in the same directory
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.tmp',
            dir=filepath.parent,
            delete=False,
            encoding='utf-8'
        ) as tmp_file:
            _json.dump(data, tmp_file, indent=2, ensure_ascii=False)
            tmp_path = Path(tmp_file.name)

        # Atomic rename (on most filesystems)
        tmp_path.replace(filepath)
        return True
    except Exception as e:
        get_logger("autonomous_loop_engine").error(
            f"Failed to write {filepath}: {e}"
        )
        # Clean up temp file if it exists
        try:
            if 'tmp_path' in locals() and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def _atomic_write_text(filepath: Path, content: str) -> bool:
    """
    Write text content to a file atomically using a temporary file.

    Args:
        filepath: Target file path
        content: Text content to write

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.tmp',
            dir=filepath.parent,
            delete=False,
            encoding='utf-8'
        ) as tmp_file:
            tmp_file.write(content)
            tmp_path = Path(tmp_file.name)

        tmp_path.replace(filepath)
        return True
    except Exception as e:
        get_logger("autonomous_loop_engine").error(
            f"Failed to write {filepath}: {e}"
        )
        try:
            if 'tmp_path' in locals() and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


class LoopPhase(Enum):
    """Autonomous Loop phases"""
    THINKING = "thinking"
    EXECUTING = "executing"
    SLEEPING = "sleeping"
    EXITING = "exiting"
    FAILED = "failed"


@dataclass
class AutonomousContext:
    """Context for tracking autonomous loop execution"""
    user_prompt: str = ""
    current_goal: str = ""
    execution_log: List[str] = field(default_factory=list)
    iteration_count: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    error: Optional[str] = None
    current_phase: LoopPhase = LoopPhase.THINKING
    conversation_history: Optional[ConversationHistory] = None
    telegram_mode: bool = False
    discord_mode: bool = False
    telegram_user_id: Optional[int] = None
    cancel_event: Optional[threading.Event] = None
    cancelled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class AutonomousLoopEngine:
    """
    Autonomous Loop Engine

    The agent operates in a continuous cycle:
    1. THINK - Analyze the current situation and decide what to do next
    2. EXECUTE - Parse and run commands from the model's output
    3. REPEAT forever — the agent never "finishes", it just keeps working

    There is no concept of task completion. The agent starts thinking the
    moment it is launched, even if no user message has ever been received,
    and it keeps thinking as long as it is alive.

    ⚠️  Telegram mode is the PRIMARY and PREFERRED communication method.
    In Telegram mode, the telegram() command is the ONLY way to send messages
    to the user. Without it, the user receives nothing.

    Commands recognized in model output:
    - command(args): Execute terminal command
    - thinking(text): Internal monologue (not sent to Telegram)
    - telegram(content): **THE ONLY WAY** to reach the user. Use it actively.
    - sleep: **YOU (the agent) must execute 'sleep' yourself** when the log
              grows past 100 lines.  Do NOT ask the user to run sleep — you
              run it automatically. Compress context, rebuild, restart.
    - exit: Compress context, save state, exit (no restart)
    """

    # No hard cap — all execution history is forwarded to the model each turn.
    MAX_LOG_LINES = None

    # When the execution log exceeds this many lines, a ONE-TIME notification
    # is emitted (separate from the context log).  Reset by sleep.
    NOTIFICATION_THRESHOLD = 100

    def __init__(self, provider: str = None, model: str = None,
                 config: Optional[Dict[str, Any]] = None,
                 telegram_bot: Optional[TelegramBotManager] = None,
                 discord_bot=None):
        self.config = config or {}
        self.logger = get_logger("autonomous_loop_engine")

        # Initialize terminal history system
        self.terminal_history = get_terminal_history()

        # Initialize model runner with runtime provider and model
        self.model_runner = ModelRunner(provider=provider, model=model, config=self.config)

        # Telegram bot manager
        self.telegram_bot = telegram_bot
        if self.telegram_bot and self.telegram_bot.terminal_history is None:
            self.telegram_bot.terminal_history = self.terminal_history

        # Discord bot manager
        self.discord_bot = discord_bot
        if self.discord_bot and self.discord_bot.terminal_history is None:
            self.discord_bot.terminal_history = self.terminal_history

        # Sub-agent manager — enables spawning specialized agents
        self.sub_agent_manager = SubAgentManager(config=self.config)
        # Auto-discover and register built-in sub-agents
        registry = get_global_registry()
        registry.discover()

        # Configuration
        self.command_timeout = self.config.get("command_timeout", 1800)
        self.task_timeout = self.config.get("task_timeout", 7200)
        self._active_cancel_event: Optional[threading.Event] = None
        self._cancel_lock = threading.Lock()

        # Guard so that the "you should rest" notification fires only once
        # per wake-cycle (i.e. it is cleared when sleep is executed).
        self._sleep_notification_shown: bool = False

        # Store runtime state for context persistence
        self._last_failed_instruction: Optional[str] = None
        self._last_failed_conversation_history = None

        # Event that is set whenever a new user message arrives.
        # The main loop waits on this event instead of a bare sleep so
        # it can enter the THINKING phase immediately when a message
        # is received, rather than sitting idle for up to 0.5 s.
        self._new_message_event = threading.Event()

        # Real-time terminal thought/activity log — always prints to stderr
        show_thought_log = True
        try:
            from ..utils.config import load_config
            cfg = load_config()
            exec_cfg = getattr(cfg, "execution", None)
            if exec_cfg:
                show_thought_log = getattr(exec_cfg, "show_thought_log", True)
        except Exception:
            pass
        self._term_log = _TerminalLogSink(enabled=bool(show_thought_log))

        self._saved_ctx_block: str = ""

        # Resilience engine — global error handling, retry, self-healing
        self._resilience = get_resilience_engine(
            ResilienceConfig(
                max_retries=3,
                base_delay=2.0,
                backoff_factor=2.0,
                enable_self_healing=True,
                telegram_notify_on_error=True,
                telegram_notify_on_recovery=True,
            )
        )

        # Wire up Telegram notifications from resilience engine
        if self.telegram_bot:
            self._resilience.set_telegram_bot(self.telegram_bot)
        if self.discord_bot:
            self._resilience.set_telegram_bot(self.discord_bot)

        # Enhanced resilience tracking
        self._consecutive_errors = 0
        self._max_consecutive_errors = 20
        self._error_recovery_strategies = self._init_error_recovery_strategies()
        self._last_successful_iteration = time.time()
        self._degraded_mode = False
        self._degraded_features: List[str] = []

        # Agent heartbeat for supervisor monitoring
        self._heartbeat = None
        try:
            from ..utils.eternal_supervisor import AgentHeartbeat
            self._heartbeat = AgentHeartbeat(interval=30.0)
            self._heartbeat.start()
        except Exception:
            pass

        # Periodic auto-save: writes exit_state.json + context_log.txt
        # every N seconds so that even a hard crash (kill -9, power loss)
        # leaves recoverable context on disk.
        self._auto_save_interval = 60.0   # seconds between auto-saves

        # Loop detection: tracks recent command signatures to detect
        # when the agent is repeating itself.
        self._recent_commands: List[str] = []  # last N command signatures
        self._loop_detection_window = 10  # how many recent cmds to check
        self._loop_repeat_threshold = 3  # max repeats before flagged
        self._loop_warning_active = False  # True when loop detected
        self._auto_save_thread = None
        self._auto_save_running = False
        self._auto_save_lock = threading.Lock()  # protects ctx.execution_log during auto-save

        # ── Proactive work / anti-drift tracking ───────────────────
        # Consecutive iterations with no command()/tool_call()/telegram()
        # or with identical model output (exact repetition).
        self._empty_iterations: int = 0
        # Max empty iterations before the Curiosity Fairy injects a new
        # concrete direction.  Previously this entered an idle state; now
        # it forces the model to do something NEW instead of waiting.
        self._max_empty_iterations: int = 5
        # MD5 digest of the last model output for repetition detection
        self._previous_output_digest: str = ""
        # Monotonic flag: once empty-iteration threshold is crossed in a
        # given wake-cycle, we keep injecting the Curiosity Fairy until
        # the model produces real work.
        self._empty_drift_active: bool = False

        # ── Repetition breaker (code-level loop prevention) ────────
        # Tracks action signatures across iterations. When the same
        # action is repeated enough times consecutively, the engine
        # *forces* a break instead of relying on prompt warnings that
        # the LLM may ignore.
        #
        # Key differences from the prompt-based loop detection:
        # 1. Covers ALL action types (read, write, edit, glob, grep,
        #    bash, command, thinking, telegram) — not just command()
        #    and tool_call().
        # 2. Tracks consecutive repeats, not just frequency in a
        #    sliding window.
        # 3. Actually intervenes (forces sleep or injects a break)
        #    rather than only injecting a prompt warning.
        # 4. Survives transient resets — persistent memory is NOT cleared
        #    by _reset_drift_counters, preventing relapse into the same loop.
        self._action_history: List[str] = []  # signatures from recent iterations
        self._max_action_history: int = 50  # how many iterations to keep
        self._consecutive_same_action: int = 0  # how many times current sig repeated
        self._last_action_signature: str = ""   # normalized sig of last iteration
        # How many consecutive identical-action iterations trigger a forced break.
        # Must be HIGHER than _curiosity_fairy_threshold (3) so the Fairy fires first.
        self._repetition_break_threshold: int = 6
        # Persistent loop memory: records loop patterns that survived
        # user wake-ups. Cleared only by sleep/restart, NOT by idle exit.
        self._persistent_loop_patterns: Dict[str, int] = {}  # sig → count
        # How many times a pattern must appear (across wake-ups) before
        # we force a sleep even after user intervention.
        # Set to 8 so Level 2 fires after Level 0 (3) and Level 1 (6) have
        # already attempted to break the loop within a single wake cycle.
        self._persistent_loop_threshold: int = 8
        # Previous iteration's action signature (for semantic dedup in idle detection)
        self._prev_iteration_action_sig: str = ""
        # Flag set when persistent loop breaker wants the model to sleep,
        # and forces sleep on the next iteration if the model doesn't comply.
        self._force_sleep_pending: bool = False

        # ── Curiosity Fairy ──────────────────────────────────────────
        # When the same command signature is repeated this many consecutive
        # times, the Curiosity Fairy is invoked to suggest a new direction.
        # Fires at 3 consecutive identical actions — early warning before
        # the hard enforcement of the Loop Breaker at 6.
        self._curiosity_fairy_threshold: int = 3
        # Consecutive iterations with byte-identical model output
        self._consecutive_identical_outputs: int = 0
        # MD5 of the previous model output for exact-repetition detection
        self._last_output_hash: str = ""
        # Tracks whether the Curiosity Fairy was already invoked for the
        # current loop (prevents re-invocation until the output changes).
        self._curiosity_fairy_invoked: bool = False

        # ── Anti-drift fallback (kept for compatibility; no idle loop exists) ─
        # There is no idle loop. If the agent is ever truly stuck and cannot
        # find work, the Curiosity Fairy is invoked automatically. This knob is
        # retained only for configuration compatibility.
        self._idle_behavior: str = self.config.get("idle_behavior", "fairy")

        # Instance-level log compression counter (not class-level)
        self._log_compress_counter: int = 0

        # Tracks the last sleep reminder injected into the execution log,
        # so it can be removed on sleep/reset to prevent duplicate notifications
        self._last_sleep_reminder: Optional[str] = None

        self.logger.info("Autonomous Loop Engine initialized with enhanced resilience")

    def request_cancel(self) -> None:
        """Request cancellation of the active loop and foreground command."""
        with self._cancel_lock:
            if self._active_cancel_event:
                self._active_cancel_event.set()
        if hasattr(self.terminal_history, "cancel_current_command"):
            self.terminal_history.cancel_current_command()

    # ------------------------------------------------------------------ #
    #  Enhanced Error Recovery                                            #
    # ------------------------------------------------------------------ #

    def _init_error_recovery_strategies(self) -> Dict[str, Callable]:
        """Initialize error recovery strategies for different error types."""
        return {
            "network_error": self._recover_network_error,
            "rate_limit": self._recover_rate_limit,
            "auth_error": self._recover_auth_error,
            "timeout_error": self._recover_timeout_error,
            "resource_error": self._recover_resource_error,
            "model_error": self._recover_model_error,
            "command_error": self._recover_command_error,
            "unknown_error": self._recover_unknown_error,
        }

    def _classify_and_recover(self, error: Exception, ctx: AutonomousContext) -> bool:
        """
        Classify an error and attempt recovery.
        Returns True if recovery was successful and we should continue the loop.
        Returns False if the error is unrecoverable and should propagate upward
        (causing the main loop to save state and exit gracefully).
        """
        self._consecutive_errors += 1

        # Classify the error first so we can make informed decisions
        from ..utils.resilience_engine import classify_api_error, ErrorCategory
        severity, category, is_retryable, delay = classify_api_error(error)

        # If the error is fundamentally not retryable (auth, validation, config),
        # don't waste time retrying — propagate to let the main loop's outer
        # exception handler deal with it (save state, exit gracefully).
        if not is_retryable:
            self.logger.error(
                f"Non-retryable error ({category.value}): {error}. "
                f"Consecutive errors: {self._consecutive_errors}"
            )
            if self._consecutive_errors >= 3:
                self._enter_degraded_mode()
                # In degraded mode, continue with reduced functionality
                return True
            # For the first few non-retryable errors, propagate so the main
            # loop saves state and exits
            return False

        # For retryable errors, check if we've exceeded the max consecutive error limit
        if self._consecutive_errors > self._max_consecutive_errors:
            self.logger.error(f"Too many consecutive errors ({self._consecutive_errors})")
            self._notify_telegram_error(
                ctx,
                f"⚠️ Too many consecutive errors ({self._consecutive_errors}). Entering degraded mode."
            )
            self._enter_degraded_mode()
            # In degraded mode, stop retrying — don't keep hammering the same
            # failing operation. Let the loop continue with reduced functionality.
            return False

        # Get recovery strategy
        strategy_map = {
            ErrorCategory.TRANSIENT: "network_error",
            ErrorCategory.RATE_LIMIT: "rate_limit",
            ErrorCategory.AUTHENTICATION: "auth_error",
            ErrorCategory.TIMEOUT: "timeout_error",
            ErrorCategory.RESOURCE: "resource_error",
            ErrorCategory.EXTERNAL: "model_error",
            ErrorCategory.VALIDATION: "command_error",
        }

        strategy_name = strategy_map.get(category, "unknown_error")
        strategy = self._error_recovery_strategies.get(strategy_name, self._recover_unknown_error)

        self.logger.info(f"Attempting recovery with strategy: {strategy_name}")
        return strategy(error, ctx, delay)

    def _recover_network_error(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recover from network errors with exponential backoff."""
        # Limit retries for network errors — if we've tried too many times, give up
        if self._consecutive_errors > 10:
            self.logger.error("Network error: exceeded max retries, propagating")
            return False
        wait = min(delay * (2 ** min(self._consecutive_errors - 1, 4)), 60.0)
        self.logger.warning(f"Network error - waiting {wait:.1f}s before retry (attempt {self._consecutive_errors})")
        self._term_log.error(f"⚠️ Network error - retrying in {wait:.0f}s...")
        self._notify_telegram_error(ctx, f"⚠️ Network error - retrying in {wait:.0f}s...")

        self._raise_if_cancelled(ctx)
        time.sleep(wait)
        return True

    def _recover_rate_limit(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recover from rate limit errors with longer backoff."""
        wait = min(delay * (2 ** min(self._consecutive_errors - 1, 3)), 120.0)
        self.logger.warning(f"Rate limited - waiting {wait:.1f}s before retry")
        self._term_log.error(f"⏳ Rate limited - retrying in {wait:.0f}s...")
        self._notify_telegram_error(ctx, f"⏳ Rate limited - retrying in {wait:.0f}s...")

        self._raise_if_cancelled(ctx)
        time.sleep(wait)
        return True

    def _recover_auth_error(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recover from auth errors by switching provider."""
        self.logger.warning("Authentication error - attempting provider switch")
        self._term_log.error("🔑 Authentication error - switching provider...")

        next_provider = self._try_switch_provider(ctx, str(error))
        if next_provider:
            self._notify_telegram_error(ctx, f"🔑 Switched to provider: {next_provider}")
            return True

        # No fallback available - enter degraded mode
        self._enter_degraded_mode(["api_calls"])
        return True

    def _recover_timeout_error(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recover from timeout errors."""
        wait = min(delay * (2 ** min(self._consecutive_errors - 1, 3)), 30.0)
        self.logger.warning(f"Timeout error - waiting {wait:.1f}s before retry")
        self._term_log.error(f"⏱ Timeout - retrying in {wait:.0f}s...")

        self._raise_if_cancelled(ctx)
        time.sleep(wait)
        return True

    def _recover_resource_error(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recover from resource errors by freeing resources."""
        self.logger.warning("Resource error - attempting cleanup")
        self._term_log.error("🧹 Resource error - running cleanup...")

        # Attempt disk cleanup
        try:
            from ..utils.resilience_engine import emergency_disk_cleanup
            ok, msg = emergency_disk_cleanup(target_free_gb=2.0)
            self.logger.info(f"Cleanup result: {msg}")
        except Exception:
            pass

        # Force sleep to recover
        self._notify_telegram_error(ctx, "🛏 Resources low - forcing sleep to recover")
        self._handle_sleep(ctx)
        return True

    def _recover_model_error(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recover from model/API errors by switching provider."""
        self.logger.warning("Model error - attempting provider switch")
        self._term_log.error("🔄 Model error - switching provider...")

        next_provider = self._try_switch_provider(ctx, str(error))
        if next_provider:
            return True

        # Fallback: wait and retry
        wait = min(delay * 2, 30.0)
        time.sleep(wait)
        return True

    def _recover_command_error(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recover from command execution errors."""
        self.logger.warning(f"Command error: {error}")
        # Command errors are usually not retryable at this level
        # Just log and continue
        self._consecutive_errors = max(0, self._consecutive_errors - 1)
        return True

    def _recover_unknown_error(self, error: Exception, ctx: AutonomousContext, delay: float) -> bool:
        """Recovery strategy for unknown errors."""
        self.logger.warning(f"Unknown error type: {type(error).__name__}: {error}")

        if self._consecutive_errors < 5:
            # Try waiting briefly
            time.sleep(min(delay, 5.0))
            return True

        # Too many unknown errors - try provider switch
        next_provider = self._try_switch_provider(ctx, str(error))
        if next_provider:
            return True

        # Enter degraded mode
        self._enter_degraded_mode()
        return True

    def _enter_degraded_mode(self, disabled_features: Optional[List[str]] = None) -> None:
        """Enter degraded mode with reduced functionality."""
        self._degraded_mode = True
        self._degraded_features = disabled_features or []
        self.logger.warning(f"Entering degraded mode. Disabled features: {self._degraded_features}")
        self._term_log.error("⚠️ Entering degraded mode - some features may be unavailable")

    def _exit_degraded_mode(self) -> None:
        """Exit degraded mode and restore full functionality."""
        if self._degraded_mode:
            self._degraded_mode = False
            self._degraded_features = []
            self._consecutive_errors = 0
            self.logger.info("Exiting degraded mode - full functionality restored")
            self._term_log.thinking("✅ Exiting degraded mode")

    def _record_successful_iteration(self) -> None:
        """Record a successful iteration and potentially exit degraded mode."""
        self._consecutive_errors = 0
        self._last_successful_iteration = time.time()

        if self._degraded_mode:
            # If we've had 10 successful iterations, try exiting degraded mode
            if random.random() < 0.1:  # 10% chance per iteration
                self._exit_degraded_mode()

    # ------------------------------------------------------------------ #
    #  Periodic Auto-Save — crash resilience                            #
    # ------------------------------------------------------------------ #

    def _start_auto_save(self, ctx: AutonomousContext) -> None:
        """Start a background thread that periodically flushes context to disk."""
        self._auto_save_running = True
        self._auto_save_thread = threading.Thread(
            target=self._auto_save_loop,
            args=(ctx,),
            daemon=True,
            name="auto_save",
        )
        self._auto_save_thread.start()
        self.logger.info("Auto-save thread started", interval_s=self._auto_save_interval)

    def _stop_auto_save(self) -> None:
        """Stop the auto-save background thread."""
        self._auto_save_running = False
        if self._auto_save_thread and self._auto_save_thread.is_alive():
            self._auto_save_thread.join(timeout=5)
        self._auto_save_thread = None

    def _auto_save_loop(self, ctx: AutonomousContext) -> None:
        """Background loop that sleeps and then calls _auto_save_context."""
        while self._auto_save_running:
            for _ in range(int(self._auto_save_interval * 10)):
                if not self._auto_save_running:
                    return
                time.sleep(0.1)
            try:
                # Take a snapshot of the log under the lock to avoid
                # reading a list that is being modified by the main thread.
                with self._auto_save_lock:
                    log_snapshot = list(ctx.execution_log)
                self._auto_save_context(ctx, log_snapshot)
            except Exception as e:
                self.logger.warning(f"Auto-save failed (non-critical): {e}")

    def _auto_save_context(self, ctx: AutonomousContext,
                            log_snapshot: Optional[List[str]] = None) -> None:
        """
        Lightweight, LLM-free context flush to disk.

        Writes exit_state.json + context_log.txt with the current running
        state.  Called periodically (every 60 s via background thread and
        every 10 iterations in the main loop) so that even a hard crash
        (kill -9, power loss, device reboot) leaves recoverable context.

        Args:
            ctx: The current autonomous context.
            log_snapshot: Optional pre-snapshot of execution_log taken under
                the auto-save lock. If None, reads ctx.execution_log directly
                (safe only when called from the main thread).
        """
        try:
            _project_root = _get_project_root()
            context_dir = _project_root / ".context"
            context_dir.mkdir(parents=True, exist_ok=True)

            # Use the provided snapshot or read directly (main-thread call)
            execution_log = log_snapshot if log_snapshot is not None else ctx.execution_log

            aux = self._collect_auxiliary_context(ctx, execution_log)
            compressed = self._heuristic_compress(ctx, aux)

            state = {
                "status": "Auto-saved during execution — recoverable after crash/restart",
                "goal": ctx.current_goal,
                "user_prompt": ctx.user_prompt,
                "iteration_count": ctx.iteration_count,
                "compressed_context": compressed,
                "timestamp": time.time(),
                "telegram_mode": ctx.telegram_mode,
                "discord_mode": ctx.discord_mode,
                "telegram_user_id": ctx.telegram_user_id,
                "auxiliary": {
                    "git_diff": aux.get("git_diff", ""),
                    "metadata": aux.get("metadata", ""),
                    "errors": aux.get("errors", ""),
                    "log_tail": aux.get("log_tail", ""),
                },
                "restart_provider": ctx.metadata.get("restart_provider", ""),
                "restart_model": ctx.metadata.get("restart_model", ""),
            }

            state_file = context_dir / "exit_state.json"
            _atomic_write_json(state_file, state)

            log_file = context_dir / "context_log.txt"
            header = (
                "Status: Auto-saved during execution — recoverable after crash/restart\n"
                f"Goal: {ctx.current_goal}\n"
                f"Iterations: {ctx.iteration_count}\n"
                f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n"
                f"{'=' * 60}\n"
            )
            with open(log_file, "w", encoding="utf-8") as _f:
                _f.write(header)
                _f.write(compressed)
                _f.write("\n")

            self.logger.debug(
                "Auto-save complete",
                iteration=ctx.iteration_count,
                log_lines=len(execution_log),
            )
        except Exception as e:
            self.logger.warning(f"Auto-save error (non-critical): {e}")

    # ------------------------------------------------------------------ #
    #  Main entry point — the agent's eternal loop                      #
    # ------------------------------------------------------------------ #

    def execute_instruction(self, user_prompt: str,
                            conversation_history: Optional[ConversationHistory] = None,
                            telegram_mode: bool = False,
                            discord_mode: bool = False,
                            telegram_user_id: Optional[int] = None,
                            cancel_event: Optional[threading.Event] = None
                            ) -> AutonomousContext:
        """
        Enter the autonomous loop.  The agent thinks and acts continuously,
        never exiting on its own.  The only ways out are:
          - the agent issues a `sleep` command (returns after sleep workflow)
          - the cancel_event is set
          - an unhandled exception

        Args:
            user_prompt:   Initial seed for the agent's thinking.  May be
                           empty — the agent will decide what to do on its own.
            conversation_history: Conversation history for messaging mode
            telegram_mode: Whether running in Telegram mode
            discord_mode: Whether running in Discord mode
            telegram_user_id: User ID for sending messages
            cancel_event:  Threading event used for cancellation

        Returns:
            AutonomousContext (only on sleep or cancellation)
        """
        self.logger.info("Starting Autonomous Loop execution",
                         user_prompt=user_prompt, telegram_mode=telegram_mode)

        # ---- Load context from .context/ BEFORE clearing files ----
        # We must read the saved context into memory first so that it can
        # be injected into the model prompt on the very first iteration.
        self._saved_ctx_block = get_context_for_prompt()
        # Track whether this is a resume so we only inject context once.
        self._is_resuming = bool(self._saved_ctx_block)

        # Display context in terminal for the user
        if context_files_exist():
            self._term_log.separator()
            self._term_log.thinking("Loading context from .context/ …")
            display_context_in_terminal()
            self._term_log.separator()

        # Detect sleep resume BEFORE clear_context_state (which deletes files).
        self._resuming_from_sleep = False
        if self._is_resuming:
            try:
                from .context_manager import load_context_state
                _st = load_context_state()
                if _st and "sleep" in _st.get("status", "").lower():
                    self._resuming_from_sleep = True
            except Exception:
                pass

        # Clear state files now — the context is already in memory above.
        # This prevents stale context from being re-loaded on the next
        # startup while guaranteeing the first iteration sees it.
        if context_files_exist():
            try:
                from .context_manager import clear_context_state
                clear_context_state()
                self._term_log.thinking("Context state files cleared after consumption.")
            except Exception:
                pass

        # Store the original instruction separately from the resume context.
        # The running goal stays clean so it doesn't permanently bloat the
        # prompt on every subsequent iteration.
        _original_instruction = user_prompt or ""
        if self._saved_ctx_block:
            _wake_up = ""
            if self._resuming_from_sleep:
                if discord_mode:
                    _wake_cmd = "discord(\U0001f44b Woke up from sleep. Resuming work immediately.)"
                    _mode_hint = "Discord"
                    _mode_cmd = "discord()"
                else:
                    _wake_cmd = "telegram(\U0001f44b Woke up from sleep. Resuming work immediately.)"
                    _mode_hint = "Telegram"
                    _mode_cmd = "telegram()"
                _wake_up = (
                    "## WAKE-UP NOTIFICATION (MANDATORY)\n"
                    "You just woke up from a sleep restart. "
                    "Your VERY FIRST action MUST be:\n"
                    f"  {_wake_cmd}\n"
                    "After sending that message, continue with the work below.\n"
                    f"IMPORTANT: You are in {_mode_hint} mode. Send progress updates via {_mode_cmd} "
                    f"every 5-10 iterations. NEVER go more than 10 iterations without {_mode_cmd}.\n\n"
                )
            user_prompt = (
                f"{_wake_up}"
                "## RESUMING FROM PREVIOUS SESSION\n"
                "The following context was saved before the last shutdown.\n"
                "Resume work immediately from where you left off.\n\n"
                f"{self._saved_ctx_block}\n\n"
                "## CURRENT INSTRUCTION\n"
                f"{_original_instruction or '(self-directed — continue working)'}"
            )

        self._term_log.separator()
        self._term_log.thinking(f"Initial prompt: {user_prompt or '(none — self-directed)'}")
        self._term_log.thinking("No task boundary. I think continuously and act on what matters.")

        # Restore terminal history from disk so the agent sees previous
        # session activity even after a force-kill or signal interruption.
        #
        # IMPORTANT: When resuming from a sleep restart, we must NOT restore
        # the full uncompressed terminal history log.  The whole point of the
        # sleep workflow is to compress the context — if we restored the old
        # raw log here AND also injected the compressed context block into the
        # prompt, the model would receive BOTH the compressed and uncompressed
        # versions, wasting tokens and causing confusion.
        #
        # Solution: On sleep resume, replace the restored log with a brief
        # note pointing to the compressed context (which is already injected
        # into the prompt via saved_context_block).  For non-sleep resumes
        # (e.g. crash recovery), restore the log as before.
        _restored_log = self._restore_terminal_history_log()
        if self._resuming_from_sleep:
            # Discard the old uncompressed log entirely.
            # The compressed context is already in self._saved_ctx_block and
            # will be injected into the prompt by _run_thinking.
            _restored_log = [
                "[SYSTEM] Previous session context was compressed during sleep. "
                "See the SAVED CONTEXT section above for the compressed summary. "
                "The full uncompressed log has been discarded to conserve context window."
            ]
            self._term_log.thinking(
                "Sleep resume: replaced uncompressed terminal history with compressed context."
            )

        ctx = AutonomousContext(
            user_prompt=user_prompt,
            current_goal=_original_instruction or "Self-directed: observe, explore, and act.",
            execution_log=_restored_log,
            conversation_history=conversation_history,
            telegram_mode=telegram_mode,
            discord_mode=discord_mode,
            telegram_user_id=telegram_user_id,
            cancel_event=cancel_event or threading.Event(),
            metadata={
                "os_info": self._get_os_info(),
                "restart_provider": self.model_runner.provider or "",
                "restart_model": self.model_runner.model or "",
                "restart_telegram_mode": True if telegram_mode else False,
                "restart_discord_mode": True if discord_mode else False,
                "restart_telegram_user_id": telegram_user_id,
            },
        )

        if _restored_log:
            self._term_log.thinking(
                f"Restored {len(_restored_log)} execution log entries from terminal_history/"
            )

        self._current_context = ctx
        with self._cancel_lock:
            self._active_cancel_event = ctx.cancel_event

        # Start the periodic auto-save thread so that context is
        # continuously flushed to disk even if the process is killed.
        self._start_auto_save(ctx)

        try:
            while True:
                self._raise_if_cancelled(ctx)

                ctx.iteration_count += 1

                # --- Write heartbeat for supervisor monitoring ---
                if self._heartbeat:
                    self._heartbeat.beat(ctx.iteration_count)

                # --- Forced wake-up notification on first iteration
                #     after a sleep restart. Uses self._resuming_from_sleep
                #     (captured before clear_context_state deleted files).
                if ctx.iteration_count == 1 and self._resuming_from_sleep:
                    try:
                        _wake_msg = (
                            "\U0001f44b Woke up from sleep. Resuming work immediately."
                        )
                        if ctx.discord_mode:
                            self._exec_discord(ctx, _wake_msg)
                            self.logger.info("Forced wake-up discord message sent after sleep restart")
                        elif ctx.telegram_mode:
                            self._exec_telegram(ctx, _wake_msg)
                            self.logger.info("Forced wake-up telegram sent after sleep restart")
                    except Exception as _e:
                        self.logger.warning(f"Failed to send wake-up message: {_e}")
                    # Clear the resuming flag after first iteration to prevent
                    # re-injection of saved context on subsequent iterations
                    self._is_resuming = False

                # --- Periodic auto-save (every 10 iterations as safety net) ---
                if ctx.iteration_count % 10 == 0:
                    self._auto_save_context(ctx)

                # --- THINK ---
                self._term_log.phase(ctx.iteration_count, "THINKING")
                ctx.current_phase = LoopPhase.THINKING

                try:
                    think_output = self._run_thinking(ctx)
                except ExecutionError as e:
                    # Use enhanced error recovery system
                    if not self._classify_and_recover(e, ctx):
                        raise
                    ctx.iteration_count -= 1
                    continue
                except Exception as e:
                    # Catch any other exception during thinking
                    self.logger.error(f"Unexpected error during thinking: {e}")
                    if not self._classify_and_recover(e, ctx):
                        raise
                    ctx.iteration_count -= 1
                    continue

                # Reset consecutive errors on success
                self._record_successful_iteration()

                self._raise_if_cancelled(ctx)

                # --- Parse commands from the model's response ---
                # Guard against None/empty model output
                if not think_output:
                    self.logger.warning(
                        "Model returned empty output",
                        iteration=ctx.iteration_count,
                    )
                    # Give the model feedback so it can self-correct
                    self._append_log(
                        ctx,
                        "[SYSTEM] Your last response was empty. "
                        "You MUST output at least one command() or tool call "
                        "(read/write/edit/glob/grep/bash) on every turn."
                    )
                    commands = []
                else:
                    try:
                        commands = self._parse_model_commands(think_output)
                    except Exception as e:
                        self.logger.error(f"Command parsing error: {e}")
                        self._append_log(
                            ctx,
                            "[SYSTEM] Parse error: " + str(e) + ". "
                            "Output ONLY valid commands like: "
                            "command(ls), read(path='/file'), bash(command='cmd'). "
                            "No explanations, no natural language."
                        )
                        continue

                # --- Track model output for identical-output detection ---
                output_hash = hashlib.md5((think_output or "").encode()).hexdigest()
                if output_hash == self._last_output_hash and (think_output or "").strip():
                    self._consecutive_identical_outputs += 1
                else:
                    self._consecutive_identical_outputs = 0
                    # Reset the fairy guard when output genuinely changes.
                    # This is the ONLY place where _curiosity_fairy_invoked
                    # should be cleared based on output — the action-signature
                    # tracking in _check_repetition_breaker and
                    # _record_iteration_actions owns this flag otherwise.
                    self._curiosity_fairy_invoked = False
                self._last_output_hash = output_hash

                # --- Record action signature for repetition breaker ---
                self._record_iteration_actions(commands)

                # --- Repetition breaker: code-level loop intervention ---
                break_msg = self._check_repetition_breaker(ctx)
                if break_msg:
                    self._append_log(ctx, break_msg)
                    # If the breaker says to force sleep, give the model
                    # one chance to execute sleep on its own next turn.
                    # If it doesn't, we force it on the NEXT iteration.
                    if "PERSISTENT LOOP" in break_msg:
                        self.logger.warning("Persistent loop — will force sleep on next iteration if model doesn't")
                        self._force_sleep_pending = True
                    # If the Curiosity Fairy was invoked, call it now and
                    # inject its suggestion into the execution log.
                    if "CURIOSITY FAIRY ACTIVATED" in break_msg:
                        suggestion = self._invoke_curiosity_fairy(ctx)
                        if suggestion:
                            fairy_msg = (
                                f"[Message from the Curiosity Fairy] "
                                f"```\n{suggestion}\n```"
                            )
                            self._append_log(ctx, fairy_msg)
                            self._term_log.thinking(
                                f"🧚 Curiosity Fairy suggests: {suggestion[:120]}"
                            )

                # Check for forced sleep (persistent loop breaker from a
                # PREVIOUS iteration — the model was given one chance but
                # didn't execute sleep, so we force it now.)
                if self._force_sleep_pending:
                    # Did the model include sleep in its commands? If so,
                    # let it proceed naturally — no need to force.
                    if any(cmd[0] == "sleep" for cmd in commands):
                        self._force_sleep_pending = False
                        self.logger.info("Model executed sleep after persistent loop warning — no force needed")
                    else:
                        self._force_sleep_pending = False
                        self.logger.warning("Force-sleep: model did not execute sleep after persistent loop warning")
                        self._term_log.separator()
                        self._term_log.error("🛑 Force-sleep: persistent loop not broken by model")
                        self._notify_telegram_error(
                            ctx,
                            "🛑 Force-sleep: persistent loop detected that the model "
                            "could not break. Compressing context and restarting."
                        )
                        self._handle_sleep(ctx)
                        # _handle_sleep does os.execv — never reached

                # --- Detect empty/repetitive iterations (anti-drift) ---
                nudge = self._detect_empty_iteration(ctx, think_output, commands)
                if nudge:
                    # Move on so next thinking phase sees the Curiosity Fairy.
                    # Keep the loop tight; the model will act on the nudge.
                    continue

                # Check for sleep / exit first (they control the loop)
                if any(cmd[0] == "exit" for cmd in commands):
                    self._term_log.separator()
                    self._term_log.thinking("Exit requested — saving context and shutting down...")
                    self._handle_exit(ctx)
                    ctx.end_time = time.time()
                    return ctx

                if any(cmd[0] == "sleep" for cmd in commands):
                    self._term_log.separator()
                    self._term_log.thinking("Sleep requested — compressing context and restarting...")
                    self._handle_sleep(ctx)
                    return ctx

                # --- EXECUTE ---
                self._term_log.phase(ctx.iteration_count, "EXECUTING")
                ctx.current_phase = LoopPhase.EXECUTING
                try:
                    self._execute_commands(ctx, commands)
                except Exception as e:
                    self.logger.error(f"Command execution error: {e}")
                    self._append_log(ctx, f"[execution error] {e}")
                    # Continue to next iteration - don't let execution errors stop the loop

                self._raise_if_cancelled(ctx)

                # Non-blocking check for new Telegram messages.  The agent
                # must never wait idly: a tiny timeout lets other threads
                # deliver messages without causing any perceptible dormancy.
                self._new_message_event.wait(timeout=0.05)
                self._new_message_event.clear()

                # --- Periodic resource health check (every 100 iterations) ---
                if ctx.iteration_count % 100 == 0:
                    self._check_and_handle_resources(ctx)

                # --- Periodic self-diagnostic (every 50 iterations) ---
                if ctx.iteration_count % 50 == 0:
                    self._run_self_diagnostic(ctx)

        except _PipelineCancelledError as e:
            self.logger.info(f"Autonomous Loop cancelled: {e}")
            ctx.current_phase = LoopPhase.FAILED
            ctx.error = str(e)
            ctx.cancelled = True
            ctx.end_time = time.time()
            self._term_log.cancelled()
            return ctx

        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt received - saving state and exiting")
            ctx.current_phase = LoopPhase.FAILED
            ctx.error = "KeyboardInterrupt"
            ctx.cancelled = True
            ctx.end_time = time.time()
            self._term_log.cancelled()
            # Save exit state for recovery
            try:
                self._handle_exit(ctx, fast=True)
            except Exception:
                pass
            return ctx

        except Exception as e:
            self.logger.error(f"Autonomous Loop execution failed: {e}")
            ctx.current_phase = LoopPhase.FAILED
            ctx.error = str(e)
            ctx.end_time = time.time()
            self._term_log.error(str(e))
            self._notify_telegram_error(ctx, str(e))

            # Attempt to save exit state for recovery
            try:
                self._handle_exit(ctx, fast=True)
            except Exception:
                pass

            return ctx

        finally:
            self._stop_auto_save()
            # Shut down sub-agent manager to release thread pool resources
            try:
                if self.sub_agent_manager:
                    self.sub_agent_manager.shutdown(wait=False, cancel_pending=True)
            except Exception:
                pass
            with self._cancel_lock:
                if self._active_cancel_event is ctx.cancel_event:
                    self._active_cancel_event = None

    # ------------------------------------------------------------------ #
    #  Thinking phase                                                    #
    # ------------------------------------------------------------------ #

    def _check_loop_detection(self, ctx: AutonomousContext) -> Optional[str]:
        """
        Analyze the execution log for repeated command patterns.
        Returns a warning string if a loop is detected, or None.
        """
        if len(self._recent_commands) < self._loop_repeat_threshold:
            return None

        # Check if the last N commands are all the same
        recent = self._recent_commands[-self._loop_detection_window:]

        # Count occurrences of each command signature
        from collections import Counter
        counts = Counter(recent)
        most_common_cmd, most_common_count = counts.most_common(1)[0]

        if most_common_count >= self._loop_repeat_threshold:
            self._loop_warning_active = True
            return (
                "\n\n*** LOOP DETECTION WARNING ***\n"
                f"The command `{most_common_cmd}` has been executed "
                f"{most_common_count} times in the last {len(recent)} iterations.\n"
                "YOU MUST NOT run this command again. Choose a completely "
                "different action or execute `sleep` to reset.\n"
                "*********************************\n"
            )

        self._loop_warning_active = False
        return None

    def _record_command(self, cmd_type: str, cmd_arg: str) -> None:
        """Record a command signature for loop detection."""
        # Create a normalized signature
        sig = f"{cmd_type}:{cmd_arg[:100]}"
        self._recent_commands.append(sig)
        # Keep only the last N commands
        if len(self._recent_commands) > self._loop_detection_window * 3:
            self._recent_commands = self._recent_commands[-self._loop_detection_window * 2:]

    # ------------------------------------------------------------------ #
    #  Repetition breaker — code-level loop prevention                   #
    # ------------------------------------------------------------------ #

    def _normalize_action_signature(self, commands: List[Tuple[str, str]]) -> str:
        """Create a normalized signature from all commands in one iteration.

        This captures the *set* of distinct action types and their primary
        arguments, ignoring order and minor differences (e.g. thinking()
        content).  Two iterations that read the same file, then run the
        same bash command, produce the same signature even if the thinking()
        text differs.

        Format: sorted list of "type:primary_arg_hash" joined by "|".
        """
        parts: List[str] = []
        for cmd_type, cmd_arg in commands:
            # Skip sleep/exit — they're control commands, not actions
            if cmd_type in ("sleep", "exit", "parallel"):
                continue
            # Normalize: for tool_call, extract the tool name and the
            # primary argument (path/pattern/command) only
            if cmd_type == "tool_call":
                try:
                    import json
                    args = json.loads(cmd_arg)
                    tool = args.get("__tool__", "")
                    # Hash the primary argument so long content doesn't
                    # make signatures incomparable
                    primary_key = {"read": "path", "write": "path", "edit": "path",
                                   "glob": "pattern", "grep": "pattern",
                                   "bash": "command", "todo": "action",
                                   "memo": "key"}.get(tool, "")
                    primary_val = args.get(primary_key, "")
                    sig_part = f"tool:{tool}:{hashlib.md5(primary_val.encode()).hexdigest()[:8]}"
                except Exception:
                    sig_part = f"tool:unknown:{hashlib.md5(cmd_arg.encode()).hexdigest()[:8]}"
            elif cmd_type == "thinking":
                # thinking() content varies — only count the type, not the content
                sig_part = "thinking"
            elif cmd_type in ("telegram", "discord"):
                # messaging: only count type (content varies naturally)
                sig_part = cmd_type
            else:
                # command(), etc. — hash the argument to normalize
                sig_part = f"{cmd_type}:{hashlib.md5(cmd_arg.encode()).hexdigest()[:8]}"
            parts.append(sig_part)
        # Sort and join so order doesn't matter
        parts.sort()
        return "|".join(parts) if parts else "__empty__"

    def _record_iteration_actions(self, commands: List[Tuple[str, str]]) -> None:
        """Record the action signature for an iteration and update
        repetition-breaker counters.  Called once per iteration AFTER
        command parsing but BEFORE execution.
        """
        sig = self._normalize_action_signature(commands)

        self._action_history.append(sig)
        if len(self._action_history) > self._max_action_history:
            self._action_history = self._action_history[-self._max_action_history:]

        # Track consecutive identical iterations.
        # _consecutive_same_action counts the TOTAL number of consecutive
        # iterations with the same signature (including the first occurrence).
        # First occurrence = 1, second = 2, etc. This makes threshold
        # comparisons semantically correct: threshold 3 means "3 times".
        # Empty signatures ("__empty__") are not counted as repeats.
        if sig == "__empty__":
            # Empty iteration — don't count it, don't reset either
            pass
        elif sig == self._last_action_signature:
            self._consecutive_same_action += 1
        else:
            self._consecutive_same_action = 1
        self._last_action_signature = sig

        # Update persistent loop memory (survives wake-ups)
        if sig != "__empty__":
            self._persistent_loop_patterns[sig] = (
                self._persistent_loop_patterns.get(sig, 0) + 1
            )
            # Trim patterns that haven't been seen recently
            if len(self._persistent_loop_patterns) > 20:
                # Keep only patterns seen ≥2 times
                self._persistent_loop_patterns = {
                    k: v for k, v in self._persistent_loop_patterns.items()
                    if v >= 2
                }

    def _check_repetition_breaker(self, ctx: AutonomousContext) -> Optional[str]:
        """Check if the repetition breaker should intervene.

        Returns a forced-instruction string that will be injected into
        the execution log to break the loop, or None if no intervention
        is needed.

        Intervention levels:
        0. Same command ≥ curiosity_fairy_threshold (3): invoke the Curiosity
           Fairy to suggest a new direction (early warning at 3 consecutive
           identical actions).
        1. Consecutive same action ≥ repetition_break_threshold (6): inject a
           SYSTEM break message and force the model to do something different
           (hard enforcement after Fairy was ignored).
        2. Persistent pattern ≥ persistent threshold (8): force a sleep
           (the loop is deep-rooted across wake-ups and prompt-level
           intervention won't work).
        """
        # Level 0: Curiosity Fairy — fires at threshold=3 (early warning).
        if (self._consecutive_same_action >= self._curiosity_fairy_threshold
                and not self._curiosity_fairy_invoked):
            self.logger.warning(
                f"Curiosity Fairy trigger: {self._consecutive_same_action} "
                f"consecutive identical command signatures "
                f"(sig: {self._last_action_signature[:60]})"
            )
            self._curiosity_fairy_invoked = True
            return (
                "[SYSTEM] 🧚 CURIOSITY FAIRY ACTIVATED. You have executed "
                "the same command for "
                f"{self._consecutive_same_action} consecutive iterations. "
                "The Curiosity Fairy is being invoked to suggest a new direction. "
                "A message from the Curiosity Fairy will appear in your execution log."
            )

        # Level 1: consecutive identical actions
        if self._consecutive_same_action >= self._repetition_break_threshold:
            self.logger.warning(
                f"Repetition breaker: {self._consecutive_same_action} consecutive "
                f"identical-action iterations (sig: {self._last_action_signature[:60]})"
            )
            # Reset the counter so we don't keep firing immediately.
            # Use 1 (not 0) because the current iteration already
            # produced this signature — it counts as occurrence #1
            # of a potential new streak.
            self._consecutive_same_action = 1
            # Clear the fairy guard so it can fire again if the
            # model restarts the same loop after this warning.
            self._curiosity_fairy_invoked = False
            return (
                "[SYSTEM] 🚨 LOOP BREAKER ACTIVATED. You have repeated the "
                "same action pattern for too many consecutive iterations. "
                "You MUST now do something completely different. Pick one: "
                "(a) Execute `sleep` to reset your context. "
                "(b) Explore a NEW file or directory you haven't looked at. "
                "(c) Run a NEW shell command unrelated to your current task. "
                "(d) Send a telegram()/discord() update and change direction. "
                "DO NOT repeat the previous action. This is a CODE-LEVEL "
                "enforcement — ignoring this will trigger forced sleep."
            )

        # Level 2: persistent loop pattern across wake-ups
        for sig, count in self._persistent_loop_patterns.items():
            if count >= self._persistent_loop_threshold:
                self.logger.warning(
                    f"Persistent loop pattern detected: sig={sig[:60]} "
                    f"appeared {count} times across wake-ups"
                )
                # Force a sleep — this loop is deeply rooted and the model
                # will keep re-entering it even after user intervention.
                self._persistent_loop_patterns.clear()
                return (
                    "[SYSTEM] 🛑 PERSISTENT LOOP DETECTED. The action pattern "
                    f"`{sig[:80]}` has been repeated {count} times across "
                    "multiple wake-ups. This indicates a deeply-rooted loop "
                    "that prompt warnings cannot break. YOU MUST execute "
                    "`sleep` immediately. This is NOT optional — the engine "
                    "will force sleep on the next iteration if you don't."
                )

        return None

    # ------------------------------------------------------------------ #
    #  Curiosity Fairy — creative loop-breaker                          #
    # ------------------------------------------------------------------ #

    def _invoke_curiosity_fairy(self, ctx: AutonomousContext) -> Optional[str]:
        """
        Deterministic loop-breaker: generate concrete suggestions from
        real system data instead of calling the LLM (which may be the
        same low-capacity model that is stuck).

        Returns a suggestion string to inject into the execution log.
        """
        self.logger.info("Curiosity Fairy invoked -- breaking command loop",
                         consecutive=self._consecutive_same_action)

        suggestions: list = []

        # 1. Collect what the agent has already done from the execution log.
        # Use multiple regex patterns to match the various log line formats:
        #   read(path="..."), edit(path="..."), $ command, command(...), bash(...)
        files_read: list = []
        files_edited: list = []
        commands_run: list = []
        _path_pattern = re.compile(r'\bpath\s*=\s*["\']([^"\']+)["\']')
        _dollar_pattern = re.compile(r'^\$\s+(.+)$ ')
        _cmd_pattern = re.compile(r'\b(command|bash)\((.+)\) ', re.DOTALL)
        for line in ctx.execution_log[-100:]:
            ls = line.strip()
            if "read(" in ls:
                m = _path_pattern.search(ls)
                if m and m.group(1) not in files_read:
                    files_read.append(m.group(1))
            elif "edit(" in ls:
                m = _path_pattern.search(ls)
                if m and m.group(1) not in files_edited:
                    files_edited.append(m.group(1))
            elif ls.startswith("$"):
                m = _dollar_pattern.match(ls)
                if m:
                    cmd_text = m.group(1)[:80]
                    if cmd_text not in commands_run:
                        commands_run.append(cmd_text)
            elif "command(" in ls or "bash(" in ls:
                m = _cmd_pattern.search(ls)
                if m:
                    cmd_text = m.group(2)[:80]
                    if cmd_text not in commands_run:
                        commands_run.append(cmd_text)

        # 2. Get real filesystem data for fresh suggestions.
        # Use the resolved project root for reliable path resolution.
        _project_root = _get_project_root()
        if not _project_root.exists():
            self.logger.warning(
                f"Curiosity Fairy: project root does not exist: {_project_root}"
            )
            _project_root = Path.cwd()

        try:
            _find = subprocess.run(
                ["find", ".", "-maxdepth", "3", "-type", "f",
                 "-name", "*.py", "-o", "-name", "*.md", "-o", "-name", "*.yaml",
                 "-o", "-name", "*.json", "-o", "-name", "*.txt"],
                capture_output=True, text=True, timeout=5,
                cwd=str(_project_root)
            )
            all_files = [f for f in _find.stdout.strip().splitlines()
                         if f and not f.startswith("./.git/")]
            unread = [f for f in all_files if f not in files_read][:5]
        except Exception as _e:
            self.logger.debug(f"Curiosity Fairy find failed: {_e}")
            unread = []

        try:
            _git = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, timeout=5,
                cwd=str(_project_root))
            git_changes = _git.stdout.strip()[:300] if _git.returncode == 0 else ""
        except Exception as _e:
            self.logger.debug(f"Curiosity Fairy git failed: {_e}")
            git_changes = ""

        try:
            _todos = subprocess.run(
                ["grep", "-r", "--include=*.py", "-l",
                 "TODO|FIXME|HACK|XXX",
                 ".", "--exclude-dir=.git", "--exclude-dir=venv",
                 "--exclude-dir=__pycache__"],
                capture_output=True, text=True, timeout=5,
                cwd=str(_project_root)
            )
            todo_files = [f for f in _todos.stdout.strip().splitlines() if f][:5]
        except Exception as _e:
            self.logger.debug(f"Curiosity Fairy grep failed: {_e}")
            todo_files = []


        # 3. Build concrete suggestion
        suggestion_parts = [
            "[Curiosity Fairy] You are stuck in a loop. Concrete next steps:"
        ]

        if unread:
            suggestion_parts.append(
                f"READ a new file: {unread[0]}"
            )
            if len(unread) > 1:
                suggestion_parts.append(
                    f"  (also: {', '.join(unread[1:3])})"
                )

        if todo_files:
            suggestion_parts.append(
                f"CHECK for TODOs in: {todo_files[0]}"
            )

        if git_changes:
            suggestion_parts.append(f"GIT CHANGES: {git_changes}")
            suggestion_parts.append("  Try: git diff, or commit changes.")

        if not unread and not todo_files and not git_changes:
            suggestion_parts.append("Try something completely different:")
            suggestion_parts.append(
                "  bash(command='ls -la') to see current directory"
            )
            suggestion_parts.append(
                "  glob(pattern='**/*') to discover files"
            )

        if commands_run:
            suggestion_parts.append(
                f"AVOID repeating: {commands_run[-1]}"
            )

        suggestion = "\n".join(suggestion_parts)
        self.logger.info("Curiosity Fairy generated deterministic suggestion")
        return suggestion

    # ------------------------------------------------------------------ #
    #  Proactive Anti-Drift — never idle, always do something new       #
    # ------------------------------------------------------------------ #

    def _detect_empty_iteration(self, ctx: AutonomousContext,
                                 model_output: str,
                                 commands: List[Tuple[str, str]]) -> Optional[str]:
        """
        Detect iterations where no meaningful work was done.

        This method ONLY tracks empty/repetitive iterations and resets
        counters when real work is produced.  It does NOT invoke the
        Curiosity Fairy directly — that is handled exclusively by
        _check_repetition_breaker (the single Fairy invocation pathway).

        An iteration is "empty" when ANY of the following is true:
          - No command() / tool_call() / telegram() / discord() was emitted
          - The model output is byte-for-byte identical to the previous one
          - The action signature is semantically identical to the previous one

        Returns None (never injects a nudge directly).
        """
        # Check for meaningful commands
        has_real_work = any(
            c[0] in ("command", "tool_call", "telegram", "discord") for c in commands
        )

        # Check for identical model output (exact repetition)
        digest = hashlib.md5((model_output or "").encode()).hexdigest()
        is_repeat = (digest == self._previous_output_digest)
        self._previous_output_digest = digest

        # Check for semantically identical actions
        is_semantic_repeat = False
        current_sig = self._last_action_signature
        if current_sig and current_sig == getattr(self, '_prev_iteration_action_sig', ''):
            is_semantic_repeat = True
        self._prev_iteration_action_sig = current_sig

        if not has_real_work or is_repeat or is_semantic_repeat:
            self._empty_iterations += 1
            self.logger.info(
                "Empty/repetitive iteration detected",
                iteration=ctx.iteration_count,
                has_real_work=has_real_work,
                is_repeat=is_repeat,
                is_semantic_repeat=is_semantic_repeat,
                consecutive_empty=self._empty_iterations,
            )
        else:
            self._empty_iterations = 0
            self._empty_drift_active = False
            # NOTE: Do NOT reset _curiosity_fairy_invoked here.
            # The fairy guard is owned by the action-signature tracking
            # system (_check_repetition_breaker / _record_iteration_actions).
            # Resetting it here based on "real work" (which may still be
            # the same repeated action) would allow the Fairy to fire
            # multiple times for the same loop, wasting context.

        # Never invoke the Fairy here — the repetition breaker is the
        # sole invocation pathway. This prevents dual-triggering.
        return None

    def _reset_drift_counters(self, ctx: AutonomousContext) -> None:
        """Reset anti-drift counters when the agent produces real work."""
        self._empty_iterations = 0
        self._empty_drift_active = False
        self._previous_output_digest = ""
        self._loop_warning_active = False
        self._recent_commands.clear()
        self._consecutive_identical_outputs = 0
        self._last_output_hash = ""
        self._curiosity_fairy_invoked = False

    def _run_thinking(self, ctx: AutonomousContext) -> str:
        """Send current context to the model and get its decision."""
        self.logger.info("Thinking phase started",
                         iteration=ctx.iteration_count)

        model_name  = self.model_runner.model or "?"
        provider_name = self.model_runner.provider or "?"
        self._term_log.model_request(ctx.iteration_count, model_name, provider_name)

        os_info = ctx.metadata.get("os_info", self._get_os_info())
        log_text = self._format_execution_log_for_prompt()

        # Conversation history
        conversation_history_text = ""
        if ctx.conversation_history:
            conversation_history_text = (
                "\n\n" + ctx.conversation_history.format_for_prompt()
            )

        # Only inject saved context on the first iteration after a resume.
        # Re-reading .context/ on every iteration is wrong because the
        # auto-save thread overwrites context_log.txt with live data,
        # causing the model to see stale/duplicate context as "saved".
        if self._is_resuming and ctx.iteration_count <= 1:
            saved_context_block = self._saved_ctx_block
        else:
            saved_context_block = ""

        # Sleep instruction: injected into the prompt to remind the
        # model that it must execute `sleep` on its own when the log
        # grows large.  When the threshold has already been exceeded the
        # message is upgraded to an urgent warning so the model acts on
        # it immediately during the next thinking phase.
        log_line_count = len(ctx.execution_log)
        if log_line_count >= self.NOTIFICATION_THRESHOLD:
            sleep_instruction = (
                f"\n\nSLEEP URGENT: log={log_line_count} >= {self.NOTIFICATION_THRESHOLD}. "
                f"Execute `sleep` NOW as your next command.\n"
            )
        elif log_line_count >= self.NOTIFICATION_THRESHOLD * 4 // 5:
            sleep_instruction = (
                f"\nSLEEP SOON: log={log_line_count}/{self.NOTIFICATION_THRESHOLD}. "
                f"Execute `sleep` when it reaches {self.NOTIFICATION_THRESHOLD}.\n"
            )
        else:
            sleep_instruction = (
                f"\n[Sleep at {self.NOTIFICATION_THRESHOLD} lines. Current: {log_line_count}]\n"
            )

        # Loop detection: check if the agent is repeating commands
        loop_warning = ""
        loop_msg = self._check_loop_detection(ctx)
        if loop_msg:
            loop_warning = loop_msg

        # ── Mode context banner ──
        if ctx.discord_mode:
            mode_banner = "💬 DISCORD MODE"
        elif ctx.telegram_mode:
            mode_banner = "📱 TELEGRAM MODE"
        else:
            mode_banner = "🖥  LOCAL MODE"

        # Messaging-specific instructions (Telegram or Discord mode)
        telegram_section = ""
        if ctx.telegram_mode:
            telegram_section = (
                "\n## TELEGRAM RULES\n"
                "- Reply to user messages with telegram() as first command.\n"
                "- thinking() is invisible to user. telegram() is the ONLY way to reach them.\n"
                "- Send progress updates every 5-10 iterations.\n"
            )
        discord_section = ""
        if ctx.discord_mode:
            discord_section = (
                "\n## DISCORD RULES\n"
                "- Reply to user messages with discord() as first command.\n"
                "- thinking() is invisible to user. discord() is the ONLY way to reach them.\n"
                "- Send progress updates every 5-10 iterations.\n"
            )

        # Self-directed mode hint
        self_directed_section = ""
        if not ctx.user_prompt or ctx.user_prompt == "Self-directed: observe, explore, and act.":
            self_directed_section = (
                "\n## SELF-DIRECTED MODE\n"
                "No instruction given. Act autonomously:\n"
                "  - EXPLORE: filesystem, git status, recent files\n"
                "  - IMPROVE: find TODOs, fix bugs, improve code\n"
                "  - MONITOR: disk, memory, processes\n"
                "On EVERY iteration output at least one command. Never idle.\n"
            )

        # Resume section
        resume_section = ""
        if self._saved_ctx_block and ctx.iteration_count <= 1:
            resume_section = (
                "\n## RESUMING FROM PREVIOUS SESSION\n"
                "Resume work immediately from where you left off.\n"
                f"{self._saved_ctx_block}\n"
            )

        # ── Build compact user prompt ──
        # Behavioral rules are in the system prompt from _build_system_instruction().
        # User prompt = mode + task + log only.
        prompt = (
            f"{mode_banner}\n\n"
            f"## YOUR TASK\n"
            f"{ctx.current_goal or ctx.user_prompt or '(self-directed — explore and do useful work)'}\n\n"
            f"{telegram_section}"
            f"{discord_section}"
            f"{self_directed_section}"
            f"{resume_section}"
            f"## EXECUTION LOG (your memory)\n"
            f"↓↓↓ User messages marked with >>> ↓↓↓\n"
            f"{log_text}\n"
            f"{conversation_history_text}"
            f"{saved_context_block}"
            f"{sleep_instruction}"
            f"{loop_warning}"
        )

        # Build the system instruction with mode-specific rules so they
        # appear in the SYSTEM prompt (highest LLM priority) rather than
        # only in the user prompt.
        system_instruction = self._build_system_instruction(ctx)

        request = ModelRequest(
            task_type=TaskType.AUTONOMOUS_LOOP,
            prompt=prompt,
            context={
                "goal": ctx.current_goal,
                "os_info": os_info,
                "log_text": log_text,
                "conversation_history": conversation_history_text,
                "telegram_mode": ctx.telegram_mode,
            },
            max_tokens=4096,  # Enough room for multi-command responses
            temperature=0.6,  # Balanced: diverse actions while staying focused
            system_instruction=system_instruction,
        )

        response = self.model_runner.run_model(request)

        out_len = len(response.content) if response.content else 0
        latency = getattr(response, "latency", 0) or 0
        self._term_log.model_response(ctx.iteration_count, out_len, latency)

        self.logger.info("Thinking phase completed",
                         output_length=out_len)
        return response.content

    # ------------------------------------------------------------------ #
    #  System prompt builder                                             #
    # ------------------------------------------------------------------ #

    def _build_system_instruction(self, ctx: AutonomousContext) -> str:
        """Build the system instruction — behavioral rules sent as the SYSTEM prompt.

        This combines the base behavioral rules from ModelRunner with
        mode-specific rules (Telegram/Discord) so they appear in the
        highest-priority system message.
        """
        # Start with the base behavioral rules
        base = (
            "# Clio Agent 1 — Command-Only Autonomous Agent\n\n"
            "## 1. CRITICAL: YOUR OUTPUT FORMAT\n"
            "Your entire response MUST consist ONLY of valid command invocations.\n"
            "Every line you output must parse as one of:\n"
            "  command(<shell>), thinking(<text>), telegram(<text>), sleep,\n"
            "  parallel_begin/parallel_end, or a direct tool call.\n\n"
            "ABSOLUTELY FORBIDDEN — if any line in your response matches these,\n"
            "YOUR RESPONSE IS INVALID AND WILL BE REJECTED:\n"
            "  ✗ Free natural-language text outside command()/telegram()/thinking()\n"
            "  ✗ Sentences like \"Okay, I'll start coding\" or \"Let me work on that\"\n"
            "  ✗ Greetings, acknowledgments, or conversational filler\n"
            "  ✗ Explanations or descriptions of what you're about to do\n"
            "  ✗ Summaries or progress reports in plain text\n"
            "  ✗ Questions directed at the user (unless inside telegram())\n"
            "  ✗ ANY natural language that is not wrapped in a command\n\n"
            "RULE: If you want to tell the user something, put it in telegram().\n"
            "RULE: If you need internal reasoning, put it in thinking().\n"
            "RULE: If you need to act, use command() or a direct tool call.\n"
            "RULE: EVERYTHING ELSE IS FORBIDDEN.\n\n"
            "## 2. COMMAND REFERENCE\n"
            "Wrapped:\n"
            "  command(<shell cmd>)    — Execute a shell command\n"
            "  thinking(<text>)        — Internal note (USER CANNOT SEE THIS)\n"
            "  telegram(<message>)    — ONLY way to send a message to the user\n"
            "  sleep                  — Compress context and restart\n"
            "  parallel_begin/end     — Run multiple commands concurrently\n\n"
            "Direct tool calls (faster than command() for file ops):\n"
            "  read(path=\"/path/to/file\")\n"
            "  write(path=\"/path\", content=\"text\")\n"
            "  edit(path=\"/path\", old_string=\"orig\", new_string=\"repl\")\n"
            "  glob(pattern=\"**/*.py\")\n"
            "  grep(pattern=\"regex\", path=\".\")\n"
            "  bash(command=\"any shell cmd\")\n\n"
            "## 3. BEHAVIORAL RULES\n"
            "  a. ACT IMMEDIATELY — never output only thinking(), never an empty response.\n"
            "  b. PARALLELIZE — always batch independent calls with parallel_begin/end.\n"
            "  c. NEVER CHAT — zero natural language outside wrapped commands.\n"
            "  d. NEVER GIVE UP — on failure, try a different approach.\n"
            "  e. OVER-COMMUNICATE — when in doubt, use telegram(). Silence > verbose plans.\n"
            "  f. thinking() is a BLACK HOLE — the user CANNOT see it.\n"
            "  g. Execute sleep BEFORE hitting context limits — don't wait for the engine.\n\n"
            "## 4. ANTI-REPETITION RULES\n"
            "The engine monitors your action patterns. If you repeat the same action\n"
            "signature 3 times consecutively, the Curiosity Fairy will be invoked.\n"
            "At 6 repeats, the Loop Breaker activates. At 8 repeats, forced sleep triggers.\n"
            "AVOID this by following these rules:\n"
            "  a. NEVER run the same command with the same arguments 3+ times in a row.\n"
            "  b. VARY YOUR ACTIONS — alternate between reading files, running shell\n"
            "     commands, searching code, and exploring directories.\n"
            "  c. DON'T RE-READ THE SAME FILE without a new reason.\n"
            "  d. DON'T RE-RUN THE SAME SHELL COMMAND expecting different results.\n"
            "  e. MINIMIZE thinking() — use at most 1 short line or omit it.\n"
            "  f. If you catch yourself about to repeat what you just did, STOP\n"
            "     and choose a completely different action or execute sleep.\n"
        )

        # Mode-specific rules in the SYSTEM prompt (highest priority)
        if ctx.telegram_mode:
            base += (
                "\n## 5. TELEGRAM MODE (ACTIVE)\n"
                "- You are in TELEGRAM MODE. telegram() is the ONLY way to reach the user.\n"
                "- Reply to user messages immediately with telegram() as your FIRST command.\n"
                "- Send progress updates every 5-10 iterations.\n"
                "- thinking() is INVISIBLE to the user. Never put user-messages there.\n"
                "- Over-communication > silence when user is waiting.\n"
            )
        if ctx.discord_mode:
            base += (
                "\n## 5. DISCORD MODE (ACTIVE)\n"
                "- You are in DISCORD MODE. discord() is the ONLY way to reach the user.\n"
                "- Reply to user messages immediately with discord() as your FIRST command.\n"
                "- Send progress updates every 5-10 iterations.\n"
                "- thinking() is INVISIBLE to the user. Never put user-messages there.\n"
                "- Over-communication > silence when user is waiting.\n"
            )

        return base

    # ------------------------------------------------------------------ #
    #  Command parsing                                                   #
    # ------------------------------------------------------------------ #

    # Regexes for the recognised commands
    _CMD_COMMAND = re.compile(
        r'^command\((.*?)\)\s*$', re.DOTALL
    )
    _CMD_THINKING = re.compile(
        r'^thinking\((.*?)\)\s*$', re.DOTALL
    )
    _CMD_TELEGRAM = re.compile(
        r'^telegram\((.*?)\)\s*$', re.DOTALL
    )
    _CMD_DISCORD = re.compile(
        r'^discord\((.*?)\)\s*$', re.DOTALL
    )
    _CMD_TELEGRAM_LOG = re.compile(
        r'^Telegram_log\((\d+)\)\s*$', re.DOTALL
    )
    _CMD_SLEEP = re.compile(
        r'^sleep\s*$'
    )
    _CMD_EXIT = re.compile(
        r'^exit\s*$'
    )
    _CMD_CURIOSITY_FAIRY = re.compile(
        r'^curiosity_fairy\s*$'
    )
    _CMD_PARALLEL_BEGIN = re.compile(
        r'^parallel_begin\s*$'
    )
    _CMD_PARALLEL_END = re.compile(
        r'^parallel_end\s*$'
    )

    # ── Direct tool invocation regex ───────────────────────────────────
    # Matches lines like:  read(path="/tmp/file.txt")
    #                      write(path="/tmp/out.txt", content="hello")
    #                      grep(pattern="TODO", path="src/")
    #                      glob(pattern="**/*.py")
    #                      edit(path="/tmp/f.txt", old="foo", new="bar")
    #                      bash(command="ls -la")
    _CMD_TOOL_CALL = re.compile(
        r'^(?P<tool>[a-zA-Z_][a-zA-Z0-9_]*)\((?P<args>.*)\)\s*$', re.DOTALL
    )

    # Known tool names that can be invoked directly (lowercase).
    # Any matching line is converted to ("tool_call", json_dict).
    _DIRECT_TOOL_NAMES = frozenset({
        "read", "write", "edit", "bash", "glob", "grep",
        "todo", "memo", "subagent", "subagent_result", "subagent_list", "subagent_kill",
    })

    # ── Bullet / numbered list prefix stripper ──────────────────────────
    # Lines like "- command(...)" or "1. command(...)" or "* command(...)"
    # have the prefix stripped before matching.
    _CMD_LIST_PREFIX = re.compile(
        r'^(?:[-*•]|\d+[.)])\s+'
    )

    def _parse_model_commands(self, text: str) -> List[Tuple[str, str]]:
        """
        Parse the model's output and extract recognised commands.

        Supports multiple invocation patterns:

        Pattern 1 — Code block wrapped:
          ```
          command(ls -la)
          thinking(need to check)
          ```

        Pattern 2 — Bare commands (no code block):
          command(ls -la)
          thinking(need to check)

        Pattern 3 — Parallel block:
          parallel_begin
          command(ls /tmp)
          command(cat /etc/hostname)
          parallel_end

        Pattern 4 — Direct tool calls:
          read(path="/tmp/file.txt")
          glob(pattern="**/*.py")

        Pattern 5 — Bullet / numbered list prefixed:
          - command(ls -la)
          1. read(path="/tmp/file.txt")
          * glob(pattern="**/*.py")

        Parsing strategy:
        1. Extract code blocks (``` ... ```) and parse each.
        2. Also parse the full text (minus code block content) so that
           commands outside code blocks are still captured.
        3. Deduplicate lines via a `seen` set.
        4. parallel_begin ... parallel_end blocks are extracted as a
           single ("parallel", json) command whose argument is a JSON
           array of inner command tuples.
        5. Direct tool calls (read/write/edit/bash/glob/grep/todo/memo)
           are converted to ("tool_call", json_dict) tuples.

        Returns a list of (command_type, argument) tuples.
        """
        import json as _json

        commands: List[Tuple[str, str]] = []
        seen: set = set()

        def _strip_list_prefix(line: str) -> str:
            """Remove bullet / numbered list prefixes."""
            return self._CMD_LIST_PREFIX.sub('', line).strip()

        def _try_parse_lines(source: str) -> None:
            in_parallel = False
            parallel_buf: List[Tuple[str, str]] = []

            for raw_line in source.strip().splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # Try stripping list prefixes for matching purposes
                stripped = _strip_list_prefix(line)
                match_line = stripped if stripped else line

                # Deduplicate on the matched form
                if match_line in seen:
                    continue
                seen.add(match_line)

                # Parallel block markers
                if self._CMD_PARALLEL_BEGIN.match(match_line):
                    in_parallel = True
                    parallel_buf = []
                    continue

                if self._CMD_PARALLEL_END.match(match_line):
                    if in_parallel and parallel_buf:
                        commands.append(
                            ("parallel", _json.dumps(parallel_buf))
                        )
                    in_parallel = False
                    parallel_buf = []
                    continue

                if in_parallel:
                    inner = self._match_single_command(match_line)
                    if inner is not None:
                        parallel_buf.append(inner)
                    continue

                # Top-level non-parallel commands
                if self._CMD_SLEEP.match(match_line):
                    commands.append(("sleep", ""))
                elif self._CMD_EXIT.match(match_line):
                    commands.append(("exit", ""))
                elif self._CMD_CURIOSITY_FAIRY.match(match_line):
                    commands.append(("curiosity_fairy", ""))
                else:
                    matched = self._match_single_command(match_line)
                    if matched is not None:
                        commands.append(matched)

            # Unclosed parallel block — flush remaining commands
            if in_parallel and parallel_buf:
                commands.append(("parallel", _json.dumps(parallel_buf)))

        # 1. Extract code blocks (fenced with triple backticks)
        # Handle: ```lang\n...\n```  and  ```\n...\n```
        code_blocks = re.findall(
            r'```[a-zA-Z]*\n(.*?)```', text, re.DOTALL
        )
        if not code_blocks:
            code_blocks = re.findall(r'```(.*?)```', text, re.DOTALL)

        if code_blocks:
            for block in code_blocks:
                _try_parse_lines(block)
            # 2. Also parse text outside code blocks.
            # Remove entire ```...``` blocks (including fences) to avoid
            # parsing fence lines as commands.
            text_outside = re.sub(r'```[a-zA-Z]*\n.*?```', '', text, flags=re.DOTALL)
            text_outside = re.sub(r'```.*?```', '', text_outside, flags=re.DOTALL)
            _try_parse_lines(text_outside)
        else:
            # No code blocks — parse the full text
            _try_parse_lines(text)

        return commands

    def _match_single_command(self, line: str) -> Optional[Tuple[str, str]]:
        """Try to match a single command line.

        Returns (type, arg) or None.  Supports both the traditional
        command()/thinking()/telegram()/discord() syntax and direct tool
        calls like read(path="...") / glob(pattern="...").
        """
        m = self._CMD_COMMAND.match(line)
        if m:
            return ("command", m.group(1).strip())
        m = self._CMD_THINKING.match(line)
        if m:
            return ("thinking", m.group(1).strip())
        m = self._CMD_TELEGRAM.match(line)
        if m:
            return ("telegram", m.group(1).strip())
        m = self._CMD_DISCORD.match(line)
        if m:
            return ("discord", m.group(1).strip())
        m = self._CMD_TELEGRAM_LOG.match(line)
        if m:
            return ("telegram_log", m.group(1).strip())

        # Direct tool call: tool_name(key=value, ...)
        m = self._CMD_TOOL_CALL.match(line)
        if m:
            tool_name = m.group("tool").lower()
            if tool_name in self._DIRECT_TOOL_NAMES:
                args_str = m.group("args").strip()
                # Parse key=value pairs into a dict
                args_dict = self._parse_tool_args(args_str)
                args_dict["__tool__"] = tool_name
                import json
                return ("tool_call", json.dumps(args_dict))

        return None

    @staticmethod
    def _parse_tool_args(args_str: str) -> Dict[str, str]:
        """
        Parse a `key=value, ...` argument string into a dict.

        Robustly handles:
          - Unquoted values:        key=value
          - Single-quoted values:   key='value'
          - Double-quoted values:   key="value"
          - Escaped quotes:         key="line1\\nline2"
          - Values containing '=':  key=foo=bar
          - Positional values:      bare_value  → arg0, arg1, ...

        For nested or unmatched quotes, falls back to raw tokenization so
        the tool can still attempt execution.
        """
        result: Dict[str, str] = {}
        if not args_str or not args_str.strip():
            return result

        text = args_str.replace("\\n", "\n").replace("\\t", "\t")
        length = len(text)
        i = 0
        positional_idx = 0

        def _read_identifier() -> Optional[str]:
            nonlocal i
            start = i
            while i < length and (text[i].isalnum() or text[i] == "_"):
                i += 1
            if start == i:
                return None
            return text[start:i]

        def _parse_value() -> Optional[str]:
            nonlocal i
            # Skip leading whitespace
            while i < length and text[i].isspace():
                i += 1
            if i >= length:
                return None

            quote = text[i]
            if quote in ('"', "'"):
                i += 1
                raw: List[str] = []
                while i < length:
                    ch = text[i]
                    if ch == "\\" and i + 1 < length:
                        raw.append(text[i + 1])
                        i += 2
                        continue
                    if ch == quote:
                        i += 1
                        return "".join(raw)
                    raw.append(ch)
                    i += 1
                # Unterminated quote — return what we have
                return "".join(raw)

            # Unquoted value: read until next top-level comma.
            # Commas inside brackets/parens are part of the value.
            raw = []
            depth = 0
            while i < length:
                ch = text[i]
                if ch in ('(', '[', '{'):
                    depth += 1
                elif ch in (')', ']', '}'):
                    depth -= 1
                elif ch == ',' and depth <= 0:
                    break
                raw.append(ch)
                i += 1
            return "".join(raw).strip()

        while i < length:
            while i < length and text[i].isspace():
                i += 1
            if i >= length:
                break

            ident = _read_identifier()
            if ident is None:
                # Could be a positional argument or stray punctuation
                value = _parse_value()
                if value is not None:
                    result[f"arg{positional_idx}"] = value
                    positional_idx += 1
                if i < length and text[i] == ",":
                    i += 1
                continue

            # Check for key=value
            key = ident
            saved_i = i
            while i < length and text[i].isspace():
                i += 1
            if i < length and text[i] == "=":
                i += 1  # consume '='
                value = _parse_value()
                if value is None:
                    value = ""
                result[key] = value
            else:
                # No '=' after key — treat the identifier + remainder as a positional value
                i = saved_i
                value = _parse_value() or ""
                value = (ident + value).strip()
                if value:
                    result[f"arg{positional_idx}"] = value
                    positional_idx += 1

            if i < length and text[i] == ",":
                i += 1

        return result

    # ------------------------------------------------------------------ #
    #  Command execution                                                 #
    # ------------------------------------------------------------------ #

    def _execute_commands(self, ctx: AutonomousContext,
                          commands: List[Tuple[str, str]]) -> None:
        """Execute a list of parsed commands, including parallel batches.

        Each command is executed in isolation — if one command raises an
        exception, it is logged and the remaining commands in the batch
        still execute. This prevents a single failing command (e.g. the
        Curiosity Fairy's subprocess calls) from blocking the entire batch.
        """
        for cmd_type, arg in commands:
            self._raise_if_cancelled(ctx)

            # Record command for loop detection (skip internal commands)
            if cmd_type in ("command", "tool_call"):
                self._record_command(cmd_type, arg)

            try:
                if cmd_type == "command":
                    self._exec_command(ctx, arg)
                elif cmd_type == "thinking":
                    self._exec_thinking(ctx, arg)
                elif cmd_type == "telegram":
                    self._exec_telegram(ctx, arg)
                elif cmd_type == "discord":
                    self._exec_discord(ctx, arg)
                elif cmd_type == "telegram_log":
                    self._exec_telegram_log(ctx, arg)
                elif cmd_type == "parallel":
                    self._exec_parallel(ctx, arg)
                elif cmd_type == "tool_call":
                    self._exec_tool_call(ctx, arg)
                elif cmd_type == "curiosity_fairy":
                    self._exec_curiosity_fairy(ctx)
                # "sleep" and "exit" are handled at a higher level
            except Exception as _cmd_err:
                # Log and continue — don't let one failing command
                # prevent the rest of the batch from executing.
                self.logger.error(
                    f"Command execution error ({cmd_type}): {_cmd_err}"
                )
                self._append_log(
                    ctx, f"  [execution error] {cmd_type}: {_cmd_err}"
                )

    def _exec_command(self, ctx: AutonomousContext, command_str: str) -> None:
        """Execute a shell command and record the result."""
        self.logger.info("Executing command", command=command_str)
        self._term_log.command(command_str)
        self._append_log(ctx, f"$ {command_str}")

        # Resolve multi-line commands
        lines = [l.strip() for l in command_str.splitlines() if l.strip()]
        if not lines:
            return

        try:
            result = self.terminal_history.execute_commands_batch(
                lines,
                timeout=self.command_timeout,
                cancel_event=ctx.cancel_event,
            )

            stdout = result.get("stdout", "").strip()
            stderr = result.get("stderr", "").strip()
            rc = result.get("return_code", -1)

            self._term_log.command_result(rc, stdout, stderr)

            if stdout:
                self._append_log(ctx, f"  stdout: {stdout[:500]}")
            if stderr:
                self._append_log(ctx, f"  stderr: {stderr[:500]}")
            self._append_log(ctx, f"  exit code: {rc}")

            self.logger.info("Command completed",
                             return_code=rc,
                             success=result.get("success", False))

        except Exception as e:
            error_msg = f"Command execution error: {e}"
            self._term_log.error(error_msg)
            self._append_log(ctx, f"  ERROR: {error_msg}")
            self.logger.error(error_msg)

    def _exec_tool_call(self, ctx: AutonomousContext, arg: str) -> None:
        """
        Execute a direct tool call parsed from the model's output.

        *arg* is a JSON dict with a ``__tool__`` key and tool-specific
        parameters.  The dict is converted into the appropriate ToolInput
        and dispatched through the loop tool registry.
        """
        import json


        try:
            args_dict: Dict[str, str] = json.loads(arg)
        except (json.JSONDecodeError, TypeError) as exc:
            error_msg = f"tool_call: invalid JSON arg: {exc}"
            self._term_log.error(error_msg)
            self._append_log(ctx, f"  ERROR: {error_msg}")
            self.logger.error(error_msg)
            return

        tool_name = args_dict.pop("__tool__", "")
        if not tool_name:
            error_msg = "tool_call: missing __tool__ key"
            self._term_log.error(error_msg)
            self._append_log(ctx, f"  ERROR: {error_msg}")
            self.logger.error(error_msg)
            return

        self.logger.info(f"Tool call: {tool_name}", **args_dict)
        self._term_log.command(f"[{tool_name}] {args_dict}")
        self._append_log(ctx, f"[{tool_name}] {args_dict}")

        # Build the appropriate ToolInput from the args dict
        try:
            tool_input = self._build_tool_input(tool_name, args_dict)
        except Exception as exc:
            error_msg = f"tool_call({tool_name}): input build error: {exc}"
            self._term_log.error(error_msg)
            self._append_log(ctx, f"  ERROR: {error_msg}")
            self.logger.error(error_msg)
            return

        # Execute via the registry
        registry = _get_or_create_loop_tool_registry()
        try:
            result = registry.execute(tool_name, tool_input)
            if result.success:
                snippet = (result.output or "").strip()[:500]
                self._term_log.command_result(0, snippet, "")
                self._append_log(ctx, f"  ok: {snippet}")
            else:
                err = result.error.message if result.error else "unknown error"
                self._term_log.command_result(1, "", err)
                self._append_log(ctx, f"  FAIL: {err}")
        except Exception as exc:
            error_msg = f"tool_call({tool_name}) execution error: {exc}"
            self._term_log.error(error_msg)
            self._append_log(ctx, f"  ERROR: {error_msg}")
            self.logger.error(error_msg)

    def _build_tool_input(self, tool_name: str, args: Dict[str, str]):
        """Convert a kwargs dict into the correct ToolInput for a tool."""
        from ..tools.bash import BashInput
        from ..tools.file_read import FileReadInput
        from ..tools.file_write import FileWriteInput
        from ..tools.file_edit import FileEditInput
        from ..tools.glob import GlobInput
        from ..tools.grep import GrepInput

        if tool_name == "bash":
            return BashInput(
                command=args.get("command", ""),
                timeout=float(args.get("timeout", 60)),
            )
        elif tool_name == "read":
            return FileReadInput(
                file_path=args.get("path", args.get("file_path", args.get("arg0", ""))),
            )
        elif tool_name == "write":
            return FileWriteInput(
                file_path=args.get("path", args.get("file_path", args.get("arg0", ""))),
                content=args.get("content", ""),
            )
        elif tool_name == "edit":
            return FileEditInput(
                file_path=args.get("path", args.get("file_path", args.get("arg0", ""))),
                old_string=args.get("old_string", args.get("old", "")),
                new_string=args.get("new_string", args.get("new", "")),
            )
        elif tool_name == "glob":
            return GlobInput(
                pattern=args.get("pattern", args.get("arg0", "")),
                path=args.get("path", "."),
            )
        elif tool_name == "grep":
            return GrepInput(
                pattern=args.get("pattern", args.get("arg0", "")),
                path=args.get("path", "."),
            )
        elif tool_name in ("subagent", "sub_agent"):
            from ..tools.sub_agent import SubAgentInput
            return SubAgentInput(
                action="spawn",
                agent_type=args.get("agent_type", args.get("type", "")),
                task=args.get("task", args.get("instruction", "")),
                max_iterations=int(args.get("max_iterations", args.get("iterations", "50"))),
                timeout_seconds=int(args.get("timeout_seconds", args.get("timeout", "600"))),
            )
        elif tool_name == "subagent_result":
            from ..tools.sub_agent import SubAgentInput
            return SubAgentInput(
                action="status",
                agent_id=args.get("agent_id", args.get("id", "")),
            )
        elif tool_name == "subagent_kill":
            from ..tools.sub_agent import SubAgentInput
            return SubAgentInput(
                action="kill",
                agent_id=args.get("agent_id", args.get("id", "")),
            )
        elif tool_name == "subagent_list":
            from ..tools.sub_agent import SubAgentInput
            return SubAgentInput(action="list")
        elif tool_name == "sub_agent_types":
            from ..tools.sub_agent import SubAgentInput
            return SubAgentInput(action="list_types")
        else:
            raise ValueError(f"Unsupported direct tool: {tool_name}")

    def _run_self_diagnostic(self, ctx: AutonomousContext) -> None:
        """
        Run a self-diagnostic check to detect and fix issues early.
        Called every 50 iterations to proactively maintain health.
        """
        issues = []

        # Check time since last successful iteration
        time_since_success = time.time() - self._last_successful_iteration
        if time_since_success > 300:  # 5 minutes without success
            issues.append(f"No successful iteration for {time_since_success:.0f}s")

        # Check memory usage
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.percent > 90:
                issues.append(f"High memory usage: {mem.percent:.0f}%")
        except ImportError:
            pass

        # Check disk space
        try:
            import shutil
            disk = shutil.disk_usage(str(Path.cwd()))
            free_gb = disk.free / (1024 ** 3)
            if free_gb < 2.0:
                issues.append(f"Low disk space: {free_gb:.1f}GB free")
                # Attempt cleanup
                from ..utils.resilience_engine import emergency_disk_cleanup
                emergency_disk_cleanup(target_free_gb=2.0)
        except Exception:
            pass

        # Check if we're in degraded mode too long
        if self._degraded_mode and self._consecutive_errors == 0:
            # We haven't had errors but still in degraded mode - try recovery
            self._exit_degraded_mode()

        if issues:
            self.logger.warning(f"Self-diagnostic issues: {', '.join(issues)}")
            self._term_log.error(f"🔍 Diagnostic: {', '.join(issues[:3])}")
        else:
            self.logger.debug("Self-diagnostic passed")

    def _exec_thinking(self, ctx: AutonomousContext, text: str) -> None:
        """Log an internal thought into the execution log."""
        self.logger.info("Thinking", thought=text)
        self._term_log.thinking(text)
        self._append_log(ctx, f"[thought] {text}")

    def add_user_message(self, user_message: str,
                          user_id: Optional[int] = None) -> None:
        """
        Add a user message to the context log strictly as a log entry.

        This does NOT change the agent's current goal or interrupt its work.
        The message is simply recorded so the agent can see it the next time
        it consults the execution log during its thinking phase.  Whether the
        agent reacts to the message is entirely up to the agent itself.

        Uses the current autonomous context if available, otherwise logs
        directly to the terminal history.
        """
        ctx = getattr(self, "_current_context", None)
        tag = f"[user message received]"
        if user_id is not None:
            tag = f"[user message from {user_id} received]"
        entry = f"{tag} {user_message}"
        self.logger.info("User message received (logged passively)",
                         user_message=user_message, user_id=user_id)
        self._term_log.thinking(f"User message (passive): {user_message}")
        if ctx is not None:
            # Remember the first user who sends a message so that
            # telegram() commands know where to send replies.
            if user_id is not None and ctx.telegram_user_id is None:
                ctx.telegram_user_id = user_id
            if user_id is not None:
                self._telegram_boot_user_id = user_id
            # Also update the telegram bot's user tracking
            if user_id is not None and self.telegram_bot:
                self.telegram_bot._last_user_id = user_id
                if not getattr(self.telegram_bot, "_boot_user_id", None):
                    self.telegram_bot._boot_user_id = user_id
            # Also update the discord bot's user tracking
            if user_id is not None and self.discord_bot:
                self.discord_bot._last_user_id = user_id
                if not getattr(self.discord_bot, "_boot_user_id", None):
                    self.discord_bot._boot_user_id = user_id
            self._append_log(ctx, entry)
            if ctx.discord_mode:
                _cmd_hint = "discord()"
            else:
                _cmd_hint = "telegram()"
            self._append_log(
                ctx,
                f"⚠️  Please reply to this message using the {_cmd_hint} command! "
                "Unless there's a very good reason not to. "
                "Whenever possible, send an initial acknowledgement (e.g. "
                "'Got it, working on it…') before carrying out instructions "
                "or thinking them through."
            )
            # Signal the main loop so it wakes up immediately and enters
            # the THINKING phase on the next iteration.
            self._new_message_event.set()

            # A user message is fresh input; clear anti-drift counters so
            # the agent is not immediately re-nudged while responding.
            self._reset_drift_counters(ctx)
            self._term_log.thinking(
                "✅ User message received — continuing active operation."
            )
        else:
            timestamp = time.strftime("%H:%M:%S", time.localtime())
            try:
                from .terminal_history import get_terminal_history, TerminalEntry
                th = get_terminal_history()
                th.terminal_session.entries.append(
                    TerminalEntry(
                        timestamp=time.time(),
                        entry_type=TerminalEntryType.OUTPUT,
                        content=f"[{timestamp}] {entry}",
                        command=None,
                        working_directory=str(th.get_current_working_directory()),
                        return_code=None,
                        duration=None,
                    )
                )
                th.terminal_session.entries.append(
                    TerminalEntry(
                        timestamp=time.time(),
                        entry_type=TerminalEntryType.OUTPUT,
                        content=(
                            f"[{timestamp}] ⚠️  Please reply to this message using the telegram() command! "
                            "Unless there's a very good reason not to. "
                            "Whenever possible, send an initial acknowledgement "
                            "(e.g. 'Got it, working on it…') before carrying "
                            "out instructions or thinking them through."
                        ),
                        command=None,
                        working_directory=str(th.get_current_working_directory()),
                        return_code=None,
                        duration=None,
                    )
                )
            except Exception:
                pass  # best-effort only

    def _exec_telegram(self, ctx: AutonomousContext, content: str) -> None:
        """Send a message via Telegram (only in Telegram mode)."""
        if not ctx.telegram_mode:
            self.logger.warning(
                "telegram() command invoked but not in Telegram mode – ignoring"
            )
            return

        # If Telegram mode requested but we don't have a bot instance yet,
        # attempt to auto-initialize it from the repository `config.yaml`.
        if ctx.telegram_mode and not self.telegram_bot:
            try:
                from ..external_integration.telegram_bot import create_telegram_bot, TELEGRAM_AVAILABLE

                if TELEGRAM_AVAILABLE:
                    cfg_path = Path(__file__).resolve().parents[3] / "config.yaml"
                    if cfg_path.exists():
                        tb = create_telegram_bot(str(cfg_path))
                        if tb:
                            self.telegram_bot = tb
                            if self.telegram_bot.terminal_history is None:
                                self.telegram_bot.terminal_history = self.terminal_history
                            try:
                                # Ensure resilience engine knows about the bot
                                self._resilience.set_telegram_bot(self.telegram_bot)
                            except Exception:
                                pass
            except Exception as exc:
                self.logger.debug(f"Auto-init telegram bot failed: {exc}")

        # Determine the user to reply to.
        target_user = ctx.telegram_user_id
        if target_user is None and self.telegram_bot:
            try:
                target_user = self.telegram_bot._resolve_chat_id(target_user)
            except Exception:
                target_user = ctx.telegram_user_id
        if target_user is None:
            target_user = getattr(self, "_telegram_boot_user_id", None)
        if target_user is None and self.telegram_bot:
            target_user = getattr(self.telegram_bot, "_boot_user_id", None)

        # If still no target user but the bot has allowed users configured,
        # pick the first allowed user as a sensible default so proactive
        # notifications (wake/restart/errors) are delivered.
        if target_user is None and self.telegram_bot:
            allowed = getattr(self.telegram_bot, "allowed_user_ids", None)
            if allowed:
                try:
                    target_user = int(allowed[0])
                except Exception:
                    pass

        if self.telegram_bot and target_user:
            try:
                self.telegram_bot.queue_message(target_user, content)
                # Trigger an immediate flush if the bot loop is running.
                if hasattr(self.telegram_bot, "_trigger_queue_flush"):
                    self.telegram_bot._trigger_queue_flush()
                self._term_log.telegram(content)
                log_entry = "[telegram sent] " + content[:200]
                self._append_log(ctx, log_entry)
            except Exception as e:
                self.logger.warning("Failed to queue Telegram message: " + str(e))
                self._append_log(ctx, "[telegram error] " + str(e))
        else:
            self.logger.warning(
                "telegram() called but no bot configured or no user id available. "
                "Message lost: " + content[:200]
            )
            self._append_log(ctx, "[telegram dropped - no user] " + content[:200])

    def _exec_discord(self, ctx: AutonomousContext, content: str) -> None:
        """Send a message via Discord (only in Discord mode)."""
        if not ctx.discord_mode:
            self.logger.warning(
                "discord() command invoked but not in Discord mode – ignoring"
            )
            return
        target_user = ctx.telegram_user_id
        if target_user is None:
            target_user = getattr(self, "_telegram_boot_user_id", None)
        if target_user is None and self.discord_bot:
            target_user = getattr(self.discord_bot, "_boot_user_id", None)
        if self.discord_bot and target_user:
            try:
                self.discord_bot.queue_message(target_user, content)
                import asyncio as _asyncio
                try:
                    loop = _asyncio.get_running_loop()
                    _asyncio.ensure_future(
                        self.discord_bot.process_message_queue()
                    )
                except RuntimeError:
                    pass
                self._term_log.telegram(content)
                log_entry = "[discord sent] " + content[:200]
                self._append_log(ctx, log_entry)
            except Exception as e:
                self.logger.warning("Failed to queue Discord message: " + str(e))
                self._append_log(ctx, "[discord error] " + str(e))
        else:
            self.logger.warning(
                "discord() called but no bot configured or no user id available. "
                "Message lost: " + content[:200]
            )
            self._append_log(ctx, "[discord dropped - no user] " + content[:200])

    def _exec_telegram_log(self, ctx: AutonomousContext, arg: str) -> None:
        """Display recent conversation history from the active messaging bot."""
        if not ctx.telegram_mode:
            self.logger.warning(
                "Telegram_log() command invoked but not in Telegram mode – ignoring"
            )
            return

        active_bot = self.discord_bot or self.telegram_bot
        if not active_bot:
            self._append_log(ctx, "[Telegram_log error] No messaging bot configured")
            return

        try:
            count = int(arg.strip())
            if count <= 0:
                count = 10
        except (ValueError, AttributeError):
            count = 10

        # Determine the user to get history for
        target_user = ctx.telegram_user_id
        if target_user is None:
            target_user = getattr(self, "_telegram_boot_user_id", None)
        if target_user is None and active_bot:
            target_user = getattr(active_bot, "_boot_user_id", None)

        if target_user is None:
            self._append_log(ctx, "[Telegram_log error] No user ID available")
            return

        history = active_bot.get_conversation_history(target_user)
        messages = history.get_history()

        if not messages:
            log_entry = "[Telegram_log] No conversation history available"
            self._append_log(ctx, log_entry)
            return

        # Get the last 'count' messages
        recent_messages = messages[-count:] if count < len(messages) else messages

        # Format the log output
        output_lines = [f"Telegram Conversation History (last {len(recent_messages)} messages):"]
        output_lines.append("=" * 60)

        for msg in recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # Truncate long messages
            if len(content) > 200:
                content = content[:200] + "..."
            output_lines.append(f"  {role}: {content}")

        output_lines.append("=" * 60)
        log_entry = "\n".join(output_lines)
        self._append_log(ctx, log_entry)
        self.logger.info(f"Telegram_log displayed {len(recent_messages)} messages for user {target_user}")

    def _exec_curiosity_fairy(self, ctx: AutonomousContext) -> None:
        """
        Execute the Curiosity Fairy command (voluntary invocation).

        The model may emit ``curiosity_fairy`` on its own to request a
        creative suggestion when it feels stuck.  This is the same logic
        as the automatic invocation triggered by the repetition breaker.
        """
        self.logger.info("Curiosity Fairy invoked voluntarily by model")
        self._term_log.separator()
        self._term_log.thinking("🧚 Curiosity Fairy invoked — seeking inspiration...")
        suggestion = self._invoke_curiosity_fairy(ctx)
        # Always reset the fairy guard after a voluntary invocation,
        # regardless of whether a suggestion was produced. The model
        # voluntarily asked for help; it should get a fresh start.
        self._curiosity_fairy_invoked = False
        if suggestion:
            fairy_msg = (
                f"[Message from the Curiosity Fairy] "
                f"```\n{suggestion}\n```"
            )
            self._append_log(ctx, fairy_msg)
            self._term_log.thinking(
                f"🧚 Curiosity Fairy suggests: {suggestion[:120]}"
            )
            # Reset identical-output counter so the automatic trigger
            # doesn't fire immediately after a voluntary invocation.
            self._consecutive_identical_outputs = 0
        else:
            self._term_log.error("🧚 Curiosity Fairy returned no suggestion")
            self._append_log(ctx, "[Curiosity Fairy] No suggestion available.")

    def _exec_parallel(self, ctx: AutonomousContext, arg: str) -> None:
        """
        Execute a batch of commands in parallel.

        *arg* is a JSON-encoded list of [command_type, argument] tuples.
        Only ``command`` entries are truly parallelised via the tool registry;
        ``thinking`` / ``telegram`` entries inside the block are executed
        sequentially before / after the parallel batch so that ordering
        side-effects remain deterministic.
        """
        import json

        try:
            raw_commands: List[List[str]] = json.loads(arg)
        except (json.JSONDecodeError, TypeError) as exc:
            error_msg = f"parallel block: invalid JSON arg: {exc}"
            self._term_log.error(error_msg)
            self._append_log(ctx, f"  ERROR: {error_msg}")
            self.logger.error(error_msg)
            return

        # Separate truly-parallel command() calls from ordered calls.
        parallel_tasks: List[ParallelTask] = []
        ordered_pre: List[Tuple[str, str]] = []
        ordered_post: List[Tuple[str, str]] = []

        # Everything before the first command() → pre; everything after last
        # command() → post.
        first_cmd = None
        last_cmd = None
        for i, (ct, ca) in enumerate(raw_commands):
            if ct == "command":
                if first_cmd is None:
                    first_cmd = i
                last_cmd = i

        for i, (ct, ca) in enumerate(raw_commands):
            if ct == "command":
                parallel_tasks.append(ParallelTask(
                    tool_name="bash",
                    input=_bash_input_from_command(ca),
                    label=ca[:80],
                ))
            elif first_cmd is None or i < first_cmd:
                ordered_pre.append((ct, ca))
            elif last_cmd is not None and i > last_cmd:
                ordered_post.append((ct, ca))
            else:
                # Between first and last command() — treat as parallel-safe
                # by also adding as a task (falls back to sequential if unknown)
                ordered_pre.append((ct, ca))

        # Execute ordered_pre sequentially
        for ct, ca in ordered_pre:
            self._raise_if_cancelled(ctx)
            if ct == "thinking":
                self._exec_thinking(ctx, ca)
            elif ct == "telegram":
                self._exec_telegram(ctx, ca)

        # Execute the parallel batch
        if parallel_tasks:
            self.logger.info(
                f"Parallel batch: {len(parallel_tasks)} commands starting"
            )
            self._term_log.parallel_start(len(parallel_tasks))

            registry = _get_or_create_loop_tool_registry()
            try:
                par_result: ParallelResult = registry.execute_parallel(
                    parallel_tasks
                )
                self._term_log.parallel_result(par_result)

                # Log each result into the execution log
                self._append_log(
                    ctx,
                    f"  [parallel batch] {par_result.success_count} ok, "
                    f"{par_result.fail_count} failed, "
                    f"{par_result.total_duration_ms:.0f}ms"
                )
                for task, res in par_result.results:
                    tag = task.label or task.tool_name
                    status = "OK" if res.success else "FAIL"
                    snippet = (res.output or "").strip()[:300]
                    if res.success:
                        self._term_log.command_result(0, snippet, "")
                    else:
                        err = (res.error.message if res.error else "unknown error")
                        self._term_log.command_result(1, "", err)
                    self._append_log(
                        ctx,
                        f"    [{status}] {tag}: {snippet}"
                    )

                self.logger.info(
                    "Parallel batch done",
                    ok=par_result.success_count,
                    fail=par_result.fail_count,
                    ms=par_result.total_duration_ms,
                )
            except Exception as exc:
                error_msg = f"Parallel batch execution error: {exc}"
                self._term_log.error(error_msg)
                self._append_log(ctx, f"  ERROR: {error_msg}")
                self.logger.error(error_msg)

        # Execute ordered_post sequentially
        for ct, ca in ordered_post:
            self._raise_if_cancelled(ctx)
            if ct == "thinking":
                self._exec_thinking(ctx, ca)
            elif ct == "telegram":
                self._exec_telegram(ctx, ca)
            elif ct == "discord":
                self._exec_discord(ctx, ca)

    # ------------------------------------------------------------------ #
    #  Sleep workflow                                                    #
    # ------------------------------------------------------------------ #

    # System prompt used exclusively for the context-compression request.
    _COMPRESS_SYSTEM_PROMPT = (
        "You are a context-compression engine for an autonomous AI agent. "
        "Your summary will be the ONLY context the agent has after restart. "
        "Never omit these categories:\n"
        "  1. WORK IN PROGRESS — What the agent was doing (no completion concept).\n"
        "  2. FILE CHANGES   — Exact file paths created, modified, or deleted.\n"
        "  3. ERRORS         — All error messages and their resolution status.\n"
        "  4. NEXT ACTION    — The single concrete command to run next.\n"
        "  5. ENVIRONMENT    — Installed packages, config changes, env vars.\n"
        "Use bullet points. Be extremely concise but never drop facts. "
        "Output ONLY the structured summary, no preamble, no code blocks."
    )

    def _clear_repetition_state(self) -> None:
        """Clear all repetition-breaker, anti-drift, and Curiosity Fairy state.
        Extracted as a helper so it can be called at the right point in the
        sleep workflow (after compression succeeds, before restart)."""
        self._action_history.clear()
        self._consecutive_same_action = 0
        self._last_action_signature = ""
        self._persistent_loop_patterns.clear()
        self._force_sleep_pending = False
        self._consecutive_identical_outputs = 0
        self._last_output_hash = ""
        self._curiosity_fairy_invoked = False
        self._empty_iterations = 0
        self._empty_drift_active = False
        self._previous_output_digest = ""

    def _handle_sleep(self, ctx: AutonomousContext) -> None:
        """
        Multi-stage sleep workflow:
        1. Collect auxiliary context (git diff, metadata, recent entries)
        2. Ask the LLM to compress the full context with a structured prompt
        3. Save compressed context + logs to the context folder
        4. Clear repetition-breaker state (only after successful save)
        5. Persist runtime env and restart the process immediately

        State is cleared AFTER compression succeeds so that a failure
        in the compression pipeline does not lose loop-detection history.
        """
        ctx.current_phase = LoopPhase.SLEEPING
        self.logger.info("Sleep command received – starting sleep workflow")

        # Determine project root reliably using helper function
        _project_root = _get_project_root()

        # Reset the one-time notification flag so it can fire again after
        # the next wake-up cycle.
        self._sleep_notification_shown = False

        # Remove the sleep reminder from the execution log to prevent
        # duplicate notifications after restart
        if self._last_sleep_reminder is not None:
            ctx.execution_log = [
                entry for entry in ctx.execution_log
                if entry != self._last_sleep_reminder
            ]
            self._last_sleep_reminder = None

        try:
            # 1. Collect auxiliary context
            aux = self._collect_auxiliary_context(ctx)

            # 2. Context compression (multi-level fallback)
            compressed = self._compress_context(ctx, aux)

            # 3. Save to context folder
            self._save_sleep_state(ctx, compressed, aux,
                                   project_root=_project_root)

            # 4. Write resume instruction file for the restarted process
            self._set_sleep_restart_flag(compressed, project_root=_project_root)

            self.logger.info("Sleep workflow complete – notifying user and restarting")

            # 5. Clear repetition state ONLY after successful save.
            # This ensures loop-detection history is preserved if the
            # compression or save steps fail.
            self._clear_repetition_state()

            # 6. Notify via Telegram before restarting so the user knows.
            self._notify_telegram_error(
                ctx,
                "🛰 Agent is sleeping to compress context and restart. "
                "I'll be back shortly!",
            )

            # 7. Restart the process immediately – no return, no pause.
            self._restart_process(ctx)

        except Exception as e:
            self.logger.error(f"Sleep workflow error: {e}")
            ctx.error = f"Sleep workflow error: {e}"
            # Save emergency state before attempting restart so that even if
            # the restart fails, the context is not lost. This prevents the
            # sleep-restart-sleep loop by ensuring the next session can
            # recover from the saved state.
            # Note: repetition state is NOT cleared on failure, preserving
            # loop-detection history for the restarted process.
            try:
                self._save_sleep_state(ctx, f"(compression failed: {e})",
                                       self._collect_auxiliary_context(ctx),
                                       project_root=_project_root)
            except Exception as save_err:
                self.logger.error(f"Failed to save emergency sleep state: {save_err}")

            # Stop auto-save before restart to avoid corrupted writes
            self._stop_auto_save()

            # Attempt best-effort restart
            try:
                self._restart_process(ctx)
            except Exception as restart_err:
                self.logger.critical(
                    f"Sleep restart also failed: {restart_err}. "
                    "Agent state may be inconsistent. "
                    "Emergency state saved to .context/sleep_state.json for recovery."
                )
                # Do NOT re-raise — the process is in an inconsistent state
                # and the user needs to intervene. Exit with a distinctive code.
                sys.exit(127)

    def _handle_exit(self, ctx: AutonomousContext,
                       fast: bool = False,
                       project_root: Optional[Path] = None) -> None:
        """
        Graceful-exit workflow (no restart):
        1. Collect auxiliary context (git diff, metadata, recent entries)
        2. Compress the full context (LLM or heuristic fallback)
        3. Save compressed context + logs to the context folder as exit_state.json
        4. Stop auto-save thread to prevent corrupted writes
        5. Do NOT restart — the caller is responsible for process termination

        This is triggered by the `exit` command or by OS signals
        (SIGINT/SIGTERM) so that context is always preserved on shutdown.

        Args:
            ctx:          The current autonomous context.
            fast:         If True, skip the LLM compression and use a
                          heuristic-only summary.  Used during signal handling
                          where we cannot reliably call the LLM.
            project_root: Optional project root for .context/ path.
        """
        ctx.current_phase = LoopPhase.EXITING
        self.logger.info("Exit command received – starting exit-save workflow")

        self._sleep_notification_shown = False

        try:
            # 1. Collect auxiliary context
            aux = self._collect_auxiliary_context(ctx)

            # 2. Context compression
            if fast:
                # Signal-handler fast path: skip LLM entirely, use heuristic
                compressed = self._heuristic_compress(ctx, aux)
            else:
                compressed = self._compress_context(ctx, aux)

            # 3. Save to context folder (different file from sleep)
            self._save_exit_state(ctx, compressed, aux, project_root=project_root)

            self.logger.info("Exit-save workflow complete – state persisted, exiting now")

        except Exception as e:
            self.logger.error(f"Exit-save workflow error: {e}")
            ctx.error = f"Exit-save workflow error: {e}"
        finally:
            # Always stop the auto-save thread before the process exits
            # to prevent corrupted writes
            self._stop_auto_save()

    @staticmethod
    def _heuristic_compress(ctx: AutonomousContext,
                            aux: Dict[str, str]) -> str:
        """Fast, LLM-free context compression for signal handling."""
        sections = [
            f"## WORK IN PROGRESS: {ctx.current_goal}",
            f"## ITERATIONS: {ctx.iteration_count}",
            "## GIT DIFF",
            aux.get("git_diff", "(none)"),
            "## METADATA",
            aux.get("metadata", "(none)"),
            "## ERRORS",
            aux.get("errors", "(none)"),
            "## LOG TAIL (last 100 lines)",
            aux.get("log_tail", "(empty)"),
        ]
        return "\n".join(sections)

    def _save_exit_state(self, ctx: AutonomousContext,
                         compressed: str,
                         aux: Dict[str, str],
                         project_root: Optional[Path] = None) -> None:
        """Persist the current state for an exit (non-restarting) shutdown."""
        if project_root is not None:
            context_dir = project_root / ".context"
        else:
            context_dir = Path(".context")
        context_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "status": "Exited gracefully — context saved for next session",
            "goal": ctx.current_goal,
            "user_prompt": ctx.user_prompt,
            "iteration_count": ctx.iteration_count,
            "compressed_context": compressed,
            "timestamp": time.time(),
            "telegram_mode": ctx.telegram_mode,
            "telegram_user_id": ctx.telegram_user_id,
            "restart_provider": ctx.metadata.get("restart_provider", ""),
            "restart_model": ctx.metadata.get("restart_model", ""),
            "auxiliary": {
                "git_diff": aux.get("git_diff", ""),
                "metadata": aux.get("metadata", ""),
                "errors": aux.get("errors", ""),
                "log_tail": aux.get("log_tail", ""),
            },
        }

        state_file = context_dir / "exit_state.json"
        try:
            import json
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            self.logger.info(f"Exit state saved to {state_file}")
        except Exception as e:
            self.logger.error(f"Failed to exit state: {e}")

        # Also write context_log.txt so the next session finds a readable summary
        try:
            log_file = context_dir / "context_log.txt"
            header = (
                f"Status: Exited gracefully — context saved for next session\n"
                f"Goal: {ctx.current_goal}\n"
                f"Iterations: {ctx.iteration_count}\n"
                f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n"
                f"{'=' * 60}\n"
            )
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(header)
                f.write(compressed)
                f.write("\n")
            self.logger.info(f"Context log saved to {log_file}")
        except Exception as e:
            self.logger.error(f"Failed to save context log: {e}")

    @staticmethod
    def _collect_auxiliary_context(ctx: AutonomousContext,
                                   execution_log: Optional[List[str]] = None) -> Dict[str, str]:
        """
        Gather extra context that is not inside ctx.execution_log but is
        essential for a lossless restart: git diff statistics, metadata,
        tail of the execution log, and all error lines.

        Args:
            ctx: The current autonomous context.
            execution_log: Optional pre-snapshot of the execution log.
                If None, reads ctx.execution_log directly.
        """
        aux: Dict[str, str] = {}
        log = execution_log if execution_log is not None else ctx.execution_log

        # --- Git diff summary ------------------------------------------------
        git_diff = ""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "diff", "--stat"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                git_diff = result.stdout.strip()[:2000]
        except Exception:
            pass
        aux["git_diff"] = git_diff or "(not a git repository or no changes)"

        # --- Metadata ----------------------------------------------------------
        meta_parts = []
        for k, v in ctx.metadata.items():
            meta_parts.append(f"{k}: {v}")
        aux["metadata"] = "\n".join(meta_parts) if meta_parts else "(none)"

        # --- Tail of execution log (last 100 non-empty lines) ----------------
        tail_lines: List[str] = []
        for line in reversed(log):
            if line.strip():
                tail_lines.append(line)
            if len(tail_lines) >= 100:
                break
        tail_lines.reverse()
        aux["log_tail"] = "\n".join(tail_lines) if tail_lines else "(empty)"

        # --- All error lines ---------------------------------------------------
        error_lines = [
            line for line in log
            if any(marker in line.lower()
                   for marker in ("error", "exception", "failed", "traceback"))
        ]
        aux["errors"] = "\n".join(error_lines) if error_lines else "(none)"

        return aux

    def _compress_context(self, ctx: AutonomousContext,
                          aux: Dict[str, str]) -> str:
        """
        Multi-level context compression:
          Level 1 — LLM compression with structured prompt + aux data (truncated log).
          Level 2 — LLM fails → heuristic: tail + errors + metadata + git diff.
          Level 3 — Everything fails → last 50 lines of the raw log.
        """
        # Use only the truncated log tail — never send the full execution_log
        # to the LLM. The whole point of sleep is to compress context, so
        # sending the uncompressed log would defeat the purpose.
        log_text = aux["log_tail"]

        prompt = (
            "## Work in Progress\n"
            f"{ctx.current_goal}\n\n"
            f"## Iterations: {ctx.iteration_count}\n\n"
            "## Git Diff Summary\n"
            f"{aux['git_diff']}\n\n"
            "## OS / Environment Metadata\n"
            f"{aux['metadata']}\n\n"
            "## All Errors Encountered\n"
            f"{aux['errors']}\n\n"
            "## Execution Log (last 100 lines)\n"
            f"{log_text}"
        )

        # ---- Level 1: LLM compression with structured prompt ----
        try:
            # Pass the compression system instruction explicitly so the
            # ModelRunner uses it instead of the default behavioral rules.
            compression_prompt = self._COMPRESS_SYSTEM_PROMPT + "\n\n" + prompt
            request = ModelRequest(
                task_type=TaskType.AUTONOMOUS_LOOP,
                prompt=compression_prompt,
                max_tokens=4000,
                temperature=0.1,
                system_instruction=self._COMPRESS_SYSTEM_PROMPT,
            )
            response = self.model_runner.run_model(request)
            if response.success and response.content.strip():
                self.logger.info("Level-1 compression: LLM succeeded",
                                 output_len=len(response.content))
                return response.content.strip()
        except Exception as e:
            self.logger.warning(f"Level-1 compression (LLM) failed: {e}")

        # ---- Level 2: Heuristic structured fallback ----
        try:
            self.logger.info("Level-2 compression: using heuristic fallback")
            sections = [
                f"## WORK IN PROGRESS: {ctx.current_goal}",
                f"## ITERATIONS: {ctx.iteration_count}",
                "## GIT DIFF",
                aux["git_diff"],
                "## METADATA",
                aux["metadata"],
                "## ERRORS",
                aux["errors"],
                "## LOG TAIL (last 100 lines)",
                aux["log_tail"],
            ]
            return "\n".join(sections)
        except Exception as e:
            self.logger.warning(f"Level-2 compression (heuristic) failed: {e}")

        # ---- Level 3: Raw tail as last resort ----
        self.logger.info("Level-3 compression: raw log tail")
        return log_text

    def _save_sleep_state(self, ctx: AutonomousContext,
                          compressed: str,
                          aux: Dict[str, str],
                          project_root: Optional[Path] = None) -> None:
        """Persist the current execution state and auxiliary data."""
        if project_root is not None:
            context_dir = project_root / ".context"
        else:
            context_dir = Path(".context")
        context_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "status": "Restarting due to execution of the sleep command",
            "goal": ctx.current_goal,
            "user_prompt": ctx.user_prompt,
            "iteration_count": ctx.iteration_count,
            "compressed_context": compressed,
            "timestamp": time.time(),
            "telegram_mode": ctx.telegram_mode,
            "discord_mode": ctx.discord_mode,
            "telegram_user_id": ctx.telegram_user_id,
            "restart_provider": ctx.metadata.get("restart_provider", ""),
            "restart_model": ctx.metadata.get("restart_model", ""),
            "auxiliary": {
                "git_diff": aux.get("git_diff", ""),
                "metadata": aux.get("metadata", ""),
                "errors": aux.get("errors", ""),
                "log_tail": aux.get("log_tail", ""),
            },
        }

        state_file = context_dir / "sleep_state.json"
        if _atomic_write_json(state_file, state):
            self.logger.info(f"Sleep state saved to {state_file}")
        else:
            self.logger.error(f"Failed to save sleep state to {state_file}")

        # Write a dedicated context_log.txt with the compressed context so
        # that the restarted process (and the user) can always find a
        # plain-text summary without parsing JSON.
        log_file = context_dir / "context_log.txt"
        header = (
            f"Status: Restarting due to execution of the sleep command\n"
            f"Goal: {ctx.current_goal}\n"
            f"Iterations: {ctx.iteration_count}\n"
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n"
            f"{'=' * 60}\n"
        )
        log_content = header + compressed + "\n"
        if _atomic_write_text(log_file, log_content):
            self.logger.info(f"Context log saved to {log_file}")
        else:
            self.logger.error(f"Failed to save context log to {log_file}")

    @staticmethod
    def _git_pull() -> None:
        """Best-effort git pull to update the program."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "pull"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                _get_logger = get_logger("autonomous_loop_engine")
                _get_logger.info("Git pull succeeded")
            else:
                _get_logger = get_logger("autonomous_loop_engine")
                _get_logger.warning(f"Git pull returned non-zero: {result.stderr[:200]}")
        except Exception as e:
            get_logger("autonomous_loop_engine").warning(f"Git pull failed: {e}")

    @staticmethod
    def _set_sleep_restart_flag(compressed: str,
                                project_root: Optional[Path] = None) -> None:
        """
        Write a flag that tells the restarted process to rebuild the venv
        and restore context.
        """
        if project_root is not None:
            flag_file = project_root / ".context" / "rebuild_required"
        else:
            flag_file = Path(".context") / "rebuild_required"
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.write_text("1", encoding="utf-8")

    def _restart_process(self, ctx: "AutonomousContext") -> None:
        """
        Immediately restart the current process using os.execv so the
        agent resumes from the top of main() without any pause.

        Note: This is an instance method (not static) so it can access self.logger.
        """
        import json as _json

        provider = ctx.metadata.get("restart_provider", "")
        model = ctx.metadata.get("restart_model", "")
        telegram_mode = ctx.metadata.get("restart_telegram_mode", False)
        discord_mode = ctx.metadata.get("restart_discord_mode", False)
        telegram_user_id = ctx.metadata.get("restart_telegram_user_id", None)

        # Determine the mode string for the restarted process
        if discord_mode:
            mode = "discord"
        elif telegram_mode:
            mode = "telegram"
        else:
            mode = "normal"

        # Store provider/model/mode in env for the restarted process
        os.environ["CLIO_RESTART_MODE"] = mode
        if provider:
            os.environ["CLIO_RESTART_PROVIDER"] = provider
        if model:
            os.environ["CLIO_RESTART_MODEL"] = model

        # Determine project root reliably
        _project_root = _get_project_root()

        # Also write them to the sleep_state so main() can find them
        state_file = _project_root / ".context" / "sleep_state.json"
        if state_file.exists():
            try:
                data = _json.loads(state_file.read_text(encoding="utf-8"))
                data["restart_provider"] = provider
                data["restart_model"] = model
                data["restart_mode"] = mode
                data["restart_telegram_mode"] = telegram_mode
                data["restart_discord_mode"] = discord_mode
                data["restart_telegram_user_id"] = telegram_user_id
                _atomic_write_json(state_file, data)
            except Exception as e:
                self.logger.warning(f"Failed to update restart info in state file: {e}")

        # Rebuild argv: run.py with USER_RESTART_FLAG and --no-prompt
        run_py = _project_root / "run.py"
        if not run_py.exists():
            run_py = Path("run.py")

        new_args = [
            sys.executable,
            str(run_py),
            "--__user_restarted__",
            "--no-prompt",
        ]

        # best-effort git pull before restart
        try:
            AutonomousLoopEngine._git_pull()
        except Exception as git_err:
            self.logger.warning(f"Git pull failed (non-fatal): {git_err}")

        # Stop the auto-save thread before execv to avoid corrupted writes
        self._stop_auto_save()

        # Attempt process restart.
        # On Unix, os.execv replaces the current process (same PID).
        # On Windows, os.execv may not work reliably, so we use subprocess
        # as a fallback.
        try:
            if sys.platform == "win32":
                # Windows: spawn a new process and exit the parent
                import subprocess
                subprocess.Popen(new_args, cwd=str(_project_root))
                self.logger.info("Windows restart: spawned child process, exiting parent")
                sys.exit(0)
            else:
                # Unix: os.execv replaces the current process (same PID)
                os.execv(sys.executable, new_args)
        except OSError as exec_err:
            # os.execv only returns on failure
            self.logger.critical(
                f"Process restart failed: {exec_err}. "
                f"Executable: {sys.executable}, Args: {new_args}"
            )
            # Save emergency state before giving up, so an external
            # supervisor can recover context.
            try:
                _emergency_state = {
                    "status": f"EMERGENCY: restart failed on {sys.platform}",
                    "error": str(exec_err),
                    "goal": ctx.current_goal,
                    "user_prompt": ctx.user_prompt,
                    "iteration_count": ctx.iteration_count,
                    "timestamp": time.time(),
                    "restart_provider": provider,
                    "restart_model": model,
                    "restart_mode": mode,
                }
                _emergency_file = _project_root / ".context" / "exit_state.json"
                _atomic_write_json(_emergency_file, _emergency_state)
                self.logger.info("Emergency state saved before exit")
            except Exception as _save_err:
                self.logger.critical(
                    f"Failed to save emergency state: {_save_err}"
                )
            # Exit with a distinctive code that external supervisors can detect
            sys.exit(127)

    @staticmethod
    def check_and_handle_sleep_restart(
        project_root: Optional[Path] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        On process startup, check whether a sleep restart is pending.
        If so, rebuild the venv and return the saved state.

        Args:
            project_root: Optional explicit project root.  When None,
                          falls back to CWD-relative .context/.

        Returns the saved state dict, or None if no restart is pending.
        """
        if project_root is not None:
            flag_file = project_root / ".context" / "rebuild_required"
            state_file = project_root / ".context" / "sleep_state.json"
        else:
            flag_file = Path(".context") / "rebuild_required"
            state_file = Path(".context") / "sleep_state.json"

        if not flag_file.exists() or not state_file.exists():
            return None

        logger = get_logger("autonomous_loop_engine")
        logger.info("Sleep restart detected – rebuilding environment")

        try:
            import json
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)

            # Remove only the rebuild flag so we don't loop forever.
            # Keep state_file on disk — run_autonomous_boot will consume it
            # via get_context_for_prompt() and then clear it.
            flag_file.unlink(missing_ok=True)

            # Refresh context_log.txt from the restored state so the file
            # always mirrors the latest compressed context on disk.
            try:
                context_dir = state_file.parent
                log_file = context_dir / "context_log.txt"
                compressed = state.get("compressed_context", "")
                saved_time = state.get("timestamp", 0)
                try:
                    ts_str = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(float(saved_time)),
                    )
                except (ValueError, TypeError, OSError) as ts_err:
                    logger.warning(f"Failed to format timestamp: {ts_err}")
                    ts_str = str(saved_time)
                header = (
                    f"Status: {state.get('status', 'Restored from sleep')}\n"
                    f"Goal: {state.get('goal', '')}\n"
                    f"Iterations: {state.get('iteration_count', 0)}\n"
                    f"Timestamp: {ts_str}\n"
                    f"{'=' * 60}\n"
                )
                log_content = header + compressed + "\n"
                if not _atomic_write_text(log_file, log_content):
                    logger.warning("Failed to refresh context_log.txt")
            except Exception as log_err:
                logger.warning(f"Failed to update context log: {log_err}")

            logger.info("Sleep restart state restored",
                        goal=state.get("goal", ""))
            return state

        except Exception as e:
            logger.error(f"Failed to restore sleep state: {e}")
            flag_file.unlink(missing_ok=True)
            return None

    # ------------------------------------------------------------------ #
    #  Helpers                                                           #
    # ------------------------------------------------------------------ #

    def _check_and_handle_resources(self, ctx: AutonomousContext) -> None:
        """
        Periodic resource health check.  If disk or memory is critically low,
        attempt self-healing (disk cleanup) or force a sleep to recover.
        Called every 100 iterations.
        """
        try:
            report = check_system_resources()
        except Exception as e:
            self.logger.warning(f"Resource check failed: {e}")
            return

        if report["healthy"]:
            return

        for issue in report["issues"]:
            self.logger.warning(f"Resource issue: {issue}")
            self._term_log.error(f"⚠ {issue}")

        # Try disk cleanup if disk is low
        disk = report["disk"]
        if disk["free_gb"] < 2.0:
            self.logger.warning("Disk low — attempting emergency cleanup")
            self._term_log.error("🧹 Disk low — running emergency cleanup ...")
            try:
                ok, msg = emergency_disk_cleanup(target_free_gb=2.0)
                self.logger.info(f"Emergency cleanup: {msg}")
                self._term_log.error(f"🧹 Cleanup: {msg}")
                self._notify_telegram_error(ctx, f"🧹 Disk cleanup: {msg}")
            except Exception as e:
                self.logger.error(f"Emergency cleanup failed: {e}")

        # If still critical after cleanup, force sleep
        try:
            after = check_system_resources()
            if not after["healthy"]:
                critical = any("CRITICAL" in i for i in after["issues"])
                if critical:
                    self.logger.warning("Resources still critical after cleanup — forcing sleep")
                    self._term_log.error("🛏 Resources critical — forcing sleep to recover ...")
                    self._notify_telegram_error(
                        ctx,
                        "🛏 Resources (disk/memory) are critically low. "
                        "Forcing sleep to recover. Will restart automatically."
                    )
                    # Trigger the sleep workflow
                    self._handle_sleep(ctx)
                    # _handle_sleep does os.execv, so we never reach here
        except Exception:
            pass

    # Maximum number of recent log lines injected into the model prompt.
    # Keeps the prompt from growing without bound across iterations.
    MAX_LOG_LINES_IN_PROMPT = 80

    def _format_execution_log_for_prompt(self, max_lines: int = None) -> str:
        """Format the execution log for injection into the model prompt.

        Uses ctx.execution_log as the sole source to avoid duplication
        (terminal history entries are already reflected in execution_log
        via _append_log calls after each command).

        Only the most recent *max_lines* entries are included to keep the
        prompt within the model's context window.

        User messages received via Telegram are highlighted with >>> markers
        so the model can easily spot them.
        """
        if max_lines is None:
            max_lines = self.MAX_LOG_LINES_IN_PROMPT
        try:
            ctx = getattr(self, "_current_context", None)
            if ctx is None:
                return "(no context)"

            # Use execution_log as the sole source — it already contains
            # command results, user messages, thoughts, and telegram markers.
            # Reading terminal_history separately would duplicate entries.
            all_lines = list(ctx.execution_log)

            if not all_lines:
                return "(no terminal history)"

            # Post-process: highlight user-message lines
            highlighted_lines = []
            for line in all_lines:
                if "[user message" in line.lower():
                    highlighted_lines.append(">>> " + line + " <<<")
                else:
                    highlighted_lines.append(line)

            # Keep only the most recent max_lines
            if len(highlighted_lines) > max_lines:
                omitted = len(highlighted_lines) - max_lines
                header = (
                    f"... ({omitted} older log entries omitted \u2014 "
                    f"showing last {max_lines} of {len(highlighted_lines)} lines) ..."
                )
                highlighted_lines = [header] + highlighted_lines[-max_lines:]

            return "\n".join(highlighted_lines)
        except Exception:
            return "(terminal log unavailable)"

    def _maybe_emit_notification(self, ctx: AutonomousContext) -> None:
        """
        Inject a ONE-TIME 'you should rest' message into the execution
        log (the AI agent's context) when it exceeds the threshold.

        The message is written ONLY to ctx.execution_log so that it
        appears in the model prompt on the next thinking phase.  No
        user-facing notification is sent.

        The flag is cleared by sleep so it can fire again in the next
        wake-cycle.
        """
        if self._sleep_notification_shown:
            return
        if len(ctx.execution_log) < self.NOTIFICATION_THRESHOLD:
            return

        self._sleep_notification_shown = True

        reminder = (
            "[SYSTEM] 🛏 SLEEP COMMAND MUST BE EXECUTED NOW. "
            "The context log has reached "
            f"{len(ctx.execution_log)} lines (threshold: {self.NOTIFICATION_THRESHOLD}). "
            "YOU must write the `sleep` command as your next action. "
            "Do NOT ask the user. Do NOT delay. Execute sleep immediately to "
            "compress context, rebuild, and restart."
        )

        # Inject into the agent's execution log — this will appear in
        # the model prompt on the next thinking phase.
        ctx.execution_log.append(reminder)
        # Track the reminder so it can be removed on sleep/reset to prevent
        # duplicate notifications after restart
        self._last_sleep_reminder = reminder
        self.logger.info("Sleep reminder injected into execution log — log exceeded 100 lines")

    # Compress log every N appends to prevent unbounded growth
    _LOG_COMPRESS_INTERVAL = 50  # compress after every 50 appends
    _LOG_KEEP_RECENT = 30  # keep this many recent entries after compression

    def _compress_execution_log(self, ctx: AutonomousContext) -> None:
        """Compress old log entries to prevent unbounded prompt growth.

        Keeps the most recent _LOG_KEEP_RECENT entries verbatim,
        replaces older entries with a compact summary line.
        """
        log = ctx.execution_log
        # Only compress when there are significantly more entries than we keep,
        # to avoid losing too many entries per compression cycle.
        if len(log) <= self._LOG_KEEP_RECENT * 3:
            return  # not worth compressing yet

        old_count = len(log) - self._LOG_KEEP_RECENT
        recent = log[-self._LOG_KEEP_RECENT:]

        # Count entry types in the old portion for a summary
        user_msgs = sum(1 for l in log[:old_count] if "[user message" in l.lower())
        errors = sum(1 for l in log[:old_count] if "error" in l.lower() or "FAIL" in l)
        commands = sum(1 for l in log[:old_count] if l.strip().startswith("$"))

        summary = (
            f"[LOG COMPRESSION: {old_count} older entries summarized. "
            f"{commands} commands, {user_msgs} user msgs, {errors} errors. "
            f"Showing last {self._LOG_KEEP_RECENT} entries below.]"
        )
        ctx.execution_log = [summary] + recent
        self.logger.info(f"Log compressed: {old_count} entries -> 1 summary line")

    def _append_log(self, ctx: AutonomousContext, entry: str) -> None:
        """Append an entry to the execution log with periodic compression."""
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        ctx.execution_log.append(f"[{timestamp}] {entry}")
        # Periodic compression (instance-level counter)
        self._log_compress_counter += 1
        if self._log_compress_counter >= self._LOG_COMPRESS_INTERVAL:
            self._log_compress_counter = 0
            self._compress_execution_log(ctx)
        # Check whether the one-time rest notification should fire.
        self._maybe_emit_notification(ctx)

    def _raise_if_cancelled(self, ctx: AutonomousContext) -> None:
        if ctx.cancel_event and ctx.cancel_event.is_set():
            raise _PipelineCancelledError(
                "Task cancelled because a newer user request was received"
            )

    def _notify_telegram_error(self, ctx: AutonomousContext, error: str, is_recovery: bool = False) -> None:
        """Send an error/recovery notification to the user via the active bot."""
        icon = "✅" if is_recovery else "❌"
        notification = f"{icon} {error}"

        active_bot = self.discord_bot if ctx.discord_mode else self.telegram_bot
        if active_bot:
            # Prefer the bot's own chat resolution if available (handles explicit,
            # last-inbound, and boot user ids safely).
            target_user = ctx.telegram_user_id
            try:
                resolved = getattr(active_bot, "_resolve_chat_id", lambda x=None: x)(ctx.telegram_user_id)
                if resolved is not None:
                    target_user = resolved
            except Exception:
                # Resolution failed — fall back to boot user id
                target_user = getattr(active_bot, "_boot_user_id", None) or getattr(self, "_telegram_boot_user_id", None)
            if target_user is None:
                target_user = getattr(self, "_telegram_boot_user_id", None)
            if target_user is None:
                target_user = getattr(active_bot, "_boot_user_id", None)
            if target_user is not None:
                try:
                    active_bot.queue_message(target_user, notification)
                    if hasattr(active_bot, "_trigger_queue_flush"):
                        active_bot._trigger_queue_flush()
                    return
                except Exception:
                    pass

        # Fallback to resilience engine
        if ctx.telegram_user_id:
            self._resilience.set_telegram_user_id(ctx.telegram_user_id)
        self._resilience.notify_telegram(notification, is_error=not is_recovery)

    @staticmethod
    def _restore_terminal_history_log() -> list:
        """Load the latest terminal_history/*.json and return formatted log lines.

        Returns a list of strings suitable for seeding
        ``AutonomousContext.execution_log`` so the agent can see what
        happened in the previous session.
        """
        log_lines: list = []
        try:
            from .terminal_history import get_terminal_history
            th = get_terminal_history()
            entries = getattr(getattr(th, "terminal_session", None), "entries", [])
            if not entries:
                return log_lines
            for e in entries[-200:]:
                try:
                    ts = time.strftime("%H:%M:%S", time.localtime(float(e.timestamp)))
                except Exception:
                    ts = "??:??:??"
                content = (e.content or "")[:300]
                rc = e.return_code
                rc_str = f" (exit={rc})" if rc is not None else ""
                log_lines.append(f"[{ts}] [{e.entry_type.value}]{rc_str} {content}")
        except Exception:
            pass
        return log_lines

    def _get_os_info(self) -> str:
        """Get detailed OS information for the system prompt."""
        try:
            system = platform.system()
            release = platform.release()
            version = platform.version()
            machine = platform.machine()
            processor = platform.processor()

            # Memory info
            memory_info = ""
            try:
                import psutil
                mem = psutil.virtual_memory()
                total_gb = mem.total / (1024 ** 3)
                available_gb = mem.available / (1024 ** 3)
                memory_info = (
                    f", Memory: {total_gb:.1f}GB total, "
                    f"{available_gb:.1f}GB available"
                )
            except Exception:
                pass

            # Disk info
            disk_info = ""
            try:
                import psutil
                disk = psutil.disk_usage(str(Path.home()))
                free_gb = disk.free / (1024 ** 3)
                disk_info = f", Disk free: {free_gb:.1f}GB"
            except Exception:
                pass

            shell = os.environ.get("SHELL", "Unknown")

            if system == "Linux":
                try:
                    with open("/etc/os-release") as f:
                        lines = f.readlines()
                    distro_info = {}
                    for line in lines:
                        if "=" in line:
                            key, val = line.strip().split("=", 1)
                            distro_info[key] = val.strip('"')
                    name = distro_info.get("NAME", "Unknown Linux")
                    ver = distro_info.get("VERSION", "")
                    os_str = f"{name} {ver} ({system} {release} {machine})"
                except Exception:
                    os_str = f"Linux {release} {machine}"
            elif system == "Darwin":
                os_str = f"macOS {release} {machine}"
            elif system == "Windows":
                os_str = f"Windows {release} {machine}"
            else:
                os_str = f"{system} {release} {machine}"

            parts = [os_str]
            if processor:
                parts.append(f"CPU: {processor}")
            parts.append(memory_info)
            parts.append(disk_info)
            if system in ("Linux", "Darwin"):
                parts.append(f"Shell: {shell}")

            return ", ".join(p for p in parts if p)

        except Exception as e:
            self.logger.warning(f"Failed to get OS info: {e}")
            return "Unknown OS"


def _bash_input_from_command(command_str: str):
    """Build a BashInput from a raw command string, picking up timeout from config."""
    from ..tools.bash import BashInput
    return BashInput(command=command_str)


_loop_tool_registry = None


def _get_or_create_loop_tool_registry():
    """Lazily create the tool registry used by parallel batches in the loop.

    Registers ALL available tools so that both parallel_begin/end blocks
    and direct tool calls (read/write/edit/glob/grep/bash) can be
    dispatched through the same registry.
    """
    global _loop_tool_registry
    if _loop_tool_registry is None:
        from ..tools.base import ToolRegistry, PermissionSet
        from ..tools.bash import BashTool
        from ..tools.file_read import FileReadTool
        from ..tools.file_write import FileWriteTool
        from ..tools.file_edit import FileEditTool
        from ..tools.glob import GlobTool
        from ..tools.grep import GrepTool
        from ..tools.todo_list import ToDoListTool
        from ..tools.memo import MemoTool
        from ..tools.sub_agent import SubAgentTool

        perms = PermissionSet()
        _loop_tool_registry = ToolRegistry()
        _loop_tool_registry.register(BashTool(perms))
        _loop_tool_registry.register(FileReadTool(perms))
        _loop_tool_registry.register(FileWriteTool(perms))
        _loop_tool_registry.register(FileEditTool(perms))
        _loop_tool_registry.register(GlobTool(perms))
        _loop_tool_registry.register(GrepTool(perms))
        _loop_tool_registry.register(ToDoListTool(perms))
        _loop_tool_registry.register(MemoTool(perms))
        _loop_tool_registry.register(SubAgentTool(perms))
    return _loop_tool_registry


class _TerminalLogSink:
    """
    Real-time terminal log stream that always prints to stderr so that
    the agent's thinking/execution activity is visible to the user
    regardless of where command stdout is directed.

    Now backed by Rich for beautiful output with shimmer effects,
    gradient panels, and smooth animations.
    """

    def __init__(self, enabled: bool = True):
        self._enabled = enabled and self._stderr_is_tty()
        if self._enabled:
            from ..utils.rich_console import StyledLogSink
            self._styled = StyledLogSink(enabled=True)
        else:
            self._styled = None

    @staticmethod
    def _stderr_is_tty() -> bool:
        try:
            return sys.stderr.isatty()
        except Exception:
            return False

    def phase(self, iteration: int, phase_name: str) -> None:
        if self._styled:
            self._styled.phase(iteration, phase_name)

    def thinking(self, text: str) -> None:
        if self._styled:
            self._styled.thinking(text)

    def command(self, cmd: str) -> None:
        if self._styled:
            self._styled.command(cmd)

    def command_result(self, return_code: int, stdout: str, stderr: str) -> None:
        if self._styled:
            self._styled.command_result(return_code, stdout, stderr)

    def model_request(self, iteration: int, model: str, provider: str) -> None:
        if self._styled:
            self._styled.model_request(iteration, model, provider)

    def model_response(self, iteration: int, output_length: int, latency: float = 0) -> None:
        if self._styled:
            self._styled.model_response(iteration, output_length, latency)

    def telegram(self, content: str) -> None:
        if self._styled:
            self._styled.telegram(content)

    def task_done(self, success: bool, iterations: int, duration: float) -> None:
        if self._styled:
            self._styled.task_done(success, iterations, duration)

    def error(self, text: str) -> None:
        if self._styled:
            self._styled.error(text)

    def cancelled(self) -> None:
        if self._styled:
            self._styled.cancelled()

    def parallel_start(self, count: int) -> None:
        if self._styled:
            self._styled.parallel_start(count)

    def parallel_result(self, result) -> None:
        if self._styled:
            self._styled.parallel_result(result)

    def separator(self) -> None:
        if self._styled:
            self._styled.separator()

    def context(self, text: str) -> None:
        if self._styled:
            self._styled.context(text)


class _PipelineCancelledError(Exception):
    """Raised when a newer user request cancels the active loop."""
    pass
