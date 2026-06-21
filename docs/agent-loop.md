# Agent Loop — How It Works

This document explains the core think-execute loop in detail.

## The Perpetual Loop

```
┌─────────────────────────────────────────────────────────────┐
│                    AutonomousLoopEngine                      │
│                                                             │
│   ┌──────────┐     ┌──────────┐     ┌──────────────────┐   │
│   │  PLANNER  │────▶│ EXECUTOR │────▶│ LOOP CONTROLLER  │   │
│   │  (think)  │     │  (act)   │     │  (observe)       │   │
│   └──────────┘     └──────────┘     └────────┬─────────┘   │
│        ▲                                       │            │
│        │                                       │            │
│        └────────────── feedback ───────────────┘            │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │                   EXECUTION LOG                       │  │
│   │  [14:00:01] Read file: config.yaml                   │  │
│   │  [14:00:02] Bash: git status                         │  │
│   │  [14:00:03] Grep: "TODO" in src/                     │  │
│   │  ...                                                 │  │
│   └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Step-by-Step: One Iteration

### Phase 1: THINK (Planner)

The Planner builds two prompts:

**System Prompt** — Behavioral rules (from `AGENTS.md`):
- Output format (JSON schema)
- Anti-loop rules
- Tool usage guidelines
- Telegram/Discord mode rules

**User Prompt** — Current context:
- Current goal
- Last 80 lines of execution log
- Saved context from previous session (if resuming)
- OS info and environment details
- Loop warnings (if agent is stuck)

The Planner sends these to the LLM via `ModelRunner`, which routes to the
configured provider (OpenRouter, OpenAI, etc.).

The LLM returns structured JSON:
```json
{
  "thinking": "I need to check the git status first",
  "actions": [
    {"type": "bash", "args": {"command": "git status"}},
    {"type": "read", "args": {"path": "README.md"}}
  ]
}
```

The Planner parses this into an `AgentPlan` containing `AgentAction` objects.

### Phase 2: EXECUTE (Executor)

The Executor iterates through each `AgentAction`:

1. **Map** the action type to a Tool (e.g., `bash` → `BashTool`)
2. **Build** the tool input from action args
3. **Run** the tool: `tool.execute(input) → ToolResult`
4. **Record** the result in the execution log

Special actions:
- `telegram()` — Send message via Telegram bot
- `discord()` — Send message via Discord bot
- `sleep` — Trigger context compression and restart
- `exit` — Graceful shutdown
- `parallel` — Execute multiple actions concurrently

### Phase 3: OBSERVE (LoopController)

After execution, the LoopController:

1. **Checks for loops** — Compares action signatures across recent iterations
2. **Triggers Curiosity Fairy** — If the agent is stuck in a repeat pattern
3. **Compresses the log** — If it exceeds the threshold (80 lines)
4. **Injects warnings** — Into the next iteration's prompt

### Phase 4: CHECK & REPEAT

- If `sleep` was requested → compress context, save state, restart process
- If `exit` was requested → save context, terminate
- Otherwise → go back to Phase 1

## The Sleep/Resume Cycle

```
Log grows large (100+ lines)
    │
    ▼
Agent emits {"type": "sleep"}
    │
    ▼
Handle Sleep:
    1. LLM compresses full log → structured summary
    2. Save to .context/sleep_state.json
    3. Save to .context/context_log.txt
    4. Optional: git pull, rebuild venv
    5. os.execv() → restart process (same PID on Unix)
    │
    ▼
Resume:
    1. Read .context/sleep_state.json
    2. Inject compressed summary into prompt
    3. Continue from where we left off
```

## Loop Detection (Curiosity Fairy)

The engine monitors action signatures. If the same pattern repeats:

| Repeats | Action |
|---|---|
| 3 | Curiosity Fairy suggests a new direction (soft) |
| 6 | Loop Breaker activates (hard enforcement) |
| 8 | Forced sleep triggers |

Action signatures are hashes of `action_type + sorted_args`, so `bash:ls -la`
run 3 times in a row triggers the Fairy.

## Auto-Save

A background thread writes state to disk:
- Every **60 seconds**
- Every **10 iterations**

This ensures that even `kill -9` or power loss leaves recoverable state in
`.context/exit_state.json`.
