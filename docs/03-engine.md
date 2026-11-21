# 03 — The engine: life loop, attention, drives

The core of Clio Agent is a tick-based loop that runs on its own clock. This document
specifies its phases, its state machine, its rhythm and energy model, its attention
economics, its motivational system, and its failure semantics. Primary sources:
`clio/engine/life.py`, `clio/engine/states.py`, `clio/engine/rhythm.py`,
`clio/engine/attention.py`, `clio/engine/workspace.py`, `clio/engine/drives.py`,
`clio/engine/reverie.py`, `clio/core/runner.py`.

## Part I — The life loop

## 1. `life()` — the outer loop

`Life.life()` (`clio/engine/life.py:3445-3563`):

1. **`wake_up()` is retried forever** (60 s sleep between attempts) until it succeeds or
   `control.ending` is signalled. Startup failure is a wait, not a death.
2. Each iteration begins with **`control.refresh()`** — the control-plane row is the
   authority on pause/stop/ending, rechecked every cycle ([02](02-safety-and-guardian.md) §6.1).
3. Only `control.ending` (a signal) breaks the loop. `stopped`/`paused` → the stop is
   recorded, state becomes `PAUSED`, and the loop parks.
4. Otherwise the iteration is `_cycle_until_stopped()` (`life.py:3406-3444`), which
   **races** `_cycle()` against `control.wait_stop()` — an oversight stop lands between
   phases, not only at cycle boundaries.
5. A cycle exception is logged, recorded in the stops ledger, presence is published, and the
   loop waits for the next tick. **A raised cycle is a failed cycle, not a death.**
6. On exit: `bookmark(focus)` is saved, continuity is saved, and the exit is audited
   (`life.py:3535-3563`).

## 2. `_cycle()` — one pass

`clio/engine/life.py:3565-3660`. Order:

```
_pace() → _drain_steering() → _maybe_fast_lane()
  → _maybe_reopen_parked() → _maybe_park_zombies()
  → perceive() → attention.appraise(...)
  → [interrupt handling | _choose_focus()]
  → _assemble_workspace() → decide() → act() → record()
  → next_focus() → _maybe_rotate_project_focus()
  → state WAITING transitions → rhythm.after_cycle() → _wait_until_next()
```

- **`_pace()`** (`life.py:3300-3378`): triggers nightly consolidation at
  `memory.consolidation.run_at` (default `04:00`, `clio/config.py:771`) **on a clock rather
  than on going to sleep** (`life.py:3327-3350` — the pass runs even when the agent never
  sleeps); low energy triggers a `RESTING` micro-rest, after which the state returns to
  `THINKING_REFLECTING`.
- **`_drain_steering()`** (`life.py:1494-1544`): a message bound (via
  `conversation_project_bindings`) to the project that currently holds the execution focus,
  while that run is under way, is re-routed into the steering queue instead of taking a full
  deliberation turn ([10](10-sandboxes-steering-and-policy.md) §4). Everything else stays an
  ordinary message; mail is never silently swallowed.
- **`_maybe_fast_lane()`** (`life.py:1546-1621`): at a clean boundary during a locked
  project quantum, one quick message in a *secondary* conversation is answered read-only —
  tier capped at T2, dialogue tools only, `active_project_id` untouched — inside a budget of
  `projects.fast_lane_budget_s` (90 s). A message that would create work is deliberately
  left unhandled for the full social turn, with its commissioning machinery.
- **`perceive()`** (`life.py:389`): pulls percepts that arrived during the last sleep;
  early arrivals during a tick are held and wake the sleep (`_wait_until_next`,
  `life.py:427-480`).
- **`appraise()`**: scores everything and decides interrupts (§6).
- **Interrupt handling** (`life.py:3596-3618`): a salient-enough percept bookmarks the
  current focus, pushes it onto `_suspended_stack` when a goal is owed
  (`_push_suspended`, `life.py:272-282`), and replaces it — so owed work survives the
  interruption and is handed back by `next_focus`.
- **Project rotation** (`_maybe_rotate_project_focus`, `life.py:768`): a project that has
  held the pass for its full interval (or a 90-minute session) may hand it to the
  best-scoring waiting project — through a switch protocol, never silent reassignment.

## 3. The state machine

Eight states (`clio/engine/states.py:10-18`):

```
THINKING_FOCUSED, THINKING_REFLECTING, ACTING, WAITING, NOTIFYING,
RESTING, PAUSED, RESUMING
```

