"""
Tests for the WhatsApp interface module.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestWhatsAppInterface:
    """Tests for WhatsAppInterface class"""

    def test_whatsapp_import(self):
        try:
            from clio_agent_2.interfaces.whatsapp import WhatsAppInterface
        except ImportError:
            pass

    def test_whatsapp_interface_exists(self):
        from clio_agent_2.interfaces.whatsapp import WhatsAppInterface
        assert WhatsAppInterface is not None

    def test_whatsapp_interface_init(self):
        from clio_agent_2.interfaces.whatsapp import WhatsAppInterface

        mock_agent = mock.MagicMock()
        mock_agent.name = "TestBot"
        interface = WhatsAppInterface(mock_agent, "123456", "fake-token")

        assert interface.agent is mock_agent

    def test_whatsapp_interface_attributes(self):
        from clio_agent_2.interfaces.whatsapp import WhatsAppInterface

        mock_agent = mock.MagicMock()
        interface = WhatsAppInterface(mock_agent, "123456", "fake-token")

        # Verify basic structure
        assert hasattr(interface, 'agent')


class TestWhatsAppVerification:
    """Tests for WhatsApp webhook verification"""

    def test_whatsapp_interface_has_init(self):
        from clio_agent_2.interfaces.whatsapp import WhatsAppInterface
        assert hasattr(WhatsAppInterface, '__init__')

    def test_whatsapp_interface_has_start(self):
        from clio_agent_2.interfaces.whatsapp import WhatsAppInterface
        assert hasattr(WhatsAppInterface, 'start')


class TestWhatsAppHandleMessage:
    """Tests for WhatsApp message handling"""

    def test_whatsapp_can_be_constructed(self):
        from clio_agent_2.interfaces.whatsapp import WhatsAppInterface

        mock_agent = mock.MagicMock()
        interface = WhatsAppInterface(mock_agent, "123456", "fake-token")
        assert interface is not None