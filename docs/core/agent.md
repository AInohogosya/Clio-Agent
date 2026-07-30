# Core → Agent (`core/agent.py`)

`ClioAgent` is the autonomous agent. It owns the system prompt, the user-message
flow, tool-call parsing, slash-command handling, and the autonomous thinking loop.

## Constants

| Name | Value | Purpose |
|------|-------|---------|
| `MESSAGE_PROCESS_TIMEOUT` | `360.0` | Max wall-clock seconds to process one user message. |

## Responsibilities

- **`BASE_SYSTEM_PROMPT_TEMPLATE`** — the static persona/instructions (see
  [The Agent Loop](../architecture/agent-loop.md)). It is a plain string (not an
  f-string) so JSON examples with `{}` are sent verbatim.
- **`process_message(text)`** — handles one inbound message: builds the LLM
  prompt, calls the router, parses tool calls, executes them, and returns a string
  (never `None`). The agent no longer returns a natural-language reply; any
  user-facing text is delivered via the `say` command through the response
  callbacks.
- **`autonomous_think()`** — the timer-driven loop body; may emit `say` messages to
  the user via response callbacks.
- **Tool-call parsing** — `_parse_tool_calls`, `_extract_json_objects`,
  `_is_valid_tool_call` handle single objects, JSON arrays, and inline JSON,
  feeding parse failures back to the model as feedback.
- **Slash commands** — a large built-in set handled in-process (see
  [Slash Commands](../usage/slash-commands.md)): `/llm_default`, `/llm_lock`,
  `/llm_unlock`, `/llm_models`, `/llm_search`, `/config`, `/context`,
  `/clear_context`, `/status`, `/settings`, `/reconfigure`, and more.
- **Response callbacks** — `response_callbacks` lets interfaces receive any
  user-facing message, including autonomous `say` output.

## Construction

A `ClioAgent` is wired with:

- `config` — the `Config` instance (for provider/model/lock state).
- `llm_router` — the `LLMRouter`.
- `tool_registry` — the `ToolRegistry`.
- `context_log` — a `ContextLog` (for persistence/compression).
- `response_callbacks` — list of callables that render messages to the user.

## LLM settings guardrail

Any change to the provider/model is forced through `llm_router.set_llm_provider` /
`set_llm_model`, which refuse the write while `LLM_SETTINGS_LOCKED` is on. This is
how prompt injection or an autonomous self-edit is prevented from silently swapping
the model. See [LLM Settings Lock](../configuration/llm-lock.md).

## Thread-safety / concurrency

All context-log mutations go through an `asyncio.Lock`. The loop is
single-threaded async; `asyncio.run` drives each interface.

See also: [LLM Router](llm-router.md), [Retry Helper](retry.md), [Context Management](../architecture/context-management.md).
