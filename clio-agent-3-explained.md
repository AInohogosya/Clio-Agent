# How Clio Agent 3 Works

**Clio Agent 3 Beta** — Python package `ethos`, `3.0.0-beta.3`, released
2026-10-03 JST (`ethos/__init__.py`, `pyproject.toml:7-8`).

> *An agent that is there when you aren't. Not a function that maps a prompt
> to a reply.*

Every other agent is a function: prompt in, reply out, nothing in between.
This one is a **resident**. It has its own computer, its own memory, its own
intentions, its own sense of time. It runs on a clock of its own whether or
not anyone is talking to it, thinks when nobody is talking, remembers across
restarts, talks as an equal (answer / answer-later / ask / acknowledge / work
silently / decline with reason), and is safe by reversibility rather than by
refusal.

Identity constant rendered into every prompt: *"Silence from people is when
I am most myself."* (`ethos/prompts/identity.py`) — because an agent that
believes it is idle has no reason to ever speak first.

This document describes the whole program as built: runtime, life loop,
memory, models, work, tools, safety, channels, interface, and operations.
File references are `path:line`.

---

## 1. Running it

```bash
pip install -e .   # once — Python >=3.12
ethos up           # every time — from any directory
```

`ethos up` (`ethos/__main__.py:110`, `ethos/core/bootstrap.py`) is the whole
command. It:

1. brings up PostgreSQL 16 + pgvector + pg_trgm if missing
   (`scripts/ensure_postgres.sh`: existing `$ETHOS_DSN` → Docker
   `pgvector/pgvector:pg16` → local Homebrew cluster at `127.0.0.1:5433`),
2. applies Alembic migrations, seeds the self-model,
3. installs/builds the interface if missing,
4. starts **seven supervised processes** detached from the terminal,
5. waits until the agent has published a sign of life *after* start
   (`ethos await-agent`, not merely a pid),
6. opens the page.

Idempotent: run it on a live agent and it says so and leaves it alone.
Exits 0 when supervisor + bridge are up (even with no usable model, so
Settings stays available), 1 on failure, 130 on Ctrl-C — the agent keeps
running in its own session by design.

Other commands (`ethos/__main__.py`, `Makefile`):

```bash
ethos status          # is the agent there? (sign of life, not pid)
ethos stop            # SIGTERM supervisor (POSIX) / stop-file + taskkill (Windows)
ethos logs            # supervisor + children in one prefixed stream
ethos doctor          # config, DB, pgvector, migrations, keys, model probes, doors, filesystem
ethos supervisor      # full 7-process model in foreground (agent development)
ethos core            # everything embedded in one process (loop development)
ethos core --listen-channels  # + hold channel doors (single-process dev only)
ethos gateway | ethos toolhost | ethos comms | ethos worker
ethos db upgrade | ethos db seed | ethos version
make up|run|run-dev|status|stop|logs|doctor|migrate|seed|test|lint|clean
```

`ethos status` answers one question from the agent's own `presence.update`
row in `events` (15-min window, `ethos/core/status.py:AGENT_ABSENT_S`): a
supervisor with dead children and a bridge serving last-known state both look
healthy otherwise. The browser uses the same window, so they cannot disagree.

Requirements: Python 3.12+, PostgreSQL 16 (+`vector`, `pg_trgm`) — the one
hard dependency — Node 20+ only for `interface/` + `web/` bridge. The agent
runs headless without them. `playwright install chromium` and the fastembed
model `BAAI/bge-large-en-v1.5` (~1.2 GB, `config/ethos.yaml:49`) download more
than a wheel.

---

## 2. System overview

```
                ┌──────────────────────────────────────────────────┐
                │                   PostgreSQL 16                  │
                │  events (+LISTEN/NOTIFY) · messages · memory ·    │
                │  intentions · cycles · journal · audit · models  │
                └──────▲──────────────▲──────────────▲─────────────┘
                       │ SQL / events │ SQL / events │ SQL / events
   Telegram etc. ──► ethos-comms ─────┘              │              ┌─ browser / terminal
   WhatsApp/Slack/                                   │              │  interface/ (@project-phone/*)
   Discord/Email ──► (doors, hear)                   │              ▼  web/server/index.mjs (stateless bridge)
                                                     │              ── HTTP/WS ──► Postgres/events
   ethos-core (Life: pace→perceive→attend→decide→act→record)
     ├─ engine  (rhythm, attention, states, drives, reverie)
     ├─ social  (deliberation → etiquette → comms_out.send)
     ├─ gsl     (understand→devise→plan→execute→verify)
     ├─► gateway  (in-process or unix socket :8700 / TCP fallback)
     │     ├─ router → provider (Anthropic/OpenAI/Google/compat HTTP)
     │     └─ spend guard, breaker, retry, embeddings, secrets
     └─► toolhost (in-process or toolhost.sock / TCP)
           └─ guardian gate → journal → 52 tools → FS / browser / net
   ethos-worker ×2 ── thread.request ──► threads/worker ──► gateway/toolhost
   Foundry / APEX subagents ── OpenAI-compat shim (gateway/compat.py) ──► gateway
```

Coordination is **not RPC**. It is a **transactional event bus**:
`events` table + `LISTEN/NOTIFY` (`ethos/bus/event_bus.py:40`).
`EventBus.publish()` (`event_bus.py:171`) does `INSERT events … RETURNING id`
+ `pg_notify(ethos_events,{id})` on the **same connection** — atomic with the
business row when a `conn` is passed, `NOTIFY` delivered only on commit.
Receivers `_on_notify → _fetch_and_enqueue → _dispatch_loop`
(`event_bus.py:86,95,140`) with a 16384-id seen-window dedupe and per-kind +
wildcard handlers whose exceptions are logged, never propagated.
`drain()` (`event_bus.py:203`) recovers startup gaps via
`consumed_by` array. 21 kinds (`ethos/schemas/events.py:10`):
`percept, msg.inbound/outbound, timer.fired, thread.request/result,
control.update, guardian.undo, presence.update, message.handled,
budget.update`, etc. `percept_from_event()` (`events.py:79`) splits
`sender_class/sender_person/urgency/summary` from payload and marks
`is_untrusted` from trust.

