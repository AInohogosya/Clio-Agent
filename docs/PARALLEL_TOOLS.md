# Parallel Tool Execution

## Overview

The AI agent can execute multiple tool calls **concurrently** (in parallel) by wrapping them with `parallel_begin` and `parallel_end` markers. This speeds up independent operations — file reads, directory listings, network calls, etc. — that don't depend on each other's output.

## Syntax

### Basic Parallel Block

```
parallel_begin
command(first independent command)
command(second independent command)
command(third independent command)
parallel_end
```

### Mixed Tool Calls in Parallel Block

You can mix `command()` wrappers and direct tool calls:

```
parallel_begin
read(path="/tmp/file1.txt")
read(path="/tmp/file2.txt")
glob(pattern="src/**/*.py")
command(ls -la /var/log)
parallel_end
```

### Direct Tool Call Syntax

Instead of wrapping shell commands in `command()`, you can call tools directly:

| Tool | Syntax | Description |
|------|--------|-------------|
| `read` | `read(path="/path/to/file")` | Read file contents |
| `write` | `write(path="/path", content="text")` | Write content to file |
| `edit` | `edit(path="/path", old_string="a", new_string="b")` | Replace text in file |
| `glob` | `glob(pattern="**/*.py")` | Find files by pattern |
| `grep` | `grep(pattern="regex", path=".")` | Search file contents |
| `bash` | `bash(command="any shell cmd")` | Execute shell command |

## How It Works

1. The model emits a `parallel_begin` marker, followed by one or more tool calls, then a `parallel_end` marker.
2. The engine's `_parse_model_commands` collects all inner commands into a single `("parallel", json_batch)` tuple.
3. `_exec_parallel` runs all `command()` and direct tool call entries concurrently via `ThreadPoolExecutor` (max 8 workers by default).
4. `thinking()` and `telegram()` entries inside the block are executed **sequentially** — before or after the parallel batch — to keep message ordering deterministic.
5. Results are aggregated into a `ParallelResult` and appended to the execution log.

## Example: Sequential (Slow)

```
thinking(I need to check three directories)
command(ls -la /tmp)
command(ls -la /var/log)
command(ls -la /home)
```

These run one after another. Three round-trips through the terminal history system.

## Example: Parallel (Fast)

```
thinking(I need to check three directories — running them in parallel)
parallel_begin
command(ls -la /tmp)
command(ls -la /var/log)
command(ls -la /home)
parallel_end
```

These run concurrently. Total wall-clock time ≈ the slowest single command.

## Example: Mixed Direct Tool Calls + Commands

```
parallel_begin
read(path="/tmp/config.yaml")
read(path="/tmp/secrets.env")
glob(pattern="src/**/*.py")
grep(pattern="TODO", path="src/")
command(git status)
parallel_end
```

All five operations run concurrently.

## Error Handling

- If one command in the batch fails, the others still complete. Failures are reported individually.
- Invalid JSON in the batch argument is caught and logged as an error.
- If the JSON parse fails entirely, an error is appended to the execution log.
- The `cancel_event` is checked before and after the batch.

## Key Files

| File | Role |
|------|------|
| `src/ai_agent/tools/base.py` | `ParallelTask`, `ParallelResult`, `ToolRegistry.execute_parallel()` |
| `src/ai_agent/core_processing/autonomous_loop_engine.py` | `_parse_model_commands` (parallel blocks + direct tool calls), `_exec_parallel()`, `_exec_tool_call()`, `_build_tool_input()` |
| `src/ai_agent/external_integration/model_runner.py` | System prompt & autonomous loop template with parallel syntax + direct tool call syntax |
| `AGENTS.md` | High-level operational guidelines for the AI agent (concurrent tool use policy) |

## AI Agent Guidance

The system prompt in `model_runner.py` explicitly instructs the AI to:

- **Always parallelize independent operations** (file reads, directory listings, network calls)
- **Only serialize dependent operations** (e.g., `cd` before `ls`, `mkdir` before `cp`)
- Use `parallel_begin`/`parallel_end` blocks for concurrent execution
- Prefer direct tool calls (`read`/`write`/`edit`/`glob`/`grep`/`bash`) over `command()` for file operations
- One command failing in a parallel batch does NOT stop the others
- Multiple invocation formats are accepted (code block, bare, bullet list)

The `AGENTS.md` file at the project root provides additional high-level guidance reinforcing these rules.

## Related Types

```python
# base.py
@dataclass
class ParallelTask:
    tool_name: str          # e.g. "bash", "read", "glob"
    input: ToolInput        # the tool input
    label: str = ""         # optional log label

@dataclass
class ParallelResult:
    results: List[Tuple[ParallelTask, ToolResult]]
    total_duration_ms: float
    success_count: int
    fail_count: int
    parallel: bool = True
```

## Implementation Notes

- **Thread safety**: Each tool call is independent, so `ThreadPoolExecutor` is safe here.
- **Ordering guarantee**: Commands outside `parallel_begin/end` remain sequential. The batch is atomic from the outer loop's perspective.
- **Max workers**: `min(num_tasks, 8)` by default. Configurable via `max_workers` parameter (0 = auto).
- **Tool registry**: The loop tool registry (`_get_or_create_loop_tool_registry`) registers ALL available tools (bash, read, write, edit, glob, grep, todo, memo), not just BashTool.
- **Direct tool calls**: Outside of parallel blocks, direct tool calls like `read(path="...")` are also supported and dispatched through the same registry via `_exec_tool_call()`.
