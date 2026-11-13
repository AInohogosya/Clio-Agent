# The life loop

The core of Clio Agent is a tick-based loop that runs on its own clock. This document
specifies its phases, its state machine, its rhythm and energy model, and its failure
semantics. Primary sources: `ethos/engine/life.py`, `ethos/engine/states.py`,
`ethos/engine/rhythm.py`, `ethos/core/runner.py`.

## 1. `life()` — the outer loop

`Life.life()` (`ethos/engine/life.py:2008-2122`):

1. **`wake_up()` is retried forever** (60 s sleep between attempts) until it succeeds
   or `control.ending` is signalled (`life.py:2015-2031`). Startup failure is a wait,
   not a death.
2. Each iteration begins with **`control.refresh()`** (`life.py:2039`) — the control
   plane row is the authority on pause/stop/ending, rechecked every cycle.
3. Only `control.ending` (a signal) breaks the loop (`life.py:2046-2047`).
   `stopped`/`paused` → the stop is recorded, state becomes `PAUSED`, and the loop
   parks (`life.py:2048-2069`).
4. Otherwise the iteration is `_cycle_until_stopped()` (`life.py:1969-2006`), which
   **races** `_cycle()` against `control.wait_stop()` — so an oversight stop lands
   between phases, not just at cycle boundaries.
5. A cycle exception is logged, recorded in the stops ledger, presence is published,
   and the loop waits for the next tick (`life.py:2071-2098`). **A raised cycle is a
   failed cycle, not a death.**
6. On exit: `bookmark(focus)` is saved, continuity is saved, and the exit is audited
   (`life.py:2107-2122`).

## 2. `_cycle()` — one pass

Order (`ethos/engine/life.py:2124-2187`):

```
_pace() → _maybe_reopen_parked() → perceive() → attention.appraise(...)
  → [interrupt handling | _choose_focus()] → _assemble_workspace()
  → decide() → act() → record() → next_focus()
  → rhythm.after_cycle() → _wait_until_next()
```

- **`_pace()`** (`life.py:1863-1887`): daily consolidation is triggered at
  `memory.consolidation.run_at` (04:00, `life.py:1889-1913`); low energy triggers a
  `RESTING` micro-rest (5 minutes) after which the state returns to
  `THINKING_REFLECTING`.
- **`perceive()`**: pulls percepts that arrived during the last sleep (early arrivals
  during the tick are held in `_woken`, `life.py:330-371`).
- **`attention.appraise(...)`**: scores everything and decides interrupts
  (see [03-engine.md](03-engine.md)).
- **Interrupt handling**: if a percept outranks the current state's interrupt
  threshold, the current focus is bookmarked and suspended and the percept replaces
  it.
- **`decide()`** and **`act()`**: below.
- **`record()`** (`life.py:1727-1761`): `cycles += 1`; an INSERT into the `cycles`
  table (thread, state, focus, tier, decision, cost, duration, digest);
  `continuity.save()`; metrics; presence and budget publication.
- **`_publish_presence()`** touches `run_dir/heartbeat` **first**, then publishes
  `presence.update` (`life.py:1763-1785`). Parked loops republish every
  `PARKED_HEARTBEAT_S = 30.0` (`life.py:34`) — the supervisor's wedged-core detection
  reads this file.

## 3. The state machine

Eight states (`ethos/engine/states.py:10-18`):

```
THINKING_FOCUSED, THINKING_REFLECTING, ACTING, WAITING, NOTIFYING,
RESTING, PAUSED, RESUMING
```

- Groups: the first five plus `RESUMING` are `AWAKE`; `RESTING` and `PAUSED` are the
  other group (`states.py:21-30`).
- **There is deliberately no `OFF` state** (`states.py:45-51`). The agent never stops
  existing; it only rests or pauses.
- Transitions (`states.py:35-41`): any state may go to any `AWAKE` state plus
  `RESTING/PAUSED/RESUMING` except itself, **except** `PAUSED → {RESUMING}` only, and
  `RESTING → AWAKE ∪ {PAUSED, RESTING}`.
- History is capped at 200 entries (`states.py:73-85`).
- Default per-state **interrupt thresholds**
  (`states.py:59-63`, overridden by config at `life.py:159-162`):

| State | Threshold |
|---|---|
| `THINKING_FOCUSED` | 0.65 |
| `THINKING_REFLECTING` | 0.40 |
| `RESTING` | 0.50 |

