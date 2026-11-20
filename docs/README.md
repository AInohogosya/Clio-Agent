# Clio Agent — Design Documentation

This folder documents the **fine-grained mechanisms, specifications, theories, and
philosophy** of **Clio Agent 3.1** (Python package `clio`). It is written against the
code in this checkout: every load-bearing claim carries a `file:line` reference, and
where the code and the prose disagree, the code is the source of truth.

## What the agent is

Clio Agent is not a function that maps a prompt to a reply. It is a **resident**: a
continuously-running autonomous agent with its own clock, its own memory, its own
intentions, and its own sense of time. Its core is a tick-based **life loop**
(`pace → perceive → attend → decide → act → record`) that runs whether or not anyone
is talking to it (`clio/engine/life.py:147-151`).

Two commitments run through everything:

> **Golden rule** (`clio/prompts/identity.py:171-179`): *human-likeness and performance
> are both mandatory. Neither is sacrificed for the other.*

> **Safety thesis** (`clio/guardian/`): *safe by reversibility, not by refusal.*
> Deletions go to trash, writes are backed up before they land, every action is journaled,
> and six hard limits (H1–H6) are enforced **outside the mind**.

## The ten documents

| Document | Covers |
|---|---|
| [01-philosophy.md](01-philosophy.md) | The resident thesis; the golden rule; the honesty discipline; silence, idleness and consent; why prompts are facts, not personas |
| [02-safety-and-guardian.md](02-safety-and-guardian.md) | **Part I** — reversibility over refusal, the hard limits H1–H6, what is and is not enforced, oversight supremacy, approvals. **Part II** — the guardian gate, the action classifier, the write-ahead journal, trash, snapshots, the artifact registry, the audit chain |
| [03-engine.md](03-engine.md) | **Part I** — `life()`, `_cycle()`, the 8-state machine, rhythm and energy. **Part II** — salience scoring, tiers T0–T3, interruption thresholds, the 13-block workspace. **Part III** — the seven drives, impulse arbitration, reverie and mind-wandering |
| [04-memory-and-self-model.md](04-memory-and-self-model.md) | **Part I** — episodes/facts/know-how/questions, decay and reinforcement, hybrid retrieval, working context, nightly consolidation, continuity. **Part II** — the identity kernel, the versioned self-model, what the agent may and may not rewrite about itself |
| [05-social.md](05-social.md) | **Part I** — deliberation, the six reply actions, ignore reasons, the honesty guardrail, commissions. **Part II** — the outbound pipeline, quiet hours, digests, per-person budgets, cross-talk, relationships |
| [06-work.md](06-work.md) | **Part I** — the General Solution Loop: understand → devise → plan → execute → evaluate → verify; acceptance, stage notices, parking. **Part II** — threads and workers, workspaces and leases, the subagents Foundry / APEX / AXIOM |
| [07-gateway-and-toolhost.md](07-gateway-and-toolhost.md) | **Part I** — model routing (`q − μ·ĉ − ν·l̂`), providers, response normalization, honest pricing, the spend guard, circuit breakers, embeddings. **Part II** — the 55-tool catalog, execution and idempotency, code kernels, browser and desktop, the toolshed |
| [08-runtime.md](08-runtime.md) | **Part I** — the seven supervised processes, the supervisor, bootstrap, the transactional event bus, the control plane, platform abstraction. **Part II** — channels as doors, the ten adapters, webhook/JWT security. **Part III** — metrics, the hash-chained audit log, exhaustive logging, secret redaction |
| [09-version-3-2.md](09-version-3-2.md) | The 3.2 direction: recursive intention graphs, EV budget allocation, federated residents, leases, session drivers, the signed toolshed |

## Conventions used in these documents

- **`file:line` references** point at this checkout, e.g. `clio/engine/rhythm.py:92-122`.
- **Parts.** Where two or three subjects share a document, each subject is a `Part`
  heading; section numbering restarts per part, so a reference like "§5" means the
  5th section of the part under discussion.
- **Stated-once rule.** Where the docs quote the code's design comments (markers
  `[D-xx]`, `[H1..H6]`, `[P1..P7]`), those markers are the code's own discipline tags
  for design decisions; the code carries the rationale at the definition site.
- **Numbers are defaults.** Nearly every constant is overridable via environment
  (`CLIO_*`), config YAML in `config/`, or the live control plane; the values quoted
  here are what the code ships with.
- **No invented behaviour.** If a mechanism is not in this checkout yet (e.g. parts of
  the 3.2 direction), the document says so explicitly rather than describing it as
  shipped.

## Relationship to the other documents in the repo

- `README.md` (repo root) — the product-facing tour and operations guide.
- `AI_AGENT_ARCHITECTURE.md` — a compact architecture map with file:line references;
  the docs here expand on it and stay consistent with it.
- `clio-agent-3-1-explained.md` — a narrative walkthrough; superseded where the code
  has moved, kept for the storytelling.
- `CHANGELOG.md` — release-by-release changes.
