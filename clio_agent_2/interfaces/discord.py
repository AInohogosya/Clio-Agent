"""
Discord Interface for Clio-Agent-2.
Provides Discord bot integration.
"""

import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import discord
from discord.ext import commands, tasks

from core.agent import ClioAgent, MESSAGE_PROCESS_TIMEOUT

logger = logging.getLogger(__name__)


class DiscordInterface:
    """
    Discord Bot Interface for Clio-Agent-2.
    
    Features:
    - Receive messages from Discord channels/DMs
    - Send responses back to Discord
    - Support for slash commands
    - Autonomous mode notifications
    - Rich embed formatting
    """
    
    def __init__(self, agent: ClioAgent, bot_token: str):
        """
        Initialize the Discord interface.
        
        Args:
            agent: ClioAgent instance
            bot_token: Discord bot token
        """
        self.agent = agent
        self.bot_token = bot_token
        self.bot: Optional[commands.Bot] = None
        self.tree: Optional[discord.app_commands.Tree] = None
        self.guild_sessions = {}  # Store conversation state per guild
        # Slash commands must only be added to the command tree once. Discord's
        # ``on_ready`` event can fire multiple times (every reconnect/resume),
        # so we guard registration to avoid ``CommandAlreadyRegistered`` errors.
        self._commands_registered = False
    
    async def send_message(self, message: str, channel: discord.TextChannel = None):
        """
        Send a message through the Discord bot.
        
        Args:
            message: Message text to send
            channel: Specific channel to send to (optional)
        """
        if self.bot is None:
            return
        
        # Split long messages
        max_length = 2000
        chunks = [message[i:i+max_length] for i in range(0, len(message), max_length)]
        
        for chunk in chunks:
            try:
                if channel:
                    await channel.send(chunk)
            except Exception as e:
                print(f"Error sending Discord message: {e}")
    
    async def handle_message(self, message: discord.Message):
        """
        Handle incoming messages.
        
        Args:
            message: Discord message object
        """
        # Ignore bot's own messages
        if message.author == self.bot.user:
            return
        
        # Ignore messages without content
        if not message.content:
            return
        
        user_message = message.content
        
        # Check if bot is mentioned (for server messages)
        if isinstance(message.channel, discord.TextChannel):
            if not self.bot.user.mentioned_in(message):
                return
            # Remove the mention from the message. Discord uses both ``<@id>``
            # and the nickname form ``<@!id>``, so strip either variant.
            user_message = re.sub(
                rf"<@!?{self.bot.user.id}>", "", user_message
            ).strip()
        
        # Store guild session
        guild_id = message.guild.id if message.guild else "dm"
        if guild_id not in self.guild_sessions:
            self.guild_sessions[guild_id] = {
                "channel": message.channel,
                "last_active": asyncio.get_running_loop().time(),
            }
        
        # Show typing indicator
        async with message.channel.typing():
            try:
                # Process message through agent. Bound by a watchdog so a slow
                # or unreachable LLM can never freeze this channel indefinitely.
                try:
                    response = await asyncio.wait_for(
                        self.agent.process_message(user_message),
                        timeout=MESSAGE_PROCESS_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        "Discord message timed out after %.0fs",
                        MESSAGE_PROCESS_TIMEOUT,
                    )
                    await message.channel.send(
                        "⚠️ 応答に時間がかかりすぎたため中断しました。"
                        "しばらくしてからもう一度お試しください。"
                    )
                    return

                # ``process_message`` is guaranteed to return a string, but
                # never let a ``None`` reach ``len()`` in ``send_message``
                # (defence-in-depth against an empty model completion).
                if response is None:
                    response = "⚠️ Sorry, I was unable to generate a response."

                # The reply system has been removed: process_message no longer
                # returns a natural-language reply, so there is nothing to send
                # here in the normal case. User-facing output is delivered
                # through the response callback (send_response ->
                # handle_autonomous_message). Only non-empty returns (e.g.
                # internal errors) are sent.
                if response:
                    await self.send_message(response, message.channel)
            
            except Exception as e:
                error_msg = f"⚠️ Error: {str(e)}"
                await message.channel.send(error_msg)
    
    async def setup_slash_commands(self):
        """
        Register slash commands on the command tree.

        This must run exactly once for the lifetime of the bot. Adding a command
        that already exists raises ``CommandAlreadyRegistered``. Because Discord
        can dispatch ``on_ready`` more than once (every reconnect/resume), the
        registration is guarded by a flag and the actual ``tree.sync()`` network
        call is performed separately in ``on_ready``.
        """
        if self._commands_registered:
            return
        self._commands_registered = True

        @self.tree.command(name="status", description="Show agent status")
        async def status_command(interaction: discord.Interaction):
            await interaction.response.defer()
            status = await self.agent.get_status()
            status_text = "📊 **Agent Status:**\n\n"
            for key, value in status.items():
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value[:5])
                status_text += f"• **{key}**: `{value}`\n"
            
            embed = discord.Embed(
                title=self.agent.name,
                description=status_text,
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        
        @self.tree.command(name="help", description="Show available commands")
        async def help_command(interaction: discord.Interaction):
            await interaction.response.defer()
            help_text = await self.agent.execute_command("help", [])
            
            embed = discord.Embed(
                title="Help - Available Commands",
                description=f"```\n{help_text}\n```",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
        
        @self.tree.command(name="settings", description="Show current settings")
        async def settings_command(interaction: discord.Interaction):
            await interaction.response.defer()
            config_dict = self.agent.config.to_dict()
            settings_text = "⚙️ **Settings:**\n\n"
            for key, value in config_dict.items():
                settings_text += f"• **{key}**: `{value}`\n"
            
            embed = discord.Embed(
                title="Agent Settings",
                description=settings_text,
                color=discord.Color.gold()
            )
            await interaction.followup.send(embed=embed)
        
        @self.tree.command(name="models", description="List available models")
        async def models_command(interaction: discord.Interaction):
            await interaction.response.defer()
            # Check if any providers are configured before making API calls
            available_providers = self.agent.llm_router.get_available_providers()
            if not available_providers:
                embed = discord.Embed(
                    title="No LLM Providers Configured",
                    description="Please set up your API keys first using /reconfigure or by editing config/.env",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed)
                return
            
            models = await self.agent.llm_router.list_all_models()
            models_text = "📚 **Available Models:**\n\n"
            for provider, model_list in models.items():
                models_text += f"\n**{provider.upper()}:**\n"
                for model in model_list[:10]:
                    models_text += f"• `{model}`\n"
            
            embed = discord.Embed(
                title="Available LLM Models",
                description=models_text,
                color=discord.Color.purple()
            )
            await interaction.followup.send(embed=embed)
        
        @self.tree.command(name="think", description="Record a thought")
        @discord.app_commands.describe(thought="The thought to record")
        async def think_command(interaction: discord.Interaction, thought: str):
            await interaction.response.defer()
            result = await self.agent.execute_command("think", [thought])
            await interaction.followup.send(result)
        
        @self.tree.command(name="context", description="Show recent context entries")
        @discord.app_commands.describe(count="Number of entries to show (default: 20)")
        async def context_command(interaction: discord.Interaction, count: int = 20):
            await interaction.response.defer()
            result = await self.agent.execute_command("context", [str(count)])
            
            if len(result) > 2000:
                result = result[:1997] + "..."
            
            embed = discord.Embed(
                title="Recent Context",
                description=f"```\n{result}\n```",
                color=discord.Color.greyple()
            )
            await interaction.followup.send(embed=embed)

    async def handle_autonomous_message(self, message: str):
        """
        Callback for autonomous mode messages.
        
        Args:
            message: Message from autonomous loop
        """
        if message.startswith("[Autonomous Thought]"):
            return

        embed = discord.Embed(
            title=self.agent.name,
            description=message,
            color=discord.Color.purple()
        )
        embed.set_footer(text=self.agent.name)
        
        # Send to all active guild channels. Iterate over a snapshot: the
        # ``await channel.send(...)`` below yields control back to the event
        # loop, which can let ``handle_message`` register a new guild session
        # and mutate ``guild_sessions`` mid-iteration (``RuntimeError:
        # dictionary changed size during iteration``).
        for guild_id, session in list(self.guild_sessions.items()):
            channel = session.get("channel")
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    logger.warning(
                        "Failed to deliver autonomous message to guild %s: %s",
                        guild_id,
                        e,
                    )
    
    async def on_ready(self):
        """
        Handle the bot ready event.

        Discord may dispatch ``on_ready`` multiple times (on every reconnect or
        session resume). Command *registration* is therefore idempotent (guarded
        inside ``setup_slash_commands``); here we only (re)sync the tree with
        Discord, which is a safe operation to repeat.
        """
        print(f"🎮 Discord bot logged in as {self.bot.user}")
        print(f"Connected to {len(self.bot.guilds)} guilds")

        # Make sure commands are registered (no-op after the first call), then
        # publish them to Discord.
        await self.setup_slash_commands()
        try:
            await self.tree.sync()
        except Exception as e:
            logger.error("Failed to sync Discord slash commands: %s", e)
    
    async def start(self):
        """Start the Discord bot."""
        # Register callback for agent responses
        self.agent.register_response_callback(self.handle_autonomous_message)
        
        # Initialize agent
        await self.agent.initialize()
        started = await self.agent.ensure_autonomous_loop()
        if not started:
            print("⚠️  Continuous thinking could not start because no LLM model is configured.")
        
        # Create bot with intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self.tree = self.bot.tree

        # Register the slash commands on the tree once, up front (before the bot
        # connects). ``on_ready`` will publish them via ``tree.sync()``; keeping
        # registration out of the (repeatable) ``on_ready`` path prevents the
        # ``CommandAlreadyRegistered`` error on reconnects.
        await self.setup_slash_commands()

        # Register events
        @self.bot.event
        async def on_ready():
            await self.on_ready()
        
        @self.bot.event
        async def on_message(message):
            await self.handle_message(message)
            await self.bot.process_commands(message)
        
        # Start bot
        print(f"🎮 Discord bot (Beta) starting...")
        await self.bot.start(self.bot_token)
    
    async def stop(self):
        """Stop the Discord bot."""
        await self.agent.stop_autonomous_loop()
        if self.bot:
            await self.bot.close()
