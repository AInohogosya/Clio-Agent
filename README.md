# Clio-Agent-2

![Clio-Agent-2](docs/assets/thumbnail.png)

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/your-org/Clio-Agent-2/workflows/CI/badge.svg)](https://github.com/your-org/Clio-Agent-2/actions/workflows/ci.yml)
[![Code Style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Type Checked: MyPy](https://img.shields.io/badge/Type%20Checked-MyPy-blue)](https://mypy-lang.org/)
[![Security: Bandit](https://img.shields.io/badge/Security-Bandit-green)](https://bandit.readthedocs.io/)

> **A friendly, autonomous AI agent you run on your own machine.** It's designed to feel like a casual, close buddy — not just a tool — and it can read/write files, search the web, run shell commands, and — in *autonomous mode* — act and message you on its own (sometimes just to chat). It works from your **terminal (CLI)**, **Telegram**, or **Discord (Beta)**, and talks to many large-language-model providers (OpenAI, Google, Anthropic, OpenRouter, Grok, DeepSeek, Mistral, Groq, Perplexity, Together, Fireworks, NVIDIA NIM, Qwen, HuggingFace, DeepInfra, Ollama, and more).

> **⚠️ Note about this AI agent:** This AI agent does not necessarily respond immediately when you send a message; it may reply several hours later. It is a truly human-like AI agent, and it is not simply being lazy or doing nothing—rather, it is prioritizing its own tasks, which can cause responses to messages to be delayed.

Clio-Agent-2 is designed to be operated mostly through **one command** (`python3 run.py`), which sets everything up for you and then launches the agent. This README explains what it is, how to use it, what it can and cannot do, and how to fix common problems.

---

## 🛡️ Security & Safety First

**Please read this before running the agent.** Clio-Agent-2 is powerful, and a few of its behaviors are easy to underestimate.

1. **It runs shell commands on your computer.** The agent has a `shell_command` tool that executes arbitrary commands with the privileges of **the user that launched it**. There is **no sandbox, allow-list, or confirmation prompt** around this tool. Combined with web access and autonomous mode, a poorly-worded task or a malicious web page the agent fetches could cause it to run destructive commands (e.g., deleting files). **Run it on a dedicated/least-privilege account or in a container/VM you don't mind wiping, and think carefully about what you let it do.**
2. **Autonomous mode is ON by default.** Every launch starts a background "thinking" loop that can take actions and **proactively message you** without any input from you. This calls the model repeatedly (every few seconds by default), which **costs money and uses API quota**. Disable it if you want a purely on-demand assistant (set `AUTONOMOUS_MODE=false` in the config, or run `/stop` in the CLI).
3. **It needs an API key and internet.** You must supply at least one paid LLM provider key, and the machine needs internet access for setup and for the agent to work.

These are *features* (the agent is meant to act), but they are also the main risks. The rest of this document explains how to use them safely.

---

## 📋 Requirements

| Requirement | Notes |
|-------------|-------|
| **Python** | 3.8 or newer (tested on Python 3.14). The launcher uses only the standard library. |
| **Internet** | Needed to install dependencies and to call LLM / search APIs. |
| **At least one LLM API key** | OpenAI, Google (Gemini), Anthropic, OpenRouter, Grok, DeepSeek, or any other supported provider. |
| **OS** | Linux, macOS, or Windows. |

A virtual environment and all Python dependencies are created for you automatically on first run (see Quick Start). You don't need to install anything manually unless you want to (see [Troubleshooting](#-troubleshooting)).

---

## 🚀 Quick Start (one command)

From the project root, just run:

```bash
python3 run.py
```

That single command **automatically**:

1. Checks that your system is compatible (Python version, write permissions).
2. Creates a Python **virtual environment** (`.venv`) if one doesn't exist.
3. **Installs all dependencies** into that environment.
4. Creates the configuration file `clio_agent_2/config/.env` from a template.
5. On the **first run** (when no real API key + model are set), opens an interactive **configuration screen** and walks you through entering your keys and choosing a default model.
6. Launches the agent (CLI by default).

After the first run, `python3 run.py` just starts the agent — setup is skipped unless something is missing. Use `--no-setup` to skip the first-run prompt entirely and launch immediately.

---

## 📑 Table of Contents

- [Choosing an interface](#-choosing-an-interface)
- [Configuration](#-configuration)
- [How it works (mental model)](#-how-it-works-mental-model)
- [Features in depth](#-features-in-depth)
- [Command reference (CLI)](#-command-reference-cli)
- [Tips for working with the agent](#-tips-for-working-with-the-agent)
- [Troubleshooting](#-troubleshooting)
- [Limitations & known weaknesses](#-limitations--known-weaknesses)
- [Project structure](#-project-structure)
- [License](#-license)

---

## 🖥️ Choosing an interface

The *same* agent runs behind three different front-ends. Pick one with a flag:

```bash
python3 run.py             # Terminal (CLI) — the default
python3 run.py --telegram  # Telegram bot
python3 run.py --discord   # Discord bot (Beta)
python3 run.py --whatsapp  # WhatsApp Business API bot
python3 run.py --all       # Run every configured interface at once
```

| Interface | How you talk to it | Good for |
|-----------|-------------------|-----------|
| **CLI** | Type in your terminal. Rich, colored output. | Local use, development, debugging. |
| **Telegram** | Chat with the bot on your phone/desktop. | Mobile, chat-style interaction. |
| **Discord** | Slash commands + embeds in a server (Beta). | Communities / servers. |
| **WhatsApp** | Chat via WhatsApp Business API (webhook). | Business / customer messaging. |

> **Telegram / Discord / WhatsApp setup:** you must first create a bot and put its token in the config (see below). Until a real token is set, those interfaces will not connect. Discord and WhatsApp are marked **Beta** and expose fewer features than the CLI/Telegram.

---

## 🔧 Configuration

All secrets and settings live in **one file**: `clio_agent_2/config/.env`. A human-readable mirror (without secrets) is kept in sync at `clio_agent_2/config/config.yaml`.

> ⚠️ **There are two `config/` folders.** The one the program actually reads and writes is **`clio_agent_2/config/`**. A second, older `config/` directory also exists at the repository root — **do not edit files there**; changes there are ignored. If a setting "won't take effect," you most likely edited the wrong `.env`.

### Option A — the configuration screen (recommended)

The screen is a single navigable menu where you set **all** of the following at once: every LLM provider key, the web-search API key, the Telegram/Discord bot tokens, and the default model. A ✅/❌ status is shown after each change.

```bash
python3 run.py            # First run auto-opens the screen if nothing is configured
python3 run.py setup     # Open the screen manually, then exit (aliases: config / configure)
python3 run.py --setup  # Same thing, long-form flag
```

From inside a running CLI session you can also just type:

```
/configure
```

…which opens the **same** screen and applies changes live (new keys, tokens and model take effect immediately).

### Option B — one non-interactive command

Configure everything from a single command (handy for scripts / containers):

```bash
python3 run.py setup \
    --openai sk-... --provider openai --model gpt-4o \
    --telegram-token 123456:ABC
```

Per-provider flags exist for every built-in provider (e.g. `--google`, `--anthropic`, `--openrouter`, `--grok`, `--deepseek`, `--mistral`, `--groq`, `--perplexity`, `--together`, `--fireworks`, `--nim`, `--qwen`, `--huggingface`, `--deepinfra`), plus `--search`, `--telegram-token`, `--discord-token`, `--whatsapp-phone-number-id`, `--whatsapp-access-token`, `--whatsapp-app-secret`, `--whatsapp-webhook-verify-token`, `--whatsapp-webhook-url`, `--provider`, `--model`, and `--custom` (JSON, for OpenAI-compatible "Other" providers). Run `python3 run.py setup --help` for the full list.

### Option C — edit `.env` directly

Open `clio_agent_2/config/.env` in a text editor. The most common keys:

```env
# --- LLM provider keys (set at least one) ---
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
ANTHROPIC_API_KEY=...
OPENROUTER_API_KEY=...
GROK_API_KEY=...
DEEPSEEK_API_KEY=...
# ...and the other built-in providers (see table below)

# --- Web search (optional, for the web_search tool) ---
SEARCH_API_KEY=...          # e.g. a Serper / Bing-style search key

# --- Chat bots (optional) ---
TELEGRAM_BOT_TOKEN=123456:ABC
DISCORD_BOT_TOKEN=...

# --- WhatsApp Business API (optional) ---
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_APP_SECRET=...
WHATSAPP_WEBHOOK_VERIFY_TOKEN=...
WHATSAPP_WEBHOOK_URL=https://your-domain.com
WHATSAPP_WEBHOOK_PORT=8080

# --- Core behavior ---
DEFAULT_LLM_PROVIDER=openai   # which provider to use by default
DEFAULT_MODEL=gpt-4o          # MUST be set explicitly — there is no built-in default
CONTEXT_LOG_MAX_LINES=1000     # auto-compress the memory once it exceeds this
AUTONOMOUS_MODE=true           # start the self-driven loop on launch?
THINKING_INTERVAL=5.0         # seconds between autonomous thinking cycles
LLM_SETTINGS_LOCKED=true       # block model/provider changes unless you /llm_unlock
```

### Check your configuration

```bash
python3 run.py status   # or: python3 run.py --status
```

This prints which providers/tokens are configured and whether the agent is *ready* (a real API key **and** a real default model are both set).

> 💡 **Placeholders are treated as "not set."** Values like `your_openai_api_key_here` or `sk-your-actual-openai-api-key-here` are recognized as empty, so the first-run screen still appears when it should.

---

## 🧠 How it works (mental model)

It helps to think of Clio-Agent-2 as **one brain with four mouths**:

- **The brain (`ClioAgent`)** holds a *context log* (its memory) and decides what to do next by calling an LLM. It runs a **background loop** that keeps "thinking" on its own when autonomous mode is on.
- **The tools** are the hands the brain can use: read/write files, search the web, run shell commands, etc. The model emits a tool call (JSON) and the program executes it and feeds the result back.
- **The interfaces** (CLI / Telegram / Discord / WhatsApp) are just different ways to feed messages in and show replies out. They all drive the same brain.

A typical turn looks like: *you send a message → it's added to memory → the LLM replies (possibly with tool calls) → tools run → results go back into memory → the reply is shown to you.* In autonomous mode this loop also runs on a timer with no message from you.

---

## ✨ Features in depth

### 1. Multi-provider LLM routing

One agent, many models. Switch providers/models at any time (when unlocked).

| Provider | Env var for the key | Notes |
|----------|--------------------|-------|
| OpenAI | `OPENAI_API_KEY` | default model `gpt-4o` |
| Google (Gemini) | `GOOGLE_API_KEY` | default `gemini-1.5-pro` |
| Anthropic | `ANTHROPIC_API_KEY` | |
| OpenRouter | `OPENROUTER_API_KEY` | also sets `OPENROUTER_HTTP_REFERER` / `OPENROUTER_APP_NAME` |
| Grok (xAI) | `GROK_API_KEY` | |
| DeepSeek | `DEEPSEEK_API_KEY` | OpenAI-compatible |
| Mistral | `MISTRAL_API_KEY` | OpenAI-compatible |
| Groq | `GROQ_API_KEY` | OpenAI-compatible |
| Perplexity | `PERPLEXITY_API_KEY` | OpenAI-compatible |
| Together | `TOGETHER_API_KEY` | OpenAI-compatible |
| Fireworks | `FIREWORKS_API_KEY` | OpenAI-compatible |
| NVIDIA NIM | `NIM_API_KEY` | OpenAI-compatible |
| Qwen (Alibaba) | `QWEN_API_KEY` | OpenAI-compatible |
| HuggingFace | `HUGGINGFACE_API_KEY` | OpenAI-compatible |
| DeepInfra | `DEEPINFRA_API_KEY` | OpenAI-compatible |
| Ollama (local) | *(none — local server)* | OpenAI-compatible |
| **"Other" (custom)** | `CUSTOM_*_...` | any OpenAI-compatible endpoint (LM Studio, vLLM, LocalAI, enterprise gateways…) |

Use `/llm_providers`, `/llm_models [provider]`, `/llm_search <query>` to browse, and `/llm_default <provider> [model]` to pick the active one. See [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for custom-provider details.

### 2. Tools (what the agent can do)

| Tool(s) | What it does |
|----------|--------------|
| `read_file` | Read a file (alias `path`). |
| `write_file` | Create/overwrite a file (alias `path`). |
| `append_file` | Append to a file. |
| `edit_file` | Edit/replace content in a file. |
| `web_search` | Internet search via your search API (Serper/Bing-style). |
| `fetch_url` | Fetch and read the contents of a URL. |
| `search_files` | Find files by name under a directory (alias `directory`). |
| `search_content` | Grep for text inside files. |
| `list_directory` | List a directory's contents. |
| `shell_command` *(aliases: `run_shell_command`, `execute_shell_command`)* | **Run an arbitrary shell command on the host.** ⚠️ See the safety warning. |
| `thinking` | Record an internal note/reasoning into memory (agent's own "scratchpad"). |

Tool calls are parsed from the model's reply in several shapes — a single JSON object, a JSON array, or JSON objects embedded in free-form prose — so multi-step turns are executed rather than silently dropped.

### 3. Context & memory

The agent keeps a rolling **context log** of everything: your messages, its tool calls + results, its `thinking` notes, and system events.

- **Auto-compression:** once the log exceeds `CONTEXT_LOG_MAX_LINES` (default 1000), older entries are summarized by the LLM so the most important context fits in the model's window. This means **very old details can be lost** — see [Limitations](#-limitations--known-weaknesses).
- **Persistence:** the log is saved to a JSON file and **restored on restart**, so the agent remembers prior sessions. The latest entries are flushed to disk on a clean shutdown/restart (Ctrl+C, SIGTERM, `/exit`), and a `.bak` backup protects against a half-written file. `/clear_context` backs the context up first, and `/restore_context` recovers it — so context is never silently lost.

### 4. Autonomous loop

When on (the default), the agent runs a background loop that thinks on a timer (`THINKING_INTERVAL`, default **5 seconds**) and can take actions and message you **without any prompt**. If a cycle errors repeatedly, it backs off exponentially (doubling the wait, capped at 5 minutes) instead of hammering the API. Turn it off with `/stop` (or `AUTONOMOUS_MODE=false`); turn it back on with `/start`.

### 5. Retry / robustness

Transient failures are retried automatically (up to **5 attempts** with exponential back-off): LLM calls absorb network blips/timeouts/provider overload, and shell commands retry transient timeouts. **Permanent** errors — a missing model/provider or bad credentials — are *not* retried (they'd only waste time and money).

### 6. Meta-controller (stuck detection)

A lightweight watchdog tracks recent actions and flags when the agent appears **stuck repeating itself** (same action signature recurring in a small window), so it can be nudged onto a different path.

### 7. LLM settings lock (guardrail)

The active **provider and model are locked by default** (`LLM_SETTINGS_LOCKED=true`) so they can't change unexpectedly — e.g. via a prompt-injection picked up from a web page, or an autonomous self-edit. To change them deliberately:

```
/llm_unlock                 # allow changes for this session
/llm_default openai gpt-4o # or: /config model gpt-4o
/llm_lock                   # re-secure afterwards
```

The lock state is persisted to `.env`, so it survives restarts. (Note: this guard covers the *model*, not the *tools* — see the safety warning about `shell_command`.)

---

## 📟 Command reference (CLI)

Type these in the CLI (and most work via Telegram/Discord too). `/help` inside the agent lists them; `/help all` shows only the user-facing set.

| Command | What it does |
|----------|---------------|
| `/help` | Show all commands. `/help all` → user commands only. |
| `/settings` | View current settings. |
| `/reconfigure` | Interactive wizard to change every setting. |
| `/configure` | Open the config screen (keys, tokens, model); applies live. |
| `/config <setting> <value>` | Change one setting: `provider`, `model`, `autonomous_mode`, `thinking_interval`. |
| `/llm_providers` | List configured LLM providers. |
| `/llm_models [provider]` | List available models (all providers, or one). |
| `/llm_search <query>` | Search for a model by name. |
| `/llm_default <provider> [model]` | Set the active provider / model. |
| `/llm_lock` | Lock provider+model (block changes). |
| `/llm_unlock` | Unlock provider+model (allow changes). |
| `/api_keys` | Show which API keys are configured. |
| `/status` | Show agent status (running, context size, tools, providers). |
| `/context [count]` | Show the most recent context entries. |
| `/clear_context` | Wipe the context log (backed up; see `/restore_context`). |
| `/restore_context` | Restore the last cleared context from backup. |
| `/start` | Enable and start autonomous mode. |
| `/stop` | Disable autonomous mode. |
| `/exit` or `/quit` | Quit the CLI. |
| `/think <thought>` | (Agent-internal) record a reasoning note. |
| `/models`, `/search_models` | (Deprecated) use `/llm_models` / `/llm_search`. |

> The agent also understands **normal messages** — just type your request and it will reply (and may call tools). There is no "send" button quirk; in the CLI you type a line and press Enter.

---

## 💡 Tips for working with the agent

A few habits make collaboration go better:

- **Give it a clear goal, then check in.** The agent is built to work autonomously. State the outcome you want, then periodically ask `/status` or just message it "what have you done so far?" — it can go quiet while working.
- **Restate important context.** Like any LLM-based agent, it can "forget" details from much earlier in a long session (especially after context compression). If something agreed earlier matters, say it again rather than assuming it remembers.
- **It's available any time.** You can assign work or ask questions 24/7; there are no "business hours."
- **It can message you first.** In autonomous mode it may proactively report progress or ask questions — that's expected, not a bug.
- **It's designed to be friendly.** It's built to feel like a casual companion, so in autonomous mode it may also just check in, share something interesting, or say hi — that's by design, not a glitch.
- **Keep the model lock on** unless you're deliberately switching models; it protects you from unexpected provider/model changes.
- **Review autonomous actions** (especially shell commands) if you've given it broad permissions — see the safety warning at the top.

---

## 🛠️ Troubleshooting

### The first-run setup screen never appeared
You already have a real API key **and** a real default model set, so the agent is considered "ready" and skips setup. Open the screen manually:
```bash
python3 run.py setup      # or type /configure inside the CLI
```

### "No LLM providers configured" / model calls fail
Your key is still a **placeholder** (e.g. `your_openai_api_key_here`) or `DEFAULT_MODEL` is empty. Set a real key + model via `/configure` or:
```bash
python3 run.py setup --openai sk-... --provider openai --model gpt-4o
```

### The autonomous loop doesn't start
There is **no built-in default model** — you must set `DEFAULT_MODEL` explicitly. If it's missing, the agent runs but the thinking loop won't start and you'll see *"no LLM model is configured."* Fix with `/llm_default <provider> <model>` or set `DEFAULT_MODEL` in `.env`.

### Telegram / Discord won't connect
The token in `.env` is still the placeholder (`your_telegram_bot_token_here` / `your_discord_bot_token_here`). Set a real one:
```bash
python3 run.py setup --telegram-token 123456:ABC
python3 run.py setup --discord-token <token>
```
(Discord is **Beta** and supports fewer features than the CLI/Telegram.)

### I edited `.env` but nothing changed
You probably edited the **wrong** `config/` folder. The live one is **`clio_agent_2/config/.env`** — *not* the `config/` directory at the repository root (that copy is ignored). Also: changes made outside the config screen take effect after a restart (or use `/configure` for live updates).

### Virtual-environment creation failed
The launcher creates `.venv` automatically. If it can't, install the OS package and retry, or set one up by hand:
```bash
# Debian/Ubuntu:
sudo apt-get install python3-venv python3-pip
# Then, from the project root:
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r clio_agent_2/requirements.txt
python3 run.py --no-setup
```

### Dependency installation problems
```bash
pip install --upgrade pip
pip install -r clio_agent_2/requirements.txt --no-cache-dir
```

### "Python version too old"
Clio-Agent-2 needs **Python 3.8+**. Upgrade Python, then re-run `python3 run.py`. (The bundled setup was verified on Python 3.14; some behaviors rely on modern `asyncio`, so a current 3.x is recommended.)

### A shell command "timed out"
Commands are killed and retried up to 5 times on a *transient* timeout. If it still fails, the command genuinely exceeded the timeout — run it manually or raise the `timeout` argument the agent passes to `shell_command`.

### Context / memory seems wrong or empty
- Run `/context` to inspect recent entries.
- Auto-compression summarizes old entries past `CONTEXT_LOG_MAX_LINES`; old specifics may be gone (expected).
- `/clear_context` wipes the log, but it is **backed up** first — use `/restore_context` to recover it.

---

## ⚠️ Limitations & known weaknesses

This project is a working autonomous agent, not a hardened product. Be aware of:

1. **No sandbox around shell commands.** `shell_command` runs anything, as you, with no allow-list or confirmation. This is the single biggest risk — see the [safety warning](#-security--safety-first). Prefer a container/VM or a least-privilege account.
2. **Autonomous mode is on by default** and calls the model every few seconds, spending API quota/money and acting without your input. Disable it for an on-demand assistant.
3. **No default model.** You *must* set `DEFAULT_MODEL` or the thinking loop won't start.
4. **Memory is lossy.** Context is compressed (summarized) once it grows, so the agent can "forget" details from much earlier. The log is **durably persisted** and restored on restart (flushed on clean shutdown, with a `.bak` fallback if the file is corrupt); the only residual risk is a hard crash within ~10 entries of the last write, and `/clear_context` is now recoverable via `/restore_context`.
5. **The LLM lock guards the model, not the tools.** Provider/model changes are blocked by default (good), but tool actions — including `shell_command` — are not similarly gated.
6. **Stuck/repetition detection is basic.** It only catches an *exact* action signature repeating within a small recent window, so varied or long-pause loops may go unnoticed.
7. **Compression failures are swallowed.** If the LLM summarization errors, the agent logs *"Compression failed"* and continues with uncompressed context rather than halting.
8. **Discord is Beta** and exposes fewer features than the CLI/Telegram.
9. **Needs internet + a paid API key** to do anything useful; it can't run fully offline (except against a local Ollama/OpenAI-compatible server you host).
10. **Two `config/` folders can confuse you** (root vs `clio_agent_2/config`). Only the latter is used.
11. **Prompt-injection exposure.** Because the agent can fetch web pages and run commands, malicious content it reads could try to steer its actions. The model lock limits *model* switching, but not tool use.

---

## 📂 Project structure

```
Sora/
├── run.py                  # The ONLY command you run (re-launches main.py)
├── README.md               # This file
├── LICENSE                 # MIT License
├── CONTRIBUTING.md         # Contribution guidelines
├── CODE_OF_CONDUCT.md      # Community standards
├── SECURITY.md             # Security policy
├── CHANGELOG.md            # Release history
├── pyproject.toml          # Modern Python packaging config
├── package.json            # Node.js dependencies (for WhatsApp service)
├── nimotron.py             # NVIDIA Nimotron CLI (standalone)
├── tiny_agent.py           # Minimal agent example
├── whatsapp_service.js     # WhatsApp webhook server (Node.js)
├── TINYAGENT_README.md     # Tiny agent documentation
├── WHATSAPP_README.md      # WhatsApp setup documentation
├── tests/                  # Test suite
├── docs/
│   ├── README.md           # Documentation index
│   ├── CONFIGURATION.md    # Full configuration reference
│   ├── architecture/       # Architecture docs
│   ├── configuration/      # Configuration docs
│   ├── core/               # Core module docs
│   ├── development/        # Development guides
│   ├── interfaces/         # Interface docs
│   ├── operations/         # Operations docs
│   ├── tools/              # Tool docs
│   └── usage/              # Usage guides
├── config/                 # ⚠️ stray/legacy copy — NOT used; edit clio_agent_2/config instead
└── clio_agent_2/
    ├── README.md           # Package-specific readme
    ├── __init__.py         # Package init
    ├── apply_fixes.py      # Auto-fix utilities
    ├── main.py             # Real entry point: auto-setup, venv, deps, launch
    ├── requirements.txt    # Python dependencies
    ├── meta_controller.py  # Stuck-detection watchdog
    ├── config/
    │   ├── __init__.py
    │   ├── .env            # ★ CANONICAL config (keys, model, tokens)
    │   ├── .env.example    # Template copied on first run
    │   ├── config.yaml     # Readable mirror (no secrets)
    │   ├── settings.py     # Config loading / persistence
    │   └── setup_env.py    # Interactive setup wizard
    ├── core/
    │   ├── __init__.py
    │   ├── agent.py        # The brain: message handling + autonomous loop
    │   ├── context_manager.py  # Context log + compression + persistence
    │   ├── llm_router.py   # Multi-provider LLM routing
    │   ├── retry.py        # Generic retry-with-backoff helper
    │   └── token_budget.py # Token budget management
    ├── interfaces/
    │   ├── __init__.py
    │   ├── cli.py          # Terminal interface
    │   ├── telegram.py     # Telegram bot
    │   ├── discord.py      # Discord bot (Beta)
    │   └── whatsapp.py     # WhatsApp Business API (Beta)
    ├── tools/
    │   ├── __init__.py
    │   └── tool_registry.py  # File/web/search/shell/thinking tools
    └── utils/
        ├── __init__.py
        └── instance_lock.py # Single-instance locks
```

For the complete settings list and custom-provider setup, see [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.