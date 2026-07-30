# Tools → Shell Tool (`ShellCommandTool`)

`shell_command` executes shell commands on the local machine. It is the most
powerful tool — and the biggest risk.

## Names

Registered under three names that all run the same function:

- `shell_command`
- `run_shell_command`
- `execute_shell_command`

## `run_command(command=None, cmd=None, ...)`

- `command` (preferred) or `cmd` (alias) is the shell string to run.
- Executes with the privileges of the user that launched the agent.
- Returns a `ToolResult` containing the combined output and the **exit code**
  (e.g. `"Exit code: 0"`). Non-zero exits set `success=False` and include the code
  in `error` (e.g. `"code 7"`).
- Transient command timeouts are absorbed via `core.retry.retry_async`.

## ⚠️ Safety

> **There is no sandbox, allow-list, or confirmation prompt around this tool.**
> Combined with web access and autonomous mode, a poorly-worded task or a malicious
> web page could cause it to run destructive commands (e.g. deleting files).

Mitigations:

- Run the agent on a dedicated/least-privilege account or in a container/VM you
  don't mind wiping.
- Keep autonomous mode off (`AUTONOMOUS_MODE=false`) for an on-demand assistant.
- Review [Safety](../operations/safety.md) before enabling autonomous actions.

## Test coverage

`tests/test_tool_registry.py` asserts:
`test_shell_command_is_registered_with_common_aliases`,
`test_shell_command_runs_successfully`, `test_shell_command_accepts_cmd_alias`,
`test_shell_command_surfaces_nonzero_exit_code`.

See also: [Retry Helper](../core/retry.md), [Safety](../operations/safety.md).
