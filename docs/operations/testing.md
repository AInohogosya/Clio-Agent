# Operations → Testing

The `tests/` directory contains focused regression tests. They are written to run
**without `pytest-asyncio`** — async coroutines are driven via a small `_run`
helper that calls `asyncio.run`. A standalone runner is provided for environments
without `pytest` installed.

## Files

| Test | What it covers |
|------|----------------|
| `test_tool_registry.py` | `FileSearchTool.list_directory` (path alias, empty dir, permission/OS errors), `shell_command` aliases & exit codes, `say` tool. |
| `test_tool_parsing.py` | Revamped tool-call parsing: single object, JSON array, multiple inline objects, no-tool free text. |
| `test_autonomous_say.py` | `autonomous_think` delivers `say` messages to the user via response callbacks. |
| `test_llm_settings_lock.py` | The provider/model guardrail: defaults to locked; refuses writes while locked; `/llm_unlock` enables them. |
| `test_process_message_none.py` | `process_message` always returns a string (never `None`), fixing the Telegram `len()` crash. |
| `test_providers.py` | Provider routing / discovery behaviors. |
| `test_retry.py` | `retry_async` back-off and retryable-exception behavior. |
| `test_say_command.py` | The `say` command delivery path. |
| `test_thinking_tool.py` | `ThinkingTool.think` alias keywords (`thought`/`text`/`content`/`context`/`note`/`message`) and missing-arg error. |
| `run_tool_parsing_check.py` | Standalone driver for `test_tool_parsing.py` (no pytest needed). |

## Running the tests

With `pytest` installed:

```bash
python3 -m pytest tests/ -v
```

Without `pytest` (drives the parsing tests directly):

```bash
python3 tests/run_tool_parsing_check.py
```

## Notes

- Tests import the package as `clio_agent_2.*`; run them from the repo root (the
  standalone runner and `test_tool_parsing.py` add the repo root to `sys.path`).
- Many tests mock `ClioAgent`/`LLMRouter` dependencies but exercise the **real**
  logic (parsing, guardrail, tool behavior).

See also: [Development → Contributing](../development/contributing.md).
