# 06 — Work: the GSL, delegation, and the subagents

The General Solution Loop (GSL) is the one framework the agent uses to pursue any goal in
any field. This document covers the loop, its evidence standards, mid-turn steering, and the
delegation machinery: threads, workers, workspaces and leases, and the three sibling
subagents. Sources: `clio/gsl/`, `clio/threads/`, `clio/subagents/`, `clio/tools/catalog.py`.

## Part I — The General Solution Loop

## 1. The cycle

`GeneralSolverLoop.advance()` (`clio/gsl/loop.py:750-1010`):

```
understand → [maybe decompose] → devise (3 candidates) → plan (≤7 leaf steps)
  → prepare_next_step (steering) → try_step → evaluate → verify → reflect/revise → (repeat)
```

Terminal states `{done, parked, failed, cancelled}`; a budget exhaustion parks the goal.
`STEP_FAILURES_BEFORE_SWITCH = 2` (`loop.py:64`): two failures of the current step switch
the step, not the plan; the plan is revised in reflect/revise.

## 2. Understand → devise → plan

### 2.1 Understand (`clio/gsl/planner.py:146-196`, budget 2000 tokens)

The goal is restated with what is actually being asked, constraints, and what success looks
like. `_maybe_decompose` (3.2) hooks in here (`loop.py:845`, `:1981+`): a goal too complex
for one plan decomposes into child intentions ([09](09-version-3-2.md) §1.1).

### 2.2 Devise (`planner.py:198-252`, budget 2000 tokens)

**Exactly three** candidate approaches, each carrying `id, summary, expected_value,
estimated_cost_usd, reversible`. Selection:

```
pick argmax (EV − 0.05 · cost)
```

Three is not arbitrary: one candidate invites anchoring on the first idea, and five dilutes
a 2000-token budget. The `reversible` flag feeds the guardian's risk calculus.

Budget bucket: a commissioned goal spends from the `commitment` bucket, everything else
from `discretionary` (`budget_bucket_for`, `planner.py:79-87`) — owed work has reserved
funds ([02](02-safety-and-guardian.md) §5).

### 2.3 Plan (`planner.py:479-570`, budget 8000 tokens)

**`MAX_LEAF_STEPS = 7`** (`planner.py:254-255`). Failure modes raise
`PlanUnavailable(kind=unusable | no_model | rate_limited)` (`planner.py:32-76`). After
repeated planning attempts planning **degrades gracefully** (tier and reasoning budget step
down, schema relaxes) — a degraded plan beats no plan, and the park-reopen backoff brings
the goal back around.

`allow_empty_step = False` and `min_real_plan_steps = 1` (`clio/config.py:449-459`): a step
that called no tool did not succeed, and padding a failed call out with invented steps is
refused — the plan is what the model actually returned.

`model_hint` (`config.py:430-435`): when set, the solver's thinking calls (plan, step,
verify) carry the hint so they run at the strongest model without making every two-second
housekeeping call pay that model's latency.

## 3. Step execution

