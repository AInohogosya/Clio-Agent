# Architecture Reference

Detailed technical reference for the Clio Agent 1 codebase.

---

## System Components

### Entry Points

| File | Purpose |
|---|---|
| `run.py` | Main bootstrap: venv creation, dependency installation, signal handling, CLI parsing |
| `agent_core.py` | Thin backward-compat wrapper around the autonomous loop |
| `src/clio_agent.py` | Console_scripts entry point (`Clio-Agent` / `clio-agent` commands) |
| `install.sh` | One-command installer for fresh systems |

### Core Processing (`src/ai_agent/core_processing/`)

| File | Class | Responsibility |
|---|---|---|
| `autonomous_loop_engine.py` | `AutonomousLoopEngine` | Main loop: think → execute → repeat. Lifecycle, auto-save, sleep/resume |
| `planner.py` | `Planner` | Builds prompts, calls LLM, parses JSON into `AgentPlan` |
| `executor.py` | `Executor` | Iterates `AgentPlan.actions`, maps to tools, runs them |
| `loop_controller.py` | `LoopController` | Log management, loop detection, Curiosity Fairy |
| `context_manager.py` | (functions) | Reads/writes `.context/` folder for crash recovery |
| `agent_schema.py` | `AgentPlan`, `AgentAction`, `ActionType` | JSON schema for LLM output + parser |
| `terminal_history.py` | `TerminalHistory` | Persistent terminal history log |

### External Integration (`external_integration/`)

| File | Class | Responsibility |
|---|---|---|
| `model_runner.py` | `ModelRunner` | Multi-provider LLM client |
| `telegram_bot.py` | `TelegramBotManager` | Telegram bot: receive, queue, anti-duplication |
| `discord_bot.py` | `DiscordBotManager` | Discord bot |
| `ollama_provider.py` | `OllamaProvider` | Local Ollama provider |
| `openrouter_provider.py` | `OpenRouterProvider` | OpenRouter provider |
| `google_provider.py` | `GoogleProvider` | Google Gemini provider |
| `multi_provider_vision_client.py` | `MultiProviderVisionAPIClient` | Vision API across providers |

### Tools (`src/ai_agent/tools/`)

| File | Class | Purpose |
|---|---|---|
| `base.py` | `ToolExecutor`, `ToolRegistry`, `PermissionSet` | Base classes, registry, permissions |
| `file_read.py` | `FileReadTool` | Read files |
| `file_write.py` | `FileWriteTool` | Write files |
| `file_edit.py` | `FileEditTool` | Find & replace |
| `bash.py` | `BashTool` | Execute shell commands |
| `glob.py` | `GlobTool` | File pattern matching |
| `grep.py` | `GrepTool` | Content search |
| `todo_list.py` | `ToDoListTool` | Task tracking |
| `memo.py` | `MemoTool` | Note storage |
| `sub_agent.py` | `SubAgentTool` | Spawn sub-agents |

### Sub-Agents (`src/ai_agent/sub_agents/`)

| File | Class | Purpose |
|---|---|---|
| `base.py` | `SubAgentBase`, `SubAgentResult` | Abstract base, result types |
| `manager.py` | `SubAgentManager` | ThreadPoolExecutor orchestration |
| `registry.py` | `SubAgentRegistry`, `@sub_agent` | Decorator-based registration |
| `context.py` | `SubAgentContext` | Context dataclass |
| `agents/research_agent.py` | `ResearchAgent` | Research tasks |
| `agents/review_agent.py` | `ReviewAgent` | Code/output review |
| `agents/architect_agent.py` | `ArchitectAgent` | System design |

### Utilities (`src/ai_agent/utils/`)

| File | Purpose |
|---|---|
| `config.py` | YAML config loading, atomic writes, singleton |
| `resilience_engine.py` | Error classification, self-healing, global exception hook |
| `provider_fallback.py` | Circuit breaker, health tracking, automatic failover |
| `eternal_supervisor.py` | Watchdog process with auto-restart |
| `watchdog.py` | Lightweight process monitor |
| `exceptions.py` | Custom exception hierarchy with error categories |
| `logger.py` | Structured logging |
| `platform_compat.py` | Cross-platform helpers |
| `security.py` | Command blocking |
| `settings_manager.py` | Persistent settings |
| `cost_manager.py` | API cost tracking |

