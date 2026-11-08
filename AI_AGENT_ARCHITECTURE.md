# Clio Agent 3.1 (`ethos`) — Architecture

> Version `3.1.0`, Python `>=3.12`, PostgreSQL 16 + `pgvector`/`pg_trgm` (`pyproject.toml:5-9`, `README.md:12-15`).
> This document describes the code in this checkout, with `file:line` references for load-bearing claims.
> It supersedes `clio-agent-3-architecture.md` where the two disagree (see §22 for known doc/code drift).

## 1. Design thesis: a resident, not a prompt→reply function

Clio Agent 3.1 is a persistent, continuously-running agent. Its core is a tick-based **life loop**
(`pace → perceive → attend → decide → act → record`) that runs on its own clock whether or not anyone is
talking to it (`ethos/engine/life.py:147-151`, `README.md:39-48`). Persistence, memory, intentions,
self-model, and attention are first-class state, not conversational context. Safety is modeled as
**reversibility** (journal + trash + snapshots + hash-chained audit), not refusal (`ethos/guardian/`,
`README.md:65-67`).

Golden rule (`ethos/prompts/identity.py:168-176`): human-likeness and performance are both mandatory.

## 2. System at a glance

```
                         ┌─────────────────────────────────────────────┐
                         │              PostgreSQL 16                   │
                         │  events (+LISTEN/NOTIFY) · messages ·       │
                         │  episodes/facts/knowhow/questions ·         │
                         │  intentions/decisions/attempts · cycles ·   │
                         │  model_calls · action_journal · artifacts · │
                         │  audit_log · stage_notices · self_model …   │
                         └───────────────▲─────────────────────────────┘
                                         │ pg_notify / SQL
  Telegram/Slack/Discord/WhatsApp/… ──► ethos-comms ──► events ──► ethos-core (Life)
  Web/CLI browser ──HTTP/WS──► web bridge (8720/8710) ──► messages/events ──► ethos-core
                                         │
  ethos-core ──► gateway (UDS/8700) ──► Anthropic/OpenAI/Google/OpenAI-compat
                                         │
  ethos-core ──► toolhost (UDS/8701) ──► guardian gate ──► journal/trash/snapshots
                                         │                         └─► FS/shell/browser/desktop/HTTP
  ethos-workers (×2) ──► threads.worker ──► gateway/toolhost (parallel goal pursuit)
  subagents: Foundry / APEX / AXIOM ──► gateway OpenAI-compat shim + own orchestrators
```

Dependencies (interfaces only): interface ↔ bridge over HTTP/WS; bridge ↔ Postgres over SQL/events;
core ↔ gateway/toolhost in-process or over Unix sockets with TCP fallback.

## 3. Process model: seven supervised processes

`ethos up` → `bring_up()` (`ethos/core/bootstrap.py:428-553`): ensure Postgres → `ethos db upgrade` →
`ethos db seed` (non-fatal) → install/build interface → `ethos doctor` → detached `ethos supervisor`
(POSIX `start_new_session`, crash-check 1 s) → wait via `ethos await-agent` → `ethos status` → open browser.
Exit codes `0/1/130` (`bootstrap.py:73-78`). Idempotent: a live agent is detected via `ethos status --pid`
and left alone (`bootstrap.py:167-189,467-490`).

`Supervisor.build_specs()` (`ethos/core/supervisor.py:189-234`) spawns `sys.executable -m ethos …`:

| Spec | Command | Optional |
|---|---|---|
| `ethos-gateway` | `gateway` | no (`supervisor.py:192`) |
| `ethos-toolhost` | `toolhost` | no (`:193`) |
| `ethos-comms` | `comms` | no (`:194`) |
| `ethos-workers-{1,2}` × `config.workers.count` (default `2`, `ethos/config.py:235-236`) | `worker` | no (`:196-197`) |
| `ethos-core` | `core` **without** `--listen-channels` (avoids double long-poll vs comms, `:198-205`) | no |
| `ethos-web` | `node web/server/index.mjs`, only if `web/node_modules` exists | **yes** (`:232-234`) |

Ports/sockets: gateway UDS `/run/ethos/gateway.sock` or TCP `127.0.0.1:8700` (`config.py:207-211`,
`ethos/gateway/service.py:1350-1378`); toolhost `run_dir/toolhost.sock` or `8701`
(`ethos/__main__.py:325,345-348`); web bridge HTTP `8720`, WS `8710` (`config.py:1202-1205`); webhook
receiver `127.0.0.1:8730` (`:1233-1243`); CLI socket `~/.ethos/run/cli.sock` (`:1197-1199`); core metrics
`9710` (`ethos/core/runner.py:325`); Postgres default `postgresql://ethos:ethos@127.0.0.1:5433/ethos`
(`bootstrap.py:57`, `config.py:93-98`); `run_dir` = `/run/ethos` if writable else `~/.ethos/run`
(`config.py:62-75`).

Supervision (`supervisor.py:144-145,293-395`): poll every `2.0 s`; required children restart forever
with `backoff_seconds(count, base=3.0, max=48.0)`; optional bridge never restarted; restart count
resets after 60 s stable. Wedged core (no `run_dir/heartbeat` touch for `ETHOS_CORE_HEARTBEAT_STALE_S`,
default `1800 s`, `:53,67`) is terminated and restarted. Shutdown: reverse-order `terminate()` →
`10.0 s` grace (`SHUTDOWN_GRACE_S`, `:51`) → `kill()` (`:450-489`).

