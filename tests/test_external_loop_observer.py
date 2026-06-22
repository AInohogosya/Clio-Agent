"""Comprehensive tests for the External Loop Observer system."""

import json
import tempfile
import unittest
from pathlib import Path

import sys
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_SRC_PATH = str(_PROJECT_ROOT / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

from ai_agent.core_processing.external_loop_observer.action_normalizer import (
    ActionNormalizer, NormalizedAction,
)
from ai_agent.core_processing.external_loop_observer.pattern_analyzer import (
    PatternAnalyzer, PatternMatch, PatternType,
)
from ai_agent.core_processing.external_loop_observer.intervention import (
    Intervention, InterventionLevel, decide_intervention,
)
from ai_agent.core_processing.external_loop_observer.observer import (
    ExternalObserver, ObserverConfig,
)


def _read_cmd(path):
    return ("tool_call", json.dumps({"__tool__": "read", "path": path}))


def _grep_cmd(pattern, path="."):
    return ("tool_call", json.dumps({"__tool__": "grep", "pattern": pattern, "path": path}))


def _make_iteration(iter_num, commands, output=""):
    norm = ActionNormalizer()
    return norm.normalize_iteration(iter_num, commands, output)


# === 1. ActionNormalizer tests ===

class TestNormalizedAction(unittest.TestCase):
    def test_equality(self):
        a1 = NormalizedAction("read", "abc12345", "read", "0", "read(file.py)")
        a2 = NormalizedAction("read", "abc12345", "read", "0", "read(file.py)")
        self.assertEqual(a1, a2)

    def test_inequality(self):
        a1 = NormalizedAction("read", "abc", "read", "0", "")
        a2 = NormalizedAction("read", "xyz", "read", "0", "")
        self.assertNotEqual(a1, a2)

    def test_signature(self):
        a = NormalizedAction("read", "abc", "read", "0", "")
        self.assertEqual(a.signature(), "read:read:abc:0")

    def test_hashable(self):
        a = NormalizedAction("read", "abc", "read", "0", "")
        self.assertIn(a, {a})


class TestActionNormalizerToolCalls(unittest.TestCase):
    def setUp(self):
        self.norm = ActionNormalizer()

    def test_read_tool(self):
        arg = json.dumps({"__tool__": "read", "path": "/tmp/test.py"})
        r = self.norm._normalize_single("tool_call", arg)
        self.assertIsNotNone(r)
        self.assertEqual(r.tool, "read")

    def test_grep_tool(self):
        arg = json.dumps({"__tool__": "grep", "pattern": "TODO", "path": "src/"})
        r = self.norm._normalize_single("tool_call", arg)
        self.assertEqual(r.operation, "search")

    def test_bash_tool(self):
        arg = json.dumps({"__tool__": "bash", "command": "ls -la"})
        r = self.norm._normalize_single("tool_call", arg)
        self.assertEqual(r.tool, "bash")

    def test_thinking_excluded(self):
        arg = json.dumps({"__tool__": "thinking", "text": "x"})
        self.assertIsNone(self.norm._normalize_single("tool_call", arg))

    def test_same_file_same_hash(self):
        arg = json.dumps({"__tool__": "read", "path": "/tmp/a.py"})
        r1 = self.norm._normalize_single("tool_call", arg)
        r2 = self.norm._normalize_single("tool_call", arg)
        self.assertEqual(r1.target_hash, r2.target_hash)

    def test_diff_file_diff_hash(self):
        a1 = json.dumps({"__tool__": "read", "path": "/tmp/a.py"})
        a2 = json.dumps({"__tool__": "read", "path": "/tmp/b.py"})
        r1 = self.norm._normalize_single("tool_call", a1)
        r2 = self.norm._normalize_single("tool_call", a2)
        self.assertNotEqual(r1.target_hash, r2.target_hash)


class TestActionNormalizerMessaging(unittest.TestCase):
    def setUp(self):
        self.norm = ActionNormalizer()

    def test_telegram(self):
        r = self.norm._normalize_single("telegram", "Hello!")
        self.assertEqual(r.tool, "telegram")
        self.assertEqual(r.operation, "message")

    def test_same_msg_same_hash(self):
        r1 = self.norm._normalize_single("telegram", "msg")
        r2 = self.norm._normalize_single("telegram", "msg")
        self.assertEqual(r1.target_hash, r2.target_hash)

    def test_diff_msg_diff_hash(self):
        r1 = self.norm._normalize_single("telegram", "msg A")
        r2 = self.norm._normalize_single("telegram", "msg B")
        self.assertNotEqual(r1.target_hash, r2.target_hash)


class TestActionNormalizerLifecycle(unittest.TestCase):
    def setUp(self):
        self.norm = ActionNormalizer()

    def test_sleep(self):
        r = self.norm._normalize_single("sleep", "")
        self.assertEqual(r.tool, "lifecycle")
        self.assertEqual(r.operation, "sleep")

    def test_exit(self):
        r = self.norm._normalize_single("exit", "")
        self.assertEqual(r.tool, "lifecycle")
        self.assertEqual(r.operation, "exit")


class TestNormalizeIteration(unittest.TestCase):
    def setUp(self):
        self.norm = ActionNormalizer()

    def test_multiple_commands(self):
        cmds = [
            ("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/a.py"})),
            ("tool_call", json.dumps({"__tool__": "grep", "pattern": "TODO", "path": "."})),
        ]
        r = self.norm.normalize_iteration(1, cmds)
        self.assertEqual(len(r.actions), 2)

    def test_thinking_excluded(self):
        cmds = [
            ("thinking", "thought"),
            ("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/a.py"})),
        ]
        r = self.norm.normalize_iteration(1, cmds)
        self.assertEqual(len(r.actions), 1)

    def test_output_digest(self):
        cmds = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/a.py"}))]
        r = self.norm.normalize_iteration(1, cmds, output_text="output")
        self.assertTrue(len(r.output_digest) > 0)

    def test_sig_deterministic(self):
        cmds = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/a.py"}))]
        r1 = self.norm.normalize_iteration(1, cmds)
        r2 = self.norm.normalize_iteration(1, cmds)
        self.assertEqual(r1.signature(), r2.signature())

    def test_diff_cmds_diff_sig(self):
        c1 = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/a.py"}))]
        c2 = [("tool_call", json.dumps({"__tool__": "read", "path": "/tmp/b.py"}))]
        r1 = self.norm.normalize_iteration(1, c1)
        r2 = self.norm.normalize_iteration(2, c2)
        self.assertNotEqual(r1.signature(), r2.signature())


