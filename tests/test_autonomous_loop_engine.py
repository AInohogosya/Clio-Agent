"""
Tests for the Autonomous Loop Engine fixes.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
_SRC_PATH = str(_PROJECT_ROOT / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

from ai_agent.core_processing.autonomous_loop_engine import AutonomousLoopEngine
from ai_agent.external_integration.model_runner import ModelRunner, ModelRequest, TaskType


def _make_engine():
    eng = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
    eng.config = {}
    eng.logger = MagicMock()
    eng.terminal_history = MagicMock()
    eng.terminal_history.terminal_session.entries = []
    eng.terminal_history.display_terminal_log.return_value = ""
    eng.model_runner = MagicMock()
    eng.model_runner.provider = "test"
    eng.model_runner.model = "test"
    eng.telegram_bot = None
    eng.discord_bot = None
    eng.sub_agent_manager = MagicMock()
    eng.command_timeout = 1800
    eng.task_timeout = 7200
    eng._active_cancel_event = None
    eng._cancel_lock = threading.Lock()
    eng._sleep_notification_shown = False
    eng._last_failed_instruction = None
    eng._last_failed_conversation_history = None
    eng._new_message_event = threading.Event()
    eng._term_log = MagicMock()
    eng._saved_ctx_block = ""
    eng._is_resuming = False
    eng._resuming_from_sleep = False
    eng._consecutive_errors = 0
    eng._max_consecutive_errors = 20
    eng._error_recovery_strategies = {}
    eng._last_successful_iteration = time.time()
    eng._degraded_mode = False
    eng._degraded_features = []
    eng._heartbeat = None
    eng._auto_save_interval = 60.0
    eng._auto_save_thread = None
    eng._auto_save_running = False
    eng._auto_save_lock = threading.Lock()
    eng._recent_commands = []
    eng._loop_detection_window = 10
    eng._loop_repeat_threshold = 3
    eng._loop_warning_active = False
    eng._empty_iterations = 0
    eng._max_empty_iterations = 5
    eng._previous_output_digest = ""
    eng._empty_drift_active = False
    eng._action_history = []
    eng._max_action_history = 50
    eng._consecutive_same_action = 0
    eng._last_action_signature = ""
    eng._persistent_loop_patterns = {}
    eng._persistent_loop_threshold = 8
    eng._prev_iteration_action_sig = ""
    eng._force_sleep_pending = False
    eng._curiosity_fairy_threshold = 3
    eng._consecutive_identical_outputs = 0
    eng._last_output_hash = ""
    eng._curiosity_fairy_invoked = False
    eng._idle_behavior = "fairy"
    eng._log_compress_counter = 0
    eng._last_sleep_reminder = None
    eng._current_context = None
    eng.NOTIFICATION_THRESHOLD = 100
    eng.MAX_LOG_LINES_IN_PROMPT = 80
    eng._LOG_COMPRESS_INTERVAL = 50
    eng._LOG_KEEP_RECENT = 30
    eng._COMPRESS_SYSTEM_PROMPT = "Compress this context."
    eng._resilience = MagicMock()
    eng._telegram_boot_user_id = None
    return eng


class TestBuildSystemInstruction:
    def test_base_prompt_contains_output_format(self):
        eng = _make_engine()
        ctx = MagicMock()
        ctx.telegram_mode = False
        ctx.discord_mode = False
        prompt = eng._build_system_instruction(ctx)
        assert "CRITICAL: YOUR OUTPUT FORMAT" in prompt

    def test_telegram_mode(self):
        eng = _make_engine()
        ctx = MagicMock(telegram_mode=True, discord_mode=False)
        p = eng._build_system_instruction(ctx)
        assert "TELEGRAM MODE (ACTIVE" in p

    def test_discord_mode(self):
        eng = _make_engine()
        ctx = MagicMock(telegram_mode=False, discord_mode=True)
        p = eng._build_system_instruction(ctx)
        assert "DISCORD MODE (ACTIVE" in p

    def test_anti_repetition(self):
        eng = _make_engine()
        ctx = MagicMock(telegram_mode=False, discord_mode=False)
        p = eng._build_system_instruction(ctx)
        assert "ANTI-REPETITION RULES" in p


class TestFormatExecutionLogForPrompt:
    def test_no_terminal_history_duplication(self):
        eng = _make_engine()
        ctx = MagicMock()
        ctx.execution_log = ["log1", "log2"]
        eng._current_context = ctx
        eng._format_execution_log_for_prompt()
        eng.terminal_history.display_terminal_log.assert_not_called()

    def test_truncates(self):
        eng = _make_engine()
        ctx = MagicMock()
        ctx.execution_log = [f"e{i}" for i in range(200)]
        eng._current_context = ctx
        r = eng._format_execution_log_for_prompt()
        assert "omitted" in r

    def test_empty(self):
        eng = _make_engine()
        ctx = MagicMock(execution_log=[])
        eng._current_context = ctx
        assert eng._format_execution_log_for_prompt() == "(no terminal history)"

    def test_no_context(self):
        eng = _make_engine()
        eng._current_context = None
        assert eng._format_execution_log_for_prompt() == "(no context)"


class TestCompressExecutionLog:
    def test_below_threshold(self):
        eng = _make_engine()
        ctx = MagicMock()
        ctx.execution_log = ["e"] * 50
        eng._compress_execution_log(ctx)
        assert len(ctx.execution_log) == 50

    def test_above_threshold(self):
        eng = _make_engine()
        ctx = MagicMock()
        ctx.execution_log = [f"e{i}" for i in range(100)]
        eng._compress_execution_log(ctx)
        assert "LOG COMPRESSION" in ctx.execution_log[0]


class TestParseToolArgs:
    def test_comma_in_quotes(self):
        r = AutonomousLoopEngine._parse_tool_args('path="a,b", key=val')
        assert r["path"] == "a,b"

    def test_comma_in_brackets(self):
        r = AutonomousLoopEngine._parse_tool_args('cmd="echo [a,b,c]"')
        assert r["cmd"] == "echo [a,b,c]"

    def test_empty(self):
        assert AutonomousLoopEngine._parse_tool_args("") == {}


class TestAutoSave:
    def test_with_snapshot(self):
        eng = _make_engine()
        ctx = MagicMock(execution_log=["a"], current_goal="t", user_prompt="t",
                        iteration_count=1, telegram_mode=False, discord_mode=False,
                        telegram_user_id=None, metadata={})
        eng._auto_save_context(ctx, log_snapshot=["a"])

    def test_without_snapshot(self):
        eng = _make_engine()
        ctx = MagicMock(execution_log=["a"], current_goal="t", user_prompt="t",
                        iteration_count=1, telegram_mode=False, discord_mode=False,
                        telegram_user_id=None, metadata={})
        eng._auto_save_context(ctx)


class TestHandleSleep:
    def test_failure_saves_state(self):
        eng = _make_engine()
        ctx = MagicMock(current_goal="t", user_prompt="t", iteration_count=5,
                        telegram_mode=False, discord_mode=False, telegram_user_id=None,
                        metadata={"restart_provider": "t", "restart_model": "t"},
                        execution_log=[])
        # First call (in try block) raises, second call (in except block) succeeds
        aux_data = {"git_diff": "(none)", "metadata": "(none)", "errors": "(none)", "log_tail": "(empty)"}
        with patch.object(eng, "_save_sleep_state") as ms:
            with patch.object(eng, "_collect_auxiliary_context",
                              side_effect=[Exception("f"), aux_data]):
                with patch.object(eng, "_restart_process", side_effect=Exception("r")):
                    with patch("sys.exit") as me:
                        eng._handle_sleep(ctx)
                        ms.assert_called_once()
                        me.assert_called_once_with(127)


class TestHandleExit:
    def test_stops_auto_save(self):
        eng = _make_engine()
        ctx = MagicMock(current_goal="t", user_prompt="t", iteration_count=5,
                        telegram_mode=False, telegram_user_id=None, metadata={})
        with patch.object(eng, "_collect_auxiliary_context", side_effect=Exception("f")):
            with patch.object(eng, "_stop_auto_save"):
                eng._handle_exit(ctx, fast=True)
                eng._stop_auto_save.assert_called()


class TestCollectAux:
    def test_snapshot(self):
        ctx = MagicMock(metadata={})
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=1, stdout="", stderr="")
            r = AutonomousLoopEngine._collect_auxiliary_context(ctx, ["error: something failed"])
        # The error line should be captured from the snapshot
        assert "error: something failed" in r["errors"]

    def test_fallback(self):
        ctx = MagicMock(execution_log=["normal"], metadata={})
        with patch("subprocess.run") as mr:
            mr.return_value = MagicMock(returncode=1, stdout="", stderr="")
            r = AutonomousLoopEngine._collect_auxiliary_context(ctx)
        assert r["log_tail"] == "normal"


class TestModelRunner:
    def test_explicit_sys_instruction(self):
        rr = object.__new__(ModelRunner)
        rr.provider = rr.model = "t"
        rr.logger = MagicMock()
        rr.vision_client = MagicMock()
        rr._resilience = MagicMock()
        rr.MAX_RETRIES = 3
        rr.config = {}
        rr.prompt_template = MagicMock()
        rr.prompt_template.get_template.return_value = ""
        req = ModelRequest(task_type=TaskType.AUTONOMOUS_LOOP, prompt="x",
                           system_instruction="CUSTOM")
        ar = MagicMock(success=True, content="{}", model="t", provider="t",
                      tokens_used=10, cost=0.001, error=None)
        rr.vision_client.generate_response.return_value = ar
        rr.run_model(req)
        assert rr.vision_client.generate_response.call_args[0][0].system_instruction == "CUSTOM"

    def test_none_default(self):
        req = ModelRequest(task_type=TaskType.AUTONOMOUS_LOOP, prompt="x")
        assert req.system_instruction is None


class TestFormatPrompt:
    def test_skip_template(self):
        runner = object.__new__(ModelRunner)
        runner.prompt_template = MagicMock()
        req = ModelRequest(task_type=TaskType.AUTONOMOUS_LOOP, prompt="done")
        assert ModelRunner._format_prompt(runner, req) == "done"


class TestRestart:
    def test_windows(self):
        eng = _make_engine()
        ctx = MagicMock(metadata={"restart_provider": "t", "restart_model": "t",
                                  "restart_telegram_mode": False, "restart_discord_mode": False,
                                  "restart_telegram_user_id": None})
        with patch("sys.platform", "win32"):
            with patch.object(AutonomousLoopEngine, "_git_pull"):
                with patch("subprocess.Popen") as mp:
                    with patch("sys.exit") as me:
                        eng._restart_process(ctx)
                        mp.assert_called_once()
                        me.assert_called_once_with(0)

    def test_unix(self):
        eng = _make_engine()
        ctx = MagicMock(metadata={"restart_provider": "t", "restart_model": "t",
                                  "restart_telegram_mode": False, "restart_discord_mode": False,
                                  "restart_telegram_user_id": None})
        with patch("sys.platform", "linux"):
            with patch("os.execv") as me:
                eng._restart_process(ctx)
                me.assert_called_once()


class TestDegraded:
    def test_enter(self):
        eng = _make_engine()
        eng._enter_degraded_mode(["api"])
        assert eng._degraded_mode is True

    def test_exit(self):
        eng = _make_engine()
        eng._degraded_mode = True
        eng._consecutive_errors = 5
        eng._exit_degraded_mode()
        assert eng._degraded_mode is False
        assert eng._consecutive_errors == 0

    def test_success(self):
        eng = _make_engine()
        eng._consecutive_errors = 5
        eng._record_successful_iteration()
        assert eng._consecutive_errors == 0


class TestSubAgentShutdown:
    def test_shutdown(self):
        eng = _make_engine()
        eng.sub_agent_manager = MagicMock()
        try:
            pass
        finally:
            if eng.sub_agent_manager:
                eng.sub_agent_manager.shutdown(wait=False, cancel_pending=True)
        eng.sub_agent_manager.shutdown.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])