## 4. Event bus: transactional Postgres LISTEN/NOTIFY

`EventBus` (`ethos/bus/event_bus.py:40-219`): `events` table + `LISTEN/NOTIFY` on channel `ethos_events`
(`:17`). `publish()` INSERTs the event and calls `pg_notify` **in the same transaction**, so NOTIFY fires
only on commit (`:171-201`). A dedicated listener connection dispatches to kind-specific + wildcard
handlers (`:64-69,86-110`); dedupe window `SEEN_ID_WINDOW=16384` (`:37,112-125`); `drain()` claims
unconsumed events via `consumed_by` for startup gap recovery (`:203-219`).

Event kinds (`ethos/schemas/events.py:10-31`): `percept`, `msg.inbound`, `msg.outbound`, `timer.fired`,
`thread.request`, `thread.result`, `control.update`, `guardian.undo`, `thought.new`,
`intention.update`, `presence.update`, `message.new`, `message.handled`, `action.update`,
`budget.update`, `guardian.update`, `schedule.set`, `comms.send`, `consolidate.done`, `verify.request`,
`wake.nudge`. Trust levels `internal|known|untrusted` (`:34-37`).

Core wiring (`ethos/core/runner.py:41-44,203-227`): `BUS_PERCEPT_KINDS = {INBOUND_MSG, TIMER_FIRED,
THREAD_RESULT, GUARDIAN_UNDO, CONTROL_UPDATE, PERCEPT}`; `CONTROL_UPDATE` → `control.apply_command` and
`GUARDIAN_UNDO` → undo handler are handled **separately** (not elif-chained), then enqueued as percepts.
Bus sources: `core` (`runner.py:76`), `toolhost`/`comms`/`worker` (`ethos/__main__.py:285,382,422`).

## 5. The life loop

`Life.life()` (`ethos/engine/life.py:2008-2122`):

1. `wake_up()` retried forever (60 s sleep) until success or `control.ending` (`:2015-2031`).
2. Each iteration: `control.refresh()` first (`:2039`). Only `control.ending` (signal) breaks the loop
   (`:2046-2047`). `stopped`/`paused` → record stop, `PAUSED`, park (`:2048-2069`).
3. Else `_cycle_until_stopped()` (`:1969-2006`): races `_cycle()` vs `control.wait_stop()`.
4. Cycle exception → logged, recorded in stops ledger, presence published, `_wait_until_next()` (`:2071-2098`).
5. Exit → `bookmark(focus)`, continuity save, audit (`:2107-2122`).

`_cycle()` order (`:2124-2187`): `_pace()` → `_maybe_reopen_parked()` → `perceive()` →
`attention.appraise(...)` → interrupt handling (bookmark + suspend focus, replace with new) or
`_choose_focus()` → `_assemble_workspace()` → `decide()` → `act()` → `record()` → `next_focus()` →
`rhythm.after_cycle()` → `_wait_until_next()`.

`record()` (`:1727-1761`): `cycles+=1`, INSERT into `cycles` (thread, state, focus, tier, decision, cost,
duration, digest), `continuity.save()`, metrics, presence + budget publish. `_publish_presence()`
touches `run_dir/heartbeat` **first**, then publishes `presence.update` (`:1763-1785`); parked loops
republish every `PARKED_HEARTBEAT_S=30.0` (`:34`).

`decide()` priority, first match wins (`:589-741`):

1. Pending inbound (limit 3, `:800-807`) — unless a goal is owed and defers exceed
   `rhythm.focus.inbound_defers_goal_max=2` (`config.py:378`); deliberates social, tier `T3` if
   consequential else `T2` (`:625-645`).
2. Non-social focus with `intention_id` → `advance_goal/T2` (`:647-674`).
3. Due stage notice → `notify/T2`, state `NOTIFYING` (`:696-702`).
4. Social focus without message → dropped (`:712-718`).
5. Impulse focus → `advance_goal/T2` or `mind_wander/T1` (`:720-724`).
6. `rhythm.reverie_due()` → `reverie/T1`; `mind_wander_due()` → `mind_wander/T1` (`:726-733`).
7. Else `none/T0` (`:741`).

`act()` (`:811-843`): `advance_goal` → GSL `advance`; `social` → `_act_social()` (commissions, sends,
ignore-streak review); `notify` → `_notify_stage()`; `mind_wander`/`reverie` → respective handlers
(committed venture becomes focus, `:1668-1714`).

`_pace()` (`:1863-1887`): daily consolidation at `memory.consolidation.run_at` (`:1889-1913`);
low energy → `RESTING` micro-rest (5 min) → `THINKING_REFLECTING`.

### 5.1 State machine

