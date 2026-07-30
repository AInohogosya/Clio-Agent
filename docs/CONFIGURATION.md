# Configuration Reference

This is the complete reference for configuring **Clio-Agent-2**. The canonical
config file is **`clio_agent_2/config/.env`** (created from
`clio_agent_2/config/.env.example` on first run). A human-readable mirror is kept
at `clio_agent_2/config/config.yaml` (no secrets).

> ⚠️ Only `clio_agent_2/config/` is used. The root-level `config/` folder is a
> stray/legacy copy and is **ignored**.

## How configuration is loaded

`clio_agent_2/config/settings.py` defines a `Config` class that:

1. Loads `.env` via `python-dotenv` (with a pure-stdlib fallback loader if
   `python-dotenv` is unavailable).
2. Exposes each setting as an attribute (`config.openai_api_key`, etc.).
3. Preserves a `.env` file on disk (`save_to_env`) and a `config.yaml` mirror.
4. Reports which keys are configured via `validate_api_keys()`.

The single source of truth for *which providers exist* is
`core/llm_router.BUILTIN_PROVIDER_INFO`; the setup wizard reads it so the list
always matches what the agent can actually use.

## The fastest way to configure

```bash
python3 run.py            # First run auto-opens the configuration screen
python3 run.py setup      # Open the screen manually, then exit
python3 run.py config     # Alias for `setup`
/configure                # From inside the running CLI (applies live)
```

Non-interactive, single command:

```bash
python3 run.py setup \
    --openai sk-... --provider openai --model gpt-4o \
    --telegram-token 123456:ABC
```

See [Setup Wizard](configuration/setup-wizard.md) for all flags.

## LLM provider API keys

Provide **at least one**. The "large-scale model APIs":

| Variable | Provider | Default model example |
|----------|----------|-----------------------|
| `OPENAI_API_KEY` | OpenAI | `gpt-4o` |
| `GOOGLE_API_KEY` | Google (Gemini) | `gemini-pro` |
| `ANTHROPIC_API_KEY` | Anthropic | `claude-3-opus` |
| `OPENROUTER_API_KEY` | OpenRouter | `openai/gpt-4o` |
| `GROK_API_KEY` | Grok (xAI) | `grok-2` |
| `DEEPSEEK_API_KEY` | DeepSeek | `deepseek-chat` |
| `MISTRAL_API_KEY` | Mistral | `mistral-large-latest` |
| `GROQ_API_KEY` | Groq | `llama-3.3-70b-versatile` |
| `PERPLEXITY_API_KEY` | Perplexity | `sonar` |
| `TOGETHER_API_KEY` | Together | `meta-llama/...` |
| `FIREWORKS_API_KEY` | Fireworks | `accounts/fireworks/...` |
| `NVIDIA_API_KEY` | NVIDIA | `nvidia/...` |
| `QWEN_API_KEY` | Qwen (Alibaba) | `qwen-max` |
| `HUGGINGFACE_API_KEY` | HuggingFace | `meta-llama/...` |
| `DEEPINFRA_API_KEY` | DeepInfra | `meta-llama/...` |

### Local / keyless providers

- **Ollama** — keyless by default. Set `OLLAMA_BASE_URL` (e.g. `http://localhost:11434`).
  A configured base URL counts as "configured".

### OpenRouter extras

- `OPENROUTER_HTTP_REFERER` — optional HTTP referer header.
- `OPENROUTER_APP_NAME` — optional app name header.

## Search API

| Variable | Purpose |
|----------|---------|
| `SEARCH_API_KEY` | API key for web search (e.g. Serper, Bing). |

If unset, `web_search` / `fetch_url` will report that search is not configured.

## Bot tokens

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot token (`123456:ABC...`). |
| `DISCORD_BOT_TOKEN` | Discord bot token. |

Placeholder values (`your_...`, `<...>`, `...placeholder...`) are treated as
**not configured**, so the first-run screen always appears when it should.

## Core agent settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `DEFAULT_LLM_PROVIDER` | (first configured) | Provider used by default. |
| `CURRENT_MODEL` | (none) | The model to use. **Required** — the thinking loop won't start without it. |
| `LLM_SETTINGS_LOCKED` | `true` | Locks provider/model changes (guardrail). See [LLM Settings Lock](configuration/llm-lock.md). |
| `AGENT_NAME` | `Clio-Agent-2` | Display name. |
| `AUTONOMOUS_MODE` | `true` | Start the autonomous thinking loop on launch. |
| `THINKING_INTERVAL` | `5.0` | Seconds between autonomous thinking cycles. |
| `CONTEXT_LOG_MAX_LINES` | `1000` | Context log length that triggers compression. |

## Custom "Other" providers

Add any OpenAI-compatible `/chat/completions` endpoint. Up to several slots via
env vars (the setup wizard writes these for you):

| Variable | Meaning |
|----------|---------|
| `CUSTOM_<ID>_BASE_URL` | Base URL of the endpoint. |
| `CUSTOM_<ID>_API_KEY` | Optional API key. |
| `CUSTOM_<ID>_LABEL` | Human-readable label. |
| `CUSTOM_<ID>_AUTH_HEADER` | Auth header name (usually `Authorization`). |
| `CUSTOM_<ID>_AUTH_PREFIX` | Token prefix (e.g. `Bearer`). |
| `CUSTOM_<ID>_MODELS_PATH` | Path to models list, if the provider exposes one. |
| `CUSTOM_<ID>_DEFAULT_MODEL` | Default model for this provider. |

`<ID>` is a short uppercase identifier you choose (e.g. `FOO`). See
[LLM Providers](configuration/providers.md).

## Validating and inspecting config

```bash
python3 run.py status     # Print which providers/keys are configured
```

Inside the CLI:

```text
/settings        # View current settings
/config <k> <v>  # Change a setting live
/llm_lock        # Lock provider/model
/llm_unlock      # Unlock provider/model (then change)
```

## Persistence notes

- Changing a setting via `/config`, `/llm_default`, or the wizard writes through
  to `clio_agent_2/config/.env` and updates `config.yaml`.
- The LLM settings lock state is persisted, so it survives restarts.
- Custom providers are persisted (without their secret API key) in `config.yaml`.
