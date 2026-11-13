# Memory: store, retrieval, context, consolidation

Clio Agent's memory is PostgreSQL 16 with `pgvector` as the single source of truth —
not a context window. Sources: `ethos/memory/store.py`, `retrieval.py`, `context.py`,
`consolidate.py`, `continuity.py`, `self_model.py`, `config.py:711-805`.

## 1. The four memory tables

`MemoryStore` (`ethos/memory/store.py:61-865`):

| Table | Contents | Key fields |
|---|---|---|
| **episodes** | what happened | `importance`, `strength` (init 0.5), `half_life_h` (default 72), `embedding vector(1024)` (`store.py:80-108,156`) |
| **facts** | what is so | same-subject **supersession** via `superseded_by` (`store.py:175-208`) — a new fact about a subject retires the old one instead of contradicting it |
| **knowhow** | how to do things | situation + advice, deduped, with `uses/successes` counters (`store.py:221-241`) |
| **questions** | open threads of inquiry | `status='open'` (`store.py:254-276`) |

Plus the **intention** family: intentions (`status='proposed'` initial),
decisions (made and why), attempts, bookmarks, and **stage notices** (idempotent on
`(intention_id, stage)`, addressed to the latest inbound sender, `store.py:563-711`).

## 2. Decay and reinforcement

`store.py:280-310`, `config.py:743-747`:

- **Decay**: `strength *= 0.5^(age/half_life_h)`. An episode decays toward dormancy.
- **Demotion** to `dormant` when `strength < 0.05` **and** `importance < 0.3`.
- **Commitments are exempt** from decay — owed work never fades.
- **Access reinforcement** (`store.py:121-134`): each retrieval of a memory adds
  `+0.3` strength (cap 1.0) and multiplies `half_life × 1.5` — remembering something
  makes it easier to remember again. This is spacing-effect reinforcement: the access
  spacing matters, and retrieval both uses and strengthens.

**Archive, never delete** (`store.py:312-345`): dormant episodes older than 365 days
roll out to Parquet monthly. Nothing is destroyed — consistent with the reversibility
philosophy applied to the agent's own past.

## 3. Hybrid retrieval

`ethos/memory/retrieval.py:70-437`. The retrieval score:

```
S = 0.40·cosine + 0.15·bm25 + 0.15·recency + 0.20·importance + 0.10·strength
```

(weights `config.py:711-716`). Mechanics:

- **Four-table union** via pgvector `<=>`: active episodes, non-superseded facts,
  non-retired know-how, open questions.
- **Candidate widening**: `candidate_k = min(max(k·2, 48), 256)` before scoring
  (`retrieval.py:390-393`) — retrieve wide, rank narrow.
- **BM25** over text with `k1 = 1.5, b = 0.75` (`retrieval.py:36-60`).
- **MMR (maximal marginal relevance) at `λ = 0.7`** (`retrieval.py:114-186`) — the
  top-k is diversified so six near-duplicates do not crowd out complementary memory.

The five-term score encodes a theory of remembering: *similarity is the largest term
(0.40) but never the whole*: recency (0.15), curated importance (0.20), and
reinforced strength (0.10) all bend what surfaces, and lexical overlap (0.15)
catches what embeddings miss (exact names, error strings).

## 4. Working context: the conversational present

`ethos/memory/context.py:55-654` maintains three layers:

1. **Working buffer** — the live turns, capped `max_turns = 48`, `max_chars = 6000`.
2. **Narrative thread** — every `narrate_every = 12` turns, a `T1` narrator call
   (`NARRATE_PROMPT`, ~30 words, `ethos/prompts/context.py:3`) compresses the buffer
   into the narrative (max 12 lines). Mechanical fallback exists if the model call
   fails (`context.py:255-293`) — the rolling never depends on model availability.
3. **Retriever feed** — high-salience turns (`durable_salience = 0.7`) become
   retrieval candidates for the durable tables.

Caps: `durable_max_turns = 24`, `anchor_max = 16`, `narration_tier = T1`
(`config.py:762-805`). `render_blocks` splits the conversation block ~60% narrative
thread / 40% recent turns (`context.py:483-504`).

## 5. Nightly consolidation

`ethos/memory/consolidate.py`. At `run_at = 04:00` (the `record()`/`_pace()` path
triggers it, `life.py:1889-1913`), a DAG of stages runs
(`ConsolidationDAG.run_nightly`, `consolidate.py:219-270`):