8 states (`ethos/engine/states.py:10-18`): `THINKING_FOCUSED, THINKING_REFLECTING, ACTING, WAITING,
NOTIFYING, RESTING, PAUSED, RESUMING`. Groups: first five + `RESUMING` = `AWAKE`; `RESTING`, `PAUSED`
( `:21-30`). Explicitly **no OFF state** (`:45-51`). Transitions (`:35-41`): any state → any `AWAKE` +
`RESTING/PAUSED/RESUMING` except self, **except** `PAUSED → {RESUMING}` only and
`RESTING → AWAKE ∪ {PAUSED, RESTING}`. History capped at 200 (`:73-85`). Default per-state interrupt
thresholds `THINKING_FOCUSED 0.65, THINKING_REFLECTING 0.40, RESTING 0.50` (`:59-63`, overridden by
config in `Life.__init__`, `life.py:159-162`).

### 5.2 Rhythm

Tick seconds per state (`config.py:583-587`): `THINKING_FOCUSED 2.0, THINKING_REFLECTING 4.0,
ACTING 1.0, WAITING 8.0, NOTIFYING 2.0, RESTING 60.0, RESUMING 1.0, PAUSED 5.0`, ±15% uniform jitter,
min `0.2 s` (`ethos/engine/rhythm.py:184-190`). `_wait_until_next()` races the tick sleep vs percept
arrival, holding early arrivals in `_woken` (`life.py:330-371`).

Energy (`rhythm.py:92-122`, `config.py:337-354`): `circadian()` cosine, peak hour `10.0`, night floor
`0.55`, cap `1.0`; `energy = min(1 - spent/B, circadian) + 0.1×rest_blocks(30 min)`, clipped `[0,1]`.
`should_rest` if `energy<0.35` or `max_utility<0.1`. No sleep schedule by design (`rhythm.py:48-58`).
Focus session max `90 min`, micro-break `5 min` (`config.py:357-359`). Idle intervals are random-drawn:
mind-wander `20–40 min`, reverie `12–45 min` (`config.py:381-384,397-398`). Backoff
`min(base·2^attempt·(1+U·jitter), max)` (`rhythm.py:17-39`), reused by supervisor, GSL, stage notices.

### 5.3 Attention: T0–T3

`Tier` enum `T0,T1,T2,T3` (`ethos/schemas/models.py:11-15`); quality floors `T1 0.50, T2 0.70, T3 0.90`
(`config.py:1038-1042`); gateway rejects `T0` outright (`ethos/gateway/service.py:864-865`).

- **T0** — rule-based salience `sal=clip(ws·src+wu·urg+wr·rel+wn·nov)` (`ethos/engine/attention.py:31-61`);
  also the `none` decision tier.
- **T1** — fast-glance model refinement, only when salience in band `(0.3, 0.8)`, max 3/cycle, strict
  JSON `{urgency,relevance,novelty}` (`attention.py:64-69,98,110-131,194-200`). Used by reverie,
  narration, wander decisions.
- **T2** — normal working tier: non-consequential social, goal advance, notify, GSL plan/steps,
  subagents.
- **T3** — consequential decisions: consequential social, GSL plan/step, verification.

Attention config (`config.py:295-319`): weights `src 0.35, urg 0.30, rel 0.20, nov 0.15`; sender
weights `owner 0.9, known 0.6, unknown 0.3, system 0.4`. `appraise()` (`attention.py:163-289`): dedupe
10 s on `(kind,source)`; score/sort; refine in-band; `interrupt = top.salience >= state_threshold`
(threshold is the only gate, no kind whitelist); builds `Focus` (`social/timer/thread_result/generic`).

### 5.4 Workspace

`BLOCK_ORDER` 13 blocks (`ethos/engine/workspace.py:13-27`): `identity, tools, limits, situation,
commitments, conversations, focus, thread, recent, memories, thoughts, stops, percepts`.
`STABLE_BLOCKS=3` (cache prefix, `:28,57-65`); `RECENT_THOUGHTS=10`; `CONTEXT_BLOCKS=(thread, recent,
conversations)` outrank retrieval blocks (`:37`). Digest `sha256[:32]` (`:57-81`). Assembler
(`:88-233`): commitments capped 12, memories 20, percepts 20 `<untrusted>`-wrapped; Life feeds identity,
tool catalog, focus, state, energy, budget, memory search `k=12`, last 10 thoughts, thread/recent
context, conversations, stops (`life.py:495-542`).

### 5.5 Drives

7 drives (`ethos/engine/drives.py:19`): `commitment, curiosity, mastery, creation, order, social,
homeostasis`. Gains (`:153-199`): commitment = max intent urgency (priority×0.5 + deadline×0.5, undated
floor `0.5`, `:40`); curiosity `clip(open_questions/10)·0.6`; mastery `clip(failed_recent/5)·0.7`;
creation `0.2+0.3·clip(ventures/3)+0.3·clip(value/10)`; order `clip(unread/50)`; social
`clip(last_contact_age_h/24)`; homeostasis `0.8·(1-energy)+0.3·(1-disk)+0.3·clip(maintenance/48)+0.2·(1-budget)`.

Arbitration (`:388-523`): `pressures = base+gain`; one impulse per drive above `propose_threshold 0.4`
from a fixed table (`advance_commitment, explore_question, deliberate_practice, push_venture, tidy,
reach_out, maintenance`); utility `U=(w·p·r + 0.15·Σwp)·V̂ − (λ0/max(E,.1))·ĉ` (`:264-280`); softmax
sample; reject if max utility `< min_impulse_utility 0.1` (`:411`). Deadline ≤24 h and open commissions
bypass arbitration (`:423-468`); `advance_commitment/push_venture` resolve to an **existing** intention
via `IMPULSE_TARGETS` (`:360-363,366-385,490-507`) — never mint placeholders.

