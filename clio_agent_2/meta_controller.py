"""Meta-LLM watchdog module.

This module introduces two pieces of "meta" capability for the agent framework:

1. :class:`RepetitionDetector` - detects when the agent is stuck repeating the
   same action over and over by tracking a rolling window of action signatures.

2. The Meta-LLM watchdog - :func:`run_meta` asks a *separate* meta-LLM (routed
   through whatever ``llm_router`` object is supplied) to read the recent
   context and emit a single code-blocked ``ACTION`` describing the next move.
   Parsing helpers (:func:`extract_action_block`) and a self-repair prompt
   builder (:func:`_coding_agent_prompt`) support it.

The module is intentionally standalone and import-safe: it has no side effects
at import time and never imports the concrete ``LLMRouter`` (the router is
duck-typed via the argument to :func:`run_meta`).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Sequence


# ---------------------------------------------------------------------------
# RepetitionDetector
# ---------------------------------------------------------------------------

class RepetitionDetector:
    """Tracks rolling action signatures to detect stuck / repetitive behaviour.

    The signature of an action is::

        sha1(f"{tool}|{sorted(args.items())}|{result_ok}")[:16]

    A bounded (default 6) window of the most recent signatures is kept. The
    agent is considered "stuck" when the most recent signature appears at least
    ``threshold`` (default 4) times inside that window.
    """

    def __init__(self, window: int = 6, threshold: int = 4) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        if threshold < 1:
            raise ValueError("threshold must be >= 1")
        self.window: int = window
        self.threshold: int = threshold
        self._history: List[str] = []

    @staticmethod
    def _signature(tool: str, args: Dict[str, Any], result_ok: bool) -> str:
        """Compute the 16-char hex signature for a single action.

        FIX: the old code did ``sorted(args.items())`` which raises TypeError
        whenever ``args`` holds an unhashable value (a list/dict) or values
        that are not mutually orderable (e.g. an int beside a str). That crash
        propagated out of ``record()`` and took the agent down. We now build a
        stable, order-independent signature from a canonical JSON dump.
        """
        canonical = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
        raw = f"{tool}|{canonical}|{result_ok}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def record(self, tool: str, args: Dict[str, Any], result_ok: bool) -> str:
        """Record one action and return its signature.

        Keeps only the most recent ``self.window`` signatures.
        """
        sig = self._signature(tool, args, result_ok)
        self._history.append(sig)
        if len(self._history) > self.window:
            # Keep only the most recent `window` entries.
            self._history = self._history[-self.window:]
        return sig

    def is_stuck(self) -> bool:
        """Return True if the latest signature repeats >= threshold times.

        Only signatures currently inside the window are considered.
        """
        if len(self._history) < self.threshold:
            return False
        last = self._history[-1]
        count = sum(1 for sig in self._history if sig == last)
        return count >= self.threshold

    def reset(self) -> None:
        """Clear all recorded history."""
        self._history.clear()


# ---------------------------------------------------------------------------
# Meta-LLM watchdog
# ---------------------------------------------------------------------------

META_SYSTEM_PROMPT: str = """\
You are a meta-controller for an autonomous coding agent. Your job is to read \
the agent's recent context and decide the single most useful next step.

When asked, you MUST respond with exactly ONE fenced code block containing an \
ACTION. The block has this exact shape:

ACTION
MODE: <bug_hunt|build|research>
TOPIC: <short>
REASON: <why>

