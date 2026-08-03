"""
Tests for the __init__.py modules and package structure.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestPackageImports:
    """Tests that all packages can be imported"""

    def test_clio_agent_2_import(self):
        import clio_agent_2
        assert clio_agent_2 is not None

    def test_core_import(self):
        from clio_agent_2 import core
        assert core is not None

    def test_tools_import(self):
        from clio_agent_2 import tools
        assert tools is not None

    def test_config_import(self):
        from clio_agent_2 import config
        assert config is not None

    def test_interfaces_import(self):
        from clio_agent_2 import interfaces
        assert interfaces is not None

    def test_utils_import(self):
        from clio_agent_2 import utils
        assert utils is not None


class TestCoreModuleExports:
    """Tests for core module exports"""

    def test_agent_exported(self):
        from clio_agent_2.core import ClioAgent
        assert ClioAgent is not None

    def test_context_manager_exported(self):
        from clio_agent_2.core import ContextLog, ContextEntry
        assert ContextLog is not None
        assert ContextEntry is not None

    def test_llm_router_exported(self):
        from clio_agent_2.core import LLMRouter, OpenAIProvider
        assert LLMRouter is not None
        assert OpenAIProvider is not None

    def test_retry_exported(self):
        from clio_agent_2.core import retry_async
        assert retry_async is not None

    def test_token_budget_exported(self):
        from clio_agent_2.core import estimate_tokens, truncate_to_tokens
        assert estimate_tokens is not None
        assert truncate_to_tokens is not None


class TestToolsModuleExports:
    """Tests for tools module exports"""

    def test_tool_registry_exported(self):
        from clio_agent_2.tools import ToolRegistry, ToolResult
        assert ToolRegistry is not None
        assert ToolResult is not None

    def test_file_edit_tool_exported(self):
        from clio_agent_2.tools import FileEditTool
        assert FileEditTool is not None

    def test_web_search_tool_exported(self):
        from clio_agent_2.tools import WebSearchTool
        assert WebSearchTool is not None

    def test_file_search_tool_exported(self):
        from clio_agent_2.tools import FileSearchTool
        assert FileSearchTool is not None

    def test_shell_command_tool_exported(self):
        from clio_agent_2.tools import ShellCommandTool
        assert ShellCommandTool is not None

    def test_thinking_tool_exported(self):
        from clio_agent_2.tools import ThinkingTool
        assert ThinkingTool is not None

    def test_say_tool_exported(self):
        from clio_agent_2.tools import SayTool
        assert SayTool is not None


class TestConfigModuleExports:
    """Tests for config module exports"""

    def test_config_exported(self):
        from clio_agent_2.config import Config
        assert Config is not None

    def test_settings_exported(self):
        from clio_agent_2.config.settings import Config as SettingsConfig
        assert SettingsConfig is not None


class TestInterfacesModuleExports:
    """Tests for interfaces module exports"""

    def test_telegram_interface_exported(self):
        from clio_agent_2.interfaces.telegram import TelegramInterface
        assert TelegramInterface is not None

    def test_discord_interface_exported(self):
        try:
            from clio_agent_2.interfaces.discord import DiscordInterface
            assert DiscordInterface is not None
        except ImportError:
            pass  # Optional dependency

    def test_whatsapp_interface_exported(self):
        from clio_agent_2.interfaces.whatsapp import WhatsAppInterface
        assert WhatsAppInterface is not None

    def test_cli_interface_exported(self):
        from clio_agent_2.interfaces.cli import CLIInterface
        assert CLIInterface is not None


class TestUtilsModuleExports:
    """Tests for utils module exports"""

    def test_instance_lock_exported(self):
        from clio_agent_2.utils import SingleInstanceLock
        assert SingleInstanceLock is not None


class TestMetaControllerExports:
    """Tests for meta_controller exports"""

    def test_repetition_detector_exported(self):
        from clio_agent_2.meta_controller import RepetitionDetector
        assert RepetitionDetector is not None

    def test_run_meta_exported(self):
        from clio_agent_2.meta_controller import run_meta
        assert run_meta is not None


class TestApplyFixesExports:
    """Tests for apply_fixes exports"""

    def test_is_token_configured_exported(self):
        from clio_agent_2.apply_fixes import _is_token_configured
        assert _is_token_configured is not None

    def test_patch_exported(self):
        from clio_agent_2.apply_fixes import _patch
        assert _patch is not None


class TestSetupEnvExports:
    """Tests for setup_env exports"""

    def test_configure_screen_exported(self):
        from clio_agent_2.config.setup_env import configure_screen
        assert configure_screen is not None

    def test_interactive_setup_exported(self):
        from clio_agent_2.config.setup_env import interactive_setup
        assert interactive_setup is not None

    def test_apply_overrides_exported(self):
        from clio_agent_2.config.setup_env import apply_overrides_from_argv
        assert apply_overrides_from_argv is not None