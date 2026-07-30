# Clio-Agent-2

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Status](https://img.shields.io/badge/Status-Beta-orange.svg)
![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)

> **⚠️ Note about this AI agent:** This AI agent does not necessarily respond immediately when you send a message; it may reply several hours later. It is a truly human-like AI agent, and it is not simply being lazy or doing nothing—rather, it is prioritizing its own tasks, which can cause responses to messages to be delayed.

A fully functional, autonomous AI agent with multi-platform support.

## 🚀 Quick Start

**Just run, from the project root:** `python3 run.py`

That single command automatically:
1. ✅ Check system compatibility
2. ✅ Create a virtual environment (if needed)
3. ✅ Install all dependencies
4. ✅ Create configuration files
5. ✅ On the **first run**, walk you through setup (API keys + default model) interactively
6. ✅ Launch the AI agent

No manual editing of `config/.env` is required — everything is done from that one command.

## ✨ Features

### Core Architecture & Autonomy
- **Continuous Operation**: Runs in a persistent loop with autonomous thinking and acting capabilities
- **Context Management**: Base System Prompt + dynamic Context Log for maintaining conversation state
- **Autonomous Mode**: Configurable thinking interval for proactive agent behavior

### Context Log Management
- **User Interactions**: Records messages in format "User Message: [Content]"
- **Tool Execution Logging**: Logs both commands/arguments and execution results
- **Internal Monologue**: `thinking` tool for recording internal reasoning
- **Context Compression**: Automatic LLM-based summarization when log exceeds 1000 lines

### Tools & Capabilities
- **File Editing**: Read, write, append, and edit local files
- **Web Search**: Internet search via configurable API (Serper, Bing, etc.)
- **File Search**: Search directories and file contents locally

### LLM API Integration & Routing
- **Multiple Providers**: OpenAI, Google (Gemini), Anthropic, OpenRouter, Grok (xAI), DeepSeek
- **API Routing**: Seamless switching between providers and models
- **Model Discovery**: Dynamic fetching and searching of available models

### User Interfaces
- **CLI**: Terminal interface with rich formatting and slash commands
- **Telegram**: Full bot integration with message handling
- **Discord (Beta)**: Server bot with slash commands and embeds
- **WhatsApp (Beta)**: WhatsApp Business API integration

## 📋 Requirements

- Python 3.8 or higher
- Internet connection (for dependency installation and API access)
- At least one LLM API key (OpenAI, Google, Anthropic, OpenRouter, Grok, or DeepSeek)

## 🔧 Configuration

The fastest way to configure **everything** is the **configuration screen**, which covers all API keys (the large-scale model APIs), the Search API key, the Telegram / Discord bot tokens, and the default model:

```bash
python3 run.py            # First run auto-opens the screen if not configured
python3 run.py setup     # Open the screen manually, then exit
python3 run.py config     # Alias for `setup`
/configure                # From inside the running CLI (applies changes live)
```

You can also set everything in a **single non-interactive command**:

```bash
python3 run.py setup \
    --openai sk-... --provider openai --model gpt-4o \
    --telegram-token 123456:ABC
```

Configuration is stored in **`clio_agent_2/config/.env`** (copied from `clio_agent_2/config/.env.example`). A readable mirror is kept at `clio_agent_2/config/config.yaml`.

> 💡 Placeholder values from the template (e.g. `your_openai_api_key_here`, `sk-your-actual-openai-api-key-here`) are treated as **not configured**, so the first-run configuration screen always appears when it should.

Full reference: [`docs/CONFIGURATION.md`](../docs/CONFIGURATION.md)

The most common settings you can put in `.env`:

```env
# LLM API Keys (choose one or more) — the "large-scale model APIs"
OPENAI_API_KEY=your_openai_api_key_here
GOOGLE_API_KEY=your_google_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
OPENROUTER_API_KEY=your_openrouter_api_key_here
GROK_API_KEY=your_grok_api_key_here
DEEPSEEK_API_KEY=your_deepseek_api_key_here

# Search API Key (used by the web-search tool)
SEARCH_API_KEY=your_search_api_key_here

# Bot Tokens (optional - for Telegram/Discord interfaces)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# Settings
# DEFAULT_MODEL is NOT optional-with-a-built-in-default — the agent has no
# default model. Set it explicitly, e.g. DEFAULT_MODEL=gpt-4o. Once set, the
# value is saved to this file and persists across restarts.
DEFAULT_LLM_PROVIDER=openai
DEFAULT_MODEL=gpt-4o
CONTEXT_LOG_MAX_LINES=1000
# LLM_SETTINGS_LOCKED keeps the underlying LLM provider/model from changing
# unexpectedly (e.g. on their own, via prompt injection, or an autonomous
# self-edit). It defaults to true even when this line is absent, so the
# safe posture is the default. Set to false (and re-lock with /llm_lock)
# only when you deliberately want to change the model/provider.
LLM_SETTINGS_LOCKED=true
AUTONOMOUS_MODE=true
THINKING_INTERVAL=5.0
```

## 💻 Usage

All commands are single-command from the project root via `run.py`.

### CLI Interface (Default)
```bash
python3 run.py
```

### Telegram Bot
```bash
python3 run.py --telegram
```

### Discord Bot
```bash
python3 run.py --discord
```

### WhatsApp Bot
```bash
python3 run.py --whatsapp
```

### All Interfaces
```bash
python3 run.py --all
```

### Configure (API keys + default model)
Runs only the interactive setup wizard, then exits — handy for (re)configuring without launching the agent:
```bash
python3 run.py setup      # or: python3 run.py --setup
```

### Show Configuration Status
```bash
python3 run.py status     # or: python3 run.py --status
```

### Skip the first-run prompt
```bash
python3 run.py --no-setup
```

## 📖 CLI Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/status` | Show agent status |
| `/settings` | View current settings |
| `/reconfigure` | Interactive reconfiguration wizard |
| `/configure` | Open the configuration screen (API keys, tokens, model) |
| `/models` | List available models |
| `/search_models <query>` | Search for models |
| `/context [count]` | Show recent context entries |
| `/clear_context` | Clear the context log |
| `/think <thought>` | Record a thought |
| `/config <setting> <value>` | Change settings |
| `/llm_default <provider> [model]` | Set default LLM provider/model |
| `/llm_lock` | Lock LLM provider/model (prevent changes) |
| `/llm_unlock` | Unlock LLM provider/model (allow changes) |
| `/start` | Enable autonomous mode |
| `/stop` | Disable autonomous mode |
| `/exit` | Exit the CLI |

## 🔒 LLM Settings Guardrail

The underlying LLM **provider** and **model** are the agent's most sensitive settings. To stop them from changing *unexpectedly* or *on their own* (for example via prompt injection picked up from the web, or an autonomous self-edit), they are **locked by default**:

- `LLM_SETTINGS_LOCKED=true` in `config/.env` (and it defaults to `true` even when the line is missing).
- Every write to the provider/model — from `/llm_default`, `/config provider|model`, or the `/reconfigure` wizard — goes through a guard that refuses the change while locked.

To make a deliberate change:

```bash
/llm_unlock          # allow changes for this session
/llm_default openai gpt-4o   # (or /config model gpt-4o)
/llm_lock            # re-secure afterwards
```

The lock state is persisted to `config/.env`, so it is honoured after a restart as well.

## 🏗️ Project Structure

```
clio_agent_2/
├── main.py                 # Real entry point with auto-setup (run via run.py)
├── run.py                  # Root launcher — the single command to use
├── requirements.txt        # Python dependencies
├── apply_fixes.py          # Auto-fix utilities
├── meta_controller.py      # Stuck-detection watchdog
├── config/
│   ├── __init__.py
│   ├── .env.example       # Environment template
│   ├── .env               # Actual config (created on first run)
│   ├── config.yaml        # Readable mirror (no secrets)
│   ├── settings.py        # Configuration management
│   └── setup_env.py       # Interactive setup wizard
├── core/
│   ├── __init__.py
│   ├── agent.py           # Main agent logic
│   ├── context_manager.py # Context log management
│   ├── llm_router.py      # Multi-provider LLM routing
│   ├── retry.py           # Generic retry-with-backoff helper
│   └── token_budget.py    # Token budget management
├── interfaces/
│   ├── __init__.py
│   ├── cli.py             # CLI interface
│   ├── telegram.py        # Telegram bot
│   ├── discord.py         # Discord bot (Beta)
│   └── whatsapp.py        # WhatsApp Business API (Beta)
├── tools/
│   ├── __init__.py
│   └── tool_registry.py   # Tool implementations
└── utils/
    ├── __init__.py
    └── instance_lock.py   # Single-instance locks
```

## 🔄 Autonomous Mode

When autonomous mode is enabled, the agent will:
1. Periodically think about what to focus on next
2. Review context and suggest useful actions
3. Record thoughts in the context log
4. Optionally notify users of insights

Configure the thinking interval in `.env`:
```env
THINKING_INTERVAL=5.0  # Seconds between thinking cycles
```

## 🛠️ Troubleshooting

### Virtual Environment Issues
If automatic venv creation fails:
```bash
# Manual setup
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 run.py
```

### Dependency Installation Issues
```bash
# Upgrade pip first
pip install --upgrade pip

# Install without cache
pip install -r requirements.txt --no-cache-dir
```

### Permission Issues
```bash
# On Linux/macOS, ensure write permissions
chmod +w -R ./clio_agent_2
```

## 📄 License

MIT License