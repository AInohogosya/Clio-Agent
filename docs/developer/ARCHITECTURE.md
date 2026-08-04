# Architecture Overview

Comprehensive guide to Clio-Agent-2's system design, module relationships, and data flow.

---

## 🏗️ High-Level Design

Clio-Agent-2 is structured as a **single agent with four interfaces** — one brain, multiple front-ends.

```
┌──────────────────────────────────────────┐
│              ClioAgent (the brain)        │
│  ┌────────────┐  ┌──────────────────┐   │
│  │ ContextLog  │  │ ToolRegistry      │   │
│  │ (memory)    │  │ (hands)           │   │
│  └────────────┘  └──────────────────┘   │
│         ↓                  ↓              │
│     LLMRouter  ←── tool results ──→ say  │
└──────────────────────────────────────────┘
     ↑     ↑     ↑     ↑
  CLI  Telegram Discord WhatsApp
  (interfaces/)
```

### The Brain: `ClioAgent`

Located at `clio_agent_2/core/agent.py`.

Responsibilities:
- Manages context/memory via `ContextLog`
- Drives multi-turn LLM tool-execution loop
- Handles autonomous mode scheduling
- Manages slash commands
- Circuit-breaker and stuck-detection

### The Hands: `ToolRegistry`

Located at `clio_agent_2/tools/tool_registry.py`.

Responsibilities:
- Declares all available tools (name + schema + implementation)
- Dispatches tool execution
- Dependency injection — receives `context_log`, `search_api_key`, `response_sink`

### The Memory: `ContextLog`

Located at `clio_agent_2/core/context_manager.py`.

Responsibilities:
- Records every message, tool call, tool result, and thinking note
- Maintains a hot sliding window + cold archive
- Handles compression when the log exceeds `CONTEXT_LOG_MAX_LINES`
- Persists to `data/context.json` with `.bak` backup
- Archives compressed entries to `data/context_archive.jsonl`

### The Connector: `LLMRouter`

Located at `clio_agent_2/core/llm_router.py`.

Responsibilities:
- Routes chat requests to configured LLM providers
- Manages provider selection and model listing
- Enforces LLM settings lock
- Handles 15+ built-in providers + custom "Other" providers

---

## 📁 Module Dependency Map

```
run.py
  └── clio_agent_2/main.py (entry point)
        ├── core/agent.py          [ClioAgent]
        │     ├── core/context_manager.py  [ContextLog]
        │     │     └── core/llm_router.py  [LLMRouter]
        │     │           └── core/token_budget.py  [TokenBudget]
        │     │                 └── core/retry.py  [RetryProtocol]
        │     └── tools/tool_registry.py   [ToolRegistry]
        │           ├── core/llm_router.py  [LLMRouter — called on tool execution]
        │           └── core/agent.py       [send_response callback]
        ├── interfaces/
        │     ├── cli.py           [CLIInterface]
        │     ├── telegram.py      [TelegramInterface]
        │     ├── discord.py       [DiscordInterface]
        │     └── whatsapp.py      [WhatsAppInterface]
        │           └── tools/tool_registry.py  (shares same ToolRegistry)
        └── config/
              ├── settings.py      [Config]
              └── setup_env.py     [interactive_setup]
```

---

## 🔄 Data Flow: A Single Turn

```
User message
    ├─► added to ContextLog (user message entry)
    ├─► _build_context_messages()
    │       ├─► _system_block()    ← BASE_SYSTEM_PROMPT + rolling summary
    │       ├─► context_log.get_entries_as_messages()  ← hot window
    │       └─► + new user turn
    └─► _run_agent_turn(messages)
            ├─► llm_router.chat(messages)  ← LLM call
            ├─► _parse_tool_calls(response)  ← extract JSON tool calls
            │
            ├─► For each tool call:
            │       tool_registry.execute_tool(name, args)
            │       ├─► say tool → calls send_response(message)
            │       │       └─► all response_callbacks fired
            │       ├─► read_file → reads file, returns content
            │       ├─► shell_command → executes in subprocess
            │       └─► ... (other tools)
            │
            ├─► tool results → appended as "user" message
            ├─► llm_router.chat(messages)  ← follow-up LLM call
            └─► repeat until no more tool calls or MAX_TOOL_ITERATIONS
```

---

## 🔄 Data Flow: Autonomous Loop