# === 2. PatternAnalyzer tests ===

class TestPatternAnalyzerExact(unittest.TestCase):
    def setUp(self):
        self.analyzer = PatternAnalyzer()

    def test_no_pattern_with_few(self):
        for i in range(3):
            self.analyzer.add_iteration(_make_iteration(i + 1, [_read_cmd(f"/tmp/f{i}.py")]))
        self.assertEqual(len(self.analyzer.analyze()), 0)

    def test_exact_loop_detected(self):
        for i in range(5):
            self.analyzer.add_iteration(_make_iteration(i + 1, [_read_cmd("/tmp/same.py")]))
        matches = self.analyzer.analyze()
        exact = [m for m in matches if m.pattern_type == PatternType.EXACT]
        self.assertTrue(len(exact) >= 1)
        self.assertEqual(exact[0].repeat_count, 5)

    def test_exact_confidence_increases(self):
        for i in range(8):
            self.analyzer.add_iteration(_make_iteration(i + 1, [_read_cmd("/tmp/same.py")]))
        matches = self.analyzer.analyze()
        exact = [m for m in matches if m.pattern_type == PatternType.EXACT][0]
        self.assertGreater(exact.confidence, 0.8)

    def test_two_repeats_detected(self):
        for i in range(2):
            self.analyzer.add_iteration(_make_iteration(i + 1, [_read_cmd("/tmp/same.py")]))
        matches = self.analyzer.analyze()
        exact = [m for m in matches if m.pattern_type == PatternType.EXACT]
        self.assertTrue(len(exact) >= 1)


