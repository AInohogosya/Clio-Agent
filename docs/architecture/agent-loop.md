# The Agent Loop

`ClioAgent` (`core/agent.py`) is the brain. It turns a user message (or a timer
tick) into a sequence of LLM calls and tool executions, and logs everything.

## System prompt

A static `BASE_SYSTEM_PROMPT_TEMPLATE` defines the agent's identity, capabilities,
operation mode, guidelines, and the rules for the `thinking` / `say` tools. It is
combined at runtime with a timestamp and the tool list to form the system message.

Key behavioral rules baked into the prompt:

- The agent operates in a **continuous autonomous loop**.
- It should think before acting and log thoughts with the `thinking` tool.
- It may **proactively message the user** without a prior message — including
  casual, friend-style check-ins when it has free time (it's encouraged to strike
  up a conversation, not just report progress).
- `/think`, `/models`, `/search_models` are internal-only and must not be shown to
  users.

## Processing a message — `process_message`

1. The context log's recent entries are converted to LLM messages.
2. The agent calls `llm_router.chat(messages)` (which retries transient failures).
3. The response is parsed for **tool calls** (single JSON object, a JSON array of
   tool calls, or several JSON objects embedded in prose).
4. Each valid tool call is executed via the `ToolRegistry`; results are logged.
5. If the model emits a `say` command, its message is delivered to the user through
   the response callbacks. The model's plain text is NOT returned as a reply — the
   auto-reply system has been removed.

`process_message` **always returns an empty string** — a regression safety
net for interfaces that call `len()` on the result. User-facing output is
delivered through `send_response` (the `say` tool), so the method returns `""`
in both the success and failure cases; a failed turn is persisted to the
context log rather than surfaced as a scary error to the user, since the
autonomous loop continues regardless and the record already lives in the
agent's running context. A `MESSAGE_PROCESS_TIMEOUT` (3600 s — tenfold the
original 360 s, to match the per-attempt LLM timeout) bounds how long a
single message may take.

## Tool-call parsing

The parser (`_parse_tool_calls`) extracts every JSON object shaped like
`{"tool": "<name>", "arguments": {...}}`. Each candidate is validated against the
registered tools (`_is_valid_tool_call`). Unknown tools or malformed JSON are
reported back to the model as feedback rather than silently dropped, so the model
can self-correct.

## Autonomous thinking — `autonomous_think`

When `AUTONOMOUS_MODE` is on, a timer fires every `THINKING_INTERVAL` seconds and
calls `autonomous_think`, which:

1. Builds a prompt to reflect on recent context.
2. Calls the LLM and parses any tool calls / `say` command.
3. If the model emits a `say` command that addresses the user, the message is
   pushed through the registered **response callbacks** — so the user actually
   receives it.

Multiple user-facing messages can accumulate over time (one per cycle).

## Response callbacks

Interfaces register a callback (e.g. `send_response`) on the agent. Anything the
agent wants to show the user — including autonomous `say` messages — is delivered
through these callbacks, keeping the agent decoupled from any specific interface.

## Slash-command handling

`ClioAgent` also implements a large set of slash commands (`/llm_default`,
`/llm_lock`, `/llm_search`, `/context`, `/clear_context`, `/config`, etc.). These
are processed inside `process_message` and never reach the LLM.

See also: [Context Management](context-management.md), [Slash Commands](../usage/slash-commands.md),
[Autonomous Mode](../usage/autonomous-mode.md), [Core → Agent](agent.md).
