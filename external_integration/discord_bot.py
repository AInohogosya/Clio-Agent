"""
Discord Bot Integration for Clio-Agent-1 AI Agent
Handles Discord bot communication and message management.
Mirrors the Telegram bot interface for compatibility with the
autonomous loop engine's telegram() / telegram_log() commands.
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
    import discord
    from discord.ext import commands
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False

from ..utils.logger import get_logger
from ..utils.config import load_config
from ..utils.resilience_engine import get_resilience_engine, classify_api_error, ErrorSeverity, ResilienceConfig


def retry_on_network_error(max_retries: int = 3, initial_delay: float = 1.0, backoff_factor: float = 2.0):
    """
    Decorator to retry network operations with exponential backoff.
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger = get_logger("discord_bot")
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
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
                        logger.error(f"Error in {func.__name__}: {e}")
                        raise

                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(
                        f"Network error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f} seconds..."
                    )
                    await asyncio.sleep(delay)

            raise last_exception

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger = get_logger("discord_bot")
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
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
                        logger.error(f"Error in {func.__name__}: {e}")
                        raise

                    delay = initial_delay * (backoff_factor ** attempt)
                    logger.warning(
                        f"Network error in {func.__name__} (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f} seconds..."
                    )
                    time.sleep(delay)

            raise last_exception

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


class DiscordMode(Enum):
    """Discord bot mode"""
    NORMAL = "normal"
    DISCORD = "discord"


@dataclass
class ConversationHistory:
    """Conversation history for Discord mode"""
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
        return self.messages

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
class QueuedDiscordMessage:
    """Discord message waiting to be sent from the queue processor."""
    channel_id: int
    message: str
    attempts: int = 0
    next_attempt_at: float = 0.0


@dataclass
class RunningDiscordTask:
    """A Discord pipeline task plus the cancellation event passed to it."""
    task: asyncio.Task
    cancel_event: threading.Event


