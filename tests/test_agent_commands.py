"""
Tests for ClioAgent.execute_command - all slash commands.
"""
import asyncio
import tempfile
from pathlib import Path
from unittest import mock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.core.llm_router import LLMRouter
from clio_agent_2.config.settings import Config


def _run(coro):
    return asyncio.run(coro)


def _make_mock_agent():
    """Create a mock agent with mocked dependencies for command testing"""
    config = mock.MagicMock(spec=Config)
    config.default_llm_provider = "openai"
    config.current_model = "gpt-4o"
    config.autonomous_mode = True
    config.thinking_interval = 5.0
    config.context_log_max_lines = 1000
    config.agent_name = "TestAgent"
    config.llm_settings_locked = True
    config.save_settings = mock.MagicMock(return_value=True)
    config.get_env_path = mock.MagicMock(return_value=Path("/tmp/.env"))
    config.to_dict = mock.MagicMock(return_value={})
    config.validate_api_keys = mock.MagicMock(return_value={})
    config.load_custom_providers = mock.MagicMock(return_value=[])

    llm_router = mock.MagicMock(spec=LLMRouter)
    llm_router.default_provider = "openai"
    llm_router.current_model = "gpt-4o"
    llm_router.llm_settings_locked = True
    llm_router.get_available_providers = mock.MagicMock(return_value=["openai"])
    llm_router.set_llm_provider = mock.MagicMock()
    llm_router.set_llm_model = mock.MagicMock()
    llm_router.lock_llm_settings = mock.MagicMock()
    llm_router.unlock_llm_settings = mock.MagicMock()
    llm_router.list_all_models = mock.AsyncMock(return_value={"openai": ["gpt-4o", "gpt-4o-mini"]})
    llm_router.search_models = mock.AsyncMock(return_value=[])

    agent = mock.MagicMock(spec=ClioAgent)
    agent.config = config
    agent.llm_router = llm_router
    agent.name = "TestAgent"
    agent.autonomous_mode = True
    agent.thinking_interval = 5.0
    agent.is_running = False
    agent._consecutive_failures = 0
    agent._circuit_open = False

    # Mock context_log
    context_log = mock.MagicMock()
    context_log.get_line_count = mock.MagicMock(return_value=10)
    context_log.get_recent_entries = mock.MagicMock(return_value=[])
    context_log.clear = mock.MagicMock()
    context_log.restore_backup = mock.MagicMock(return_value=True)
    context_log.add_thinking = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
    agent.context_log = context_log

    # Mock tool_registry
    tool_registry = mock.MagicMock()
    tool_registry.list_tools = mock.MagicMock(return_value=["read_file", "shell_command", "say"])
    agent.tool_registry = tool_registry

    # Bind real methods
    agent.persist_settings = ClioAgent.persist_settings.__get__(agent, ClioAgent)
    agent.execute_command = ClioAgent.execute_command.__get__(agent, ClioAgent)
    agent.get_status = ClioAgent.get_status.__get__(agent, ClioAgent)
    agent.start_autonomous_loop = mock.AsyncMock(return_value=True)
    agent.stop_autonomous_loop = mock.AsyncMock()
    agent.stop = mock.MagicMock()

    return agent


class TestClioAgentHelpCommand:
    """Tests for /help command"""

    def test_help_without_args(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("help", []))
        assert "Available Commands" in result
        assert "User Commands" in result
        assert "Agent-Internal Commands" in result

    def test_help_with_all_arg(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("help", ["all"]))
        assert "User Commands" in result
        assert "Agent-Internal Commands" not in result


