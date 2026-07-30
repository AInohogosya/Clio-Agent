# Getting Started

Clio-Agent-2 is designed to be operated mostly through **one command**. This guide
takes you from a fresh clone to a running agent.

## Requirements

| Requirement | Notes |
|-------------|-------|
| **Python** | 3.8+ (tested on 3.14). The launcher uses only the standard library. |
| **Internet** | Needed to install dependencies and to call LLM / search APIs. |
| **At least one LLM API key** | OpenAI, Google, Anthropic, OpenRouter, Grok, DeepSeek, … |
| **OS** | Linux, macOS, or Windows. |

> Run it on a dedicated/least-privilege account or in a container/VM you don't
> mind wiping. See [Safety](operations/safety.md).

## One-command setup & launch

From the project root:

```bash
python3 run.py
```

That command **automatically**:

1. Checks system compatibility (Python version, write permissions).
2. Creates a Python **virtual environment** (`.venv`) if needed.
3. **Installs all dependencies** into that environment.
4. Creates `clio_agent_2/config/.env` from the template.
5. On the **first run** (no real API key + model), opens an interactive
   **configuration screen** to walk you through keys and a default model.
6. Launches the agent (CLI by default).

## Choosing an interface

```bash
python3 run.py            # CLI (default)
python3 run.py --telegram # Telegram bot
python3 run.py --discord  # Discord bot (Beta)
python3 run.py --all      # All configured interfaces
```

## Configuring without launching

```bash
python3 run.py setup      # Interactive screen, then exit
python3 run.py status     # Show configuration status, then exit
```

Non-interactive example:

```bash
python3 run.py setup \
    --openai sk-... --provider openai --model gpt-4o \
    --telegram-token 123456:ABC
```

## Manual environment setup (if auto-setup fails)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r clio_agent_2/requirements.txt
python3 run.py
```

## What to do next

- Learn the [Slash Commands](usage/slash-commands.md).
- Understand [Autonomous Mode](usage/autonomous-mode.md).
- Review [Safety](operations/safety.md) before letting it act on its own.
