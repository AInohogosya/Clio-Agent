"""
Telegram Bot Integration for VEXIS-CLI AI Agent
Handles Telegram bot communication and message management
"""

import asyncio
import inspect
import threading
import time
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from functools import wraps

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

    # Stub classes so type annotations don't cause NameError at import time
    class Update:  # type: ignore[no-redef]
        """Stub for telegram.Update when python-telegram-bot is not installed."""
        ALL_TYPES = []

        def __init__(self):
            self.effective_user = None
            self.message = None

    class _ContextTypesStub:  # type: ignore[no-redef]
        """Stub for telegram.ext.ContextTypes when python-telegram-bot is not installed."""
        DEFAULT_TYPE = None

    ContextTypes = _ContextTypesStub()  # type: ignore[assignment]

    class Application:  # type: ignore[no-redef]
        """Stub for telegram.ext.Application when python-telegram-bot is not installed."""
        @staticmethod
        def builder():
            return None

    class CommandHandler:  # type: ignore[no-redef]
        """Stub for telegram.ext.CommandHandler when python-telegram-bot is not installed."""
        def __init__(self, *args, **kwargs):
            pass

    class MessageHandler:  # type: ignore[no-redef]
        """Stub for telegram.ext.MessageHandler when python-telegram-bot is not installed."""
        def __init__(self, *args, **kwargs):
            pass

    class _FiltersStub:  # type: ignore[no-redef]
        """Stub for telegram.ext.filters when python-telegram-bot is not installed."""
        TEXT = None
        COMMAND = None

        def __and__(self, other):
            return None

        def __invert__(self):
            return None

    filters = _FiltersStub()  # type: ignore[assignment]

from ..utils.logger import get_logger
from ..utils.config import load_config
from ..utils.resilience_engine import get_resilience_engine, classify_api_error, ErrorSeverity, ResilienceConfig


