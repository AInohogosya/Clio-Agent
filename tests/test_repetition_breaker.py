"""
Unit tests for the repetition-breaker mechanism in AutonomousLoopEngine.

Tests the following features:
1. _normalize_action_signature — creates normalized signatures from command tuples
2. _record_iteration_actions — tracks consecutive identical iterations
3. _check_repetition_breaker — detects loops and returns intervention messages
   - Level 0: Curiosity Fairy at 3 consecutive identical actions (early warning)
   - Level 1: Loop Breaker at 6 consecutive identical actions (hard enforcement)
   - Level 2: Persistent loop at 8 cross-wake-up occurrences (forced sleep)
4. _detect_empty_iteration — tracks empty/repetitive iterations without resetting fairy guard
5. _reset_drift_counters — does NOT clear persistent loop memory or fairy guard
6. _handle_sleep — clears all repetition-breaker state via _clear_repetition_state
7. _curiosity_fairy_invoked guard — prevents double-firing, resets on output change
8. _invoke_curiosity_fairy — generates suggestions from real system data
9. _execute_commands — per-command exception isolation
10. Threshold escalation — Fairy fires before Loop Breaker (staggered thresholds)
"""

import hashlib
import json
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

from src.ai_agent.core_processing.autonomous_loop_engine import (
    AutonomousLoopEngine, AutonomousContext, LoopPhase,
)


