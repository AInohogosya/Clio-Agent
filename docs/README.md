# Clio-Agent-2 Documentation

Welcome to the documentation for **Clio-Agent-2**, an autonomous AI agent you run
on your own machine. It can read/write files, search the web, run shell commands,
and — in *autonomous mode* — act and message you on its own. It runs from your
**terminal (CLI)**, **Telegram**, or **Discord (Beta)**, and talks to many
LLM providers (OpenAI, Google, Anthropic, OpenRouter, and more).

> **⚠️ Note about this AI agent:** This AI agent does not necessarily respond immediately when you send a message; it may reply several hours later. It is a truly human-like AI agent, and it is not simply being lazy or doing nothing—rather, it is prioritizing its own tasks, which can cause responses to messages to be delayed.

> ⚠️ **Safety first.** The agent can run shell commands with your privileges and
> autonomous mode is on by default. Read [Operations → Safety](operations/safety.md)
> before running it.

## Table of Contents

### Getting Started
- [Getting Started](getting-started.md) — install, configure, and launch in one command.
- [Configuration Reference](CONFIGURATION.md) — every `.env` setting, provider, and token.

### Architecture
- [Architecture Overview](architecture/overview.md) — how the pieces fit together.
- [Entry Point & Auto-Setup](architecture/entry-point.md) — `run.py` and `main.py`.
- [Directory Structure](architecture/directory-structure.md) — what lives where.
- [The Agent Loop](architecture/agent-loop.md) — autonomous thinking and acting.
- [Context Management](architecture/context-management.md) — the context log, compression, persistence.

### Core Modules
- [Agent (`core/agent.py`)](core/agent.md) — message handling and the autonomous loop.
- [LLM Router (`core/llm_router.py`)](core/llm-router.md) — multi-provider routing.
- [Retry Helper (`core/retry.py`)](core/retry.md) — retry-with-backoff.
- [Meta Controller (`meta_controller.py`)](core/meta-controller.md) — stuck-detection watchdog.

### Tools
- [Tools Overview](tools/overview.md) — the tool registry and `ToolResult`.
- [File Tools](tools/file-tools.md) — read / write / append / edit.
- [Web Tools](tools/web-tools.md) — `web_search` and `fetch_url`.
- [Search Tools](tools/search-tools.md) — `search_files`, `search_content`, `list_directory`.
- [Shell Tool](tools/shell-tools.md) — `shell_command` (the most powerful, and riskiest).
- [Thinking Tool](tools/thinking-tool.md) — `thinking` internal monologue.
- [Say Tool](tools/say-tool.md) — the agent's explicit way to address you.

### Interfaces
- [Interfaces Overview](interfaces/overview.md)
- [CLI](interfaces/cli.md)
- [Telegram](interfaces/telegram.md)
- [Discord (Beta)](interfaces/discord.md)

### Configuration
- [Environment Reference](configuration/env-reference.md) — full `.env` key list.
- [Setup Wizard](configuration/setup-wizard.md) — run `python3 run.py setup`.
- [LLM Providers](configuration/providers.md) — built-in and custom "Other" providers.
- [LLM Settings Lock](configuration/llm-lock.md) — the provider/model guardrail.

### Usage
- [Slash Commands](usage/slash-commands.md) — full command reference.
- [Autonomous Mode](usage/autonomous-mode.md) — proactive behavior.
- [Troubleshooting](usage/troubleshooting.md) — common problems and fixes.

### Operations
- [Safety](operations/safety.md) — risks and how to run responsibly.
- [Known Limitations](operations/known-limitations.md) — what it can't / doesn't do well.
- [Testing](operations/testing.md) — the test suite and how to run it.

### Development
- [Contributing](development/contributing.md)
- [Adding a Tool](development/adding-tools.md)
- [Adding a Provider](development/adding-providers.md)

## How the docs map to the code

| Docs section | Source location |
|--------------|-----------------|
| Architecture / Entry Point | `run.py`, `clio_agent_2/main.py` |
| Core modules | `clio_agent_2/core/*.py`, `clio_agent_2/meta_controller.py` |
| Tools | `clio_agent_2/tools/tool_registry.py` |
| Interfaces | `clio_agent_2/interfaces/*.py` |
| Configuration | `clio_agent_2/config/*.py` |

The canonical configuration file is `clio_agent_2/config/.env` (copied from
`clio_agent_2/config/.env.example`). A readable mirror is kept at
`clio_agent_2/config/config.yaml`.