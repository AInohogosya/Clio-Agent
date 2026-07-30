# Configuration → LLM Settings Lock

The underlying LLM **provider** and **model** are the agent's most sensitive
settings. To stop them from changing *unexpectedly* or *on their own* — for example
via prompt injection picked up from the web, or an autonomous self-edit — they are
**locked by default**.

## How it works

- `LLM_SETTINGS_LOCKED=true` in `config/.env`. It defaults to `true` **even when
  the line is missing**, so the safe posture is the default.
- `LLMRouter` keeps `default_provider` / `current_model` in private fields and
  exposes them through properties whose setters call `set_llm_provider` /
  `set_llm_model`.
- Both setters refuse the write while locked, raising `LLMSettingsLockedError`.
- Every write path — `/llm_default`, `/config provider|model`, the `/reconfigure`
  wizard — goes through this guardrail.

## Making a deliberate change

```text
/llm_unlock                  # allow changes for this session
/llm_default openai gpt-4o   # (or /config model gpt-4o)
/llm_lock                    # re-secure afterwards
```

`force=True` (the only way to bypass the lock programmatically) is used **only** by
the explicit `/llm_unlock` flow — never by normal commands.

## Persistence

The lock state is written to `config/.env`, so it is honored after a restart. The
YAML mirror reflects it too.

## Scope

The lock guards the **model**, not the **tools**. Provider/model changes are
blocked by default, but tool actions — including `shell_command` — are not
similarly gated. See [Safety](../operations/safety.md).

See also: [LLM Router](../core/llm-router.md), [LLM Providers](providers.md),
[Core → Agent](../core/agent.md).
