# Clio Agent 1

<img width="1280" height="720" alt="Clio Agent 1" src="https://github.com/user-attachments/assets/eae19b90-fe77-4d9c-9ebb-5a3f79566fd1" />


**An autonomous AI agent that thinks and acts continuously in your terminal — and lives on through crashes.**

Clio Agent 1 (codename VEXIS-CLI) runs a perpetual think-execute loop: it decides what to do, runs shell commands on your machine, evaluates the results, and then does it again. And again. It never "completes" — it works until you stop it, and even then it saves enough context to pick up exactly where it left off.

Two ways to talk to it: directly in the terminal, or remotely via **Telegram bot** (send instructions from your phone).

---

## Table of Contents

1. [What Makes Clio Agent 1 Different](#what-makes-clio-agent-1-different)
2. [Quick Start](#quick-start)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Architecture Deep Dive](#architecture-deep-dive)
6. [Configuration (`config.yaml`)](#configuration-configyaml)
7. [Running the Agent](#running-the-agent)
8. [AI Providers](#ai-providers)
9. [Command Reference](#command-reference)
10. [Resilience Systems](#resilience-systems)
11. [Telegram Mode](#telegram-mode)
12. [Docker Deployments](#docker-deployments)
13. [Troubleshooting](#troubleshooting)

---

## What Makes Clio Agent 1 Different

| Feature | What it means |
|---|---|
| **Perpetual autonomous loop** | No "task completion" concept. The agent starts thinking the moment it launches and keeps going until the process is killed or it issues `sleep`/`exit`. |
| **Context survival across restarts** | Ctrl+C, crashes, power loss — compressed execution state is saved to `.context/`. On restart the agent reads it back and resumes mid-work. |
| **LLM-powered context compression** | When the execution log grows large, the agent issues a `sleep` command: an LLM compresses the full log into a structured summary (Work in Progress, File Changes, Errors, Next Action, Environment), saves it, and restarts the process — with a `git pull` in between. |
| **Periodic auto-save** | A background thread flushes context to disk every 60 seconds and every 10 iterations, so even `kill -9` leaves recoverable state. |
| **Provider fallback with circuit breaker** | If a cloud provider fails (rate limit, auth error, timeout), the engine automatically switches to another available provider. Circuit breakers prevent cascading failures. |
| **Self-healing command execution** | Failed commands are auto-classified and fixed: missing packages are installed, `permission denied` triggers `sudo` retry, SSL errors trigger cert updates, disk-full triggers emergency cleanup. |
| **Parallel command batches** | `parallel_begin ... parallel_end` blocks execute multiple shell commands concurrently, with thinking/telegram running sequentially around the batch. |
| **Eternal Supervisor** | `--supervisor` flag spawns a watchdog process that monitors the agent and auto-restarts it on crash/hang with exponential backoff + jitter. |
| **Telegram-first design** | The agent communicates with the user via `telegram()` commands. It has a complete anti-duplication protocol to never send the same reply twice. |
| **16 AI providers** | Ollama (local), Google, OpenAI, Anthropic, xAI, Meta, Groq, DeepSeek, Mistral, Microsoft Azure, Amazon Bedrock, Cohere, Together AI, MiniMax, Zhipu AI, OpenRouter. |
| **Zero-config bootstrap** | First run creates a virtualenv, installs all dependencies, and prompts for provider/model selection — no manual setup. |
| **Real-time terminal log** | Agent thought/activity is streamed to stderr with color-coded timestamps, visible to the user at all times. |

---

## Quick Start

```bash
git clone https://github.com/AInohogosya/VEXIS-CLI.git
cd VEXIS-CLI
pip install -e .
```

After installation, invoke the agent from **any directory**:

```bash
Clio-Agent "list all files in the current directory"
```

Or use the lowercase alias:

```bash
clio-agent "list all files in the current directory"
```

On first run, `Clio-Agent` will:

1. Create a Python virtual environment (`venv/`)
2. Install all dependencies automatically
3. Restart itself inside the virtual environment
4. Show you an interactive menu to pick an AI provider and model
5. Launch the Telegram bot and enter the perpetual autonomous loop

---

## Prerequisites

| Requirement | Minimum |
|---|---|
| Python | 3.8+ |
| RAM | 4 GB (8 GB+ for local models) |
| Disk | 2 GB free (10 GB+ for local models) |
| OS | macOS, Linux, Windows |

**For cloud providers:** an API key from the provider's dashboard.

**For local models:** [Ollama](https://ollama.com/) installed and running (`ollama serve`).

---

## Installation

```bash
git clone https://github.com/AInohogosya/VEXIS-CLI.git
cd VEXIS-CLI
pip install -e .
```

> **⚠️ `pip install -e .` is required.** Without it, the `clio-agent` / `Clio-Agent` commands will not be available in your PATH. Cloning the repository alone does not register the entry point.

The `pip install -e .` step installs the `Clio-Agent` and `clio-agent` commands globally, so you can run the agent from **any directory**.

All agent dependencies (venv, packages) are handled automatically on first launch.

---

## Architecture Deep Dive

### The Perpetual Think-Execute Loop

```
┌─────────────────────────────────────────────────────────────┐
│                    main() — Clio-Agent                      │
│  venv bootstrap → dependency install → provider selection    │
│                          │                                   │
│                          ▼                                   │
│              AutonomousAIAgent                               │
│                          │                                   │
│                          ▼                                   │
│           AutonomousLoopEngine.execute_instruction()         │
│                          │                                   │
│              ┌───────────▼───────────┐                       │
│              │    ETERNAL LOOP       │                        │
│              │                       │                        │
│              │  1. THINK            │                        │
│              │     Build prompt with │                        │
│              │     execution log,    │                        │
│              │     context from      │                        │
│              │     .context/, system │                        │
│              │     instructions      │                        │
│              │           │           │                        │
│              │     Call LLM via      │                        │
│              │     ModelRunner       │                        │
│              │     (with fallback &  │                        │
│              │     circuit breaker)  │                        │
│              │           │           │                        │
│              │  2. PARSE            │                        │
│              │     Extract command() │                        │
│              │     thinking()        │                        │
│              │     telegram() sleep  │                        │
│              │     exit parallel     │                        │
│              │           │           │                        │
│              │  3. EXECUTE          │                        │
│              │     Run shell cmds    │                        │
│              │     Send Telegram     │                        │
│              │     Log everything    │                        │
│              │           │           │                        │
│              │  4. CHECK            │                        │
│              │     sleep? → compress │                        │
│              │       context,        │                        │
│              │       save state,     │                        │
│              │       restart process │                        │
│              │     exit? → save      │                        │
│              │       state, stop     │                        │
│              │     else → REPEAT     │                        │
│              └───────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

### Sleep / Context Compression Workflow

When the execution log exceeds 100 lines, the agent issues a `sleep` command. This triggers:

1. **Collect** auxiliary context (git diff, metadata, errors, log tail)
2. **Compress** via 3-level fallback:
   - Level 1: LLM compression with structured prompt (Work in Progress, File Changes, Errors, Next Action, Environment)
   - Level 2: Heuristic compression (structured sections from raw log)
   - Level 3: Raw log tail (last 5000 chars)
3. **Save** compressed state to `.context/sleep_state.json` and `.context/context_log.txt`
4. **Set** rebuild flag and restart the process with `os.execv` (replaces PID)
5. On restart: read compressed context, inject into first prompt, clear state files

### Persistent Context Files

| File | Purpose |
|---|---|
| `.context/sleep_state.json` | State saved on `sleep` command (includes compressed context, git diff, errors) |
| `.context/exit_state.json` | State saved on `exit` command or Ctrl+C signal |
| `.context/context_log.txt` | Plain-text compressed context (always mirrors latest state) |
| `.context/rebuild_required` | Flag file consumed on startup to trigger venv rebuild |
| `.context/watchdog_heartbeat.json` | Agent heartbeat (PID, iteration, timestamp) |
| `.context/supervisor_heartbeat.json` | Supervisor heartbeat (if running under `--supervisor`) |

---

## Configuration (`config.yaml`)

Created from interactive setup on first run. Git-ignored by default.

```yaml
# ── AI Provider & Model Settings ──────────────────────────────
api:
  preferred_provider: openrouter
  models:
    ollama: "llama3.2:3b"
    google: "gemini-3.1-pro-preview"
    openai: "gpt-4o"
    anthropic: "claude-3.5-sonnet-20241022"
    # ... 16 providers total
    openrouter: "openai/gpt-oss-120b"
  local_endpoint: "http://localhost:11434"
  timeout: 30
  max_retries: 3
  retry_delay: 1.0

# ── Execution Settings ────────────────────────────────────────
execution:
  mode: auto
  safety_mode: true
  dry_run: false
  verify_commands: true
  command_timeout: 1800
  task_timeout: 7200
  max_iterations: 500
  auto_recovery: true
  show_thought_log: true

# ── Telegram Bot Settings ─────────────────────────────────────
telegram:
  enabled: true
  bot_token: "YOUR_BOT_TOKEN"
  bot_username: "my_agent_bot"
  allowed_user_ids:
    - 123456789

# ── Security ──────────────────────────────────────────────────
security:
  enable_command_blocking: false
  enable_confirmation_prompts: false
  enable_sudo_warning: false
  enable_shell_pipe_warning: false
  enable_sandbox: true

# ── Performance & Cache ───────────────────────────────────────
performance:
  max_concurrent_tasks: 1
  memory_limit_mb: 1024

cache:
  enabled: true
  max_size: 1000
  ttl: 3600
  persist_to_disk: true

# ── Cost Management ───────────────────────────────────────────
cost:
  warning_threshold: 0.8
  critical_threshold: 0.95

# ── Logging ───────────────────────────────────────────────────
logging:
  level: INFO
  console: true
  json_format: false
```

The interactive setup (`--setting` or first run) shows a provider selection menu with arrow-key navigation, followed by a live model list fetched from the provider's API with context windows and pricing.

---

## Running the Agent

```bash
# Basic — starts Telegram bot mode with interactive setup
Clio-Agent

# Run with an initial instruction
Clio-Agent "set up a Python project with tests"

# Skip interactive prompts, use saved config
Clio-Agent --no-prompt

# Force reconfiguration of provider/model
Clio-Agent --setting

# Run under eternal supervisor (auto-restart on crash)
Clio-Agent --supervisor

# Enable enhanced self-healing
Clio-Agent --self-heal

# Self-diagnostic
Clio-Agent --health-check

# Environment check
Clio-Agent --check
Clio-Agent --fix

# Max iterations (0 = unlimited)
Clio-Agent --max-iterations 1000

# Debug mode (verbose logging)
Clio-Agent --debug
```

### Telegram Mode

```bash
# Configure in config.yaml:
#   telegram.enabled: true
#   telegram.bot_token: "YOUR_BOT_TOKEN"
#   telegram.allowed_user_ids: [123456789]

Clio-Agent  # auto-detects Telegram mode from config
```

Send instructions to your bot from Telegram — the agent executes them on your machine and replies back. Supports `/start`, `/restart`, `/help` commands. The bot uses message queuing with retry, and the agent has a full anti-duplication protocol to avoid double-replies.

---

## AI Providers

### Local (No API Key)

| Provider | Setup | Recommended Models |
|---|---|---|
| **Ollama** | [ollama.com](https://ollama.com/) → `ollama serve` | `llama3.2:3b`, `qwen3:8b`, `deepseek-r1:7b` |

### Cloud (API Key Required)

| Provider | Env Variable | Get Key From |
|---|---|---|
| Google Gemini | `GOOGLE_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com/api-keys) |
| Anthropic | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com/) |
| xAI | `XAI_API_KEY` | [x.ai](https://x.ai/) |
| Meta | `META_API_KEY` | [developers.meta.com](https://developers.meta.com/) |
| Groq | `GROQ_API_KEY` | [console.groq.com](https://console.groq.com/) |
| DeepSeek | `DEEPSEEK_API_KEY` | [platform.deepseek.com](https://platform.deepseek.com/) |
| Mistral | `MISTRAL_API_KEY` | [console.mistral.ai](https://console.mistral.ai/) |
| Microsoft Azure | `AZURE_OPENAI_API_KEY` | [portal.azure.com](https://portal.azure.com) |
| Amazon Bedrock | `AWS_ACCESS_KEY_ID` | [console.aws.amazon.com](https://console.aws.amazon.com/) |
| Cohere | `COHERE_API_KEY` | [dashboard.cohere.com](https://dashboard.cohere.com/) |
| Together AI | `TOGETHER_API_KEY` | [api.together.ai](https://api.together.ai/) |
| MiniMax | `MINIMAX_API_KEY` | [api.minimaxi.chat](https://api.minimaxi.chat/) |
| Zhipu AI | `ZHIPUAI_API_KEY` | [open.bigmodel.cn](https://open.bigmodel.cn/) |
| OpenRouter | `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) |

API keys are checked in this order: environment variables → saved settings → interactive prompt.

---

## Command Reference

The agent recognizes these commands in its thinking output:

| Command | Description |
|---|---|
| `command(<shell>)` | Execute a terminal command on the host machine |
| `thinking(<text>)` | Internal monologue (invisible to user — use sparingly) |
| `telegram(<msg>)` | **The only way** to send messages to the user in Telegram mode |
| `Telegram_log(<n>)` | Display last n messages from Telegram conversation history |
| `sleep` | Compress context, save state, rebuild env, restart process |
| `exit` | Save context and shut down without restart |
| `parallel_begin ... parallel_end` | Execute multiple `command()` calls concurrently |

The agent emits these automatically based on the situation. The user does not type them directly.

---

## Resilience Systems

Clio Agent 1 has multiple layers of self-healing:

### 1. Error Recovery (`resilience_engine.py`)
- Error classification (rate limit, auth, timeout, network, resource, validation)
- Exponential backoff with configurable factor
- Circuit breaker per provider (opens after 5 failures, 60s cooldown)
- Auto-retry with Telegram notification on high-severity errors

### 2. Self-Healing Commands (`ResilienceEngine.execute_with_healing()`)
- Missing command → auto-install package
- Permission denied → retry with sudo
- SSL error → update certifi
- Disk full → emergency cleanup
- Connection refused → check service status
- Network down → wait and retry

### 3. Provider Fallback (`provider_fallback.py`)
- Automatic failover when a provider returns auth/network errors
- Circuit breaker prevents hitting a broken provider repeatedly
- Fallback tries the next available provider automatically

### 4. Eternal Supervisor (`eternal_supervisor.py`)
- `--supervisor` spawns a separate watchdog process
- Monitors agent heartbeat (30s interval, 600s timeout)
- Auto-restart on crash/hang with exponential backoff + jitter
- Health checks every 5 minutes (disk, memory, process)
- Daily/hourly restart limits to prevent restart loops

### 5. Watchdog (`watchdog.py`)
- Lightweight process monitor that can run the agent in a child process
- Separate tool from the Eternal Supervisor; provides an alternative monitoring approach

### 6. Periodic Auto-Save
- Background thread writes `exit_state.json` + `context_log.txt` every 60 seconds
- Also writes every 10 iterations as safety net
- Survives `kill -9`, power loss, device reboot

### 7. Graceful Signal Handling
- SIGINT/SIGTERM trigger emergency context save via stack frame inspection
- Signal handlers walk the call stack to find the live agent instance
- Saves compressed context + execution log + git diff before terminating

---

## Docker Deployments

Pre-configured Docker environments are available for multiple platforms:

```
docker/
├── alpine/Dockerfile
├── rockylinux/Dockerfile
├── ubuntu/Dockerfile (+ entrypoint.sh)
├── windows/Dockerfile (+ entrypoint.ps1)
├── macos/Dockerfile
├── docker-compose.yml
└── README.md
```

---

## Troubleshooting

### "No AI provider configured"
Run without `--no-prompt` to get the interactive setup:
```bash
Clio-Agent
```

### Ollama not detected
```bash
ollama --version
ollama serve
ollama pull llama3.2:3b
```

### Virtual environment issues
```bash
rm -rf venv
Clio-Agent  # recreates automatically
```

### Agent doesn't resume after Ctrl+C
Check `.context/` for `exit_state.json` or `sleep_state.json`. Use the `exit` command from within the agent instead of killing the process.

### Environment health check
```bash
Clio-Agent --health-check
```

---

## License

MIT — see [peripherals/LICENSE](peripherals/LICENSE) for details.
