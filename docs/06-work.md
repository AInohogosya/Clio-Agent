# GSL: the General Solution Loop

The General Solution Loop (GSL) is the one framework the agent uses to pursue any
goal in any field. Sources: `clio/gsl/loop.py`, `planner.py`, `evaluator.py`,
`acceptance.py`, `verification.py`, `config.py:436-496`.

## 1. The cycle

`GeneralSolverLoop.advance()` (`clio/gsl/loop.py:166-1016`):

```
understand → devise (3 candidates) → plan (≤7 steps)
  → try_step → evaluate → verify → reflect/revise → (repeat)
```

Terminal states `{done, parked, failed, cancelled}` (`loop.py:158-163`); a budget
exhaustion parks the goal (`loop.py:489-497`).

## 2. Understand → devise → plan

### 2.1 Understand (`planner.py:146-187`, budget 2000 tokens; planner-local
override 4000 in `config/gsl.yaml`)

The goal is restated with what is actually being asked, constraints, and what
success looks like. `_maybe_decompose` (3.2) hooks in here: a goal too complex for
one plan decomposes into child intentions
([09-version-3-2.md](09-version-3-2.md)).

### 2.2 Devise (`planner.py:190-240`)

**Exactly three** candidate approaches, each carrying `id, summary, expected_value,
estimated_cost_usd, reversible`. Selection:

```
pick argmax (EV − 0.05 · cost)          (planner.py:234-240)
```

Three is not arbitrary: one candidate is a trap (too few invites anchoring on the
first idea), and five dilutes a 2000-token budget. The `reversible` flag feeds the
guardian's risk calculus — a reversible approach can be tried where an irreversible
one needs justification.

Budget bucket: a commissioned goal spends from the `commitment` bucket, everything
else from `discretionary` (`budget_bucket_for`, `planner.py:79-87`) — owed work has
reserved funds.

### 2.3 Plan (`planner.py:276-376`, budget 8000 tokens)

**≤ 7 leaf steps** (`MAX_LEAF_STEPS = 7`). Failure modes raise
`PlanUnavailable(kind=unusable | no_model | rate_limited)` (`planner.py:32-76`).
After 3 consecutive planning attempts, planning **degrades gracefully**:
`T1 → T2`, reasoning budget 2000, relaxed schema (`planner.py:295-322`) — a
degraded plan beats no plan, and the park-reopen backoff (`15s → 240s`, later
`park_reopen_backoff = 1800s` from config) brings the goal back around.

## 3. Step execution

`_execute_step` (`loop.py:889+`): tier `T3` if the step is consequential else `T2`,
`step_tokens = 16000`, **one repair turn** — a failed tool call gets exactly one
chance to be corrected with the error in view, which is the loop's stance on
persistence ("repeating a call that has already failed is not persistence").

Steps run through the toolhost with idempotency keys `thread:step:n`
(`loop.py` / `threads/worker.py:499-504`) — a replayed cycle never re-executes a
side effect ([02-safety-and-guardian.md](02-safety-and-guardian.md) §4.1).

`STEP_FAILURES_BEFORE_SWITCH = 2` (`loop.py:57`): two failures of the current step
switch the step, not the plan; the plan is revised in `reflect/revise`.

## 4. Evaluation and acceptance

### 4.1 Objective checks (`evaluator.py:17-369`)

Check types: `command, syntax, file_exists, file_contains, json_field,
http_status, metric_gte, none/human`; `_check_command` runs with a 120 s timeout.
`verified = passed_all AND checks_run > 0` (`evaluator.py:338-369`) — **zero checks
run is never verified**, closing the "I checked nothing and it passed" hole.

### 4.2 Acceptance derivation (`acceptance.py`)

For software work, `derive_checks` (`acceptance.py:251-309`) recognizes 20 project
markers and 40 code suffixes and derives: a syntax check, then the project's real
test runner (`pytest` / `npm test` / `tsc` / `go test` / `cargo test`) and `ruff`,
capped at `max_commands = 3` with a 180 s timeout. The acceptance criteria come from
the project, not from the model's confidence.

## 5. The verification ladder

`VerificationLadder` (`clio/gsl/verification.py:83-437`), three rungs in order:

1. **Objective checks** — the §4 commands and assertions.
2. **Independent verifier** — a thread running a **different `diversity_family`**
   model at `T3`, shown **only the goal and the artifact** (`verification.py:350-387`;
   in threads `_run_verifier` shows `artifact[:6000]`, `threads/worker.py:261`).
   Independence is structural: different model family, no conversation context, no
   access to the claim that the work was done.
   **A judge cannot overturn failing checks** (`verification.py:276-290`) —
   the invariant behind "a check that ran outranks a model."
3. **Human review** — for `type=human` criteria only (`verification.py:301-305`).

Token budget for the model verifier: 1200 (`run_model_verifier`,
`verification.py:93`); the independent-thread verifier waits up to 240 s
(`verification.py:408`).

