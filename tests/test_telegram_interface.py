"""
Tests for Telegram interface markdown sanitization functions.
Covers sanitize_markdown, the only markdown sanitizer in use.
"""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

from clio_agent_2.interfaces.telegram import (
    sanitize_markdown,
    _retry_bot_request,
    TelegramInterface,
)


class TestSanitizeMarkdown:
    """Tests for sanitize_markdown function"""

    def test_none_input(self):
        result = sanitize_markdown(None)
        assert result is None

    def test_empty_input(self):
        assert sanitize_markdown("") == ""

    def test_preserves_code_blocks(self):
        text = "```\nmy code\n```"
        result = sanitize_markdown(text)
        assert "my code" in result
        assert "```" in result

    def test_preserves_inline_code(self):
        text = "Here is `some code`"
        result = sanitize_markdown(text)
        assert "`some code`" in result

    def test_preserves_urls(self):
        text = "Check [this link](https://example.com) out"
        result = sanitize_markdown(text)
        assert "https://example.com" in result

    def test_preserves_bold(self):
        text = "This is **bold text** here"
        result = sanitize_markdown(text)
        assert "**bold text**" in result

    def test_plain_text_unchanged(self):
        text = "Hello world"
        result = sanitize_markdown(text)
        assert result == "Hello world"

    def test_complex_markdown(self):
        text = "**bold** _italic_ `code` [url](https://x.com)"
        result = sanitize_markdown(text)
        assert "bold" in result
        assert "code" in result
        assert "https://x.com" in result


class TestRetryBotRequest:
    """Tests for _retry_bot_request"""

    def test_success_on_first_try(self):
        calls = []

        async def success():
            calls.append(1)
            return "ok"

        result = asyncio.run(_retry_bot_request(success, max_retries=3, base_delay=0.001))
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_on_timed_out(self):
        from telegram.error import TimedOut

        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise TimedOut("Timed out")
            return "recovered"

        result = asyncio.run(_retry_bot_request(flaky, max_retries=3, base_delay=0.001))
        assert result == "recovered"
        assert len(calls) == 2

    def test_retries_on_network_error(self):
        from telegram.error import NetworkError

        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise NetworkError("Network error")
            return "recovered"

        result = asyncio.run(_retry_bot_request(flaky, max_retries=3, base_delay=0.001))
        assert result == "recovered"
        assert len(calls) == 2

    def test_raises_after_max_retries(self):
        from telegram.error import TimedOut

        calls = []

        async def always_fail():
            calls.append(1)
            raise TimedOut("Timed out")

        try:
            asyncio.run(_retry_bot_request(always_fail, max_retries=3, base_delay=0.001))
            assert False, "Expected TimedOut"
        except TimedOut:
            pass
        assert len(calls) == 3

    def test_does_not_retry_non_network_errors(self):
        from telegram.error import BadRequest

        calls = []

        async def raise_bad_request():
            calls.append(1)
            raise BadRequest("Bad request")

        try:
            asyncio.run(_retry_bot_request(raise_bad_request, max_retries=3, base_delay=0.001))
            assert False, "Expected BadRequest"
        except BadRequest:
            pass
        assert len(calls) == 1