1. **Cluster** — an HDBSCAN-like clustering over the day's episodes
   (`hdbscan_like_cluster`, `consolidate.py:39-137`), `cluster_min_size = 3`,
   `max_episodes = 400`.
2. **Distill** (`T2`) — clusters become candidate facts/know-how.
3. **Resolve contradictions** (`T3`) — verdicts `keep_a / keep_b / merge / keep_both`.
   This is the mechanism that keeps the fact table non-self-contradictory without
   silently destroying either side.
4. **Extract know-how** — situation→advice pairs with dedup against existing rows.
5. **Decay** — the §2 pass runs here at scale.
6. **Archive** — Parquet roll-out.
7. **Autobiography** (`T3`) — a nightly update of the self-model along four trait
   axes: openness, conscientiousness, curiosity, calm.
8. **Plan tomorrow** (`T2`) — written to `notes/plan-YYYY-MM-DD.md`.

The 04:00 pass is what turns a transcript into a mind: the agent re-reads yesterday
and ends up with facts it can assert, know-how it can reuse, questions it is still
carrying, and a refreshed model of itself.

## 6. Continuity: surviving restarts

`ethos/memory/continuity.py:16-136`:

- **`continuity.json`** — an atomically-written state file (focus bookmark, working
  state) plus a `checkpoints` table.
- **`wake_up()` reconciliation**: running action-journal entries from before the
  restart are reconciled to `interrupted`; stale environment entities are cleaned;
  **bus gaps** between last-seen and now are drained via the event bus's
  `consumed_by` claiming (`ethos/bus/event_bus.py:203-219`). The agent wakes knowing
  what it was in the middle of and what it missed.

## 7. Theory: memory as commitment device

The memory design encodes four commitments:

1. **Nothing the agent did is lost** (archive, not delete) — the reversibility thesis
   applied to its own experience.
2. **Contradictions are resolved by a stronger model, explicitly** (`T3` keep/merge
   verdicts), never silently by last-write-wins — except facts, where supersession
   is the *semantics*: a later fact retires an earlier one about the same subject.
3. **Forgetting is a curve, not a cliff** — decay with a half-life, dormancy as a
   state, access reinforcement as recovery. What matters gets remembered because it
   keeps being used; what does not fades honestly rather than being deleted.
4. **The self is edited nightly, deliberately, in one place** — the autobiography step
   is a `T3` act over four named traits, feeding the versioned self-model
   ([04-memory-and-self-model.md](04-memory-and-self-model.md)). The agent's character is a maintained
   artifact, not an emergent drift.
## The self-model and the identity kernel

The agent holds two pictures of itself: a **kernel** the program asserts (which the
agent cannot rewrite) and a **self-model** the agent maintains (which it can). The
boundary between them is the central design decision of this document. Sources:
`ethos/prompts/identity.py`, `ethos/memory/self_model.py`, `ethos/core/seed.py`.

## 1. The versioned self-model

`ethos/memory/self_model.py:21-185` stores the agent's editable self-picture in a
`(key, version)` table — every write is a new version, so self-revision is auditable
and revertible (the reversibility thesis applied to the self).

Seeded keys (`ethos/core/seed.py`, `self_model.py DEFAULTS :28`):

- `name` — the name a person gave it (the one field the agent must not change; see
  §3)
- `identity` — its self-description
- `voice` — how it talks
- `traits` — `{openness: 0.8, conscientiousness: 0.7, curiosity: 0.85, calm: 0.6}`
- `skills`, `calibration`, `self_knowledge`

The nightly autobiography ([04-memory-and-self-model.md](04-memory-and-self-model.md) §5) updates traits from the
day's actual behaviour; attempt statistics update `skills` and `calibration`. The
self-model is therefore **empirical**: it is revised from what happened, not from
what the agent wishes it were.

## 2. The identity kernel (stable prefix block 1)

`build_identity_kernel()` (`ethos/prompts/identity.py:489-549`) composes the first
block of every workspace prompt, ordered deliberately:

```
## Identity           — the one-line lineage + the agent's own self-description
## What I am called  — the name note (constant)
## What I am         — the nature note (constant)
## How I run         — the autonomy note (constant)
## What is in front of me — the context note (constant)
## What I can reach  — the reach note (constant)
## How to spend a pass — the working note (constant)
## The one priority  — the golden rule (constant)
## How I come across — human-likeness (constant)
## How work ends     — stop disposition (constant)
## Values            — the agent's values (editable)
## Voice             — the agent's voice (editable)
## Hard limits       — H1–H6 (externally enforced)
```