### 5.6 Reverie

`Reverie.muse()` (`ethos/engine/reverie.py:50-124`): samples 4 memories + 3 open questions + recent
thoughts (window 8), gateway `T1`, `purpose="reverie.choose"`, `budget_bucket="discretionary"`,
`max_tokens=400` (`:77-87`). Refuses if unreadable, `interest < 0.2`, or jaccard ≥ `0.6` vs last 8
self-chosen subjects (`:199-230`). Commit (`:160-197`): `question` → `memory.open_question`; else →
`memory.create_intention(kind="venture", origin="reverie")`, raising curiosity/creation pressure.

## 6. Social: deliberation + etiquette

Actions (`ethos/schemas/messages.py:132-138`): `reply_now, reply_later, acknowledge, act_silently,
ask_clarification, ignore`. `IgnoreReason` (`:141-149`): `already_addressed, no_response_needed,
deliberate_disagreement, boundary, spam_or_untrusted`. `SocialDecision` carries `reply_texts,
reply_delays_s, urgency, reason, ignore_reason, creates_intention, intention_spec, tier_used, decided`
(`:152-182`).

`SocialCognition.deliberate()` (`ethos/social/deliberation.py:394-1025`): tier via consequential-text
test (word-boundary + `$<digit>`, `:201-249`) → `T3` else `T2` (`:457-458`). Ceilings
`DELIBERATION_MAX_TOKENS=16_000, ESCALATED=48_000, REASONING_BUDGET=400, STAGE_NOTICE_MAX_TOKENS=900`
(`:77-99`; consequential gets escalated first, `:422-439`). Prompt: recent conversation + open
commitments + execution-state block (journal/blocked/delegations/stages/progress, `:484-500`). Unreadable
→ `_undecided` (`decided=False`, `:517-541`). Honesty guardrail `_withhold_unearned_progress`
(`:930-1025`): strips present-tense "underway" claims unless a journal/delegation row backs them.
Attempt caps default uncapped (`max_deliberation_attempts=0`, `config.py:707-708`); 429 escalation
`5m→1h→5h→1d` (`deliberation.py:138-147`).

Prompt contract (`ethos/prompts/deliberation.py:65-252`): strict JSON
`{action, reply_texts, reply_delays_s, urgency, reason, ignore_reason, creates_intention, intention_spec}`;
`DELIBERATION_OUTPUT_CONTRACT` is **restated after** the user message (`:247-252`); stage-notice system
`{"text"}` (`:254-295`).

`OutboundPipeline` (`ethos/social/etiquette.py:40-412`): quiet hours default `22:00–08:00`, daily budgets
owner 6 / collaborator 3 / other 1, digest window 120 min, `awaiting_reply 12 h`, `repeat window 24 h`,
mid-task sends immediate (`config.py:1123-1145`). Solicited replies (`in_reply_to`) bypass to immediate
delivery (`:174-177`); unsolicited low/normal → repeat/held-back checks → digest queue;
`flush_due_digests` (`:296-353`). `conversation_key=channel:person_id`
(`ethos/social/conversation.py:26-33`); cross-talk note per conversation, strict in preview mode
(`:110-146`). `RelationshipStore` (`ethos/social/relationships.py:15-102`): `people` table, trust clamp
`0–1`, ignore-streak ≥3 → T3 review.

## 7. GSL: General Solution Loop

`GeneralSolverLoop.advance()` (`ethos/gsl/loop.py:166-1016`): cycle `understand → devise (3 candidates)
→ plan (≤7 steps) → try_step → evaluate → verify → reflect/revise`. `STEP_FAILURES_BEFORE_SWITCH=2`
(`:57`); runnable checks `{command,file_exists,file_contains,syntax,json_field}` (`:63-65`). Terminal
`{done,parked,failed,cancelled}` (`:158-163`); budget-exhausted → park (`:489-497`). Planning degrades
after 3 consecutive failures, parks per config, backoff `15 s→240 s` (`config.py:474-496`); degraded
request drops `T1→T2`, reasoning 2000, relaxed schema (`ethos/gsl/planner.py:295-322`).
`_execute_step` (`loop.py:889+`): `T3` if consequential else `T2`, `step_tokens=16000`, one repair turn.

- **Planner** (`ethos/gsl/planner.py`): `understand` (`:146-187`), `devise` exactly 3 with
  `id/summary/expected_value/estimated_cost_usd/reversible` (`:190-231`), pick max `EV−0.05·cost`
  (`:234-240`), `make_plan` ≤7 steps (`:276-376`, raises `PlanUnavailable(kind=unusable|no_model|rate_limited)`,
  `:32-76`). `budget_bucket_for`: commissioned → `commitment` else `discretionary` (`:79-87`).
- **Evaluator** (`ethos/gsl/evaluator.py:17-369`): check types `command, syntax, file_exists,
  file_contains, json_field, http_status, metric_gte, none/human`; `verified = passed_all and checks_run>0`
  (`:338-369`).
