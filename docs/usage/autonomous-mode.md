# Usage → Autonomous Mode

In autonomous mode, Clio-Agent-2 runs a persistent **thinking loop** that can act
and message you on its own — without any input from you.

## What it does

When enabled, a timer fires every `THINKING_INTERVAL` seconds and calls
`autonomous_think`, which:

1. Reflects on recent context.
2. Calls the LLM and parses any tool calls / `say` command.
3. Executes tools (file, web, shell, …) and logs them.
4. If the model emits a `say` command addressing you, delivers that message through
   the response callbacks — so you receive it proactively.

Multiple user-facing messages can accumulate over time (one per cycle).

## Enabling / disabling

- `AUTONOMOUS_MODE=true` in `config/.env` starts it on launch (the default).
- Toggle at runtime: `/start` (enable), `/stop` (disable).
- Configure the cadence: `THINKING_INTERVAL=5.0` (seconds between cycles).

## Costs & risks

- The loop calls the model **every few seconds**, which **costs money and uses API
  quota**. Disable it for a purely on-demand assistant.
- Autonomous actions include the `shell_command` tool — powerful and **unsandboxed**
  (see [Safety](../operations/safety.md)).
- The agent may fetch web pages and run commands on its own initiative.

## Recommendation

For an on-demand assistant, set `AUTONOMOUS_MODE=false` (or run `/stop`). For
autonomous operation, run on a dedicated/least-privilege account or in a container.

See also: [The Agent Loop](../architecture/agent-loop.md), [Say Tool](../tools/say-tool.md).
