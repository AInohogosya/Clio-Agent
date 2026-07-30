# Configuration → Setup Wizard

The setup wizard is the single, navigable **configuration screen** for Clio-Agent-2.
It covers every secret/setting the agent needs: LLM provider keys, the search key,
Telegram/Discord tokens, and the default model.

## Interactive usage

```bash
python3 run.py                 # First run auto-opens the screen
python3 run.py setup           # Open the screen manually, then exit
python3 run.py config          # Alias for `setup`
/configure                     # From inside the running CLI (applies live)
```

On first run (no real API key + model), `main.py` calls `auto_configure_if_needed`,
which launches this screen automatically. Use `python3 run.py --no-setup` to skip.

## Non-interactive (batch) usage

```bash
python3 run.py setup \
    --openai sk-... --google AIza... --anthropic sk-ant-... \
    --openrouter sk-or-... --grok xai-... --deepseek sk-... \
    --search serp-... \
    --telegram-token 123456:ABC --discord-token xyz \
    --provider openai --model gpt-4o
```

Run `python3 run.py setup --help` for the full flag list. When
`setup`/`config`/`configure` is given extra flags via `run.py`, they are forwarded
to the wizard's batch mode through `apply_overrides_from_argv`.

## How it stays in sync

The wizard reads its provider catalogue from `core.llm_router.BUILTIN_PROVIDER_INFO`
— the single source of truth — so the screen always matches the providers the agent
can actually use. It writes values through `Config.save_to_env` (persisting
`.env`) and updates the `config.yaml` mirror.

## Adding custom providers

The wizard also writes the `CUSTOM_<ID>_*` variables for any "Other"
OpenAI-compatible provider you add by ID. See [LLM Providers](providers.md).

See also: [Environment Reference](env-reference.md), [Configuration Reference](../CONFIGURATION.md).