Perception is event-driven (arrives immediately) but processing is
**tick-driven** (drained at the top of each cycle). Hybrid.

### The seven processes (`ethos/core/supervisor.py:163`)

Built by `build_specs()`: `ethos-gateway`, `ethos-toolhost`, `ethos-comms`,
`ethos-worker-1..N` (`config.workers.count`, default 2), `ethos-core`
(**without** `--listen-channels`), optionally `ethos-web`
(`node web/server/index.mjs` by absolute path, only if `web/node_modules`
exists and `node` is found — optional child, never restarted once exited).

Supervisor (`supervisor.py:113`): starts all, polls every 2.0s
(`POLL_S`), restarts crashed non-optional children with
`backoff_seconds(count,3.0,48.0)` (`supervisor.py:281`), stable ≥60s resets
count, gives up at 5 (`MAX_RESTARTS`). Shutdown reverses children with
10s grace then `kill()` (`supervisor.py:364`).

`ethos-comms` holds every door; `ethos-core` only answers on them
(`ethos/core/runner.py:60,299`). The dev flag `--listen-channels`
(`ethos/__main__.py:179`) is opt-in for the single-process run
(`make run-dev`): two long-polls on one Telegram bot = Telegram conflict and
a channel that looks configured and receives nothing.

Control plane (`ethos/core/control.py:63`): stop/pause/emergency files +
DB lifecycle row. `load()` merges DB + files (stopped never inherited,
preview inherited); `apply_command()` handles
`pause_all/pause_actions/resume/stop/emergency_stop/preview_on/off`;
`refresh()` runs at the top of every cycle (`life.py:1155`). The toolhost
gate reads the **same row** with `owns_row=False` (`__main__.py:267`) so a
toolhost restart cannot clear a real stop — a gate that cannot see stop or
preview gates nothing.

Scheduler (`ethos/core/scheduler.py:17`): persisted timers in
`env_entities kind=schedule`, 1s poll, `schedule_at/schedule_every(≥1s)/
cancel`, fires as `timer.fired` with `urgency 0.5, salience 0.5`.

---

## 3. The life loop

`ethos/engine/life.py:109`: `perceive → attend → reflect → decide → act →
record`, never exits. `LifeDeps` wired by `CoreRuntime` (`runner.py:187`).
Starts `RESUMING`, `wake_up()` (`life.py:152`) restores `Focus` from the
continuity snapshot if its intention is still open, restores context,
`rhythm.mark_awake()`, transitions to `THINKING_REFLECTING`, audits +
`PRESENCE_UPDATE`.

`life()` (`life.py:1146`): `wake_up()`, then `while True`:
`control.refresh()` → if stopped: bookmark, save `stopped` snapshot, exit →
if paused: `PAUSED → _parked()` (30s presence heartbeat) `→ RESUMING →
THINKING_REFLECTING` → `try _cycle()` except `CancelledError: raise` except
`Exception: log + presence + wait` — **a failing cycle is a failed cycle,
never death**.

`_cycle()` (`life.py:1207`):
`_pace → perceive → attention.appraise → focus update → _assemble_workspace
→ decide → act → record → next_focus → WAITING transitions →
rhythm.after_cycle → wait_until_next`.

- `_pace()` (`life.py:1077`): maybe consolidate, `should_rest()` →
  `RESTING → THINKING_REFLECTING`; no sleep window — no bedtime.
- `perceive()` (`life.py:191`): non-blocking drain of `_percept_queue`
  (filled by `CoreRuntime._wire_bus().on_event`, `runner.py:203`, for
  `BUS_PERCEPT_KINDS = INBOUND_MSG,TIMER_FIRED,THREAD_RESULT,GUARDIAN_UNDO,
  CONTROL_UPDATE,PERCEPT`).
- Focus: if `appraisal.interrupt`: bookmark + `new_focus` (`life.py:1227`);
  elif no focus: `_choose_focus()` via drives.
- `decide()` (`life.py:392`) priority:
  1. pending inbound → `social.deliberate()` immediately (suspends
     non-social focus; `T3` if `is_consequential_text()` else `T2`),
  2. focus has `intention_id` → `advance_goal/T2`,
  3. social focus, no message → `social/T2/social=None`,
  4. impulse focus → `advance_goal/T2` or `mind_wander/T1`,
  5. `reverie_due()` → `reverie/T1`,
  6. `mind_wander_due()` → `mind_wander/T1`,
  7. else `none/T0`.
- `act()` (`life.py:498`): `advance_goal` via `_advance_goal()` + GSL,
  `social` via `_act_social()`, `mind_wander`, `reverie`; records
  `decision.thoughts`, `duration_ms`.
- `record()` (`life.py:978`): `cycles+=1`, `INSERT cycles`,
  `ContinuityManager.save(thread,cycle,state,focus,energy,spent,context)`,
  `metrics.observe_cycle`, presence + budget publish.
- `next_focus()` (`life.py:1034`): `goal_done/parked → None`;
  `social → commission focus` or resume suspended focus; else keep.

### Rhythm, states, attention, drives

- **Rhythm** (`ethos/engine/rhythm.py:184`): `tick = base[state] ±15%
  jitter, min 0.2s`. Defaults (`config/rhythm.yaml:76`):
  `FOCUSED 2, REFLECTING 4, ACTING 1, WAITING 8, NOTIFYING 2, RESTING 60,
  RESUMING 1, PAUSED 5`. Energy (`rhythm.py:109`):
  `E = min(1−sd/Bd, circadian) + rest_boost`, clipped `[0,cap]`, cosine wave
  around `peak_hour 10`, `night_floor 0.55` — a floor, not a countdown to a
  bed it never goes to. `mind_wandering 20/40/3`, `reverie 12/45`,
  `gap/backoff/due_daily` helpers, `PARKED_HEARTBEAT_S 30`.
