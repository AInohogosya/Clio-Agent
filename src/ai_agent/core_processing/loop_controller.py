"""
Loop Controller for Clio-Agent-1 AI Agent System

Manages execution log lifecycle, loop detection, log compression for prompt
injection, and the Curiosity Fairy mechanism that suggests new actions
when the agent is stuck in a repetitive loop.

The controller acts as a centralized observability layer: every agent
iteration funnels through it, giving the engine consistent data for
anti-loop decisions, prompt formatting, and runtime context.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .agent_schema import AgentAction, AgentPlan, ActionType
from .context_manager import (
    context_files_exist,
    get_context_for_prompt,
    display_context_in_terminal,
)
from ..utils.logger import get_logger
from ..external_integration.telegram_bot import ConversationHistory


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LOG_LINES_IN_PROMPT: int = 80
"""Maximum number of execution-log lines injected into the LLM prompt."""

NOTIFICATION_THRESHOLD: int = 100
"""Number of iterations between periodic user-facing notifications."""

COMPRESS_INTERVAL: int = 50
"""Append operations between compression passes."""

KEEP_RECENT: int = 30
"""Most recent log entries retained after compression."""

LOOP_REPEAT_THRESHOLD: int = 4
"""Consecutive identical action-signature repeats before triggering Curiosity Fairy."""

PERSISTENT_LOOP_THRESHOLD: int = 6
"""Consecutive identical action-signature repeats before forcing a sleep."""


# ---------------------------------------------------------------------------
# LoopContext dataclass
# ---------------------------------------------------------------------------

@dataclass
class LoopContext:
    """Mutable context bag carried through every loop iteration.

    Bundles the runtime state that ``LoopController`` needs to inspect
    and update across iterations without scattered globals.
    """

    user_prompt: str = ""
    """The user's current prompt text."""

    current_goal: str = ""
    """The agent's current high-level goal (may be updated by the planner)."""

    execution_log: List[str] = field(default_factory=list)
    """Chronological list of log entries from all iterations."""

    iteration_count: int = 0
    """How many loop iterations have executed so far."""

    start_time: float = field(default_factory=time.time)
    """Unix timestamp when the loop started."""

    end_time: Optional[float] = None
    """Unix timestamp when the loop finished (``None`` while running)."""

    telegram_mode: bool = False
    """Whether the agent is communicating via Telegram."""

    discord_mode: bool = False
    """Whether the agent is communicating via Discord."""

    telegram_user_id: Optional[int] = None
    """The Telegram user/chat ID for replies (if in Telegram mode)."""

    conversation_history: Optional[ConversationHistory] = None
    """Shared Telegram/Discord conversation memory."""

    cancel_event: Optional[Any] = None
    """Threading/async ``Event`` — signals the loop to abort gracefully."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Arbitrary extra data (open key-value bag for plugins / tools)."""


# ---------------------------------------------------------------------------
# Internal log entry representation
# ---------------------------------------------------------------------------

@dataclass
class _LogEntry:
    """A single execution-log record."""
    timestamp: str
    iteration: int
    content: str
    is_user_message: bool = False
    has_error: bool = False


# ---------------------------------------------------------------------------
# LoopController
# ---------------------------------------------------------------------------

