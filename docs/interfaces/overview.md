# Interfaces Overview

Interfaces are how messages get **in** to the agent and how agent responses get
**out**. All three implement the same contract: receive user text, call
`ClioAgent.process_message`, and register a response callback that renders the
agent's messages (delivered via the `say` command) back to the user. The agent no
longer returns a natural-language reply for a user message.

| Interface | Module | Launch flag | Notes |
|-----------|--------|-------------|-------|
| CLI | `interfaces/cli.py` | (default) | Rich terminal UI + slash commands. |
| Telegram | `interfaces/telegram.py` | `--telegram` | Full bot integration. |
| Discord | `interfaces/discord.py` | `--discord` | Beta; fewer features. |

Run all configured interfaces with `python3 run.py --all`.

## Common contract

- Each interface constructs a `ClioAgent` (with `config`, `llm_router`,
  `tool_registry`, `context_log`).
- It registers a `send_response` callback so the agent can push messages out
  (this is the ONLY user-facing output path — the `say` command delivers here).
- It calls `len()` on `process_message`'s return, which is why `process_message`
  must always return a string (never `None`) — see
  `tests/test_process_message_none.py`.

## Token gating

The Telegram/Discord bots only start if a **real** token is configured. Placeholder
tokens (`your_...`, `<...>`) are treated as not configured, so a fresh install won't
try to connect with a bogus token. (`_is_token_configured` from `apply_fixes.py`
enforces this.)

See also: [Getting Started](../getting-started.md), [Slash Commands](../usage/slash-commands.md).