- **States** (`ethos/engine/states.py:10`): 8 —
  `THINKING_FOCUSED, THINKING_REFLECTING, ACTING, WAITING, NOTIFYING,
  RESTING, PAUSED, RESUMING`. All `AWAKE` except `RESTING/PAUSED`;
  all-to-all transitions except `PAUSED → RESUMING` only. History
  `deque(200)`.
- **Attention** (`ethos/engine/attention.py:35`):
  `sal = clip(ws·src + wu·urg + wr·rel + wn·nov)`,
  weights `src .35 urg .30 rel .20 nov .15` (`rhythm.yaml:1`);
  senders `owner .9 known .6 unknown .3 system .4`.
  `rel` = embed cosine or Jaccard fallback; `nov = 1−cosine` or 0.5.
  `refine_salience()` (`attention.py:93`, T1, `max_tokens 120`) recomputes
  `{urgency,relevance,novelty}` only for the middle band `[.3,.8]`, max
  3/cycle. `appraise()` (`attention.py:145`) dedupes `(kind,source)/10s`,
  scores, sorts, thresholds by state
  (`FOCUSED .65 REFLECTING .40 RESTING .50 default .65`), re-sorts; interrupt
  iff top ≥ threshold **and** kind in `INTERRUPTIBLE_KINDS =
  msg.inbound,timer.fired,control.update,guardian.undo,thread.result`.
  `msg.inbound → social / Respond to X` (`attention.py:243`).
- **Drives / reverie / workspace**: `drives.py` proposes foci
  (`commitment .2 curiosity .25…`, `λ0 .08 τ .15 propose .4 bypass 24h`);
  `reverie.py` re-reads yesterday on schedule (the 02:15 self-repair:
  notices a promised-but-unbuilt reply and builds it); `workspace.py`
  assembles the stable cached context + tool-catalog block each cycle.
  Quiet hours manufacture no work — in the kernel, not wished for.

---

## 4. Social cognition: deciding what to say

### Deliberation (`ethos/social/deliberation.py`, `ethos/prompts/deliberation.py`)

Every inbound message is INSERTed (`record_inbound`) then deliberated.
Contract (`deliberation.py prompts:118`): strict JSON only —

```json
{"action": "reply_now|reply_later|acknowledge|act_silently|ask_clarification|ignore",
 "reply_texts": ["..."], "urgency": "low|normal|high|critical",
 "reason": "...", "ignore_reason": "IGNORE_REASONS",
 "creates_intention": false, "intention_spec": {"title": "...", "desired_end_state": "...", "deadline_iso": null}}
```

The contract block is restated **after** the user message
(`DELIBERATION_OUTPUT_CONTRACT`, `prompts:146,263`) — the last thing read,
not the first — because long messages ending in a question pull the model
into answering the person and dropping the envelope (5/14 prose without it,
0/24 with it on the 2600-char probe). `_ASK_AGAIN` restates it again on
retry; the retry also says the last answer could not be read.

Pipeline (`deliberation.py:258`): T0 ignore rules (empty → `ignore/
no_response_needed`; unknown + link-only <220ch → `ignore/spam_or_untrusted`)
→ system prompt (an equal that may wait; `creates_intention` independent of
`action`; claim underway only if `Execution state` shows journaled work;
`Journaled/blocked|parked` or `unavailable` rendered) → `_ask()`
(`deliberation.py:615`, 2 attempts, different ceilings; skips 2nd on
unreachable/429 — time not space) as `ModelRequest Tier T3-if-consequential
else T2 / social.deliberate` (`deliberation.py:335,669`;
`DELIBERATION_MAX_TOKENS 8000` ceiling-not-reservation,
`REASONING_BUDGET 400` as endpoint `reasoning.max_tokens`,
`ESCALATED 24000`, `MAX_DELIBERATION_ATTEMPTS 3`,
`MAX_TRANSIENT_ATTEMPTS 12`).

Parsing is adversarially careful (`deliberation.py:1092`): try every `{`,
last object with `action` wins; cut-off recovery keeps closed members and
drops the half-written one (a half-sentence is never delivered);
`reply_texts` list-only, uncapped, stripped, blanks dropped.
Recovery (`deliberation.py:758,1238,1326`): pure prose → deliver the model's
own words as the reply; wrong-key object (`{"permission":…}` about the
*subject*) → use words under `reply/response/answer/message/text` one level
down. Both force `reply_now + normal + creates_intention=False` with a
`recovered` provenance — a recovered "I'll do it" is a promise with nothing
behind it, the failure the prompt exists to prevent, so the object is asked
for again first. `note/summary/body`, `{}`, and provider-truncated halves are
never sent. Unreadable → `_undecided()` (`deliberation.py:1022`):
`act_silently + decided=False + reply_texts=[]` — nothing fabricated.
`_withhold_unearned_progress()` (`deliberation.py:559`) strips claims work is
underway when `readable and !something_happened` (journal + open intentions
checked in `_execution_state`, `deliberation.py:468`); emptied → `decided=False`.
Reply-action without text → `act_silently`. The deliberation is handed real
tool names (`tool_names_line`) and told its words precede the work, so it no
longer reports "I can't write files" with `fs.write/fs.edit/shell.run`
registered.

**Decline ≠ failure.** A decline stays in the transcript with a reason. A
failure (no model, provider raised, unparsable) is a turn with
`decided=False` on `message.handled`: the surface reads "The agent could not
answer" (not "chose not to reply"), it does not count against the streak, is
**not** retired, and retries (`pending_inbound`: `social_decision NULL OR
decided=false AND attempts<3|12 AND next_retry_after<=now`,
`deliberation.py:866`; `record_failure` merges attempts and rate-limit waits,
`mark_handled` never overwrites `decided=false`).

### Etiquette: the sending pipeline (`ethos/social/etiquette.py`)

