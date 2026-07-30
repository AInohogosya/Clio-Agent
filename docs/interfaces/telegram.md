# Interfaces → Telegram (`interfaces/telegram.py`)

Run Clio-Agent-2 as a Telegram bot. Launch with `python3 run.py --telegram`.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Configure it: `python3 run.py setup --telegram-token 123456:ABC` (or set
   `TELEGRAM_BOT_TOKEN` in `clio_agent_2/config/.env`, or use `/configure`).
3. Launch: `python3 run.py --telegram`.

The bot **only starts if a real token is set** — placeholder tokens are rejected by
`_is_token_configured`.

## Features

- Full message handling: user text → `process_message` → tools/`say`; only `say`
  messages are delivered to the chat (the auto-reply was removed).
- Autonomous `say` messages are delivered to the chat via the response callback.
- Supports the slash-command set (Telegram native slash commands where available).

## Notes

- Built on `python-telegram-bot` (≥ 20).
- Uses `asyncio.get_running_loop()` (not the deprecated `get_event_loop()`) so it
  works on Python 3.12+/3.14.
- See `tests/test_process_message_none.py` for the Telegram-facing `len()` bug this
  design must avoid.

See also: [Interfaces Overview](overview.md), [Setup Wizard](../configuration/setup-wizard.md).
