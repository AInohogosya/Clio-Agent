# Configuration Reference

Exhaustive list of every configuration parameter, environment variable, and setting in Clio-Agent-2.

---

## 📁 Configuration File

| File | Purpose |
|------|--------|
| `clio_agent_2/config/.env` | **Primary config** — secrets and settings |
| `clio_agent_2/config/.env.example` | Template with placeholder values |
| `clio_agent_2/config/config.yaml` | Human-readable mirror (no secrets, auto-synced) |
| `clio_agent_2/config/setup_env.py` | Interactive configuration logic |
| `clio_agent_2/config/settings.py` | Config class: loading, validation, persistence |

---

## 🔑 LLM Provider Keys

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `OPENAI_API_KEY` | string | No | OpenAI API key |
| `GOOGLE_API_KEY` | string | No | Google Gemini API key |
| `ANTHROPIC_API_KEY` | string | No | Anthropic API key |
| `OPENROUTER_API_KEY` | string | No | OpenRouter API key |
| `GROK_API_KEY` | string | No | xAI Grok API key |
| `DEEPSEEK_API_KEY` | string | No | DeepSeek API key |
| `MISTRAL_API_KEY` | string | No | Mistral API key |
| `GROQ_API_KEY` | string | No | Groq API key |
| `PERPLEXITY_API_KEY` | string | No | Perplexity API key |
| `TOGETHER_API_KEY` | string | No | Together API key |
| `FIREWORKS_API_KEY` | string | No | Fireworks API key |
| `NIM_API_KEY` | string | No | NVIDIA NIM API key |
| `QWEN_API_KEY` | string | No | Qwen (Alibaba) API key |
| `HUGGINGFACE_API_KEY` | string | No | HuggingFace Inference API key |
| `DEEPINFRA_API_KEY` | string | No | DeepInfra API key |

At least **one** must be set. Ollama needs no API key (uses localhost by default).

---

## 🔐 Custom "Other" Provider Variables

| Variable Pattern | Required | Description |
|------------------|----------|-------------|
| `CUSTOM_N_NAME` | ✅ | Display name |
| `CUSTOM_N_BASE_URL` | ✅ | API base URL (e.g. `http://localhost:8000/v1`) |
| `CUSTOM_N_API_KEY` | ❌ | API key (often optional for local services) |
| `CUSTOM_N_MAX_TOKENS` | ❌ | Max tokens per response |
| `CUSTOM_N_CONTEXT_WINDOW` | ❌ | Token context window for estimation |

Replace `N` with the provider index (`CUSTOM_1_*`, `CUSTOM_2_*`, etc.).

---

## ⚙️ Core Settings

| Variable | Default | Type | Description |
|----------|---------|------|-------------|
| `DEFAULT_LLM_PROVIDER` | `openai` | string | Active LLM provider name |
| `DEFAULT_MODEL` | *(none)* | string | Model identifier. **Must be set explicitly.** |
| `AUTONOMOUS_MODE` | `true` | boolean | Start background thinking loop on launch |
| `THINKING_INTERVAL` | `5.0` | float (seconds) | Time between autonomous cycles |
| `CONTEXT_LOG_MAX_LINES` | `1000` | int | Auto-compress context above this threshold |
| `LLM_SETTINGS_LOCKED` | `true` | boolean | Block provider/model changes without unlock |
| `AGENT_NAME` | `Clio-Agent-2` | string | Agent display name |

---

## 🔍 Web Search

| Variable | Required | Description |
|----------|----------|-------------|
| `SEARCH_API_KEY` | No | Serper/Bing-style search API key. Without it, `web_search` errors. |

---

## 🤖 Bot Tokens

| Variable | Required For | Description |
|----------|-------------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram | Bot token from @BotFather |
| `DISCORD_BOT_TOKEN` | Discord | Bot token from Discord Developer Portal |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp | Meta phone number ID |
| `WHATSAPP_ACCESS_TOKEN` | WhatsApp | Meta access token |
| `WHATSAPP_APP_SECRET` | WhatsApp | Meta app secret |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | WhatsApp | Custom webhook verify token |
| `WHATSAPP_WEBHOOK_URL` | WhatsApp | Public HTTPS webhook URL |
| `WHATSAPP_WEBHOOK_PORT` | WhatsApp | Port (default: 8080) |

---

## 🔧 OpenRouter Extras

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_HTTP_REFERER` | auto-set | HTTP referer header |
| `OPENROUTER_APP_NAME` | auto-set | App name header |

These are set automatically when an OpenRouter key is detected.

---

## ⚙️ Internal Constants (not user-configurable)

| Constant | Value | Location | Description |
|----------|-------|----------|-------------|
| `MAX_TOOL_ITERATIONS` | 5 | `agent.py` | Max tool rounds per turn |
| `CIRCUIT_BREAKER_THRESHOLD` | 5 | `agent.py` | Consecutive failures before pause |
| `MAX_CONTEXT_TOKENS` | 8000 | `agent.py` | Per-call context budget |
| `DEFAULT_CONTEXT_WINDOW_SIZE` | 50 | `agent.py` | Hot window size |
| `COLD_ARCHIVE_BATCH` | 25 | `agent.py` | Entries per compression batch |
| `MESSAGE_PROCESS_TIMEOUT` | 3600s | `agent.py` | Per-message timeout |

---

## 📝 Setting Values via Slash Commands

These runtime changes are persisted to `.env`:

```text
/config provider openai                → DEFAULT_LLM_PROVIDER
/config model gpt-4o                   → DEFAULT_MODEL
/config autonomous_mode false          → AUTONOMOUS_MODE
/config thinking_interval 10           → THINKING_INTERVAL
/llm_default google gemini-1.5-pro    → DEFAULT_LLM_PROVIDER + DEFAULT_MODEL
/llm_lock                              → LLM_SETTINGS_LOCKED=true
/llm_unlock                            → LLM_SETTINGS_LOCKED=false
```

---

## 🧭 Related Docs

- [Configuration Guide](../../user/CONFIGURATION.md) — user-facing config guide
- [API Reference: Config](API.md#config) — programmatic config usage
- [LLM Providers](../../user/LLM_PROVIDERS.md) — provider-specific setup