- Groups: the first five plus `RESUMING` are `AWAKE` (`states.py:32`).
- **There is deliberately no `OFF` state** (`states.py:45-51`). The agent never stops
  existing; it only rests or pauses.
- Transitions (`states.py:35-41`): any state may go to any `AWAKE` state plus
  `RESTING/PAUSED/RESUMING` except itself; `PAUSED → {RESUMING}` only;
  `RESTING → AWAKE ∪ {PAUSED, RESTING}`.
- History capped at 200 entries (`states.py:73-85`).
- Default per-state **interrupt thresholds** (`states.py:59-66`, overridden by
  `rhythm.attention.interrupt_thresholds`): `THINKING_FOCUSED 0.65`,
  `THINKING_REFLECTING 0.40`, `RESTING 0.50`.
- `WAITING` is entered when there is no focus and left when one is acquired
  (`life.py:3646-3652`).

The threshold is the only interrupt gate — there is no kind whitelist
(`clio/engine/attention.py:106-118` explains its removal: a hardcoded filter sat between
the salience math and the interrupt). A salient-enough timer interrupts deep focus; an
unsalient message does not interrupt reflection.

## 4. Rhythm and energy

### 4.1 Tick pacing

Tick seconds per state (`clio/config.py:585-590`):

| State | Tick (s) | State | Tick (s) |
|---|---|---|---|
| `THINKING_FOCUSED` | 2.0 | `NOTIFYING` | 2.0 |
| `THINKING_REFLECTING` | 4.0 | `RESTING` | 60.0 |
| `ACTING` | 1.0 | `RESUMING` | 1.0 |
| `WAITING` | 8.0 | `PAUSED` | 5.0 |

Each tick is jittered ±15% (uniform) with a floor of 0.2 s
(`clio/engine/rhythm.py:189-192`). `_wait_until_next()` races the tick sleep against
percept arrival, so a message never waits for the tick to elapse (`life.py:427-480`).

### 4.2 Energy

`clio/engine/rhythm.py:92-125`, configured at `clio/config.py:338-356`:

```
E = min(1 − spent/B, circadian) + ρ_rest, clipped to [0, cap]
```

- **`circadian`** is a cosine over the day with a **night floor of 0.55** — above
  `low_energy` (0.35) so the night alone is never a reason to stop working
  (`config.py:343-348`). **There is no bedtime by design**: work due at 04:00 is work due.
- **`spent/B`**: energy dips as the day's discretionary spend guard runs toward its cap.
- **Rest blocks**: each 30-minute rest block adds `rest_boost_per_30min`, capped
  (`rhythm.py:120-121`).
- **`should_rest`** when `energy < 0.35` or `max_utility < 0.1` (`rhythm.py:125`).
- Focus sessions cap at 90 minutes with 5-minute micro-breaks
  (`FocusConfig`, `config.py:358-361`) — attention fatigue is modelled, not ignored.

### 4.3 Backoff — one curve everywhere

The shared formula, reused by the supervisor, the GSL park-reopen loop, the worker and the
stage-notice retries (`clio/engine/rhythm.py:17-38`):

```
delay = min(base · 2^attempt · (1 + U · jitter), max)
```

Jitter is what stops everything that failed in the same window from retrying in the same
window.

## 5. `decide()` — the priority ladder

`clio/engine/life.py:1215-1424`. First match wins:

1. **Pending inbound** — `pending_inbound(limit=3)` (`life.py:1483-1490`). When a goal is
   owed and the focus is not social, deferrals are counted and bounded by
   `rhythm.focus.inbound_defers_goal_max = 2` (`config.py:374-380`): an unbounded backlog
   once meant `advance_goal` was *never* reached, not deprioritised — never. The message
   stays at the head of the queue and is answered on the next pass. Social messages are
   deliberated: tier `T3` if `is_consequential_text` else `T2`
   (`life.py:1240-1275`; the same function `SocialCognition` uses, so the tier the message
   is deliberated at and the tier it is labelled with cannot disagree, `life.py:1434-1443`).
2. **Pending governance notices** for a project lead role (`life.py:1281-1300`), then
   **progress notices** (`T2`).
3. **Impulse focus / owed goal**: an impulse focus is dropped when commissioned work is
   owed ("commissioned work outranks idle impulses", `life.py:1331-1348`); otherwise the
   current intention advances at `T2` (`life.py:1355`).
4. **Due stage notice** → `notify`, tier `T2`, state `NOTIFYING`
   (`life.py:1368-1382`) — "tell {person} that '{title}' reached {stage}".