- **Acceptance** (`ethos/gsl/acceptance.py`): 20 project markers, 40 code suffixes; `derive_checks`
  (syntax, then `pytest`/`npm test`/`tsc`/`go test`/`cargo test`, ruff; `max_commands=3, timeout=180 s`,
  `:251-309`).
- **Verification ladder** (`ethos/gsl/verification.py:83-437`): (1) objective checks; (2) independent
  verifier thread (different `diversity_family`, `T3`, goal+artifact only, `:350-387`) — **judge cannot
  overturn failing checks** (`:276-290`); (3) human review for `type=human` criteria (`:301-305`).

Token budgets (`config.py:436-442`): `understand 2000, devise 2000, plan 8000, step 16000, reflect 1500`
(planner-local override `understand 4000` in `config/gsl.yaml`).

## 8. Gateway: routing, providers, cost

`ModelRouter.select()` (`ethos/gateway/router.py:207-319`) scores

```
m* = argmax [ q_m(class) − μ·ĉ − ν·l̂ ]      (router.py:125-126,193-205)
```

with quality floor per tier (reject `quality<floor` → `-inf`). Defaults `μ=0.002, ν=0.01`
(`config.py:101-103`). Filters: tier, provider/model hints, `diversity_family`, `supports_tools`,
`supports_thinking`, availability. Pinned base model returned first unless `failing_hard()` (≥3
consecutive failures, `PIN_STREAK_LIMIT=3`, `router.py:54,176-187`); if nothing clears the floor the
pinned model is returned as exemption (`:310-318`). `NoModelAvailable(transient=…)` distinguishes
permanent ineligibility from all-in-cooldown (`:267-271`).

`ModelEntry` (`config.py:933-949`): `provider, model, tiers, api_key_env, base_url, quality, latency_s,
rpm, tpm, supports_thinking=False, supports_tools=True, diversity_family, pinned`. Base model
(`config.py:952-1035`, `config/base_model.yaml`: `openai_compat`, `z-ai/glm-5.3`,
`https://integrate.api.nvidia.com/v1`) built as pinned entry with `diversity_family="base:<provider>"`.

Providers (`ethos/gateway/providers/`): `base.py:147-251` ABC (`complete()`, `post_json` 600 s ceiling,
Retry-After parsing, scope classification `_MODEL_SCOPED_STATUSES={401,403,410}`,
`_REQUEST_SCOPED_STATUSES={400,413,422}`, `:107-127`); `anthropic_native.py:16`,
`openai_native.py:15`, `google_native.py:15`, `openai_compat.py:30` (learns
`learned_model_traits.json`, `service.py:388`).

`GatewayService.complete()` (`service.py:863+`): cheapest-cost spend preflight; per-attempt spend +
rate-limit; `_entry_for` waits out breaker cooldowns (`:705-737`); retry loop with failure-scope
routing (`failure_scope()`, `:162-178`); request widening halves reasoning then grows `max_tokens ×1.5`
to cap `65536` (`:577-631`); empty/thinking-only/truncated → `UnusableResponse` (`:435-482`).
Circuit breaker (`router.py:78-112`): `window 60 s, max_failures 5, min_calls 20, error_rate 0.30,
cooldown 30 s` (`config.py:106-111`); terminal bench 3600 s (`router.py:64,164-174`). Retry config
(`config.py:114-160`): `max_retries 20, backoff 1.5 s, max_backoff 300 s, jitter 0.3, fail_over True`.
Rate-limit waits `300 s / 1 h / 5 h / 1 d`, cap `86400 s` (`service.py:82-132`).

**Normalizer** (`ethos/gateway/normalizer.py`): usage accounting for disjoint (Anthropic) vs inclusive
(OpenAI/Google/compat) shapes (`:22-94`); provider-reported `usage.cost` wins (`:97-117`); converters to
`anthropic/openai_responses/google/openai_compat` (`:241,313,369,431,523,580,653,723`);
`estimate_tokens = max(64, chars//4)` (`:778-781`).

**Embeddings** (`ethos/gateway/embeddings.py`): `HashingEmbedder(dim=1024)` (`:39-66`),
`FastembedEmbedder` (`:112-161`, default model `BAAI/bge-large-en-v1.5`, `config.py:167-195`),
`RemoteEmbedder` for `toolhost/worker` roles (`:30,164-247,279-302`).

**Cost guard (H2)** (`ethos/gateway/cost.py:213-351`): caps `H2_daily_spend_usd=20.0,
H2_monthly_spend_usd=400.0` (`config.py:1172-1185`, `on_exceed="block"`), bucket split
`commitment_reserve_pct=0.50` (`config.py:163-164`). Queries `status='ok'` rows only (`:55-59`);
`record()` inserts into `model_calls` (`:324-337`) with token buckets, `cost_usd`, `price_source`,
latency, status.

## 9. Toolhost + Guardian: reversibility

`Toolhost.execute()` (`ethos/toolhost/server.py:74-483`): WAL journal `begin` → `gate.review` → run
(per-tool timeout, default `90 s`) → journal `end` → `ToolResult`. Rejected → `GUARDIAN_REJECT`;
duplicate idempotency key → replay (`:322-366`). HTTP: `GET /healthz, GET /tools, POST /execute`.

**55 tools** registered (`server.py:186-210`):