def retry_on_network_error(max_retries: int = 3, initial_delay: float = 1.0, backoff_factor: float = 2.0):
    """
    Decorator to retry network operations with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds before first retry
        backoff_factor: Multiplier for delay after each retry (exponential backoff)
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = get_logger("telegram_bot")
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # Check if it's a network-related error
                    error_msg = str(e).lower()
                    error_type_name = type(e).__name__.lower()
                    is_network_error = any(
                        keyword in error_msg
                        for keyword in ['timeout', 'network', 'connection', 'timed out', 'unreachable', 'readerror', 'read error']
                    ) or any(
                        keyword in error_type_name
                        for keyword in ['readerror', 'read_error', 'connectionerror', 'timeouterror']
                    )

                    if not is_network_error or attempt == max_retries:
                        # Not a network error or max retries reached, raise the exception
                        logger.error(f"Error in {func.__name__}: {e}")
                        raise

                    # Calculate delay with exponential backoff
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(
                        f"Network error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f} seconds..."
                    )
                    await asyncio.sleep(delay)

            # If we get here, all retries failed
            raise last_exception

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = get_logger("telegram_bot")
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # Check if it's a network-related error
                    error_msg = str(e).lower()
                    error_type_name = type(e).__name__.lower()
                    is_network_error = any(
                        keyword in error_msg
                        for keyword in ['timeout', 'network', 'connection', 'timed out', 'unreachable', 'readerror', 'read error']
                    ) or any(
                        keyword in error_type_name
                        for keyword in ['readerror', 'read_error', 'connectionerror', 'timeouterror']
                    )

                    if not is_network_error or attempt == max_retries:
                        # Not a network error or max retries reached, raise the exception
                        logger.error(f"Error in {func.__name__}: {e}")
                        raise

                    # Calculate delay with exponential backoff
                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(
                        f"Network error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f} seconds..."
                    )
                    time.sleep(delay)

            # If we get here, all retries failed
            raise last_exception

        # Return appropriate wrapper based on whether function is async
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class TelegramMode(Enum):
    """Telegram bot mode"""
    NORMAL = "normal"
    TELEGRAM = "telegram"


@dataclass
class ConversationHistory:
    """Conversation history for Telegram mode"""
    user_id: int
    messages: List[Dict[str, str]] = field(default_factory=list)
    max_length: int = 50

    def add_message(self, role: str, content: str):
        """Add a message to the conversation history"""
        if content is None:
            return
        self.messages.append({"role": role, "content": content})
        # Trim to max length
        if len(self.messages) > self.max_length:
            self.messages = self.messages[-self.max_length:]

    def get_history(self) -> List[Dict[str, str]]:
        """Get the conversation history"""
        return self.messages

    def clear(self):
        """Clear the conversation history"""
        self.messages = []

    def format_for_prompt(self) -> str:
        """Format conversation history for inclusion in prompts"""
        if not self.messages:
            return ""

        formatted = "\nConversation History:\n"
        for msg in self.messages:
            formatted += f"{msg['role']}: {msg['content']}\n"
        return formatted


@dataclass
class QueuedTelegramMessage:
    """Telegram message waiting to be sent from the queue processor."""

    chat_id: int
    message: str
    attempts: int = 0
    next_attempt_at: float = 0.0




@dataclass
class RunningTelegramTask:
    """A Telegram pipeline task plus the cancellation event passed to it."""

    task: asyncio.Task
    cancel_event: threading.Event


class TelegramBotManager:
    """
    Manages Telegram bot integration for AI agent

    Handles:
    - Bot initialization and message receiving
    - Conversation history management
    - Message sending

    """

    def __init__(self, bot_token: str, allowed_user_ids: Optional[List[int]] = None, max_history_length: int = 50, terminal_history=None):
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids or []
        self.max_history_length = max_history_length
        self.logger = get_logger("telegram_bot")
        self.terminal_history = terminal_history

        # Conversation history per user
        self.conversation_histories: Dict[int, ConversationHistory] = {}

        # Callback for processing messages
        self.message_callback: Optional[Callable[[str, int], str]] = None
        self.restart_callback: Optional[Callable[[int], None]] = None
        # Callback for injecting user messages into the engine's execution log
        self.user_message_callback: Optional[Callable[[str, int], None]] = None

        # Track running tasks per user so a newer prompt can supersede old work
        self._current_tasks: Dict[int, RunningTelegramTask] = {}
        self._task_lock = asyncio.Lock()

        # Application instance
        self.application: Optional[Application] = None

        # Running state
        self.is_running = False
        self._should_restart = True

        # Message queue for sending messages from synchronous context
        self.message_queue: List[QueuedTelegramMessage] = []
        self._queue_lock = threading.Lock()
        self._max_queue_send_attempts = 5
        self._queue_retry_delay = 2.0

        # Background thread for processing message queue
        self.queue_processor_thread: Optional[threading.Thread] = None
        self.queue_processor_running = False

        # Resilience engine for error recovery
        self._resilience = get_resilience_engine()

        # Check if telegram is available
        if not TELEGRAM_AVAILABLE:
            self.logger.error("python-telegram-bot not installed. Install with: pip install python-telegram-bot>=21.0.0")

    def set_message_callback(self, callback: Callable[[str, int], str]):
        """Set the callback function for processing messages"""
        self.message_callback = callback

    def set_restart_callback(self, callback: Callable[[int], None]):
        """Set the callback function used by the /restart command."""
        self.restart_callback = callback

    def set_user_message_callback(self, callback: Callable[[str, int], None]):
        """Set the callback function used to inject user messages into the engine."""
        self.user_message_callback = callback

    def set_shared_conversation_history(self, history: ConversationHistory):
        """Set the shared conversation history (user_id=0) so that
        user messages added via handle_message are visible to the
        autonomous loop's thinking prompt.
        """
        self.conversation_histories[history.user_id] = history

    def get_conversation_history(self, user_id: int) -> ConversationHistory:
        """Get or create conversation history for a user"""
        if user_id not in self.conversation_histories:
            self.conversation_histories[user_id] = ConversationHistory(
                user_id=user_id,
                max_length=self.max_history_length
            )
        return self.conversation_histories[user_id]

    def clear_conversation_history(self, user_id: int):
        """Clear conversation history for a user"""
        if user_id in self.conversation_histories:
            self.conversation_histories[user_id].clear()
            self.logger.info(f"Cleared conversation history for user {user_id}")

    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_user_allowed(user_id):
            await update.message.reply_text("Sorry, you are not authorized to use this bot.")
            return

        await update.message.reply_text(
                    "VEXIS-CLI AI Agent\n\n"
                    "Send me commands and I'll execute them on your computer.\n"
                    "Use /restart to restart while keeping current settings.\n"
                    "Use /help for more information.\n\n"
                    "Note: I check my conversation history and execution log before "
                    "sending any message to avoid duplicate responses."
                )

    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def restart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /restart command"""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_user_allowed(user_id):
            return

        await self._cancel_user_task(user_id)
        await update.message.reply_text("Restarting VEXIS-CLI with the same provider, model, and API settings...")

        if self.restart_callback:
            self.restart_callback(user_id)
        else:
            await update.message.reply_text("Restart is not configured for this bot session.")

    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_user_allowed(user_id):
            return

        await update.message.reply_text(
                    "VEXIS-CLI AI Agent Help\n\n"
                    "Commands:\n"
                    "/start - Start the bot\n"
                    "/restart - Restart while keeping current provider/model/API settings\n"
                    "/help - Show this help message\n\n"
                    "Just send any instruction and I'll execute it on your computer!\n\n"
                    "Note: I check my conversation history and execution log before sending "
                    "any message to avoid sending duplicate responses."
                )

    async def _cancel_user_task(self, user_id: int):
        """Signal any running task for the specified user to stop."""
        running = self._current_tasks.get(user_id)
        if not running:
            return

        if not running.task.done():
            self.logger.info(f"Cancelling running task for user {user_id}")
            running.cancel_event.set()
            running.task.cancel()

    async def _process_message_async(self, user_message: str, user_id: int,
                                      processing_msg, history, cancel_event: threading.Event) -> str:
        """Process message asynchronously with cancellation support."""
        if self.message_callback:
            loop = asyncio.get_event_loop()
            if len(inspect.signature(self.message_callback).parameters) >= 3:
                return await loop.run_in_executor(None, self.message_callback, user_message, user_id, cancel_event)
            return await loop.run_in_executor(None, self.message_callback, user_message, user_id)
        return "Message callback not set. Bot not properly configured."

    @retry_on_network_error(max_retries=10, initial_delay=1.0, backoff_factor=2.0)
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages: start a background task for AI processing."""
        if not update.effective_user or not update.message:
            return

        user_id = update.effective_user.id

        if not self._is_user_allowed(user_id):
            await update.message.reply_text("Sorry, you are not authorized to use this bot.")
            return

        if not update.message.text:
            return

        user_message = update.message.text

        # Check for slash commands that may arrive via text handlers in some clients
        if user_message.strip() == "/restart":
            await self.restart_command(update, context)
            return

        # Track the first user we hear from so the engine knows where to send telegram() replies
        if not getattr(self, "_boot_user_id", None):
            self._boot_user_id = user_id

        # Add user message to conversation history
        history = self.get_conversation_history(user_id)
        history.add_message("user", user_message)

        # Also add to the shared conversation history (user_id=0) that the engine reads
        shared_history = self.get_conversation_history(0)
        shared_history.add_message("user", user_message)

        # Inject the message into the engine's execution log so the agent sees it
        if self.user_message_callback:
            try:
                self.user_message_callback(user_message, user_id)
            except Exception as e:
                self.logger.warning(f"Failed to inject user message into engine: {e}")

        # Cancel any existing running task for this user
        await self._cancel_user_task(user_id)

        # When message_callback is None (autonomous loop mode), the agent
        # handles replies via telegram() commands.  We do NOT need to spawn
        # a background task that would just wait around doing nothing.
        if self.message_callback is None:
            self.logger.info(
                f"No message_callback set; autonomous loop will handle "
                f"user {user_id}'s message via telegram() commands."
            )
            # Send a quick acknowledgement so the user knows the message
            # was received.  The agent's actual reply will come via
            # telegram() commands on the next thinking iteration.
            try:
                await update.message.reply_text(
                    "✅ Message received! The AI agent is processing your request.\n"
                    "Reply will be sent shortly via the agent."
                )
            except Exception:
                pass
            # Flush the message queue so any pending telegram() replies from
            # the agent are delivered immediately.
            try:
                await self.process_message_queue()
            except Exception as e:
                self.logger.warning(f"Failed to flush message queue: {e}")
            return

        # Send a "processing" message that the task will edit with the final reply
        processing_msg = None
        try:
            processing_msg = await update.message.reply_text("Processing your message...")
        except Exception:
            pass
        cancel_event = threading.Event()
        task = asyncio.create_task(
            self._handle_message_task(user_id, user_message, processing_msg, history, cancel_event)
        )
        async with self._task_lock:
            self._current_tasks[user_id] = RunningTelegramTask(task=task, cancel_event=cancel_event)

    async def _handle_message_task(self, user_id: int, user_message: str,
                                    processing_msg, history, cancel_event: threading.Event):
        """Actual message processing task that can be cancelled.

        In Telegram-Mode the autonomous loop handles responses via telegram()
        commands.  This task acts as a safety-net: if the loop does not
        produce a reply within a reasonable time, we let the user know the
        message was received and is being processed.
        """
        try:
            response = None
            if self.message_callback:
                response = await self._process_message_async(user_message, user_id, processing_msg, history, cancel_event)

                # Check if task was cancelled
                if cancel_event.is_set():
                    self.logger.info(f"Task for user {user_id} was cancelled, skipping response")
                    return

                # Add assistant response to conversation history (only if non-None)
                if response is not None:
                    history.add_message("assistant", response)

                # Truncate long messages if exceeds Telegram limit (4096 chars)
                if response and len(response) > 4000:
                    self.logger.info(f"Response is {len(response)} chars, truncating with [omitted]")
                    response = self._truncate_message(response, max_length=4000)

                # Send response: edit the "processing" message, or send a new one if edit fails
                if processing_msg and response is not None:
                    try:
                        await processing_msg.edit_text(response)
                    except Exception as edit_err:
                        self.logger.warning(f"Could not edit processing message: {edit_err}. Sending as new message.")
                        try:
                            await processing_msg.chat.send_message(response)
                        except Exception as send_err:
                            self.logger.error(f"Failed to send response as new message: {send_err}")
                elif response is not None:
                    # processing_msg was never created (reply_text failed); send as new message
                    try:
                        await self.application.bot.send_message(chat_id=user_id, text=response)
                    except Exception as send_err:
                        self.logger.error(f"Failed to send response as new message: {send_err}")

                # Queued phase messages are sent by the background queue
                # processor. Do not drain the queue here: doing so can keep this
                # handler alive indefinitely if Telegram is temporarily down.
            else:
                # No direct message_callback — the autonomous loop replies
                # via telegram() commands.  Just update the processing message
                # so the user knows the message was received.
                self.logger.info(
                    f"No message_callback set; autonomous loop will handle "
                    f"user {user_id}'s message via telegram() commands."
                )
                if processing_msg:
                    try:
                        await processing_msg.edit_text(
                            "The AI agent is processing your message.\n"
                            "Reply will be sent via the agent's autonomous loop."
                        )
                    except Exception:
                        pass

        except asyncio.CancelledError:
            self.logger.info(f"Task for user {user_id} cancelled - switching to new task")
            try:
                if processing_msg:
                    await processing_msg.edit_text("Task cancelled - processing new request...")
            except Exception as e:
                self.logger.error(f"Error editing message: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            try:
                if processing_msg:
                    await processing_msg.edit_text(f"Error processing your request: {str(e)}")
            except Exception as edit_err:
                self.logger.error(f"Error editing message: {edit_err}")
                try:
                    if processing_msg:
                        await processing_msg.chat.send_message(f"Error: {str(e)}")
                except Exception:
                    pass
        finally:
            # Clean up task reference
            try:
                async with self._task_lock:
                    running = self._current_tasks.get(user_id)
                    if running and running.task == asyncio.current_task():
                        del self._current_tasks[user_id]
            except Exception:
                pass  # best-effort cleanup during shutdown

    def _is_user_allowed(self, user_id: int) -> bool:
        """Check if user is allowed to use the bot"""
        if not self.allowed_user_ids:
            # If no allowed users specified, allow everyone
            return True
        return user_id in self.allowed_user_ids

    def _truncate_message(self, message: str, max_length: int = 4000) -> str:
        """Truncate message if it exceeds max length, adding [omitted] in the middle.

        Keeps beginning and end of message, omitting the middle portion.
        Format: "<beginning> [omitted] <end>"

        Note: length is measured in characters (not bytes), which is what
        Telegram's API uses for its 4096-character limit.
        """
        if len(message) <= max_length:
            return message

        omitted_tag = " [omitted] "
        tag_len = len(omitted_tag)
        if max_length <= tag_len:
            return message[:max_length]

        available_space = max_length - tag_len
        half_space = available_space // 2

        beginning = message[:half_space]
        end = message[-(available_space - half_space):]

        return f"{beginning}{omitted_tag}{end}"

    @retry_on_network_error(max_retries=2, initial_delay=0.5, backoff_factor=2.0)
    async def send_message(self, chat_id: int, message: str):
        """Send a message to a specific chat"""
        if not self.application:
            self.logger.error("Telegram application not initialized")
            return False

        # Truncate if too long
        if len(message) > 4000:
            self.logger.warning(f"Message too long ({len(message)} chars), truncating with [omitted]")
            message = self._truncate_message(message, max_length=4000)

        await self.application.bot.send_message(chat_id=chat_id, text=message)
        return True

    def queue_message(self, chat_id: int, message: str):
        """
        Queue a message to be sent from the async event loop.
        This method is synchronous and can be called from any context.
        """
        with self._queue_lock:
            self.message_queue.append(QueuedTelegramMessage(chat_id=chat_id, message=message))
        self.logger.info(f"Message queued for chat {chat_id}")

    async def process_message_queue(self):
        """Process currently-sendable queued messages once and return.

        This method is intentionally bounded. The old implementation retried
        forever inside request handling, which could leave a user task marked as
        running and cause later Telegram messages to appear ignored.
        """
        for queued_message in self._pop_sendable_messages():
            await self._send_queued_message(queued_message)

    def _pop_sendable_messages(self) -> List[QueuedTelegramMessage]:
        """Pop messages that are due to be sent, leaving delayed retries queued."""
        now = time.time()
        sendable: List[QueuedTelegramMessage] = []
        delayed: List[QueuedTelegramMessage] = []

        with self._queue_lock:
            for queued_message in self.message_queue:
                if queued_message.next_attempt_at <= now:
                    sendable.append(queued_message)
                else:
                    delayed.append(queued_message)

            self.message_queue = delayed

        return sendable

    async def _send_queued_message(self, queued_message: QueuedTelegramMessage):
        """Send a queued message once, re-queueing with a bounded retry budget."""
        try:
            await self.send_message(queued_message.chat_id, queued_message.message)
            self.logger.info(f"Sent queued message to user {queued_message.chat_id}")
        except Exception as e:
            queued_message.attempts += 1
            if queued_message.attempts >= self._max_queue_send_attempts:
                self.logger.error(
                    f"Dropping queued message to user {queued_message.chat_id} "
                    f"after {queued_message.attempts} failed attempts: {e}"
                )
                return

            queued_message.next_attempt_at = time.time() + (self._queue_retry_delay * queued_message.attempts)
            self.logger.warning(
                f"Failed to send queued message to user {queued_message.chat_id}: {e}. "
                f"Retry {queued_message.attempts}/{self._max_queue_send_attempts} scheduled."
            )
            with self._queue_lock:
                self.message_queue.append(queued_message)

    async def _queue_flush_callback(self, context):
        """Periodic callback to flush queued outbound messages.
        Registered on the application's job_queue so it runs on the
        correct event loop with a valid bot session."""
        try:
            messages_to_send = self._pop_sendable_messages()
            for queued_message in messages_to_send:
                await self._send_queued_message(queued_message)
        except Exception as e:
            self.logger.error(f"Error in queue flush callback: {e}")

    def _start_queue_processor(self):
        """Start a periodic job on the application's event loop to flush the
        outbound message queue.  Using the application's own loop avoids the
        cross-event-loop session mismatch that occurred with the old background
        thread approach."""
        if not self.application:
            self.logger.warning("Cannot start queue processor: application not initialised")
            return

        try:
            jq = self.application.job_queue
            if jq is not None:
                jq.run_repeating(
                    self._queue_flush_callback,
                    interval=self._queue_retry_delay,
                    first=0.0,
                    name="queue-flusher",
                )
                self.logger.info("Queue flusher registered on application job_queue")
                return
        except Exception as e:
            self.logger.warning(f"job_queue not available ({e}), falling back to background thread")

        # Fallback: background thread (same approach as before, but only used
        # when the application has no job_queue).
        def queue_processor():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.queue_processor_running = True
            while self.queue_processor_running:
                if self.application:
                    try:
                        messages_to_send = self._pop_sendable_messages()
                        for queued_message in messages_to_send:
                            loop.run_until_complete(self._send_queued_message(queued_message))
                    except Exception as e:
                        self.logger.error(f"Error in queue processor: {e}")
                        time.sleep(2)
                time.sleep(0.1)
            loop.close()
            self.logger.info("Queue processor stopped")

        self.queue_processor_thread = threading.Thread(target=queue_processor, daemon=True)
        self.queue_processor_thread.start()
        self.logger.info("Queue processor thread started (fallback)")

    def _stop_queue_processor(self):
        """Stop background queue processor"""
        self.queue_processor_running = False
        if self.queue_processor_thread:
            self.queue_processor_thread.join(timeout=2)
            self.logger.info("Queue processor thread stopped")

    def start_bot(self):
        """Start the Telegram bot (blocking)"""
        if not TELEGRAM_AVAILABLE:
            self.logger.error("Cannot start bot: python-telegram-bot not installed")
            return False

        if not self.bot_token:
            self.logger.error("Cannot start bot: bot_token not set")
            return False

        # Outer loop to ensure session remains active after task completion
        while self._should_restart:
            try:
                # Create application
                self.application = Application.builder().token(self.bot_token).build()

                # Add handlers
                self.application.add_handler(CommandHandler("start", self.start_command))
                self.application.add_handler(CommandHandler("restart", self.restart_command))
                self.application.add_handler(CommandHandler("help", self.help_command))
                self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

                # Start queue processor thread
                self._start_queue_processor()

                # Start bot
                self.is_running = True
                self.logger.info("Starting Telegram bot...")
                self.application.run_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )

                # After run_polling returns (e.g., due to network error),
                # the loop will restart and wait for the next task
                self.logger.info("Telegram bot polling stopped")
                self.is_running = False
                self._stop_queue_processor()

                if self._should_restart:
                    self.logger.info("Restarting Telegram bot polling...")
                    self.application = None
                    time.sleep(2)

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received, stopping Telegram bot")
                self.is_running = False
                self._should_restart = False
                self._stop_queue_processor()
                if self.application:
                    try:
                        self.application.stop()
                    except Exception:
                        pass
                    self.application = None
                break
            except Exception as e:
                self.logger.error(f"Error in Telegram bot: {e}")
                self.is_running = False
                self._stop_queue_processor()

                # Classify the error to determine recovery strategy
                severity, category, is_retryable, suggested_delay = classify_api_error(e)

                if not is_retryable or not self._should_restart:
                    self.logger.error(f"Telegram bot encountered non-retryable error ({category.value}): {e}")
                    break

                # Exponential backoff with jitter
                base_wait = max(suggested_delay, 5.0)
                # Each consecutive failure increases the wait
                self._resilience.record_failure("telegram:polling")
                cb = self._resilience._circuit_breakers.get("telegram:polling", {})
                consecutive_failures = cb.get("failures", 1) if isinstance(cb, dict) else 1
                wait = min(base_wait * (2 ** min(consecutive_failures, 6)), 300.0)  # Cap at 5 minutes

                self.logger.info(f"Waiting {wait:.0f}s before restarting Telegram (consecutive failures: {consecutive_failures})")
                time.sleep(wait)

        return True

    async def _stop_application(self):
        """Internal method to stop the application from async context."""
        if self.application:
            try:
                await self.application.stop()
                await self.application.shutdown()
                self.logger.info("Telegram application stopped and shut down")
            except Exception as e:
                self.logger.error(f"Error stopping application: {e}")

    def stop_bot(self):
        """Stop the Telegram bot gracefully"""
        self.is_running = False
        self._should_restart = False
        self._stop_queue_processor()

        if self.application:
            self.logger.info("Stopping Telegram bot...")
            try:
                # Try to get the running loop (if we're in an async context)
                loop = asyncio.get_running_loop()
                # Schedule stop on the running loop from a thread-safe context
                future = asyncio.run_coroutine_threadsafe(
                    self._stop_application(), loop
                )
                future.result(timeout=10)
            except RuntimeError:
                # No running loop - we're in a sync context
                # The application will stop when run_polling returns
                # Just signal that we want to stop
                self.logger.info("No running event loop, bot will stop on next polling cycle")
            except Exception as e:
                self.logger.error(f"Error stopping application: {e}")


def create_telegram_bot(config_path: Optional[str] = None, terminal_history=None) -> Optional[TelegramBotManager]:
    """
    Create a Telegram bot manager from configuration

    Args:
        config_path: Path to config.yaml file. If None, loads from default location.
        terminal_history: Optional TerminalHistory instance for command execution.

    Returns:
        TelegramBotManager instance or None if telegram is disabled or not available
    """
    if not TELEGRAM_AVAILABLE:
        print("python-telegram-bot library not installed")
        print("To enable Telegram mode, install it with:")
        print("  pip install python-telegram-bot>=21.0.0")
        return None

    try:
        import yaml
        from pathlib import Path
        from ..core_processing.terminal_history import get_terminal_history

        # Use provided terminal_history or get default
        if terminal_history is None:
            terminal_history = get_terminal_history()


        # Load config directly from YAML to avoid singleton cache
        config_dict = {}
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                config_dict = yaml.safe_load(f) or {}


        # If config_dict is empty or telegram section not found, try Config object
        telegram_config = None
        if config_dict.get('telegram'):
            telegram_config = config_dict['telegram']
        else:
            # Fallback to default config loading
            config_obj = load_config()
            if hasattr(config_obj, 'telegram'):
                # telegram is a TelegramConfig dataclass
                telegram_config = config_obj.telegram
                # Convert dataclass to dict if needed
                if hasattr(telegram_config, '__dataclass_fields__'):
                    from dataclasses import asdict
                    telegram_config = asdict(telegram_config)
                elif hasattr(telegram_config, '__dict__'):
                    telegram_config = telegram_config.__dict__

        # If still no telegram config, return None
        if not telegram_config:
            telegram_config = {}

        if not telegram_config.get('enabled', False):
            return None

        bot_token = telegram_config.get('bot_token') or ''
        if not bot_token:
            print("Telegram bot token not configured")
            print("Please set bot_token in config.yaml under telegram section")
            return None

        # Support both 'authorized_users' (config.yaml key) and 'allowed_user_ids' (legacy key)
        raw_allowed = (
            telegram_config.get('authorized_users')
            or telegram_config.get('allowed_user_ids')
            or []
        )
        allowed_user_ids = [int(uid) for uid in raw_allowed if uid is not None]
        max_history_length = telegram_config.get('max_history_length', 50)

        return TelegramBotManager(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            max_history_length=max_history_length,
            terminal_history=terminal_history
        )
    except Exception as e:
        print(f"Error loading Telegram configuration: {e}")
        return None
