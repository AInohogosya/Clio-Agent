"""
Context Manager for Clio-Agent-1 AI Agent System

Provides always-on context visibility: reads the .context/ folder and
surfaces its contents in the terminal and in the model prompt at all
times — not just on restart.

Files monitored in .context/:
  sleep_state.json   — last sleep/restart state
  exit_state.json    — last graceful-exit state
  rebuild_required   — flag file (consumed on startup)
  context_log.txt   — plain-text compressed context summary (always
                      updated on sleep/exit and on restart restore)

The manager is a lightweight, stateless reader: every call re-reads
disk so that the latest context is always reflected.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default: relative to CWD for backward compatibility.
CONTEXT_DIR = Path(".context")


def set_context_dir(path: Path) -> None:
    """Override the global context directory (e.g. to project root)."""
    global CONTEXT_DIR
    CONTEXT_DIR = Path(path)

STATE_FILES = (
    "sleep_state.json",
    "exit_state.json",
)

CONTEXT_LOG_FILE = "context_log.txt"

# Maximum characters of the compressed context that we inject into the
# model prompt.  Keeps the prompt from growing without bound.
MAX_COMPRESSED_PROMPT_CHARS = 3000

# Maximum number of execution-log lines we surface in the prompt.
MAX_LOG_LINES_IN_PROMPT = 80


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Safely read a JSON file, returning None on any error."""
    try:
        if path.exists() and path.is_file():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _format_timestamp(ts: Any) -> str:
    """Convert a unix timestamp to a human-readable string."""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return str(ts)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_context_state() -> Optional[Dict[str, Any]]:
    """
    Load the most recent context state from the .context/ folder.

    Priority: sleep_state.json > exit_state.json (whichever has the
    newer timestamp).  Returns *None* if no state file exists.
    """
    try:
        CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    best: Optional[Dict[str, Any]] = None
    best_ts: float = 0.0

    for fname in STATE_FILES:
        data = _read_json_file(CONTEXT_DIR / fname)
        if data is None:
            continue
        ts = data.get("timestamp", 0) or 0
        # Use strict > so that sleep_state.json (first in STATE_FILES)
        # wins over exit_state.json when timestamps are equal.
        if ts > best_ts:
            best = data
            best_ts = ts

    return best


def context_files_exist() -> bool:
    """Return True if at least one state file or context_log is present."""
    if any((CONTEXT_DIR / f).exists() for f in STATE_FILES):
        return True
    return (CONTEXT_DIR / CONTEXT_LOG_FILE).exists()


def get_context_summary() -> str:
    """
    Build a compact, human-readable summary of the current context
    suitable for display in the terminal.
    """
    state = load_context_state()
    if state is None:
        return "📂 No context found in .context/"

    lines: List[str] = []
    lines.append("╔══════════════════════════════════════════════════════════╗")
    lines.append("║  📂 CONTEXT — .context/ folder                          ║")
    lines.append("╠══════════════════════════════════════════════════════════╣")

    # Status
    status = state.get("status", "(unknown)")
    lines.append(f"║  Status  : {status}")

    # Goal
    goal = state.get("goal", "(none)")
    lines.append(f"║  Goal    : {goal}")

    # Iterations
    iterations = state.get("iteration_count", 0)
    lines.append(f"║  Iters   : {iterations}")

    # Timestamp
    ts = _format_timestamp(state.get("timestamp", 0))
    lines.append(f"║  Saved   : {ts}")

    # Compressed context
    compressed = state.get("compressed_context", "")
    if compressed:
        lines.append("╠══════════════════════════════════════════════════════════╣")
        lines.append("║  Compressed Context:")
        for cl in compressed.splitlines():
            # Wrap long lines
            while len(cl) > 56:
                lines.append(f"║    {cl[:56]}")
                cl = cl[56:]
            lines.append(f"║    {cl}")

    # Auxiliary
    aux = state.get("auxiliary", {})
    if aux:
        errors = aux.get("errors", "(none)")
        if errors and errors != "(none)":
            lines.append("╠══════════════════════════════════════════════════════════╣")
            lines.append("║  Errors:")
            for el in errors.splitlines()[:5]:
                truncated = el if len(el) <= 56 else el[:53] + "..."
                lines.append(f"║    {truncated}")

        git_diff = aux.get("git_diff", "")
        if git_diff and git_diff != "(not a git repository or no changes)":
            lines.append("╠══════════════════════════════════════════════════════════╣")
            lines.append("║  Git Diff:")
            for gl in git_diff.splitlines()[:5]:
                truncated = gl if len(gl) <= 56 else gl[:53] + "..."
                lines.append(f"║    {truncated}")

    # Execution log tail (from auxiliary data)
    aux_display = state.get("auxiliary", {})
    log_tail = aux_display.get("log_tail", "")
    if log_tail:
        tail_lines = [l for l in log_tail.splitlines() if l.strip()]
        lines.append("╠══════════════════════════════════════════════════════════╣")
        lines.append(f"║  Execution Log (last {min(len(tail_lines), 10)} lines):")
        for entry in tail_lines[-10:]:
            truncated = entry if len(entry) <= 56 else entry[:53] + "..."
            lines.append(f"║    {truncated}")

    lines.append("╚══════════════════════════════════════════════════════════╝")
    return "\n".join(lines)


