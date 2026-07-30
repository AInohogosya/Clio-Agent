# Tools Overview

Tools are the agent's capabilities. They are registered in `ToolRegistry`
(`clio_agent_2/tools/tool_registry.py`) and invoked when the LLM emits a tool
call like `{"tool": "read_file", "arguments": {"filepath": "/tmp/x.txt"}}`.

## `ToolResult`

Every tool returns a `ToolResult`:

| Field | Meaning |
|-------|---------|
| `success` | `True`/`False`. |
| `output` | The result text (or empty on failure). |
| `error` | An error message when `success` is `False`. |

`to_dict()` serializes it for logging.

## `ToolRegistry`

| Method | Purpose |
|--------|---------|
| `register_tool(name, func)` | Register an async tool by name. |
| `get_tool(name)` | Look up a tool function. |
| `list_tools()` | List all registered tool names. |
| `execute_tool(name, arguments)` | Run a tool; log the execution to the context log; wrap exceptions in `ToolResult`. |

## Default tool set

Registered in `_register_default_tools`:

| Tool | Backing class | Docs |
|------|---------------|------|
| `read_file` | `FileEditTool` | [File Tools](file-tools.md) |
| `write_file` | `FileEditTool` | [File Tools](file-tools.md) |
| `append_file` | `FileEditTool` | [File Tools](file-tools.md) |
| `edit_file` | `FileEditTool` | [File Tools](file-tools.md) |
| `web_search` | `WebSearchTool` | [Web Tools](web-tools.md) |
| `fetch_url` | `WebSearchTool` | [Web Tools](web-tools.md) |
| `search_files` | `FileSearchTool` | [Search Tools](search-tools.md) |
| `search_content` | `FileSearchTool` | [Search Tools](search-tools.md) |
| `list_directory` | `FileSearchTool` | [Search Tools](search-tools.md) |
| `shell_command` | `ShellCommandTool` | [Shell Tool](shell-tools.md) |
| `run_shell_command` | `ShellCommandTool` (alias) | [Shell Tool](shell-tools.md) |
| `execute_shell_command` | `ShellCommandTool` (alias) | [Shell Tool](shell-tools.md) |
| `thinking` | `ThinkingTool` | [Thinking Tool](thinking-tool.md) |
| `say` | `SayTool` | [Say Tool](say-tool.md) |

## Logging

`execute_tool` records every run (command/args + result or error) into the context
log when one is attached, so the agent's actions are auditable and feed back into
future prompts.

## Reliability conventions

Tools follow a few conventions that the tests assert:

- **Argument aliases** — many tools accept both a canonical keyword and common
  aliases the model may emit (e.g. `filepath`/`path`, `command`/`cmd`).
- **Clear errors** — missing/invalid arguments return a friendly `ToolResult`
  error rather than raising.
- **No swallowed failures** — permission/IO errors are surfaced in the output
  string (e.g. `"ACCESS DENIED"`).

See [Development → Adding a Tool](../development/adding-tools.md) to extend the set.

See also: [Architecture → Agent Loop](../architecture/agent-loop.md).
