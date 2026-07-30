# Development → Adding a Provider

LLM providers are handled by `LLMRouter` in `clio_agent_2/core/llm_router.py`.

## Option A: A built-in provider

1. Add an entry to `BUILTIN_PROVIDER_INFO` (the single source of truth the setup
   wizard reads). Include at least the `env_var` key and any metadata the UI needs.
2. Implement a provider class subclassing `LLMProvider` (ABC) with:
   - `name` property,
   - `async chat_completion(messages, model, **kwargs)`,
   - `async stream_chat(messages, model, **kwargs)`,
   - `async list_models()`.
3. Register it in `LLMRouter.register_providers` when its env key is configured.
4. Add the env key to `Config.validate_api_keys` and `settings.py` so it's
   recognized and reported by `python3 run.py status`.

`OpenAIProvider` is the reference implementation (`/chat/completions`, 120 s
timeout). Model a new OpenAI-compatible provider on it.

## Option B: A custom "Other" provider (no code needed)

Any OpenAI-compatible `/chat/completions` endpoint works via
`OpenAICompatibleProvider` by setting `CUSTOM_<ID>_*` env vars:

- `CUSTOM_<ID>_BASE_URL`
- `CUSTOM_<ID>_API_KEY`
- `CUSTOM_<ID>_LABEL`
- `CUSTOM_<ID>_AUTH_HEADER`
- `CUSTOM_<ID>_AUTH_PREFIX`
- `CUSTOM_<ID>_MODELS_PATH`
- `CUSTOM_<ID>_DEFAULT_MODEL`

Custom providers are registered at startup, count as "configured" when they have a
base URL, and are persisted (minus the secret key) in `config.yaml`. The setup
wizard can write these for you. See [LLM Providers](../configuration/providers.md).

## Guardrail compatibility

Any provider/model change must honor `LLM_SETTINGS_LOCKED` — changes go through
`set_llm_provider` / `set_llm_model`, which raise `LLMSettingsLockedError` while
locked. See [LLM Settings Lock](../configuration/llm-lock.md).

## Retry behavior

Use `retry_async` for transient failures; do **not** retry permanent errors
(missing model/provider, bad credentials). See [Retry Helper](../core/retry.md).

See also: [LLM Router](../core/llm-router.md), [Contributing](contributing.md).
