# Configuration → Environment Reference

This is the full inventory of environment variables recognized by
`clio_agent_2/config/.env` (the canonical config file). The readable mirror is
`clio_agent_2/config/config.yaml`.

For the narrative version, see [Configuration Reference](../CONFIGURATION.md).

## LLM provider keys

Provide at least one. The agent's "large-scale model APIs":

`OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`,
`GROK_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`,
`PERPLEXITY_API_KEY`, `TOGETHER_API_KEY`, `FIREWORKS_API_KEY`, `NVIDIA_API_KEY`,
`QWEN_API_KEY`, `HUGGINGFACE_API_KEY`, `DEEPINFRA_API_KEY`.

### OpenRouter extras
`OPENROUTER_HTTP_REFERER`, `OPENROUTER_APP_NAME`.

### Ollama (local, keyless)
`OLLAMA_BASE_URL` (e.g. `http://localhost:11434`). A configured base URL counts as
"configured".

## Search
`SEARCH_API_KEY` — API key for `web_search` / `fetch_url`.

## Bot tokens
`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`.

> Placeholder values (`your_...`, `<...>`, `...placeholder...`) are treated as
> **not configured**.

## Core agent settings

| Variable | Default | Meaning |
|----------|---------|---------|
| `DEFAULT_LLM_PROVIDER` | first configured | Default provider. |
| `CURRENT_MODEL` | (none) | Model to use. **Required** for the loop to start. |
| `LLM_SETTINGS_LOCKED` | `true` | Lock provider/model changes. |
| `AGENT_NAME` | `Clio-Agent-2` | Display name. |
| `AUTONOMOUS_MODE` | `true` | Start the autonomous loop on launch. |
| `THINKING_INTERVAL` | `5.0` | Seconds between thinking cycles. |
| `CONTEXT_LOG_MAX_LINES` | `1000` | Triggers compression. |

## Custom "Other" providers (`CUSTOM_<ID>_*`)

For each custom provider, set (replacing `<ID>` with a short uppercase id):

`CUSTOM_<ID>_BASE_URL`, `CUSTOM_<ID>_API_KEY`, `CUSTOM_<ID>_LABEL`,
`CUSTOM_<ID>_AUTH_HEADER`, `CUSTOM_<ID>_AUTH_PREFIX`, `CUSTOM_<ID>_MODELS_PATH`,
`CUSTOM_<ID>_DEFAULT_MODEL`.

See [LLM Providers](providers.md).

## Inspecting and validating

```bash
python3 run.py status        # Which providers/keys are configured?
```

Inside the CLI: `/settings`, `/status`, `/config <k> <v>`.
