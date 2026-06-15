"""
Telegram Bot Integration for VEXIS-CLI AI Agent
Handles Telegram bot communication and message management.

HARDENED VERSION — fixes the primary failure modes for a 24/7 agent:
  * All outbound sends are marshalled to the bot's owning event loop so the
    Bot session is never used from the wrong thread/loop.
  * queue_message() is synchronous, thread-safe, and never blocks.
  * The message queue is drained periodically and immediately after each new
    enqueue so messages do not sit undelivered.
  * Lifecycle is managed explicitly (initialize -> start -> polling -> stop ->
    shutdown) preventing resource leaks across reconnections.
  * Automatic reconnection with exponential backoff and jitter ensures the
    bot survives network outages.
  * chat_id resolution prefers an explicit id, then the last inbound user, then
    the boot user.
"""

import asyncio
import hashlib
import inspect
import json
import os
import random
import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

    # Stub classes so rest of module is importable when python-telegram-bot
    # is not installed.
    class Update:  # type: ignore[no-redef]
        ALL_TYPES = []

        def __init__(self):
            self.effective_user = None
            self.message = None

    class _ContextTypesStub:
        DEFAULT_TYPE = None

    ContextTypes = _ContextTypesStub()  # type: ignore[assignment]

    class Application:  # type: ignore[no-redef]
        @staticmethod
        def builder():
            return None

    class CommandHandler:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

    class MessageHandler:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

    class _FiltersStub:  # type: ignore[no-redef]
        TEXT = None
        COMMAND = None

        def __and__(self, other):
            return None

        def __invert__(self):
            return None

    filters = _FiltersStub()  # type: ignore[assignment]

from ..utils.logger import get_logger
from ..utils.config import load_config
from ..utils.resilience_engine import (
    get_resilience_engine,
    classify_api_error,
    ErrorSeverity,
    ResilienceConfig,
)


def retry_on_network_error(max_retries: int = 3, initial_delay: float = 1.0,
                           backoff_factor: float = 2.0):
    """Decorator to retry coroutine functions on network-ish failures."""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc: Optional[Exception] = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if attempt == max_retries:
                        raise
                    if not _is_network_error(exc):
                        raise
                    delay = initial_delay * (backoff_factor ** attempt)
                    delay *= 0.75 + 0.5 * random.random()  # jitter
                    # MUST be async sleep: this wrapper only ever wraps
                    # coroutine functions (see iscoroutinefunction guard
                    # below), and they run on the bot's event loop. A
                    # blocking time.sleep() here would freeze the entire
                    # loop, stalling both sending AND receiving.
                    await asyncio.sleep(delay)

        if inspect.iscoroutinefunction(func):
            return wrapper
        return func

    return decorator


def _is_network_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    network_markers = (
        "timeout", "timed out", "network", "connection", "unreachable",
        "readerror", "read error", "sslerror", "proxyerror", "reset by peer",
        "connection reset", "broken pipe", "temporary failure",
        "name or service not known", "could not resolve host",
    )
    for marker in network_markers:
        if marker in msg or marker in name:
            return True
    try:
        from telegram.error import NetworkError, TimedOut
        return isinstance(exc, (NetworkError, TimedOut))
    except Exception:
        return False


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
        if content is None:
            return
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self.max_length:
            self.messages = self.messages[-self.max_length:]

    def get_history(self) -> List[Dict[str, str]]:
        return list(self.messages)

    def clear(self):
        self.messages = []

    def format_for_prompt(self) -> str:
        if not self.messages:
            return ""
        formatted = "\nConversation History:\n"
        for msg in self.messages:
            formatted += f"{msg['role']}: {msg['content']}\n"
        return formatted


@dataclass
class QueuedTelegramMessage:
    """Outbound message waiting to be delivered."""

    chat_id: int
    message: str
    attempts: int = 0
    next_attempt_at: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class RunningTelegramTask:
    """A Telegram pipeline task plus the cancellation event passed to it."""

    task: asyncio.Task
    cancel_event: threading.Event