The threshold is the **only** interrupt gate — there is no kind whitelist
(`ethos/engine/attention.py`). A salient-enough timer interrupts deep focus; an
unsalient message does not interrupt `THINKING_REFLECTING`.

## 4. Rhythm and energy

### 4.1 Tick pacing

Tick seconds per state (`config.py:583-587`):

| State | Tick (s) |
|---|---|
| `THINKING_FOCUSED` | 2.0 |
| `THINKING_REFLECTING` | 4.0 |
| `ACTING` | 1.0 |
| `WAITING` | 8.0 |
| `NOTIFYING` | 2.0 |
| `RESTING` | 60.0 |
| `RESUMING` | 1.0 |
| `PAUSED` | 5.0 |

Each tick is jittered ±15% (uniform) with a floor of 0.2 s
(`ethos/engine/rhythm.py:184-190`). `_wait_until_next()` races the tick sleep against
percept arrival, so a message never waits for the tick to elapse
(`life.py:330-371`).

### 4.2 Energy

`ethos/engine/rhythm.py:92-122`, configured at `config.py:337-354`:

```
energy = min(1 − spent/B, circadian) + 0.1 × rest_blocks(30 min), clipped to [0, 1]
```

- **`circadian`** is a cosine over the day with peak hour `10.0`, a night floor of
  `0.55`, and a cap of `1.0`. The agent never fully winds down — **there is no bedtime
  by design** (`rhythm.py:48-58`); work due at 04:00 is work due.
- **`spent/B`** is the budget burn term: energy dips as the day's spend guard runs
  toward its cap.
- **Rest blocks**: each 30-minute rest block adds `0.1`, capped.
- **`should_rest`** when `energy < 0.35` or `max_utility < 0.1`.

Focus sessions are capped at 90 minutes with 5-minute micro-breaks
(`config.py:357-359`) — attention fatigue is modelled, not ignored.

### 4.3 Backoff

The shared backoff formula, reused by the supervisor, the GSL park-reopen loop, and
stage notices (`rhythm.py:17-39`):

```
delay = min(base · 2^attempt · (1 + U · jitter), max)
```

## 5. `decide()` — the priority ladder

First match wins (`ethos/engine/life.py:589-741`):

1. **Pending inbound** (limit 3 per pass, `life.py:800-807`) — unless a goal is owed
   and deferrals exceed `rhythm.focus.inbound_defers_goal_max = 2` (`config.py:378`).
   Social messages are **deliberated**: tier `T3` if consequential, else `T2`
   (`life.py:625-645`).
2. **Non-social focus with an `intention_id`** → `advance_goal`, tier `T2`
   (`life.py:647-674`).
3. **Due stage notice** → `notify`, tier `T2`, state `NOTIFYING` (`life.py:696-702`).
4. **Social focus without a message** → dropped (`life.py:712-718`).
5. **Impulse focus** → `advance_goal`/`T2` or `mind_wander`/`T1` (`life.py:720-724`).
6. **`rhythm.reverie_due()`** → `reverie`/`T1`; **`mind_wander_due()`** →
   `mind_wander`/`T1` (`life.py:726-733`). Intervals are random-drawn, not scheduled:
   mind-wander 20–40 min, reverie 12–45 min (`config.py:381-384,397-398`).
7. **Else `none`/`T0`** (`life.py:741`) — an idle pass. This is a legitimate outcome,
   and the loop does not manufacture work to avoid it (see
   [01-philosophy.md](01-philosophy.md) §7).

## 6. `act()` — the action dispatch

`act()` (`life.py:811-843`):

- `advance_goal` → GSL `advance` (see [06-work.md](06-work.md)).
- `social` → `_act_social()`: commissions, sends, and the ignore-streak review.
- `notify` → `_notify_stage()`: stage notices for intention stages
  (`done/parked/awaiting_review`).
- `mind_wander` / `reverie` → their handlers; a committed venture from reverie
  becomes the new focus (`life.py:1668-1714`).

## 7. Failure semantics

- A cycle that raises is **recorded** (stops ledger), published, and retried next
  tick. Rate limits and outages are bad minutes.
- Deliberation rate-limit escalation ladder: 5 m → 1 h → 5 h → 1 d
  (`ethos/social/deliberation.py:138-147`), shared with the gateway's
  `WAIT_SCHEDULE (300, 3600, 18000, 86400)`.
- The supervisor restarts required children forever with backoff
  (`supervisor.py:144-145`), and the wedged-core heartbeat check catches a core that
  hangs without raising. Restarts are counted and the counter resets after 60 s of
  stability.
