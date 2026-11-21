# 04 — Memory and the self-model

Clio Agent's memory is PostgreSQL 16 with `pgvector` as the single source of truth — not a
context window. Sources: `clio/memory/store.py`, `retrieval.py`, `context.py`,
`consolidate.py`, `continuity.py`, `self_model.py`, `clio/core/seed.py`,
`clio/prompts/identity.py`, `clio/config.py` (`MemoryConfig`, `config.py:820-828`).

## Part I — Memory

## 1. The tables

`MemoryStore` (`clio/memory/store.py:200+`):

| Table | Contents | Key fields / behaviour |
|---|---|---|
| **episodes** | what happened | `importance`, `strength` (init 0.5), `half_life_h` (default 72), `embedding vector(1024)` |
| **facts** | what is so | same-subject **supersession** via `superseded_by` — a new fact about a subject retires the old one instead of contradicting it |
| **knowhow** | how to do things | situation + advice, deduped, with `uses/successes` counters |
| **questions** | open threads of inquiry | `status='open'` |

Plus the intention family: `intentions` (initial status `proposed`), `decisions`,
`attempts`, `bookmarks`, `stage_notices` (idempotent on `(intention_id, stage)`, addressed
to the latest inbound sender), and in 3.2 `intention_edges` and `workspace_leases`
([09](09-version-3-2.md) §1, §7). Since migration `0008`, every isolated table also carries
`(tenant_id, space_id)` with RLS ([10](10-sandboxes-steering-and-policy.md) §8).

Scoped memory (3.2): `episodes / facts / knowhow / questions` carry
`scope (private | project | org)` and `namespace_id` with indexes
(`migrations/versions/0006_clio_3_2.py:60-72`).

## 2. Decay and reinforcement

`decay_pass` (`store.py:543-573`), config at `clio/config.py:749-769`:

- **Decay**: `strength *= 0.5^(age/half_life_h)`. An episode decays toward dormancy.
- **Demotion** to `dormant` when `strength < 0.05` **and** `importance < 0.3`
  (`DecayConfig`, `config.py:755-759`).
- **Commitments are exempt** from decay (`commitments_exempt: True`) — owed work never fades.
- **Access reinforcement** (`SpacingConfig`, `config.py:749-753`; applied on retrieval):
  each retrieval of a memory adds `+0.3` strength (cap 1.0) and multiplies
  `half_life × 1.5` — remembering something makes it easier to remember again.

**Archive, never delete** (`archive_pass`, `store.py:575-620`; `archive_after_days: 365`):
dormant episodes older than 365 days roll out to Parquet monthly. Nothing is destroyed —
the reversibility philosophy applied to the agent's own past.

## 3. Hybrid retrieval

`clio/memory/retrieval.py`. The score (`:83-115`):

```
S = 0.40·cosine + 0.15·bm25 + 0.15·recency + 0.20·importance + 0.10·strength
```

(weights `RetrievalWeights`, `config.py:723-729`).

Mechanics:

- **Four-table union** via pgvector `<=>`: active episodes, non-superseded facts,
  non-retired know-how, open questions.
- **Candidate widening**: `candidate_k = min(max(k·2, 48), 256)` before scoring
  (`retrieval.py:402-430`; `config.py:735,746`) — retrieve wide, rank narrow.
- **BM25** over text with `k1 = 1.5, b = 0.75` (`retrieval.py:36-60`).
- **MMR at λ = 0.7** (`retrieval.py:117-176`) — the top-k is diversified so six
  near-duplicates do not crowd out complementary memory.
- Scoped retrieval (3.2) unions **private** with **authorized** project/org namespaces;
  **episodes stay private** always ([09](09-version-3-2.md) §6).

The five-term score encodes a theory of remembering: similarity is the largest term (0.40)
but never the whole; recency (0.15), curated importance (0.20) and reinforced strength
(0.10) bend what surfaces, and lexical overlap (0.15) catches what embeddings miss —
exact names, error strings.

**Embedding honesty**: vectors come from `FastembedEmbedder` (`BAAI/bge-large-en-v1.5`,
1024 dims, ~1.2 GB of ONNX weights) with a zero-dependency `HashingEmbedder(dim=1024)`
fallback — the memory layer never depends on a model being reachable
(`clio/gateway/embeddings.py:39-160`, [07](07-gateway-and-toolhost.md) §6). The dimension
is asserted against the model (`assert_model_dim`, `embeddings.py:87-107`): switching to a
384-dim model without a migration is refused loudly, because memories already in the store
were embedded by the previous model.

## 4. Working context: the conversational present

`clio/memory/context.py` maintains three layers:

1. **Working buffer** — the live turns, capped `max_turns = 48`, `max_chars = 6000`
   (`config.py:774-818`).
