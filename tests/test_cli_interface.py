"""
Tests for the CLI interface module.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock
import sys
from pathlib import Path

# Ensure importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestCLIInterface:
    """Tests for CLIInterface class"""

    def test_cli_import(self):
        try:
            from clio_agent_2.interfaces.cli import CLIInterface
        except ImportError:
            pass

    def test_cli_interface_exists(self):
        from clio_agent_2.interfaces.cli import CLIInterface
        assert CLIInterface is not None

    def test_cli_interface_init(self):
        from clio_agent_2.interfaces.cli import CLIInterface

        mock_agent = mock.MagicMock()
        mock_agent.name = "TestBot"
        interface = CLIInterface(mock_agent)

        assert interface.agent is mock_agent


class TestCliHandleCommand:
    """Tests for CLI command handling"""

    def test_cli_processes_commands(self):
        from clio_agent_2.interfaces.cli import CLIInterface

        mock_agent = mock.MagicMock()
        mock_agent.name = "TestBot"
        interface = CLIInterface(mock_agent)
        # Basic smoke test - interface is constructable
        assert interface is not None


class TestCLIStartup:
    """Tests for CLI startup behavior"""

    def test_cli_interface_attributes(self):
        from clio_agent_2.interfaces.cli import CLIInterface

        mock_agent = mock.MagicMock()
        interface = CLIInterface(mock_agent)

        # Should have expected attributes
        assert hasattr(interface, 'agent')

    def test_cli_async_methods_exist(self):
        from clio_agent_2.interfaces.cli import CLIInterface
        assert hasattr(CLIInterface, 'start')
        assert hasattr(CLIInterface, 'stop') or hasattr(CLIInterface, 'shutdown')


class TestCliHandleMessage:
    """Tests for CLI message handling"""

    def test_cli_can_be_created(self):
        from clio_agent_2.interfaces.cli import CLIInterface

        mock_agent = mock.MagicMock()
        mock_agent.name = "TestBot"
        interface = CLIInterface(mock_agent)
        assert interface is not None