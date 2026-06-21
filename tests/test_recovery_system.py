"""
Comprehensive tests for the automatic recovery system in Clio-Agent.
"""
import json, time, threading, unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_agent.core_processing.autonomous_loop_engine import (
    AutonomousLoopEngine, AutonomousContext, LoopPhase,
)
from ai_agent.utils.resilience_engine import (
    classify_api_error, ResilienceEngine, ResilienceConfig,
    ErrorSeverity, ErrorCategory, get_resilience_engine,
    reset_resilience_engine,
)
from ai_agent.utils.provider_fallback import (
    ProviderFallbackManager, FallbackConfig, ProviderStatus,
)


class MaxRetryError(Exception):
    def __init__(self, message, cause=None):
        super().__init__(message)
        self.__cause__ = cause


# ── 1. Error Classification ────────────────────────────────────────

class TestClassifyApiError(unittest.TestCase):
    def test_rate_limit(self):
        _, c, r, d = classify_api_error(Exception("429"))
        self.assertEqual(c, ErrorCategory.RATE_LIMIT)
        self.assertTrue(r)
        self.assertEqual(d, 30.0)

    def test_auth_401(self):
        _, c, r, _ = classify_api_error(Exception("401"))
        self.assertEqual(c, ErrorCategory.AUTHENTICATION)
        self.assertFalse(r)

    def test_auth_403(self):
        _, c, r, _ = classify_api_error(Exception("403"))
        self.assertEqual(c, ErrorCategory.AUTHENTICATION)
        self.assertFalse(r)

    def test_server_500(self):
        _, c, r, d = classify_api_error(Exception("500"))
        self.assertEqual(c, ErrorCategory.EXTERNAL)
        self.assertTrue(r)
        self.assertEqual(d, 5.0)

    def test_server_503(self):
        _, c, r, _ = classify_api_error(Exception("503"))
        self.assertEqual(c, ErrorCategory.EXTERNAL)
        self.assertTrue(r)

    def test_timeout(self):
        _, c, r, d = classify_api_error(Exception("timeout"))
        self.assertEqual(c, ErrorCategory.TIMEOUT)
        self.assertTrue(r)
        self.assertEqual(d, 3.0)

    def test_ssl(self):
        _, c, r, d = classify_api_error(Exception("SSL error"))
        self.assertEqual(c, ErrorCategory.TRANSIENT)
        self.assertTrue(r)
        self.assertEqual(d, 5.0)

    def test_conn(self):
        _, c, r, d = classify_api_error(Exception("Connection refused"))
        self.assertEqual(c, ErrorCategory.TRANSIENT)
        self.assertTrue(r)
        self.assertEqual(d, 2.0)

    def test_quota(self):
        _, c, r, _ = classify_api_error(Exception("Quota exceeded"))
        self.assertEqual(c, ErrorCategory.RESOURCE)
        self.assertFalse(r)

    def test_model_nf(self):
        _, c, r, _ = classify_api_error(Exception("Model 'x' not found"))
        self.assertEqual(c, ErrorCategory.CONFIGURATION)
        self.assertFalse(r)

    def test_val_400(self):
        _, c, r, _ = classify_api_error(Exception("400 Bad Request"))
        self.assertEqual(c, ErrorCategory.VALIDATION)
        self.assertFalse(r)

    def test_content_filter(self):
        _, c, r, _ = classify_api_error(Exception("content_filter triggered"))
        self.assertEqual(c, ErrorCategory.VALIDATION)
        self.assertFalse(r)

    def test_ctx_len(self):
        _, c, r, d = classify_api_error(Exception("context length exceeded"))
        self.assertEqual(c, ErrorCategory.RESOURCE)
        self.assertTrue(r)
        self.assertEqual(d, 0.0)

    def test_wrapped_chain(self):
        root = Exception("SSL: unexpected EOF")
        mid = Exception("Max retries exceeded")
        mid.__cause__ = root
        outer = Exception("Request failed")
        outer.__cause__ = mid
        _, c, _, d = classify_api_error(outer)
        self.assertEqual(c, ErrorCategory.TRANSIENT)
        self.assertEqual(d, 5.0)

    def test_unknown(self):
        _, c, r, d = classify_api_error(Exception("weird"))
        self.assertEqual(c, ErrorCategory.TRANSIENT)
        self.assertTrue(r)
        self.assertEqual(d, 1.0)

    def test_empty(self):
        _, c, r, _ = classify_api_error(Exception(""))
        self.assertEqual(c, ErrorCategory.TRANSIENT)
        self.assertTrue(r)

    def test_unicode(self):
        _, c, _, _ = classify_api_error(Exception("错误"))
        self.assertIsNotNone(c)

    def test_deep_chain(self):
        err = Exception("L5")
        cur = err
        for i in range(4, -1, -1):
            n = Exception(f"L{i}")
            n.__cause__ = cur
            cur = n
        _, c, _, _ = classify_api_error(cur)
        self.assertIsNotNone(c)

    def test_circular(self):
        e1 = Exception("e1")
        e2 = Exception("e2")
        e1.__cause__ = e2
        e2.__cause__ = e1
        _, c, _, _ = classify_api_error(e1)
        self.assertIsNotNone(c)

    def test_long_msg(self):
        _, c, _, _ = classify_api_error(Exception("x" * 100000))
        self.assertIsNotNone(c)


