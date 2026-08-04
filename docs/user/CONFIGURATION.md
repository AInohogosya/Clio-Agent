# Configuration Guide

Complete reference for every setting in Clio-Agent-2.

---

## 📁 Configuration File Location

**Live config:** `clio_agent_2/config/.env`
**Mirror (readable, no secrets):** `clio_agent_2/config/config.yaml`
**Template (copied on first run):** `clio_agent_2/config/.env.example`

> ⚠️ There is also a `config/` folder at the repository root, but it is **legacy/stray** and is NOT read by the program. Only `clio_agent_2/config/.env` is used.

---

## 🗂️ Configuration Sections

### LLM Provider Keys

```env
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
ANTHROPIC_API_KEY=...
OPENROUTER_API_KEY=...
GROK_API_KEY=...
DEEPSEEK_API_KEY=...
MISTRAL_API_KEY=...
GROQ_API_KEY=...
PERPLEXITY_API_KEY=...
TOGETHER_API_KEY=...
FIREWORKS_API_KEY=...
NIM_API_KEY=...
QWEN_API_KEY=...
HUGGINGFACE_API_KEY=...
DEEPINFRA_API_KEY=...
# Ollama needs no key (uses localhost)
```

At least **one key must be set** for the agent to function.

### Default Model

```env
DEFAULT_MODEL=gpt-4o
DEFAULT_LLM_PROVIDER=openai
```

`DEFAULT_MODEL` is required — there is **no built-in default**. If it is empty or still a placeholder, the autonomous loop will not start.

### Core Behavior

```env
AUTONOMOUS_MODE=true           # run background thinking loop on launch
THINKING_INTERVAL=5.0          # seconds between autonomous cycles
CONTEXT_LOG_MAX_LINES=1000     # auto-compress context above this threshold
AGENT_NAME=Clio-Agent-2       # friendly name for the agent
```

### Security

```env
LLM_SETTINGS_LOCKED=true       # block provider/model changes without /llm_unlock
```

When locked, `/llm_default` and `/config provider/model` all refuse to change anything. Run `/llm_unlock` (one-time) to make your change, then `/llm_lock` to lock again.

### Web Search (Optional)

```env
SEARCH_API_KEY=...
```

A Serper/Bing-style API key. Without it, the `web_search` tool returns an error when called.

### Bot Tokens (Optional)

```env
TELEGRAM_BOT_TOKEN=123456:ABC
DISCORD_BOT_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_APP_SECRET=...
WHATSAPP_WEBHOOK_VERIFY_TOKEN=...
WHATSAPP_WEBHOOK_URL=https://your-domain.com
WHATSAPP_WEBHOOK_PORT=8080
```

### Custom "Other" Providers

For any OpenAI-compatible endpoint (LM Studio, vLLM, LocalAI, enterprise gateways):

```env
CUSTOM_1_NAME=lm-studio
CUSTOM_1_BASE_URL=http://localhost:1234/v1
CUSTOM_1_API_KEY=sk-...
# Context window (optional)
CUSTOM_1_MAX_TOKENS=8192
# Context window estimate for token budget (optional)
CUSTOM_1_CONTEXT_WINDOW=4096
```

Multiple custom providers are supported — just use `CUSTOM_2_*`, `CUSTOM_3_*`, etc.

---

## 📝 Changing Settings at Runtime

The agent exposes several ways to modify settings without restarting:

| Method | What It Changes |
|--------|----------------|
| `/configure` | Opens the full config screen (live-updating) |
| `/reconfigure` | Non-interactive reconfiguration |
| `/config <k> <v>` | One setting at a time |
| `/llm_default <provider> [model]` | Change the LLM (respects lock) |
| `/llm_lock` / `/llm_unlock` | Toggle the model lock |

Runtime changes are **persisted to `.env`** on shutdown, so they survive restarts.

---

## 📊 Configurable Parameters Reference

| Parameter | Default | Type | Description |
|-----------|---------|------|-------------|
| `DEFAULT_LLM_PROVIDER` | `openai` | string | Active provider name |
| `DEFAULT_MODEL` | *(none)* | string | Model identifier |
| `AUTONOMOUS_MODE` | `true` | boolean | Launch the thinking loop automatically |
| `THINKING_INTERVAL` | `5.0` | float | Seconds between autonomous cycles |
| `CONTEXT_LOG_MAX_LINES` | `1000` | int | Entries before auto-compression |
| `LLM_SETTINGS_LOCKED` | `true` | boolean | Block changes to LLM settings |
| `AGENT_NAME` | `Clio-Agent-2` | string | Display name |
| `SEARCH_API_KEY` | *(none)* | string | Web search API key |
| `TELEGRAM_BOT_TOKEN` | *(none)* | string | Telegram bot token |
| `DISCORD_BOT_TOKEN` | *(none)* | string | Discord bot token |
| `WHATSAPP_PHONE_NUMBER_ID` | *(none)* | string | WhatsApp phone number ID |
| `WHATSAPP_ACCESS_TOKEN` | *(none)* | string | WhatsApp access token |
| `WHATSAPP_APP_SECRET` | *(none)* | string | WhatsApp app secret |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | *(none)* | string | WhatsApp verify token |
| `WHATSAPP_WEBHOOK_URL` | *(none)* | URL | Webhook endpoint URL |
| `WHATSAPP_WEBHOOK_PORT` | `8080` | int | Webhook server port |

---

## 🔄 Configuration Schema (`config.yaml`)

The `config.yaml` file is a human-readable mirror of `.env` (without secrets). It is kept in sync automatically. You can look at it for reference but **do not edit it** — changes will be overwritten.

---

## ✅ Verification

Always verify your configuration after changes:

```bash
python3 run.py status
```

Output shows which providers have valid keys, the current default model, whether autonomous mode is on, and a final "Ready" / "Not ready" indicator.

---

## 🧭 Related Docs

- [Installation Guide](INSTALLATION.md) — installing Clio-Agent-2
- [LLM Providers](LLM_PROVIDERS.md) — choosing and switching models
- [Bot Interfaces](BOT_INTERFACES.md) — Telegram, Discord, WhatsApp setup
