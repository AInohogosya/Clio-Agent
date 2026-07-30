# Development → Contributing

Guidelines for working on Clio-Agent-2.

## Project layout

See [Directory Structure](../architecture/directory-structure.md). The agent code
lives under `clio_agent_2/`; configuration under `clio_agent_2/config/`; tests under
`tests/`.

## Environment

The launcher creates and uses a `.venv`. Activate it before running tools:

```bash
source clio_agent_2/.venv/bin/activate   # Windows: .venv\Scripts\activate
```

Dependencies are pinned in `clio_agent_2/requirements.txt`.

## Conventions

- **Async everywhere.** Interfaces, the agent loop, the router, and tools are all
  `async`/`await`.
- **Tools return `ToolResult`.** Don't raise for expected failures — return
  `ToolResult(success=False, error=...)`.
- **Clear errors & argument aliases.** Accept common aliases the model may emit
  (e.g. `filepath`/`path`), and return friendly errors on missing args.
- **Don't swallow failures.** Surface permission/IO errors in the output string.
- **Settings changes go through the lock.** Provider/model writes must honor
  `LLM_SETTINGS_LOCKED` (see [LLM Settings Lock](../configuration/llm-lock.md)).
- **`process_message` always returns a string** (never `None`).

## Documentation

When you add or change a module, update the matching file under `docs/` and link it
from `docs/README.md`. This tree is the canonical reference.

## Running tests

See [Testing](../operations/testing.md):

```bash
python3 -m pytest tests/ -v
python3 tests/run_tool_parsing_check.py   # no pytest needed
```

## One-off patches

`clio_agent_2/apply_fixes.py` is a self-contained patch script for known fresh-
install issues. Re-run it from the repo root if you hit a covered bug.

See also: [Adding a Tool](adding-tools.md), [Adding a Provider](adding-providers.md).