`OutboundPipeline` (`etiquette.py:40`): `can_contact()` (critical bypasses
quiet hours with audit; high respects them; else quiet → hold; per-relation
daily budget by local-day `COUNT outbound sent today`), `send()` routing
(`etiquette.py:146`): `in_reply_to != None` → immediate `_deliver` (an answer,
not an interruption, bypasses all silence gates); `intention_id +
midtask_sends_immediate (on)` → `_send_midtask` (commissioning "I'll do it"
is itself a turn, so updates would otherwise meet "own word unanswered"
forever — skips digest/unanswered, keeps repeat + quiet + budget);
`low|normal + person` → `_why_not_speak_first` (duplicate within
`repeat_window_h 24h`? own last word unanswered within
`awaiting_reply_h 12h`?) else `_enqueue_digest`; `normal` not allowed →
digest; else suppressed. `_hold` records `status=held` + audit (held ≠ sent,
transcript never claims it was said); `flush_due_digests` on age ≥ window or
length ≥ 4, rechecking hold + contact, merging with `\n\n`; `_record`
INSERTs outbound `messages` + `MESSAGE_NEW`. Conversations keyed
`{channel}:{person_id}` (`conversation.py:26`, last 8 live / 6 turns /
72h); cross-talk rule (`cross_talk_note`, `identity.py`): connections assumed
unknown; preview mode (`/preview on`, same `ControlPlane.preview` the tool
gate refuses on) forbids even confirming another conversation exists.

---

## 5. Work: GSL, tools, verification

`GeneralSolverLoop.advance()` (`ethos/gsl/loop.py:298`) advances
`intention.resume_state` (JSONB — survives restarts) through
`understand → devise(3) → pick_approach → make_plan(3–7) → try_step →
evaluate → verify → reflect/revise` (`loop.py:70`).
`MAX_STRATEGIES 3`, switch after 2 step failures, park after exhaustion
(`loop.py:48,418`); `done/parked/failed/cancelled` terminal,
`awaiting_review/waiting` not (`loop.py:62`); `proposed→active` on first
advance; budget-exhausted parks; `_park()` writes `parked_reason`,
`close_intention(parked)`, `open_question(0.7)`; `_save_resume` keeps
bookmark + last 8 artifacts.

Planning (`ethos/gsl/planner.py`, `prompts/gsl.py:181,201,222`):
`understand` (T3 if `commissioned_by`/venture/commitment, else T2;
`commitment/discretionary` buckets), `devise` exactly 3, `pick_approach`
`max(expected_value − .05·cost)`, `make_plan` 3–7 steps (degraded 1–3, checks
optional, never pad; `was_truncated → PlanUnavailable`; empty descriptions
dropped; <1 real step raises). `PlanUnavailable` carries
`unusable/no_model/rate_limited + retry_after_s`; shared backoff survives
restart via `resume[retry_after]` (`loop.py:151,277`; `gsl.yaml:57`:
degrade 3, park 6, 15s/240s/.25, degraded budget 2000; rate-limited waits
5m/1h/5d/1d).

Execution (`loop.py:587`): `ModelRequest T2/T3, step_tokens 16000`, max 6
`{tool,args}` actions per step; truncation and
`allow_empty_step:false` empty steps are `step_failed` unless explicit
`done:true`; each action gets
`ToolContext{idempotency_key={intention}:{strategy}:{step}:{idx}}`
(`loop.py:706`); a raising tool is recorded `ok:false` (not unwound) so the
step's own machinery counts/reflects/switches; `verify_step_checks`
(`command/file_exists/file_contains/syntax/json_field` via `_step_check`)
can fail the step with stderr/stdout; results → `SolverResult.actions`,
`resume_state.artifacts` (cap 20), `ContextLayer.note_actions()`
(`memory/context.py:144`); `_reflect()` saves lessons/knowhow(2)/questions(2).

`Toolhost.execute()` (`ethos/toolhost/server.py:189`):
unknown → NOT_FOUND → `ActionJournal.begin` WAL (`guardian/journal.py:82`,
`idempotency_key` unique; `DuplicateAction → ok:true{idempotent,prior}`) →
`GuardianGate.review()` → timeout execution → `journal.end`
(`ok/rejected/timeout/failed`); reject → `guardian_reject`; digest capped
2000ch; `auto_register_commission`; `ACTION_UPDATE` publish.
`GET /healthz`, `GET /tools` (catalog), `POST /execute`.
52 builtins (`server.py:103`; categories `classifier.py:473`):
`shell.run/job/session{open,write,read,close}` (6),
`fs.{read,write,edit,move,delete,list,stat}` (7), `code.{exec,reset}` (2,
persistent `KernelSession`, 60k caps, `kernels.py:29`),
`http.request`, `comm.send`,
`memory.{record,search,remember_fact}+open_question+random` (5),
`intent.{create,get,list,update,close}` (5), `thread.spawn`,
`schedule.{at,every,cancel}` (3), `tools.{register,run,list}` (3, toolshed:
`~/toolshed/<name>/tool.yaml` + stdin/stdout JSON subprocess — the eleventh
repetition becomes a capability), `secrets.{set,get,list}` (3),
`browser.{visit,click,type,extract,screenshot,close}` (6, Playwright),
`desktop.{key,type,click,move,screenshot,windows}` (6),
`subagent.{status}+foundry.run+apex.run` (3).
Full `name(params*)` rendered once in the stable workspace prefix
(`identity.py:442`); steps get a pointer (`loop.py:855`).

