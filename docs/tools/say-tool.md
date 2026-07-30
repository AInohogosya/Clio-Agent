# Tools → Say Tool (`SayTool`)

`say` is the agent's explicit, first-class — and ONLY — way to address the user.
It is a **normal, executable tool**, registered and run exactly like `read_file` /
`shell_command` / `thinking`: the model emits a `say` tool call, the agent executes
it through the usual tool-execution loop (`_execute_tool_round` → `execute_tool`),
and running it delivers the message to the user through the response sink
(`send_response`). The model's plain natural-language text is NOT shown to the user;
the auto-reply behaviour has been removed, so the agent must emit the `say` tool to
communicate.

## `SayTool(context_log=None, response_sink=None)` / `say(message=None, text=None, content=None, say=None)`

- Constructed with an optional `context_log` and a `response_sink` (the agent wires
  `ClioAgent.send_response` in here). The constructor is dependency-injected so the
  tool can deliver its message without reaching into a global.
- `message` (preferred) or any alias (`text`, `content`, `say`) is the user-facing
  text.
- On a message it delivers the text via `response_sink` (best-effort), records it in
  the context log, and returns it in `output` (e.g. `"Status: all good."`).
- With no text, returns a clear error mentioning `message`.

## How it reaches the user

1. The model emits a `say` command in its response.
2. The agent executes it like any other tool. Running `say` delivers the message
   through the agent's **response callbacks** (so the CLI prints it, Telegram/Discord
   send it, etc.). This is the ONLY user-facing output path — the agent no longer
   returns a natural-language reply for a user message.
3. The surrounding JSON is never shown to the user.

In **autonomous mode**, each thinking cycle that emits a `say` command pushes one
message to the user — so the agent can proactively report progress, share
insights, or ask questions without the user messaging first.

## Test coverage

`tests/test_tool_registry.py` asserts:
`test_say_command_is_registered_as_a_tool`,
`test_say_tool_returns_the_user_facing_message`, `test_say_tool_requires_a_message`,
`test_say_tool_delivers_through_response_sink`.

`tests/test_say_command.py` / `tests/test_autonomous_say.py` verify the agent runs
`say` through `execute_tool` (the same path as other tools) and the message reaches
the user via the response channel.

See also: [The Agent Loop](../architecture/agent-loop.md),
[Autonomous Mode](../usage/autonomous-mode.md), [Tools Overview](overview.md).