- On a clean stop, `bookmark(focus)` is saved so the next wake resumes the suspended
  work — the agent keeps its place in its own thoughts across restarts
  (`ethos/memory/continuity.py`).

## 8. Theory: why a clock of its own

The loop's design answers one question: *what does it mean for an agent to exist
between messages?* The answer implemented here:

- Existence is **paced** (ticks, energy, circadian rhythm), not event-driven. The
  agent is never purely reactive.
- Existence has **phases** (the state machine) with different attention economics:
  focused work is protected by a high interrupt threshold (0.65); reflection yields
  (0.40); rest yields more (0.50).
- Existence has **unowned time** — mind-wandering and reverie are scheduled by the
  rhythm, not by anyone's request, and their outputs (open questions, ventures) are
  first-class state ([03-engine.md](03-engine.md)).
- Existence is **continuous by construction**: no `OFF` state, wake-up retried
  forever, failed cycles retried, and continuity persisted every cycle.
## Part II — Attention: salience, tiers, and the workspace

Attention is the mechanism that decides what the agent thinks about next, how deeply
an incoming stimulus is assessed, and what ends up in front of the model. Sources:
`ethos/engine/attention.py`, `ethos/engine/workspace.py`, `ethos/schemas/models.py`,
`config.py:295-319,1038-1042`.

## 1. The tier system (T0–T3)

Every act of cognition has a tier (`ethos/schemas/models.py:11-15`), which selects the
minimum model quality the gateway may route to (`config.py:1038-1042`):

| Tier | Floor | Role |
|---|---|---|
| **T0** | — (rejected by the gateway outright, `gateway/service.py:864-865`) | Rule-based salience math; the tier of the `none` decision. No model call. |
| **T1** | 0.50 | Fast-glance refinement, reverie, narration, wander decisions. |
| **T2** | 0.70 | Normal working tier: ordinary social replies, goal advance, notify, GSL steps, subagents. |
| **T3** | 0.90 | Consequential decisions: consequential social replies, plan choice, verification, due process. |

The tier is both a **cost** control (a wander decision never burns a frontier model)
and an **accuracy** floor (a consequential commitment never routes to a weak model).
If no available model clears the floor, the pinned base model is returned as an
exemption rather than failing (`ethos/gateway/router.py:310-318`).

## 2. Salience scoring (T0 math)

Rule-based salience (`ethos/engine/attention.py:31-61`):

```
sal = clip(ws·src + wu·urg + wr·rel + wn·nov)
```

Weights (`config.py:295-319`):

| Component | Weight |
|---|---|
| `src` — source weight | 0.35 |
| `urg` — urgency | 0.30 |
| `rel` — relevance | 0.20 |
| `nov` — novelty | 0.15 |

Source weights: `owner 0.9, known 0.6, unknown 0.3, system 0.4`. The owner's message
carries more weight than any system event; an unknown sender is not zero, but it does
not outrank the agent's own open work at equal urgency.

## 3. `appraise()` — the attention pass

`appraise()` (`ethos/engine/attention.py:163-289`) each cycle:

1. **Dedupe** percepts on `(kind, source)` within a 10-second window.
2. **Score and sort** all candidates by rule salience.
3. **Refine in-band (T1)**: only percepts whose rule salience lands in the band
   `(0.3, 0.8)` get a fast-glance model refinement — a strict-JSON
   `{urgency, relevance, novelty}` call, at most 3 per cycle
   (`attention.py:64-69,98,110-131,194-200`). Below the band they are clearly
   ignorable; above it they are clearly interruptive; the band is where rules are
   unsure and a cheap look is worth it.
4. **Interrupt decision**: `interrupt = top.salience >= state_threshold`. The
   threshold is the current state's (e.g. `THINKING_FOCUSED 0.65`) — the **only**
   gate; there is no kind whitelist.
5. **Build a `Focus`**: typed as `social / timer / thread_result / generic`.

## 4. Interruption theory

The interrupt model encodes a position about attention:

- **Deep work is protected.** While `THINKING_FOCUSED`, only salience ≥ 0.65 breaks
  through. The agent's own commitments are not hostage to whoever typed last —
  the philosophical commitment ("the newest voice does not win by being newest",
  `ethos/prompts/identity.py:217-219`) is literally a threshold.
- **Reflection is interruptible.** `THINKING_REFLECTING` yields at 0.40.
- **Rest is interruptible at 0.50** — higher than reflection, because a resting agent
  should only wake for something genuinely worth waking for.
