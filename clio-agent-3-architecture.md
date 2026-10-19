# Clio Agent 3 Architecture

*This description is based on the codebase located at `/Users/sasakikousei/Desktop/Clio-Agent/ethos` (Python package `ethos`, version derived from `pyproject.toml`).*

## 1. Overview

Clio Agent 3 is a "persistent, general-purpose autonomous agent" (`pyproject.toml:8`). Unlike a typical chatbot—which operates via a simple prompt-response exchange—it maintains a continuous "life loop" (`pace → perceive → attend → decide → act → record`) that runs according to its own internal clock. Users can interact with the agent asynchronously across various channels, including Telegram, Slack, Discord, Email, WhatsApp, the Web, and the CLI. The agent evaluates the "salience" (prominence/importance) of incoming inputs to autonomously decide whether to respond, perform a task, or remain silent. Persistent memory is stored in PostgreSQL 16 (utilizing `pgvector`), with autonomous processes for memory consolidation and self-model updates occurring overnight.

**Runtime Model**: The system does not rely on a single-process loop. Executing `ethos up` (`ethos/core/bootstrap.py:35`) launches a seven-process architecture under a supervisor—comprising a gateway, toolhost, communications module, two worker processes, a core engine, and an optional web interface (`ethos/core/supervisor.py:163`). At its heart is a tick-based life loop running within the `ethos-core` process (defaulting to 4 seconds ±15%; `ethos/engine/rhythm.py:184`). Inter-process communication is facilitated by a transactional event bus built on PostgreSQL's `events` table, utilizing the `LISTEN/NOTIFY` mechanism (`ethos/bus/event_bus.py:40`). Perceptions arrive in an event-driven manner, but they are processed—cleared from the queue—at the beginning of each tick.

## 2. Components

