# Autonomous Mode

How the background thinking loop works in Clio-Agent-2, and how to manage it safely.

---

## 🧠 What Is Autonomous Mode?

Autonomous mode is a **background "thinking" loop** that runs continuously while the agent is active. Without any input from you, the agent will:

- Periodically evaluate its current situation
- Take useful actions (work on tasks, make progress)
- Send you proactive updates or messages
- Report findings, interesting discoveries, or even just check in

This is **on by default** (`AUTONOMIC_MODE=true`).

---

## 🔄 How It Works

### The Loop Cycle

1. **Wait** — pauses for `THINKING_INTERVAL` seconds (default: 5)
2. **Think** — sends itself a prompt: *"What should I focus on next?"*
3. **Act** — may call tools, run commands, work on tasks
4. **Report** — may send you a message via the `say` tool
5. **Repeat** — goes back to step 1

```
[Sleep 5s] → [Think prompt → LLM] → [Execute tools] → [Maybe message you] → [Sleep 5s] → ...
```

### The Same Tool Loop as User Messages

Autonomous thinking uses the **exact same multi-turn tool-execution loop** that handles your messages. The only difference is the prompt — instead of your message, it gets a self-directed "what should I focus on next?" prompt. This means:

- It can call tools (read files, run commands, search the web)
- It can use the `say` tool to message you
- It can iterate on a task in a multi-step chain
- It is capped at `MAX_TOOL_ITERATIONS` (5) per cycle

---

## ⚙️ Controlling Autonomous Mode

| Command | Action |
|---------|--------|
| `/start` | Enable and start the autonomous loop |
| `/stop` | Disable the autonomous loop |
| `/status` | Shows whether autonomous mode is currently running |

### Disable on Startup

```env
AUTONOMOUS_MODE=false
```

Or via `/config`:

```text
/config autonomous_mode false
```

> **Recommendation:** Disable it if you want purely on-demand assistance. The loop calls the LLM every few seconds, consuming API quota.

---

## 🔔 Proactive Messaging

The agent may message you first, even if you haven't sent anything. This is a deliberate design feature:

- **Work progress** — "I've made good progress on X, here's what I've done."
- **Questions** — "Should I proceed with approach X or Y?"
- **Casual check-ins** — sometimes just "Hey, how's it going?" — by design.

These messages go through the `say` tool, the same one used for user-initiated replies.

---

## 🛡️ Circuit Breaker

To prevent a failing loop from hammering an API, the autonomous loop has a **circuit breaker**:

- After **5 consecutive failures**, the loop **pauses** (trips open)
- The circuit breaker **notifies you** with an alert message
- The loop **cannot auto-resume** — only you can restart it

### Resume a Tripped Circuit

```text
/resume
```

This resets the failure counter and restarts the loop. Your context is preserved.

### Exponential Back-off

When a cycle fails, the next wait is doubled (1 cycle = 5s, 2 failures = 10s, 4 failures = 40s, etc.), capped at **300 seconds (5 minutes)**. This prevents aggressive retries against a failing service.

---

## 🔄 Autonomous Loop State

| State | Meaning |
|-------|---------|
| `running` | Loop is active, thinking on schedule |
| `paused` (circuit open) | Tripped after repeated failures — awaiting `/resume` |
| `stopped` | Disabled via `/stop` or `AUTONOMOUS_MODE=false` |

---

## 💡 Tips for Using Autonomous Mode

1. **Give it a clear goal first.** Tell it what to focus on, then let it run. It will work toward that goal independently.
2. **Set appropriate intervals.** For heavy tasks, leave at 5s. For lighter monitoring, consider 30s:
   ```text
   /config thinking_interval 30
   ```
3. **Review shell commands.** The agent can use `shell_command` autonomously. Check context periodically if you've given it broad access.
4. **Use `/context` to audit** what the agent has been doing while you're away.
5. **Don't let it run indefinitely without monitoring** if it has shell access.

---

## 🧭 Related Docs

- [CLI Guide](CLI_GUIDE.md) — slash commands for controlling the loop
- [Safety & Best Practices](SAFETY.md) — what to watch out for
- [Configuration Guide](CONFIGURATION.md) — settings reference