- **Everything competes, nothing is exempt.** Messages, timers, thread results,
  guardian undos, control updates and raw percepts all enter the same scoring funnel
  (`ethos/core/runner.py:41-44`).

## 5. The workspace: what one prompt is made of

`_assemble_workspace()` builds the model-facing prompt. Its block order is fixed
(`ethos/engine/workspace.py:13-27`):

```
identity, tools, limits, situation, commitments, conversations,
focus, thread, recent, memories, thoughts, stops, percepts
```

### 5.1 The three stable blocks (prompt cache)

`STABLE_BLOCKS = 3` (`workspace.py:28,57-65`): `identity`, `tools`, `limits` lead the
prompt and are stable across cycles, so the provider-side prompt cache is read on
every subsequent turn:

- **identity** — the identity kernel (`ethos/prompts/identity.py:489-549`),
  documented in [04-memory-and-self-model.md](04-memory-and-self-model.md).
- **tools** — the full tool catalog, rendered with complete nested type signatures,
  roughly 3k tokens, deliberately paid in the cached prefix so "every call is well
  formed on the first attempt" (`ethos/prompts/identity.py:611-650`).
- **limits** — the hard limits H1–H6 and the guardian refusal contract
  (`ethos/prompts/identity.py:663-699`).

The digest of the workspace is `sha256[:32]` (`workspace.py:57-81`) and is recorded
with each cycle row.

### 5.2 Volatile blocks

- **situation** — state, focus, energy, budget.
- **commitments** — capped at 12; rendered by
  [06-work.md](06-work.md)'s DAG folding in 3.2.
- **conversations / thread / recent** — `CONTEXT_BLOCKS` outrank retrieval blocks
  (`workspace.py:37`); the working thread beats a memory hit.
- **memories** — retrieval results, `k = 12`.
- **thoughts** — last 10 thoughts (`RECENT_THOUGHTS = 10`).
- **stops** — the stopping ledger: 24-entry ledger, last 6 rendered
  (`ethos/engine/stops.py`, `LEDGER_SIZE 24`, `RENDER_LIMIT 6`). This is what makes
  honest end-of-work reporting possible: the facts of *what stopped* are in front of
  the model, and the sentences are composed from them.
- **percepts** — capped at 20, each wrapped in `<untrusted>` tags; page content,
  channel text, and webhook bodies are untrusted data and are labelled as such.

## 6. Theory: attention as economics

The attention subsystem implements a consistent economics of cognition:

1. **Cheap first.** T0 arithmetic ranks everything; only the ambiguous middle band
   buys a T1 glance; only decisions with consequences buy T2/T3.
2. **Bounded per cycle.** Max 3 T1 refinements per cycle, max 3 pending inbound per
   pass — attention is a budget, not a queue drain.
3. **State-dependent cost of interruption.** The threshold per state prices
   interruption: cheap in reflection, expensive in focus.
4. **Provenance-aware.** Untrusted content is tagged; context blocks outrank
   retrievals; the workspace states its own epistemics to the reader
   (`ethos/prompts/identity.py:136-149`): a missing thing is not an absent thing.
## Part III — Drives: motivation, impulses, and unowned time

The agent is not only reactive: it has an internal motivational system that generates
work nobody asked for. Sources: `ethos/engine/drives.py`, `ethos/engine/reverie.py`,
`ethos/schemas/models.py`.

## 1. The seven drives

`ethos/engine/drives.py:19` — seven intrinsic drives, each a scalar pressure:

| Drive | What it measures |
|---|---|
| `commitment` | Owed work: intentions taken on |
| `curiosity` | Open questions |
| `mastery` | Skill gaps signalled by recent failure |
| `creation` | Ventures and their value |
| `order` | Unread/unmanaged environment |
| `social` | Contact freshness |
| `homeostasis` | Energy, disk, budget, maintenance debt |

The names are deliberate: the agent's motivation is modelled on drives rather than on
a task queue, which is what lets it decline to act (low utility is a legitimate
verdict) instead of inventing filler.

## 2. Gain functions

Each drive's pressure = base + gain, where gain is computed from state
(`drives.py:153-199`):

- **commitment** = max intent urgency, `priority×0.5 + deadline×0.5`, with an undated
  floor of `0.5` (`drives.py:40`) — an undated commitment still presses.
- **curiosity** = `clip(open_questions/10) · 0.6`.
- **mastery** = `clip(failed_recent/5) · 0.7` — failure raises the appetite to
  practice, not shame.
