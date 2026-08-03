"""
Tests for the agent command execution edge cases.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.core.llm_router import LLMRouter
from clio_agent_2.config.settings import Config
from clio_agent_2.core.llm_router import LLMSettingsLockedError


def _run(coro):
    return asyncio.run(coro)


def _make_mock_agent():
    config = mock.MagicMock(spec=Config)
    config.default_llm_provider = "openai"
    config.current_model = "gpt-4o"
    config.autonomous_mode = True
    config.thinking_interval = 5.0
    config.context_log_max_lines = 1000
    config.agent_name = "TestAgent"
    config.llm_settings_locked = True
    config.save_settings = mock.MagicMock(return_value=True)
    config.get_env_path = mock.MagicMock(return_value=mock.MagicMock())
    config.to_dict = mock.MagicMock(return_value={})
    config.validate_api_keys = mock.MagicMock(return_value={})
    config.load_custom_providers = mock.MagicMock(return_value=[])

    llm_router = mock.MagicMock(spec=LLMRouter)
    llm_router.default_provider = "openai"
    llm_router.current_model = "gpt-4o"
    llm_router.llm_settings_locked = True
    llm_router.get_available_providers = mock.MagicMock(return_value=["openai", "anthropic"])
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

    context_log = mock.MagicMock()
    context_log.get_line_count = mock.MagicMock(return_value=10)
    context_log.get_recent_entries = mock.MagicMock(return_value=[])
    context_log.clear = mock.MagicMock()
    context_log.restore_backup = mock.MagicMock(return_value=True)
    context_log.add_thinking = mock.AsyncMock()
    context_log.add_system_message = mock.AsyncMock()
    agent.context_log = context_log

    tool_registry = mock.MagicMock()
    tool_registry.list_tools = mock.MagicMock(return_value=["read_file", "shell_command", "say"])
    agent.tool_registry = tool_registry

    agent.persist_settings = ClioAgent.persist_settings.__get__(agent, ClioAgent)
    agent.execute_command = ClioAgent.execute_command.__get__(agent, ClioAgent)
    agent.get_status = ClioAgent.get_status.__get__(agent, ClioAgent)
    agent.start_autonomous_loop = mock.AsyncMock(return_value=True)
    agent.stop_autonomous_loop = mock.AsyncMock()
    agent.stop = mock.MagicMock()

    return agent


class TestAgentCommandsEdgeCases:
    """Additional edge case tests for agent commands"""

    def test_reconfigure_no_args(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("reconfigure", []))
        assert "Reconfigure" in result
        assert "current settings" in result

    def test_reconfigure_with_args(self):
        agent = _make_mock_agent()
        agent.config.save_settings = mock.MagicMock(return_value=True)
        result = _run(agent.execute_command("reconfigure", ["autonomous_mode", "false"]))
        assert "autonomous_mode" in result.lower()

    def test_settings_shows_all(self):
        agent = _make_mock_agent()
        agent.config.to_dict = mock.MagicMock(return_value={
            "default_llm_provider": "openai",
            "current_model": "gpt-4o",
            "autonomous_mode": True,
            "thinking_interval": 5.0,
        })
        result = _run(agent.execute_command("settings", []))
        assert "Current Settings" in result

    def test_config_provider(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["provider", "anthropic"]))
        assert "Provider" in result

    def test_config_model(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["model", "gpt-4o-mini"]))
        assert "Model" in result

    def test_config_autonomous_mode(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["autonomous_mode", "true"]))
        assert "enabled" in result.lower()
        assert agent.autonomous_mode is True

    def test_config_thinking_interval(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["thinking_interval", "10"]))
        assert "10" in result
        assert agent.thinking_interval == 10.0

    def test_config_invalid_setting(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["invalid", "value"]))
        assert "Unknown setting" in result

    def test_config_missing_args(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("config", ["provider"]))
        assert "provide both setting name and value" in result

    def test_llm_lock_unlock(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_lock", []))
        assert "LOCKED" in result
        agent.llm_router.lock_llm_settings.assert_called()

        result = _run(agent.execute_command("llm_unlock", []))
        assert "UNLOCKED" in result
        agent.llm_router.unlock_llm_settings.assert_called()

    def test_api_keys_shows_status(self):
        agent = _make_mock_agent()
        agent.config.validate_api_keys = mock.MagicMock(return_value={
            "openai": True,
            "anthropic": False,
        })
        result = _run(agent.execute_command("api_keys", []))
        assert "API Key Configuration Status" in result

    def test_status_shows_info(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("status", []))
        assert "Agent Status" in result
        assert "TestAgent" in result

    def test_models_deprecated(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("models", []))
        assert "deprecated" in result.lower()
        assert "llm_models" in result

    def test_search_models_deprecated(self):
        agent = _make_mock_agent()
        agent.llm_router.get_available_providers = mock.MagicMock(return_value=["openai"])
        agent.llm_router.search_models = mock.AsyncMock(return_value=[])
        result = _run(agent.execute_command("search_models", ["test"]))
        assert "deprecated" in result.lower()
        assert "llm_search" in result

    def test_context_default(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("context", []))
        assert "Recent Context" in result

    def test_context_with_number(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("context", ["5"]))
        assert "Recent Context" in result
        agent.context_log.get_recent_entries.assert_called_with(5)

    def test_context_invalid_number(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("context", ["not-a-number"]))
        assert "Invalid count" in result

    def test_clear_context(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("clear_context", []))
        assert "cleared" in result.lower()
        assert "backed up" in result.lower()

    def test_restore_context_success(self):
        agent = _make_mock_agent()
        agent.context_log.restore_backup = mock.MagicMock(return_value=True)
        result = _run(agent.execute_command("restore_context", []))
        assert "restored" in result.lower()

    def test_restore_context_no_backup(self):
        agent = _make_mock_agent()
        agent.context_log.restore_backup = mock.MagicMock(return_value=False)
        result = _run(agent.execute_command("restore_context", []))
        assert "No backup found" in result

    def test_think_with_args(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("think", ["This", "is", "a", "thought"]))
        assert "Thought recorded" in result
        agent.context_log.add_thinking.assert_called_with("This is a thought")

    def test_think_empty(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("think", []))
        assert "No thought provided" in result

    def test_start_already_running(self):
        agent = _make_mock_agent()
        agent.is_running = True
        agent.start_autonomous_loop = mock.AsyncMock(return_value=True)
        result = _run(agent.execute_command("start", []))
        # Should still try to start
        agent.start_autonomous_loop.assert_called()

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

    def test_llm_default_locked(self):
        agent = _make_mock_agent()
        agent.llm_router.set_llm_provider = mock.MagicMock(
            side_effect=LLMSettingsLockedError("LLM settings are locked")
        )
        result = _run(agent.execute_command("llm_default", ["anthropic"]))
        assert "🔒" in result
        assert "locked" in result.lower()

    def test_llm_default_unknown_provider(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("llm_default", ["unknown"]))
        assert "not configured" in result.lower()

    def test_llm_models_no_providers(self):
        agent = _make_mock_agent()
        agent.llm_router.get_available_providers = mock.MagicMock(return_value=[])
        result = _run(agent.execute_command("llm_models", []))
        assert "No LLM providers configured" in result

    def test_llm_search_no_providers(self):
        agent = _make_mock_agent()
        agent.llm_router.get_available_providers = mock.MagicMock(return_value=[])
        result = _run(agent.execute_command("llm_search", ["test"]))
        assert "No LLM providers configured" in result

    def test_llm_providers_empty(self):
        agent = _make_mock_agent()
        agent.llm_router.get_available_providers = mock.MagicMock(return_value=[])
        result = _run(agent.execute_command("llm_providers", []))
        assert "No LLM providers configured" in result


class TestAgentCommandHelp:
    """Tests for help command variations"""

    def test_help_all(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("help", ["all"]))
        assert "User Commands" in result
        assert "Agent-Internal Commands" not in result

    def test_help_default(self):
        agent = _make_mock_agent()
        result = _run(agent.execute_command("help", []))
        assert "Available Commands" in result
        assert "User Commands" in result
        assert "Agent-Internal Commands" in result