class TestPatternAnalyzerCyclic(unittest.TestCase):
    def setUp(self):
        self.analyzer = PatternAnalyzer()

    def test_cyclic_detected(self):
        for i in range(9):
            path = "/tmp/a.py" if i % 2 == 0 else "/tmp/b.py"
            self.analyzer.add_iteration(_make_iteration(i + 1, [_read_cmd(path)]))
        matches = self.analyzer.analyze()
        cyclic = [m for m in matches if m.pattern_type == PatternType.CYCLIC]
        self.assertTrue(len(cyclic) >= 1)
        self.assertEqual(cyclic[0].cycle_length, 2)

    def test_three_step_cycle(self):
        files = ["/tmp/a.py", "/tmp/b.py", "/tmp/c.py"]
        for i in range(12):
            self.analyzer.add_iteration(_make_iteration(i + 1, [_read_cmd(files[i % 3])]))
        matches = self.analyzer.analyze()
        cyclic = [m for m in matches if m.pattern_type == PatternType.CYCLIC]
        self.assertTrue(len(cyclic) >= 1)
        self.assertEqual(cyclic[0].cycle_length, 3)

    def test_no_cycle_with_unique(self):
        for i in range(10):
            self.analyzer.add_iteration(_make_iteration(i + 1, [_read_cmd(f"/tmp/u{i}.py")]))
        matches = self.analyzer.analyze()
        cyclic = [m for m in matches if m.pattern_type == PatternType.CYCLIC]
        self.assertEqual(len(cyclic), 0)


class TestPatternAnalyzerDrift(unittest.TestCase):
    def setUp(self):
        self.analyzer = PatternAnalyzer()

    def test_drift_detected(self):
        for i in range(5):
            paths = [f"/tmp/f{j}.py" for j in range(4 - i)]
            cmds = [_read_cmd(p) for p in paths]
            self.analyzer.add_iteration(_make_iteration(i + 1, cmds))
        matches = self.analyzer.analyze()
        drift = [m for m in matches if m.pattern_type == PatternType.DRIFT]
        self.assertTrue(len(drift) >= 1)

    def test_no_drift_stable(self):
        for i in range(5):
            cmds = [_read_cmd(f"/tmp/f{j}.py") for j in range(3)]
            self.analyzer.add_iteration(_make_iteration(i + 1, cmds))
        matches = self.analyzer.analyze()
        drift = [m for m in matches if m.pattern_type == PatternType.DRIFT]
        self.assertEqual(len(drift), 0)


class TestPatternAnalyzerOutputStall(unittest.TestCase):
    def setUp(self):
        self.analyzer = PatternAnalyzer()

    def test_stall_detected(self):
        for i in range(5):
            self.analyzer.add_iteration(
                _make_iteration(i + 1, [_read_cmd("/tmp/a.py")], output="same"))
        matches = self.analyzer.analyze()
        stalls = [m for m in matches if m.pattern_type == PatternType.OUTPUT_STALL]
        self.assertTrue(len(stalls) >= 1)
        self.assertEqual(stalls[0].confidence, 0.9)

    def test_no_stall_varying(self):
        for i in range(5):
            self.analyzer.add_iteration(
                _make_iteration(i + 1, [_read_cmd("/tmp/a.py")], output=f"out {i}"))
        matches = self.analyzer.analyze()
        stalls = [m for m in matches if m.pattern_type == PatternType.OUTPUT_STALL]
        self.assertEqual(len(stalls), 0)


# === 3. Intervention tests ===

