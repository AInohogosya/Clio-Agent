# Interfaces

The four user-facing front-ends of Clio-Agent-2. All interfaces share the same `ClioAgent` backend.

---

## 📦 Overview

| Interface | File | Status |
|-----------|------|--------|
| **CLI** | `interfaces/cli.py` | Stable (691 lines) |
| **Telegram** | `interfaces/telegram.py` | Stable (873 lines) |
| **Discord** | `interfaces/discord.py` | Beta (373 lines) |
| **WhatsApp** | `interfaces/whatsapp.py` | Beta (323 lines) |

### Common Pattern

All interfaces:
1. Create a `ClioAgent` instance.
2. Register a `response_callback` that routes agent output to their platform.
3. Start the event loop / poll for messages.
4. Dispatch slash commands through `agent.execute_command()`.

---

## 💻 CLI Interface

**File:** `clio_agent_2/interfaces/cli.py`

A full-screen terminal TUI built with **prompt_toolkit** (input handling) and **rich** (rendering).

### Layout

```
Header: agent name | provider | model | 🔒 lock status
─────────────────────────────────────────────────────
Message area  (rich markdown rendering, scrollable)
─────────────────────────────────────────────────────
Input bar: "> " ──── prompt with completion support
```

### Features

- Markdown rendering of agent responses
- Slash commands with auto-completion
- Full-screen scrollable history
- Signal handling for clean exit (Ctrl+C saves context)

---

## 📱 Telegram Interface

**File:** `clio_agent_2/interfaces/telegram.py`

A **polling-based** bot using `python-telegram-bot`.

### Key Behaviors

- **Conflict recovery**: Retries on 409 Conflict with backoff; clears webhooks; handles rate limits (`RetryAfter`)
- **PID lock**: Prevents second instance on the same machine
- **Markdown sanitization**: Aggressive message sanitization to avoid parsing errors, with code block protection
- **Message timeouts**: Bounded by `MESSAGE_PROCESS_TIMEOUT` (3600s)
- **Bilingual errors**: English + Japanese fallback messages

### Running

```bash
python3 run.py --telegram
```

Evict a stuck instance:
```bash
python3 run.py --telegram --replace
```

---

## 🎮 Discord Interface (Beta)

**File:** `clio_agent_2/interfaces/discord.py`

A bot using `discord.py` with slash commands.

### Supported Slash Commands

| Command | Description |
|---------|-------------|
| `/status` | Agent status |
| `/help` | Help & commands |
| `/settings` | Current settings |
| `/models` | LLM models |
| `/think` | Agent thinking |
| `/context` | View context |

### Features

- Rich embeds for autonomous mode messages
- Direct messages work without mention
- Server channels require explicit `@mention` of the bot

### Known Limitations

- Fewer features than CLI/Telegram
- Slash command registration is idempotent (guards against `CommandAlreadyRegistered`)
- Some markdown formatting may differ from CLI

---

## 💬 WhatsApp Interface (Beta)

**File:** `clio_agent_2/interfaces/whatsapp.py`

A **webhook-based** bot using `pywa` library over the WhatsApp Cloud API.

### Requirements

- Meta Developer Account
- WhatsApp Business Account
- Public HTTPS webhook URL (NGROK or real server)
- Required credentials: `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `WHATSAPP_WEBHOOK_URL`

### Key Behaviors

- **Message length cap**: 4096 chars, auto-split for long responses
- **Webhook mode**: Receives prompts via WhatsApp Cloud API webhooks
- **Legacy mode**: `node whatsapp_service.js` for one-off message sending

### Running

```bash
# Webhook bot mode
python3 run.py --whatsapp

# One-off message send (legacy)
python3 run.py --whatsapp +1234567890 "Hello from Clio-Agent!"
```

---

## 🔌 The Interface Contract

Every interface must implement:

```python
class SomeInterface:
    def __init__(self, agent: ClioAgent, **kwargs):
        self.agent = agent
        self.response_callbacks = [self._route_to_platform]

    def _route_to_platform(self, message: str):
        """Send message to the user via this platform."""
        ...

    async def start(self):
        """Start the interface event loop."""
        self.agent.register_response_callback(self._route_to_platform)
        await self.agent.initialize()
        await self.agent.ensure_autonomous_loop()
        ...
```

When the agent's `say` tool fires, it calls all registered callbacks. Each interface registers its own callback that formats and delivers the message.

---

## 🧭 Related Docs

- [Core Modules: ClioAgent](CORE_MODULES.md#coreagentpy--clioagent-the-brain) — the shared backend
- [API Reference](API.md) — programmatic interface usage
- [Bot Interfaces](../../user/BOT_INTERFACES.md) — user-oriented setup guide for Telegram, Discord, WhatsApp