class TestTelegramInterfaceSendMessage:
    """Tests for TelegramInterface.send_message"""

    def test_send_message_no_application(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")
        interface.application = None

        result = asyncio.run(interface.send_message("Hello", chat_id=123))
        assert result is None

    def test_send_message_to_specific_chat(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")

        mock_bot = MagicMock()
        mock_send = AsyncMock(return_value={})
        mock_bot.send_message = mock_send

        interface.application = MagicMock()
        interface.application.bot = mock_bot

        asyncio.run(interface.send_message("Hello", chat_id=12345))

        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args.kwargs["chat_id"] == 12345
        assert "Hello" in call_args.kwargs.get("text", "")

    def test_send_message_broadcast(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")

        mock_bot = MagicMock()
        mock_send = AsyncMock(return_value={})
        mock_bot.send_message = mock_send

        interface.application = MagicMock()
        interface.application.bot = mock_bot
        interface.chat_sessions = {123: {"user": "test"}, 456: {"user": "test2"}}

        asyncio.run(interface.send_message("Broadcast message"))

        assert mock_send.call_count == 2


class TestTelegramInterfaceHandleMessage:
    """Tests for TelegramInterface.handle_message"""

    def test_handle_message_empty_text(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")

        update = MagicMock()
        update.message.text = None
        update.message.text = ""
        context = MagicMock()

        result = asyncio.run(interface.handle_message(update, context))
        assert result is None


class TestTelegramInterfaceHandleCommand:
    """Tests for TelegramInterface._handle_command"""

    def test_handle_start_command(self):
        agent = mock.MagicMock()
        agent.name = "TestBot"
        interface = TelegramInterface(agent, "fake-token")

        update = MagicMock()
        update.effective_chat.id = 12345

        context = MagicMock()
        context.bot.send_message = AsyncMock()

        asyncio.run(interface._handle_command(update, context, "/start"))

        context.bot.send_message.assert_called_with(
            chat_id=12345,
            text=mock.ANY,
            parse_mode="Markdown"
        )

    def test_handle_help_command(self):
        agent = mock.MagicMock()
        agent.name = "TestBot"
        agent.execute_command = AsyncMock(return_value="Help text")
        interface = TelegramInterface(agent, "fake-token")

        update = MagicMock()
        update.effective_chat.id = 12345
        context = MagicMock()

        asyncio.run(interface._handle_command(update, context, "/help"))

        agent.execute_command.assert_called_with("help", [])

    def test_handle_exit_command(self):
        agent = mock.MagicMock()
        agent.name = "TestBot"
        agent.execute_command = AsyncMock(return_value="__EXIT__")
        interface = TelegramInterface(agent, "fake-token")

        update = MagicMock()
        update.effective_chat.id = 12345
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        asyncio.run(interface._handle_command(update, context, "/exit"))

        context.bot.send_message.assert_called_with(chat_id=12345, text="👋 Goodbye!")

    def test_handle_command_with_bot_username(self):
        """Commands like /cmd@MyBot should strip the bot suffix"""
        agent = mock.MagicMock()
        agent.name = "TestBot"
        agent.execute_command = AsyncMock(return_value="Result")
        interface = TelegramInterface(agent, "fake-token")

        update = MagicMock()
        update.effective_chat.id = 12345
        context = MagicMock()
        context.bot.send_message = AsyncMock()

        asyncio.run(interface._handle_command(update, context, "/status@MyBot"))

        agent.execute_command.assert_called_with("status", [])


class TestTelegramInterfaceLongMessage:
    """Tests for TelegramInterface._send_long_message"""

    def test_short_message(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")

        mock_bot = MagicMock()
        mock_send = AsyncMock()
        mock_bot.send_message = mock_send

        asyncio.run(interface._send_long_message(mock_bot, 123, "Short message"))

        assert mock_send.call_count == 1

    def test_long_message_split(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")

        mock_bot = MagicMock()
        mock_send = AsyncMock()
        mock_bot.send_message = mock_send

        long_text = "x" * 5000
        asyncio.run(interface._send_long_message(mock_bot, 123, long_text))

        assert mock_send.call_count >= 2


class TestTelegramInterfaceAutonomous:
    """Tests for TelegramInterface handle_autonomous_message"""

    def test_autonomous_message_delivered(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")

        interface.send_message = AsyncMock()

        asyncio.run(interface.handle_autonomous_message("Hello from agent"))

        interface.send_message.assert_called_with("Hello from agent")

    def test_autonomous_thought_not_delivered(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")

        interface.send_message = AsyncMock()

        asyncio.run(interface.handle_autonomous_message("[Autonomous Thought] Internal reasoning"))

        interface.send_message.assert_not_called()


class TestTelegramInterfaceErrorHandling:
    """Tests for TelegramInterface error handling"""

    def test_handle_telegram_error_conflict(self):
        agent = mock.MagicMock()
        interface = TelegramInterface(agent, "fake-token")
        interface._conflict_event = asyncio.Event()

        from telegram.error import Conflict
        context = MagicMock()
        context.error = Conflict("409 Conflict")

        asyncio.run(interface._handle_telegram_error(None, context))

        assert interface._conflict_event.is_set()

    def test_stop_autonomous_loop(self):
        agent = mock.MagicMock()
        agent.stop_autonomous_loop = AsyncMock()
        interface = TelegramInterface(agent, "fake-token")
        interface.application = MagicMock()
        interface.application.stop = AsyncMock()
        interface.application.shutdown = AsyncMock()
        interface.application.updater = MagicMock()
        interface.application.updater.running = False

        asyncio.run(interface.stop())

        agent.stop_autonomous_loop.assert_called()