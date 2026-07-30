# Context Management

The **context log** (`core/context_manager.py`) is the agent's memory. Every user
message, assistant response, tool execution, and thought is recorded as a
`ContextEntry` and used to build the conversation fed to the LLM.

## Entry types

| `entry_type` | Meaning |
|--------------|---------|
| `user_message` | A message from the user (`"User Message: ..."`). |
| `assistant_response` | The agent's reply text. |
| `tool_execution` | A tool name + arguments + result. |
| `thinking` | An internal monologue recorded via the `thinking` tool. |
| `system` | Misc. system notes. |

## Data model

`ContextEntry` stores `entry_type`, `content`, an ISO timestamp, and free-form
`metadata`. It can render to a dict (`to_dict`), a string (`__str__`), or a single
log line (`to_log_line`).

`ContextLog` keeps entries in a bounded `deque` (capacity `max_lines * 2`) with an
`asyncio.Lock` for safe concurrent access.

## Compression

When the log reaches `max_lines` (default **1000**), the log triggers an
**LLM-based compression** via a `compression_callback` (supplied by the agent).
Older entries are summarized into fewer entries while recent context is preserved.

> ⚠️ Compression failures are swallowed — the agent logs `"Compression failed"` and
> continues with uncompressed context rather than halting. Memory is therefore
> **lossy**: the agent can "forget" details from much earlier.

## Building LLM messages

`get_entries_as_messages()` converts entries to the `{"role", "content"}` list
expected by the router:

- `user_message` → `user`
- `assistant_response` → `assistant`
- `system` → `system`
- `thinking` and `tool_execution` → `system`

## Persistence

If a `persist_path` is given, the log is saved as JSON:

- **Throttled during normal operation** — every **10** added entries (and
  immediately after a compression), to bound disk I/O.
- **Flushed on shutdown** — `ContextLog.save()` / `save_async()` write the
  latest state immediately. `main.py` calls this from the SIGINT/SIGTERM
  handlers, an `atexit` hook, and the run-loop `finally`, so even a clean
  restart (`/exit`, Ctrl+C, `SIGTERM`) loses **no** recent context.
- **Crash-safe** — `_save_to_file()` rotates a `*.json.bak` copy of the
  previous good state *before* overwriting, and `load_from_file()` falls back
  to that `.bak` if the primary file is missing or corrupt.
- Survives restarts via `load_from_file()`.
- `clear()` backs the current state up to `*.trash.json` and persists the
  empty state; `restore_backup()` (exposed as `/restore_context`) recovers it,
  so `/clear_context` is no longer irreversible.
- `export_to_file()` writes a human-readable `.log`-style dump.
- `save_async()` is the async wrapper used by the loop.

> ⚠️ Normal operation still throttles disk writes to every ~10 entries, so a
> **hard crash** (e.g. `SIGKILL`, power loss) within that window can lose the
> most recent few entries. A clean restart always flushes first, and the `.bak`
> protects against a half-written file.

See also: [The Agent Loop](agent-loop.md), [Tools → Thinking Tool](../tools/thinking-tool.md).
