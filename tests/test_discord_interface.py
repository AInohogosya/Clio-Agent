"""
Tests for the Discord interface module.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestDiscordInterface:
    """Tests for DiscordInterface"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        # Import DiscordInterface lazily to avoid hard dependency if discord.py not installed
        try:
            from clio_agent_2.interfaces.discord import DiscordInterface
        except ImportError:
            pytest.skip("discord.py not installed")

        self.DiscordInterface = DiscordInterface
        self.mock_agent = mock.MagicMock()
        self.mock_agent.name = "TestAgent"
        self.interface = DiscordInterface(self.mock_agent, "fake-token")

    def test_init(self):
        assert self.interface.agent is self.mock_agent
        assert self.interface.bot_token == "fake-token"
        assert self.interface.client is None
        assert self.interface.chat_sessions == {}

    @pytest.mark.asyncio
    async def test_send_message(self):
        from discord import TextChannel, Message

        mock_channel = MagicMock(spec=TextChannel)
        mock_channel.send = AsyncMock(return_value=MagicMock(spec=Message))

        self.interface._get_channel = mock.MagicMock(return_value=mock_channel)

        await self.interface.send_message("Hello Discord", channel_id=123)

        mock_channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_response_callback(self):
        self.interface.agent = mock.MagicMock()
        self.interface.agent.register_response_callback = mock.MagicMock()

        await self.interface.register_response_callback()
        if hasattr(self.interface, '_register'):
            self.interface._register()


class TestDiscordMarkdown:
    """Test Discord-specific markdown handling if present"""

    def test_discord_import(self):
        try:
            from clio_agent_2.interfaces.discord import DiscordInterface
        except ImportError:
            pytest.skip("discord.py not installed")


class TestDiscordHandleMessage:
    """Tests for DiscordInterface message handling"""

    def test_discord_interface_init(self):
        try:
            from clio_agent_2.interfaces.discord import DiscordInterface
        except ImportError:
            pytest.skip("discord.py not installed")

        mock_agent = mock.MagicMock()
        mock_agent.name = "TestBot"
        interface = DiscordInterface(mock_agent, "token")

        assert interface.agent is mock_agent
        assert interface.bot_token == "token"