```
start_autonomous_loop()
    └─► asyncio.create_task(run_autonomous_loop())
            ├─► autonomous_think()
            │       └─► same _run_agent_turn() loop but with internal prompt
            │           └─► agent may: call tools, message user via /say
            │
            ├─► success: sleep(thinking_interval), repeat
            ├─► failure (consecutive): increment counter
            │       ├─► < 5 failures: sleep(backoff), retry
            │       └─► == 5 failures: TRIP CIRCUIT BREAKER
            │           ├─► circuit_open = True
            │           ├─► is_running = False
            │           ├─► send_response(alert)  ← notify operator
            │           └─► loop exits
            └─► /resume: resets circuit, restarts loop
```

---

## 📦 Package Layout

```
clio_agent_2/
├── __init__.py
├── main.py              # Entry point: venv setup, dependency checks, CLI/Telegram launch
│
├── config/
│   ├── __init__.py
│   ├── .env             # ⭐ Secrets and settings (CANONICAL)
│   ├── .env.example     # Template
│   ├── config.yaml      # Human-readable mirror
│   ├── settings.py      # Config class: loading, validation, persistence
│   └── setup_env.py     # Interactive configuration wizard
│
├── core/
│   ├── __init__.py
│   ├── agent.py         # ClioAgent class: brain, message loop, autonomous loop, commands
│   ├── context_manager.py  # ContextLog: memory, compression, persistence
│   ├── llm_router.py    # LLMRouter: 15+ providers, custom providers, lock
│   ├── retry.py         # Async retry with exponential backoff
│   └── token_budget.py  # Token estimation via tiktoken
│
├── interfaces/
│   ├── __init__.py
│   ├── cli.py           # Terminal TUI (prompt_toolkit + rich)
│   ├── telegram.py      # Telegram bot (python-telegram-bot, polling)
│   ├── discord.py       # Discord bot (discord.py, slash commands)
│   └── whatsapp.py      # WhatsApp Business API (pywa, webhooks)
│
├── tools/
│   ├── __init__.py
│   └── tool_registry.py # All tool implementations + registry
│
├── utils/
│   ├── __init__.py
│   └── instance_lock.py # PID-file single-instance lock
│
├── requirements.txt
└── data/                # Runtime data
    ├── context.json     # Persisted agent memory
    └── context.json.bak # Memory backup
```

---

## ⚙️ Key Design Patterns

### Dependency Injection

`ToolRegistry` receives its dependencies rather than reaching into globals:

```python
tool_registry = ToolRegistry(
    context_log=context_log,
    search_api_key=config.search_api_key,
    response_sink=agent.send_response,
)
```

### Graceful Degradation

`main.py` wraps every import in try/except and falls back to minimal stubs, so the application always launches — even if half the dependencies are broken.

### Circuit Breaker

The autonomous loop pauses after 5 consecutive failures instead of hammering a broken endpoint. Only an explicit operator action (`/resume`) can restart it. Context is preserved throughout.

### Token Budget

The agent uses `tiktoken` for accurate token counting, with a 4-char/token fallback. Context calls are capped at `MAX_CONTEXT_TOKENS` (8000) with half the budget reserved for the hot window.

---

## 🔑 Key Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `MAX_TOOL_ITERATIONS` | 5 | `agent.py` | Max consecutive tool rounds per turn |
| `CIRCUIT_BREAKER_THRESHOLD` | 5 | `agent.py` | Consecutive LLM failures before pause |
| `MAX_CONTEXT_TOKENS` | 8000 | `agent.py` | Total context budget per LLM call |
| `DEFAULT_CONTEXT_WINDOW_SIZE` | 50 | `agent.py` | Hot rolling window entries |
| `COLD_ARCHIVE_BATCH` | 25 | `agent.py` | Entries per compression batch |
| `MESSAGE_PROCESS_TIMEOUT` | 3600s | `agent.py` | Per-message timeout |
| `THINKING_INTERVAL_DEFAULT` | 5.0s | `agent.py` | Autonomous cycle interval |
| `CONTEXT_LOG_MAX_LINES` | 1000 | `agent.py` | Auto-compression threshold |

---

## 🧭 Related Docs

- [API Reference](API.md) — Python API for embedding or extending
- [Core Modules](CORE_MODULES.md) — Deep dives into each core module
- [Tool System](tools/OVERVIEW.md) — The extensible tool registry
- [Interfaces](interfaces/OVERVIEW.md) — All four interface implementations