## 6. Stage notices: "tell me when it's done"

`_notice_stage` (`loop.py:2022`) publishes a notice when an intention reaches
`done / parked / awaiting_review`. Notices are **idempotent per `(intention_id,
stage)`** (`clio/memory/store.py:563-711`), addressed to the latest inbound sender,
decided via a small dedicated prompt (`STAGE_NOTICE_MAX_TOKENS = 900`), and run
through the outbound pipeline's mid-task immediate rule
([05-social.md](05-social.md) §1).

## 7. Parking: the honest end state

`_park` (`loop.py:1924`) is not failure. A parked goal keeps its state, its partial
work, and its budget accounting; the reopen sweep (`_maybe_reopen_parked`,
`life.py` / `park_reopen_backoff`) retries it later. The worker process parks (not
closes) on any failed thread result — the plain reading of a returned value, "the
work is done," closed intentions as `done` over pursuits that had failed
(`clio/__main__.py:468-476`). Parked-vs-done is the ledger's honesty mechanism:
**an intention is closed `done` only when the verification ladder says so.**

## 8. Token budgets (summary)

`config.py:436-442`:

| Phase | Tokens |
|---|---|
| understand | 2000 (planner-local 4000) |
| devise | 2000 |
| plan | 8000 |
| step | 16000 |
| reflect | 1500 |

## 9. Theory: one loop for everything

The GSL is the answer to "how does a general agent do work without a skill
library?" The bet is that **understanding → option generation → planning →
execution → evidence-based verification** is field-independent, and that what
varies by field is the *acceptance evidence* (§4), which is derived from the
environment (project markers, test runners, files, HTTP) rather than from a
taxonomy of skills. The loop therefore generalizes by construction: new domains
need new checks, not new code paths.
## Part II — Delegation: threads, workers, and the subagents

The agent pursues multiple goals in parallel by delegating bounded slices of itself.
Sources: `clio/threads/manager.py`, `worker.py`, `context.py`,
`clio/subagents/`, `config.py:239-242`.

## 1. Threads: bounded parallel selves

`ThreadManager` (`clio/threads/manager.py:107-377`):

- **Limits**: `max_concurrent = 4`, `max_depth = 2`, `mode = inprocess`
  (`config.py:239-242`) — depth 2 means a thread may spawn a thread, but not
  recursively beyond that [D-01, D-29]. Parallelism is deliberately small: each
  thread is a full working context with its own workspace and budget, not a cheap
  coroutine.
- **`spawn_spec`** (`manager.py:246-263`): reaps stale threads first, then **carves
  the budget** — `remaining/2` of the parent's budget goes to the child — and creates
  an intention for the delegated work **unless** the thread's role is `verifier`
  (a verifier does not own the work it judges).
- **Transport**: `inprocess` threads run via `asyncio.create_task`; otherwise a
  `thread.request` event goes over the bus to the **worker process**
  (`manager.py:203-263`).
- **Failure semantics**: a thread result with `failure_reason` in
  `_FAILED_RESULT_STATUSES {failed, unavailable, blocked, timeout, no-gateway,
  missing-intention}` **parks** the intention (`IntentionStatus.parked`) — the plain
  reading of a returned value, "the work is done," once closed failed pursuits as
  `done` (`clio/__main__.py:468-476`).
- **Orphan reaper**: threads older than `stale_after_s` (default 7200 s) are reaped
  and their intentions parked (`manager.py:176-243`) — a lost thread becomes a
  recoverable parked goal, not a silent leak.

## 2. The worker process

`run_thread` (`clio/threads/worker.py:153-482`) dispatches on role:

1. **`verifier`** → `_run_verifier` (§5 below).
2. **Subagent roles** `("foundry", "apex", "axiom")` (`worker.py:36`) → `_run_subagent`
   (§4), attempts default `0` (unbounded) and episode weight `0.6`
   (`worker.py:220,247`).
3. **Else** → `_pursue` (`worker.py:285+`): a miniature GSL — devise (T2, 1200
   tokens) → plan → **≤5 steps** (1500 tokens each, one repair turn), executed via
   `toolhost.execute` with idempotency keys `thread:step:n`, in an isolated
   workspace. The thread's prompt carries the identity + tool catalog + hard-limit
   prefix (`worker.py:326`) — a delegated self is the same kind of agent, not a
   stripped-down assistant.

Rate-limit handling: backoff 30→300 s for rate limits, 5→60 s for transient errors,
cap 0 = unbounded retries (`worker.py:43-49`); a `ThreadAnswerUnusable` result
(`worker.py:58`) triggers `_complete_json`'s one repair turn (`worker.py:115`).

## 3. Workspaces: worktrees and leases

`clio/threads/context.py:12-35`:

- **Git repositories** get a **git worktree**; everything else gets
  `~/work/.threads/<id>/` [D-29]. Delegated work never tramples the parent's
  working tree.