class TestDecideIntervention(unittest.TestCase):
    def test_no_patterns(self):
        inv = decide_intervention([])
        self.assertEqual(inv.level, InterventionLevel.LOG)

    def test_low_severity(self):
        p = PatternMatch(PatternType.EXACT, 0.1, 1, 2, 1, 2, "minor")
        inv = decide_intervention([p])
        self.assertEqual(inv.level, InterventionLevel.LOG)

    def test_nudge(self):
        p = PatternMatch(PatternType.EXACT, 0.5, 1, 3, 1, 3, "moderate")
        inv = decide_intervention([p])
        self.assertGreaterEqual(inv.level, InterventionLevel.NUDGE)

    def test_alert(self):
        p = PatternMatch(PatternType.EXACT, 0.9, 1, 7, 1, 7, "high")
        inv = decide_intervention([p])
        self.assertGreaterEqual(inv.level, InterventionLevel.ALERT)

    def test_interrupt(self):
        p = PatternMatch(PatternType.EXACT, 1.0, 1, 12, 1, 12, "extreme")
        inv = decide_intervention([p])
        self.assertGreaterEqual(inv.level, InterventionLevel.INTERRUPT)
        self.assertTrue(inv.force_sleep)

    def test_output_stall_min_nudge(self):
        p = PatternMatch(PatternType.OUTPUT_STALL, 0.9, 1, 3, 1, 3, "stall")
        inv = decide_intervention([p])
        self.assertGreaterEqual(inv.level, InterventionLevel.NUDGE)

    def test_payload_has_message(self):
        p = PatternMatch(PatternType.EXACT, 0.9, 1, 7, 1, 7, "test")
        inv = decide_intervention([p])
        self.assertIsNotNone(inv.control_file_payload)
        self.assertIn("agent_message", inv.control_file_payload)
        self.assertTrue(inv.control_file_payload["observer"])

    def test_highest_severity_wins(self):
        ps = [
            PatternMatch(PatternType.EXACT, 0.2, 1, 2, 1, 2, "minor"),
            PatternMatch(PatternType.CYCLIC, 0.95, 2, 8, 1, 8, "major"),
        ]
        inv = decide_intervention(ps)
        self.assertGreaterEqual(inv.level, InterventionLevel.ALERT)


# === 4. ExternalObserver integration tests ===