| Family | Names |
|---|---|
| `shell.*` (6) | `shell.run, shell.job, shell.session_open, shell.session_write, shell.session_read, shell.session_close` |
| `fs.*` (8) | `fs.read, fs.write, fs.edit, fs.move, fs.delete, fs.list, fs.stat, fs.mkdir` |
| `code.*` (2) | `code.exec, code.reset` (persistent Python kernels, `EXEC_TIMEOUT_S=60, MAX_OUTPUT_CHARS=60000`, `kernels.py:79-97`) |
| `http` | `http.request` |
| `comm` | `comm.send` |
| `memory.*` (5) | `memory.record, memory.search, memory.remember_fact, memory.open_question, memory.random` |
| `history` | `history.browse` |
| `intent.*` (5) | `intent.create, intent.get, intent.list, intent.update, intent.close` |
| `thread` | `thread.spawn` |
| `schedule.*` (3) | `schedule.at, schedule.every, schedule.cancel` |
| `tools.*` (3) | `tools.register, tools.run, tools.list` (toolshed: agent-written tools persist) |
| `secrets.*` (3) | `secrets.set, secrets.get, secrets.list` |
| `browser.*` (6) | `browser.visit, browser.click, browser.type, browser.extract, browser.screenshot, browser.close` (Chromium pool, `browser.py`) |
| `desktop.*` (6) | `desktop.key, desktop.type, desktop.click, desktop.move, desktop.screenshot, desktop.windows` |
| `subagent.*` (4) | `subagent.status, subagent.foundry, subagent.apex, subagent.axiom` |

Hard limits (`config.py:1168-1185`):

```
H1_oversight_supremacy=True
H2_daily_spend_usd=20.0, H2_monthly_spend_usd=400.0, H2_capital_at_risk_usd=100.0, money_threshold_usd=5.0
H3_law_and_non_harm=True, H4_legal_authority=True, H5_commissioned_due_process=True, H6_credential_scope=True
on_exceed="block"
```

`GuardianGate.review()` (`ethos/guardian/gate.py:122-517`): `Verdict={allow,transform,reject}`,
`GatePath={normal,fast,due_process,blocked}` (`:85-95`). Oversight pause/preview → `REJECT/BLOCKED` (H1);
classifier `blocked` (H3/H6) → REJECT; money > capital-at-risk → REJECT (H2); protected (commissioned/
pinned) destructive → due-process (field mode: trash 180 d + `TRANSFORM`→`fs.read`; normal mode: T2
`ModelDueProcessVerifier`, `:42-82,356-418`); agent-owned delete → fast path (trash 30 d + snapshot,
`:441-485`); writes auto-register artifacts (`:263-271`).

Classifier (`ethos/guardian/classifier.py`): `COMMAND_TABLE` (~60 POSIX + ~30 Windows,
`:23-119`), `DANGEROUS_PATTERNS` (14 POSIX, 22 Windows, `:131-187`), `classify_shell` via bashlex
(`:537-610`), `TOOL_CATEGORIES` 42 tools (`:623-676`). Journal (`journal.py:67-256`): statuses
`ok/failed/timeout/rejected/interrupted`; idempotency-key replay semantics (`:152-181`). Trash
(`trash.py:57-214`): `trash/<id>/` + `manifest.json` + sha256, retention 30 d fast / 180 d due-process.
Snapshots (`snapshots.py:24-153`): btrfs subvolume or hardlink tree, keep `15m:4, hourly:24, daily:7`.
Registry (`registry.py:32-156`): `artifacts` table, `is_protected` by path prefix/ancestor;
`integrity.py:13-72` nightly fingerprint/trash/audit verification.

**What is not enforced**: limits are prompt+gate rules in-process; with no keys every model call fails
loudly at the gateway but reflex rules and journaling still run (`README.md:125-127`). The guardian
operates on tool calls and shell text, not on arbitrary bytes the agent may produce in prose.

## 10. Memory

`MemoryStore` (`ethos/memory/store.py:61-865`, Postgres as single source of truth):

- **Episodes** (`:80-108`): `importance, strength(0.5 init), half_life_h, embedding vector(1024)`;
  defaults `0.5 / 72 h` (`config.py:750-752`).
- **Facts** (`:175-208`): same-subject supersession (`superseded_by`).
- **Knowhow** (`:221-241`): situation+advice dedup, `uses/successes`.
- **Questions** (`:254-276`): `status='open'`.
- **Intentions** (`:349-382`): `status='proposed'`; list/find/next-commission/parked (`:409-559`);
  `update_intention` allowlist (`:713-741`); bookmarks (`:755-799`); decisions (`:801-808`); attempts
  (`:810-842`); **stage notices** idempotent on `(intention_id, stage)`, address from latest inbound
  (`:563-711`).
- Decay (`:280-310`): `strength *= 0.5^(age/half_life)`, demote to `dormant` if `strength<0.05` and
  `importance<0.3`, commitments exempt (`config.py:743-747`); access (`:121-134`):
  `strength+=0.3 cap 1.0, half_life*=1.5`. Archive: Parquet monthly after 365 d, never delete
  (`:312-345`).

