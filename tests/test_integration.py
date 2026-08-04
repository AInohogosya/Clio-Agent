"""
Integration tests for ClioAgent with ToolRegistry.
"""
import asyncio
import tempfile
from pathlib import Path
from unittest import mock

from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.core.llm_router import LLMRouter
from clio_agent_2.tools.tool_registry import ToolRegistry, ToolResult, FileEditTool
from clio_agent_2.config.settings import Config


def _run(coro):
    return asyncio.run(coro)


class _TestConfig(Config):
    """Test config with mocked values"""
    def __init__(self):
        self.agent_name = "IntegrationTestAgent"
        self.autonomous_mode = True
        self.thinking_interval = 5.0
        self.context_log_max_lines = 1000
        self.default_llm_provider = "openai"
        self.current_model = "gpt-4o"
        self.llm_settings_locked = True
        self.openai_api_key = "sk-test"
        self.google_api_key = None
        self.anthropic_api_key = None
        self.openrouter_api_key = None
        self.grok_api_key = None
        self.deepseek_api_key = None
        self.mistral_api_key = None
        self.groq_api_key = None
        self.perplexity_api_key = None
        self.together_api_key = None
        self.fireworks_api_key = None
        self.nvidia_api_key = None
        self.nim_api_key = None
        self.qwen_api_key = None
        self.huggingface_api_key = None
        self.deepinfra_api_key = None
        self.ollama_api_key = None
        self.ollama_base_url = None
        self.search_api_key = None
        self.openrouter_http_referer = None
        self.openrouter_app_name = None
        self.telegram_bot_token = None
        self.discord_bot_token = None
        self.custom_providers = []


class TestAgentToolRegistryIntegration:
    """Integration tests for agent with real ToolRegistry"""

    def _make_agent(self):
        config = _TestConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.chat = mock.AsyncMock(return_value='{"tool": "say", "arguments": {"message": "Hello"}}')

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClioAgent(config, llm_router)
            # Override paths to temp directory
            agent.context_log.persist_path = Path(tmp) / "context.json"
            return agent

    def test_agent_initializes_tool_registry(self):
        agent = self._make_agent()
        assert agent.tool_registry is not None
        assert isinstance(agent.tool_registry, ToolRegistry)

    def test_agent_has_all_default_tools(self):
        agent = self._make_agent()
        tools = agent.tool_registry.list_tools()
        expected = [
            "read_file", "write_file", "append_file", "edit_file",
            "web_search", "fetch_url",
            "search_files", "search_content", "list_directory",
            "shell_command", "run_shell_command", "execute_shell_command",
            "thinking", "say"
        ]
        for tool in expected:
            assert tool in tools

    def test_agent_file_operations(self):
        agent = self._make_agent()
        # Disable sandbox for test — files are in a temp directory.
        FileEditTool.sandbox_root = None
        try:
            # Write a file
            result = _run(agent.tool_registry.execute_tool(
                "write_file",
                {"filepath": str(agent.context_log.persist_path.parent / "test.txt"), "content": "Hello"}
            ))
            assert result.success is True

            # Read it back
            result = _run(agent.tool_registry.execute_tool(
                "read_file",
                {"filepath": str(agent.context_log.persist_path.parent / "test.txt")}
            ))
            assert result.success is True
            assert "Hello" in result.output
        finally:
            FileEditTool.sandbox_root = None

    def test_agent_shell_command(self):
        agent = self._make_agent()
        result = _run(agent.tool_registry.execute_tool(
            "shell_command",
            {"command": "echo hello"}
        ))
        assert result.success is True
        assert "hello" in result.output

    def test_agent_say_tool(self):
        agent = self._make_agent()
        result = _run(agent.tool_registry.execute_tool(
            "say",
            {"message": "Test message"}
        ))
        assert result.success is True
        assert result.output == "Test message"

    def test_agent_thinking_tool(self):
        agent = self._make_agent()
        result = _run(agent.tool_registry.execute_tool(
            "thinking",
            {"thought": "Internal thought"}
        ))
        assert result.success is True
        assert "Thought recorded" in result.output