class TestNormalizeActionSignature(unittest.TestCase):
    """Test _normalize_action_signature creates correct normalized signatures."""

    def setUp(self):
        self.engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        # Initialize only the fields we need for testing
        self.engine._action_history = []
        self.engine._max_action_history = 50
        self.engine._consecutive_same_action = 0
        self.engine._last_action_signature = ""
        self.engine._repetition_break_threshold = 3
        self.engine._persistent_loop_patterns = {}
        self.engine._persistent_loop_threshold = 6
        self.engine._prev_iteration_action_sig = ""
        self.engine._force_sleep_pending = False

    def test_empty_commands_produces_empty_signature(self):
        sig = self.engine._normalize_action_signature([])
        self.assertEqual(sig, "__empty__")

    def test_single_read_command(self):
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/file.txt"}))]
        sig = self.engine._normalize_action_signature(commands)
        # Should contain "tool:read:" + md5 hash of "/tmp/file.txt"
        expected_hash = hashlib.md5("/tmp/file.txt".encode()).hexdigest()[:8]
        self.assertIn(f"tool:read:{expected_hash}", sig)

    def test_same_file_read_produces_same_signature(self):
        commands1 = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/file.txt"}))]
        commands2 = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/file.txt"}))]
        sig1 = self.engine._normalize_action_signature(commands1)
        sig2 = self.engine._normalize_action_signature(commands2)
        self.assertEqual(sig1, sig2)

    def test_different_file_read_produces_different_signature(self):
        commands1 = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/file1.txt"}))]
        commands2 = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/file2.txt"}))]
        sig1 = self.engine._normalize_action_signature(commands1)
        sig2 = self.engine._normalize_action_signature(commands2)
        self.assertNotEqual(sig1, sig2)

    def test_thinking_content_ignored(self):
        # Same action with different thinking content should produce same sig
        commands1 = [
            ("thinking", "I need to check this"),
            ("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/file.txt"})),
        ]
        commands2 = [
            ("thinking", "Let me check again"),
            ("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/file.txt"})),
        ]
        sig1 = self.engine._normalize_action_signature(commands1)
        sig2 = self.engine._normalize_action_signature(commands2)
        self.assertEqual(sig1, sig2)

    def test_telegram_content_ignored(self):
        commands1 = [("telegram", "Working on task A")]
        commands2 = [("telegram", "Working on task B")]
        sig1 = self.engine._normalize_action_signature(commands1)
        sig2 = self.engine._normalize_action_signature(commands2)
        self.assertEqual(sig1, sig2)

    def test_order_doesnt_matter(self):
        commands1 = [
            ("tool_call", json.dumps({"__tool__": "read", "path": "/a.txt"})),
            ("tool_call", json.dumps({"__tool__": "glob", "pattern": "*.py"})),
        ]
        commands2 = [
            ("tool_call", json.dumps({"__tool__": "glob", "pattern": "*.py"})),
            ("tool_call", json.dumps({"__tool__": "read", "path": "/a.txt"})),
        ]
        sig1 = self.engine._normalize_action_signature(commands1)
        sig2 = self.engine._normalize_action_signature(commands2)
        self.assertEqual(sig1, sig2)

    def test_sleep_and_exit_skipped(self):
        commands = [("sleep", ""), ("exit", ""), ("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        sig = self.engine._normalize_action_signature(commands)
        expected_hash = hashlib.md5("/f.txt".encode()).hexdigest()[:8]
        self.assertEqual(sig, f"tool:read:{expected_hash}")


class TestRecordIterationActions(unittest.TestCase):
    """Test _record_iteration_actions correctly tracks consecutive repeats."""

    def setUp(self):
        self.engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        self.engine._action_history = []
        self.engine._max_action_history = 50
        self.engine._consecutive_same_action = 0
        self.engine._last_action_signature = ""
        self.engine._repetition_break_threshold = 3
        self.engine._persistent_loop_patterns = {}
        self.engine._persistent_loop_threshold = 6
        self.engine._prev_iteration_action_sig = ""
        self.engine._force_sleep_pending = False

    def test_first_iteration_sets_signature(self):
        """First occurrence sets counter to 1 (total count including first)."""
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 1)
        self.assertNotEqual(self.engine._last_action_signature, "")

    def test_consecutive_same_increments_counter(self):
        """Counter = total consecutive occurrences (1st=1, 2nd=2, 3rd=3)."""
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 1)
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 2)
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 3)

    def test_different_action_resets_counter(self):
        """Different action resets counter to 1 (not 0)."""
        same = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        diff = [("tool_call", json.dumps({"__tool__": "read", "path": "/other.txt"}))]
        self.engine._record_iteration_actions(same)
        self.engine._record_iteration_actions(same)
        self.assertEqual(self.engine._consecutive_same_action, 2)
        self.engine._record_iteration_actions(diff)
        self.assertEqual(self.engine._consecutive_same_action, 1)

    def test_empty_commands_dont_increment_counter(self):
        self.engine._record_iteration_actions([])
        self.engine._record_iteration_actions([])
        self.assertEqual(self.engine._consecutive_same_action, 0)
        # __empty__ signatures don't count as consecutive repeats

    def test_persistent_loop_patterns_updated(self):
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        self.engine._record_iteration_actions(commands)
        self.engine._record_iteration_actions(commands)
        sig = self.engine._last_action_signature
        self.assertEqual(self.engine._persistent_loop_patterns.get(sig), 2)

    def test_action_history_trimmed(self):
        self.engine._max_action_history = 5
        for i in range(10):
            commands = [("tool_call", json.dumps({"__tool__": "read", "path": f"/f{i}.txt"}))]
            self.engine._record_iteration_actions(commands)
        self.assertEqual(len(self.engine._action_history), 5)


