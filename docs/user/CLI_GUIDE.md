# CLI Interface Guide

Complete reference for using Clio-Agent-2 from your terminal.

---

## 💻 Starting the CLI

```bash
python3 run.py
```

The CLI is the default interface — no flag required.

---

## 🖥️ Interface Layout

The CLI is a full-screen terminal TUI built with `prompt_toolkit` and `rich`:

```
╔══════════════════════════════════════════╗
║ 🤖 Clio-Agent-2                        ║
║ Provider: openai  Model: gpt-4o 🔒     ║
╠══════════════════════════════════════════╣
║                                          ║
║  [Agent messages appear here ...]        ║
║                                          ║
║                                          ║
╠══════════════════════════════════════════╣
║ > Your input here...                     ║
╚══════════════════════════════════════════╝
```

### Sections

| Section | Description |
|---------|-------------|
| **Header** | Shows agent name, provider, model, and lock state |
| **Message area** | Conversation history (scrollable) |
| **Status bar** | Shows autonomous mode status |
| **Input bar** | Where you type messages and commands |

---

## ⌨️ Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Enter** | Send your message |
| **Ctrl+C** | Quit the agent |
| **Ctrl+D** | Quit on empty input |

---

## 🗣️ Sending Messages

Just type in the input bar and press **Enter**.

The agent will process your message and respond. Responses may include tool execution (file operations, web search, shell commands, etc.).

> **No "send" button required.** The agent listens for your Enter keypress.

---

## 📌 Slash Commands

Type a command starting with `/` and press Enter.

### User-Facing Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all available commands |
| `/help all` | Show only user-facing commands |
| `/settings` | View all current settings |
| `/reconfigure` | Open interactive reconfiguration wizard |
| `/configure` | Open the full configuration screen (live-updating) |
| `/config <setting> <value>` | Change a single setting |
| `/llm_providers` | List configured LLM providers |
| `/llm_models [provider]` | List available models (all or one provider) |
| `/llm_search <query>` | Search for models by name |
| `/llm_default <provider> [model]` | Set the active LLM provider/model |
| `/llm_lock` | Lock provider+model (block changes) |
| `/llm_unlock` | Unlock provider+model (allow changes) |
| `/api_keys` | Show which API keys are configured |
| `/status` | Show agent status (context size, tools, providers) |
| `/context [count]` | Show the most recent context entries |
| `/clear_context` | Wipe context (backed up automatically) |
| `/restore_context` | Restore the last cleared context backup |
| `/start` | Enable and start autonomous mode |
| `/stop` | Disable autonomous mode |
| `/exit` or `/quit` | Quit the agent |

### Agent-Internal Commands

| Command | Description |
|---------|-------------|
| `/think <thought>` | Record an internal reasoning note |

> The `/think` command is for the agent's own scratchpad. You can use it, but it behaves differently from a regular message.

---

## ⚙️ Configuration Commands in Detail

### `/config` — Change a Single Setting

```text
/config provider openai       # Change default provider
/config model gpt-4o-mini     # Change model
/config autonomous_mode true  # Enable autonomous mode
/config thinking_interval 10  # Set thinking interval to 10 seconds
```

### `/llm_default` — Change LLM (Respects Lock)

```text
/llm_default                    # Show current LLM
/llm_default openai             # Change provider (keep current model)
/llm_default google gemini-1.5-flash  # Change provider + model
```

### `/llm_lock` / `/llm_unlock`

```text
/llm_unlock                     # Allow LLM changes
/llm_default google gemini-1.5-flash  # Now this will work
/llm_lock                       # Re-secure
```

---

## 🧠 Context Commands

The agent maintains a memory called the **context log**.

| Command | Effect |
|---------|--------|
| `/context 20` | Show the 20 most recent context entries |
| `/clear_context` | Wipe the context log. **Backed up first** — use `/restore_context` to recover |
| `/restore_context` | Restore from the last backup |

> **Context compression:** Once context exceeds `CONTEXT_LOG_MAX_LINES` (default 1000), older entries are summarized by the LLM. Very old details may be lost after compression.

---

## 🤖 Autonomous Mode Commands

| Command | Effect |
|---------|--------|
| `/start` | Start/restart the autonomous thinking loop |
| `/stop` | Stop the autonomous loop |

### What happens on startup

Autonomous mode is **ON by default**. This means the agent starts thinking on a timer immediately after launch. It may proactively message you.

Use `/stop` to disable and use `/start` to re-enable.

---

## 🪟 Interaction Tips

- **Be direct and clear.** The agent works best with specific goals.
- **Check in periodically.** Use `/status` or ask "what have you done?" to keep tabs on background work.
- **Repeat important context.** After a long session (especially post-compression), restate details that matter — the agent may have lost them.
- **Review autonomous shell actions.** Autonomous mode can run shell commands on its own — be cautious.

---

## 🚪 Exiting the Agent

```text
/exit
/quit
# or
Ctrl+C
```

On exit, the agent preserves your context and settings automatically.

---

## 🧭 Related Docs

- [Quick Start](QUICK_START.md) — getting the agent running
- [Autonomous Mode](AUTONOMOUS_MODE.md) — how the background loop works
- [Configuration Guide](CONFIGURATION.md) — all settings
