# Tool System

The extensible tool registry — Clio-Agent-2's extensible "hands."

---

## 🛠️ Overview

The agent has no hardcoded tool calls — all actions go through `ToolRegistry`, which dynamically dispatches tools based on the model's JSON output.

### Available Tools

| Tool | Aliases | Purpose |
|------|---------|---------|
| `read_file` | `path` | Read file contents |
| `write_file` | `path` | Create/overwrite a file |
| `append_file` | — | Append content to a file |
| `edit_file` | — | Replace `old_str` with `new_str` |
| `web_search` | — | Internet search (via configured search API) |
| `fetch_url` | — | Fetch and read URL content |
| `search_files` | `directory` | Find files by glob pattern |
| `search_content` | — | Search text in files |
| `list_directory` | `path` | List directory contents |
| `shell_command` | `run_shell_command`, `execute_shell_command` | Run arbitrary shell commands |
| `thinking` | `thought`, `text`, `content`, `note`, `message` | Record internal monologue |
| `say` | `message`, `text`, `content` | Deliver user-facing output |

---

## 🏗️ Tool Registry

**File:** `clio_agent_2/tools/tool_registry.py`

### Registration Pattern

```python
class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something useful."

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "First parameter"},
            },
            "required": ["param1"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        result = do_work(arguments["param1"])
        return ToolResult(success=True, output=result)

# Register
registry.register_tool(MyTool())
```

### BaseTool Interface

Every tool must implement:

```python
class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def schema(self) -> Dict[str, Any]: ...

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any]) -> ToolResult: ...
```

### `ToolResult`

```python
@dataclass
class ToolResult:
    success: bool
    output: str = ""
    error: Optional[str] = None
```

---

## 📋 Existing Tool Implementations

### File Tools (`tool_file.py`)

Wraps `read_file`, `write_file`, `append_file`, `edit_file` with safe path resolution and argument aliases (e.g. `path` for `read_file`).

### Shell Tool (`tool_shell.py`)

Runs commands via `subprocess.run` with a configurable timeout, retries on transient timeout, and returns stdout/stderr. **No sandboxing.**

### Web Tools (`tool_web.py`)

`web_search` uses the configured search API. `fetch_url` uses `httpx` to fetch a URL and returns its text content (capped at ~5000 chars).

### Search Tools (`tool_search.py`)

`search_files` uses `glob`-style patterns. `search_content` uses grep-style text matching.

### Thinking & Say Tools

- `thinking` — records internal notes; outputs are NOT shown to the user
- `say` — delivers user-facing output through the agent's `response_sink`; this is the **only** way the agent produces user-visible output

---

## 🔒 Security Considerations

- `shell_command` has **no sandbox, allow-list, or confirmation prompt**
- The agent's system prompt explicitly instructs the model **never to ask for confirmation before using a tool**
- Tools receive `context_log` via the registry so they can record their own activity
- `edit_file` refuses ambiguous edits that match multiple locations in a file

---

## 🧭 Related Docs

- [Core Modules: ContextLog](CORE_MODULES.md#context-log-the-memory) — how tool calls are logged
- [API Reference: ToolRegistry](API.md#toolregistry) — programmatic usage
- [Safety & Best Practices](../../user/SAFETY.md) — risks of shell tool access