Verification, three rungs (`gsl/verification.py:81`):
Rung 1 `ObjectiveChecker` (`gsl/evaluator.py:17`):
`command/syntax/file_exists/file_contains/json_field/http_status/metric_gte/
none/human/unknown`, every result `passed+observed`
(`none/human/unknown → passed:true observed:false`; `command` cwd+120s;
syntax py/json/yaml/toml/node/bash); empty checks → `passed:true
verified:false`; `verified = passed && ran>0`.
`acceptance.py` derives checks from `changed_paths` (fs paths + absolute code
paths in shell; `CODE_SUFFIXES`; project root = nearest marker, never `$HOME`;
syntax-first + ≤3 project commands: pytest
`sys.executable -m pytest -q -p no:cacheprovider` on touched tests, npm/tsc/go/
cargo, ruff; 180s timeout); `uncovered_paths` recorded;
`unverified_is_failure` (`gsl.yaml:43`): code changed + !verified/uncovered →
fail.
Rung 2 independent verifier if `!passed OR consequential
(commissioned/venture/commitment/difficulty≥.6) OR criteria-without-checks`:
`Thread depth:1` else `run_model_verifier` T3 with `diverse_provider_hint`
anti-echo; pass can never overturn failed-check/blocked (records
contradiction).
Rung 3 human criteria → `awaiting_review` (audit only, no composed message).

---

## 6. Gateway: thinking as a routed, budgeted utility

`GatewayService.complete()` (`ethos/gateway/service.py:627`):
`SpendGuard` check → `ModelRouter.select()` → `provider.complete()` →
record/billing. T0 reflex never calls the gateway (`service.py:713`).

- **Routing** (`ethos/gateway/router.py:94,138`):
  `m* = argmax_m [ q_m(class) − μ·ĉ − ν·l̂at ]`, `-inf` if `quality < floor`;
  `ĉ = costs.estimate` (default 5000in+800out), `μ .002 ν .01`
  (`config/ethos.yaml:25`). Floors (`models.yaml:1`): T1 .50 T2 .70 T3 .90.
  Eligibility (`router.py:165`): tier membership, provider/model hints,
  `diversity_family`, `tools→supports_tools`,
  `thinking_budget→supports_thinking`, excludes, `is_available`
  (breaker + key). Pinned base model wins if eligible, exempt from
  score/floor (`router.py:178,217`). Hint miss retries hints-cleared.
  Catalogue e.g. `sonnet-4-5[T2,T3] .93/6s, haiku[T1,T2] .78/2.5s, o3[T3]
  .92/8s, gpt-4.1[T2,T3] .88/5s, 4o-mini[T1,T2] .62/2s, gemini-pro[T3] .91/7s,
  flash[T1,T2] .72/2.5s, deepseek[T2] .72, llama-70b[T1] .58,
  local-vllm[T1] .50`. Default base
  (`base_model.yaml:12`): `openai_compat z-ai/glm-5.3 @integrate.api.nvidia.com,
  q1.0/5s, thinking+tools`, prepended by `sync_base_model()`
  (`service.py:268`); `~/.ethos/base_model.yaml` wins; env key wins over file.
- **Providers** (`gateway/providers/`): `Provider` ABC (`base.py:103`) +
  `normalizer.py` pairs; `PROVIDER_CLASSES` (`service.py:40`); Anthropic,
  OpenAI, Google natives + OpenAI-compat. `_readable()`
  (`service.py:357`): empty/truncated (`normalizer.py:120`) →
  `UnusableResponse(request_scoped, counts_against_model=False)`.
  `_widen_request()` (`service.py:480`): halve reasoning/thinking to floor/off,
  then grow `max_tokens` to cap. Reasoning budgets separated deliberately:
  `DELIBERATION_REASONING_BUDGET 400` as endpoint `reasoning.max_tokens` (not
  `thinking_budget`, which would disqualify non-thinking models); unknown
  params dropped after one rejection; both `reasoning_content` spellings read
  so thinking-only replies are visible.
- **Resilience**: `CircuitBreaker` (`router.py:50`): opens after `max_failures
  5 in window 60s` OR `error-rate .30 over min_calls 20`, clears after
  `cooldown 30s`. `NoModelAvailable(transient)` distinguishes no-catalogue
  (fail fast) from all-eligible-in-cooldown (wait out with backoff,
  `service.py:597`). `ProviderError.scope` (`base.py:57`): `model` (401/403
  credential — fail over only, never stay), `request` (400/413/422),
  `provider` (429/timeout/5xx). Backoff `base·2^attempt·(1+rand·jitter)`
  capped (`client.py:18`, `service.py:406`); 429 schedule
  `300/3600/18000/86400s` cap 86400 (`service.py:89`);
  `RATE_LIMIT_MAX_WAIT 300`, `LONG_WAIT 10`. `_next_entry()`
  (`service.py:439`): exclude failed, honor `fail_over`; client retries
  transport only, not 502.
- **Cost (H2)** (`ethos/gateway/cost.py:25`):
  `(in·pin + cached·pcached + out·pout + reasoning·pout)/1M, round6`;
  e.g. sonnet `3.00/.30/15.00`, haiku `.8/.08/4`, mini `.15/.075/.6`
  (`pricing.yaml`; defaults `.35/.07/1.4 $/M`). `SpendGuard`
  (`cost.py:40`): daily/monthly caps from `permissions.hard_limits
  (H2 daily 20 / monthly 400)`; `commitment_cap = daily·reserve_pct .50`,
  `discretionary = rest`; `check(bucket,est)` before call raises
  `BudgetExceededError → 402`; sums `model_calls status=ok` per day/month/
  bucket; no-DB = no-op. Out-of-band `deploy/oob/oob_guardian.py`
  (hypervisor side) watches the same $20/$400 with kill switch.
- **Embeddings** (`ethos/gateway/embeddings.py:112`): `FastembedEmbedder`
  ONNX local, L2-normed, `BAAI/bge-large-en-v1.5` dim 1024 (1.2 GB;
  `bge-small 384/.067GB` needs `dim:384` + widening migration —
  `assert_model_dim`, `embeddings.py:87`). `HashingEmbedder` (blake2b
  2-bucket + log-tf, dim 1024) for fallback/tests
  (`ETHOS_EMBEDDING=hashing` needs no model). `build_embedder` falls back
  except dim-mismatch raises. `toolhost/worker` never own ONNX arenas —
  `RemoteEmbedder` over gateway `/v1/embed → {vectors,dim}`
  (`embeddings.py:164,279`; `service.py:1001`).
