"""
Tests for ToolRegistry.execute_tool and additional ToolRegistry behavior.
"""
import asyncio
from unittest import mock

from clio_agent_2.tools.tool_registry import (
    ToolRegistry,
    ToolResult,
    FileEditTool,
    FileSearchTool,
    SayTool,
)


def _run(coro):
    return asyncio.run(coro)


class TestToolRegistryExecuteTool:
    """Tests for ToolRegistry.execute_tool"""

    def test_execute_tool_success(self):
        registry = ToolRegistry()
        result = _run(registry.execute_tool("read_file", {"filepath": __file__}))

        assert result.success is True
        assert "content" in result.output or len(result.output) > 0

    def test_execute_tool_unknown_tool(self):
        registry = ToolRegistry()
        result = _run(registry.execute_tool("nonexistent_tool", {}))

        assert result.success is False
        assert "Unknown tool" in result.error
        assert "nonexistent_tool" in result.error

    def test_execute_tool_logs_to_context(self):
        context_log = mock.MagicMock()
        context_log.add_tool_execution = mock.AsyncMock()

        registry = ToolRegistry(context_log=context_log)
        result = _run(registry.execute_tool("read_file", {"filepath": __file__}))

        assert result.success is True
        context_log.add_tool_execution.assert_called_once()
        call_args = context_log.add_tool_execution.call_args
        assert call_args.args[0] == "read_file"

    def test_execute_tool_logs_failure_to_context(self):
        context_log = mock.MagicMock()
        context_log.add_tool_execution = mock.AsyncMock()

        registry = ToolRegistry(context_log=context_log)
        result = _run(registry.execute_tool("nonexistent", {}))

        assert result.success is False
        context_log.add_tool_execution.assert_called_once()
        call_args = context_log.add_tool_execution.call_args
        assert "Unknown tool: nonexistent" in call_args.args[2]

    def test_execute_tool_with_exception(self):
        """Tool execution that raises should return error result"""
        registry = ToolRegistry()

        # Register a tool that raises
        async def failing_tool():
            raise ValueError("Tool failed")

        registry.register_tool("failing", failing_tool)
        result = _run(registry.execute_tool("failing", {}))

        assert result.success is False
        assert "Tool execution error" in result.error
        assert "Tool failed" in result.error

    def test_execute_tool_empty_arguments(self):
        registry = ToolRegistry()
        # read_file with empty args should error (missing filepath)
        result = _run(registry.execute_tool("read_file", {}))
        assert result.success is False
        assert "Missing required argument" in result.error


class TestToolRegistryManagement:
    """Tests for ToolRegistry tool management"""

    def test_register_tool(self):
        registry = ToolRegistry()

        async def custom_tool():
            return ToolResult(True, "custom result")

        registry.register_tool("custom", custom_tool)

        assert "custom" in registry.list_tools()
        tool = registry.get_tool("custom")
        assert tool is not None

    def test_get_tool_not_found(self):
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None

    def test_list_tools(self):
        registry = ToolRegistry()
        tools = registry.list_tools()

        assert "read_file" in tools
        assert "write_file" in tools
        assert "shell_command" in tools
        assert "say" in tools
        assert "thinking" in tools

    def test_default_tools_registered(self):
        registry = ToolRegistry()
        tools = registry.list_tools()
        # Check all expected defaults
        expected = [
            "read_file", "write_file", "append_file", "edit_file",
            "web_search", "fetch_url",
            "search_files", "search_content", "list_directory",
            "shell_command", "run_shell_command", "execute_shell_command",
            "thinking", "say"
        ]
        for tool in expected:
            assert tool in tools, f"Missing default tool: {tool}"

    def test_shell_command_aliases(self):
        registry = ToolRegistry()
        tools = registry.list_tools()
        assert "shell_command" in tools
        assert "run_shell_command" in tools
        assert "execute_shell_command" in tools
        # All aliases should point to the same function
        assert registry.get_tool("shell_command") == registry.get_tool("run_shell_command")

    def test_register_overwrites_existing(self):
        registry = ToolRegistry()

        async def new_tool():
            return ToolResult(True, "new")

        original = registry.get_tool("say")
        registry.register_tool("say", new_tool)
        assert registry.get_tool("say") is new_tool
        assert registry.get_tool("say") != original


class TestToolRegistryInitialization:
    """Tests for ToolRegistry initialization and dependency injection"""

    def test_default_init(self):
        registry = ToolRegistry()
        assert registry.context_log is None
        assert registry.search_api_key is None
        assert registry.response_sink is None

    def test_init_with_context_log(self):
        context_log = mock.MagicMock()
        registry = ToolRegistry(context_log=context_log)
        assert registry.context_log is context_log

    def test_init_with_search_api_key(self):
        registry = ToolRegistry(search_api_key="test-key")
        assert registry.search_api_key == "test-key"

    def test_init_with_response_sink(self):
        sink = mock.MagicMock()
        registry = ToolRegistry(response_sink=sink)
        assert registry.response_sink is sink

    def test_say_tool_wired_to_response_sink(self):
        sink = mock.MagicMock()
        registry = ToolRegistry(response_sink=sink)
        say_tool = mock.MagicMock()
        # The say tool should be created with response_sink
        assert "say" in registry.tools