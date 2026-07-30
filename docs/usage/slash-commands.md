# Usage → Slash Commands

Clio-Agent-2 understands a large set of slash commands (handled in-process by
`ClioAgent.process_message`, never sent to the LLM). The CLI supports all of them;
Telegram/Discord support them where the platform allows.

## Configuration & status

| Command | Purpose |
|---------|---------|
| `/configure` | Open the configuration screen (API keys, tokens, model). |
| `/reconfigure` | Interactive reconfiguration wizard. |
| `/status` | Show agent status. |
| `/settings` | View current settings. |
| `/config <setting> <value>` | Change a setting live. |

## LLM provider / model

| Command | Purpose |
|---------|---------|
| `/llm_default <provider> [model]` | Set default provider/model (honors the lock). |
| `/llm_models` | List available models by provider. |
| `/llm_search <query>` | Search for models. |
| `/llm_lock` | Lock provider/model (prevent changes). |
| `/llm_unlock` | Unlock provider/model (allow changes). |

## Context

| Command | Purpose |
|---------|---------|
| `/context [count]` | Show recent context entries. |
| `/clear_context` | Clear the context log (**irreversible**). |
| `/think <thought>` | Record a thought. |

## Autonomous mode

| Command | Purpose |
|---------|---------|
| `/start` | Enable autonomous mode. |
| `/stop` | Disable autonomous mode. |

## Session

| Command | Purpose |
|---------|---------|
| `/exit` | Exit the CLI. |

## Internal-only commands

These are for the agent's own use and must **not** be shown to or explained to
users (per the system prompt): `/think`, `/models` (deprecated; use `/llm_models`),
`/search_models` (deprecated; use `/llm_search`).

## Changing the model safely

```text
/llm_unlock
/llm_default openai gpt-4o
/llm_lock
```

The `/config` and `/llm_default` paths all honor the [LLM Settings Lock](../configuration/llm-lock.md).

See also: [Autonomous Mode](autonomous-mode.md), [CLI](../interfaces/cli.md).
