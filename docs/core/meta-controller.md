# Core → Meta Controller (`meta_controller.py`)

`meta_controller.py` provides two "meta" capabilities for the agent framework. It
is intentionally standalone and import-safe: no side effects at import, and it
duck-types the router (never imports the concrete `LLMRouter`).

## `RepetitionDetector`

Detects when the agent is stuck repeating the same action.

- **Signature** of an action:
  `sha1(f"{tool}|{sorted(args.items())}|{result_ok}")[:16]`
- Keeps a bounded, rolling **window** (default **6**) of the most recent
  signatures.
- The agent is considered **stuck** when the latest signature appears at least
  `threshold` (default **4**) times inside the window.

API:

| Method | Purpose |
|--------|---------|
| `record(tool, args, result_ok)` | Record one action; returns its signature. |
| `is_stuck()` | `True` if the latest signature repeats ≥ threshold times. |
| `reset()` | Clear recorded history. |

> ⚠️ Detection is **basic**: it only catches an *exact* action signature repeating
> within a small recent window, so varied or long-pause loops may go unnoticed.

## Meta-LLM watchdog

`run_meta(llm_router, recent_entries, recent_recommendations)` asks a *separate*
meta-LLM to read the recent context and emit a single code-blocked `ACTION`
describing the next move.

- Builds a context blob (`_build_context_blob`) from recent entries and
  recommendations.
- Calls `await llm_router.chat([system, user])` with `META_SYSTEM_PROMPT`.
- Parses the `ACTION` block via `extract_action_block`.
- On **any** failure (LLM error or no parseable block) raises `RuntimeError` whose
  message is built by `_coding_agent_prompt` (a self-repair prompt helper).

This is useful as an optional supervisor that can redirect the agent when the
`RepetitionDetector` flags a stuck state.

## Exports

`RepetitionDetector`, `META_SYSTEM_PROMPT`, `extract_action_block`, `run_meta`,
`_coding_agent_prompt`.

See also: [Core → Agent](agent.md), [Known Limitations](../operations/known-limitations.md).
