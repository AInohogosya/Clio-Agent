# First Launch & Configuration

What happens when you run Clio-Agent-2 for the first time, and how to work through it.

---

## 🔄 What Happens on First Launch

Running `python3 run.py` without any prior setup triggers a **fully automated first-run sequence**:

1. **System check** — verifies Python version and write permissions
2. **Virtual environment creation** — creates `.venv/` if missing
3. **Dependency installation** — installs all Python packages
4. **Config file creation** — copies `.env.example` to `clio_agent_2/config/.env`
5. **Configuration screen** — opens the interactive setup wizard
6. **Agent launch** — starts the CLI (or your chosen interface)

> **Note:** If the configuration screen doesn't appear, it means you already have a real API key and default model set (or your `.env` was pre-populated). You can open it manually with `python3 run.py setup`.

---

## ⚙️ The Configuration Screen

The screen is a **navigable, single-page form** with all settings in one place. It is the recommended way to configure Clio-Agent-2.

### What You Set Up

| Section | What It Controls |
|---------|-----------------|
| **LLM Provider Keys** | Every supported AI provider's API key |
| **Default Model** | The LLM used by default (MUST be explicitly set) |
| **Web Search** | The search API key for the `web_search` tool |
| **Telegram Bot** | Bot token for Telegram interface |
| **Discord Bot** | Bot token for Discord interface |
| **WhatsApp** | WhatsApp Business API credentials |
| **Agent Behavior** | Autonomous mode, thinking interval, context size |

### Keyboard Navigation

The configuration screen uses `prompt_toolkit` — standard keyboard navigation:

- **↑ / ↓** or **j / k** — move between options
- **Tab** — next field
- **Space / Enter** — toggle or select
- **Esc / Ctrl+C** — cancel without saving

---

## 📝 Configuring Later With Slash Commands

From inside any running session (CLI or Telegram):

| Command | Purpose |
|---------|---------|
| `/configure` | Re-open the full configuration screen (live-updating) |
| `/reconfigure` | Non-interactive wizard (for Discord/WhatsApp) |
| `/config <setting> <value>` | Change one setting at once |
| `/settings` | View current settings |
| `/status` | Show agent status and readiness |
| `/llm_default <provider> [model]` | Change the active LLM provider/model |

### Examples

```text
/config autonomous_mode false
/config thinking_interval 10
/llm_default google gemini-2.0-pro
/llm_lock    # re-secure after changing
```

---

## 🔑 Setting Up API Keys

### Minimum Required

You **must** set two things before the agent will work:

1. **A real API key** for at least one LLM provider.
2. **A default model** (`DEFAULT_MODEL`). There is no built-in default.

Without both, the agent launches but the autonomous loop will not run.

### Placeholders Are Rejected

The `.env` template ships with placeholder values like `your_openai_api_key_here`. These are automatically detected and treated as "not configured." Replace them with real credentials.

---

## 🧩 Multi-Provider Setup

You can and should configure multiple providers for flexibility:

```env
OPENAI_API_KEY=sk-proj-...
GOOGLE_API_KEY=AIza...
ANTHROPIC_API_KEY=sk-ant-...
```

Then switch between them:

```text
/llm_providers        # see what's configured
/llm_models openai   # list models for a provider
/llm_default google gemini-1.5-pro  # switch
/llm_lock            # re-enable the lock
```

---

## ⚠️ The LLM Settings Lock

By default (`LLM_SETTINGS_LOCKED=true`), the active provider and model **cannot be changed** without explicitly unlocking. This is a security feature that prevents prompt-injection from silently switching your model.

```text
/llm_unlock                     # allow changes
/llm_default openai gpt-4o      # change model
/llm_lock                       # lock again
```

---

## 🤖 Setting Up Bot Interfaces

### Telegram

1. Open Telegram, message **@BotFather**, use `/newbot` to create a bot.
2. Copy the token it gives you.
3. Configure it:

```bash
python3 run.py setup --telegram-token 123456789:ABCdef...
```

Or inside the running agent:

```text
/configure → Telegram Bot Token
```

### Discord

1. Go to https://discord.com/developers/applications
2. Create a new application, add a bot, copy the token.
3. Enable **Message Content Intent** under Privileged Gateway Intents.
4. Configure it:

```bash
python3 run.py setup --discord-token YOUR_DISCORD_TOKEN
```

### WhatsApp (Business API)

Requires Meta Developer Account + WhatsApp Business Account. See [Bot Interfaces](BOT_INTERFACES.md) for detailed setup.

---

## 🎯 Check Your Configuration

```bash
python3 run.py status
```

This prints a full status table showing every provider key, the default model, whether autonomous mode is on, and whether the agent is **Ready**.

---

## 🔄 Configuration Storage

| File | What Goes There |
|------|----------------|
| `clio_agent_2/config/.env` | Secrets (API keys, tokens) and settings |
| `clio_agent_2/config/config.yaml` | Human-readable mirror (no secrets, auto-synced) |

> **Important:** Always edit the `.env` in `clio_agent_2/config/`, NOT the one at the repo root. The root `config/.env` is ignored.

---

## 🚀 Next Steps

- Learn the [CLI commands](CLI_GUIDE.md)
- Understand [autonomous mode](AUTONOMOUS_MODE.md)
- Read [Safety & Best Practices](SAFETY.md)