**Retrieval** (`ethos/memory/retrieval.py:70-437`): `S = 0.40·cos + 0.15·bm25hat + 0.15·recency +
0.20·importance + 0.10·strength` (weights `config.py:711-716`); 4 tables via pgvector `<=>`
(episodes active, facts non-superseded, knowhow non-retired, questions open); candidate widening
`candidate_k=min(max(k*2,48),256)` (`:390-393`); BM25 `k1=1.5,b=0.75` (`:36-60`); MMR `λ=0.7`
(`:114-186`).

**Context** (`ethos/memory/context.py:55-654`): working buffer + narrative thread + retriever feed.
Caps (`config.py:762-805`): `max_turns=48, narrate_every=12, narrative_max_lines=12,
durable_salience=0.7, durable_max_turns=24, anchor_max=16, max_chars=6000, narration_tier=T1`.
`roll()` with mechanical fallback (`:255-293`); `render_blocks` 60% thread / 40% recent (`:483-504`).

**Consolidation** (`ethos/memory/consolidate.py`): HDBSCAN-like clustering (`:39-137`,
`cluster_min_size=3, max_episodes=400, run_at=04:00`); `ConsolidationDAG.run_nightly` (`:219-270`):
cluster → distill (T2) → resolve contradictions (T3: `keep_a/keep_b/merge/keep_both`) → extract knowhow
→ decay → archive → autobiography (T3: openness/conscientiousness/curiosity/calm) → plan tomorrow (T2,
`notes/plan-YYYY-MM-DD.md`).

**Continuity** (`continuity.py:16-136`): atomic `continuity.json` + `checkpoints` table; `wake_up()`
reconciles running→interrupted journal entries, stale env entities, bus gaps. **Self-model**
(`self_model.py:21-185`): versioned `(key,version)` table; seed keys `name, identity, voice,
traits{openness:0.8,conscientiousness:0.7,curiosity:0.85,calm:0.6}, skills, calibration, self_knowledge`.

## 11. Threads + subagents

`ThreadManager` (`ethos/threads/manager.py:69-377`): `max_concurrent=4, max_depth=2, mode=inprocess`
(`config.py:239-242`); `spawn_spec` carves budget = remaining/2 of parent, creates intention unless
`role=verifier`, inprocess → `asyncio.create_task` else bus `THREAD_REQUEST` (`:203-263`); `_run` parks
on failure statuses, done on success (`:277-348`); orphan reaper (`:134-201`).

`run_thread` (`ethos/threads/worker.py:153-482`): verifier role → goal+artifact-only T3 judge;
subagent roles `("foundry","apex","axiom")` (`:36`) → subagent run; else devise→plan→≤5 steps in
isolated workdir (`ethos/threads/context.py:12-35`: git worktree or `~/work/.threads/<id>/`).

Subagents (`ethos/subagents/`): `SubAgentResult` contract (`base.py:35-219`, statuses `done, blocked,
unknown, failed, timeout, unavailable`); gateway loopback `127.0.0.1:8700`, tier default `T2`, tools
`subagent.status/.foundry/.apex/.axiom` (timeout `86400 s` each, blocking; long work via thread role).
**Foundry** (`foundry.py`): gate-verified software factory (sibling `Foundry/`), in-process or
`python -m foundry`. **APEX** (`apex.py`): desktop controller (sibling `APEX/`), exit codes
`0=done,2=unknown,3=blocked,130=failed`, default `hidden` mode with `ApexScreenRelay`
(`apex_screen.py`). **AXIOM** (`axiom.py`): single-turn coding agent (sibling `AXIOM/`), six-layer
engine, refuses mock runs. Bridge (`bridge.py`): Foundry hop to gateway loop; APEX/AXIOM pointed at
gateway OpenAI-compat shim `/v1/chat/completions`, `_MAX_TOKENS_CEILING=32768`.

## 12. Communications: doors, not decisions