Rules:
- Emit ONLY one ACTION block. Do not emit multiple blocks or extra prose.
- MODE must be one of: bug_hunt, build, research.
- TOPIC is a short human-readable summary of what to work on.
- REASON explains why this action is the best next step given the context.
- Do NOT propose an action that merely repeats something already present in the \
recent context. Look at the actions/tools already executed and pick something \
that makes progress instead of looping.
"""


# Pre-compiled, safe regexes (module-level, raw strings, no stray backslashes).
_FENCED_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
# Capture from an "ACTION" header (line inclusive) up to a blank line, another
# "ACTION" header (a second block), or the end of the text. DOTALL lets the
# lazy match span the MODE/TOPIC/REASON lines without being cut off by them.
_ACTION_HEADER_RE = re.compile(
    r"(?im)^ACTION\b[^\n]*\n.*?(?=\n[ \t]*\n|\nACTION\b|\Z)",
    re.DOTALL,
)


def extract_action_block(text: str) -> str:
    """Pull the (fenced or unfenced) ACTION block out of ``text``.

    Returns the trimmed block (including the leading ``ACTION`` line) or an
    empty string if no parseable ACTION block is found.
    """
    if not text:
        return ""

    # Normalise line endings for predictable matching.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")

    # 1) Prefer a fenced code block that actually contains the word ACTION.
    for match in _FENCED_BLOCK_RE.finditer(normalized):
        block = match.group(1).strip()
        if re.search(r"\bACTION\b", block, re.IGNORECASE):
            return block

    # 2) Fall back to an explicit "ACTION" header in the free text. group(0)
    #    includes the "ACTION" line itself (kept consistent with the fenced
    #    path above).
    header_match = _ACTION_HEADER_RE.search(normalized)
    if header_match:
        return header_match.group(0).strip()

    # 3) Last resort: the whole message is small and clearly an action block.
    if re.search(r"\bACTION\b", normalized, re.IGNORECASE) and re.search(
        r"\bMODE:", normalized
    ):
        return normalized.strip()

    return ""



def _coding_agent_prompt(detail: str) -> str:
    """Return a prompt instructing a separate coding agent to fix this module.

    Used to build the message of the :class:`RuntimeError` raised by
    :func:`run_meta` on any failure.
    """
    return (
        "You are a coding agent. A failure occurred in "
        "clio_agent_2/meta_controller.py.\n"
        "Please inspect the file and fix it so that the meta-controller works "
        "correctly (RepetitionDetector, run_meta, extract_action_block).\n\n"
        "Failure detail:\n"
        f"{detail}\n"
    )


def _build_context_blob(
    recent_entries: Sequence[Any],
    recent_recommendations: Sequence[Any],
) -> str:
    """Render recent context entries and recommendations into text.

    Tolerates arbitrary element types (strings, context-entry-like objects)
    by stringifying each element.
    """
    lines: List[str] = []

    lines.append("--- Recent entries ---")
    if recent_entries:
        for index, entry in enumerate(recent_entries, start=1):
            lines.append(f"{index}. {entry}")
    else:
        lines.append("(none)")

    lines.append("")
    lines.append("--- Recent recommendations ---")
    if recent_recommendations:
        for index, rec in enumerate(recent_recommendations, start=1):
            lines.append(f"{index}. {rec}")
    else:
        lines.append("(none)")

    return "\n".join(lines)


async def run_meta(
    llm_router: Any,
    recent_entries: Sequence[Any],
    recent_recommendations: Sequence[Any],
) -> str:
    """Ask the meta-LLM for the next single ACTION.

    Calls ``llm_router.chat([system, user])`` with the context blob plus the
    recent recommendations, then parses the resulting ACTION block.

    Args:
        llm_router: An object providing an awaitable ``chat(messages)`` method
            that accepts a list of ``{"role", "content"}`` message dicts and
            returns a string (i.e. the real ``LLMRouter`` interface).
        recent_entries: Recent context entries (any sequence of stringifiable
            items).
        recent_recommendations: Recent recommendations (any sequence of
            stringifiable items).

    Returns:
        The parsed ACTION block string.

    Raises:
        RuntimeError: On ANY failure - an error calling the LLM, or if the
            response does not contain a parseable ACTION block. The error
            message is the output of :func:`_coding_agent_prompt`.
    """
    context_blob = _build_context_blob(recent_entries, recent_recommendations)

    user_message = (
        "Below is the agent's recent context. Read it and, following your "
        "system instructions, emit a single ACTION block (and nothing else).\n\n"
        f"{context_blob}\n"
    )

    messages = [
        {"role": "system", "content": META_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    # Call the router using its real signature: a list of role/content dicts.
    # chat() is a coroutine, so it must be awaited to obtain the response text.
    try:
        raw_response = await llm_router.chat(messages)
    except Exception as exc:  # any call error
        raise RuntimeError(
            _coding_agent_prompt(f"llm_router.chat failed: {exc}")
        ) from exc

    if not isinstance(raw_response, str):
        raw_response = str(raw_response)

    action_block = extract_action_block(raw_response)
    if not action_block:
        raise RuntimeError(
            _coding_agent_prompt(
                "No parseable ACTION block returned by the meta-LLM. "
                f"Raw output:\n{raw_response}"
            )
        )

    return action_block


__all__ = [
    "RepetitionDetector",
    "META_SYSTEM_PROMPT",
    "extract_action_block",
    "run_meta",
    "_coding_agent_prompt",
]

