# Architecture Overview

Clio-Agent-2 is a single-process, async Python application. One **main loop**
drives an LLM, interprets its tool-call output, executes tools, logs everything to
a **context log**, and — in autonomous mode — repeats on a timer without any user
input.

## High-level flow

```
   user / schedule
          │
          ▼
   ┌─────────────────────┐
   │   interface         │   CLI · Telegram · Discord
   │ (delivers messages) │
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │   ClioAgent         │   core/agent.py
   │  - builds prompt     │
   │  - parses tool calls │
   │  - runs the loop     │
   └──────┬──────────┬────┘
          │          │
          ▼          ▼
   ┌────────────┐  ┌──────────────────┐
   │ LLMRouter  │  │ ToolRegistry      │
   │ (providers)│  │ (file/web/shell…) │
   └─────┬──────┘  └────────┬──────────┘
         │                  │
         ▼                  ▼
   ContextLog ◄─────────────┘   (every action is logged)
   (persist + compress)
```

## The four responsibilities

1. **Interfaces** (`interfaces/`) — receive user messages and render agent
   responses. They call into the agent and register a response callback.
2. **Agent brain** (`core/agent.py`) — owns the system prompt, the conversation
   flow, tool-call parsing, and the autonomous thinking loop.
3. **LLM routing** (`core/llm_router.py`) — abstracts over many providers behind a
   single `chat(messages)` interface, with retry and a settings lock.
4. **Tools** (`tools/tool_registry.py`) — the agent's capabilities: file
   read/write, web search, local search, shell execution, thinking, and `say`.

## Cross-cutting modules

- **Context management** (`core/context_manager.py`) — the rolling context log,
  compression, and persistence.
- **Retry helper** (`core/retry.py`) — generic async retry-with-backoff used by
  the router and the shell tool.
- **Meta controller** (`meta_controller.py`) — a stuck-detection watchdog
  (`RepetitionDetector`) plus an optional meta-LLM that suggests the next action.
- **Configuration** (`config/`) — `.env` loading, the setup wizard, and the YAML
  mirror.

## Design principles

- **One command to run.** `run.py` re-executes `clio_agent_2/main.py`, which
  performs all setup (venv, deps, config) before launching.
- **Async everywhere.** Interfaces, the agent loop, the router, and tools are
  all `async`/`await`.
- **Safe-by-default settings.** The LLM provider/model lock defaults to **on**, so
  the model can't change on its own.
- **Resilient to transient failure.** Network/provider blips are retried; only
  permanent errors (bad credentials, missing model) propagate immediately.

See also: [Entry Point & Auto-Setup](entry-point.md), [Directory Structure](directory-structure.md),
[The Agent Loop](agent-loop.md), [Context Management](context-management.md).