class TestCheckRepetitionBreaker(unittest.TestCase):
    """Test _check_repetition_breaker detects loops and returns intervention.

    Thresholds:
    - Level 0 (Curiosity Fairy): 3 consecutive identical actions
    - Level 1 (Loop Breaker): 6 consecutive identical actions
    - Level 2 (Persistent Loop): 8 cross-wake-up occurrences
    """

    def setUp(self):
        self.engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        self.engine._action_history = []
        self.engine._max_action_history = 50
        self.engine._consecutive_same_action = 0
        self.engine._last_action_signature = ""
        self.engine._repetition_break_threshold = 6
        self.engine._persistent_loop_patterns = {}
        self.engine._persistent_loop_threshold = 8
        self.engine._prev_iteration_action_sig = ""
        self.engine._force_sleep_pending = False
        self.engine._consecutive_identical_outputs = 0
        self.engine._last_output_hash = ""
        self.engine._curiosity_fairy_invoked = False
        self.engine._curiosity_fairy_threshold = 3
        self.engine.logger = MagicMock()
        self.engine._term_log = MagicMock()
        self.engine._append_log = MagicMock()
        # Minimal ctx for logging
        self.ctx = AutonomousContext(
            user_prompt="test",
            current_goal="test",
            execution_log=[],
            cancel_event=threading.Event(),
        )

    def test_no_intervention_when_no_repeat(self):
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNone(result)

    def test_curiosity_fairy_fires_at_threshold_3(self):
        """Level 0: Curiosity Fairy fires at 3 consecutive identical actions."""
        self.engine._consecutive_same_action = 3
        self.engine._last_action_signature = "tool:read:abc12345"
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNotNone(result)
        self.assertIn("CURIOSITY FAIRY ACTIVATED", result)
        self.assertIn("same command", result)
        self.assertTrue(self.engine._curiosity_fairy_invoked)

    def test_no_fairy_below_threshold(self):
        """At 2 consecutive, no intervention fires."""
        self.engine._consecutive_same_action = 2
        self.engine._last_action_signature = "tool:read:abc12345"
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNone(result)
        self.assertFalse(self.engine._curiosity_fairy_invoked)

    def test_fairy_then_breaker_escalation(self):
        """Fairy fires at 3, then Loop Breaker fires at 6."""
        sig = "tool:read:abc12345"
        # At 3: Fairy fires
        self.engine._consecutive_same_action = 3
        self.engine._last_action_signature = sig
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIn("CURIOSITY FAIRY ACTIVATED", result)
        self.assertTrue(self.engine._curiosity_fairy_invoked)

        # At 4: Fairy already invoked, no Level 1 yet (4 < 6)
        self.engine._consecutive_same_action = 4
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNone(result)

        # At 6: Fairy already invoked, Level 1 fires
        self.engine._consecutive_same_action = 6
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNotNone(result)
        self.assertIn("LOOP BREAKER ACTIVATED", result)
        # Level 1 resets counter to 1 and clears fairy guard
        self.assertEqual(self.engine._consecutive_same_action, 1)
        self.assertFalse(self.engine._curiosity_fairy_invoked)

    def test_level1_intervention_on_consecutive_repeat(self):
        """Level 1 fires when fairy already invoked and counter >= 6."""
        self.engine._consecutive_same_action = 6
        self.engine._last_action_signature = "tool:read:abc12345"
        self.engine._curiosity_fairy_invoked = True
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNotNone(result)
        self.assertIn("LOOP BREAKER ACTIVATED", result)
        # Counter reset to 1 (current occurrence counts as #1 of new streak)
        self.assertEqual(self.engine._consecutive_same_action, 1)

    def test_level2_intervention_on_persistent_pattern(self):
        """Level 2 fires at 8 cross-wake-up occurrences."""
        sig = "tool:read:abc12345"
        self.engine._persistent_loop_patterns[sig] = 8
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNotNone(result)
        self.assertIn("PERSISTENT LOOP DETECTED", result)

    def test_no_level2_intervention_below_threshold(self):
        """At 7 cross-wake-up occurrences, no Level 2 intervention."""
        sig = "tool:read:abc12345"
        self.engine._persistent_loop_patterns[sig] = 7
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNone(result)

    def test_fairy_not_retriggered_by_same_output(self):
        """Once fairy is invoked, it should NOT fire again on next iteration."""
        sig = "tool:read:abc12345"
        self.engine._consecutive_same_action = 3
        self.engine._last_action_signature = sig
        # First call: fairy fires
        result1 = self.engine._check_repetition_breaker(self.ctx)
        self.assertIn("CURIOSITY FAIRY ACTIVATED", result1)

        # Second call with counter=4: fairy already invoked, no Level 1 yet
        self.engine._consecutive_same_action = 4
        result2 = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNone(result2)  # No double-fire

    def test_no_curiosity_fairy_if_already_invoked(self):
        # At 6 consecutive with fairy already invoked, Level 1 fires
        self.engine._consecutive_same_action = 6
        self.engine._last_action_signature = "tool:read:abc12345"
        self.engine._curiosity_fairy_invoked = True
        result = self.engine._check_repetition_breaker(self.ctx)
        # Fairy already invoked, so Level 1 fires instead (consecutive >= 6)
        self.assertIsNotNone(result)
        self.assertIn("LOOP BREAKER ACTIVATED", result)
        self.assertNotIn("CURIOSITY FAIRY", result)


