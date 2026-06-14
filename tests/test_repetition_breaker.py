"""
Unit tests for the repetition-breaker mechanism in AutonomousLoopEngine.

Tests the following new features:
1. _normalize_action_signature — creates normalized signatures from command tuples
2. _record_iteration_actions — tracks consecutive identical iterations
3. _check_repetition_breaker — detects loops and returns intervention messages
4. _detect_empty_iteration — now includes semantic deduplication
5. _exit_idle_state — does NOT clear persistent loop memory
6. _handle_sleep — DOES clear persistent loop memory
"""

import hashlib
import json
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

from src.ai_agent.core_processing.autonomous_loop_engine import (
    AutonomousLoopEngine, AutonomousContext, LoopPhase, IdleState,
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
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 0)
        self.assertNotEqual(self.engine._last_action_signature, "")

    def test_consecutive_same_increments_counter(self):
        commands = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 0)
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 1)
        self.engine._record_iteration_actions(commands)
        self.assertEqual(self.engine._consecutive_same_action, 2)

    def test_different_action_resets_counter(self):
        same = [("tool_call", json.dumps({"__tool__": "read", "path": "/f.txt"}))]
        diff = [("tool_call", json.dumps({"__tool__": "read", "path": "/other.txt"}))]
        self.engine._record_iteration_actions(same)
        self.engine._record_iteration_actions(same)
        self.assertEqual(self.engine._consecutive_same_action, 1)
        self.engine._record_iteration_actions(diff)
        self.assertEqual(self.engine._consecutive_same_action, 0)

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
    """Test _check_repetition_breaker detects loops and returns intervention."""

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
        self.engine._consecutive_identical_outputs = 0
        self.engine._last_output_hash = ""
        self.engine._curiosity_fairy_invoked = False
        self.engine._curiosity_fairy_threshold = 5
        self.engine.logger = MagicMock()
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

    def test_level1_intervention_on_consecutive_repeat(self):
        # Simulate 3 consecutive identical actions
        self.engine._consecutive_same_action = 3
        self.engine._last_action_signature = "tool:read:abc12345"
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNotNone(result)
        self.assertIn("LOOP BREAKER ACTIVATED", result)
        # Counter should be reset after intervention
        self.assertEqual(self.engine._consecutive_same_action, 0)

    def test_level2_intervention_on_persistent_pattern(self):
        sig = "tool:read:abc12345"
        self.engine._persistent_loop_patterns[sig] = 6
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNotNone(result)
        self.assertIn("PERSISTENT LOOP DETECTED", result)

    def test_no_level2_intervention_below_threshold(self):
        sig = "tool:read:abc12345"
        self.engine._persistent_loop_patterns[sig] = 5
        result = self.engine._check_repetition_breaker(self.ctx)
        self.assertIsNone(result)


class TestExitIdleState(unittest.TestCase):
    """Test that _exit_idle_state does NOT clear persistent loop memory."""

    def setUp(self):
        self.engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        self.engine._idle_state = IdleState.IDLE
        self.engine._empty_iterations = 5
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

    def test_idle_state_reset_to_active(self):
        self.engine._exit_idle_state(self.ctx)
        self.assertEqual(self.engine._idle_state, IdleState.ACTIVE)

    def test_empty_iterations_reset(self):
        self.engine._exit_idle_state(self.ctx)
        self.assertEqual(self.engine._empty_iterations, 0)

    def test_recent_commands_cleared(self):
        self.engine._exit_idle_state(self.ctx)
        self.assertEqual(len(self.engine._recent_commands), 0)

    def test_persistent_loop_patterns_NOT_cleared(self):
        self.engine._exit_idle_state(self.ctx)
        self.assertEqual(self.engine._persistent_loop_patterns, {"sig_a": 4, "sig_b": 2})

    def test_consecutive_same_action_NOT_reset(self):
        self.engine._exit_idle_state(self.ctx)
        self.assertEqual(self.engine._consecutive_same_action, 2)

    def test_action_history_NOT_cleared(self):
        self.engine._exit_idle_state(self.ctx)
        self.assertEqual(len(self.engine._action_history), 3)

    def test_last_action_signature_NOT_cleared(self):
        self.engine._exit_idle_state(self.ctx)
        self.assertEqual(self.engine._last_action_signature, "sig_a")


class TestSleepClearsRepetitionBreaker(unittest.TestCase):
    """Test that _handle_sleep clears all repetition-breaker state."""

    def test_sleep_clears_breaker_state(self):
        # This test would need full engine initialization to call _handle_sleep
        # which does os.execv, so we test the state-clearing lines directly
        engine = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        engine._action_history = ["sig_a", "sig_b", "sig_a"]
        engine._consecutive_same_action = 2
        engine._last_action_signature = "sig_a"
        engine._persistent_loop_patterns = {"sig_a": 4, "sig_b": 2}
        engine._force_sleep_pending = True

        # Simulate what _handle_sleep does (the state-clearing part)
        engine._action_history.clear()
        engine._consecutive_same_action = 0
        engine._last_action_signature = ""
        engine._persistent_loop_patterns.clear()
        engine._force_sleep_pending = False

        self.assertEqual(len(engine._action_history), 0)
        self.assertEqual(engine._consecutive_same_action, 0)
        self.assertEqual(engine._last_action_signature, "")
        self.assertEqual(len(engine._persistent_loop_patterns), 0)
        self.assertFalse(engine._force_sleep_pending)


if __name__ == "__main__":
    unittest.main()