def get_context_for_prompt() -> str:
    """
    Build a context block suitable for injection into the model's
    thinking prompt.  This is called on *every* iteration so it must
    be concise.

    Reads from JSON state files first; falls back to context_log.txt
    when no JSON state exists but the log file is present.
    """
    state = load_context_state()
    if state is None:
        # Fallback: read context_log.txt directly
        log_path = CONTEXT_DIR / CONTEXT_LOG_FILE
        if log_path.exists():
            try:
                txt = log_path.read_text(encoding="utf-8").strip()
                if txt:
                    return (
                        "\n\n"
                        "════════════════════════════════════════\n"
                        "  CONTEXT FROM .context/context_log.txt\n"
                        "════════════════════════════════════════\n"
                        + _truncate(txt, MAX_COMPRESSED_PROMPT_CHARS)
                        + "\n"
                        "════════════════════════════════════════\n"
                    )
            except Exception:
                pass
        return ""

    sections: List[str] = []

    # Compressed context (most important — the LLM's own summary)
    compressed = state.get("compressed_context", "").strip()
    if compressed:
        sections.append(
            "## SAVED CONTEXT (from previous session)\n"
            + _truncate(compressed, MAX_COMPRESSED_PROMPT_CHARS)
        )

    # Goal
    goal = state.get("goal", "").strip()
    if goal:
        sections.append(f"## PREVIOUS GOAL\n{goal}")

    # Iteration count
    iterations = state.get("iteration_count", 0)
    if iterations:
        sections.append(f"## PREVIOUS ITERATIONS\n{iterations}")

    # Errors from auxiliary
    aux = state.get("auxiliary", {})
    errors = aux.get("errors", "")
    if errors and errors != "(none)":
        sections.append(f"## PREVIOUS ERRORS\n{_truncate(errors, 500)}")

    # Git diff
    git_diff = aux.get("git_diff", "")
    if git_diff and git_diff != "(not a git repository or no changes)":
        sections.append(f"## GIT CHANGES\n{_truncate(git_diff, 500)}")

    # Execution log tail (from auxiliary data)
    log_tail = aux.get("log_tail", "")
    if log_tail:
        tail_lines = [l for l in log_tail.splitlines() if l.strip()]
        sections.append(
            "## EXECUTION LOG (tail)\n" + "\n".join(tail_lines[-MAX_LOG_LINES_IN_PROMPT:])
        )

    if sections:
        return (
            "\n\n"
            "════════════════════════════════════════\n"
            "  CONTEXT FROM .context/ FOLDER\n"
            "════════════════════════════════════════\n"
            + "\n\n".join(sections)
            + "\n"
            "════════════════════════════════════════\n"
        )
    return ""


def display_context_in_terminal() -> None:
    """
    Print the context summary to stderr so it is always visible
    regardless of stdout redirection.
    """
    summary = get_context_summary()
    try:
        import sys
        sys.stderr.write(summary + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def clear_context_state() -> None:
    """
    Remove all state files from .context/.  Called after the agent
    has fully consumed the context and does not need it anymore.
    """
    logger = get_logger("context_manager")
    for fname in STATE_FILES:
        path = CONTEXT_DIR / fname
        try:
            if path.exists():
                path.unlink()
                logger.info(f"Removed context file: {path}")
        except Exception as e:
            logger.warning(f"Failed to remove {path}: {e}")
    # Also remove rebuild flag
    flag = CONTEXT_DIR / "rebuild_required"
    try:
        if flag.exists():
            flag.unlink()
    except Exception:
        pass
    # Also remove context_log.txt to prevent stale re-injection
    log_file = CONTEXT_DIR / CONTEXT_LOG_FILE
    try:
        if log_file.exists():
            log_file.unlink()
            logger.info(f"Removed context log: {log_file}")
    except Exception as e:
        logger.warning(f"Failed to remove {log_file}: {e}")