- **Secrets** (`ethos/gateway/secrets.py:109`): agent sees only
  `{{secret:KEY}}` handles; `SecretVault.substitute()` expands real values
  only on HTTP to allowed hosts.
- **Compat shim** (`ethos/gateway/compat.py:166`): OpenAI-compatible
  `/v1` for Foundry/APEX — defaults Tier T2, `max_tokens 4096 cap 32768`,
  catalogue model as `model_hint`, `response_format` → system instruction.
  Native `ModelRequest.tools → NormalizedResponse.tool_calls` fully
  implemented here; the internal Self loop uses the JSON-contract path.

With no keys configured the agent still starts, journals, runs reflex rules —
every model call fails loudly at the gateway.

---

## 7. Memory: what it remembers and how

PostgreSQL 16 + pgvector + pg_trgm, 20 tables
(`migrations/versions/0001_initial_schema.py:27`):
`events, episodes(+vector(1024) HNSW), facts, knowhow, questions,
intentions, decisions, attempts, env_entities, self_model, people,
checkpoints, cycles, model_calls, action_journal, artifacts,
messages{id,ts,direction,channel,conversation_key,person_id,text,
social_decision,delivery}, audit_log (hash-chained), experiments,
value_ledger`.

- **Write/read** (`ethos/memory/store.py`): spacing on access
  (`strength+=.3 cap1.0, halflife×1.5`); decay
  (`strength×.5^(h/halflife)`, demote if `strength<.05 & importance<.3`,
  commitments exempt); archive >365d to monthly Parquet (never delete).
- **Retrieval** (`ethos/memory/retrieval.py:83`; `config/memory.yaml:1`):
  `S = .40·cos + .15·BM25̂ + .15·recency + .20·importance + .10·strength`;
  recency `.5^(dh/168h)`; BM25 `k1=1.5 b=.75` max-normalized; MMR `λ.7`
  rerank; `candidate_k 48 → top 24`; 4-table pgvector `<=>` prefilter
  (episodes/facts/knowhow/questions).
- **Working memory** (`ethos/memory/context.py:54`): rolling buffer
  (`max_turns 48`) + first-person narrative thread (`narrate_every 12, max
  12 lines`; overflow → episode `importance .6/336h`); narrator T1 300tok,
  fail → mechanical tally; ordering `index + salience·2N`; render
  `thread 60% / recent 40%`, `max 6000 / detail 600 chars`; snapshot persists
  narrative + last 30 turns + 3 loops; `note_actions()` feeds tool results
  into next-cycle context. The agent reads the recent tail of every live
  conversation plus this context every cycle — steps as well as deliberations.
- **Continuity** (`ethos/memory/continuity.py:16`): state-not-transcript;
  atomic `continuity.json` (tmp+fsync+rename) + `checkpoints` row **every
  cycle**. `wake_up()` (`continuity.py:63`): reload snapshot/focus, mark
  `running → interrupted` journal rows, `stale → unverified` env,
  `bus.drain(core-wake)`; reports `reconciled/missed/focus/env_checks`.
  Kill the process; the reasoning comes back.
- **Consolidation** (`ethos/memory/consolidate.py:176`, the 04:00 DAG):
  last-24h episodes (max 400) → `hdbscan_like_cluster(min3,
  mutual-reachability+MST+stability)` → per-cluster T2 distill →
  `record_fact` → pairwise T3 contradiction (`keep_a/b/merge/both`) →
  last-2d attempts → knowhow → decay/demote/archive → T3 autobiography
  (≤200w, traits clamped 0–1, keep last 30) → day-plan md.
- **Self-model** (`ethos/memory/self_model.py:21`, identity never in model):
  versioned `self_model` table
  (`name/identity(values+description)/voice/traits{open .8 consc .7 cur .85
  calm .6}/skills/calibration/self_knowledge`); every `set()` bumps
  version+reason+`PRESENCE_UPDATE`; `history()` per key; `seed_defaults()` +
  `_adopt_configured_name()` replaces only `seed`-authored names, never a
  person's rename; `identity_document()` builds the kernel from DB.

---

## 8. Safety: reversibility, not refusal

No sandbox. Filesystem reach = the running user's (probed by writing real
files — mode bits lie under macOS privacy; `ethos doctor` names `DENIED`
paths and the Full-Disk-Access remedy, `__main__.py:910`).

`GuardianGate` (`ethos/guardian/gate.py:118`) makes actions **undoable**, not
denied. `review()` (`gate.py:143`):

- `pause_actions → REJECT BLOCKED H1`; `preview → REJECT` (same
  `ControlPlane.preview` the cross-talk rule reads);
- `blocked ∈ categories → REJECT H3/H6`;
- money (`gate.py:232`): `> H2_capital_at_risk 100 → REJECT H2`,
  `> threshold 5 → audit T3-floor`;
- `destructive → _review_destructive`; `write/move + commissioned →
  auto_register`.

Hard limits (`config/permissions.yaml:43`, text `prompts/identity.py:484`):
`H1 supervisory-override, H2 money (daily 20/monthly 400/at-risk 100/
threshold 5), H3 destructive-control, H4 persistence, H5 commissioned due
process, H6 auth-scope` — all true/enforced outside the mind.

Classification (`ethos/guardian/classifier.py`):
`COMMAND_TABLE` (posix+windows: `rm/del/erase/Remove-Item → delete`,
`mkfs/format/diskpart/cipher → irreversible`, `chmod/icacls/takeown →
permission`); `DANGEROUS_PATTERNS` (`rm -rf /`, `dd /dev/`, `mkfs`,
`chmod 777 /`, fork-bomb, nmap/hydra, useradd, shadow, force-push →
blocked H3/H6 else irreversible; Windows case-insensitive incl. recursive
`Remove-Item`, `del /s`, drive-root delete, `format`, `diskpart clean`,
`cipher /w`, `reg delete/Run`, `vssadmin shadows`, `bcdedit`,
`EncodedCommand`); path extraction skips fd-duplication redirects (`>&1`
is an int, not a path — the `2>&1` crash that killed whole cycles is fixed
at both layers: classifier skips descriptors, `_execute_step` catches raising
tools as one bad call).