`_execute_step` (`loop.py:1313-1430`): tier `T3` if the step is consequential else `T2`,
`step_tokens = 16_000` (`config.py:439-441` — sized to hold a whole source file in one
action, which is what a coding step is), **one repair turn** — a failed tool call gets
exactly one chance to be corrected with the error in view ("repeating a call that has
already failed is not persistence").

Steps run through the toolhost with idempotency keys of the shape
`<thread id>:<step id>:<turn>` (`clio/threads/worker.py:521`) — a replayed cycle never
re-executes a side effect ([02](02-safety-and-guardian.md) §4.1).

## 4. Steering: mid-turn guidance

`prepare_next_step` (`loop.py:1250-1310`) runs **before every tool dispatch and step
transition**: it claims pending `steering_messages` rows atomically
(`FOR UPDATE SKIP LOCKED`, `clio/gsl/steering.py:86-123`), renders them into the step
context at high priority (`render_for_step`, `steering.py:196-216`), merges any
`constraint:` lines into the intention, and marks the rows applied. Stale claims from dead
runs are handed back by `expire_stale` (`steering.py:134-143`) — a finalized run's guidance
is never lost and never replayed twice.

Full description of the message-routing half (which messages become steering) lives in
[10](10-sandboxes-steering-and-policy.md) §4.

## 5. Evaluation and acceptance

### 5.1 Objective checks (`clio/gsl/evaluator.py`)

Check types: `command, syntax, file_exists, file_contains, json_field, http_status,
metric_gte, none/human`; commands run with a 120 s default timeout (`:191`).
`evaluate_objective` (`:425-456`):

- `passed` and `verified` are different answers and the caller needs both.
- **`verified = passed_all AND checks_run > 0`** — zero checks run is never verified,
  closing the "I checked nothing and it passed" hole.
- A check that printed nothing "verified no file" and says so (`:233-236`) — the evidence
  is quoted, not asserted.

For software work, `derive_checks` (`clio/gsl/acceptance.py:251-310`) recognizes project
markers and code suffixes and derives: a syntax check (in-process where a parser exists for
`.py/.json/.yaml/.toml`, else `node --check` / `bash -n`), then the project's real test
runner (`pytest` / `npm test` / `tsc` / `go test` / `cargo test`) and `ruff`, capped at
`max_commands = 3` with a 180 s timeout. The acceptance criteria come from the project, not
from the model's confidence.

## 6. The verification ladder

`VerificationLadder` (`clio/gsl/verification.py:159-345`), three rungs in order:

1. **Objective checks** — §5.1. **A model judge can never overturn failing objective
   checks** (`gate_blocks_completion`, `verification.py:54-75`): the gate reads the notes
   and refuses to let an independent pass stand over a failing check.
2. **Independent verifier** — a thread running a **different `diversity_family`** model at
   `T3`, shown **only the goal and the artifact** (`_pick_independent_provider`,
   `verification.py:78-107`; `clio/threads/worker.py:261-284`, artifact[:6000]).
   Independence is structural: different model family, no conversation context, no access
   to the claim that the work was done.
3. **Human review** — for `type=human` criteria, and forced when difficulty ≥ 0.6 or the
   checks could not be run at all (`verification.py:291-345`): unverifiable work becomes
   `needs_human`, never a verdict nobody heard.

**Rung 2 without a window** (`clio/gsl/verifier_inspect.py`): the old design showed the
verifier `artifact[:16000]` — one slice, everything past it invisible, and a verifier that
said "pass" about what it saw. The 3.1 inspection replaces that with map/reduce:

- **Map**: the artifact's modified files are split into **whole-covering chunks** (every
  character of every modified file lands in exactly one chunk; files kept whole when they
  fit; a single line longer than the target chunk is its own chunk rather than a 40 MB one),
  and a judge call reads each chunk with the goal, the criteria and the chunk's place in the
  whole (`chunk_file`, `verifier_inspect.py:133-180`).
- **Reduce**: chunk verdicts merge under a rule the judge cannot bargain with — **any
  `fail` fails the artifact, any `needs_human` demands a human, `pass` requires every chunk
  to have passed** — and the merged verdict carries the per-chunk trail with provenance.
- **Cap honesty**: beyond `MAX_MAP_CHUNKS`, the plan switches to summaries-plus-head per
  chunk and says so, rather than silently sampling (`verifier_inspect.py:37-44`).

## 7. Stage notices and parking

- **Stage notices**: `_notice_stage` (`loop.py:2947+`) publishes a notice when an intention
  reaches `done / parked / awaiting_review`. Idempotent per `(intention_id, stage)`,
  addressed to the latest inbound sender, decided by the 900-token prompt, and run through
  the outbound pipeline's mid-task immediate rule ([05](05-social.md) §5, §8).
- **Parking** (`_park`, `loop.py:2849-2940`) is not failure. A parked goal keeps its state,
  its partial work, and its budget accounting; the reopen sweep
  (`_maybe_reopen_parked`, `clio/engine/life.py:1777+`) retries it later. The worker parks
  (not closes) on any failed thread result — the plain reading of a returned value, "the
  work is done," once closed failed pursuits as `done` (`clio/__main__.py:547-576`).
  **An intention is closed `done` only when the verification ladder says so.**

## 8. Token budgets

| Phase | Tokens (`clio/config.py:425-441`) |
|---|---|
| understand | 2 000 |
| devise | 2 000 |
| plan | 8 000 (73% of plan calls were ending on the old 2 000 ceiling) |
| step | 16 000 (sized for a whole source file in one action) |
| reflect | 1 500 |

## Part II — Delegation: threads, workers, workspaces

## 9. Threads: bounded parallel selves

`ThreadManager` (`clio/threads/manager.py:112-500`):

- **Limits**: `max_concurrent = 4`, `max_depth = 2`, `mode = inprocess`
  (`clio/config.py:240-243`) — depth 2 means a thread may spawn a thread, but not
  recursively beyond that. Parallelism is deliberately small: each thread is a full working
  context with its own workspace and budget, not a cheap coroutine.
- **`spawn_spec`** (`manager.py:250-420`): reaps stale threads first, then **carves the
  budget** — `allocate_child_budget` (`manager.py:40-78`, EV-weighted in 3.2) — and creates
  an intention for the delegated work **unless** the thread's role is `verifier` (a verifier
  does not own the work it judges).