2. **Narrative thread** — every `narrate_every = 12` turns (or when the durable buffer is
   over `durable_max_turns = 24`), a `T1` narrator call compresses the stretch into the
   narrative and extracts **anchors** — items that stay on the thread in front of the agent
   until the anchor list itself is full (`context.py:189-330`, `:384-420`). A mechanical
   fallback exists if the model call fails, so rolling never depends on model availability.
3. **Retriever feed** — high-salience turns (`durable_salience = 0.7`) become retrieval
   candidates for the durable tables.

`render_blocks` splits the conversation block between narrative thread and recent turns
(~60/40). The open-loop tracking also powers the "unanswered queue" scenario
(`tests/scenarios/test_unanswered_queue.py`): the agent can ask the store when it is not
certain it still remembers what was said.

## 5. Nightly consolidation

`clio/memory/consolidate.py`. Triggered by the life loop at `run_at = 04:00`
(`life.py:3327-3350`, on a clock, not on sleep). `run_nightly`
(`consolidate.py:308-378`) runs a DAG of stages — the code's own order comment:
`cluster → distill → contradictions → knowhow → demote → archive → autobiography → plan`:

1. **Cluster** — an HDBSCAN-like clustering over the day's episodes
   (`hdbscan_like_cluster`, `consolidate.py:45-140`; `cluster_min_size = 3`,
   `max_episodes = 400`).
2. **Distill (T2)** — clusters become candidate facts/know-how.
3. **Resolve contradictions (T3)** — verdicts `keep_a / keep_b / merge / keep_both`
   (`_resolve_contradictions`, `consolidate.py:380-430`; threshold
   `contradiction_confidence = 0.5`). This keeps the fact table non-self-contradictory
   without silently destroying either side. A failed resolution run is logged, not fatal.
4. **Extract know-how** — situation→advice pairs, deduped against existing rows.
5. **Demote + archive** — the §2 passes at scale.
6. **Scoped promotion (3.2)** — `_promote_scoped_memories` (`consolidate.py:233-306`)
   promotes *verified* facts and *high-success* know-how to project/org scope **after**
   contradiction resolution, and updates `self_model` skills/calibration from attempt
   statistics.
7. **Autobiography (T3)** — a nightly update of the self-model along four trait axes
   (`AUTOBIOGRAPHY_PROMPT`, `consolidate.py:161-166`).
8. **Plan tomorrow (T2)** — written to `notes/plan-YYYY-MM-DD.md`.
9. **Model calibration** — the nightly `_calibrate_models` pass records measured model
   behaviour; the router consumes it (`clio/gateway/calibration.py`,
   [07](07-gateway-and-toolhost.md) §7).

The 04:00 pass is what turns a transcript into a mind: the agent re-reads yesterday and
ends up with facts it can assert, know-how it can reuse, questions it is still carrying,
and a refreshed model of itself.

## 6. Continuity: surviving restarts

`clio/memory/continuity.py:19-160`:

- **`continuity.json`** — an atomically-written state file (focus bookmark, working state,
  stops ledger) plus a `checkpoints` table whose payloads are **sealed under the space's
  envelope key** ([10](10-sandboxes-steering-and-policy.md) §8).
- **`wake_up()`** (`:77-145`): reconciles still-`running` action-journal rows to
  `interrupted` (their effect is unknown); cleans stale environment entities; **drains bus
  gaps** between last-seen and now via the event bus's `consumed_by` claiming
  (`clio/bus/event_bus.py:241-260`). The agent wakes knowing what it was in the middle of
  and what it missed.
- The bookmark mechanism is what lets a stop-and-resume continue mid-thought
  (`tests/scenarios/test_restart_continuity.py`).

## Part II — The self-model and the identity kernel

## 7. The versioned self-model

`clio/memory/self_model.py:21-160` stores the agent's editable self-picture in a
`(key, version)` table — every write is a new version with `changed_by` and a `reason`
(`set`, `:130-143`), so self-revision is auditable and revertible: the reversibility thesis
applied to the self. Writes publish `PRESENCE_UPDATE` with the key and version.

Seeded keys (`DEFAULTS`, `self_model.py:28+`; seeded by `clio/core/seed.py:20`):

- `name` — the name a person gave it (the one field the agent must not change; §9)
- `identity` — its self-description
- `voice` — how it talks
- `traits` — `{openness: 0.8, conscientiousness: 0.7, curiosity: 0.85, calm: 0.6}`
- `values` — the six DEFAULT_VALUES from the identity module
- `skills`, `calibration`, `self_knowledge`

The nightly autobiography updates traits from the day's actual behaviour; attempt
statistics update `skills` and `calibration`. The self-model is **empirical**: revised from
what happened, not from what the agent wishes it were.

