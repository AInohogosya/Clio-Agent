"""
Tests for ClioAgent core methods: initialize, get_status, save_context,
persist_settings, _can_start_autonomous_loop, autonomous loop lifecycle.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.core.agent import (
    ClioAgent,
    MAX_CONTEXT_TOKENS,
    MAX_TOOL_ITERATIONS,
    CIRCUIT_BREAKER_THRESHOLD,
    DEFAULT_CONTEXT_WINDOW_SIZE,
    COLD_ARCHIVE_BATCH,
    MESSAGE_PROCESS_TIMEOUT,
    MAX_CONTEXT_TOKENS as MAX_CTX,
)
from clio_agent_2.core.llm_router import LLMRouter


def _run(coro):
    return asyncio.run(coro)


class _MockConfig:
    """Mock config for ClioAgent tests"""
    def __init__(self):
        self.agent_name = "TestAgent"
        self.autonomous_mode = True
        self.thinking_interval = 5.0
        self.context_log_max_lines = 1000
        self.default_llm_provider = "openai"
        self.current_model = "gpt-4o"
        self.llm_settings_locked = True
        self.save_settings = mock.MagicMock(return_value=True)
        self.to_dict = mock.MagicMock(return_value={})
        self.validate_api_keys = mock.MagicMock(return_value={})
        self.get_api_key = mock.MagicMock(return_value=None)
        self.get_env_path = mock.MagicMock(return_value=mock.MagicMock())
        self.load_custom_providers = mock.MagicMock(return_value=[])


class TestClioAgentInit:
    """Tests for ClioAgent.__init__"""

    def test_init_sets_attributes(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"

        agent = ClioAgent(config, llm_router)

        assert agent.config is config
        assert agent.llm_router is llm_router
        assert agent.name == "TestAgent"
        assert agent.is_running is False
        assert agent._consecutive_failures == 0
        assert agent._circuit_open is False
        assert agent.autonomous_mode is True
        assert agent.response_callbacks == []
        assert agent.current_task is None

    def test_init_creates_context_log(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"

        agent = ClioAgent(config, llm_router)

        assert agent.context_log is not None
        assert agent.context_log.window_size == DEFAULT_CONTEXT_WINDOW_SIZE
        assert agent.context_log.cold_batch == COLD_ARCHIVE_BATCH

    def test_init_creates_tool_registry(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"

        agent = ClioAgent(config, llm_router)

        assert agent.tool_registry is not None


class TestClioAgentInitialize:
    """Tests for ClioAgent.initialize"""

    def test_initialize_loads_context(self):
        with mock.patch.object(ClioAgent, 'context_log') as mocked_log:
            config = _MockConfig()
            llm_router = mock.MagicMock(spec=LLMRouter)
            llm_router.current_model = "gpt-4o"
            agent = ClioAgent.__new__(ClioAgent)
            agent.config = config
            agent.name = "TestAgent"
            agent.context_log = mock.MagicMock()
            agent.context_log.load_from_file = mock.MagicMock(return_value=True)
            agent.context_log.get_line_count = mock.MagicMock(return_value=5)
            agent.context_log.add_system_message = mock.AsyncMock()

            result = _run(agent.initialize())

            assert "Context restored" in result
            agent.context_log.add_system_message.assert_called()

    def test_initialize_no_prior_context(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.name = "TestAgent"
        agent.context_log = mock.MagicMock()
        agent.context_log.load_from_file = mock.MagicMock(return_value=False)
        agent.context_log.add_system_message = mock.AsyncMock()

        result = _run(agent.initialize())

        assert result is None


class TestClioAgentStatus:
    """Tests for ClioAgent.get_status"""

    def test_get_status(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.get_available_providers = mock.MagicMock(return_value=["openai"])
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.name = "TestAgent"
        agent.is_running = False
        agent.tool_registry = mock.MagicMock()
        agent.tool_registry.list_tools = mock.MagicMock(return_value=["read_file"])
        agent.context_log = mock.MagicMock()
        agent.context_log.get_line_count = mock.MagicMock(return_value=42)

        status = _run(agent.get_status())

        assert status["name"] == "TestAgent"
        assert status["is_running"] is False
        assert status["autonomous_mode"] is True
        assert status["context_lines"] == 42
        assert "read_file" in status["available_tools"]
        assert "openai" in status["available_providers"]


class TestClioAgentPersistSettings:
    """Tests for ClioAgent.persist_settings"""

    def test_persist_settings_writes_to_config(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.default_provider = "openai"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.name = "TestAgent"
        agent.context_log = mock.MagicMock()
        agent.context_log.max_lines = 1000
        agent.autonomous_mode = True
        agent.thinking_interval = 5.0

        agent.persist_settings()

        config.save_settings.assert_called_once()
        settings = config.save_settings.call_args[0][0]
        assert settings["DEFAULT_LLM_PROVIDER"] == "openai"
        assert settings["DEFAULT_MODEL"] == "gpt-4o"
        assert settings["AUTONOMOUS_MODE"] == "true"
        assert settings["THINKING_INTERVAL"] == "5.0"
        assert settings["AGENT_NAME"] == "TestAgent"


class TestClioAgentCanStartAutonomous:
    """Tests for _can_start_autonomous_loop"""

    def test_can_start_with_model(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router

        assert agent._can_start_autonomous_loop() is True

    def test_cannot_start_without_model(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = ""
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router

        assert agent._can_start_autonomous_loop() is False


class TestClioAgentAutonomousLoopLifecycle:
    """Tests for autonomous loop lifecycle methods"""

    def test_start_autonomous_loop_resets_circuit_breaker(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.autonomous_mode = True
        agent.thinking_interval = 5.0
        agent._consecutive_failures = 3
        agent._circuit_open = True
        agent.context_log = mock.MagicMock()
        agent.context_log.add_system_message = mock.AsyncMock()
        agent._autonomous_task = None

        result = _run(agent.start_autonomous_loop())

        assert result is True
        assert agent._consecutive_failures == 0
        assert agent._circuit_open is False

    def test_start_autonomous_loop_idempotent(self):
        """Starting twice should not create duplicate tasks"""
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.autonomous_mode = True
        agent.thinking_interval = 5.0
        agent._consecutive_failures = 0
        agent._circuit_open = False
        agent.context_log = mock.MagicMock()
        agent.context_log.add_system_message = mock.AsyncMock()

        mock_task = MagicMock()
        mock_task.done = mock.MagicMock(return_value=False)
        agent._autonomous_task = mock_task

        # When a task is already running, should return True without creating new
        result = _run(agent.start_autonomous_loop())
        assert result is True
        assert agent._autonomous_task is mock_task  # Same task

    def test_start_autonomous_loop_no_model(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = ""
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.autonomous_mode = True
        agent.thinking_interval = 5.0
        agent.context_log = mock.MagicMock()
        agent.context_log.add_system_message = mock.AsyncMock()
        agent._autonomous_task = None

        result = _run(agent.start_autonomous_loop())

        assert result is False
        agent.context_log.add_system_message.assert_called()
        call_args = agent.context_log.add_system_message.call_args
        assert "no LLM model" in call_args[0][0].lower()

    def test_stop_autonomous_loop(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.is_running = True
        agent._autonomous_task = None

        agent.stop()

        assert agent.is_running is False

    def test_stop_autonomous_loop_with_cancel(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.is_running = True

        mock_task = MagicMock()
        mock_task.done = mock.MagicMock(return_value=False)
        mock_task.cancel = mock.MagicMock()

        # Make await work
        async def mock_await():
            pass

        mock_task.__await__ = lambda: mock_await().__await__()

        async def wait_side_effect():
            try:
                await mock_task
            except asyncio.CancelledError:
                pass

        agent._autonomous_task = mock_task
        agent._stop_autonomous_loop_async = lambda: wait_side_effect()

        agent.stop()
        assert agent.is_running is False


class TestClioAgentSaveContext:
    """Tests for save_context methods"""

    def test_save_context_async(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.context_log = mock.MagicMock()
        agent.context_log.save_async = mock.AsyncMock()

        _run(agent.save_context())
        agent.context_log.save_async.assert_called()

    def test_save_context_sync(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.context_log = mock.MagicMock()
        agent.context_log.save = mock.MagicMock()

        agent.save_context_sync()
        agent.context_log.save.assert_called()


class TestClioAgentRegisterCallback:
    """Tests for register_response_callback"""

    def test_register_callback_adds_to_list(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.response_callbacks = []

        callback = mock.MagicMock()
        agent.register_response_callback(callback)

        assert len(agent.response_callbacks) == 1
        assert agent.response_callbacks[0] is callback

    def test_register_multiple_callbacks(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.response_callbacks = []

        cb1 = mock.MagicMock()
        cb2 = mock.MagicMock()
        agent.register_response_callback(cb1)
        agent.register_response_callback(cb2)

        assert len(agent.response_callbacks) == 2


class TestClioAgentSendResponse:
    """Tests for send_response method"""

    def test_send_response_calls_all_callbacks(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.response_callbacks = []

        called = []
        async def callback(msg):
            called.append(msg)

        agent.register_response_callback(callback)
        agent.register_response_callback(callback)

        _run(agent.send_response("test message"))

        assert called == ["test message", "test message"]

    def test_send_response_ignores_callback_errors(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.response_callbacks = []

        async def bad_callback(msg):
            raise Exception("Callback error")

        async def good_callback(msg):
            pass

        agent.register_response_callback(bad_callback)
        agent.register_response_callback(good_callback)

        # Should not raise
        _run(agent.send_response("test"))

    def test_send_response_no_callbacks(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.response_callbacks = []

        # Should not raise
        _run(agent.send_response("test"))


class TestClioAgentSystemBlock:
    """Tests for _system_block and _build_context_messages"""

    def test_system_block_has_content(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.context_log = mock.MagicMock()
        agent.context_log.working_summary = ""
        agent.tool_registry = mock.MagicMock()
        agent.tool_registry.list_tools = mock.MagicMock(return_value=["read_file", "say"])
        agent._cached_prompt = ""
        agent._cached_tools = ""
        agent.BASE_SYSTEM_PROMPT_TEMPLATE = ClioAgent.BASE_SYSTEM_PROMPT_TEMPLATE

        block = agent._system_block()
        assert block["role"] == "system"
        assert "Clio-Agent-2" in block["content"]

    def test_system_block_with_summary(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.context_log = mock.MagicMock()
        agent.context_log.working_summary = "Previous context summary"
        agent.tool_registry = mock.MagicMock()
        agent.tool_registry.list_tools = mock.MagicMock(return_value=["read_file"])

        block = agent._system_block()
        assert "Rolling context summary" in block["content"]
        assert "Previous context summary" in block["content"]

    def test_build_context_messages(self):
        config = _MockConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        agent = ClioAgent.__new__(ClioAgent)
        agent.config = config
        agent.llm_router = llm_router
        agent.context_log = mock.MagicMock()
        agent.context_log.working_summary = ""
        agent.tool_registry = mock.MagicMock()
        agent.tool_registry.list_tools = mock.MagicMock(return_value=["read_file"])
        agent._cached_prompt = ""
        agent._cached_tools = ""
        agent.BASE_SYSTEM_PROMPT_TEMPLATE = ClioAgent.BASE_SYSTEM_PROMPT_TEMPLATE

        messages = agent._build_context_messages("User message here")

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "User message here"