Due process (`gate.py:256`): commissioned path →
`ModelDueProcessVerifier` (T2, 200 tokens, fail-closed) checks consistency
with `{uri,commissioned_by,intention,tool,args}`; unsupported → `REJECT
DUE_PROCESS H5`; supported → `trash.move(due_process,180d)` +
`TRANSFORM → fs.read` (trashed path, `undo_ref`); agent-owned delete →
`trash.move(fast_path,30d)` + pre-delete snapshot → same transform; other
irreversible → `ALLOW NORMAL` audited/journaled.

Journal (`guardian/journal.py:82`): write-ahead `begin(running)` before
`gate.review`, `end(ts_end/status/digest/undo_ref)` after; DB or fsync JSONL.
Trash (`guardian/trash.py:57`): sha256 fingerprint + manifest,
`fast 30d / due_process 180d`, collision-safe restore, `purge_expired`.
Snapshots (`guardian/snapshots.py:24`): btrfs else tree-hardlink,
`pre-delete` reason, prune `minutes_15:4/hourly:24/daily:7`.
Registry (`guardian/registry.py:32`): `commissioned/pinned` protection by
prefix/ancestor, fingerprint-verified, `auto_register_write`.
Audit (`observability/audit.py:33`): hash-chained, seeded genesis; structlog
JSON with secret regex masking (`observability/logger.py:24`); Prometheus
(`metrics.py`) + OpenTelemetry. **No encryption at rest.**

The interface can queue undo (`/api/actions/:id/undo`) and close intentions —
never set status or edit actions (a second unreviewed writer).

---

## 9. Channels: doors, not decisions

A channel adapter is a door, not a decision. Every message from any door
reaches the life loop by one road (`inbound_to_bus`, `comms/host.py:733`:
`record_inbound + publish INBOUND_MSG`) and is scored by one attention model.

Every door closed until opened (`config/channels.yaml`):
`cli enabled:true socket ~/.ethos/run/cli.sock`,
`web enabled:true http 8720 / ws 8710`,
`telegram/whatsapp/slack/discord/email/shared-webhook enabled:false`.
**Empty allowlist admits nobody.** `accept_from_anyone` (off everywhere) is a
separate switch — "nobody configured yet" must never read as "everybody
welcome" on an agent holding a shell. Telegram groups stay refused via
`allow_groups` (broadcast ≠ conversation); admitting ≠ trusting (budgets
still apply; unknown senders score `urgency .2` vs known `.3`,
`attention.py:202`).

- `CommsHost.plan()` (`comms/host.py:205`, no mutation): creds via
  `channels.credentials()`; telegram needs token; whatsapp/slack need
  `webhook.enabled` + shared loopback receiver by path; discord socket-only;
  `reconcile()` close-before-open by fingerprint; `watch_channels()` 2s /
  rescan 8s — a saved door needs no restart.
- `ChannelAdapter` (`comms/adapters/base.py:93`): `start` = speak, `listen` =
  hear (only `ethos-comms` listens). `_accepts_id`: empty → False,
  `accept_from_anyone → True`, else member. `_destination` prefix-enforced.
  Telegram (`adapters/telegram.py:62,330`): groups need `allow_groups` even
  under anyone; refuses with id; `send` requires `tg:`; poll 5s;
  `mark_seen_update 4096/drop 1024`.
- **Who it is from**: `From: Ada Lovelace (@ada, tg:…) via telegram` —
  Telegram names/handles on-update; WhatsApp `contacts[].profile.name`;
  Discord `global_name|username`; Slack `users.info real_name` (costs the one
  API call needing `users:read`, refusals cached); Email `From:` display.
  Address never lost (`person_id` prefix-checked per door); names trimmed,
  control-stripped, length-capped, never proof of identity (anyone can be
  `owner`).
- **Where sends go** (`config/people.yaml:3`): `people[].channels` maps
  door → address (`tg:/wa:/slack:C/D/dc:`; Discord/Slack address by channel —
  a DM channel ≈ person+conversation; `web/cli` need none — the reader is the
  address). Missing address → `channel_unaddressed` before queueing (never a
  row claiming an unsent conversation); `GET /api/channels` reports contacts.
- **Settings → Messaging** writes `0600` `$ETHOS_HOME/channels.yaml` +
  `people.yaml` (key-by-key merge over `config/*.yaml`), reports whether +
  which-var + 4-char hint, never values; environment still wins (rotation
  without file rewrite; the two supervised processes share nothing else).
  Enabling WhatsApp/Slack turns the shared receiver on in the same write
  (no poller — without a listener the door is open, complete, and deaf).
- Terminal: `phone channels[get/set/unset]`, `phone send --channel/--to`,
  `/channels`, `/channel [name]` (`allowed` = per-door
  `allowed_chat_ids/phone_numbers/ids`; `acceptFromAnyone` spelled alike).

Telegram is the door to start with (long-poll: no URL/tunnel/cert):
token from `@BotFather` → paste in Settings → put the bot's `/id` chat id in
*both* allowlist and `people[0].channels telegram: "tg:<id>"`.

---

## 10. Interface and bridge

`interface/` — `@project-phone/core|web|cli` (binary `phone`, Ink 5),
React 18 + Vite 6 + `duplex-client.ts`; `web/server/index.mjs` — the
stateless bridge: serves the built UI, reads the agent's tables, translates
HTTP/WS ⇄ store, turns surface commands into `events` rows (only direct write
is the inbound `messages` row for an instant transcript,
`ON CONFLICT DO NOTHING`). No state of its own; supervisor-optional (never
restarted once exited — `npm start -- --interface` replaces the page and
leaves the agent alone; `ETHOS_WEB_PORT` / `channels.web.http_port`
overrides; second port = second page beside the running one).

