# Interfaces → Discord (Beta) (`interfaces/discord.py`)

Run Clio-Agent-2 as a Discord bot. Launch with `python3 run.py --discord`.

> ⚠️ **Beta.** Discord support exposes fewer features than the CLI/Telegram.

## Setup

1. Create an application/bot at the
   [Discord Developer Portal](https://discord.com/developers/applications) and copy
   the bot token.
2. Configure it: `python3 run.py setup --discord-token <token>` (or set
   `DISCORD_BOT_TOKEN` in `clio_agent_2/config/.env`, or use `/configure`).
3. Launch: `python3 run.py --discord`.

The bot **only starts if a real token is set**.

## Features

- Server bot with slash commands and embeds.
- User messages → `process_message` → tools/`say`; only `say` messages are shown
  (the auto-reply was removed). Autonomous `say` messages are delivered via the
  response callback.
- Per-server/guild message handling.

## Notes

- Built on `discord.py` (≥ 2.0).
- Uses `asyncio.get_running_loop()` for Python 3.12+/3.14 compatibility.
- Consider Discord's per-message length limits when the agent produces long output.

See also: [Interfaces Overview](overview.md), [Setup Wizard](../configuration/setup-wizard.md).
