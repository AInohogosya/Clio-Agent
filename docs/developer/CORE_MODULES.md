# Core Modules

Deep dives into each core module in `clio_agent_2/core/`.

---

## 📦 `core/agent.py` — ClioAgent (the brain)

**File:** `clio_agent_2/core/agent.py`
**Lines:** ~1150

### What It Does

`ClioAgent` is the central class. It ties together context memory, the tool registry, and LLM routing into a working agent loop.

### Key Design Decisions

1. **One system block per LLM call** — The agent constructs a single `system` message (containing `BASE_SYSTEM_PROMPT` + rolling summary). Every provider gets exactly one system block, which is required by Anthropic and keeps the prompt clean for OpenAI.

2. **Tool calls are parsed loosely** — The model may emit a single JSON object, a JSON array, or JSON objects embedded in free-form prose. All three are accepted so multi-step turns execute rather than being silently dropped.

3. **`say` is the only output channel** — Plain model text is never shown to the user. Only the `say` tool produces user-visible output. The model is told this explicitly in its system prompt.

4. **Failures are logged, not surfaced** — `process_message` never returns a user-error. Failures are logged to context; the loop continues. This prevents misleading error messages.

5. **Circuit breaker** — After 5 consecutive LLM failures, the autonomous loop pauses. Only `/resume` or `/start` restarts it.

### Key Methods

| Method | Purpose |
|--------|---------|
| `_system_block()` | Build the single system message (base prompt + summary) |
| `_build_context_messages(user_turn)` | Assemble the full prompt (system + hot window + user message) |
| `_run_agent_turn(messages)` | Drive the multi-turn tool-execution loop |
| `_execute_tool_round(tool_calls)` | Run a batch of tools, return `[TOOL OK]` / `[TOOL FAILED]` feedback |
| `_parse_tool_calls(response)` | Extract tool calls from model response in all supported formats |
| `_compress_context(entries)` | LLM-based summarization of old context entries |
| `run_autonomous_loop()` | Background timer loop with backoff + circuit breaker |

---

## 📦 `core/llm_router.py` — LLMRouter (the connector)

**File:** `clio_agent_2/core/llm_router.py`
**Lines:** ~1264

### What It Does

Routes all LLM API calls to the configured provider. Supports 15+ built-in providers and any custom OpenAI-compatible endpoint.

### Provider Architecture

- **`BuiltinProvider`** (ABC) — base class for built-in providers
- Each has a dedicated wrapper for the provider's native API
- OpenAI-compatible providers extend `OpenAICompatibleProvider`
- Custom "Other" providers are configured via `CUSTOM_*` env vars

### LLM Settings Lock

- `lock_llm_settings()` / `unlock_llm_settings()` / `set_llm_provider()` / `set_llm_model()`
- `LLMSettingsLockedError` is raised on attempts to change locked settings
- Lock state is persisted to `.env` via `save_settings()`

### Key Methods

| Method | Purpose |
|--------|---------|
| `chat(messages, **kwargs)` | Send chat completion request, return text |
| `list_all_models()` | Get available models per provider |
| `search_models(query)` | Search models by name |
| `get_available_providers()` | List providers with configured keys |
| `set_llm_provider(provider)` | Change active provider |
| `set_llm_model(model)` | Change active model |
| `lock_llm_settings()` | Enable the lock |
| `unlock_llm_settings()` | Disable the lock |

---

## 📦 `core/context_manager.py` — ContextLog (the memory)

**File:** `clio_agent_2/core/context_manager.py`

### What It Does

The agent's long-term memory. A rolling context log that:

1. Records every user message, tool call, tool result, and thinking note
2. Keeps a hot sliding window (~50 entries) for immediate context
3. Archives older/cold entries via LLM summarization
4. Persists to disk on every write (`data/context.json`)
5. Creates a `.bak` backup for corruption protection
6. Archives compressed entries to `data/context_archive.jsonl`

### Three Tiers of Memory

```
Hot window (in memory, in prompt)   ← recent 50 entries
Rolling summary (in system prompt)  ← compressed older entries
Cold archive (on disk only)         ← never lost, re-loadable
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `add_user_message(content)` | Log a user message |
| `add_tool_call(name, args)` | Log a tool invocation |
| `add_tool_result(name, result)` | Log a tool result |
| `add_thinking(content)` | Log internal monologue |
| `get_entries_as_messages(max_tokens)` | Get entries formatted for the LLM |
| `clear()` | Wipe the log (backs up first) |
| `restore_backup()` | Recover the last cleared backup |
| `load_from_file()` | Restore from `context.json` |
| `save()` / `save_async()` | Persist to disk |

### Compression

When `line_count > max_lines`, the oldest batch (size=`cold_batch`) is compressed via LLM summarization. The summary is injected into the system prompt as a "Rolling context summary." Raw entries are archived to disk.

---

## 📦 `core/retry.py` — RetryProtocol

**File:** `clio_agent_2/core/retry.py`

Implements generic async retry with exponential backoff and jitter. Used by LLM calls to handle transient failures.

```python
from clio_agent_2.core.retry import retry_with_backoff

@retry_with_backoff(max_retries=5, base_delay=1.0)
async def call_llm():
    ...
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_retries` | 5 | Maximum attempts |
| `base_delay` | 1.0s | Initial retry delay |
| `max_delay` | 60.0s | Maximum retry delay |
| `backoff_factor` | 2.0 | Multiplier per retry |

---

## 📦 `core/token_budget.py` — TokenBudget

**File:** `clio_agent_2/core/token_budget.py`

Estimates token usage using `tiktoken` with a 4-char/token fallback when tiktoken is unavailable.

```python
from clio_agent_2.core.token_budget import TokenBudget

budget = TokenBudget(model="gpt-4o")
token_count = budget.estimate(text)
max_entries = budget.fit_entries(entries, max_tokens=4000)
```

---

## 🧭 Related Docs

- [Architecture Overview](ARCHITECTURE.md) — system design and module map
- [API Reference](API.md) — using the core modules programmatically
- [Tool System](tools/OVERVIEW.md) — the extensible tool registry
