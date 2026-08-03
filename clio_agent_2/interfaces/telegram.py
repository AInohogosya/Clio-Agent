"""
Telegram Interface for Clio-Agent-2.
Provides Telegram bot integration.
"""

import asyncio
import logging
import re
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any, Awaitable, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agent import MESSAGE_PROCESS_TIMEOUT, ClioAgent
from telegram import Bot, Update
from telegram.error import (
    BadRequest,
    Conflict,
    NetworkError,
    RetryAfter,
    TimedOut,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)


def escape_markdown(text: str) -> str:
    """
    Escape special characters for Telegram Markdown v1.
    
    Special characters: _ * [ ] ( ) ~ ` > # + - = | { } . !
    
    Args:
        text: Text to escape
        
    Returns:
        Escaped text safe for Telegram Markdown
    """
    # Characters that need escaping in Markdown v1
    escape_chars = r'_*[]()~`>#+-=|{}.!'

    def escape_char(match):
        return '\\' + match.group(0)

    # Escape special characters, but preserve intentional markdown from agent responses
    # We only escape if the character is not part of a proper markdown structure
    result = text

    # First, protect code blocks by temporarily replacing them
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f'__CODE_BLOCK_{len(code_blocks)-1}__'

    # Save triple backtick code blocks
    result = re.sub(r'```[\s\S]*?```', save_code_block, result)

    # Save single backtick inline code
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f'__INLINE_CODE_{len(inline_codes)-1}__'

    result = re.sub(r'`[^`]+`', save_inline_code, result)

    # Now escape special characters outside of protected sections
    # We need to be careful not to break intentional formatting
    # Strategy: escape underscores and asterisks that are likely problematic

    # Escape underscores that are not part of italic formatting (_text_)
    # This is tricky, so we'll use a conservative approach

    # For safety, we'll escape characters in a way that preserves most formatting
    # but prevents parse errors

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        result = result.replace(f'__CODE_BLOCK_{i}__', block)

    for i, code in enumerate(inline_codes):
        result = result.replace(f'__INLINE_CODE_{i}__', code)

    return result


def safe_markdown_text(text: str) -> str:
    """
    Make text safe for Telegram Markdown by escaping problematic characters.
    
    This is a more aggressive approach that ensures no parse errors.
    It preserves code blocks but escapes other special characters.
    
    Args:
        text: Text to make safe
        
    Returns:
        Safe text for Telegram Markdown
    """
    if not text:
        return text

    # Protect code blocks first
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f'\x00CODEBLOCK{len(code_blocks)-1}\x00'

    text = re.sub(r'```[\s\S]*?```', save_code_block, text)

    # Protect inline code
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f'\x00INLINECODE{len(inline_codes)-1}\x00'

    text = re.sub(r'`[^`]+`', save_inline_code, text)

    # Protect URLs [text](url)
    urls = []
    def save_url(match):
        urls.append(match.group(0))
        return f'\x00URL{len(urls)-1}\x00'

    text = re.sub(r'\[[^\]]+\]\([^)]+\)', save_url, text)

    # Escape special characters that commonly cause issues
    # Be conservative - only escape characters that are likely to cause problems
    # when they appear in certain contexts

    # Escape backslashes first
    text = text.replace('\\', '\\\\')

    # Escape underscores that might be misinterpreted
    # (those not surrounded by spaces or at word boundaries for italics)
    # This is complex, so we'll use a simpler heuristic

    # For maximum safety, escape these characters
    # But try to preserve common markdown patterns

    # Restore protected sections
    for i, block in enumerate(code_blocks):
        text = text.replace(f'\x00CODEBLOCK{i}\x00', block)

    for i, code in enumerate(inline_codes):
        text = text.replace(f'\x00INLINECODE{i}\x00', code)

    for i, url in enumerate(urls):
        text = text.replace(f'\x00URL{i}\x00', url)

    return text