class LoopController:
    """Central controller for loop observability and anti-loop logic.

    Responsibilities
    ----------------
    * **Execution log** — append, compress, and format for prompts.
    * **Loop detection** — detect repeated action signatures and expose
      ``check_loop()`` / ``check_empty_iteration()`` to the engine.
    * **Curiosity Fairy** — suggest fresh actions when the agent is stuck.
    * **Drift counters** — track and reset streak counters that other
      subsystems use to detect stalled state.
    """

    # Construction ---------------------------------------------------------

    def __init__(self, *, project_root: Optional[str] = None) -> None:
        """
        Initialise the controller.

        Parameters
        ----------
        project_root:
            Path to the project root directory.  Used by the
            Curiosity Fairy when scanning the filesystem.
            Defaults to ``os.getcwd()``.
        """
        self.logger = get_logger("loop_controller")
        self._project_root: str = project_root or os.getcwd()

        # Compression bookkeeping
        self._append_count: int = 0

        # Loop-detection bookkeeping
        self._recent_signatures: List[str] = []
        self._consecutive_repeats: int = 0
        self._drift_counters: Dict[str, int] = {}

    # ------------------------------------------------------------------ #
    #  Public: LoopContext lifecycle                                      #
    # ------------------------------------------------------------------ #

    def create_loop_context(
        self,
        *,
        user_prompt: str = "",
        current_goal: str = "",
        telegram_mode: bool = False,
        discord_mode: bool = False,
        telegram_user_id: Optional[int] = None,
        conversation_history: Optional[ConversationHistory] = None,
        cancel_event: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        iteration_count: int = 0,
    ) -> LoopContext:
        """Build a fresh :class:`LoopContext` with sensible defaults."""
        return LoopContext(
            user_prompt=user_prompt,
            current_goal=current_goal,
            iteration_count=iteration_count,
            telegram_mode=telegram_mode,
            discord_mode=discord_mode,
            telegram_user_id=telegram_user_id,
            conversation_history=conversation_history,
            cancel_event=cancel_event,
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------ #
    #  Public: Execution log                                              #
    # ------------------------------------------------------------------ #

    def append_log(
        self,
        context: LoopContext,
        content: str,
        *,
        is_user_message: bool = False,
        has_error: bool = False,
    ) -> None:
        """Append a timestamped entry to ``context.execution_log``.

        Triggers automatic compression every ``COMPRESS_INTERVAL`` appends
        and periodically surfaces a sleep-eligible notification when the
        iteration count crosses ``NOTIFICATION_THRESHOLD``.

        Parameters
        ----------
        context:
            The active loop context.
        content:
            Human-readable description of the action.
        is_user_message:
            ``True`` when the entry represents a user-originated message.
        has_error:
            ``True`` when the entry describes a failure.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = _LogEntry(
            timestamp=timestamp,
            iteration=context.iteration_count,
            content=content,
            is_user_message=is_user_message,
            has_error=has_error,
        )

        tag = "[USER] " if is_user_message else ""
        if has_error:
            tag += "[ERROR] "
        context.execution_log.append(
            f"[{timestamp}] #{context.iteration_count} {tag}{content}"
        )
        self.logger.debug(f"Log appended: #{context.iteration_count} {content}")

        self._append_count += 1

        # Periodic compression
        if self._append_count > 0 and self._append_count % COMPRESS_INTERVAL == 0:
            self.compress_log(context)

        # Periodic sleep notification
        if context.iteration_count > 0 and context.iteration_count % NOTIFICATION_THRESHOLD == 0:
            self.logger.info(
                f"Iteration {context.iteration_count} — "
                f"consider sleep if no real work remains."
            )

    def compress_log(self, context: LoopContext) -> None:
        """Compress old log entries, keeping the most recent ``KEEP_RECENT``.

        Compression strategy:
        * Parse each line into a :class:``_LogEntry``.
        * Summarise the *old* portion (all but the last ``KEEP_RECENT``)
          into a single compact line.
        * Replace the log with ``[summary] + recent_lines``.
        """
        if len(context.execution_log) <= KEEP_RECENT:
            return  # Nothing to compress

        recent_lines = context.execution_log[-KEEP_RECENT:]
        old_lines = context.execution_log[:-KEEP_RECENT]

        # Build a compact summary of the old entries
        summary_parts: List[str] = []
        error_count = sum(1 for line in old_lines if "[ERROR]" in line)
        user_count = sum(1 for line in old_lines if "[USER]" in line)

        first_iter = _parse_iteration(old_lines[0]) if old_lines else "?"
        last_iter = _parse_iteration(old_lines[-1]) if old_lines else "?"

        summary_parts.append(
            f"[COMPRESSED {len(old_lines)} entries "
            f"(iterations #{first_iter}–#{last_iter})"
        )
        if error_count:
            summary_parts.append(f"{error_count} errors")
        if user_count:
            summary_parts.append(f"{user_count} user msgs")

        summary_line = " | ".join(summary_parts)
        context.execution_log = [summary_line] + recent_lines
        self.logger.info(
            f"Log compressed: {len(old_lines)} old entries summarised, "
            f"{KEEP_RECENT} recent retained."
        )

    def format_log_for_prompt(self, context: LoopContext) -> str:
        """Return a prompt-ready string of the execution log.

        * Limits output to ``MAX_LOG_LINES_IN_PROMPT`` lines.
        * User messages are prefixed with ``>>>`` for visibility.
        * A trailing ``[TRUNCATED]`` marker indicates when lines were dropped.
        """
        log = context.execution_log
        if not log:
            return ""

        truncated = len(log) > MAX_LOG_LINES_IN_PROMPT
        lines = log[-MAX_LOG_LINES_IN_PROMPT:] if truncated else log

        formatted: List[str] = []
        for line in lines:
            if "[USER]" in line:
                formatted.append(f">>> {line}")
            elif "[ERROR]" in line:
                formatted.append(f"!!! {line}")
            else:
                formatted.append(f"    {line}")

        parts: List[str] = []
        if truncated:
            parts.append(
                f"[TRUNCATED — showing last {MAX_LOG_LINES_IN_PROMPT} of {len(log)} entries]"
            )
        parts.append("## EXECUTION LOG")
        parts.extend(formatted)
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    #  Public: Loop detection                                             #
    # ------------------------------------------------------------------ #

    def check_loop(self, context: LoopContext, plan: AgentPlan) -> bool:
        """Detect repeated action patterns in the most recent plans.

        Returns ``True`` when the same action signature appears
        ``LOOP_REPEAT_THRESHOLD`` or more times consecutively.

        Side effect: increments ``_consecutive_repeats`` which is read
        by :meth:`invoke_curiosity_fairy` and the engine's anti-loop logic.
        """
        signatures = plan.get_action_signatures()
        key = "|".join(sorted(signatures))

        if self._recent_signatures and self._recent_signatures[-1] == key:
            self._consecutive_repeats += 1
        else:
            self._consecutive_repeats = 1

        self._recent_signatures.append(key)
        # Keep only the last 20 signatures for memory efficiency
        if len(self._recent_signatures) > 20:
            self._recent_signatures = self._recent_signatures[-20:]

        is_loop = self._consecutive_repeats >= LOOP_REPEAT_THRESHOLD
        if is_loop:
            self.logger.warning(
                f"Loop detected: signature '{key}' repeated "
                f"{self._consecutive_repeats} times."
            )
        return is_loop

    def check_empty_iteration(self, context: LoopContext, plan: AgentPlan) -> bool:
        """Return ``True`` when the plan contains no real work.

        An "empty iteration" is one where the plan has no actions or
        only ``thinking`` / ``sleep`` actions — nothing that advances
        the task.
        """
        if not plan.actions:
            self.logger.info(
                f"Empty iteration #{context.iteration_count}: no actions."
            )
            return True

        real_types = {
            ActionType.READ, ActionType.WRITE, ActionType.EDIT,
            ActionType.GLOB, ActionType.GREP, ActionType.BASH,
            ActionType.COMMAND, ActionType.TELEGRAM, ActionType.DISCORD,
        }
        has_real = any(a.type in real_types for a in plan.actions)
        if not has_real:
            self.logger.info(
                f"Empty iteration #{context.iteration_count}: "
                f"only meta-actions ({[a.type for a in plan.actions]})."
            )
            return True
        return False

    # ------------------------------------------------------------------ #
    #  Public: Curiosity Fairy                                            #
    # ------------------------------------------------------------------ #

    def invoke_curiosity_fairy(self, context: LoopContext) -> List[str]:
        """Generate deterministic suggestions to break out of a loop.

        The "fairy" inspects the filesystem and runtime state to
        propose genuinely different actions.  Suggestions are ordered
        by relevance and deduplicated.

        Returns a list of human-readable suggestion strings.
        """
        suggestions: List[str] = []
        seen: Set[str] = set()

        # --- 1. Unread / recently-modified files -------------------------
        try:
            recent_files = self._find_recent_files(context)
            for fpath in recent_files[:5]:
                suggestion = f"Read recent file: {fpath}"
                if suggestion not in seen:
                    suggestions.append(suggestion)
                    seen.add(suggestion)
        except Exception as exc:
            self.logger.debug(f"Curiosity Fairy file scan failed: {exc}")

        # --- 2. Git status ----------------------------------------------
        try:
            git_suggestions = self._check_git_status()
            for s in git_suggestions:
                if s not in seen:
                    suggestions.append(s)
                    seen.add(s)
        except Exception as exc:
            self.logger.debug(f"Curiosity Fairy git check failed: {exc}")

        # --- 3. TODO / FIXME comments ------------------------------------
        try:
            todo_suggestions = self._find_todos()
            for s in todo_suggestions:
                if s not in seen:
                    suggestions.append(s)
                    seen.add(s)
        except Exception as exc:
            self.logger.debug(f"Curiosity Fairy TODO scan failed: {exc}")

        # --- 4. Context files --------------------------------------------
        try:
            if context_files_exist():
                ctx = get_context_for_prompt()
                if ctx and "CONTEXT FROM" in ctx:
                    suggestion = (
                        "Review saved context from .context/ folder "
                        "for prior session state."
                    )
                    if suggestion not in seen:
                        suggestions.append(suggestion)
                        seen.add(suggestion)
        except Exception as exc:
            self.logger.debug(f"Curiosity Fairy context check failed: {exc}")

        # --- 5. Generic fallbacks when nothing was found -------------------
        if not suggestions:
            suggestions.append(
                "No obvious next actions found. Consider sleeping until "
                "new input arrives."
            )

        self.logger.info(
            f"Curiosity Fairy generated {len(suggestions)} suggestions."
        )
        return suggestions

    # ------------------------------------------------------------------ #
    #  Public: Drift counters                                            #
    # ------------------------------------------------------------------ #

    def reset_drift_counters(self, context: Optional[LoopContext] = None) -> None:
        """Reset all internal drift/streak counters.

        Optionally stores the current drift snapshot in ``context.metadata``
        before resetting, so the engine can inspect what accumulated.
        """
        if context is not None:
            context.metadata["last_drift_snapshot"] = dict(self._drift_counters)
        self._drift_counters.clear()
        self._consecutive_repeats = 0
        self._recent_signatures.clear()
        self.logger.info("Drift counters reset.")

    def increment_drift(self, key: str, amount: int = 1) -> int:
        """Increment a named drift counter and return its new value."""
        self._drift_counters[key] = self._drift_counters.get(key, 0) + amount
        return self._drift_counters[key]

    def get_drift_value(self, key: str) -> int:
        """Return the current value of a named drift counter."""
        return self._drift_counters.get(key, 0)

    # ------------------------------------------------------------------ #
    #  Private helpers                                                    #
    # ------------------------------------------------------------------ #

    def _find_recent_files(self, context: LoopContext) -> List[str]:
        """Return up to 10 recently modified files under the project root.

        Skips hidden directories and avoids files already in the log.
        """
        root = Path(self._project_root)
        skip_dirs = {
            ".git", "__pycache__", ".context", "node_modules",
            ".venv", "venv", ".mypy_cache", ".pytest_cache",
        }

        already_read: Set[str] = set()
        for line in context.execution_log[-100:]:
            if line.startswith("read:") or "Read file:" in line:
                parts = line.split()
                for part in parts:
                    cleaned = part.strip("'\"")
                    if cleaned and (
                        cleaned.startswith("/") or Path(cleaned).suffix
                    ):
                        already_read.add(cleaned)

        candidates: List[tuple[float, str]] = []
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs]
                for fname in filenames:
                    full = os.path.join(dirpath, fname)
                    if full in already_read:
                        continue
                    try:
                        mtime = os.path.getmtime(full)
                        candidates.append((mtime, full))
                    except OSError:
                        continue
        except Exception:
            pass

        candidates.sort(reverse=True)
        return [c[1] for c in candidates[:10]]

    def _check_git_status(self) -> List[str]:
        """Run ``git status`` and return actionable suggestions."""
        suggestions: List[str] = []
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=self._project_root,
                timeout=10,
            )
            if result.returncode != 0:
                return suggestions

            lines = result.stdout.strip().splitlines()
            if not lines:
                suggestions.append(
                    "Git clean — consider exploring the codebase "
                    "for improvement opportunities."
                )
                return suggestions

            untracked = [l for l in lines if l.startswith("??")]
            modified = [
                l for l in lines if l.startswith(" M") or l.startswith("M ")
            ]
            staged = [
                l for l in lines if l.startswith("A ") or l.startswith("M ")
            ]

            if untracked:
                suggestions.append(
                    f"You have {len(untracked)} untracked file(s). "
                    f"Review them: {', '.join(l[3:] for l in untracked[:3])}"
                )
            if modified:
                suggestions.append(
                    f"You have {len(modified)} modified file(s). "
                    f"Check changes: {', '.join(l[3:] for l in modified[:3])}"
                )
            if staged:
                suggestions.append(
                    f"You have {len(staged)} staged change(s). "
                    f"Consider committing them."
                )
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        return suggestions

    def _find_todos(self) -> List[str]:
        """Grep for ``TODO`` / ``FIXME`` comments and return suggestions."""
        suggestions: List[str] = []
        try:
            result = subprocess.run(
                [
                    "grep", "-rn", "--include=*.py",
                    "-E", r"TODO|FIXME", self._project_root,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return suggestions

            lines = result.stdout.strip().splitlines()[:5]
            for line in lines:
                trimmed = line[:100] + ("..." if len(line) > 100 else "")
                suggestions.append(f"Found TODO/FIXME: {trimmed}")
        except (subprocess.SubprocessError, FileNotFoundError):
            pass
        return suggestions


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _parse_iteration(log_line: str) -> str:
    """Extract the iteration number from a log line like ``[#42 ...]``.

    Returns ``"?"`` if parsing fails.
    """
    try:
        parts = log_line.split("#")
        if len(parts) >= 2:
            num = parts[1].split(" ")[0]
            return num
    except Exception:
        pass
    return "?"