class TestExternalObserverBasic(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = ObserverConfig(
            control_file=".context/obs_ctl.json",
            state_file=".context/obs_state.json",
            enable_intervention=True,
            enable_persistence=True,
        )
        self.observer = ExternalObserver(
            project_root=Path(self.tmpdir), config=self.config)

    def test_init(self):
        self.assertEqual(self.observer.get_status()["total_iterations_observed"], 0)

    def test_single_no_loop(self):
        v = self.observer.on_iteration([_read_cmd("/tmp/a.py")], "out 1", 1)
        self.assertFalse(v.has_loop)

    def test_different_no_loop(self):
        for i in range(5):
            v = self.observer.on_iteration(
                [_read_cmd(f"/tmp/f{i}.py")], f"out {i}", i + 1)
        self.assertFalse(v.has_loop)

    def test_repeated_triggers_nudge(self):
        v = None
        for i in range(5):
            v = self.observer.on_iteration(
                [_read_cmd("/tmp/same.py")], f"out {i}", i + 1)
        self.assertTrue(v.has_loop)

    def test_force_sleep(self):
        v = None
        for i in range(15):
            v = self.observer.on_iteration(
                [_read_cmd("/tmp/same.py")], f"out {i}", i + 1)
        self.assertTrue(v.force_sleep)

    def test_reset(self):
        for i in range(5):
            self.observer.on_iteration(
                [_read_cmd("/tmp/same.py")], f"out {i}", i + 1)
        self.observer.reset()
        self.assertEqual(self.observer.get_status()["history_length"], 0)


class TestExternalObserverPersistence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = ObserverConfig(
            control_file=".context/obs_ctl.json",
            state_file=".context/obs_state.json",
            enable_persistence=True,
        )

    def test_state_survives(self):
        obs1 = ExternalObserver(project_root=Path(self.tmpdir), config=self.config)
        for i in range(5):
            obs1.on_iteration([_read_cmd("/tmp/same.py")], f"out {i}", i + 1)
        obs2 = ExternalObserver(project_root=Path(self.tmpdir), config=self.config)
        self.assertGreater(obs2.get_status()["total_iterations_observed"], 0)

    def test_sig_counts_preserved(self):
        obs1 = ExternalObserver(project_root=Path(self.tmpdir), config=self.config)
        for i in range(3):
            obs1.on_iteration([_read_cmd("/tmp/a.py")], f"out {i}", i + 1)
        obs2 = ExternalObserver(project_root=Path(self.tmpdir), config=self.config)
        norm = ActionNormalizer()
        it = norm.normalize_iteration(1, [_read_cmd("/tmp/a.py")])
        sig = it.signature()
        count = obs2._analyzer.get_signature_count(sig)
        self.assertGreaterEqual(count, 3)


class TestExternalObserverMixedCommands(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = ObserverConfig(
            control_file=".context/obs_ctl.json",
            state_file=".context/obs_state.json",
        )
        self.observer = ExternalObserver(
            project_root=Path(self.tmpdir), config=self.config)

    def test_varied_no_false_positive(self):
        workflow = [
            [_read_cmd("/tmp/config.py")],
            [_grep_cmd("TODO", "/tmp")],
            [_read_cmd("/tmp/utils.py")],
            [("tool_call", json.dumps({"__tool__": "edit", "path": "/tmp/out.py",
                                        "old_string": "x", "new_string": "y"}))],
            [_grep_cmd("import", "/tmp")],
            [("telegram", "Progress update")],
            [_read_cmd("/tmp/main.py")],
            [_grep_cmd("def ", "/tmp")],
        ]
        v = None
        for i, cmds in enumerate(workflow):
            v = self.observer.on_iteration(cmds, f"step {i}", i + 1)
        self.assertFalse(v.has_loop)

    def test_realistic_loop(self):
        v = None
        for i in range(8):
            cmds = [_read_cmd("/tmp/config.py"),
                    ("thinking", f"re-examining iter {i}")]
            v = self.observer.on_iteration(cmds, f"reading again {i}", i + 1)
        self.assertTrue(v.has_loop)


class TestExternalObserverCooldown(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = ObserverConfig(
            control_file=".context/obs_ctl.json",
            state_file=".context/obs_state.json",
        )
        self.observer = ExternalObserver(
            project_root=Path(self.tmpdir), config=self.config)

    def test_not_every_iteration(self):
        msgs = []
        for i in range(10):
            v = self.observer.on_iteration(
                [_read_cmd("/tmp/same.py")], f"out {i}", i + 1)
            if v.intervention_message:
                msgs.append(v.intervention_message)
        self.assertLess(len(msgs), 10)


class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = ObserverConfig(
            control_file=".context/obs_ctl.json",
            state_file=".context/obs_state.json",
        )
        self.observer = ExternalObserver(
            project_root=Path(self.tmpdir), config=self.config)

    def test_empty_commands(self):
        v = self.observer.on_iteration([], "no cmds", 1)
        self.assertFalse(v.has_loop)

    def test_observer_never_crashes(self):
        bad = [None, [("unknown_type", "garbage")],
               [("tool_call", "not json")]]
        for i, cmds in enumerate(bad):
            try:
                self.observer.on_iteration(cmds if cmds else [], f"bad {i}", i + 1)
            except Exception as e:
                self.fail(f"Observer raised on {cmds}: {e}")

    def test_rapid_iterations(self):
        v = None
        for i in range(50):
            v = self.observer.on_iteration(
                [_read_cmd(f"/tmp/f{i % 3}.py")], f"rapid {i}", i + 1)
        self.assertIsNotNone(v)


if __name__ == "__main__":
    unittest.main()