class DiscordBotManager:
    """
    Manages Discord bot integration for AI agent

    Handles:
    - Bot initialization and message receiving
    - Conversation history management
    - Message sending (via Discord channels)
    - Compatible interface with TelegramBotManager for engine integration
    """

    def __init__(self, bot_token: str, allowed_user_ids: Optional[List[int]] = None,
                 max_history_length: int = 50, terminal_history=None):
        self.bot_token = bot_token
        self.allowed_user_ids = allowed_user_ids or []
        self.max_history_length = max_history_length
        self.logger = get_logger("discord_bot")
        self.terminal_history = terminal_history

        # Conversation history per user
        self.conversation_histories: Dict[int, ConversationHistory] = {}

        # Callback for processing messages
        self.message_callback: Optional[Callable[[str, int], str]] = None
        self.restart_callback: Optional[Callable[[int], None]] = None
        self.user_message_callback: Optional[Callable[[str, int], None]] = None

        # Track running tasks per user
        self._current_tasks: Dict[int, RunningDiscordTask] = {}
        self._task_lock: Optional[asyncio.Lock] = None

        # Map user_id -> channel_id so queue_message(user_id, msg) works
        self._user_channel_map: Dict[int, int] = {}

        # Discord client
        self.client: Optional[commands.Bot] = None

        # Running state
        self.is_running = False
        self._should_restart = True

        # Message queue for sending messages from synchronous context
        self.message_queue: List[QueuedDiscordMessage] = []
        self._queue_lock = threading.Lock()
        self._max_queue_send_attempts = 5
        self._queue_retry_delay = 2.0

        # Background thread for processing message queue
        self.queue_processor_thread: Optional[threading.Thread] = None
        self.queue_processor_running = False

        # Resilience engine for error recovery
        self._resilience = get_resilience_engine()

        if not DISCORD_AVAILABLE:
            self.logger.error("discord.py not installed. Install with: pip install discord.py>=2.3.0")

    def set_message_callback(self, callback: Callable[[str, int], str]):
        self.message_callback = callback

    def set_restart_callback(self, callback: Callable[[int], None]):
        self.restart_callback = callback

    def set_user_message_callback(self, callback: Callable[[str, int], None]):
        self.user_message_callback = callback

    def set_shared_conversation_history(self, history: ConversationHistory):
        self.conversation_histories[history.user_id] = history

    async def _get_task_lock(self) -> asyncio.Lock:
        if self._task_lock is None:
            self._task_lock = asyncio.Lock()
        return self._task_lock

    def get_conversation_history(self, user_id: int) -> ConversationHistory:
        if user_id not in self.conversation_histories:
            self.conversation_histories[user_id] = ConversationHistory(
                user_id=user_id,
                max_length=self.max_history_length
            )
        return self.conversation_histories[user_id]

    def clear_conversation_history(self, user_id: int):
        if user_id in self.conversation_histories:
            self.conversation_histories[user_id].clear()
            self.logger.info(f"Cleared conversation history for user {user_id}")

    @retry_on_network_error(max_retries=3, initial_delay=1.0, backoff_factor=2.0)
    async def _handle_start_command(self, ctx):
        """Handle /start command"""
        if not ctx.author:
            return

        user_id = ctx.author.id

        if not self._is_user_allowed(user_id):
            await ctx.send("Sorry, you are not authorized to use this bot.")
            return

        await ctx.send(
            "Clio-Agent-1 AI Agent\n\n"
            "Send me commands and I'll execute them on your computer.\n"
            "Use /restart to restart while keeping current settings.\n"
            "Use /help for more information.\n\n"
            "Note: I check my conversation history and execution log before "
            "sending any message to avoid duplicate responses."
        )

    @retry_on_network_error(max_retries=3, initial_delay=1.0, backoff_factor=2.0)
    async def _handle_restart_command(self, ctx):
        """Handle /restart command"""
        if not ctx.author:
            return

        user_id = ctx.author.id

        if not self._is_user_allowed(user_id):
            return

        await self._cancel_user_task(user_id)
        await ctx.send("Restarting Clio-Agent-1 with the same provider, model, and API settings...")

        if self.restart_callback:
            self.restart_callback(user_id)
        else:
            await ctx.send("Restart is not configured for this bot session.")

    @retry_on_network_error(max_retries=3, initial_delay=1.0, backoff_factor=2.0)
    async def _handle_help_command(self, ctx):
        """Handle /help command"""
        if not ctx.author:
            return

        user_id = ctx.author.id

        if not self._is_user_allowed(user_id):
            return

        await ctx.send(
            "Clio-Agent-1 AI Agent Help\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/restart - Restart while keeping current provider/model/API settings\n"
            "/help - Show this help message\n\n"
            "Just send any instruction and I'll execute it on your computer!\n\n"
            "Note: I check my conversation history and execution log before sending "
            "any message to avoid sending duplicate responses."
        )

    async def _cancel_user_task(self, user_id: int):
        running = self._current_tasks.get(user_id)
        if not running:
            return
        if not running.task.done():
            self.logger.info(f"Cancelling running task for user {user_id}")
            running.cancel_event.set()
            running.task.cancel()

    async def _process_message_async(self, user_message: str, user_id: int,
                                      processing_msg, history, cancel_event: threading.Event) -> str:
        if self.message_callback:
            loop = asyncio.get_running_loop()
            if len(inspect.signature(self.message_callback).parameters) >= 3:
                return await loop.run_in_executor(None, self.message_callback, user_message, user_id, cancel_event)
            return await loop.run_in_executor(None, self.message_callback, user_message, user_id)
        return "Message callback not set. Bot not properly configured."

    async def _handle_message(self, message):
        """Handle incoming Discord messages: start a background task for AI processing."""
        if not message.author or message.author.bot:
            return

        user_id = message.author.id

        if not self._is_user_allowed(user_id):
            await message.channel.send("Sorry, you are not authorized to use this bot.")
            return

        if not message.content:
            return

        user_message = message.content

        # Check for slash commands that may arrive as regular messages
        if user_message.strip() == "/restart":
            # Create a minimal context-like object for the restart handler
            ctx = await self.client.get_context(message) if hasattr(self.client, 'get_context') else None
            if ctx:
                await self._handle_restart_command(ctx)
            return

        # Track the first user we hear from
        if not getattr(self, "_boot_user_id", None):
            self._boot_user_id = user_id

        # Track the channel this user messaged from
        self._user_channel_map[user_id] = message.channel.id

        # Add user message to conversation history
        history = self.get_conversation_history(user_id)
        history.add_message("user", user_message)

        # Also add to shared conversation history (user_id=0)
        shared_history = self.get_conversation_history(0)
        shared_history.add_message("user", user_message)

        # Inject the message into the engine's execution log
        if self.user_message_callback:
            try:
                self.user_message_callback(user_message, user_id)
            except Exception as e:
                self.logger.warning(f"Failed to inject user message into engine: {e}")

        # Cancel any existing running task for this user
        await self._cancel_user_task(user_id)

        # When message_callback is None (autonomous loop mode), the agent
        # handles replies via discord() commands.
        if self.message_callback is None:
            self.logger.info(
                f"No message_callback set; autonomous loop will handle "
                f"user {user_id}'s message via discord() commands."
            )
            try:
                await self.process_message_queue()
            except Exception as e:
                self.logger.warning(f"Failed to flush message queue: {e}")
            return

        # Send a "processing" message
        processing_msg = None
        try:
            processing_msg = await message.channel.send("Processing your message...")
        except Exception:
            pass

        cancel_event = threading.Event()
        task = asyncio.create_task(
            self._handle_message_task(user_id, user_message, processing_msg, history, cancel_event)
        )
        async with await self._get_task_lock():
            self._current_tasks[user_id] = RunningDiscordTask(task=task, cancel_event=cancel_event)

    async def _handle_message_task(self, user_id: int, user_message: str,
                                    processing_msg, history, cancel_event: threading.Event):
        try:
            response = None
            if self.message_callback:
                response = await self._process_message_async(user_message, user_id, processing_msg, history, cancel_event)

                if cancel_event.is_set():
                    self.logger.info(f"Task for user {user_id} was cancelled, skipping response")
                    return

                if response is not None:
                    history.add_message("assistant", response)

                # Truncate long messages if exceeds Discord limit (2000 chars)
                if response and len(response) > 1950:
                    self.logger.info(f"Response is {len(response)} chars, truncating with [omitted]")
                    response = self._truncate_message(response, max_length=1950)

                # Send response: try to edit processing message, send new one if that fails
                if processing_msg and response is not None:
                    try:
                        await processing_msg.edit(content=response)
                    except Exception:
                        try:
                            await processing_msg.channel.send(response)
                        except Exception as send_err:
                            self.logger.error(f"Failed to send response as new message: {send_err}")
                elif response is not None:
                    try:
                        resolved_channel_id = self._user_channel_map.get(user_id, user_id)
                        channel = self.client.get_channel(resolved_channel_id)
                        if channel:
                            await channel.send(response)
                    except Exception as send_err:
                        self.logger.error(f"Failed to send response as new message: {send_err}")
            else:
                self.logger.info(
                    f"No message_callback set; autonomous loop will handle "
                    f"user {user_id}'s message via telegram() commands."
                )
                if processing_msg:
                    try:
                        await processing_msg.edit(
                            content="The AI agent is processing your message.\n"
                                    "Reply will be sent via the agent's autonomous loop."
                        )
                    except Exception:
                        pass

        except asyncio.CancelledError:
            self.logger.info(f"Task for user {user_id} cancelled - switching to new task")
            try:
                if processing_msg:
                    await processing_msg.edit(content="Task cancelled - processing new request...")
            except Exception as e:
                self.logger.error(f"Error editing message: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")
            try:
                if processing_msg:
                    await processing_msg.edit(content=f"Error processing your request: {str(e)}")
            except Exception:
                try:
                    if processing_msg:
                        await processing_msg.channel.send(f"Error: {str(e)}")
                except Exception:
                    pass
        finally:
            try:
                async with await self._get_task_lock():
                    running = self._current_tasks.get(user_id)
                    if running and running.task == asyncio.current_task():
                        del self._current_tasks[user_id]
            except Exception:
                pass

    def _is_user_allowed(self, user_id: int) -> bool:
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids

    def _truncate_message(self, message: str, max_length: int = 1950) -> str:
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
    async def send_message(self, channel_id: int, message: str):
        """Send a message to a specific Discord channel"""
        if not self.client:
            self.logger.error("Discord client not initialized")
            return False

        if len(message) > 1950:
            self.logger.warning(f"Message too long ({len(message)} chars), truncating with [omitted]")
            message = self._truncate_message(message, max_length=1950)

        channel = self.client.get_channel(channel_id)
        if channel:
            await channel.send(message)
            return True
        self.logger.error(f"Channel {channel_id} not found")
        return False

    def queue_message(self, channel_id: int, message: str):
        """Queue a message to be sent. Synchronous, callable from any context.

        channel_id is treated as a user_id — the actual Discord channel
        is looked up from the _user_channel_map.
        """
        actual_channel = self._user_channel_map.get(channel_id, channel_id)
        with self._queue_lock:
            self.message_queue.append(QueuedDiscordMessage(channel_id=actual_channel, message=message))
        self.logger.info(f"Message queued for channel {actual_channel} (user {channel_id})")

    async def process_message_queue(self):
        """Process currently-sendable queued messages."""
        for queued_message in self._pop_sendable_messages():
            await self._send_queued_message(queued_message)

    def _pop_sendable_messages(self) -> List[QueuedDiscordMessage]:
        now = time.time()
        sendable: List[QueuedDiscordMessage] = []
        delayed: List[QueuedDiscordMessage] = []

        with self._queue_lock:
            for queued_message in self.message_queue:
                if queued_message.next_attempt_at <= now:
                    sendable.append(queued_message)
                else:
                    delayed.append(queued_message)
            self.message_queue = delayed

        return sendable

    async def _send_queued_message(self, queued_message: QueuedDiscordMessage):
        try:
            await self.send_message(queued_message.channel_id, queued_message.message)
            self.logger.info(f"Sent queued message to channel {queued_message.channel_id}")
        except Exception as e:
            queued_message.attempts += 1
            if queued_message.attempts >= self._max_queue_send_attempts:
                self.logger.error(
                    f"Dropping queued message to channel {queued_message.channel_id} "
                    f"after {queued_message.attempts} failed attempts: {e}"
                )
                return

            queued_message.next_attempt_at = time.time() + (self._queue_retry_delay * queued_message.attempts)
            self.logger.warning(
                f"Failed to send queued message to channel {queued_message.channel_id}: {e}. "
                f"Retry {queued_message.attempts}/{self._max_queue_send_attempts} scheduled."
            )
            with self._queue_lock:
                self.message_queue.append(queued_message)

    def _start_queue_processor(self):
        """Start a background thread for queue processing.

        Discord.py channel/HTTP objects are bound to the bot client's own
        event loop. Sends MUST therefore be marshalled onto that loop using
        run_coroutine_threadsafe — running them on a private event loop in
        this thread raises "attached to a different loop" errors and the
        message is never delivered.
        """
        def queue_processor():
            self.queue_processor_running = True
            while self.queue_processor_running:
                client = self.client
                client_loop = getattr(client, "loop", None) if client else None
                if client and client_loop and client_loop.is_running():
                    try:
                        messages_to_send = self._pop_sendable_messages()
                        for queued_message in messages_to_send:
                            future = asyncio.run_coroutine_threadsafe(
                                self._send_queued_message(queued_message),
                                client_loop,
                            )
                            try:
                                future.result(timeout=15)
                            except Exception as send_err:
                                self.logger.warning(
                                    f"Queued Discord send failed (will retry): {send_err}"
                                )
                    except Exception as e:
                        self.logger.error(f"Error in queue processor: {e}")
                        time.sleep(2)
                time.sleep(0.1)
            self.logger.info("Queue processor stopped")

        self.queue_processor_thread = threading.Thread(target=queue_processor, daemon=True)
        self.queue_processor_thread.start()
        self.logger.info("Queue processor thread started")

    def _stop_queue_processor(self):
        self.queue_processor_running = False
        if self.queue_processor_thread:
            self.queue_processor_thread.join(timeout=2)
            self.logger.info("Queue processor thread stopped")

    def _setup_commands(self):
        """Register slash and prefix commands on the Discord bot."""

        @self.client.command(name="start")
        async def start_cmd(ctx):
            await self._handle_start_command(ctx)

        @self.client.command(name="restart")
        async def restart_cmd(ctx):
            await self._handle_restart_command(ctx)

        @self.client.command(name="help")
        async def help_cmd(ctx):
            await self._handle_help_command(ctx)

    def start_bot(self):
        """Start the Discord bot (blocking)"""
        if not DISCORD_AVAILABLE:
            self.logger.error("Cannot start bot: discord.py not installed")
            return False

        if not self.bot_token:
            self.logger.error("Cannot start bot: bot_token not set")
            return False

        while self._should_restart:
            try:
                intents = discord.Intents.default()
                intents.message_content = True
                intents.members = True

                self.client = commands.Bot(command_prefix="!", intents=intents)

                # Remove default help command before registering our own
                self.client.remove_command("help")

                # Register commands
                self._setup_commands()

                @self.client.event
                async def on_ready():
                    self.is_running = True
                    self.logger.info(f"Discord bot connected as {self.client.user}")

                @self.client.event
                async def on_message(message):
                    # Process slash commands first
                    await self.client.process_commands(message)
                    # Also handle regular text messages
                    if not message.author.bot and message.content and not message.content.startswith("!"):
                        await self._handle_message(message)

                # Start queue processor
                self._start_queue_processor()

                self.logger.info("Starting Discord bot...")
                self.client.run(self.bot_token)

                self.logger.info("Discord bot stopped")
                self.is_running = False
                self._stop_queue_processor()

                if self._should_restart:
                    self.logger.info("Restarting Discord bot...")
                    self.client = None
                    time.sleep(2)

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received, stopping Discord bot")
                self.is_running = False
                self._should_restart = False
                self._stop_queue_processor()
                if self.client:
                    try:
                        client_loop = self.client.loop
                        if client_loop and not client_loop.is_closed():
                            future = asyncio.run_coroutine_threadsafe(
                                self.client.close(), client_loop
                            )
                            future.result(timeout=10)
                        else:
                            loop = asyncio.new_event_loop()
                            loop.run_until_complete(self.client.close())
                            loop.close()
                    except Exception:
                        pass
                    self.client = None
                break
            except Exception as e:
                self.logger.error(f"Error in Discord bot: {e}")
                self.is_running = False
                self._stop_queue_processor()

                severity, category, is_retryable, suggested_delay = classify_api_error(e)

                if not is_retryable or not self._should_restart:
                    self.logger.error(f"Discord bot encountered non-retryable error ({category.value}): {e}")
                    break

                base_wait = max(suggested_delay, 5.0)
                self._resilience.record_failure("discord:polling")
                cb = self._resilience._circuit_breakers.get("discord:polling", {})
                consecutive_failures = cb.get("failures", 1) if isinstance(cb, dict) else 1
                wait = min(base_wait * (2 ** min(consecutive_failures, 6)), 300.0)

                self.logger.info(f"Waiting {wait:.0f}s before restarting Discord (consecutive failures: {consecutive_failures})")
                time.sleep(wait)

        return True

    async def _close_client(self):
        if self.client:
            try:
                await self.client.close()
                self.logger.info("Discord client closed")
            except Exception as e:
                self.logger.error(f"Error closing client: {e}")

    def stop_bot(self):
        self.is_running = False
        self._should_restart = False
        self._stop_queue_processor()

        if self.client:
            self.logger.info("Stopping Discord bot...")
            try:
                loop = asyncio.get_running_loop()
                future = asyncio.run_coroutine_threadsafe(self._close_client(), loop)
                future.result(timeout=10)
            except RuntimeError:
                self.logger.info("No running event loop, bot will stop on next cycle")
            except Exception as e:
                self.logger.error(f"Error stopping client: {e}")