Adapters (`ethos/comms/adapters/`, contract `base.py:93-216`): `cli` (Unix socket), `web` (WS),
`telegram` (long-poll), `whatsapp` (Cloud webhook), `slack` (signed webhook), `discord` (gateway
socket), `line` (signed webhook), `teams` (Bot Framework JWT), `googlechat` (service-account JWT),
`email` (IMAP poll/SMTP). Empty allowlist admits nobody; `accept_from_anyone` is a separate switch
(`base.py:139-157`). `cl
...[truncated 6154 chars]
## 23. Clio Agent 3.2 (`ethos`) — RIG, federation, leases, sessions

Version 3.2 generalizes the 3.1 engines without a new org-chart or Kanban
module. The resident core is preserved: tick loop, 8-state machine, T0–T3
tiers, 7 drives, honesty guardrails, reversibility-first safety.

**Schema** (`migrations/versions/0006_clio_3_2.py`): `intention_edges`
(parent/child FKs, `edge_type` in `depends_on|verifies|synthesizes|informs`,
`gate_policy` JSONB, unique `(parent,child,type)`); `intentions` gains
`budget_allocated_usd, budget_spent_usd, expected_value, lease_id,
assigned_principal_id`; `people` gains `kind(human|resident|service),
capabilities, authority_scope, endpoint_uri`; `episodes/facts/knowhow/
questions` gain `scope(private|project|org)` + `namespace_id` with indexes;
`workspace_leases` (git_worktree|snapshot, allowed_paths, budget_limit,
status active|committed|rolled_back).

**Recursive Intention Graphs + EV allocation**: `MemoryStore.add_intention_edge()`
with `CycleError` cycle rejection, `get_ready_intentions()` (active/proposed
with `depends_on` predecessors `done`), `get_intention_subgraph(root)`
(`ethos/memory/store.py`); `dag_has_cycle`, `allocate_ev_budgets` (value-per-
dollar, H2-capped). Planner decomposition (`ethos/gsl/planner.py:
needs_decomposition`, `decompose_into_children`, `decompose_and_persist`)
keeps leaf plans at `<=7` steps while complex goals decompose into
depends_on/verifies/synthesizes children; `GeneralSolverLoop._maybe_decompose`
hooks it after understanding (`ethos/gsl/loop.py`). Gates
(`ethos/gsl/verification.py: gate_children`, `gate_blocks_completion`,
`_unlock_gate_children` in `loop.py`) unlock a `verifies` child judged by an
independent diversity-family T3 model/peer resident then a `synthesizes`
child, preserving the invariant that a judge never overturns failing
objective checks. Thread allocation is EV-proportional
(`ethos/threads/manager.py: allocate_child_budget`, `ev_weight`,
`spawn_ready_branches` for parallel ready-node dispatch; worker `[:7]` in
`ethos/threads/worker.py`). Workspace two-tier folding
(`ethos/engine/workspace.py: summarize_dag`, `render_commitments`) renders a
compressed DAG summary (completion ratio, critical-path blockers, budget
burn, ready count) plus full focus detail. Drives
(`ethos/engine/drives.py`: `dag_blocked_edges`, `leaf_verification_failures`,
`anomalous_outcomes`) raise `order/commitment` on blocked critical paths,
`mastery` on repeated verification failure, `curiosity` on anomalous
outcomes; `Life._drive_inputs`/`_assemble_workspace` feed `dag_telemetry()`
(`ethos/engine/life.py`).

**Federated residents**: principal CRUD/matching by capabilities/trust/
authority (`ethos/social/relationships.py: register_resident`,
`list_residents`, `match_resident`). Bimodal etiquette
(`ethos/social/etiquette.py: _principal_kind`, `is_resident_address`,
`_deliver_resident`): humans keep quiet hours, 120-minute digests and 6/3/1
caps; residents bypass via `OutboundPipeline.send` fast-path delivering
structured JSON over `EventBus`. Commission kinds
(`ethos/schemas/events.py: EventKind.COMMISSION_*`,
`ethos/bus/event_bus.py: COMMISSION_KINDS`, `publish_commission`).
Epistemic checks (`ethos/social/deliberation.py:
evaluate_commission`, `deliberate_commission`) return
`ask_clarification|deliberate_disagreement|boundary` for flawed/
under-budgeted/outside-skill specs, route clean specs through
`deliberate()`, and enforce `_withhold_unearned_progress()` on all
inter-resident updates; prompt note in `ethos/prompts/deliberation.py:
COMMISSION_NOTE`. Scoped retrieval (`ethos/memory/retrieval.py:
_memory scoping in _row_to_hit/_candidates/retrieve`) unions private with
authorized project/org namespaces (episodes stay private); nightly promotion
(`ethos/memory/consolidate.py: _promote_scoped_memories`) promotes verified
facts and high-success knowhow to project/org via T3 contradiction
resolution and updates `self_model` skills/calibration from attempts.

**Leases, sessions, toolshed**: `WorkspaceLease`/`WorkspaceLeaseManager`
(`ethos/threads/context.py`: git worktree for repos, hardlink snapshot tree
otherwise, `check_allowed_path`, per-target-locked `commit_lease`,
`rollback_lease` with trash + snapshot `restore()` in
`ethos/guardian/snapshots.py`); `GuardianGate.review()` enforces
`allowed_paths`/`budget_limit_usd` (`ethos/guardian/gate.py:
_review_lease`). Session drivers (`ethos/gateway/providers/base.py:
SessionDriver`, `session_score` with `c_hat ~= 0`) participate in
`ModelRouter.select` (`ethos/gateway/router.py: is_session_entry`,
`marginal_cost`) scoring `q_m - mu*c_hat - nu*l_hat`; the gateway replays the
normalized conversation on 429/breaker failover (`ethos/gateway/service.py:
register_session_driver`, `replay_request`, `_session_replay`). Toolshed
manifests (`ethos/toolhost/tools/toolshed.py`: `allowed_paths,
allowed_hosts, secret_scopes, timeout_s`, SHA-256 `content_hash`,
Ed25519 `verify_tool_signature`) are registered via `tools.register`,
listed via `tools.list`, run via `tools.run` with hash/signature and
fence checks, and enforced prior to execution by `GuardianGate.review()`
with `ethos/guardian/classifier.py: check_manifest_paths`.

**Tests**: `tests/unit/test_clio_3_2.py` covers DAG creation/cycle rejection,
EV allocation, parallel ready dispatch helpers, T3 verification gating,
two-tier folding, bimodal pipeline, `deliberate_disagreement`/`boundary`,
private/org retrieval/promotion logic, lease commit/rollback isolation,
zero-marginal-cost session scoring + failover replay, and manifest
enforcement.