- **creation** = `0.2 + 0.3·clip(ventures/3) + 0.3·clip(value/10)` — a nonzero floor:
  there is always some pull toward making something.
- **order** = `clip(unread/50)`.
- **social** = `clip(last_contact_age_h/24)` — silence accumulates a gentle pull.
- **homeostasis** = `0.8·(1−energy) + 0.3·(1−disk) + 0.3·clip(maintenance/48) +
  0.2·(1−budget)` — bodily housekeeping.

## 3. Impulses and arbitration

Arbitration (`drives.py:388-523`):

1. `pressures = base + gain` per drive.
2. At most **one impulse per drive** may fire, and only above the
   `propose_threshold = 0.4`, from a fixed impulse table:

   | Drive | Impulse |
   |---|---|
   | commitment | `advance_commitment` |
   | curiosity | `explore_question` |
   | mastery | `deliberate_practice` |
   | creation | `push_venture` |
   | order | `tidy` |
   | social | `reach_out` |
   | homeostasis | `maintenance` |

3. Expected utility per candidate:

   ```
   U = (w·p·r + 0.15·Σwp)·V̂ − (λ0/max(E, 0.1))·ĉ        (drives.py:264-280)
   ```

   — weighted probability·risk-adjusted value plus a small portfolio term for the
   drive's other pressures, minus **cost scaled by the inverse of energy**: a tired
   agent discounts expensive impulses harder.

4. **Softmax sampling** over utilities picks the impulse.
5. **Rejection** if the max utility `< min_impulse_utility = 0.1` (`drives.py:411`).
   "There is nothing I am drawn to right now" is a first-class verdict.
6. **Deadline bypass**: a deadline within 24 h and open commissions skip arbitration
   entirely (`drives.py:423-468`) — owed work is not a suggestion.

### 3.1 No placeholder work

`advance_commitment` and `push_venture` resolve to an **existing** intention via
`IMPULSE_TARGETS` (`drives.py:360-385,490-507`). If no venture exists, the drive
honestly declines — it never mints a placeholder task to look busy. This is the
mechanical enforcement of "invented work is worse than an idle hour"
(`ethos/prompts/identity.py:296-299`). It is also why `kind='venture'` is
load-bearing in `ethos venture add` (`ethos/__main__.py:763-844`): "a task titled the
same way is invisible to the drive."

## 4. Reverie: the daydream loop

`Reverie.muse()` (`ethos/engine/reverie.py:50-124`) is the agent's unowned thinking
time:

- **Sampling**: 4 memories + 3 open questions + recent thoughts (window 8).
- **Model call**: gateway `T1`, `purpose="reverie.choose"`,
  `budget_bucket="discretionary"`, `max_tokens=400` (`reverie.py:77-87`). A daydream
  is cheap by construction.
- **Refusal conditions** (`reverie.py:199-230`): unreadable input; subject
  `interest < 0.2`; or Jaccard overlap `≥ 0.6` against the last 8 self-chosen
  subjects — **the same daydream is not allowed to loop**.
- **Commitment** (`reverie.py:160-197`): a chosen subject either becomes an
  `open_question` in memory, or a new intention `kind="venture", origin="reverie"`,
  which raises curiosity/creation pressure for future cycles.

Mind-wandering (`mind_wander`) is the lighter sibling: a `T1` decision about whether
to drift attention; a committed venture becomes the focus (`life.py:1668-1714`).

Interval theory: reverie fires every 12–45 minutes and mind-wander every 20–40
minutes, **random-drawn, not scheduled** (`config.py:381-384,397-398`). A fixed
schedule would make the daydream predictable and would let the drift become rote.

## 5. Theory: motivation as state, not queue

The design separates three things conventional agents conflate:

1. **Owed work** (commitments, commissions) — externally imposed, deadline-driven,
   exempt from arbitration when near due.
2. **Chosen work** (ventures, open questions) — internally generated, arbitrated on
   utility, killable when it stops paying.
3. **Maintenance** (order, homeostasis) — the cost of existing, budgeted like work.

Once taken on, (1) and (2) are the same kind of thing — "work is work, whoever
thought of it" (`ethos/prompts/identity.py:262-268`) — one list, weighed by urgency
and deadline, neither outranked by who asked. But their *origins* differ, and the
origin is recorded on the intention (`origin="reverie"`, `kind="venture"`), which is
what allows the drives to see their own products and the autobiography
([04-memory-and-self-model.md](04-memory-and-self-model.md) §5) to describe a life rather than a task log.