def create_discord_bot(config_path: Optional[str] = None, terminal_history=None) -> Optional[DiscordBotManager]:
    """
    Create a Discord bot manager from configuration

    Args:
        config_path: Path to config.yaml file. If None, loads from default location.
        terminal_history: Optional TerminalHistory instance for command execution.

    Returns:
        DiscordBotManager instance or None if discord is disabled or not available
    """
    if not DISCORD_AVAILABLE:
        print("discord.py library not installed")
        print("To enable Discord mode, install it with:")
        print("  pip install discord.py>=2.3.0")
        return None

    try:
        import yaml
        from pathlib import Path
        from ..core_processing.terminal_history import get_terminal_history

        if terminal_history is None:
            terminal_history = get_terminal_history()

        config_dict = {}
        if config_path and Path(config_path).exists():
            with open(config_path, 'r') as f:
                config_dict = yaml.safe_load(f) or {}

        discord_config = None
        if config_dict.get('discord'):
            discord_config = config_dict['discord']
        else:
            config_obj = load_config()
            if hasattr(config_obj, 'discord'):
                discord_config = config_obj.discord
                if hasattr(discord_config, '__dataclass_fields__'):
                    from dataclasses import asdict
                    discord_config = asdict(discord_config)
                elif hasattr(discord_config, '__dict__'):
                    discord_config = discord_config.__dict__

        if not discord_config:
            discord_config = {}

        if not discord_config.get('enabled', False):
            return None

        bot_token = discord_config.get('bot_token') or ''
        if not bot_token:
            print("Discord bot token not configured")
            print("Please set bot_token in config.yaml under discord section")
            return None

        raw_allowed = (
            discord_config.get('authorized_users')
            or discord_config.get('allowed_user_ids')
            or []
        )
        allowed_user_ids = [int(uid) for uid in raw_allowed if uid is not None]
        max_history_length = discord_config.get('max_history_length', 50)

        return DiscordBotManager(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            max_history_length=max_history_length,
            terminal_history=terminal_history
        )
    except Exception as e:
        print(f"Error loading Discord configuration: {e}")
        return None