5. **Social focus with no message** → dropped (`life.py:1385-1398`): the focus is spent the
   moment its message is handled.
6. **Reverie due** (only when the focus is empty) → the reverie decision (`life.py:1315-1330`).
7. **Else `idle_model`, tier `T1`** — `rationale: "nothing pending; always call the model"`
   (`life.py:1420-1424`).

Two honest observations about the ladder:

- The idle branch is **not** a no-op: an idle pass still makes a T1 model call. The loop
  does not manufacture *work* on idle passes (the drives still refuse low-utility impulses,
  §11), but it does spend a small model call every idle tick. That is a deliberate
  trade — the model may notice something the rules cannot — and it is a cost the budget
  accounting sees.
- The social branch comments say "attention gate removed": a pending inbound message is
  deliberated because it is pending, not because it won an interrupt contest. The interrupt
  machinery (§6) still governs focus *replacement*; it no longer gates whether the head of
  the message queue is answered.

## 6. Attention: salience, tiers, and interrupts

### 6.1 The tier system (T0–T3)

Every act of cognition has a tier (`clio/schemas/models.py:11-15`), which selects the
minimum model quality the gateway may route to (`clio/config.py:1073-1079`):

| Tier | Floor | Role |
|---|---|---|
| **T0** | — (rejected outright by the gateway, `clio/gateway/service.py`) | Rule-based salience math; no model call. |
| **T1** | 0.50 | Fast-glance refinement, reverie, narration, idle model calls. |
| **T2** | 0.70 | Normal working tier: ordinary replies, goal advance, notify, GSL steps, subagents. |
| **T3** | 0.90 | Consequential decisions: consequential replies, planning, verification, due process. |

The tier is both a **cost** control (a wander decision never burns a frontier model) and an
**accuracy** floor (a consequential commitment never routes to a weak model). The pinned
base model is exempt from floors by design (`clio/gateway/router.py:289-294`,
`:361-368`) — [07](07-gateway-and-toolhost.md) §1.

### 6.2 Salience scoring (T0 math)

`estimate_salience` (`clio/engine/attention.py:61-118`):

```
sal = clip(ws·src + wu·urg + wr·rel + wn·nov)
```

Weights (`clio/config.py:296-330`): source 0.35, urgency 0.30, relevance 0.20, novelty
0.15. Source weights: `owner 0.9, known 0.6, unknown 0.3, system 0.4`. The owner's message
outweighs any system event; an unknown sender is not zero but does not outrank the agent's
own open work at equal urgency.

### 6.3 `appraise()` — the attention pass

`clio/engine/attention.py:193-290`:

1. **Dedupe** percepts on `(kind, source)` within a 10-second window.
2. **Score and sort** all candidates by rule salience.
3. **Refine in-band (T1)**: only percepts whose rule salience lands in the band
   `(0.3, 0.8)` get a fast-glance model refinement — a strict-JSON
   `{urgency, relevance, novelty}` call, at most 3 per cycle (`attention.py:131-192`,
   `:226-236`). Below the band they are clearly ignorable; above it clearly interruptive;
   the band is where rules are unsure and a cheap look is worth it.
4. **Interrupt decision**: `interrupt = top.salience >= state_threshold`
   (`attention.py:240-242`). No kind whitelist.
5. **Project lock**: while a project holds a locked quantum, an interrupt that would take
   the pass is converted into a queued/deferred outcome rather than stealing focus
   (`attention.py:250-270`) — the fast lane and steering are the sanctioned ways in.

### 6.4 The workspace: what one prompt is made of

`_assemble_workspace()` (`life.py:1095-1460`) builds the prompt. Block order is fixed
(`clio/engine/workspace.py:13-27`):

```
identity, tools, limits, situation, commitments, conversations,
focus, thread, recent, memories, thoughts, stops, percepts
```

- **Three stable blocks** (`STABLE_BLOCKS = 3`, `workspace.py:28`): `identity`, `tools`,
  `limits` lead the prompt and are byte-stable across cycles so the provider-side prompt
  cache reads on every subsequent turn.
- **Block 2 is narrowed** (`workspace.py:253-269`): under two-stage tool narrowing the
  cached prefix pins only the `tools.search` meta-tool and states the registry's size;
  the per-step sheet of up to twelve fully-rendered tools is rendered *below* the cache
  breakpoint ([07](07-gateway-and-toolhost.md) §3). A caller without the pinned meta-tool
  (a thread worker) keeps the full sheet.