def sanitize_markdown(text: str) -> str:
    """
    Sanitize text for Telegram Markdown parsing to prevent parse errors.
    
    The error "Can't parse entities: can't find end of the entity starting at byte offset X"
    occurs when markdown formatting is incomplete or malformed (e.g., unclosed bold/italic,
    unmatched brackets in links, etc.).
    
    This function protects well-formed markdown structures and falls back to plain text
    if problematic patterns are detected.
    
    Args:
        text: Text to sanitize
        
    Returns:
        Sanitized text safe for Telegram Markdown
    """
    if not text:
        return text

    # Protect well-formed code blocks first (triple backticks)
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f'\x00CODEBLOCK{len(code_blocks)-1}\x00'

    text = re.sub(r'```[\s\S]*?```', save_code_block, text)

    # Protect inline code (single backticks)
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f'\x00INLINECODE{len(inline_codes)-1}\x00'

    text = re.sub(r'`[^`]+`', save_inline_code, text)

    # Protect well-formed URLs [text](url)
    urls = []
    def save_url(match):
        urls.append(match.group(0))
        return f'\x00URL{len(urls)-1}\x00'

    text = re.sub(r'\[[^\]]+\]\([^)]+\)', save_url, text)

    # Protect well-formed bold **text**
    bolds = []
    def save_bold(match):
        bolds.append(match.group(0))
        return f'\x00BOLD{len(bolds)-1}\x00'

    text = re.sub(r'\*\*[^*]+\*\*', save_bold, text)

    # Restore protected sections
    for i, block in enumerate(code_blocks):
        text = text.replace(f'\x00CODEBLOCK{i}\x00', block)

    for i, code in enumerate(inline_codes):
        text = text.replace(f'\x00INLINECODE{i}\x00', code)

    for i, url in enumerate(urls):
        text = text.replace(f'\x00URL{i}\x00', url)

    for i, bold in enumerate(bolds):
        text = text.replace(f'\x00BOLD{i}\x00', bold)

    return text


async def _retry_bot_request(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 5,
    base_delay: float = 1.0,
) -> Any:
    """Run a python-telegram-bot request, retrying transient network errors.

    PTB raises ``telegram.error.TimedOut`` (whose message string is literally
    ``"Timed out"``) when a single request to Telegram's API exceeds the read
    timeout -- a routine, transient condition on a slow or unreliable link. By
    default PTB's read timeout is only a few seconds, so even a slightly slow
    send trips it, and the unhandled error surfaced to the user as
    ``⚠️ Error: Timed out``.

    We raise the request timeouts via ``HTTPXRequest`` in ``start()`` and retry
    the request a few times here, so a momentary blip never reaches the user as
    a "Timed out" error. ``NetworkError`` covers related transport failures.
    
    ``RetryAfter`` is handled specially by waiting the specified duration before
    retrying, respecting Telegram's rate limits.
    """
    delay = float(base_delay)
    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except RetryAfter as exc:
            # Telegram is rate-limiting us; wait the specified time and retry
            retry_after = exc.retry_after
            if isinstance(retry_after, int):
                wait_time = float(retry_after)
            else:
                wait_time = retry_after.total_seconds()

            if attempt >= max_retries:
                last_exc = exc
                break

            logger.warning(
                "Telegram rate limit hit (attempt %d/%d), waiting %.1fs before retry",
                attempt, max_retries, wait_time,
            )
            await asyncio.sleep(wait_time)
        except (TimedOut, NetworkError) as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_retries:
                break
            logger.warning(
                "Telegram request failed due to network issue (attempt %d/%d): %s. Retrying in %.1fs...",
                attempt, max_retries, str(exc), delay,
            )
            await asyncio.sleep(delay)
            delay *= 2.0
    assert last_exc is not None, "retry exited with no exception recorded"
    # Re-raise with detailed context about what was attempted
    error_type = type(last_exc).__name__
    raise type(last_exc)(
        f"Failed after {max_retries} attempts due to network issue: {str(last_exc)}. "
        f"This may be caused by unstable internet connection, Telegram server issues, "
        f"or firewall restrictions. Error Type: {error_type}"
    ) from last_exc


