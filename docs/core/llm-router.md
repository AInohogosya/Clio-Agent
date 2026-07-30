# Core → LLM Router (`core/llm_router.py`)

`LLMRouter` is the abstraction over many LLM providers. Everything in the agent
talks to providers through a single method: `await llm_router.chat(messages)`.

## Supported providers

Built-in providers (authoritative list: `BUILTIN_PROVIDER_INFO`):

OpenAI, Google (Gemini), Anthropic, OpenRouter, Grok (xAI), DeepSeek, Mistral,
Groq, Perplexity, Together, Fireworks, NVIDIA, Qwen (Alibaba), HuggingFace,
DeepInfra, and Ollama (local).

On top of those, users can add arbitrary **custom "Other" providers** — any
OpenAI-compatible `/chat/completions` endpoint — via `CUSTOM_<ID>_*` env vars.
These are wrapped by `OpenAICompatibleProvider`.

## Provider abstraction

`LLMProvider` (ABC) defines the contract:

- `name` — provider id.
- `chat_completion(messages, model, **kwargs)` — returns the completion text.
- `stream_chat(messages, model, **kwargs)` — async generator of text chunks.
- `list_models()` — list available models.

`OpenAIProvider` is the reference implementation (POSTs to `/chat/completions`
with a per-attempt `ClientTimeout` that defaults to `LLM_REQUEST_TIMEOUT`
= 1200 s — tenfold the original 120 s, so slow-but-alive models are
not cut off mid-response). A caller may override it per call via
`request_timeout=...`. `list_models` keeps a lighter 60 s timeout. Custom
providers reuse this shape via `OpenAICompatibleProvider`.

## `LLMRouter.chat`

- Builds the right provider from the current `default_provider`.
- Applies retry for **transient** failures only:
  `asyncio.TimeoutError`, `aiohttp.ClientError`, `ConnectionError`, `OSError`,
  `RuntimeError`.
- **Does not retry** permanent problems: a missing model/provider (`ValueError`)
  or bad credentials (`AuthenticationError`) — retrying those only wastes time in
  the autonomous loop.
- Retries up to `max_chat_attempts` (default **5**, per the retry policy); set to
  `1` to disable retries.

## Model discovery

- `list_all_models()` — models across all configured providers.
- `search_models(query)` — fuzzy search by name.
- `get_available_providers()` — providers that have a configured key/base URL
  (distinguishes "unknown provider" from "known but unconfigured").

## Settings lock (guardrail)

`default_provider` and `current_model` are exposed as properties whose setters
call `set_llm_provider` / `set_llm_model`. Both honor `LLM_SETTINGS_LOCKED`:

- The lock **defaults to `True`** (safe posture) when the config doesn't say
  otherwise.
- A write raises `LLMSettingsLockedError` unless `force=True` — and `force` is used
  **only** by the explicit `/llm_unlock` flow, never by normal commands.

See [LLM Settings Lock](../configuration/llm-lock.md).

## Dependencies

Uses `aiohttp` for async HTTP and `core.retry.retry_async` for back-off. The router
never imports the concrete interface modules, keeping it decoupled.

See also: [Retry Helper](retry.md), [LLM Providers](../configuration/providers.md),
[Core → Agent](agent.md).