- **Workspace leases (3.2)**: `WorkspaceLease` / `WorkspaceLeaseManager`
  (`context.py:52-260`) formalize this as a first-class object — a lease is a
  `git_worktree` or a **hardlink snapshot tree**, carries `allowed_paths` and a
  `budget_limit_usd`, and has a lifecycle `active → committed | rolled_back`.
  - `commit_lease` is per-target-locked (two threads committing to the same target
    serialize).
  - `rollback_lease` restores via trash (`lease_rollback`) and snapshot `restore()`
    (`clio/guardian/snapshots.py`).
  - `GuardianGate.review()` enforces `allowed_paths` and `budget_limit_usd` on calls
    made under a lease (`clio/guardian/gate.py:_review_lease`) — a leased thread
    physically cannot write outside its fence or overspend.
- Leases persist in the `workspace_leases` table
  (`migrations/versions/0006_clio_3_2.py`).

## 4. The subagents

Three sibling codebases (directories `Foundry/`, `APEX/`, `AXIOM/`) are reachable as
delegation targets via tools `subagent.status / subagent.foundry / subagent.apex /
subagent.axiom` (`clio/subagents/tools.py`, each timeout `86400 s` — the subagent
reports its own verdict rather than the tool call timing it out).

### 4.1 The result contract

`SubAgentResult` (`clio/subagents/base.py:35-219`), statuses
`done / blocked / unknown / failed / timeout / unavailable`. The conversion rule in
`tools.py:38`: **only `done` is success.** Everything else is reported as what it is,
and only `done` closes work.

### 4.2 Foundry — the software factory

`clio/subagents/foundry.py:274`: gate-verified software factory. Runs in-process
(an `Orchestrator` + a `FoundryGatewayProvider` hopping to the gateway) or as
`python -m foundry`. `_ensure_git_baseline` (`foundry.py:211`) initializes a
`main` branch + `.gitignore` when absent (G000).

### 4.3 APEX — the desktop controller

`clio/subagents/apex.py:361`: desktop automation via the sibling APEX engine.
Exit codes `0=done, 1=crash, 2=unknown, 3=blocked, 130=failed` → mapped to the
contract (`apex.py:56-64`) — **exit 1 is a crash**, not "unknown", and it is honest
about that. Default `hidden` mode: the controller runs in a hidden session observed
by `ApexScreenRelay` (`apex_screen.py:858`), a **read-only** relay (GET-only HTTP,
SSE `/frames`, JPEG at default 2 fps, semantic node tree, `MAX_NODES 600`,
`MAX_DEPTH 12`) so a person can watch what the hidden controller is doing without
the controller being able to touch the observer.

### 4.4 AXIOM — the single-turn coding agent

`clio/subagents/axiom.py:431`: a coding agent driven one turn at a time through
`/api/snapshot`, `/api/model`, `/api/message` on a throwaway home dir and a free
port (`axiom.py:255-287`). It **refuses mock runs** in both in-process and
subprocess modes (`axiom.py:146,308`) — a coding agent that has never run its code
is not permitted to report success.

### 4.5 The bridge

`clio/subagents/bridge.py:181`: subagents speak to the gateway through its
OpenAI-compatible shim (`/v1/chat/completions`), `_MAX_TOKENS_CEILING = 32768`
(`bridge.py:36`, duplicated in `clio/gateway/compat.py:30`). The placeholder key
`"clio-gateway"` marks requests that came from a sibling engine.

## 5. Verifier threads

`_run_verifier` (`worker.py:261`): shown `goal + artifact[:6000]` **only** — no
conversation, no claims, no step history. It runs at `T3`. This is the independent
rung of the verification ladder ([06-work.md](06-work.md) §5) realized as a process
boundary: the judge is a different process, a different model family, and blind to
the work's self-report.

## 6. Budget allocation (3.2)

`allocate_child_budget` / `ev_weight = ev/cost` (`manager.py:28,35`): child threads
share the parent's carve **proportional to expected value**, capped by H2.
`spawn_ready_branches` dispatches parallel ready-node branches of an intention DAG
([09-version-3-2.md](09-version-3-2.md)) — parallelism is allocated by value, not
by request order.

## 7. Theory: delegation as identity carve

Each thread is a **carve of the same identity** — same identity kernel, same tool
catalog, same limits, a fraction of the budget, a fenced workspace, and its own
intention to close. This has three consequences:

1. **Safety composes.** A delegated thread cannot exceed its parent's reach: the
   lease fence and budget cap are enforced at the gate, so parallelism never
   multiplies risk.
2. **Honesty composes.** The `only done is success` rule and park-on-failure mean a
   failed delegation surfaces as a parked intention with its `failure_reason`, not
   as an optimistic closure.
3. **Attention composes.** Results return as `thread.result` percepts and compete
   for attention like everything else; the parent does not block on a child
   (except the deliberately blocking `subagent.*` tools, whose 24 h timeout hands
   verdict authority back to the subagent).
