# API Reference

Python API for embedding Clio-Agent-2 programmatically or extending it.

---

## 🚀 Quick Start

```python
import asyncio
from clio_agent_2.config.settings import Config
from clio_agent_2.core.llm_router import LLMRouter
from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.tools.tool_registry import ToolRegistry
from clio_agent_2.core.context_manager import ContextLog

# 1. Load configuration
config = Config("clio_agent_2/config/.env")

# 2. Create LLM router
llm_router = LLMRouter(config)

# 3. Create the agent
agent = ClioAgent(config, llm_router)

# 4. Initialize (loads persisted context)
restored = asyncio.run(agent.initialize())
if restored:
    print(f"Context restored: {restored}")

# 5. Send a message
asyncio.run(agent.process_message("Hello, what can you do?"))

# 6. Start autonomous loop
asyncio.run(agent.start_autonomous_loop())
```

---

## 📋 `ClioAgent`

**File:** `clio_agent_2/core/agent.py`

The core agent class. Manages context, tools, LLM routing, and the autonomous loop.

### Constructor

```python
ClioAgent(config, llm_router: LLMRouter)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `config` | Config | Configuration instance (from `clio_agent_2.config.settings`) |
| `llm_router` | LLMRouter | LLM router instance |

### Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `config` | Config | The configuration object |
| `llm_router` | LLMRouter | LLM routing layer |
| `name` | str | Agent name from config |
| `context_log` | ContextLog | The context/memory log |
| `tool_registry` | ToolRegistry | Available tools |
| `autonomous_mode` | bool | Whether auto-loop is enabled |
| `thinking_interval` | float | Seconds between autonomous cycles |
| `is_running` | bool | Whether the agent is currently running |

### Methods

#### `async initialize() -> Optional[str]`
Loads persisted context from disk. Returns a restoration message or None.

#### `async process_message(message: str, deadline: Optional[float] = None) -> str`
Process a user message through the full tool-execution loop. Always returns `""`. User-facing output goes through registered callbacks via the `say` tool.

#### `async start_autonomous_loop() -> bool`
Start the background thinking loop. Idempotent — safe to call multiple times. Returns True if the loop started.

#### `async ensure_autonomous_loop() -> bool`
Start the loop only if `autonomous_mode` is True. Same as checking `autonomous_mode` then calling `start_autonomous_loop`.

#### `stop_autonomous_loop() -> None`
Stop and cancel the autonomous loop task.

#### `async save_context() -> None`
Persist current context to disk asynchronously.

#### `save_context_sync() -> None`
Synchronous context flush for signal handlers / atexit.

#### `persist_settings() -> None`
Write current LLM settings, autonomous mode, and other runtime state to `.env`.

#### `register_response_callback(callback: Callable) -> None`
Register an async function `(message: str) -> None` to receive all `say` tool output. Used by interfaces to route responses to the user.

#### `async execute_command(command: str, args: List[str]) -> str`
Execute a slash command. Returns the command output or `__EXIT__` for exit commands.

#### `async get_status() -> Dict[str, Any]`
Return a dict with `name`, `is_running`, `autonomous_mode`, `context_lines`, `available_tools`, `available_providers`.

---

## 🔌 `LLMRouter`

**File:** `clio_agent_2/core/llm_router.py`

Routes LLM requests to the configured provider. Supports 15+ built-in providers and custom OpenAI-compatible endpoints.

### Constructor

```python
LLMRouter(config)
```

### Key Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `chat` | `async chat(messages, **kwargs) -> str` | Send messages to the configured LLM, return text response |
| `list_all_models` | `async list_all_models() -> Dict[str, List[str]]` | List available models per provider |
| `search_models` | `async search_models(query: str) -> List[Dict]` | Search models by name |
| `get_available_providers` | `get_available_providers() -> List[str]` | List configured providers with valid keys |
| `set_llm_provider` | `set_llm_provider(provider: str) -> None` | Change active provider (respects lock) |
| `set_llm_model` | `set_llm_model(model: str) -> None` | Change active model (respects lock) |
| `lock_llm_settings` | `lock_llm_settings() -> None` | Enable the LLM lock |
| `unlock_llm_settings` | `unlock_llm_settings() -> None` | Disable the LLM lock |

### Exceptions

| Exception | Description |
|-----------|-------------|
| `LLMSettingsLockedError` | Raised when trying to change LLM settings while locked |

---

## 🧠 `ContextLog`

**File:** `clio_agent_2/core/context_manager.py`

Maintains the agent's rolling memory with compression, persistence, and backup.

### Constructor

```python
ContextLog(
    max_lines: int = 1000,
    window_size: int = 50,
    cold_batch: int = 25,
    compression_callback: Optional[Callable] = None,
    persist_path: str = "data/context.json",
    archive_path: str = "data/context_archive.jsonl",
)
```

### Key Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `add_user_message` | `async add_user_message(content: str)` | Log a user message |
| `add_tool_call` | `async add_tool_call(tool_name: str, arguments: dict)` | Log a tool invocation |
| `add_tool_result` | `async add_tool_result(tool_name: str, result)` | Log a tool result |
| `add_thinking` | `async add_thinking(content: str)` | Log internal monologue |
| `add_system_message` | `async add_system_message(content: str)` | Log a system event |
| `get_entries_as_messages` | `get_entries_as_messages(max_tokens: int) -> List[Dict]` | Get recent entries formatted for LLM |
| `get_recent_entries` | `get_recent_entries(count: int) -> List` | Get raw recent entries |
| `clear` | `clear()` | Clear the log (backs up first) |
| `restore_backup` | `restore_backup() -> bool` | Restore the last cleared backup |
| `load_from_file` | `load_from_file() -> bool` | Load from disk on startup |
| `save_async` | `async save_async()` | Async save to disk |
| `save` | `save()` | Synchronous save to disk |
| `get_line_count` | `get_line_count() -> int` | Current number of entries |

---

## 🔧 `Config`

**File:** `clio_agent_2/config/settings.py`

Handles loading, validation, and persistence of configuration from `.env`.

### Constructor

```python
Config(env_path: str = "clio_agent_2/config/.env")
```

### Key Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `validate_api_keys` | `validate_api_keys() -> Dict[str, bool]` | Check which provider keys are configured |
| `to_dict` | `to_dict() -> Dict[str, Any]` | All settings as a dict (no secrets) |
| `save_settings` | `save_settings(updates: Dict[str, str]) -> None` | Persist setting changes to `.env` |
| `reload` | `reload() -> None` | Reload from `.env` |
| `get_api_key` | `get_api_key(provider: str) -> Optional[str]` | Get API key for a provider |

### Key Attributes

| Attribute | Default | Description |
|-----------|---------|-------------|
| `openai_api_key` | None | |
| `google_api_key` | None | |
| `anthropic_api_key` | None | |
| `default_llm_provider` | `"openai"` | |
| `current_model` | `""` | |
| `llm_settings_locked` | True | |
| `autonomous_mode` | True | |
| `thinking_interval` | 5.0 | |
| `context_log_max_lines` | 1000 | |

---

## 🛠️ `ToolRegistry`

**File:** `clio_agent_2/tools/tool_registry.py`

Central registry for all tools. Manages tool discovery, schema, and dispatch.

### Constructor

```python
ToolRegistry(
    context_log: ContextLog,
    search_api_key: Optional[str] = None,
    response_sink: Optional[Callable] = None,
)
```

### Key Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `register_tool` | `register_tool(tool: BaseTool)` | Register a new tool |
| `list_tools` | `list_tools() -> List[str]` | List available tool names |
| `get_tool` | `get_tool(name: str)` | Get a specific tool |
| `execute_tool` | `async execute_tool(name: str, arguments: dict) -> ToolResult` | Execute by name |

### Available Tools

| Tool Name | Aliases | Controller |
|-----------|---------|-----------|
| `read_file` | `path` | Reads a file with max_lines |
| `write_file` | `path` | Creates or overwrites a file |
| `append_file` | — | Appends content to a file |
| `edit_file` | — | Replaces old_str with new_str |
| `web_search` | — | Internet search |
| `fetch_url` | — | Fetches and parses URL content |
| `search_files` | `directory` | Finds files by glob pattern |
| `search_content` | — | Greps for text in files |
| `list_directory` | `path` | Lists directory contents |
| `shell_command` | `run_shell_command`, `execute_shell_command` | Runs arbitrary shell commands |
| `thinking` | `thought`, `text`, `content`, `note`, `message` | Records internal notes |
| `say` | `message`, `text`, `content` | Delivers user-facing messages |

---

## 🧭 Related Docs

- [Architecture Overview](ARCHITECTURE.md) — system design
- [Core Modules](CORE_MODULES.md) — deep dives into each core module
- [Tool System](tools/OVERVIEW.md) — tool registry internals
- [Interfaces](interfaces/OVERVIEW.md) — all interface implementations
