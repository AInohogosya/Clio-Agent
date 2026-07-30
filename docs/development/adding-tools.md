# Development → Adding a Tool

Tools live in `clio_agent_2/tools/tool_registry.py` and are registered in
`ToolRegistry._register_default_tools`. A tool is just an `async` function that
returns a `ToolResult`.

## 1. Implement the tool

```python
class MyTool:
    @staticmethod
    async def do_thing(arg=None, alias=None) -> ToolResult:
        target = arg if arg is not None else alias
        if not target:
            return ToolResult(False, "", "Missing required argument: 'arg'.")
        try:
            # ... do work ...
            return ToolResult(True, "result text")
        except Exception as e:
            return ToolResult(False, "", f"Error: {e}")
```

Rules of thumb (enforced by existing tests):

- Accept **canonical + alias** argument names the model may emit.
- Return a `ToolResult`; don't raise for expected failures.
- **Surface** errors (e.g. permission/IO) in the output/error string.

## 2. Register it

In `ToolRegistry._register_default_tools`:

```python
self.register_tool("do_thing", MyTool.do_thing)
```

You can register multiple aliases pointing at the same function
(`run_shell_command`, `execute_shell_command` do this).

## 3. Make it discoverable

If the model should know about the tool, add it to the tool list the system prompt
surfaces (the `AVAILABLE TOOLS` description). The `say` tool is registered but
intercepted rather than executed — follow that pattern if your tool is
special-cased by the agent.

## 4. Log it

`ToolRegistry.execute_tool` already logs every execution (command/args + result or
error) to the context log when one is attached, so new tools are automatically
auditable.

## 5. Test it

Add a test under `tests/` following the no-`pytest-asyncio` style
(`asyncio.run(coro)`). See [Testing](../operations/testing.md) and
`tests/test_tool_registry.py`.

See also: [Tools Overview](../tools/overview.md), [Contributing](contributing.md).