- **The agent's name is deliberately absent from the volatile blocks** (`workspace.py:240-252`):
  it lives only in the identity kernel, so it cannot read as something that varies.
- **Digest**: `sha256[:32]` of the workspace (`workspace.py:207+`), recorded with each
  cycle row.
- **Volatile blocks**: `situation` (state, energy, spend); `commitments` (rendered with
  DAG folding in 3.2, `render_commitments` `workspace.py:124-188`); `conversations` /
  `thread` / `recent` — `CONTEXT_BLOCKS = ("thread", "recent", "conversations")`
  (`workspace.py:37`) outrank retrieval blocks: the working thread beats a memory hit;
  `memories` (retrieval, k=12); `thoughts` (last 10); `stops` (24-entry ledger, last 6
  rendered, `clio/engine/stops.py:51-55,128`); `percepts` (capped at 20, each wrapped in
  `<untrusted>` tags — page content, channel text and webhook bodies are untrusted data
  and are labelled as such).

## Part II — Drives: motivation, impulses, and unowned time

## 7. The seven drives

`clio/engine/drives.py:19`:

| Drive | What it measures |
|---|---|
| `commitment` | Owed work: intentions taken on |
| `curiosity` | Open questions |
| `mastery` | Skill gaps signalled by recent failure |
| `creation` | Ventures and their value |
| `order` | Unread/unmanaged environment |
| `social` | Contact freshness |
| `homeostasis` | Energy, disk, budget, maintenance debt |

The names are deliberate: motivation is modelled on drives rather than a task queue, which
is what lets it decline to act (low utility is a legitimate verdict) instead of inventing
filler.

## 8. Gain functions

Each drive's pressure = base + gain, computed from state (`drives.py:231-310`):

- **commitment** = max intent urgency, `priority×0.5 + deadline×0.5`, with an undated floor
  (`drives.py:23-36` — undated work still presses, but is visible to softmax).
- **curiosity** = `clip(open_questions/10) · 0.6`.
- **mastery** = `clip(failed_recent/5) · 0.7` — failure raises the appetite to practice.
- **creation** = `0.2 + 0.3·clip(ventures/3) + 0.3·clip(value/10)` — a nonzero floor.
- **order** = `clip(unread/50)`.
- **social** = `clip(last_contact_age_h/24)` — silence accumulates a gentle pull.
- **homeostasis** = `0.8·(1−energy) + 0.3·(1−disk) + 0.3·clip(maintenance/48) + 0.2·(1−budget)`.

Deadlines are read defensively: a model-written `deadline` can be a word rather than a date,
and an unreadable deadline is a deadline nobody can read — parsed with `datetime.fromisoformat`
inside a guard (`drives.py:100-119`), with the same tolerance on the life-loop side
(`_deadline_from_spec`, `life.py:3682-3710`: the words `null/none/nil/n/a/-` and anything
unreadable mean *no deadline*, and the intention is still created).

## 9. Impulses and arbitration

`DriveSystem.arbitrate` (`drives.py:439-535`):

1. `pressures = base + gain` per drive.
2. At most **one impulse per drive** may fire, only above `propose_threshold = 0.4`
   (`config.py:334`), from a fixed table:

   | Drive | Impulse |
   |---|---|
   | commitment | `advance_commitment` |
   | curiosity | `explore_question` |
   | mastery | `deliberate_practice` |
   | creation | `push_venture` |
   | order | `tidy` |
   | social | `reach_out` |
   | homeostasis | `maintenance` |

3. Expected utility:

   ```
   U = (w·p·r + 0.15·Σwp)·V̂ − (λ0/max(E, 0.1))·ĉ
   ```

   — weighted probability · risk-adjusted value plus a small portfolio term, minus **cost
   scaled by the inverse of energy**: a tired agent discounts expensive impulses harder.
4. **Softmax sampling** over utilities picks the impulse.
5. **Rejection** if max utility `< min_impulse_utility = 0.1` (`config.py:355`): "there is
   nothing I am drawn to right now" is a first-class verdict.
6. **Deadline bypass**: a deadline inside `deadline_bypass_h` (24 h) with open commissions
   skips arbitration entirely (`drives.py:465-495`) — owed work is not a suggestion.

### 9.1 No placeholder work

`advance_commitment` and `push_venture` resolve to an **existing** intention via
`IMPULSE_TARGETS` (`drives.py:396-436`). If no venture exists the drive honestly declines —
it never mints a placeholder task to look busy. This is the mechanical enforcement of
"invented work is worse than an idle hour", and it is why `kind='venture'` is load-bearing
in `clio venture add`: "a task titled the same way is invisible to the drive."