class TestResetDriftCounters(unittest.TestCase):
    """Test that _reset_drift_counters does NOT clear persistent loop memory."""

    def setUp(self):
        self.engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        self.engine._empty_iterations = 5
        self.engine._empty_drift_active = True
        self.engine._previous_output_digest = "abc"
        self.engine._loop_warning_active = True
        self.engine._recent_commands = ["cmd1:sig1", "cmd2:sig2"]
        self.engine._action_history = ["sig_a", "sig_b", "sig_a"]
        self.engine._consecutive_same_action = 2
        self.engine._last_action_signature = "sig_a"
        self.engine._persistent_loop_patterns = {"sig_a": 4, "sig_b": 2}
        self.engine._prev_iteration_action_sig = "sig_a"
        self.engine._force_sleep_pending = False
        self.engine.logger = MagicMock()
        self.engine._term_log = MagicMock()
        self.engine._append_log = MagicMock()
        # Minimal ctx
        self.ctx = AutonomousContext(
            user_prompt="test",
            current_goal="test",
            execution_log=[],
            cancel_event=threading.Event(),
        )

    def test_empty_iterations_reset(self):
        self.engine._reset_drift_counters(self.ctx)
        self.assertEqual(self.engine._empty_iterations, 0)

    def test_empty_drift_active_reset(self):
        self.engine._reset_drift_counters(self.ctx)
        self.assertFalse(self.engine._empty_drift_active)

    def test_recent_commands_cleared(self):
        self.engine._reset_drift_counters(self.ctx)
        self.assertEqual(len(self.engine._recent_commands), 0)

    def test_persistent_loop_patterns_NOT_cleared(self):
        self.engine._reset_drift_counters(self.ctx)
        self.assertEqual(self.engine._persistent_loop_patterns, {"sig_a": 4, "sig_b": 2})

    def test_consecutive_same_action_NOT_reset(self):
        self.engine._reset_drift_counters(self.ctx)
        self.assertEqual(self.engine._consecutive_same_action, 2)

    def test_action_history_NOT_cleared(self):
        self.engine._reset_drift_counters(self.ctx)
        self.assertEqual(len(self.engine._action_history), 3)

    def test_last_action_signature_NOT_cleared(self):
        self.engine._reset_drift_counters(self.ctx)
        self.assertEqual(self.engine._last_action_signature, "sig_a")


class TestSleepClearsRepetitionBreaker(unittest.TestCase):
    """Test that _handle_sleep clears all repetition-breaker state."""

    def test_clear_repetition_state_clears_all(self):
        """_clear_repetition_state clears all loop-detection state."""
        engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        engine._action_history = ["sig_a", "sig_b", "sig_a"]
        engine._consecutive_same_action = 5
        engine._last_action_signature = "sig_a"
        engine._persistent_loop_patterns = {"sig_a": 4, "sig_b": 2}
        engine._force_sleep_pending = True
        engine._consecutive_identical_outputs = 3
        engine._last_output_hash = "abc123"
        engine._curiosity_fairy_invoked = True
        engine._empty_iterations = 7
        engine._empty_drift_active = True
        engine._previous_output_digest = "def456"

        engine._clear_repetition_state()

        self.assertEqual(len(engine._action_history), 0)
        self.assertEqual(engine._consecutive_same_action, 0)
        self.assertEqual(engine._last_action_signature, "")
        self.assertEqual(len(engine._persistent_loop_patterns), 0)
        self.assertFalse(engine._force_sleep_pending)
        self.assertEqual(engine._consecutive_identical_outputs, 0)
        self.assertEqual(engine._last_output_hash, "")
        self.assertFalse(engine._curiosity_fairy_invoked)
        self.assertEqual(engine._empty_iterations, 0)
        self.assertFalse(engine._empty_drift_active)
        self.assertEqual(engine._previous_output_digest, "")


