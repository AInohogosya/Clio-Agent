# Configuration → LLM Providers

Clio-Agent-2 routes every chat completion through `LLMRouter`, which supports many
built-in providers plus arbitrary custom "Other" providers.

## Built-in providers

Authoritative list (`BUILTIN_PROVIDER_INFO`):

| Provider | Env key | Notes |
|----------|---------|-------|
| OpenAI | `OPENAI_API_KEY` | `gpt-4o`, etc. |
| Google (Gemini) | `GOOGLE_API_KEY` | |
| Anthropic | `ANTHROPIC_API_KEY` | |
| OpenRouter | `OPENROUTER_API_KEY` | Many models; optional referer/app headers. |
| Grok (xAI) | `GROK_API_KEY` | |
| DeepSeek | `DEEPSEEK_API_KEY` | |
| Mistral | `MISTRAL_API_KEY` | |
| Groq | `GROQ_API_KEY` | |
| Perplexity | `PERPLEXITY_API_KEY` | |
| Together | `TOGETHER_API_KEY` | |
| Fireworks | `FIREWORKS_API_KEY` | |
| NVIDIA | `NVIDIA_API_KEY` | |
| Qwen (Alibaba) | `QWEN_API_KEY` | |
| HuggingFace | `HUGGINGFACE_API_KEY` | |
| DeepInfra | `DEEPINFRA_API_KEY` | |
| Ollama (local) | `OLLAMA_BASE_URL` | Keyless by default. |

Browse and search available models at runtime:

```text
/llm_models            # List models by provider
/llm_search gpt-4      # Search models by name
```

## Custom "Other" providers

Any service exposing an OpenAI-compatible `/chat/completions` endpoint works out of
the box via `OpenAICompatibleProvider`. Configure with `CUSTOM_<ID>_*` env vars
(replace `<ID>` with a short uppercase id you choose):

| Variable | Meaning |
|----------|---------|
| `CUSTOM_<ID>_BASE_URL` | Base URL of the endpoint. |
| `CUSTOM_<ID>_API_KEY` | Optional API key. |
| `CUSTOM_<ID>_LABEL` | Human-readable label. |
| `CUSTOM_<ID>_AUTH_HEADER` | Auth header name (usually `Authorization`). |
| `CUSTOM_<ID>_AUTH_PREFIX` | Token prefix (e.g. `Bearer`). |
| `CUSTOM_<ID>_MODELS_PATH` | Path to models list, if exposed. |
| `CUSTOM_<ID>_DEFAULT_MODEL` | Default model for this provider. |

Custom providers are registered alongside built-ins and count as "configured" when
they have a base URL. They are persisted (without their secret key) in
`config.yaml`.

## Selecting a provider/model

```text
/llm_default openai gpt-4o     # Set default provider + model (honors the lock)
/config provider openai         # Alternative
/config model gpt-4o
```

Changes go through the guardrail (see [LLM Settings Lock](llm-lock.md)).

See also: [LLM Router](../core/llm-router.md), [Environment Reference](env-reference.md).