| Module | Responsibilities | Key Files |
|---|---|---|
| `ethos.core` | Bootstrapping, supervision, control plane, and scheduling | `bootstrap.py`, `supervisor.py`, `runner.py`, `control.py`, `scheduler.py` |
| `ethos.comms` | Channel adapters (Telegram, Slack, Discord, Email, WhatsApp, Web, Webhooks, CLI) | `host.py`, `adapters/base.py`, and individual adapter files under `adapters/` |
| `ethos.bus` | Transactional event bus (Postgres LISTEN/NOTIFY) | `bus/event_bus.py` |
| `ethos.engine` | Life loop, attention, state machine, rhythm, workspace, reverie, and drives | `engine/life.py`, `engine/attention.py`, `engine/states.py`, `engine/rhythm.py`, `engine/workspace.py`, `engine/reverie.py`, `engine/drives.py` |
| `ethos.social` | Response deliberation, etiquette (egress pipeline), and conversation state | `deliberation.py`, `etiquette.py`, `conversation.py` |
| `ethos.gateway` | Model routing, provider normalization, cost management, rate limiting, and embeddings | `service.py`, `router.py`, `normalizer.py`, `providers/`, `cost.py`, `embeddings.py`, `secrets.py` |
| `ethos.gsl` | General Solution Loop (Goal → Plan → Execute → Verify) | `loop.py`, `planner.py`, `evaluator.py`, `acceptance.py`, `verification.py` |
| `ethos.toolhost` | Tool execution host (supports ~50 tools, including shell, filesystem, code, HTTP, browser, desktop, PTY, watchers, etc.) | `server.py`, `kernels.py`, `pty_session.py`, `browser.py`, `tools/` |
| `ethos.guardian` | Safety = Reversibility. Action Journal, Gate, Trash, Snapshots, Audit | `gate.py`, `journal.py`, `trash.py`, `snapshots.py`, `registry.py`, `classifier.py` |
| `ethos.memory` | Memory, retrieval, integration, and contextual layers for episodes, facts, know-how, and questions | `store.py`, `retrieval.py`, `context.py`, `consolidate.py`, `continuity.py`, `self_model.py` |
| `ethos.threads` / `ethos.subagents` | Delegation threads and external sub-agents (Foundry/APEX) | `threads/manager.py`, `threads/worker.py`, `subagents/{base,bridge,foundry,apex,tools}.py` |
| `ethos.observability` / `ethos.prompts` | Logging, audit hash chains, Prometheus / Prompt templates | `observability/*.py`, `prompts/*.py` |
| `interface/` | TypeScript-based UI kit (3 packages: `@project-phone/core|web|cli`) | `packages/core/src/` (specifically `agent/`, `duplex-client.ts`) |
| `web/` | Node-based interface bridge (HTTP/WS ⇄ Agent's Postgres) | `web/server/index.mjs` |

## 3. Dependencies (Overview)

```
interface (web/cli) ──HTTP/WS──> web/server (bridge) ──SQL/events──> Postgres
Channels (e.g., Telegram) ──> ethos-comms ──> events table ──NOTIFY──> ethos-core (Life)
ethos-core ──> engine/social/gsl ──> gateway (in-process or Unix socket 8700)
└─> toolhost (in-process or socket/TCP 8701) ──> guardian ──> Postgres/filesystem
ethos-workers ──> thread.request event ──> threads.worker ──> gateway/toolhost
subagents (Foundry/APEX) ──OpenAI-compatible shim (gateway/compat.py)──> gateway
```

## 4. Core Flow: Tracking a Single User Message

Taking a message arriving via Telegram as an example:

1. **Reception**: `TelegramAdapter.listen()` performs a long poll; after verifying the sender against an allowlist, it constructs a `Message` object and invokes `on_inbound` (`ethos/comms/adapters/telegram.py:218,169`).

2. **Ingestion**: The `inbound_to_bus()` handler (`ethos/comms/host.py:733`) calls `SocialCognition.record_inbound()` to perform an `INSERT` into the `messages` table (`ethos/social/deliberation.py:840`) and triggers `EventBus.publish(EventKind.INBOUND_MSG, ...)` (`event_bus.py:171`—the `INSERT` and `pg_notify` occur within the same transaction).

3. **Perception**: A wildcard subscription in `ethos-core` (`CoreRuntime._wire_bus().on_event`, `ethos/core/runner.py:203`) converts the event into a `Percept` object via `percept_from_event` (`ethos/schemas/events.py:79`) and adds it to `Life.percept_queue`.

4. **Tick**: The `while` loop within `Life.life()` (`ethos/engine/life.py:1126`) executes the `_cycle()` method (`:1187`). `Attention.appraise()` (`ethos/engine/attention.py:145`) calculates salience based on T0 rules; intermediate values are refined via the T1 model call `refine_salience()`. If a threshold is exceeded, the current focus is saved via `bookmark()` to allow for an interruption.

5. **Deliberation**: `Life.decide()` (`life.py:390`) retrieves unaddressed messages via `SocialCognition.pending_inbound()` (`deliberation.py:759`) and determines whether an interruption is warranted using `_message_attention_verdict()`. If the condition is met, it proceeds to `SocialCognition.deliberate()` (`deliberation.py:258`): applying T0 ignore rules, constructing a prompt via `social_deliberation_messages()` (`ethos/prompts/deliberation.py:231`), and invoking `_ask()` to build a `ModelRequest` for `gateway.complete(request)` (`deliberation.py:610`). The gateway performs a budget check (`SpendGuard`) via `GatewayService.complete()` (`ethos/gateway/service.py:627`), selects a model via `ModelRouter.select()` (based on quality, cost, and latency; `router.py:138`), and executes the call through the appropriate provider (Anthropic, OpenAI, Google, or an OpenAI-compatible API).

6. **Parsing**: The JSON response is processed via `_parse_json()`—incorporating multi-stage recovery (handling truncation, prose, or malformed keys)—to produce a `SocialDecision` object (`deliberation.py:950+`). If the response cannot be interpreted, the system triggers `_undecided()`—ensuring no information is fabricated.

7. **Action**: The decided action is executed via the appropriate channel adapter or tool host, and the result is recorded back into memory and the event bus for future reference.

## 5. Key Subsystems

### 5.1 Seven-Process Supervisor (`ethos/core/supervisor.py`)
The supervisor manages seven processes under a single orchestration:
- **gateway** (model routing, in-process or Unix socket 8700)
- **toolhost** (tool execution, in-process or socket/TCP 8701)
- **comms** (channel adapters, managing door open/close/reopen)
- **two workers** (thread processing)
- **core** (the life loop and engine)
- **web** (optional HTTP/WS bridge)

Each process is managed by a `ProcessSpec` with restart backoff, PID file management, and signal handling. The supervisor uses `LISTEN/NOTIFY` on the events bus to detect child process state changes.

### 5.2 Life Loop and Rhythm (`ethos/engine/life.py`, `ethos/engine/rhythm.py`)
The life loop runs on a tick period of 4 seconds ±15%, driven by `RhythmController` which manages:
- Energy tracking and daily rhythm
- Focus sessions and mind-wandering intervals
- Rest periods and reverie opportunities
- Tick pacing with exponential backoff for retries

The loop executes `_cycle()` → `Attention.appraise()` → `Life.decide()` → action execution → `record()` on each tick.

### 5.3 Attention and Salience (`ethos/engine/attention.py`)
`Attention.appraise()` calculates the salience of incoming perceptions using T0 rules (hardcoded importance thresholds), with intermediate values refined through a T1 model call to `refine_salience()`. The salience determines whether a perception becomes the current focus or is queued for later consideration. Threshold crossing triggers `bookmark()` to save focus state for potential interruption.

### 5.4 State Machine (`ethos/engine/states.py`)
A hierarchical vigilance state machine with 9 states:
- `THINKING_FOCUSED`, `THINKING_REFLECTING`, `ACTING`, `WAITING`, `NOTIFYING`, `RESTING`, `PAUSED`, `RESUMING`

State groups: `AWAKE` (focused, reflecting, acting, waiting, notifying) and `RESTING`/`PAUSED`. Transitions are validated via `ALLOWED_TRANSITIONS`, with PAUSED → RESUMING and RESTING having broad allowed transitions.

### 5.5 Workspace (`ethos/engine/workspace.py`)
One cycle's working memory, assembled in block order:
```
BLOCK_ORDER = (
    "identity", "tools", "limits", "situation",
    "commitments", "conversations", "focus", "thread",
    "recent", "memories", "thoughts", "percepts",
)
```
The first 3 blocks are stable across cycles; `CONTEXT_BLOCKS = ("thread", "recent", "conversations")` outrank retrieval-based blocks.

### 5.6 Drives (`ethos/engine/drives.py`)
Seven drives with gain functions and utility-based arbitration:
`commitment`, `curiosity`, `mastery`, `creation`, `order`, `social`, `homeostasis`

Each drive proposes impulses with a gain function; utilities are softmax-selected. The commitment drive, for example, uses `UNDATED_COMMITMENT_URGENCY = 0.5` to weight undated promises, and drives operate on `DriveInputs` containing intentions, energy, diskFreeRatio, budgetRatio, and maintenance due.

### 5.7 Reverie (`ethos/engine/reverie.py`)
An "idle mind" capability that lets the agent choose its own subject when no inbound messages are present. Uses memory retrieval, self-model awareness, and question/venture commitment to select a persistent topic of thought, ensuring the agent can initiate activity rather than merely waiting.

### 5.8 Social Deliberation (`ethos/social/deliberation.py`)
T0→T3 tiered decision-making with token budgets:
- `DELIBERATION_MAX_TOKENS = 8_000` (ceiling for answer + reasoning)
- `DELIBERATION_REASONING_BUDGET = 400`
- `DELIBERATION_ESCALATED_MAX_TOKENS = 24_000`

Every inbound message becomes a deliberate choice among: `reply_now`, `reply_later`, `acknowledge`, `act_silently`, `ask_clarification`, `ignore`. T0 rules filter obvious ignores; T3 deliberation constructs prompts and calls the gateway with budget checking.

### 5.9 Gateway and Model Routing (`ethos/gateway/service.py`, `ethos/gateway/router.py`)
- `ModelRouter.select()` routes requests via scoring `m* = argmax [q_m(class) - mu*c_hat - nu*lat_hat]` based on quality, cost, and latency
- Circuit breakers, pinned models, fallover Transient vs permanent failures
- `SpendGuard` budget checking via `CostCalculator`
- Multiple providers: Anthropic, OpenAI, Google, OpenAI-compatible

### 5.10 Event Bus (`ethos/bus/event_bus.py`)
Transactional event bus backed by PostgreSQL `events` table + `LISTEN/NOTIFY`:
- Publishes are atomic with their data (NOTIFY piggybacked on same transaction)
- Dedupe window of 16,384 seen event IDs using ordered eviction
- Wildcard + kind-specific subscriptions
- `drain()` for startup gap recovery (claiming unconsumed events)

### 5.11 Guardian (`ethos/guardian/`)
Safety = Reversibility:
- **Action Journal**: Every action is logged before execution
- **Gate**: Permission gate for actions requiring confirmation
- **Trash**: Soft-delete with snapshots
- **Snapshots**: Point-in-time state captures
- **Audit**: Hash-chained audit trail

### 5.12 GSL (General Solution Loop) (`ethos/gsl/`)
Goal → Plan → Execute → Verify loop with:
- `planner.py`: Decomposes goals into steps
- `evaluator.py`: Evaluates step outcomes
- `verification.py`: Verifies acceptance criteria
- `acceptance.py`: Defines acceptance thresholds

### 5.13 Toolhost (`ethos/toolhost/`)
Supports ~50 tools including:
- Shell, filesystem, code execution
- HTTP requests, browser automation
- PTY sessions, watchers
- Desktop automation

Each tool runs in isolated kernels with resource limits and timeout handling.

### 5.14 Memory (`ethos/memory/`)
- **Store**: Persists episodes, facts, know-how, questions
- **Retrieval**: Vector search (`pgvector`) with reranking and context filtering
- **Context**: Slides memory into the workspace block order
- **Consolidate**: Overnight memory integration and pruning
- **Continuity**: Maintains agent self-model across restarts
- **Self-model**: Tracks the agent's own preferences, capabilities, and state

### 5.15 Communications (`ethos/comms/`)
Channel adapters for:
- Telegram, Slack, Discord, WhatsApp, LINE, Teams, Google Chat, Email, CLI, Web, Webhooks

Each adapter handles protocol-specific details (signing, long-polling, webhook receipt) and conforms to the `ChannelAdapter` base class. The `CommsHost` manager builds adapters from YAML configuration and supports runtime reconfiguration.

### 5.16 Threads and Sub-agents (`ethos/threads/`, `ethos/subagents/`)
- **Threads**: Delegation with managers and workers; `thread.request` events flow through the bus to `threads.worker` → gateway/toolhost
- **Sub-agents**: Foundry and APEX bridges via OpenAI-compatible shim at `gateway/compat.py`