One screen for state + conversation (an agent that thinks unobserved must be
watchable): presence/focus, thought, intention tree (deadlines/budgets),
undoable history, spend vs caps, guardian coverage. Actions: send, hold /
resume / stop / emergency-stop, undo, close intention, open/close doors, name
the agent. With no agent running it shows the **last** run — `ethos status`
answers "is it there".

---

## 11. Threads and subagents

- **Threads** (`ethos/threads/`): same framework, same identity+memory, only
  Self speaks (`manager.py:33`). `spawn_spec` enforces `max_concurrent 4 /
  max_depth 2` (`config/ethos.yaml:61`), budget = parent's remaining / 2,
  intention `origin=thread` (except verifier); `process → THREAD_REQUEST`
  else in-task + `THREAD_RESULT` (`manager.py:143`). `worker.py` runs
  `run_thread` (or `foundry/apex` roles via registry +
  `record_episode thread_result importance .6`).
- **Subagents** (`ethos/subagents/`): `SubAgent` ABC (`base.py:52`,
  `SubAgentResult{agent,status,duration,mode}`); `SubagentRegistry`
  (`__init__.py:41`) builds only `enabled` from `ethos.yaml:subagents`
  (`tier T2`, `gateway_url/model` empty = inherit, foundry `timeout 3600`
  `repo_root` empty = worktree, apex `timeout 900`). Every model call they
  make routes through this agent's gateway — models, spend guard, retries,
  base model apply equally — via `FoundryGatewayProvider`
  (`bridge.py:61`, `run_coroutine_threadsafe(gateway.complete)`, ceiling
  32768) and the OpenAI-compat wire. Bundled programs are **separate repos**
  (`.gitmodules`): **Foundry** (`foundry-factory`: deterministic gate-verified
  software factory — plain-text objective → LLM design/plan → fixed state
  machine, sandboxed gates, merge trains, signed artifacts; in-process
  `Orchestrator` else `python -m foundry`) and **APEX**
  (`revision-aware-hybrid-desktop-controller`: natural-language desktop goal →
  observe → one fenced step at a time → execute + machine-verify; exit
  `0 done/1 failed/2 unknown/3 blocked`).

---

## 12. Configuration, observability, tests, deployment

- **Config**: 12 files in `config/` (`ethos, base_model, models, pricing,
  quirks, memory, rhythm, social, gsl, permissions, channels, people`) merged
  by `load_config()` (`ethos/config.py:1596`); `~/.ethos/{channels,people,
  base_model,identity}.yaml` override-merge; `.env` (`./.env`, checkout
  `.env`, `$ETHOS_ENV_FILE`; export wins; `ETHOS_ENV_LOADED` once).
  `ethos doctor` checks DB + `vector` + Alembic head vs `alembic_version` +
  keys + **live one-token model probes** (a present key ≠ a working one;
  only base-model failure fails the exit; `--no-probe-models` degrades
  honestly) + per-door lines + socket + measured filesystem reach.
- **Observability**: structlog JSON, Prometheus
  (`observe_cycle/observe_action`, spend counters), hash-chained audit +
  600s anchor (`runner.py:273`), 60s digest flush (`runner.py:287`).
  `ethos logs [--from-start]`, `make logs`.
- **Tests**: pytest (`asyncio_mode=auto`, 120s): `tests/unit` (29 files, no
  DB), `tests/integration` (live Postgres, `integration` marker),
  `tests/scenarios` (E2E); JS `node --test` (`make test-bridge`); `make
  test*`; `make lint` (ruff, line-length 110 — format deliberately not in
  lint).
- **Deployment**: `deploy/systemd/` units + `config/ethos.yaml` sockets;
  `deploy/oob/oob_guardian.py` band-out $20/day · $400/mo kill-switch;
  `data/pgdata` gitignored (memory survives `make clean`; `make db-down &&
  rm -rf data/pgdata` to throw away); `./start.sh` deprecated shim over
  `ethos up`; `npm start` wrapper (install bridge+UI, venv via uv/fallback,
  end stale session unless `--keep`, `ethos up`, verify page, `--no-open /
  --interface` variants).

---

## 13. Build on it

| Want | Do |
|---|---|
| **Tool** | Subclass `Tool` ABC (`toolhost/server.py:45`), one line in `_register_builtin_tools()` (`server.py:103`) — catalog + prompts follow. Or runtime: `~/toolshed/<name>/tool.yaml` + stdin/stdout JSON (`tools/toolshed.py`). |
| **Provider** | Implement `Provider` ABC (`gateway/providers/base.py:103`) + `normalizer.py` pair + `PROVIDER_CLASSES` (`service.py:40`) + `models/quirks/pricing.yaml` entries. |
| **Channel** | Implement `ChannelAdapter` (`comms/adapters/base.py:93`: start/listen/send) → `CommsHost.plan()` + `channels.yaml` creds. |
| **Subagent** | Subclass `SubAgent` (`subagents/base.py:52`) → `SubagentRegistry._load` (`subagents/__init__.py:41`) → `ethos.yaml:subagents:` (+ `subagents/tools.py` delegation tools). |
| **UI** | Replace freely — it only translates Postgres ⇄ HTTP/WS; never writes agent state except inbound rows + `events`. |

Open questions the code does not settle: `@project-phone/*` + `phone`
binary vs `Clio Agent 3` naming (migration in progress?); no `uv.lock`
(pins are ranges); JSON-contract (Self) vs native `tool_calls`
(subagents) duality intentional?; `ethos-core --listen-channels` + comms
co-listening undocumented; no license text (README: "no license yet — all
rights reserved"); submodule internals (APEX/Foundry) verified only via
bridge + their docs.

---

*Rewritten from a full read of `/Users/sasakikousei/Desktop/Ethos`
(`ethos/`, `config/*.yaml`, `migrations/`, `interface/`, `web/`,
`deploy/`, `README.md`, `pyproject.toml`). Prior Japanese record:
`clio-agent-3-architecture.md`.*