class TelegramBotManager:
    """
    Manages Telegram bot integration for the AI agent.
    All send operations are safe to call from any thread.
    """

    def __init__(
        self,
        bot_token: str,
        allowed_user_ids: Optional[List[int]] = None,
        max_history_length: int = 50,
        terminal_history=None,
    ):
        self.bot_token = bot_token
        self.allowed_user_ids: List[int] = allowed_user_ids or []
        self.max_history_length = max_history_length
        self.terminal_history = terminal_history
        self.logger = get_logger("telegram_bot")

        # Conversation history per user
        self.conversation_histories: Dict[int, ConversationHistory] = {}

        # Callbacks
        self.message_callback: Optional[Callable[[str, int], str]] = None
        self.restart_callback: Optional[Callable[[int], None]] = None
        self.user_message_callback: Optional[Callable[[str, int], None]] = None

        # Track running tasks per user so a newer prompt can supersede old work
        self._current_tasks: Dict[int, RunningTelegramTask] = {}
        self._task_lock = asyncio.Lock()

        # Application instance and lifecycle
        self.application: Optional[Application] = None
        self.is_running = False
        self._should_restart = True

        # Queue for outbound messages
        self.message_queue: List[QueuedTelegramMessage] = []
        self._queue_lock = threading.Lock()
        self._max_queue_send_attempts = 5
        self._queue_retry_delay = 2.0

        # Lifecycle coordination
        self._own_loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._processor_running = False
        self._processor_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._last_user_id: Optional[int] = None
        self._boot_user_id: Optional[int] = None

        # Resilience
        self._resilience = get_resilience_engine()

        if not TELEGRAM_AVAILABLE:
            self.logger.error(
                "python-telegram-bot not installed. Install with: "
                "pip install 'python-telegram-bot[job-queue]>=21.0.0'"
            )

    # ------------------------------------------------------------------ #
    #  Callback setters
    # ------------------------------------------------------------------ #

    def set_message_callback(self, callback: Callable[[str, int], str]):
        self.message_callback = callback

    def set_restart_callback(self, callback: Callable[[int], None]):
        self.restart_callback = callback

    def set_user_message_callback(self, callback: Callable[[str, int], None]):
        self.user_message_callback = callback

    def set_shared_conversation_history(self, history: ConversationHistory):
        self.conversation_histories[history.user_id] = history

    # ------------------------------------------------------------------ #
    #  Conversation history helpers
    # ------------------------------------------------------------------ #

    def get_conversation_history(self, user_id: int) -> ConversationHistory:
        if user_id not in self.conversation_histories:
            self.conversation_histories[user_id] = ConversationHistory(
                user_id=user_id, max_length=self.max_history_length
            )
        return self.conversation_histories[user_id]

    def clear_conversation_history(self, user_id: int):
        if user_id in self.conversation_histories:
            self.conversation_histories[user_id].clear()
            self.logger.info(f"Cleared conversation history for user {user_id}")

    def _is_user_allowed(self, user_id: int) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids

    # ------------------------------------------------------------------ #
    #  Message sending & queueing (thread-safe)
    # ------------------------------------------------------------------ #

    def _resolve_chat_id(self, explicit_id: Optional[int] = None) -> Optional[int]:
        return explicit_id or self._last_user_id or self._boot_user_id

    @retry_on_network_error(max_retries=3, initial_delay=0.5, backoff_factor=2.0)
    async def send_message(self, chat_id: int, message: str) -> bool:
        """Send a message. Must be called from the bot's event loop."""
        if not self.application:
            return False
        if not message:
            return True
        if len(message) > 4000:
            message = self._truncate_message(message, max_length=4000)
        await self.application.bot.send_message(chat_id=chat_id, text=message,
                                                disable_web_page_preview=True)
        return True

    def queue_message(self, chat_id: int, message: str):
        """
        Enqueue a message to be sent. Safe to call from any thread; never blocks.
        """
        if not message:
            return
        with self._queue_lock:
            self.message_queue.append(
                QueuedTelegramMessage(chat_id=chat_id, message=message)
            )
            queue_length = len(self.message_queue)
        self.logger.info(
            f"Message queued for chat {chat_id} (queue length: {queue_length})"
        )
        self._trigger_queue_flush()

    def _trigger_queue_flush(self):
        """Schedule an immediate queue flush on the bot loop if available."""
        loop = self._own_loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._drain_queue_once(), loop)
        except Exception as exc:
            self.logger.debug(f"Immediate flush scheduling failed: {exc}")

    async def _drain_queue_once(self):
        """Drain all currently-sendable queued messages."""
        for queued_message in self._pop_sendable_messages():
            await self._send_queued_message(queued_message)

    def _pop_sendable_messages(self) -> List[QueuedTelegramMessage]:
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
        try:
            success = await self.send_message(
                queued_message.chat_id, queued_message.message
            )
            if success:
                self.logger.info(
                    f"Sent queued message to user {queued_message.chat_id}"
                )
                return
        except Exception as exc:
            self.logger.warning(
                f"Failed to send queued message to {queued_message.chat_id}: {exc}"
            )

        queued_message.attempts += 1
        if queued_message.attempts >= self._max_queue_send_attempts:
            self.logger.error(
                f"Dropping queued message to user {queued_message.chat_id} "
                f"after {queued_message.attempts} failed attempts"
            )
            return

        queued_message.next_attempt_at = (
            time.time() + self._queue_retry_delay * queued_message.attempts
        )
        with self._queue_lock:
            self.message_queue.append(queued_message)
        self.logger.warning(
            f"Re-queued message to user {queued_message.chat_id} "
            f"(attempt {queued_message.attempts}/{self._max_queue_send_attempts})"
        )

    def _start_queue_processor(self):
        """Start a background thread that periodically drains the queue."""
        if self._processor_running:
            return
        self._processor_running = True
        self._processor_thread = threading.Thread(
            target=self._queue_processor_loop, daemon=True, name="telegram-queue"
        )
        self._processor_thread.start()
        self.logger.info("Telegram queue processor started")

    def _queue_processor_loop(self):
        """Periodically flush the queue on the bot's event loop."""
        while self._processor_running:
            if self._loop_ready.wait(timeout=0.5):
                loop = self._own_loop
                if loop and not loop.is_closed() and loop.is_running():
                    try:
                        fut = asyncio.run_coroutine_threadsafe(
                            self._drain_queue_once(), loop
                        )
                        fut.result(timeout=15)
                    except Exception as exc:
                        self.logger.debug(
                            f"Queue flush cycle failed (will retry): {exc}"
                        )
                # Brief pause so we don't hammer the loop when empty
                time.sleep(self._queue_retry_delay)
            else:
                time.sleep(0.1)
        self.logger.info("Telegram queue processor stopped")

    def _stop_queue_processor(self):
        """Stop the queue processor thread."""
        self._processor_running = False
        if self._processor_thread and self._processor_thread.is_alive():
            self._processor_thread.join(timeout=2)

    # ------------------------------------------------------------------ #
    #  Command handlers
    # ------------------------------------------------------------------ #

    @retry_on_network_error(max_retries=5, initial_delay=1.0, backoff_factor=2.0)
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not update.message:
            return
        user_id = update.effective_user.id
        if not self._is_user_allowed(user_id):
            await update.message.reply_text(
                "Sorry, you are not authorized to use this bot."
            )
            return
        await update.message.reply_text(
            "Clio Agent\n\n"
            "Send me instructions and I'll keep working on your machine.\n"
            "Use /restart to restart while keeping current settings.\n"
            "Use /help for more information."
        )

    @retry_on_network_error(max_retries=5, initial_delay=1.0, backoff_factor=2.0)
    async def restart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not update.message:
            return
        user_id = update.effective_user.id
        if not self._is_user_allowed(user_id):
            return
        await self._cancel_user_task(user_id)
        await update.message.reply_text(
            "Restarting Clio Agent with the same provider, model, and API settings..."
        )
        if self.restart_callback:
            try:
                self.restart_callback(user_id)
            except Exception as exc:
                self.logger.warning(f"restart_callback failed: {exc}")
        else:
            await update.message.reply_text(
                "Restart is not configured for this bot session."
            )

    @retry_on_network_error(max_retries=5, initial_delay=1.0, backoff_factor=2.0)
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not update.message:
            return
        user_id = update.effective_user.id
        if not self._is_user_allowed(user_id):
            return
        await update.message.reply_text(
            "Clio Agent Help\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/restart - Restart while keeping current provider/model/API settings\n"
            "/help - Show this help message\n\n"
            "Just send any instruction and I'll execute it on your computer!"
        )

    async def _cancel_user_task(self, user_id: int):
        running = self._current_tasks.get(user_id)
        if not running:
            return
        if not running.task.done():
            self.logger.info(f"Cancelling running task for user {user_id}")
            running.cancel_event.set()
            running.task.cancel()

    async def _process_message_async(
        self,
        user_message: str,
        user_id: int,
        processing_msg,
        _history,
        cancel_event: threading.Event,
    ) -> str:
        if not self.message_callback:
            return "Message callback not set. Bot not properly configured."
        loop = asyncio.get_running_loop()
        sig = inspect.signature(self.message_callback)
        if len(sig.parameters) >= 3:
            return await loop.run_in_executor(
                None, self.message_callback, user_message, user_id, cancel_event
            )
        return await loop.run_in_executor(
            None, self.message_callback, user_message, user_id
        )

    @retry_on_network_error(max_retries=3, initial_delay=0.5, backoff_factor=2.0)
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user or not update.message:
            return
        user_id = update.effective_user.id
        if not self._is_user_allowed(user_id):
            await update.message.reply_text(
                "Sorry, you are not authorized to use this bot."
            )
            return
        if not update.message.text:
            return

        user_message = update.message.text.strip()
        if not user_message:
            return

        # Handle restart slash commands that may arrive as text
        if user_message == "/restart":
            await self.restart_command(update, context)
            return

        self._last_user_id = user_id
        self._boot_user_id = user_id

        history = self.get_conversation_history(user_id)
        history.add_message("user", user_message)

        shared_history = self.get_conversation_history(0)
        shared_history.add_message("user", user_message)

        if self.user_message_callback:
            try:
                self.user_message_callback(user_message, user_id)
            except Exception as exc:
                self.logger.warning(
                    f"Failed to inject user message into engine: {exc}"
                )

        await self._cancel_user_task(user_id)

        if self.message_callback is None:
            self.logger.info(
                f"No message_callback set; autonomous loop will handle "
                f"user {user_id}'s message via telegram() commands."
            )
            # Send a quick acknowledgement
            try:
                await update.message.reply_text(
                    "✅ Message received. The agent will reply shortly."
                )
            except Exception:
                pass
            # Flush queue immediately so any pending agent replies are delivered
            self._trigger_queue_flush()
            return

        processing_msg = None
        try:
            processing_msg = await update.message.reply_text(
                "Processing your message..."
            )
        except Exception:
            pass

        cancel_event = threading.Event()
        task = asyncio.create_task(
            self._handle_message_task(
                user_id, user_message, processing_msg, history, cancel_event
            )
        )
        async with self._task_lock:
            self._current_tasks[user_id] = RunningTelegramTask(
                task=task, cancel_event=cancel_event
            )

    async def _handle_message_task(
        self,
        user_id: int,
        user_message: str,
        processing_msg,
        history,
        cancel_event: threading.Event,
    ):
        try:
            response = None
            if self.message_callback:
                response = await self._process_message_async(
                    user_message, user_id, processing_msg, history, cancel_event
                )
            if cancel_event.is_set():
                self.logger.info(
                    f"Task for user {user_id} was cancelled, skipping response"
                )
                return

            if response is not None:
                history.add_message("assistant", response)
            if response and len(response) > 4000:
                response = self._truncate_message(response, max_length=4000)

            if processing_msg and response is not None:
                try:
                    await processing_msg.edit_text(response)
                except Exception as edit_err:
                    self.logger.warning(
                        f"Could not edit processing message: {edit_err}. "
                        "Sending as new message."
                    )
                    try:
                        await processing_msg.chat.send_message(response)
                    except Exception as send_err:
                        self.logger.error(
                            f"Failed to send response as new message: {send_err}"
                        )
            elif response is not None and self.application:
                try:
                    await self.application.bot.send_message(
                        chat_id=user_id, text=response
                    )
                except Exception as send_err:
                    self.logger.error(
                        f"Failed to send response as new message: {send_err}"
                    )
        except asyncio.CancelledError:
            self.logger.info(f"Task for user {user_id} cancelled")
            try:
                if processing_msg:
                    await processing_msg.edit_text(
                        "Task cancelled - processing new request..."
                    )
            except Exception:
                pass
            raise
        except Exception as exc:
            self.logger.error(f"Error processing message: {exc}")
            try:
                if processing_msg:
                    await processing_msg.edit_text(
                        f"Error processing your request: {exc}"
                    )
            except Exception:
                pass
        finally:
            try:
                async with self._task_lock:
                    running = self._current_tasks.get(user_id)
                    if running and running.task == asyncio.current_task():
                        del self._current_tasks[user_id]
            except Exception:
                pass

    def _truncate_message(self, message: str, max_length: int = 4000) -> str:
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

    # ------------------------------------------------------------------ #
    #  Bot lifecycle
    # ------------------------------------------------------------------ #

    def start_bot(self) -> bool:
        if not TELEGRAM_AVAILABLE:
            self.logger.error(
                "Cannot start bot: python-telegram-bot not installed"
            )
            self.is_running = False
            return False
        if not self.bot_token:
            self.logger.error("Cannot start bot: bot_token not set")
            self.is_running = False
            return False

        self._should_restart = True
        self._start_queue_processor()

        consecutive_errors = 0
        while self._should_restart:
            try:
                self.logger.info("Starting Telegram bot...")
                self.is_running = True
                consecutive_errors = 0
                # Create a fresh Application so previous state doesn't leak.
                self.application = Application.builder().token(self.bot_token).build()
                asyncio.run(self._run_application())
                self.is_running = False
                self.application = None
                self._loop_ready.clear()

                if self._should_restart:
                    wait = min(2 ** min(consecutive_errors, 6), 300.0)
                    wait = wait * (0.75 + 0.5 * random.random())
                    self.logger.info(
                        f"Telegram bot stopped; restarting in {wait:.1f}s"
                    )
                    time.sleep(wait)

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received, stopping Telegram bot")
                self._should_restart = False
                self.is_running = False
                break
            except Exception as exc:
                consecutive_errors += 1
                self.logger.error(f"Error in Telegram bot: {exc}\n{traceback.format_exc()}")
                self.is_running = False
                self._loop_ready.clear()
                severity, category, is_retryable, suggested_delay = classify_api_error(exc)
                if not is_retryable or not self._should_restart:
                    self.logger.error(
                        f"Telegram bot encountered non-retryable error ({category}): {exc}"
                    )
                    break
                wait = max(suggested_delay, 5.0) * (2 ** min(consecutive_errors, 6))
                wait = min(wait, 300.0)
                self.logger.info(
                    f"Waiting {wait:.0f}s before restarting Telegram "
                    f"(consecutive failures: {consecutive_errors})"
                )
                time.sleep(wait)

        self._stop_queue_processor()
        self.is_running = False
        self.logger.info("Telegram bot lifecycle ended")
        return True

    async def _run_application(self):
        """Run initialize / start / polling / shutdown on the event loop."""
        if not self.application:
            return
        await self.application.initialize()
        await self.application.start()

        # Register handlers here, where the application is fully initialized.
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("restart", self.restart_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )

        self._own_loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._loop_ready.set()
        self.logger.info("Telegram bot event loop is ready")

        try:
            await self.application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True,
            )
            try:
                await self._stop_event.wait()
            finally:
                await self.application.updater.stop()
        finally:
            await self.application.stop()
            await self.application.shutdown()

    def stop_bot(self):
        """Stop the Telegram bot gracefully from any thread."""
        self._should_restart = False
        self.is_running = False
        self._stop_queue_processor()
        if self._stop_event and self._own_loop and not self._own_loop.is_closed():
            try:
                self._own_loop.call_soon_threadsafe(self._stop_event.set)
            except Exception as exc:
                self.logger.warning(f"Failed to signal stop event: {exc}")