- **Handle retention**: finished handles are kept in a bounded cache (64) rather than
  forever — each handle pins its spec and result, "which for a thread that browsed or
  searched is the largest object the agent produces" (`ThreadsConfig.retention`,
  `config.py:245-258`).
- **Failure semantics**: a thread result with `failure_reason` in
  `_FAILED_RESULT_STATUSES {failed, unavailable, blocked, timeout, no-gateway,
  missing-intention}` **parks** the intention (`manager.py:27-31,339-385`).
- **Orphan reaper**: threads older than `stale_after_s` (default 7200 s) are reaped and
  their intentions parked (`manager.py:181-245`) — a lost thread becomes a recoverable
  parked goal, not a silent leak, and its slot is reclaimed ("four orphans and the agent can
  no longer delegate anything", `config.py:260-276`).
- **`spawn_ready_branches`** (`manager.py:427-490`): dispatches parallel ready-node
  branches of an intention DAG, capped by `max_concurrent` — parallelism is allocated by
  value, not by request order ([09](09-version-3-2.md) §2).

## 10. The worker process

`run_thread` (`clio/threads/worker.py:153-560`) dispatches on role:

1. **`verifier`** → `_run_verifier` (`worker.py:261-284`): shown `goal + artifact[:6000]`
   **only** — no conversation, no claims, no step history. Runs at `T3`.
2. **Subagent roles** `("foundry", "apex", "axiom")` (`worker.py:36`) → `_run_subagent`
   (`:186-260`), attempts default `0` (unbounded) and episode weight 0.6.
3. **Else** → `_pursue` (`worker.py:285-560`): a miniature GSL — devise (T2, 1200 tokens) →
   plan → **≤5 steps** (1500 tokens each, one repair turn), executed via the toolhost with
   idempotency keys of the shape `<thread id>:<step id>:<turn>` (`worker.py:521`), in an
   isolated workspace. The thread's prompt carries the identity + tool + hard-limit prefix
   — a delegated self is the same kind of agent, not a stripped-down assistant.

Rate-limit handling: backoff 30→300 s for rate limits, 5→60 s for transient errors,
`call_attempts = 0` (unbounded) with the `no-gateway` exception ending the task honestly
(`config.py:278-292`); a `ThreadAnswerUnusable` result triggers the one repair turn.

## 11. Workspaces and leases

`clio/threads/context.py:48-300`:

- **Git repositories** get a **git worktree**; everything else gets a
  copy-on-write/hardlink **snapshot tree** under `~/work/.threads/<id>/`. Delegated work
  never tramples the parent's working tree.
- **`WorkspaceLease`** (`context.py:53-107`) formalizes this as a first-class object: a
  lease is a `git_worktree` or a `snapshot` tree, carries `allowed_paths` and a
  `budget_limit_usd`, and has a lifecycle `active → committed | rolled_back`
  (DB checks in `migrations/versions/0006_clio_3_2.py:74-93`).
  - `commit_lease` is per-target-locked (`context.py:219-236`).
  - `rollback_lease` (`:237-291`) restores via trash (`kind="lease_rollback"`) and the
    lease's own tree. **`SnapshotManager.restore()` is not part of this path** — a stated
    gap in [02](02-safety-and-guardian.md) §4.3.
  - `GuardianGate.review()` enforces `allowed_paths` and `budget_limit_usd` on calls made
    under a lease (`clio/guardian/gate.py:249-349`), including the **home-reach rule**: a
    path outside the lease is allowed when it is inside the host user's home and not inside
    any *other* lease's root — a lease is the isolation guarantee *between projects*, not a
    fence around the person's own reach (`gate.py:212-247`).
- Project sandboxes mount the lease root at `/workspace`
  ([10](10-sandboxes-steering-and-policy.md) §1).

## 12. The subagents

Three sibling codebases (directories `Foundry/`, `APEX/`, `AXIOM/`) are reachable as
delegation targets via tools `subagent.status / subagent.foundry / subagent.apex /
subagent.axiom` (`clio/subagents/tools.py`, each `timeout_s = 86400` — the subagent reports
its own verdict rather than the tool call timing it out, `tools.py:87-91`).

### 12.1 The result contract

`clio/subagents/base.py:28-53`: statuses `done / blocked / unknown / failed / timeout /
unavailable`. `unknown` is "may or may not have happened — never retried blind"; the
conversion rule in `tools.py:41-45`: **only `done` is success.** Everything else is
reported as what it is, and only `done` closes work.

### 12.2 Foundry — the software factory

`clio/subagents/foundry.py:44,187-220`: gate-verified software factory. Runs in-process (an
`Orchestrator` + a `FoundryGatewayProvider` hopping to the gateway) or as
`python -m foundry`. `_ensure_git_baseline` (`:212`) initializes a `main` branch +
`.gitignore` when absent.

### 12.3 APEX — the desktop controller

`clio/subagents/apex.py`: desktop automation via the sibling APEX engine. Exit codes map
**once**, with the crash path named (`apex.py:50-69`):

| Exit | Status | Reading |
|---|---|---|
| 0 | `done` | verified completion |
| 1 | **`unavailable`** | the crash path — an exception escaped before any terminal state; "reading it as a failed task is how a missing pyobjc import became a report that a desktop change had been attempted and had failed" |
| 2 | `unknown` | outcome-unknown stop — never retried blind |
| 3 | `blocked` | safe block, needs a human decision |
| 130 | `failed` | failed or operator-stopped |

Default `hidden` mode: the controller runs in a hidden session observed by
`ApexScreenRelay` (`clio/subagents/apex_screen.py:246-430`), a **read-only** relay (GET-only
HTTP, SSE `/frames`, JPEG frames, semantic node tree) so a person can watch what the hidden
controller is doing without the controller being able to touch the observer. A capture
failure is reported, not hidden (`apex_screen.py:566-573`).

### 12.4 AXIOM — the single-turn coding agent

`clio/subagents/axiom.py`: a coding agent driven one turn at a time through
`/api/snapshot`, `/api/model`, `/api/message` on a throwaway home dir and a free port. It
**refuses mock runs** in both in-process and subprocess modes (`axiom.py:25-31,146-155`):
with no base model configured, AXIOM silently routes to its built-in deterministic mock
engine, and "a run answered by the mock engine is not a delegation — it is the mock's
confident-sounding fiction", so both modes refuse to report a run whose reasoning engine is
the mock. A coding agent that has never run its code is not permitted to report success.

### 12.5 The bridge

`clio/subagents/bridge.py`: subagents speak to the gateway through its OpenAI-compatible
shim (`/v1/chat/completions`), `_MAX_TOKENS_CEILING = 32768` (duplicated in
`clio/gateway/compat.py`). The placeholder key `"clio-gateway"` marks requests that came
from a sibling engine.

## 13. Budget allocation (3.2)

`allocate_child_budget` / `ev_weight = ev/cost` (`manager.py:33-78`): child threads share
the parent's carve **proportional to expected value**, capped by H2.
`allocate_ev_budgets` (`clio/memory/store.py:166-198`) allocates intention budgets by value
per dollar across a subgraph, always H2-capped. Money flows from the person, through the
period cap, through the commitment reserve, into the graph, into threads.

## 14. Theory: delegation as identity carve

Each thread is a **carve of the same identity** — same identity kernel, same tool sheet,
same limits, a fraction of the budget, a fenced workspace, and its own intention to close.
Three consequences:

1. **Safety composes.** A delegated thread cannot exceed its parent's reach: the lease fence
   and budget cap are enforced at the gate, so parallelism never multiplies risk.
2. **Honesty composes.** The `only done is success` rule and park-on-failure mean a failed
   delegation surfaces as a parked intention with its `failure_reason`, not as an optimistic
   closure.
3. **Attention composes.** Results return as `thread.result` percepts and compete for
   attention like everything else; the parent does not block on a child (except the
   deliberately blocking `subagent.*` tools, whose 24 h timeout hands verdict authority back
   to the subagent).

## 15. What this document does not claim

- **The verifier still sees a 6 000-char artifact head in the thread path** (`worker.py:261`)
  — the full-artifact chunked inspection is the rung-2 implementation for the loop's own
  verification ([10](10-sandboxes-steering-and-policy.md) §7); both exist, and the thread
  verifier's window is the older, smaller one.
- **`max_concurrent = 4` is a soft ceiling on *inprocess* concurrency**: in bus mode the
  workers are separate processes and the cap is enforced at spawn time, so a worker that is
  already running a task is not preempted by it.
- **Subagent timeouts are 24 h, not a watchdog**: a subagent that hangs holds a thread slot
  until the orphan reaper's 7200 s window or the 86400 s tool timeout, whichever comes
  first; neither inspects the subagent's internal progress.
- **Decomposition is gated on the model**: `needs_decomposition` judges difficulty from the
  model's own outputs, so a model that under-reports difficulty gets a 7-step plan for a
  40-step goal; the mitigation is the reflect/revise pass, not a static complexity estimate.