class TestAgentConfigIntegration:
    """Integration tests for agent with Config"""

    def test_agent_uses_config_values(self):
        config = _TestConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.chat = mock.AsyncMock(return_value="")

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClioAgent(config, llm_router)
            assert agent.name == "IntegrationTestAgent"
            assert agent.autonomous_mode is True
            assert agent.thinking_interval == 5.0

    def test_agent_persist_settings_updates_config(self):
        config = _TestConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.default_provider = "openai"
        llm_router.chat = mock.AsyncMock(return_value="")

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClioAgent(config, llm_router)
            agent.context_log.max_lines = 2000
            agent.thinking_interval = 10.0

            agent.persist_settings()

            assert config.context_log_max_lines == 2000
            assert config.thinking_interval == 10.0


class TestAgentContextIntegration:
    """Integration tests for agent with ContextLog"""

    def test_agent_context_persistence(self):
        config = _TestConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.chat = mock.AsyncMock(return_value="")

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClioAgent(config, llm_router)
            agent.context_log.persist_path = Path(tmp) / "context.json"

            _run(agent.context_log.add_user_message("Test message"))
            _run(agent.context_log.save_async())

            # Create new agent with same persist path
            agent2 = ClioAgent(config, llm_router)
            agent2.context_log.persist_path = Path(tmp) / "context.json"
            loaded = agent2.context_log.load_from_file()

            assert loaded is True
            assert agent2.context_log.get_line_count() == 1

    def test_agent_context_restored_on_init(self):
        config = _TestConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.chat = mock.AsyncMock(return_value="")

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClioAgent(config, llm_router)
            agent.context_log.persist_path = Path(tmp) / "context.json"
            _run(agent.context_log.add_user_message("Saved message"))
            _run(agent.context_log.save_async())

            # New agent should load context
            agent2 = ClioAgent(config, llm_router)
            agent2.context_log.persist_path = Path(tmp) / "context.json"
            restored_msg = _run(agent2.initialize())

            assert restored_msg is not None
            assert "Context restored" in restored_msg


class TestAgentLLMIntegration:
    """Integration tests for agent with LLMRouter"""

    def test_agent_uses_llm_router_for_chat(self):
        config = _TestConfig()
        llm_router = mock.MagicMock(spec=LLMRouter)
        llm_router.current_model = "gpt-4o"
        llm_router.chat = mock.AsyncMock(return_value='{"tool": "say", "arguments": {"message": "Hi"}}')

        with tempfile.TemporaryDirectory() as tmp:
            agent = ClioAgent(config, llm_router)
            agent.context_log.persist_path = Path(tmp) / "context.json"

            _run(agent.process_message("Hello"))

            llm_router.chat.assert_called()


class TestToolRegistryIntegration:
    """Integration tests for ToolRegistry with various tools"""

    def test_registry_all_tools_executable(self):
        registry = ToolRegistry()
        for tool_name in registry.list_tools():
            tool = registry.get_tool(tool_name)
            assert tool is not None
            assert callable(tool)

    def test_registry_unknown_tool_handled(self):
        registry = ToolRegistry()
        result = _run(registry.execute_tool("nonexistent", {}))
        assert result.success is False
        assert "Unknown tool" in result.error

    def test_registry_say_tool_with_response_sink(self):
        delivered = []
        async def sink(msg):
            delivered.append(msg)

        registry = ToolRegistry(response_sink=sink)
        result = _run(registry.execute_tool("say", {"message": "Hello"}))

        assert result.success is True
        assert delivered == ["Hello"]

    def test_registry_thinking_tool_with_context_log(self):
        context_log = mock.AsyncMock()
        registry = ToolRegistry(context_log=context_log)
        result = _run(registry.execute_tool("thinking", {"thought": "Test thought"}))

        assert result.success is True
        context_log.add_thinking.assert_awaited_once_with("Test thought")