### Unified LLM API (`peripherals/api/`)

| File | Provider |
|---|---|
| `base.py` | Abstract base + factory |
| `google_client.py` | Google Gemini |
| `openai_client.py` | OpenAI |
| `anthropic_client.py` | Anthropic Claude |
| `groq_client.py` | Groq |
| `xai_client.py` | xAI Grok |
| `meta_client.py` | Meta Llama |
| `mistral_client.py` | Mistral AI |
| `microsoft_client.py` | Microsoft Azure |
| `amazon_client.py` | Amazon Bedrock |
| `cohere_client.py` | Cohere |
| `deepseek_client.py` | DeepSeek |
| `together_client.py` | Together AI |

---

## Data Flow

```
User Input / Telegram Message
    │
    ▼
AutonomousLoopEngine.start()
    │
    ▼
┌─ Loop Iteration ──────────────────────────────────────────┐
│  LoopController.format_prompt()                           │
│    ├── Goal                                               │
│    ├── Execution log (last 80 lines)                      │
│    ├── Saved context (from .context/ if resuming)         │
│    ├── OS info                                            │
│    └── Loop warnings (if any)                             │
│                                                          │
│  Planner.think()                                          │
│    ├── Build system prompt (AGENTS.md rules)              │
│    ├── Build user prompt (from LoopController)            │
│    ├── ModelRunner.run_model(request)                     │
│    │    └── Provider API call (OpenAI, Anthropic, etc.)  │
│    └── Parse JSON → AgentPlan                             │
│                                                          │
│  Executor.execute_plan(plan)                              │
│    ├── For each AgentAction:                              │
│    │    ├── Map ActionType → Tool                         │
│    │    ├── Tool.execute(input) → ToolResult              │
│    │    └── Append result to execution log                │
│    └── Return (should_sleep, should_exit)                 │
│                                                          │
│  LoopController.post_iteration()                          │
│    ├── Check for repetitive patterns                      │
│    ├── Compress log if needed                             │
│    └── Inject warnings into next prompt                   │
│                                                          │
│  Check: sleep? → _handle_sleep() → os.execv() restart     │
│  Check: exit?  → _handle_exit()  → sys.exit()             │
└──────────────────────────────────────────────────────────┘
```

---

## Key Design Patterns

### 1. Structured Output (No Regex Parsing)
The LLM outputs JSON matching a strict schema. `agent_schema.py` defines the
schema and `parse_plan_from_json()` handles parsing. This eliminates the entire
class of regex-based parsing errors.

### 2. Circuit Breaker
Each provider has a circuit breaker that opens after 5 consecutive failures.
While open, requests skip that provider entirely. After 60s, it enters
half-open state to test recovery.

### 3. Context Compression (Sleep/Resume)
When the execution log grows too large, the agent:
1. Asks the LLM to compress the log into a structured summary
2. Saves the summary to `.context/sleep_state.json`
3. Restarts the process via `os.execv()`
4. On restart, injects the compressed summary into the prompt

### 4. Decorator-Based Registration
Sub-agents use a decorator pattern:
```python
@sub_agent("research", description="Performs research")
class ResearchAgent(SubAgentBase):
    ...
```

### 5. Symlink for External Integration
The `external_integration/` directory lives at the project root but needs to be
importable as `ai_agent.external_integration`. The `__init__.py` and `run.py`
create a symlink at `src/ai_agent/external_integration/` pointing to the root.

---

## Configuration Resolution Order

1. Explicit CLI path: `Clio-Agent --config /path/to/config.yaml`
2. Environment variable: `CLIO_CONFIG=/path/to/config.yaml`
3. Default: `config.yaml` in project root

API key resolution for each provider:
1. Environment variable (e.g., `OPENAI_API_KEY`)
2. `config.yaml` → `api.api_keys.<provider>`
3. Interactive prompt (if `--no-prompt` is not set)

| `minimax_client.py` | MiniMax |
| `zhipuai_client.py` | Zhipu AI |
