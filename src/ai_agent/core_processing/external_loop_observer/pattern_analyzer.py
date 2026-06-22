"""Pattern Analyzer for External Loop Observer.

Deterministic loop detection using normalized iteration signatures.
NO LLM involvement — purely algorithmic pattern matching.

Detects:
  1. EXACT:       Same iteration signature repeats consecutively
  2. CYCLIC:      A sequence of signatures repeats (A→B→A→B→A→B)
  3. DRIFT:       Iterations gradually shrink (fewer unique actions)
  4. OUTPUT_STALL: Same LLM output digest over and over
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, List, Optional

from .action_normalizer import NormalizedIteration


class PatternType(Enum):
    EXACT = auto()
    CYCLIC = auto()
    DRIFT = auto()
    OUTPUT_STALL = auto()


@dataclass
class PatternMatch:
    """A detected loop pattern."""
    pattern_type: PatternType
    confidence: float
    cycle_length: int
    repeat_count: int
    first_seen_iteration: int
    latest_iteration: int
    description: str

    @property
    def severity(self) -> float:
        if self.cycle_length == 0:
            return self.confidence * self.repeat_count
        return self.confidence * self.repeat_count / self.cycle_length


class PatternAnalyzer:
    """Analyzes a stream of normalized iterations for loop patterns."""

    def __init__(self, history_window: int = 200):
        self._history: List[NormalizedIteration] = []
        self._history_window = history_window
        self._sig_counts: Dict[str, int] = {}

    def add_iteration(self, iteration: NormalizedIteration) -> None:
        self._history.append(iteration)
        sig = iteration.signature()
        self._sig_counts[sig] = self._sig_counts.get(sig, 0) + 1
        if len(self._history) > self._history_window:
            self._history = self._history[-self._history_window:]

    def analyze(self) -> List[PatternMatch]:
        matches: List[PatternMatch] = []
        if len(self._history) < 2:
            return matches
        for detector in (self._detect_exact, self._detect_cyclic,
                         self._detect_drift, self._detect_output_stall):
            result = detector()
            if result:
                matches.append(result)
        return matches

    def get_signature_count(self, sig: str) -> int:
        return self._sig_counts.get(sig, 0)

    @property
    def history_length(self) -> int:
        return len(self._history)

    def _detect_exact(self) -> Optional[PatternMatch]:
        """Detect consecutive identical iteration signatures."""
        if len(self._history) < 2:
            return None
        sigs = [it.signature() for it in self._history]
        current_sig = sigs[-1]
        count = 0
        for sig in reversed(sigs):
            if sig == current_sig:
                count += 1
            else:
                break
        if count < 2:
            return None
        last_iter = self._history[-1].iteration_number
        first_iter = last_iter - count + 1
        confidence = min(1.0, count / 6.0)
        return PatternMatch(
            PatternType.EXACT, confidence, 1, count,
            first_iter, last_iter,
            f"EXACT loop: same signature repeated {count} times "
            f"(iterations {first_iter}-{last_iter})",
        )

    def _detect_cyclic(self) -> Optional[PatternMatch]:
        """Detect cyclic patterns like A→B→A→B→A→B."""
        if len(self._history) < 6:
            return None
        sigs = [it.signature() for it in self._history]
        n = len(sigs)
        best: Optional[PatternMatch] = None
        for cycle_len in range(2, n // 3 + 1):
            window_size = cycle_len * 3
            if window_size > n:
                continue
            window = sigs[-window_size:]
            base = window[:cycle_len]
            is_cycle = all(
                window[i * cycle_len:(i + 1) * cycle_len] == base
                for i in range(1, 3)
            )
            if is_cycle:
                # Count repeats by walking backward from the window start
                # The cycle is confirmed in the last window_size sigs.
                # Count how many full cycles fit going backward from the window.
                total_repeats = 3  # already confirmed 3 cycles in the window
                window_start = n - window_size
                # Check if the cycle extends before the window
                check_idx = window_start - cycle_len
                while check_idx >= 0:
                    if sigs[check_idx:check_idx + cycle_len] == base:
                        total_repeats += 1
                        check_idx -= cycle_len
                    else:
                        break
                if total_repeats >= 3:
                    confidence = min(1.0, total_repeats / 5.0)
                    last_iter = self._history[-1].iteration_number
                    first_iter = last_iter - (total_repeats * cycle_len) + 1
                    match = PatternMatch(
                        PatternType.CYCLIC, confidence, cycle_len,
                        total_repeats, first_iter, last_iter,
                        f"CYCLIC loop: {cycle_len}-step cycle repeated "
                        f"{total_repeats} times (iterations {first_iter}-{last_iter})",
                    )
                    if best is None or match.severity > best.severity:
                        best = match
        return best

    def _detect_drift(self) -> Optional[PatternMatch]:
        """Detect gradual degradation: fewer unique actions per iteration."""
        if len(self._history) < 5:
            return None
        recent = self._history[-5:]
        counts = [len(it.actions) for it in recent]
        is_decreasing = all(counts[i] > counts[i + 1] for i in range(len(counts) - 1))
        if not is_decreasing or counts[-1] > 2:
            return None
        return PatternMatch(
            PatternType.DRIFT, 0.7, 0, 5,
            recent[0].iteration_number, recent[-1].iteration_number,
            f"DRIFT: action count decreasing ({counts[0]} -> {counts[-1]})",
        )

    def _detect_output_stall(self) -> Optional[PatternMatch]:
        """Detect when the LLM produces the same output repeatedly."""
        if len(self._history) < 3:
            return None
        recent = self._history[-5:]
        digests = [it.output_digest for it in recent if it.output_digest]
        if len(digests) < 3 or len(set(digests)) != 1:
            return None
        return PatternMatch(
            PatternType.OUTPUT_STALL, 0.9, 1, len(digests),
            recent[0].iteration_number, recent[-1].iteration_number,
            f"OUTPUT STALL: identical LLM output for {len(digests)} iterations",
        )