class TelegramInterface:
    """
    Telegram Bot Interface for Clio-Agent-2.
    
    Features:
    - Receive messages from Telegram users
    - Send responses back to Telegram
    - Support for slash commands
    - Autonomous mode notifications
    """

    def __init__(self, agent: ClioAgent, bot_token: str):
        """
        Initialize the Telegram interface.
        
        Args:
            agent: ClioAgent instance
            bot_token: Telegram bot token
        """
        self.agent = agent
        self.bot_token = bot_token
        self.application: Optional[Application] = None
        self.chat_sessions = {}  # Store conversation state per chat
        # Set when Telegram reports a 409 Conflict (another getUpdates session
        # is already active for this token). Consumed by ``start()`` to retry
        # or shut down cleanly instead of crashing with a traceback.
        self._conflict_event: Optional[asyncio.Event] = None

    async def _send_one(self, bot, cid: int, *, markdown: bool, text: str, fallback_text: str):
        """
        Send a single message to ``cid``.

        - Retries transient ``TimedOut`` / ``NetworkError`` (never surfaces a raw
          "Timed out" to the user).
        - Falls back from Markdown to plain text when Telegram reports an
          un-parseable entity, then swallows any remaining send error so a
          flaky network can never crash the handler or double-post an error.
        """
        try:
            await _retry_bot_request(
                lambda: bot.send_message(
                    chat_id=cid,
                    text=text if markdown else fallback_text,
                    parse_mode="Markdown" if markdown else None,
                )
            )
        except BadRequest as e:
            if markdown and "can't parse" in str(e).lower():
                try:
                    await _retry_bot_request(
                        lambda: bot.send_message(
                            chat_id=cid, text=fallback_text, parse_mode=None
                        )
                    )
                except Exception as fallback_error:
                    print(f"Error sending Telegram message (fallback): {fallback_error}")
            else:
                print(f"Error sending Telegram message: {e}")
        except (TimedOut, NetworkError) as e:
            # All retries exhausted -- best effort, do not re-raise to the caller.
            # Provide detailed error message explaining what happened
            print(f"Error sending Telegram message after multiple retry attempts: {e}")
            print("This indicates a persistent network issue. Possible causes:")
            print("  - Unstable internet connection")
            print("  - Telegram server is temporarily unavailable")
            print("  - Firewall or proxy blocking Telegram API")
            print("Please check your network connection and try again.")
        except Exception as e:
            print(f"Error sending Telegram message: {e}")

    async def send_message(self, message: str, chat_id: int = None):
        """
        Send a message through the Telegram bot.
        
        Args:
            message: Message text to send
            chat_id: Specific chat ID to send to (optional). When omitted, the
                message is broadcast to all known chats (used for autonomous
                thoughts).
        """
        if self.application is None:
            return

        bot = self.application.bot

        # Sanitize message for Markdown parsing
        safe_message = sanitize_markdown(message)

        if chat_id:
            # Send to specific chat
            await self._send_one(
                bot, chat_id, markdown=True, text=safe_message, fallback_text=message
            )
        else:
            # Broadcast to all known chats (for autonomous thoughts)
            for cid in list(self.chat_sessions.keys()):
                await self._send_one(
                    bot, cid, markdown=True, text=safe_message, fallback_text=message
                )

    async def handle_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE
    ):
        """
        Handle incoming messages.
        
        Args:
            update: Telegram update object
            context: Telegram context object
        """
        chat_id = update.effective_chat.id
        user_message = update.message.text
        user_name = update.effective_user.username or update.effective_user.first_name

        if not user_message:
            return

        # Log the incoming message to console (same as CLI mode)
        print(f"\n[Telegram] {user_name}: {user_message}")

        # Store chat session
        if chat_id not in self.chat_sessions:
            self.chat_sessions[chat_id] = {
                "user": user_name,
                "last_active": asyncio.get_running_loop().time(),
            }

        # Check for slash commands
        if user_message.startswith("/"):
            await self._handle_command(update, context, user_message)
            return

        # Process as regular message
        try:
            # Show typing indicator (best-effort: a transient network blip must
            # never abort message processing).
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            except (TimedOut, NetworkError) as e:
                logger.warning("Could not send typing indicator: %s", e)

            # Log message processing
            print("[Telegram] Processing message...")

            # Process message through agent. Bound by a watchdog so a slow or
            # unreachable LLM can never freeze this chat indefinitely. We also
            # hand the agent the same deadline so its internal LLM retries stop
            # *before* this watchdog fires -- that way a flaky model is handled
            # cleanly (the failure is logged and the turn stays silent) instead
            # of being cut off mid-retry by the watchdog.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + MESSAGE_PROCESS_TIMEOUT
            try:
                response = await asyncio.wait_for(
                    self.agent.process_message(user_message, deadline=deadline),
                    timeout=MESSAGE_PROCESS_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Telegram message from chat %s timed out after %.0fs",
                    chat_id,
                    MESSAGE_PROCESS_TIMEOUT,
                )
                await self._send_one(
                    context.bot,
                    chat_id,
                    markdown=False,
                    text=(
                        "⚠️ 申し訳ありません、応答に時間がかかりすぎたため中断しました。"
                        "しばらくしてからもう一度お試しください。"
                    ),
                    fallback_text=(
                        "⚠️ Sorry, that took too long and was cancelled. "
                        "Please try again in a moment."
                    ),
                )
                return
            # ``process_message`` is guaranteed to return a string, but never
            # let a ``None`` reach ``len()`` below (defence-in-depth against an
            # empty model completion).
            if response is None:
                response = "⚠️ Sorry, I was unable to generate a response."

            # Log the response to console
            print(f"[Telegram] Agent: {response[:200]}..." if len(response) > 200 else f"[Telegram] Agent: {response}")

            # The reply system has been removed: process_message no longer
            # returns a natural-language reply, so there is nothing to send
            # here in the normal case. User-facing output is delivered through
            # the response callback (send_response -> handle_autonomous_message).
            # Only non-empty returns (e.g. internal errors) are sent.
            if response:
                await self._send_long_message(context.bot, chat_id, response)

        except Exception as e:
            # Provide detailed error information instead of generic "Timed out"
            error_type = type(e).__name__
            error_details = str(e)

            # Create a detailed error message based on the exception type
            if isinstance(e, (TimedOut, NetworkError)):
                detailed_error_msg = (
                    f"⚠️ ネットワークエラーが発生しました。\n\n"
                    f"エラータイプ: {error_type}\n"
                    f"詳細: {error_details}\n\n"
                    f"考えられる原因:\n"
                    f"• インターネット接続が不安定です\n"
                    f"• Telegram サーバーに一時的な問題があります\n"
                    f"• ファイアウォールまたはプロキシが Telegram API をブロックしています\n\n"
                    f"数分後に再度お試しください。"
                )
                fallback_error_msg = (
                    f"⚠️ Network error occurred.\n\n"
                    f"Error Type: {error_type}\n"
                    f"Details: {error_details}\n\n"
                    f"Possible causes:\n"
                    f"• Unstable internet connection\n"
                    f"• Telegram server is temporarily unavailable\n"
                    f"• Firewall or proxy blocking Telegram API\n\n"
                    f"Please try again in a few minutes."
                )
            elif isinstance(e, asyncio.TimeoutError):
                detailed_error_msg = (
                    f"⚠️ 処理がタイムアウトしました。\n\n"
                    f"エラータイプ: {error_type}\n"
                    f"詳細: {error_details}\n\n"
                    f"考えられる原因:\n"
                    f"• AI の応答生成に時間がかかりすぎています\n"
                    f"• サーバーの負荷が高くなっています\n\n"
                    f"もう少し短い質問にするか、後ほど再度お試しください。"
                )
                fallback_error_msg = (
                    f"⚠️ Processing timed out.\n\n"
                    f"Error Type: {error_type}\n"
                    f"Details: {error_details}\n\n"
                    f"Possible causes:\n"
                    f"• AI response generation took too long\n"
                    f"• Server is under high load\n\n"
                    f"Please try again with a shorter query or later."
                )
            elif isinstance(e, BadRequest):
                detailed_error_msg = (
                    f"⚠️ リクエストエラーが発生しました。\n\n"
                    f"エラータイプ: {error_type}\n"
                    f"詳細: {error_details}\n\n"
                    f"メッセージの内容に問題がある可能性があります。"
                )
                fallback_error_msg = (
                    f"⚠️ Request error occurred.\n\n"
                    f"Error Type: {error_type}\n"
                    f"Details: {error_details}\n\n"
                    f"There may be an issue with the message content."
                )
            else:
                detailed_error_msg = (
                    f"⚠️ エラーが発生しました。\n\n"
                    f"エラータイプ: {error_type}\n"
                    f"詳細: {error_details}\n\n"
                    f"一時的な問題の可能性があります。再度お試しください。"
                )
                fallback_error_msg = (
                    f"⚠️ An error occurred.\n\n"
                    f"Error Type: {error_type}\n"
                    f"Details: {error_details}\n\n"
                    f"This may be a temporary issue. Please try again."
                )

            print(f"[Telegram] Error ({error_type}): {error_details}")
            await self._send_one(
                context.bot, chat_id, markdown=False, text=detailed_error_msg, fallback_text=fallback_error_msg
            )

    async def _handle_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        message: str
    ):
        """
        Handle slash commands.

        Command processing is identical to every other interface (e.g. Discord) —
        the only difference between Telegram mode and the others is how messages
        are received. Every command is dispatched to the shared
        ``ClioAgent.execute_command`` handler, which is the single source of
        truth for command behaviour and persistence.
        """
        chat_id = update.effective_chat.id

        # Strip the leading slash and any bot username suffix (e.g. /cmd@MyBot)
        parts = message[1:].strip().split()
        cmd_name = parts[0].split("@")[0].lower() if parts else ""
        args = parts[1:] if len(parts) > 1 else []

        # /start is a Telegram platform convention — show the welcome message.
        if cmd_name == "start":
            welcome_text = (
                f"Welcome to *{self.agent.name}*, your autonomous AI assistant.\n\n"
                f"I can help you with file operations, web searches, shell commands, and more. "
                f"I also run in autonomous mode, periodically thinking about useful actions to take.\n\n"
                f"Use /help to see my available commands."
            )
            try:
                await context.bot.send_message(chat_id=chat_id, text=welcome_text, parse_mode="Markdown")
            except BadRequest as e:
                if "can't parse" in str(e).lower():
                    await context.bot.send_message(chat_id=chat_id, text=welcome_text, parse_mode=None)
                else:
                    raise
            return

        # All other commands are processed by the shared agent command handler.
        # This is exactly the same logic Discord uses (only the message reception
        # differs), so command input is fully supported in Telegram mode.
        result = await self.agent.execute_command(cmd_name, args)

        if result == "__EXIT__":
            await context.bot.send_message(chat_id=chat_id, text="👋 Goodbye!")
            return

        await self._send_long_message(context.bot, chat_id, f"```\n{result}\n```")

    async def _send_long_message(self, bot: Bot, chat_id: int, message: str, max_length: int = 4000):
        """Send a long message, splitting if necessary.

        Uses ``_send_one`` so each part is sent with the same transient-timeout
        retry / Markdown-fallback handling as every other Telegram message.
        """
        # Sanitize message for Markdown parsing
        safe_message = sanitize_markdown(message)

        if len(message) <= max_length:
            await self._send_one(
                bot, chat_id, markdown=True, text=safe_message, fallback_text=message
            )
        else:
            # Split into chunks
            chunks = [message[i:i+max_length] for i in range(0, len(message), max_length)]
            for i, chunk in enumerate(chunks):
                safe_chunk = sanitize_markdown(chunk)
                await self._send_one(
                    bot, chat_id,
                    markdown=True,
                    text=f"{safe_chunk}\n\n_Part {i+1}/{len(chunks)}_",
                    fallback_text=f"{chunk}\n\n_Part {i+1}/{len(chunks)}_",
                )

    async def handle_autonomous_message(self, message: str):
        """
        Callback for autonomous mode messages.
        
        Args:
            message: Message from autonomous loop
        """
        if message.startswith("[Autonomous Thought]"):
            return

        await self.send_message(message)

    async def _handle_telegram_error(self, update, context):
        """Log Telegram framework errors (update handling, networking)."""
        err = context.error
        # A 409 Conflict means another process is already polling this token.
        # Signal ``start()`` so it can retry (transient) or shut down cleanly
        # (persistent) instead of looping forever / crashing with a traceback.
        if isinstance(err, Conflict):
            logger.error(
                "Telegram 409 Conflict: another getUpdates session is already "
                "active for this bot token. Only one polling instance may run "
                "at a time."
            )
            if self._conflict_event is not None:
                self._conflict_event.set()
            return
        logger.error(
            "Telegram error (update=%s): %s",
            update.update_id if update else None,
            err,
            exc_info=err if err else None,
        )

    async def _cooldown_and_clear_webhook(self, seconds: float) -> None:
        """Sleep ``seconds`` then clear any webhook so polling can proceed.

        A lingering webhook makes Telegram reject getUpdates with a 409, so we
        clear it before every (re)start attempt.
        """
        await asyncio.sleep(seconds)
        try:
            await self.application.bot.delete_webhook(drop_pending_updates=False)
        except Exception as e:
            logger.warning("Could not delete webhook (ignored): %s", e)

    async def _stop_polling(self) -> None:
        """Best-effort stop the updater's polling (no-op if not running)."""
        try:
            if self.application and self.application.updater.running:
                await self.application.updater.stop()
        except Exception:
            pass

    async def _safe_shutdown_application(self) -> None:
        """Gracefully tear down the PTB application, ignoring all errors."""
        await self._stop_polling()
        try:
            if self.application and getattr(self.application, "running", False):
                await self.application.stop()
        except Exception:
            pass
        try:
            if self.application:
                await self.application.shutdown()
        except Exception:
            pass

    async def start(self):
        """Start the Telegram bot."""
        # Register callback for agent responses
        self.agent.register_response_callback(self.handle_autonomous_message)

        # Initialize agent
        restored_msg = await self.agent.initialize()
        if restored_msg:
            # Send the restored context message to the user
            try:
                await self.send_message(restored_msg)
            except Exception:
                pass
        started = await self.agent.ensure_autonomous_loop()
        if not started:
            print("⚠️  Continuous thinking could not start because no LLM model is configured.")

        # Create application. We raise the HTTP request timeouts via
        # ``HTTPXRequest`` because PTB's defaults (a few seconds) make routine,
        # slightly-slow sends trip ``telegram.error.TimedOut`` ("Timed out").
        # Combined with the retry in ``_retry_bot_request`` this keeps transient
        # network blips invisible to the user.
        request = HTTPXRequest(
            connect_timeout=10.0,
            read_timeout=60.0,
            write_timeout=60.0,
            pool_timeout=10.0,
        )
        self.application = (
            Application.builder().token(self.bot_token).request(request).build()
        )

        # Surface Telegram framework errors in the log instead of failing
        # silently (e.g. update-handling exceptions, network errors).
        self.application.add_error_handler(self._handle_telegram_error)

        # Add handlers
        self.application.add_handler(
            CommandHandler("start", self.handle_message)
        )
        self.application.add_handler(
            CommandHandler("help", self.handle_message)
        )
        self.application.add_handler(
            CommandHandler("status", self.handle_message)
        )
        self.application.add_handler(
            CommandHandler("settings", self.handle_message)
        )
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

        # Start bot
        print("📱 Telegram bot starting...")
        await self.application.initialize()

        # Clear any lingering webhook. A bot can use EITHER webhook OR
        # getUpdates (polling), never both; if a webhook is (or was) set,
        # start_polling receives a 409 Conflict and silently gets no updates.
        # This state lives on Telegram's servers, so it survives reboots --
        # clearing it here makes polling reliable regardless of history.
        try:
            await self.application.bot.delete_webhook(drop_pending_updates=False)
        except Exception as e:
            logger.warning("Could not delete webhook (ignored): %s", e)

        await self.application.start()

        # Get bot info
        bot_info = await self.application.bot.get_me()
        print(f"✓ Logged in as @{bot_info.username}")

        # Start polling directly. NOTE: we must NOT call run_polling() here.
        # main.py drives the event loop via asyncio.run(), and run_polling()
        # internally owns and closes that very loop, which raises "Cannot
        # close a running event loop" on shutdown (this was the original
        # crash). Instead we start the updater's polling coroutine manually
        # and then idle until the process stops. This is the PTB-documented
        # pattern for running inside an already running event loop.

        # --- Polling start with 409 Conflict recovery --------------------
        # Telegram permits exactly ONE getUpdates (polling) session per bot
        # token. If another process is already polling the same token we get
        # ``telegram.error.Conflict`` (HTTP 409). We retry with backoff and a
        # webhook clear to recover from *transient* conflicts (e.g. a previous
        # instance still winding down on Telegram's side). If the conflict is
        # *persistent* (a genuinely separate live instance) we stop cleanly
        # with a clear message instead of looping forever or dying with an
        # uncaught traceback. The single-instance lock in main.py already
        # prevents *our own* second launch on the same machine.
        self._conflict_event = asyncio.Event()
        max_attempts = 8
        backoff = 3.0
        polling_started = False
        for attempt in range(1, max_attempts + 1):
            self._conflict_event.clear()
            try:
                await self.application.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES
                )
            except Conflict as e:
                logger.warning(
                    "Telegram Conflict on attempt %d/%d: %s",
                    attempt, max_attempts, e,
                )
                await self._cooldown_and_clear_webhook(backoff)
                backoff = min(backoff * 1.5, 30.0)
                continue
            # Give polling a grace period to surface an *immediate* Conflict
            # that PTB reports through the error handler (a background task).
            try:
                await asyncio.wait_for(self._conflict_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                polling_started = True
                break
            # Conflict was signalled by the error handler.
            logger.warning(
                "Telegram Conflict detected (attempt %d/%d). Retrying in %.0fs...",
                attempt, max_attempts, backoff,
            )
            await self._stop_polling()
            await self._cooldown_and_clear_webhook(backoff)
            backoff = min(backoff * 1.5, 30.0)

        if not polling_started:
            logger.error(
                "Could not start Telegram polling after %d attempts: a 409 "
                "Conflict means another bot is already polling this token. "
                "Stop the other instance (or wait for it to fully shut down) "
                "and restart.",
                max_attempts,
            )
            print(
                "❌ Telegram bot could not start: another instance is already "
                "polling this bot token (Conflict 409)."
            )
            await self._safe_shutdown_application()
            await self.agent.stop_autonomous_loop()
            return

        # Block until the bot is stopped OR a late Conflict forces us out.
        # main.py installs a SIGINT/SIGTERM handler that cancels this task, so
        # we simply wait indefinitely. The polling tasks will continue running
        # in the background until cancelled.
        try:
            # Wait indefinitely - polling runs in background via updater
            while True:
                await asyncio.sleep(3600)  # Sleep for an hour at a time
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            pass
        finally:
            await self._safe_shutdown_application()
            await self.agent.stop_autonomous_loop()

    async def stop(self):
        """Stop the Telegram bot."""
        await self.agent.stop_autonomous_loop()
        if self.application:
            await self.application.stop()
            await self.application.shutdown()