class TestClioAgentLlmProvidersCommand:
    """Tests for /llm_providers command"""

    def test_llm_providers_shows_configured(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_providers", []))
        assert "Configured LLM Providers" in result
        assert "openai" in result
        assert "(default)" in result

    def test_llm_providers_empty(self):
        agent = _make_mock_agent()
        agent.llm_router.get_available_providers = mock.MagicMock(return_value=[])
        result = _run(agent.execute_command("llm_providers", []))
        assert "No LLM providers configured" in result


class TestClioAgentLlmModelsCommand:
    """Tests for /llm_models command"""

    def test_llm_models_specific_provider(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_models", ["openai"]))
        assert "Available models for openai" in result
        assert "gpt-4o" in result

    def test_llm_models_all_providers(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_models", []))
        assert "Available Models by Provider" in result
        assert "openai" in result

    def test_llm_models_unknown_provider(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_models", ["unknown"]))
        assert "not configured" in result.lower()

    def test_llm_models_no_providers(self):
        agent = _make_mock_agent()
        agent.llm_router.get_available_providers = mock.MagicMock(return_value=[])
        result = _run(agent.execute_command("llm_models", []))
        assert "No LLM providers configured" in result


class TestClioAgentLlmSearchCommand:
    """Tests for /llm_search command"""

    def test_llm_search_with_query(self):
        agent = _make_mock_agent()
        agent.llm_router.search_models = mock.AsyncMock(return_value=[
            {"provider": "openai", "model": "gpt-4o"},
            {"provider": "openai", "model": "gpt-4o-mini"},
        ])
        result = _run(agent.execute_command("llm_search", ["gpt-4"]))
        assert "Models matching 'gpt-4'" in result
        assert "gpt-4o" in result

    def test_llm_search_no_results(self):
        agent = _make_mock_agent()
        agent.llm_router.search_models = mock.AsyncMock(return_value=[])
        result = _run(agent.execute_command("llm_search", ["nonexistent"]))
        assert "No models found matching" in result

    def test_llm_search_no_query(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_search", []))
        assert "Usage: /llm_search <query>" in result

    def test_llm_search_no_providers(self):
        agent = _make_mock_agent()
        agent.llm_router.get_available_providers = mock.MagicMock(return_value=[])
        result = _run(agent.execute_command("llm_search", ["test"]))
        assert "No LLM providers configured" in result


class TestClioAgentLlmDefaultCommand:
    """Tests for /llm_default command"""

    def test_llm_default_shows_current(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_default", []))
        assert "Current LLM configuration" in result
        assert "openai" in result
        assert "gpt-4o" in result
        assert "LOCKED" in result

    def test_llm_default_set_provider(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_default", ["anthropic"]))
        assert "Provider set to: anthropic" in result
        agent.llm_router.set_llm_provider.assert_called_with("anthropic")
        agent.config.save_settings.assert_called()

    def test_llm_default_set_provider_and_model(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_default", ["openai", "gpt-4o-mini"]))
        assert "Provider set to: openai" in result
        assert "Model set to: gpt-4o-mini" in result or "gpt-4o-mini" in result
        agent.llm_router.set_llm_provider.assert_called_with("openai")
        agent.llm_router.set_llm_model.assert_called_with("gpt-4o-mini")

    def test_llm_default_unknown_provider(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_default", ["unknown"]))
        assert "not configured" in result.lower()

    def test_llm_default_locked(self):
        agent = _make_mock_agent()
        from clio_agent_2.core.llm_router import LLMSettingsLockedError
        agent.llm_router.set_llm_provider = mock.MagicMock(
            side_effect=LLMSettingsLockedError("LLM settings are locked")
        )
        result = _run(agent.execute_command("llm_default", ["anthropic"]))
        assert "🔒" in result
        assert "locked" in result.lower()


class TestClioAgentLlmLockUnlockCommand:
    """Tests for /llm_lock and /llm_unlock commands"""

    def test_llm_lock(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_lock", []))
        assert "LOCKED" in result
        agent.llm_router.lock_llm_settings.assert_called()
        agent.config.save_settings.assert_called_with({"LLM_SETTINGS_LOCKED": "true"})

    def test_llm_unlock(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_unlock", []))
        assert "UNLOCKED" in result
        agent.llm_router.unlock_llm_settings.assert_called()
        agent.config.save_settings.assert_called_with({"LLM_SETTINGS_LOCKED": "false"})


class TestClioAgentApiKeysCommand:
    """Tests for /api_keys command"""

    def test_api_keys_shows_status(self):
        agent = _make_mock_agent()
        agent.config.validate_api_keys = mock.MagicMock(return_value={
            "openai": True,
            "anthropic": False,
            "telegram": True,
        })
        result = _run(agent.execute_command("api_keys", []))
        assert "API Key Configuration Status" in result
        assert "openai" in result
        assert "anthropic" in result
        assert "✅" in result or "❌" in result


class TestClioAgentSettingsCommand:
    """Tests for /settings command"""

    def test_settings_shows_all(self):
        agent = _make_mock_agent()
        agent.config.to_dict = mock.MagicMock(return_value={
            "default_llm_provider": "openai",
            "current_model": "gpt-4o",
            "autonomous_mode": True,
        })
        result = _run(agent.execute_command("settings", []))
        assert "Current Settings" in result
        assert "default_llm_provider" in result
        assert "openai" in result


class TestClioAgentConfigCommand:
    """Tests for /config command"""

    def test_config_without_args_shows_usage(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", []))
        assert "Usage: /config <setting> <value>" in result

    def test_config_provider(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["provider", "anthropic"]))
        assert "Provider set to" in result or "provider" in result.lower()

    def test_config_model(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["model", "gpt-4o-mini"]))
        assert "Model set to" in result or "model" in result.lower()

    def test_config_autonomous_mode_true(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["autonomous_mode", "true"]))
        assert "enabled" in result.lower()
        assert agent.autonomous_mode is True

    def test_config_autonomous_mode_false(self):
        agent = _make_mock_agent()
        agent.autonomous_mode = True
        result = _run(agent.execute_command("config", ["autonomous_mode", "false"]))
        assert "disabled" in result.lower()
        assert agent.autonomous_mode is False

    def test_config_thinking_interval(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["thinking_interval", "10"]))
        assert "10" in result
        assert agent.thinking_interval == 10.0

    def test_config_invalid_setting(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["invalid_setting", "value"]))
        assert "Unknown setting" in result


class TestClioAgentStatusCommand:
    """Tests for /status command"""

    def test_status_shows_info(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("status", []))
        assert "Agent Status" in result
        assert "TestAgent" in result


class TestClioAgentContextCommand:
    """Tests for /context command"""

    def test_context_default_count(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("context", []))
        assert "Recent Context" in result

    def test_context_with_count(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("context", ["5"]))
        assert "Recent Context" in result
        agent.context_log.get_recent_entries.assert_called_with(5)

    def test_context_invalid_count(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("context", ["invalid"]))
        assert "Invalid count" in result


class TestClioAgentClearContextCommand:
    """Tests for /clear_context command"""

    def test_clear_context(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("clear_context", []))
        assert "cleared" in result.lower()
        assert "backed up" in result.lower()
        agent.context_log.clear.assert_called()


class TestClioAgentRestoreContextCommand:
    """Tests for /restore_context command"""

    def test_restore_context_success(self):
        agent = _make_mock_agent()
        agent.context_log.restore_backup = mock.MagicMock(return_value=True)
        result = _run(agent.execute_command("restore_context", []))
        assert "restored" in result.lower()
        agent.context_log.restore_backup.assert_called()

    def test_restore_context_no_backup(self):
        agent = _make_mock_agent()
        agent.context_log.restore_backup = mock.MagicMock(return_value=False)
        result = _run(agent.execute_command("restore_context", []))
        assert "No backup found" in result


class TestClioAgentThinkCommand:
    """Tests for /think command"""

    def test_think_with_thought(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("think", ["This", "is", "a", "thought"]))
        assert "Thought recorded" in result
        agent.context_log.add_thinking.assert_called_with("This is a thought")

    def test_think_empty(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("think", []))
        assert "No thought provided" in result


class TestClioAgentStartStopCommands:
    """Tests for /start and /stop commands"""

    def test_start_success(self):
        agent = _make_mock_agent()
        agent.start_autonomous_loop = mock.AsyncMock(return_value=True)
        result = _run(agent.execute_command("start", []))
        assert "enabled and running" in result.lower()
        agent.start_autonomous_loop.assert_called()
        agent.config.save_settings.assert_called()

    def test_start_no_model(self):
        agent = _make_mock_agent()
        agent.start_autonomous_loop = mock.AsyncMock(return_value=False)
        result = _run(agent.execute_command("start", []))
        assert "could not start" in result.lower()
        assert "no LLM model" in result.lower()

    def test_stop(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("stop", []))
        assert "Stopping autonomous mode" in result
        agent.stop.assert_called()


class TestClioAgentResumeCommand:
    """Tests for /resume command"""

    def test_resume_already_running(self):
        agent = _make_mock_agent()
        agent.is_running = True
        agent._circuit_open = False
        result = _run(agent.execute_command("resume", []))
        assert "already running" in result.lower()

    def test_resume_circuit_open(self):
        agent = _make_mock_agent()
        agent._circuit_open = True
        agent.is_running = False
        agent.start_autonomous_loop = mock.AsyncMock(return_value=True)
        result = _run(agent.execute_command("resume", []))
        assert "resumed" in result.lower()

    def test_resume_no_model(self):
        agent = _make_mock_agent()
        agent._circuit_open = True
        agent.start_autonomous_loop = mock.AsyncMock(return_value=False)
        result = _run(agent.execute_command("resume", []))
        assert "Could not resume" in result
        assert "no LLM model" in result.lower()


class TestClioAgentExitCommand:
    """Tests for /exit and /quit commands"""

    def test_exit(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("exit", []))
        assert result == "__EXIT__"

    def test_quit(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("quit", []))
        assert result == "__EXIT__"


class TestClioAgentUnknownCommand:
    """Tests for unknown commands"""

    def test_unknown_command(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("unknown_command", []))
        assert "Unknown command" in result
        assert "unknown_command" in result