## 10. Reverie: the daydream loop

`Reverie.muse()` (`clio/engine/reverie.py:50-260`):

- **Sampling**: 4 memories + 3 open questions + recent thoughts (window 8,
  `ReverieConfig`, `config.py:388-410`).
- **Model call**: gateway `T1`, `max_tokens = 400` (`config.py:400`) — a daydream is cheap
  by construction.
- **Refusals**: subject `interest < 0.2` (`min_interest`, `reverie.py:102-104`; "a subject
  the model is not actually interested in is not a hobby"), or Jaccard overlap ≥ 0.6
  (`repeat_threshold`) against the last 8 self-chosen subjects — **the same daydream is not
  allowed to loop** (`reverie.py:203-228`).
- **Commitment** (`reverie.py:163-195`): a chosen subject becomes either an
  `open_question` (feeding `curiosity_gain`) or a new intention
  `kind="venture", origin="reverie"` (feeding `creation_gain`) — filed differently on
  purpose, because filing everything as a venture made curiosity count nothing
  (`reverie.py:163-172`).
- A model that names an interest also names one it has no business having; the refusal path
  records *why* (`reverie.py:131-160`).

Mind-wandering is the lighter sibling: a `T1` decision about whether to drift attention; a
committed venture becomes the focus. Intervals are **random-drawn, not scheduled**:
mind-wander 20–40 min (`config.py:382-386`), reverie 12–45 min (`config.py:393-395`). A
fixed schedule would make the daydream predictable and let the drift become rote.

## 11. Theory: motivation as state, not queue

The design separates three things conventional agents conflate:

1. **Owed work** (commitments, commissions) — externally imposed, deadline-driven, exempt
   from arbitration when near due.
2. **Chosen work** (ventures, open questions) — internally generated, arbitrated on utility,
   killable when it stops paying.
3. **Maintenance** (order, homeostasis) — the cost of existing, budgeted like work.

Once taken on, (1) and (2) are the same kind of thing — one list, weighed by urgency and
deadline, neither outranked by who asked. But their *origins* differ, and the origin is
recorded on the intention (`origin="reverie"`, `kind="venture"`), which is what lets the
drives see their own products and the autobiography
([04](04-memory-and-self-model.md) §6) describe a life rather than a task log.

## 12. Failure semantics

- A cycle that raises is recorded (stops ledger), published, and retried next tick. Rate
  limits and outages are bad minutes.
- Deliberation rate-limit escalation: 5 m → 1 h → 5 h → 1 d — the shared schedule
  `RATE_LIMIT_WAIT_SCHEDULE_S = (300, 3600, 18000, 86400)`
  (`clio/gateway/service.py:93-126`), imported and used by the deliberation path
  (`clio/social/deliberation.py:11,168-204`, which also separates a plain 429 from a quota
  problem) — **one calendar for waiting everywhere**.
- The supervisor restarts required children forever with backoff and detects a wedged core
  by heartbeat (`clio/core/supervisor.py:360-375`) — [08](08-runtime.md) §2.
- On a clean stop, `bookmark(focus)` is saved so the next wake resumes the suspended work
  (`clio/memory/continuity.py:19-75`).
- Zombie parking: `_maybe_park_zombies` (`life.py:1869-1900`) closes out intentions whose
  owning threads died without a result, so nothing stays "running" forever.
- Worker call attempts default to **unbounded** with `no-gateway` as the one exception that
  ends the task honestly on the spot (`ThreadsConfig.call_attempts`, `config.py:278-292`) —
  waiting cannot fix a missing engine, and the goal is parked for the reopen sweep.

## 13. What the engine does not claim

- The idle `idle_model`/T1 pass spends a model call per idle tick (§5) — a real, budgeted
  cost, not a free "rest".
- The interrupt thresholds are defaults; a deployment can raise or lower them, and the
  social path currently answers pending mail without competing through the threshold at all
  (§5) — the "deep work is protected" story now lives in the project lock, the fast lane and
  `inbound_defers_goal_max`, not in a hard wall against messages.
- Reverie and mind-wandering are enabled by default but individually switchable
  (`ReverieConfig.enabled`, `config.py:393`).
- There is no novelty centroid on the appraisal path yet (`life.py:3588-3592` —
  `recent_centroid=None`, "nothing fills a recent-thought vector list on this path"), so
  the novelty term of salience runs on the focus comparison alone.