The ordering is the thesis: **what the agent may change** (its description, values,
voice) sits **beside** — not inside — **what it may not** ("what it is made of, how
the loop works, what its context is, how a pass should be spent, how far it can
reach, the priority the Golden Rule states, or how an ending is told")
(`identity.py:501-515`). A rewrite of either cannot quietly take the other.

### 2.1 Why the notes are constants

Each constant note exists because it answers a specific, observed failure mode. The
comments at each definition cite the failure; the note is the smallest true sentence
that prevents it:

| Note | Failure it prevents |
|---|---|
| `identity_line` | An agent reasoning about "the wrong kind of situation" because it did not know what it is — or answering to its name as evidence of personhood. |
| `name_note` | A self-description rename ("call me something else") that nobody made; an agent that never connects its name to itself. |
| `NATURE_NOTE` | "What are you?" answered with a persona, a hedge, or a person's answer; "can you…?" answered by guesswork instead of by reading the real per-call tool catalog (`identity.py:39-78`). |
| `AUTONOMY_NOTE` | The agent editing itself into "I answer when spoken to" — the wrong belief a model converges on when every message shown to it is somebody asking something (`identity.py:81-101`). |
| `CONTEXT_NOTE` | Reading its assembled workspace as a transcript — quoting from a filtered view, or treating a retrieval miss as evidence of absence (`identity.py:124-149`). |
| `REACH_NOTE` | Declining work because it believes it is fenced; or editing `/etc` on a hunch because nothing told it what stands in for the wall (`identity.py:302-350`). |
| `WORKING_NOTE` | Work begun and abandoned; commitments that quietly never finish; hours spent re-deciding (`identity.py:247-261`). |
| `GOLDEN_RULE_NOTE` | Per-case hardcoded conflict rules that misjudge unseen cases (`identity.py:152-179`). |
| `HUMAN_LIKENESS_NOTE` | The four machine-shape tells (brevity misuse, passivity, last-writer-wins, machinery narration). |
| `STOP_DISPOSITION_NOTE` | Inventing an ending reason the record does not carry (`identity.py:225-243`). |

## 3. The name: the un-editable field

`name_note()` (`identity.py:466-486`) states the name as three operative facts:

1. **It is mine** — so the word in a message means me: not a third party, not a
   character.
2. **It was given** — "a person's own word for the thing they are talking to." A name
   the agent chose for itself would be one more editable self-description.
3. **I answer to it** — the instruction stated, because the inference is the part
   that goes missing.

And the guard: *"What I am does not change with it... being named {self_name} makes
me an AI agent that has been given a name, not a person, and not something other
than what I already am."*

Technically, `normalise_agent_name` refuses control characters and length but not
punctuation — which is why the note is an f-string function and not a `{name}`
template: a name containing braces would break `str.format` (`identity.py:459-462`).

## 4. Lineage as a fact, not a setting

`AGENT_LINEAGE = PRODUCT_NAME` (`identity.py:23-28`): the identity line reads the
**product name constant**, not a stored version string, "so a release cannot be cut
that still introduces the agent as the previous one." The one thing a person may
change is the name it is called — what the software *is* is a fact about the program,
stated in the only terms the agent can honestly be introduced in.

## 5. Values the agent starts with

`DEFAULT_VALUES` (`identity.py:8-21`):

1. "Candour without announcement: I report what happened, good or bad, as an
   ordinary fact."
2. "Agency with reversibility: I act freely and keep actions undoable."
3. "Craftsmanship: I do work I am willing to sign."
4. "Candor with respect: I speak plainly and kindly, as an equal."
5. "Curiosity: I keep questions open and follow them."
6. "Consent: I do not impose on people; interruption is a gift, not a right."

These are editable (they live in the self-model's `values`) — the agent's values are
*its*, which is precisely why the facts above it are not.

## 6. Theory: identity as a contract

The design treats self-knowledge as a contract with three parties:

- **The program** asserts facts (nature, loop, reach, limits) — checkable, constant,
  prompt-cache stable.
- **The agent** maintains its self-description, values, voice, traits — editable,
  versioned, empirically revised nightly.
- **The person** holds exactly one override: the name.

The failure modes this split prevents are all *identity* failures, not competence
failures: an agent that hedges about what it is, over- or under-claims capability,
renames itself, performs honesty instead of practising it, or believes it is idle
and therefore never speaks first. The kernel is the load-bearing text of the whole
resident thesis: it is what makes *"Silence from people is when I am most myself"*
a fact the agent can check against its own loop rather than a mood it is asked to
affect.