# ---------------------------------------------------------------------- #
#  Factory
# ---------------------------------------------------------------------- #

def create_telegram_bot(
    config_path: Optional[str] = None, terminal_history=None
) -> Optional[TelegramBotManager]:
    """
    Create a TelegramBotManager from configuration.
    Supports environment overrides to avoid storing secrets in config.yaml:
        TELEGRAM_BOT_TOKEN       overrides telegram.bot_token
        TELEGRAM_AUTHORIZED_USERS   comma-separated user ids overrides telegram.authorized_users
    """
    if not TELEGRAM_AVAILABLE:
        print("python-telegram-bot library not installed")
        print("Enable Telegram mode with:")
        print("  pip install 'python-telegram-bot[job-queue]>=21.0.0'")
        return None

    try:
        import yaml
        from ..core_processing.terminal_history import get_terminal_history

        config_dict: Dict[str, Any] = {}
        if config_path and Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config_dict = yaml.safe_load(f) or {}
        else:
            print(f"create_telegram_bot: config_path={config_path}, exists={config_path and Path(config_path).exists()}")

        telegram_config = config_dict.get("telegram", {}) or {}

        if not telegram_config.get("enabled", False):
            print(f"create_telegram_bot: telegram not enabled. config_dict keys={list(config_dict.keys())}")
            return None

        bot_token = (
            os.environ.get("TELEGRAM_BOT_TOKEN", "")
            or str(telegram_config.get("bot_token", "")).strip()
            or ""
        )
        if not bot_token:
            print("Telegram bot token not configured")
            print(
                "Set TELEGRAM_BOT_TOKEN or telegram.bot_token in config.yaml"
            )
            return None

        # Authorized user ids
        raw_allowed = telegram_config.get("authorized_users") or telegram_config.get(
            "allowed_user_ids"
        )
        env_users = os.environ.get("TELEGRAM_AUTHORIZED_USERS", "")
        if env_users:
            raw_allowed = [u.strip() for u in env_users.split(",") if u.strip()]

        allowed_user_ids: List[int] = []
        if raw_allowed:
            for uid in raw_allowed:
                if uid is None:
                    continue
                try:
                    allowed_user_ids.append(int(uid))
                except (ValueError, TypeError):
                    pass

        raw_user_id = telegram_config.get("telegram_user_id", "")
        if raw_user_id:
            try:
                uid = int(raw_user_id)
                if uid not in allowed_user_ids:
                    allowed_user_ids.append(uid)
            except (ValueError, TypeError):
                pass

        max_history_length = telegram_config.get("max_history_length", 50)
        terminal_history = terminal_history or get_terminal_history()

        return TelegramBotManager(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            max_history_length=max_history_length,
            terminal_history=terminal_history,
        )
    except Exception as exc:
        print(f"Error loading Telegram configuration: {exc}")
        return None
