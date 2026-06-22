"""
External Loop Observer — Orchestrator Module.

Out-of-band loop detection that watches the agent from outside.
It has no access to the agent's goals, plans, or reasoning — only
the actions taken and how often they repeat.

Persists state across sleep restarts to detect cross-session loops.

Usage:
    observer = ExternalObserver(project_root="/path/to/project")
    observer.on_iteration(commands=[...], output_text="...", iteration_number=42)
    status = observer.get_status()
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .action_normalizer import ActionNormalizer, NormalizedIteration
from .pattern_analyzer import PatternAnalyzer, PatternMatch
from .intervention import (
    Intervention, InterventionLevel, decide_intervention,
)


@dataclass
class ObserverConfig:
    """Configuration for the ExternalObserver."""
    control_file: str = ".context/observer_control.json"
    state_file: str = ".context/observer_state.json"
    log_file: str = "logs/observer.log"
    history_window: int = 200
    analysis_interval: int = 1
    min_iterations_before_analysis: int = 3
    enable_intervention: bool = True
    enable_persistence: bool = True
    enable_logging: bool = True


@dataclass
class ObserverVerdict:
    """The observer's assessment after analyzing an iteration."""
    iteration_number: int
    has_loop: bool
    intervention_level: int
    intervention_level_name: str
    patterns: List[PatternMatch]
    intervention_message: str
    force_sleep: bool
    observer_state_summary: Dict[str, Any] = field(default_factory=dict)


class ExternalObserver:
    """Out-of-band loop observer that watches the agent from outside."""

    def __init__(self, project_root=None, config=None):
        self._config = config or ObserverConfig()
        self._project_root = project_root or Path.cwd()
        self._normalizer = ActionNormalizer()
        self._analyzer = PatternAnalyzer(history_window=self._config.history_window)
        self._last_intervention_level = InterventionLevel.LOG
        self._intervention_cooldown = 0
        self._total_iterations_observed = 0
        if self._config.enable_persistence:
            self._load_state()

    def on_iteration(self, commands, output_text="", iteration_number=0):
        """Record one agent iteration and return the observer's verdict."""
        self._total_iterations_observed += 1
        iter_num = iteration_number or self._total_iterations_observed

        normalized = self._normalizer.normalize_iteration(
            iteration_number=iter_num,
            commands=commands,
            output_text=output_text,
            timestamp=time.time(),
        )
        self._analyzer.add_iteration(normalized)

        verdict = ObserverVerdict(
            iteration_number=iter_num,
            has_loop=False,
            intervention_level=0,
            intervention_level_name="LOG",
            patterns=[],
            intervention_message="",
            force_sleep=False,
        )

        if (iter_num >= self._config.min_iterations_before_analysis
                and iter_num % self._config.analysis_interval == 0):
            patterns = self._analyzer.analyze()
            if patterns:
                verdict = self._evaluate(patterns, iter_num)

        if self._config.enable_persistence:
            self._save_state()
        return verdict

    def _evaluate(self, patterns, iteration_number):
        """Decide on an intervention based on detected patterns."""
        intervention = decide_intervention(patterns)
        self._intervention_cooldown += 1

        if (intervention.level <= self._last_intervention_level
                and self._intervention_cooldown < 3
                and intervention.level < InterventionLevel.INTERRUPT):
            return ObserverVerdict(
                iteration_number=iteration_number,
                has_loop=True,
                intervention_level=int(self._last_intervention_level),
                intervention_level_name=self._last_intervention_level.name,
                patterns=patterns,
                intervention_message="",
                force_sleep=False,
            )

        self._last_intervention_level = intervention.level
        self._intervention_cooldown = 0

        if intervention.is_actionable and self._config.enable_intervention:
            self._write_control_file(intervention)

        agent_msg = ""
        if intervention.control_file_payload:
            agent_msg = intervention.control_file_payload.get("agent_message", "")

        return ObserverVerdict(
            iteration_number=iteration_number,
            has_loop=True,
            intervention_level=int(intervention.level),
            intervention_level_name=intervention.level.name,
            patterns=patterns,
            intervention_message=agent_msg,
            force_sleep=intervention.force_sleep,
        )

    def _write_control_file(self, intervention):
        if not intervention.control_file_payload:
            return
        path = self._project_root / self._config.control_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(intervention.control_file_payload, f, indent=2)
                f.flush()
            tmp.replace(path)
        except OSError:
            pass

    def read_control_file(self):
        path = self._project_root / self._config.control_file
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def clear_control_file(self):
        path = self._project_root / self._config.control_file
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def _save_state(self):
        path = self._project_root / self._config.state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "version": 1,
                "timestamp": time.time(),
                "total_iterations_observed": self._total_iterations_observed,
                "last_intervention_level": int(self._last_intervention_level),
                "signature_counts": self._analyzer._sig_counts,
            }
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
                f.flush()
            tmp.replace(path)
        except OSError:
            pass

    def _load_state(self):
        path = self._project_root / self._config.state_file
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self._analyzer._sig_counts = state.get("signature_counts", {})
                self._total_iterations_observed = state.get(
                    "total_iterations_observed", 0
                )
        except (OSError, json.JSONDecodeError):
            pass

    def get_status(self):
        return {
            "total_iterations_observed": self._total_iterations_observed,
            "history_length": self._analyzer.history_length,
            "last_intervention_level": self._last_intervention_level.name,
            "unique_signatures": len(self._analyzer._sig_counts),
        }

    def reset(self):
        self._analyzer = PatternAnalyzer(history_window=self._config.history_window)
        self._last_intervention_level = InterventionLevel.LOG
        self._intervention_cooldown = 0