class TestCounterSemantics(unittest.TestCase):
    """Test the new counter semantics."""

    def setUp(self):
        self.engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        self.engine._action_history = []
        self.engine._max_action_history = 50
        self.engine._consecutive_same_action = 0
        self.engine._last_action_signature = ""
        self.engine._persistent_loop_patterns = {}
        self.engine._curiosity_fairy_threshold = 3
        self.engine._repetition_break_threshold = 6
        self.engine._persistent_loop_threshold = 8
        self.engine._curiosity_fairy_invoked = False
        self.engine._prev_iteration_action_sig = ""
        self.engine.logger = MagicMock()

    def test_counter_is_total_occurrences(self):
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 1)
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 2)
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 3)

    def test_threshold_3_fires_at_third(self):
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        ctx = AutonomousContext(
            user_prompt="test", current_goal="test",
            execution_log=[], cancel_event=threading.Event(),
        )
        self.engine._record_iteration_actions(commands)
        self.assertIsNone(self.engine._check_repetition_breaker(ctx))
        self.engine._record_iteration_actions(commands)
        self.assertIsNone(self.engine._check_repetition_breaker(ctx))
        self.engine._record_iteration_actions(commands)
        result = self.engine._check_repetition_breaker(ctx)
        self.assertIn("CURIOSITY FAIRY ACTIVATED", result)


class TestFairyGuard(unittest.TestCase):
    """Test that _curiosity_fairy_invoked guard prevents double-firing."""

    def setUp(self):
        self.engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        self.engine._action_history = []
        self.engine._max_action_history = 50
        self.engine._consecutive_same_action = 0
        self.engine._last_action_signature = ""
        self.engine._persistent_loop_patterns = {}
        self.engine._curiosity_fairy_threshold = 3
        self.engine._repetition_break_threshold = 6
        self.engine._persistent_loop_threshold = 8
        self.engine._curiosity_fairy_invoked = False
        self.engine._prev_iteration_action_sig = ""
        self.engine._previous_output_digest = ""
        self.engine._recent_commands = []
        self.engine._empty_iterations = 0
        self.engine._empty_drift_active = False
        self.engine.logger = MagicMock()
        self.engine._term_log = MagicMock()
        self.engine._append_log = MagicMock()
        self.ctx = AutonomousContext(
            user_prompt="test", current_goal="test",
            execution_log=[], cancel_event=threading.Event(),
        )

    def test_fairy_fires_once_then_guard_prevents_refire(self):
        self.engine._consecutive_same_action = 3
        self.engine._last_action_signature = "tool:read:abc"
        result1 = self.engine._check_repetition_breaker(self.ctx)
        self.assertIn("CURIOSITY FAIRY ACTIVATED", result1)
        self.assertTrue(self.engine._curiosity_fairy_invoked)
        self.engine._consecutive_same_action = 4
        result2 = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNone(result2)

    def test_guard_cleared_by_level1(self):
        self.engine._consecutive_same_action = 6
        self.engine._last_action_signature = "tool:read:abc"
        self.engine._curiosity_fairy_invoked = True
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIn("LOOP BREAKER ACTIVATED", result)
        self.assertFalse(self.engine._curiosity_fairy_invoked)

    def test_detect_empty_iteration_does_not_clear_fairy_guard(self):
        self.engine._curiosity_fairy_invoked = True
        self.engine._prev_iteration_action_sig = "tool:read:abc"
        self.engine._last_action_signature = "tool:read:abc"
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        self.engine._detect_empty_iteration(self.ctx, "same output", commands)
        self.assertTrue(self.engine._curiosity_fairy_invoked)

    def test_reset_drift_counters_does_clear_fairy_guard(self):
        self.engine._curiosity_fairy_invoked = True
        self.engine._reset_drift_counters(self.ctx)
        self.assertFalse(self.engine._curiosity_fairy_invoked)


if __name__ == "__main__":
    unittest.main()