Name seeding has a guard (`self_model.py:96-108`): a `name` row is only seeded when the
configured name is valid and not the default — a rename by configuration is honoured, a
rename by the agent is a new version like any other.

## 8. The identity kernel (stable prefix block 1)

`build_identity_kernel()` (`clio/prompts/identity.py:545-638`) composes the first block of
every workspace prompt, ordered deliberately:

```
## Identity            — the one-line lineage + the agent's own self-description
## What I am called    — the name note (constant)
## What I am           — the nature note (constant)
## How I run           — the autonomy note (constant)
## What is in front of me — the context note (constant)
## What I can reach    — the reach note (constant)
## How to spend a pass — the working note (constant)
## The one priority    — the golden rule (constant)
## How I come across   — human-likeness (constant)
## How work ends       — stop disposition (constant)
## Values              — the agent's values (editable)
## Voice               — the agent's voice (editable)
## Hard limits         — H1–H6 (externally enforced)
```

The ordering is the thesis: **what the agent may change** (description, values, voice) sits
**beside** — not inside — **what it may not** ("what it is made of, how the loop works,
what its context is, how a pass should be spent, how far it can reach, the priority the
Golden Rule states, or how an ending is told"). A rewrite of either cannot quietly take the
other. Each constant note cites, in its own comment, the observed failure it prevents — the
table in [01](01-philosophy.md) §5 lists them with line numbers.

The workspace assembler deliberately does **not** repeat the name in the volatile blocks
(`clio/engine/workspace.py:240-252`): repeating it would invite reading it as something that
varies, and "the one field in this prompt that is a person's word for the thing they are
talking to is the last one to look like weather."

## 9. The name: the un-editable field

`name_note()` (`clio/prompts/identity.py:522-543`) states three operative facts — it is
mine, it was given, I answer to it — and the guard: being *named* `self_name` makes it an
AI agent that has been given a name, not a person, and not something other than what it
already is. `AGENT_LINEAGE = PRODUCT_NAME` (`identity.py:28`): the lineage line reads the
product-name constant, "so a release cannot be cut that still introduces the agent as the
previous one."

The hard-limit block (`HARD_LIMIT_TEXT`, `identity.py:725-736`) and the refusal contract
(`build_limits_block`, `:737-771`) close the kernel: the limits are marked
"non-negotiable, externally enforced", and a refusal is framed as a decision to read and
route around, never to rephrase past ([02](02-safety-and-guardian.md) §3, §11).

## 10. Theory: memory as commitment device

The memory design encodes four commitments:

1. **Nothing the agent did is lost** (archive, not delete) — reversibility applied to its
   own experience.
2. **Contradictions are resolved by a stronger model, explicitly** (T3 keep/merge verdicts),
   never silently by last-write-wins — except facts, where supersession *is* the semantics.
3. **Forgetting is a curve, not a cliff** — decay with a half-life, dormancy as a state,
   access reinforcement as recovery. What matters gets remembered because it keeps being
   used; what does not fades honestly rather than being deleted.
4. **The self is edited nightly, deliberately, in one place** — the autobiography step is a
   T3 act over four named traits, feeding the versioned self-model. The agent's character
   is a maintained artifact, not emergent drift.

## 11. Identity as a contract with three parties

- **The program** asserts facts (nature, loop, reach, limits) — checkable, constant,
  prompt-cache stable.
- **The agent** maintains its self-description, values, voice, traits — editable, versioned,
  empirically revised nightly.
- **The person** holds exactly one override: the name.

The failure modes this split prevents are all *identity* failures, not competence failures:
an agent that hedges about what it is, over- or under-claims capability, renames itself,
performs honesty instead of practising it, or believes it is idle and therefore never speaks
first.

## 12. What this document does not claim

- **Encryption scope**: episode bodies, fact provenance, journal arguments and continuity
  checkpoints seal under the space's DEK; secrets and the sealed columns are covered —
  ordinary text columns are not ([10](10-sandboxes-steering-and-policy.md) §8).
- **Consolidation is nightly and batchy**: between 04:00 runs, new episodes are retrieved
  raw; the distilled view of "today" lags the day.
- **The Parquet archive is one-way convenience**: rows are demoted/archived by the nightly
  pass; there is no automated re-import path back into the live tables.
- **Scoped promotion is conservative**: only verified facts and high-success know-how leave
  `private` scope, and episodes never do — an organization serving itself from a resident
  sees its institutional memory, not its diary.
- **The self-model is advisory**: nothing mechanically prevents a `set()` of any key except
  the convention that the kernel renders constants from code, not from the store — the
  protection of `name`/lineage is that the *kernel* does not read them from the editable
  table, not that the table refuses writes.
