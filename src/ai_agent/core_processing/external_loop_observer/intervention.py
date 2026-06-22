"""
Intervention System for External Loop Observer.

Decides what action to take when a loop pattern is detected, and
communicates that decision back to the agent engine via a control file.

The intervention system is PURELY DETERMINISTIC — given a set of
PatternMatch results, it always produces the same intervention.
No LLM judgment is involved at this layer.

Intervention levels (escalating):
  0 — LOG:       Record the observation, no action
  1 — NUDGE:     Write a hint into the control file (soft suggestion)
  2 — ALERT:     Write a strong warning (hard enforcement message)
  3 — INTERRUPT: Force sleep immediately (write sleep command flag)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import List, Optional

from .pattern_analyzer import PatternMatch, PatternType


class InterventionLevel(IntEnum):
    LOG = 0
    NUDGE = 1
    ALERT = 2
    INTERRUPT = 3


@dataclass
class Intervention:
    """An intervention decision."""
    level: InterventionLevel
    message: str           # Human-readable message for logs
    control_file_payload: Optional[dict] = None  # Written to control file
    force_sleep: bool = False

    @property
    def is_actionable(self) -> bool:
        return self.level >= InterventionLevel.NUDGE


# Thresholds — when to escalate
_NUDGE_THRESHOLD = 0.35       # severity ≥ this → NUDGE
_ALERT_THRESHOLD = 0.80       # severity ≥ this → ALERT
_INTERRUPT_THRESHOLD = 1.50   # severity ≥ this → INTERRUPT

# Repeat counts that force escalation regardless of confidence
_ALERT_REPEAT_MIN = 6         # 6+ repeats → at least ALERT
_INTERRUPT_REPEAT_MIN = 10    # 10+ repeats → INTERRUPT


def decide_intervention(patterns: List[PatternMatch]) -> Intervention:
    """Given detected patterns, decide what intervention to take."""
    if not patterns:
        return Intervention(InterventionLevel.LOG, "No loop patterns detected")

    # Pick the highest-severity pattern
    worst = max(patterns, key=lambda p: p.severity)
    sev = worst.severity
    reps = worst.repeat_count

    # Determine base level from severity score
    if reps >= _INTERRUPT_REPEAT_MIN or sev >= _INTERRUPT_THRESHOLD:
        level = InterventionLevel.INTERRUPT
    elif reps >= _ALERT_REPEAT_MIN or sev >= _ALERT_THRESHOLD:
        level = InterventionLevel.ALERT
    elif sev >= _NUDGE_THRESHOLD:
        level = InterventionLevel.NUDGE
    else:
        level = InterventionLevel.LOG

    # OUTPUT_STALL always gets at least NUDGE (LLM is frozen)
    if worst.pattern_type == PatternType.OUTPUT_STALL and level < InterventionLevel.NUDGE:
        level = InterventionLevel.NUDGE

    # Build the intervention
    msg = _build_message(worst, level)
    payload = _build_payload(worst, level)
    force_sleep = level >= InterventionLevel.INTERRUPT

    return Intervention(level, msg, payload, force_sleep)


def _build_message(pattern: PatternMatch, level: InterventionLevel) -> str:
    type_name = pattern.pattern_type.name
    prefix = {
        InterventionLevel.LOG: "[observer]",
        InterventionLevel.NUDGE: "[observer ⚡ NUDGE]",
        InterventionLevel.ALERT: "[observer 🚨 ALERT]",
        InterventionLevel.INTERRUPT: "[observer 🛑 INTERRUPT]",
    }[level]
    return f"{prefix} {type_name} loop detected: {pattern.description}"


def _build_payload(pattern: PatternMatch, level: InterventionLevel) -> dict:
    return {
        "observer": True,
        "version": 1,
        "timestamp": time.time(),
        "level": int(level),
        "level_name": level.name,
        "pattern_type": pattern.pattern_type.name,
        "description": pattern.description,
        "confidence": pattern.confidence,
        "repeat_count": pattern.repeat_count,
        "cycle_length": pattern.cycle_length,
        "severity": pattern.severity,
        "iteration_range": [pattern.first_seen_iteration, pattern.latest_iteration],
        "agent_message": _agent_facing_message(pattern, level),
    }


def _agent_facing_message(pattern: PatternMatch, level: InterventionLevel) -> str:
    """Generate the message that gets injected into the agent's execution log."""
    type_name = pattern.pattern_type.name

    if level == InterventionLevel.NUDGE:
        return (
            f"[SYSTEM] ⚡ EXTERNAL OBSERVER NUDGE — {type_name} pattern detected. "
            f"This action signature has repeated {pattern.repeat_count} times. "
            f"Consider changing your approach. The observer is watching."
        )

    if level == InterventionLevel.ALERT:
        return (
            f"[SYSTEM] 🚨 EXTERNAL OBSERVER ALERT — {type_name} loop confirmed. "
            f"Action has repeated {pattern.repeat_count} times over iterations "
            f"{pattern.first_seen_iteration}-{pattern.latest_iteration}. "
            f"You MUST change your behavior NOW. Options: "
            f"(a) Execute `sleep` to reset. "
            f"(b) Switch to a completely different task/area. "
            f"(c) Explore a new file/directory. "
            f"This is an EXTERNAL enforcement — not a suggestion."
        )

    if level == InterventionLevel.INTERRUPT:
        return (
            f"[SYSTEM] 🛑 EXTERNAL OBSERVER INTERRUPT — Persistent {type_name} loop. "
            f"After {pattern.repeat_count} repetitions, the observer is forcing a context reset. "
            f"Execute `sleep` IMMEDIATELY. Do not pass go."
        )

    return ""