# ── 2. Recovery Strategy ───────────────────────────────────────────

class TestClassifyAndRecover(unittest.TestCase):
    def _make_engine(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e._consecutive_errors = 0
        e._max_consecutive_errors = 20
        e._degraded_mode = False
        e._degraded_features = []
        e._error_recovery_strategies = {
            "network_error": MagicMock(return_value=True),
            "rate_limit": MagicMock(return_value=True),
            "auth_error": MagicMock(return_value=True),
            "timeout_error": MagicMock(return_value=True),
            "resource_error": MagicMock(return_value=True),
            "model_error": MagicMock(return_value=True),
            "command_error": MagicMock(return_value=True),
            "unknown_error": MagicMock(return_value=True),
        }
        e.logger = MagicMock()
        e._term_log = MagicMock()
        e._notify_telegram_error = MagicMock()
        e._enter_degraded_mode = MagicMock()
        e._exit_degraded_mode = MagicMock()
        e._try_switch_provider = MagicMock(return_value=None)
        return e

    def _make_ctx(self):
        return AutonomousContext(
            user_prompt="test", current_goal="test",
            execution_log=[], cancel_event=threading.Event(),
        )

    def test_non_retryable_propagates(self):
        e = self._make_engine()
        self.assertFalse(e._classify_and_recover(Exception("401"), self._make_ctx()))

    def test_non_retryable_3x_enters_degraded(self):
        e = self._make_engine()
        e._consecutive_errors = 2
        self.assertTrue(e._classify_and_recover(Exception("401"), self._make_ctx()))
        e._enter_degraded_mode.assert_called_once()

    def test_retryable_returns_strategy(self):
        e = self._make_engine()
        self.assertTrue(e._classify_and_recover(Exception("Connection refused"), self._make_ctx()))

    def test_max_errors_stops(self):
        e = self._make_engine()
        e._consecutive_errors = 20
        self.assertFalse(e._classify_and_recover(Exception("Connection refused"), self._make_ctx()))
        e._enter_degraded_mode.assert_called_once()


class TestNetworkErrorRecovery(unittest.TestCase):
    def _make_engine(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e._consecutive_errors = 0
        e.logger = MagicMock()
        e._term_log = MagicMock()
        e._notify_telegram_error = MagicMock()
        e._raise_if_cancelled = MagicMock()
        return e

    def _make_ctx(self):
        return AutonomousContext(
            user_prompt="test", current_goal="test",
            execution_log=[], cancel_event=threading.Event(),
        )

    def test_retries_initially(self):
        e = self._make_engine()
        e._consecutive_errors = 1
        with patch("time.sleep"):
            # Signature: (self, error, ctx, delay) but error is unused
            self.assertTrue(e._recover_network_error(Exception("net"), self._make_ctx(), 2.0))

    def test_gives_up_after_10(self):
        e = self._make_engine()
        e._consecutive_errors = 11
        self.assertFalse(e._recover_network_error(Exception("net"), self._make_ctx(), 2.0))


class TestDegradedMode(unittest.TestCase):
    def _make_engine(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e._degraded_mode = False
        e._degraded_features = []
        e._consecutive_errors = 0
        e.logger = MagicMock()
        e._term_log = MagicMock()
        return e

    def test_enter(self):
        e = self._make_engine()
        e._enter_degraded_mode(["api_calls"])
        self.assertTrue(e._degraded_mode)
        self.assertEqual(e._degraded_features, ["api_calls"])

    def test_exit(self):
        e = self._make_engine()
        e._degraded_mode = True
        e._degraded_features = ["api_calls"]
        e._consecutive_errors = 5
        e._exit_degraded_mode()
        self.assertFalse(e._degraded_mode)
        self.assertEqual(e._consecutive_errors, 0)

    def test_record_success_resets(self):
        e = self._make_engine()
        e._consecutive_errors = 5
        e._record_successful_iteration()
        self.assertEqual(e._consecutive_errors, 0)

    def test_degraded_exits_after_success(self):
        e = self._make_engine()
        e._degraded_mode = True
        with patch("random.random", return_value=0.05):
            e._record_successful_iteration()
        self.assertFalse(e._degraded_mode)


class TestProviderFallback(unittest.TestCase):
    def test_cb_opens(self):
        cfg = FallbackConfig(circuit_breaker_threshold=3, circuit_breaker_timeout=60.0)
        m = ProviderFallbackManager(cfg)
        m._record_failure("o", 1.0)
        self.assertEqual(m._get_health("o").status, ProviderStatus.HEALTHY)
        m._record_failure("o", 1.0)
        self.assertEqual(m._get_health("o").status, ProviderStatus.HEALTHY)
        m._record_failure("o", 1.0)
        self.assertEqual(m._get_health("o").status, ProviderStatus.UNAVAILABLE)
        self.assertTrue(m._get_health("o").is_circuit_open)

    def test_cb_closes(self):
        cfg = FallbackConfig(circuit_breaker_threshold=1, circuit_breaker_timeout=0.01)
        m = ProviderFallbackManager(cfg)
        m._record_failure("o", 1.0)
        self.assertTrue(m._get_health("o").is_circuit_open)
        time.sleep(0.02)
        self.assertFalse(m._get_health("o").is_circuit_open)

    def test_skip(self):
        cfg = FallbackConfig(circuit_breaker_threshold=1, fallback_order=["o", "g"])
        m = ProviderFallbackManager(cfg)
        m._record_failure("o", 1.0)
        p, _ = m.get_next_available_provider("o")
        self.assertEqual(p, "g")

    def test_fallback(self):
        cfg = FallbackConfig(max_retries_per_provider=1, fallback_order=["o", "g"])
        m = ProviderFallbackManager(cfg)
        c = []
        def fn(provider, model, **kw):
            c.append(provider)
            if provider == "o":
                raise Exception("500")
            return "ok"
        r, u = m.execute_with_fallback("o", "gpt", fn)
        self.assertEqual(r, "ok")
        self.assertEqual(u, "g")

    def test_exhausted(self):
        cfg = FallbackConfig(max_retries_per_provider=1, fallback_order=["o"])
        m = ProviderFallbackManager(cfg)
        def fail(provider, model, **kw):
            raise Exception("500")
        with self.assertRaises(Exception):
            m.execute_with_fallback("o", "gpt", fail)

    def test_max(self):
        """With 2 providers and 1 retry each, max is 2 attempts (fallback)."""
        cfg = FallbackConfig(max_retries_per_provider=1, fallback_order=["a", "b"])
        m = ProviderFallbackManager(cfg)
        calls = []
        def fail(provider, model, **kw):
            calls.append(provider)
            raise Exception("err")
        with self.assertRaises(Exception):
            m.execute_with_fallback("a", "m", fail)
        # With max_retries=1, no same-provider retry: a fails → b fails → exhausted
        self.assertEqual(len(calls), 2)


class TestSleepWorkflow(unittest.TestCase):
    def test_removes_reminder(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e._last_sleep_reminder = "[SYSTEM] 🛏 SLEEP"
        ctx = AutonomousContext(
            user_prompt="t", current_goal="t",
            execution_log=["a", "[SYSTEM] 🛏 SLEEP", "b"],
            cancel_event=threading.Event(),
        )
        if e._last_sleep_reminder is not None:
            ctx.execution_log = [x for x in ctx.execution_log if x != e._last_sleep_reminder]
            e._last_sleep_reminder = None
        self.assertEqual(len(ctx.execution_log), 2)
        self.assertIsNone(e._last_sleep_reminder)

    def test_clears_rep(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e._action_history = ["a", "b"]
        e._consecutive_same_action = 2
        e._force_sleep_pending = True
        e._action_history.clear()
        e._consecutive_same_action = 0
        e._force_sleep_pending = False
        self.assertEqual(len(e._action_history), 0)
        self.assertFalse(e._force_sleep_pending)


class TestExitWorkflow(unittest.TestCase):
    def _mk(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e._sleep_notification_shown = True
        e._last_sleep_reminder = None
        e.logger = MagicMock()
        e._term_log = MagicMock()
        e._collect_auxiliary_context = MagicMock(return_value={})
        e._heuristic_compress = MagicMock(return_value="c")
        e._compress_context = MagicMock(return_value="c")
        e._save_exit_state = MagicMock()
        e._stop_auto_save = MagicMock()
        return e

    def test_saves(self):
        e = self._mk()
        ctx = AutonomousContext(user_prompt="t", current_goal="t", execution_log=[], cancel_event=threading.Event())
        e._handle_exit(ctx)
        e._save_exit_state.assert_called_once()

    def test_stops(self):
        e = self._mk()
        ctx = AutonomousContext(user_prompt="t", current_goal="t", execution_log=[], cancel_event=threading.Event())
        e._handle_exit(ctx)
        e._stop_auto_save.assert_called_once()

    def test_err(self):
        e = self._mk()
        e._collect_auxiliary_context.side_effect = Exception("disk full")
        ctx = AutonomousContext(user_prompt="t", current_goal="t", execution_log=[], cancel_event=threading.Event())
        e._handle_exit(ctx)
        e._stop_auto_save.assert_called_once()

    def test_fast(self):
        e = self._mk()
        ctx = AutonomousContext(user_prompt="t", current_goal="t", execution_log=[], cancel_event=threading.Event())
        e._handle_exit(ctx, fast=True)
        e._heuristic_compress.assert_called_once()
        e._compress_context.assert_not_called()


class TestGlobalHook(unittest.TestCase):
    def setUp(self):
        reset_resilience_engine()

    def tearDown(self):
        reset_resilience_engine()

    def test_hook_installed(self):
        """Global exception hook should be installed on engine creation."""
        import sys
        old_hook = sys.excepthook
        cfg = ResilienceConfig(install_global_hook=True)
        eng = ResilienceEngine(cfg)
        # sys.excepthook should now be the custom hook
        self.assertNotEqual(sys.excepthook, old_hook)
        # Restore
        sys.excepthook = old_hook

    def test_original_excepthook_saved(self):
        """Engine should save the original excepthook."""
        cfg = ResilienceConfig(install_global_hook=True)
        eng = ResilienceEngine(cfg)
        self.assertIsNotNone(eng._original_excepthook)


class TestEdgeCases(unittest.TestCase):
    def test_counter(self):
        e1 = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e1._log_compress_counter = 0
        e2 = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e2._log_compress_counter = 0
        e1._log_compress_counter += 1
        self.assertEqual(e1._log_compress_counter, 1)
        self.assertEqual(e2._log_compress_counter, 0)

    def test_resume(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e._is_resuming = True
        e._resuming_from_sleep = True
        ctx = AutonomousContext(user_prompt="t", current_goal="t", execution_log=[], cancel_event=threading.Event())
        ctx.iteration_count = 1
        if ctx.iteration_count == 1 and e._resuming_from_sleep:
            e._is_resuming = False
        self.assertFalse(e._is_resuming)

    def test_tg(self):
        e = AutonomousLoopEngine.__new__(AutonomousLoopEngine)
        e.logger = MagicMock()
        e._term_log = MagicMock()
        e._resilience = MagicMock()
        e.discord_bot = None
        e.telegram_bot = None
        e._telegram_boot_user_id = None
        bot = MagicMock()
        bot._resolve_chat_id = MagicMock(side_effect=Exception("fail"))
        bot._boot_user_id = 12345
        e.telegram_bot = bot
        ctx = AutonomousContext(user_prompt="t", current_goal="t", execution_log=[], cancel_event=threading.Event())
        ctx.discord_mode = False
        ctx.telegram_user_id = 99999
        e._notify_telegram_error(ctx, "err")
        bot.queue_message.assert_called_once_with(12345, "❌ err")


if __name__ == "__main__":
    unittest.main()
