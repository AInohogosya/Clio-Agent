# Clio Agent 1 — Developer Onboarding Guide

> **Version:** 3.0.0 | **License:** MIT | **Python:** 3.8+

Welcome to the Clio Agent 1 project! This guide will help you understand the
architecture, set up your development environment, and start contributing
quickly — even if you've never seen this codebase before.

See also: `README.md` (user-facing docs), `AGENTS.md` (agent behavioral rules),
`ARCHITECTURE.md` (technical reference), `QUICKSTART.md` (5-minute setup),
`docs/config-reference.md` (full config reference), `docs/agent-loop.md`
(detailed loop explanation).

---

## Table of Contents

1. [What Is Clio Agent 1?](#1-what-is-clio-agent-1)
2. [Project Structure](#2-project-structure)
3. [Architecture](#3-architecture)
4. [Development Setup](#4-development-setup)
5. [Key Concepts & Glossary](#5-key-concepts--glossary)
6. [Agent Loop Deep Dive](#6-agent-loop-deep-dive)
7. [AI Provider System](#7-ai-provider-system)
8. [Tool System](#8-tool-system)
9. [Sub-Agent System](#9-sub-agent-system)
10. [Telegram & Discord](#10-telegram--discord)
11. [Resilience & Self-Healing](#11-resilience--self-healing)
12. [Testing](#12-testing)
13. [Common Tasks](#13-common-tasks)
14. [Troubleshooting](#14-troubleshooting)
15. [Contributing](#15-contributing)

---

## 1. What Is Clio Agent 1?

Clio Agent 1 is an **autonomous AI agent** that runs a perpetual think-execute
loop in your terminal:

- **Thinks** — sends context to an LLM, receives structured JSON commands
- **Executes** — runs shell commands, reads/writes files, searches code
- **Repeats** — continuously, until told to `sleep` or `exit`

Key features:

| Feature | Description |
|---|---|
| **Perpetual loop** | No "task completion" — runs until stopped |
| **Context survival** | Compressed state in `.context/` survives crashes, Ctrl+C, `kill -9` |
| **16 AI providers** | Ollama, OpenAI, Anthropic, Google, Groq, xAI, Meta, Mistral, etc. |
| **Provider fallback** | Circuit breaker + automatic failover between providers |
| **Self-healing** | Auto-fixes missing packages, permission errors, SSL issues, disk-full |
| **Telegram bot** | Remote control via Telegram messages |
| **Eternal Supervisor** | Watchdog process auto-restarts the agent on crash/hang |
| **Parallel execution** | `parallel_begin … parallel_end` batches concurrent commands |

---

## 2. Project Structure

```
Clio-Agent/
├── run.py                        # Main entry point (bootstrap, venv, signals)
├── agent_core.py                 # Thin backward-compat wrapper
├── install.sh                    # One-command installer
├── pyproject.toml                # Package config, dependencies, entry points
├── config.yaml                   # Local config (DO NOT COMMIT - has secrets)
├── config.yaml.example           # Config template (all options documented)
├── AGENTS.md                     # Agent behavioral rules (system prompt)
├── README.md                     # User-facing documentation
├── DEVELOPERS.md                 # <- You are here
├── ARCHITECTURE.md               # Technical architecture reference
├── QUICKSTART.md                 # 5-minute quick start guide
│
├── src/ai_agent/                 # Main Python package
│   ├── core_processing/          # Agent loop engine
│   │   ├── autonomous_loop_engine.py  # Main think-execute loop
│   │   ├── planner.py            # Builds prompts, calls LLM, parses JSON
│   │   ├── executor.py           # Executes parsed actions via tools
│   │   ├── loop_controller.py    # Log lifecycle, loop detection, Curiosity Fairy
│   │   ├── context_manager.py    # .context/ folder I/O for crash recovery
│   │   ├── agent_schema.py       # JSON schema for structured LLM output
│   │   └── terminal_history.py   # Persistent terminal history
│   ├── tools/                    # Agent tools (read, write, edit, bash, glob, grep)
│   ├── sub_agents/               # Sub-agent system (base, manager, registry, agents)
│   ├── utils/                    # Shared utilities (config, resilience, logging, etc.)
│   ├── platform_abstraction/     # Cross-platform detection
│   └── plugins/                  # Plugin system
│
├── external_integration/         # External service integrations
│   ├── model_runner.py           # Multi-provider LLM client
│   ├── telegram_bot.py           # Telegram bot manager
│   ├── discord_bot.py            # Discord bot manager
│   └── ...
│
├── peripherals/                  # Peripheral tools
│   ├── api/                      # Unified LLM API (16 provider adapters)
│   └── ...
│
├── gui/                          # PyQt6 GUI application
├── docker/                       # Docker configurations
├── tests/                        # Test suite (pytest)
├── examples/                     # Usage examples
└── docs/                         # Supplementary documentation
    ├── agent-loop.md             # Detailed agent loop explanation
    └── config-reference.md       # Complete config.yaml reference

---

## 3. Architecture

### 3.1 High-Level Flow

```
User / Telegram
    │
    ▼
run.py (Bootstrap: venv, deps, signals, config)
    │
    ▼
AutonomousLoopEngine
    ├── Planner (think)   → builds prompts, calls LLM, parses JSON
    ├── Executor (act)    → runs tools (read, write, bash, glob, grep)
    ├── Loop Controller   → log management, loop detection, Curiosity Fairy
    └── Context Manager   → reads/writes .context/ for crash recovery
    │
    ▼
AI Providers (16): Ollama, OpenAI, Anthropic, Google, Groq, xAI, Meta,
Mistral, Azure, Bedrock, Cohere, DeepSeek, Together, MiniMax, ZhipuAI, OpenRouter
```

### 3.2 The Think-Execute Loop

```
1. THINK
   ├── LoopController formats the prompt (goal + execution log + context)
   ├── Planner sends prompt to LLM via ModelRunner
   ├── LLM returns JSON: {"thinking": "...", "actions": [...]}
   └── Planner parses JSON into AgentPlan (list of AgentActions)

2. EXECUTE
   ├── Executor iterates through AgentActions
   ├── Each action maps to a Tool (read, write, edit, bash, glob, grep)
   ├── Results are appended to the execution log
   └── Special actions: telegram(), sleep, exit, parallel_begin/end

3. REPEAT
   ├── LoopController checks for loops (Curiosity Fairy)
   ├── Context is auto-saved every 60s and every 10 iterations
   └── Back to step 1

---

## 4. Development Setup

### 4.1 Prerequisites

- **Python 3.8+** (3.12+ recommended)
- **pip** and **Git**
- At least one AI provider API key (or Ollama for local models)

### 4.2 Quick Setup

```bash
git clone https://github.com/AInohogosya/Clio-Agent-1.git
cd Clio-Agent-1

# Option A: Automated installer
bash install.sh

# Option B: Manual setup
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -e ".[all]"         # Install with all optional dependencies
cp config.yaml.example config.yaml
# Edit config.yaml with your API keys
```

### 4.3 Configuring API Keys

Edit `config.yaml` (never commit — it's in `.gitignore`):

```yaml
api:
  preferred_provider: "openrouter"
  api_keys:
    openrouter: "sk-or-v1-..."
    openai: "sk-..."

---

## 5. Key Concepts & Glossary

| Term | Definition |
|---|---|
| **Agent Loop** | The perpetual think-execute cycle that is the core of Clio |
| **Planner** | The "thinking" component — builds prompts, calls LLM, parses response |
| **Executor** | The "acting" component — runs tools based on the plan |
| **LoopController** | Manages the execution log, detects loops, triggers Curiosity Fairy |
| **Curiosity Fairy** | Anti-loop mechanism that suggests new actions when the agent is stuck |
| **Context Manager** | Reads/writes `.context/` folder for crash recovery |
| **ModelRunner** | Multi-provider LLM client that routes requests to the right API |
| **AgentPlan** | Structured JSON output from the LLM (thinking + list of actions) |
| **AgentAction** | A single action in a plan (read, write, bash, telegram, sleep, etc.) |
| **Tool** | An implementation of a specific capability (file read, bash, grep, etc.) |
| **Sub-Agent** | A specialized agent spawned by the main agent for parallel work |
| **Sleep** | Context compression + process restart (saves state, rebuilds env) |
| **Exit** | Graceful shutdown with context save |
| **Heartbeat** | Periodic signal written to disk for watchdog monitoring |
| **Circuit Breaker** | Pattern that stops hitting a failing provider after N consecutive failures |
| **Eternal Supervisor** | Watchdog process that auto-restarts the agent on crash/hang |
| **Parallel Block** | `parallel_begin … parallel_end` — executes multiple commands concurrently |

---

## 6. Agent Loop Deep Dive

Each iteration of the main loop:

1. **CHECK CANCEL** — abort if cancel_event is set
2. **HEARTBEAT** — write to `.context/` for watchdog monitoring
3. **THINK (Planner)** — build system + user prompts, call LLM, parse JSON into AgentPlan
4. **EXECUTE (Executor)** — iterate AgentActions, map to tools, run, record results
5. **LOOP CONTROL** — check for repetitive patterns, compress log, inject warnings
6. **CHECK: sleep?** → compress context, save, restart
7. **CHECK: exit?** → save context, terminate
8. **REPEAT** from step 1

**Sleep/Resume Cycle:**
1. LLM compresses full log into structured summary
2. Save to `.context/sleep_state.json`
3. `os.execv()` restarts the process (same PID on Unix)

---

## 7. AI Provider System

### 7.1 Two-Layer Design

**Layer 1: `peripherals/api/` — Unified LLM API**
- `base.py` defines `BaseLLM` abstract class + `LLMFactory`
- Each provider has its own client file (e.g., `openai_client.py`)
- All clients implement: `generate()`, `generate_stream()`, `list_models()`, etc.

**Layer 2: `external_integration/model_runner.py` — Runtime Model Runner**
- `ModelRunner` is the main interface used by the agent
- Handles provider selection, failover, and error recovery

### 7.2 Supported Providers

Ollama (local), OpenAI, Anthropic, Google Gemini, Groq, xAI Grok, Meta Llama,
Mistral, Microsoft Azure, Amazon Bedrock, Cohere, DeepSeek, Together AI,
MiniMax, Zhipu AI, OpenRouter.

### 7.3 Provider Fallback

Default order: `openrouter → google → openai → anthropic → ollama`

Circuit breaker opens after 5 consecutive failures, stays open for 60s.

### 7.4 Adding a New Provider

1. Create `peripherals/api/new_provider_client.py` implementing `BaseLLM`
2. Register: `LLMFactory.register(ProviderType.NEW, NewClient)`
3. Add default model in `config.yaml.example`

---


---

## 10. Telegram & Discord

### 10.1 Telegram Mode

Set in config:
```yaml
telegram:
  enabled: true
  bot_token: "123456:ABC-DEF..."   # Or TELEGRAM_BOT_TOKEN env var
  authorized_users:
    - 123456789                    # Your Telegram user ID
```

The `TelegramBotManager` handles: receiving messages, queueing outbound messages,
anti-duplication, and auto-reconnection with exponential backoff.

### 10.2 Discord Mode

```yaml
discord:
  enabled: true
  bot_token: "YOUR_DISCORD_BOT_TOKEN"
  authorized_users:
    - 123456789
```

---

## 12. Testing

```bash
pytest tests/ -v                              # All tests
pytest tests/test_bootstrap.py -v             # Specific file
pytest tests/ --cov=src --cov-report=html     # With coverage
```

| Test File | What It Tests |
|---|---|
| `test_bootstrap.py` | Version parsing, venv, dependencies, config, platform |
| `test_bootstrap_fixes.py` | Bootstrap bug fixes |
| `test_recovery_system.py` | Error classification, resilience, provider fallback |
| `test_repetition_breaker.py` | Anti-loop mechanisms |
| `test_environment_setup_fixes.py` | Environment edge cases |
| `test_platform_compat.py` | Cross-platform compatibility |

Tests add `src/` to `sys.path` for imports. See existing tests for patterns.

---

## 13. Common Tasks

**Modify agent behavior:** Edit `AGENTS.md` (injected as system prompt)


---

## 14. Troubleshooting

| Problem | Solution |
|---|---|
| `No AI provider configured` | Run `Clio-Agent` without `--no-prompt` |
| `ModuleNotFoundError: ai_agent` | Ensure `src/` is on `sys.path` or run `pip install -e .` |
| `Ollama not detected` | Run `ollama serve && ollama pull llama3.2:3b` |
| `Agent doesn't resume` | Check `.context/`; use `exit` instead of kill |
| `Permission denied` | Agent auto-retries with sudo |
| `SSL errors` | `pip install --upgrade certifi` |
| `Rate limited` | Agent auto-switches providers |
| `Venv issues` | `rm -rf venv && Clio-Agent` (recreates automatically) |
| `Telegram not connecting` | Verify token format (`123456:ABC...`) and `enabled: true` |

---

## 15. Contributing

- Follow **PEP 8**, use **type hints**
- Run **black** for formatting before committing
- Run **mypy** for type checking
- Commit convention: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`

**IMPORTANT:**
- **Never commit `config.yaml`** — contains API keys
- **Never commit `.context/`** — runtime state
- **Never commit `venv/`** — virtual environment
- The `src/ai_agent/external_integration` symlink is auto-created — don't commit it

---

## Quick Reference

```bash
# Setup
git clone https://github.com/AInohogosya/Clio-Agent-1.git
cd Clio-Agent-1
python3 -m venv venv && source venv/bin/activate
pip install -e ".[all]"
cp config.yaml.example config.yaml

# Run
Clio-Agent "your task here"
Clio-Agent --supervisor
Clio-Agent --health-check

# Develop
pytest tests/ -v
black src/ tests/
mypy src/
```

Welcome to the team! Check existing tests for examples and explore the codebase.

**Debug:**
```bash
tail -f clio_agent.log
cat .context/sleep_state.json
Clio-Agent --health-check
```

**Docker:**
```bash
docker-compose --profile production up -d
```

**GUI:**
```bash
python gui/app.py
```

**Add a provider:** See Section 7.4

**Add a tool:** See Section 8.2

**Add a sub-agent:** See Section 9


## 11. Resilience & Self-Healing

### 11.1 Seven Layers

1. **Error Classification** — categorizes errors (rate limit, auth, timeout, network, resource)
2. **Self-Healing Commands** — auto-install packages, retry with sudo, update certs, cleanup disk
3. **Provider Fallback** — circuit breaker + automatic failover
4. **Eternal Supervisor** — watchdog with auto-restart and exponential backoff
5. **Watchdog** — lightweight process monitor
6. **Periodic Auto-Save** — every 60s and every 10 iterations
7. **Signal Handling** — SIGINT/SIGTERM trigger emergency context save

### 11.2 Error Categories

| Category | Examples | Retry? | Strategy |
|---|---|---|---|
| `RATE_LIMIT` | HTTP 429 | Yes | 30s backoff |
| `AUTHENTICATION` | HTTP 401/403 | No | Switch provider |
| `TIMEOUT` | Request timeout | Yes | 3s backoff |
| `TRANSIENT` | SSL, connection refused | Yes | 2-5s backoff |
| `EXTERNAL` | HTTP 5xx | Yes | 5s backoff |
| `RESOURCE` | Disk full, OOM | Yes | 30s backoff |
| `VALIDATION` | HTTP 400 | No | Fix input |
| `CONFIGURATION` | Model not found | No | Fix config |

## 8. Tool System

### 8.1 Available Tools

| Tool | Purpose | Key Arguments |
|---|---|---|
| `file_read` | Read file contents | `file_path`, `start_line`, `end_line` |
| `file_write` | Write/overwrite a file | `file_path`, `content` |
| `file_edit` | Find & replace in a file | `file_path`, `old_string`, `new_string` |
| `bash` | Execute shell commands | `command`, `timeout` |
| `glob` | Find files by pattern | `pattern`, `path` |
| `grep` | Search file contents | `pattern`, `path`, `file_extensions` |
| `todo_list` | Track tasks | `action`, `items` |
| `memo` | Store notes | `action`, `content` |
| `sub_agent` | Spawn a sub-agent | `agent_type`, `task` |

### 8.2 Adding a New Tool

1. Create `src/ai_agent/tools/my_tool.py` with a `ToolExecutor` subclass
2. Register in `src/ai_agent/tools/__init__.py` and `initialize_tool_registry()`

---

## 9. Sub-Agent System

Sub-agents run in parallel threads via ThreadPoolExecutor.

**Built-in:** research, review, architect

**Lifecycle:** `spawn() → initialize() → _run() → report() → cleanup()`

**To add:** Use `@sub_agent("name")` decorator on a `SubAgentBase` subclass.

4. On restart, inject compressed summary into prompt

**Loop Detection (Curiosity Fairy):**
- 3 repeats → soft suggestion to try something new
- 6 repeats → hard enforcement (Loop Breaker)
- 8 repeats → forced sleep

**Auto-Save:** Background thread writes state every 60 seconds and every 10
iterations. Survives `kill -9` and power loss.

See `docs/agent-loop.md` for full details.

```

Or use environment variables (higher priority):

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

See `docs/config-reference.md` for all configuration options.

### 4.4 Running the Agent

```bash
Clio-Agent --help                    # Show all options
Clio-Agent                           # Interactive setup
Clio-Agent "list all Python files"   # Run a task
Clio-Agent --supervisor              # With auto-restart on crash
Clio-Agent --health-check            # Diagnostics
```

### 4.5 Development Tools

```bash
pytest tests/ -v                              # Run tests
pytest tests/ --cov=src --cov-report=html     # With coverage
black src/ tests/                             # Format code
mypy src/                                     # Type check
flake8 src/ tests/                            # Lint
```

```

See `docs/agent-loop.md` for a detailed explanation of each phase.

### 3.3 Structured Output Schema

The LLM outputs JSON matching this schema (defined in `agent_schema.py`):

```json
{
  "thinking": "(optional) brief reasoning",
  "actions": [
    {"type": "read",    "args": {"path": "/file/path"}},
    {"type": "bash",    "args": {"command": "ls -la"}},
    {"type": "write",   "args": {"path": "/file", "content": "..."}},
    {"type": "edit",    "args": {"path": "/file", "old_string": "...", "new_string": "..."}},
    {"type": "glob",    "args": {"pattern": "**/*.py"}},
    {"type": "grep",    "args": {"pattern": "TODO"}},
    {"type": "telegram","args": {"message": "Hello!"}},
    {"type": "sleep",   "args": {}},
    {"type": "exit",    "args": {}},
    {"type": "parallel","args": {"actions": [...]}}
  ]
}
```

This structured approach eliminates regex parsing errors and makes the agent
fundamentally more reliable than free-form text parsing.

```
