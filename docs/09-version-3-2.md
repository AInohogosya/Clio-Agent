# The 3.2 direction: graphs, federation, leases, sessions

Version 3.2 (in this checkout as migration `0006_clio_3_2.py` and the engines it
touches) generalizes the 3.1 engines **without adding an org-chart or a Kanban
module**. The resident core is untouched: tick loop, 8-state machine, T0–T3 tiers,
seven drives, honesty guardrails, reversibility-first safety. Primary source:
`AI_AGENT_ARCHITECTURE.md` §23; tests: `tests/unit/test_clio_3_2.py`.

## 1. Recursive intention graphs (RIG)

Schema (`migrations/versions/0006_clio_3_2.py`): `intention_edges` —
parent/child FKs, `edge_type ∈ {depends_on, verifies, synthesizes, informs}`,
`gate_policy` JSONB, unique `(parent, child, type)`. `intentions` gain
`budget_allocated_usd, budget_spent_usd, expected_value, lease_id,
assigned_principal_id`.

Store API (`ethos/memory/store.py`):

- `add_intention_edge()` with **`CycleError`** rejection — a DAG, not a graph with
  cycles; `dag_has_cycle` is the guard.
- `get_ready_intentions()` — active/proposed intentions whose `depends_on`
  predecessors are all `done`: the ready queue is a property of the graph.
- `get_intention_subgraph(root)`.
- `dag_telemetry()` (`store.py:1143`) feeds the drives and the workspace.

### 1.1 Decomposition instead of bigger plans

`ethos/gsl/planner.py`: `needs_decomposition` → `decompose_into_children` →
`decompose_and_persist`. The invariant: **leaf plans stay ≤ 7 steps**
(`MAX_LEAF_STEPS`), while a genuinely complex goal becomes a tree of children with
`depends_on / verifies / synthesizes` edges. `GeneralSolverLoop._maybe_decompose`
hooks decomposition right after understanding (`ethos/gsl/loop.py`) — the loop
itself never exceeds its own plan-size ceiling.

### 1.2 Gates: verification as graph structure

`verifies` edges become **completion gates**: a parent with a `verifies` child
cannot close until that child is done (`gate_children`, `gate_blocks_completion`,
`_unlock_gate_children` in `loop.py`; `ethos/gsl/verification.py`). The gate child
is judged by an **independent diversity-family T3 model or peer resident** —
preserving the invariant that *a judge never overturns failing objective checks*.
Verification stops being a phase and becomes **structure**: work that must be
checked is, by construction, followed by the checking of it.

## 2. EV-proportional budget allocation

- `allocate_ev_budgets` (`ethos/memory/store.py`): intention budgets allocated by
  **value per dollar** across a subgraph, always H2-capped.
- Threads share their parent's carve by EV weight: `ev_weight = ev/cost`,
  `allocate_child_budget` (`ethos/threads/manager.py:28,35`); parallel dispatch of
  ready nodes via `spawn_ready_branches` (`manager.py:422`).
- The gateway's `commitment_reserve_pct = 0.50` bucket split
  ([07-gateway-and-toolhost.md](07-gateway-and-toolhost.md) §5) now composes with per-intention budgets:
  money flows from the person, through the period cap, through the reserve, into
  the graph, into threads.

## 3. Two-tier workspace folding

`ethos/engine/workspace.py`: `summarize_dag` + `render_commitments` — the
commitments block now renders a **compressed DAG summary** (completion ratio,
critical-path blockers, budget burn, ready count) plus **full detail only for the
focus**. A graph of 50 intentions fits in the prompt the way one list of 12
commitments did; attention economics ([03-engine.md](03-engine.md) §6)
extend from percepts to goal structure.

## 4. Drives over the graph

`ethos/engine/drives.py` adds graph-derived gains:

- `dag_blocked_edges` → raises **`order`** and **`commitment`** on blocked critical
  paths — structural blockage becomes motivational pressure.
- `leaf_verification_failures` → raises **`mastery`** — repeated verification
  failure is a skill signal, not just a retry counter.
- `anomalous_outcomes` → raises **`curiosity`** — something that ended strangely is
  worth a look.
- `Life._drive_inputs` / `_assemble_workspace` feed `dag_telemetry()`
  (`ethos/engine/life.py`).

## 5. Federated residents

The `people` table becomes a principal registry: `kind (human | resident |
service)`, `capabilities`, `authority_scope`, `endpoint_uri`
(`0006_clio_3_2.py`). Store/match API in `ethos/social/relationships.py`:
`register_resident`, `list_residents`, `match_resident` (by capabilities, trust,
authority).

- **Bimodal etiquette** (`ethos/social/etiquette.py`: `_principal_kind`,
  `is_resident_address`, `_deliver_resident`): humans keep quiet hours,
  120-minute digests, and the 6/3/1 caps; **residents bypass** — a fast-path in
  `OutboundPipeline.send` delivers structured JSON over the event bus.
  Machine-to-machine speech is not rationed by human sleep.
- **Commissions between residents**: `EventKind.COMMISSION_*` kinds,
  `COMMISSION_KINDS` (`ethos/bus/event_bus.py:19`), `publish_commission` with a
  resident fast path (`:208`).
- **Epistemic checks on commissions** (`ethos/social/deliberation.py`:
  `evaluate_commission`, `deliberate_commission`): flawed/under-budgeted specs →
  `ask_clarification`; outside skill/authority → `boundary`; legitimate
  disagreement → `deliberate_disagreement` — the same reply-path discipline as
  human commissions ([05-social.md](05-social.md) §5), and
  `_withhold_unearned_progress()` is enforced on **all** inter-resident updates.
  The prompt note is `COMMISSION_NOTE` (`ethos/prompts/deliberation.py:624`).

## 6. Scoped memory and namespaces

`episodes / facts / knowhow / questions` gain `scope (private | project | org)` and
`namespace_id` with indexes. Retrieval (`ethos/memory/retrieval.py`) unions
**private** with **authorized** project/org namespaces; **episodes stay private**
always. Nightly promotion (`ethos/memory/consolidate.py:_promote_scoped_memories`)
promotes *verified* facts and *high-success* know-how to project/org scope through
T3 contradiction resolution, and updates `self_model` skills/calibration from
attempt statistics. A resident can serve an organization without leaking its diary.

## 7. Workspace leases

`WorkspaceLease` / `WorkspaceLeaseManager` (`ethos/threads/context.py:52-260`),
specified in [06-work.md](06-work.md) §3. Summary of the invariants:

- A lease is a **git worktree** (for repos) or a **hardlink snapshot tree**
  (otherwise); `check_allowed_path` gates every path used under it.
- `commit_lease` is per-target-locked; `rollback_lease` restores via trash
  (`lease_rollback`) + snapshot `restore()`.
- `GuardianGate.review()` enforces `allowed_paths` / `budget_limit_usd`
  (`ethos/guardian/gate.py:_review_lease`) — the fence is enforced at the gate, not
  by the tenant's good behaviour.

## 8. Session drivers

`SessionDriver` (`ethos/gateway/providers/base.py`): a stateful session/CLI driver
participates in `ModelRouter.select` with `session_score` carrying **`c_hat ≈ 0`**
(`ethos/gateway/router.py`: `is_session_entry`, `marginal_cost`) — a subscription
session has no marginal token cost, so at equal quality it outranks paid APIs. On
429 or breaker failover, the gateway **replays the normalized conversation** on the
next driver (`ethos/gateway/service.py`: `register_session_driver`,
`replay_request`, `_session_replay`) — failover preserves context.

## 9. The signed toolshed

Toolshed manifests (`ethos/toolhost/tools/toolshed.py`) gain first-class fences:
`allowed_paths, allowed_hosts, secret_scopes, timeout_s`, a SHA-256
`content_hash`, and an **Ed25519 signature** (`verify_tool_signature`, `:22`).
Registered via `tools.register`, listed via `tools.list`, run via `tools.run` with
hash/signature/fence checks — and `GuardianGate.review()` enforces the manifest's
fences **before execution** (`ethos/guardian/classifier.py:check_manifest_paths`).
An agent-written tool can never exceed what its signature vouched for.

## 10. Theory: scale without org charts

The 3.2 direction is a bet about how a resident agent scales:

- **Structure, not hierarchy.** Coordination is expressed as graph edges
  (`depends_on / verifies / synthesizes`) rather than as managers, roles, or boards.
  The seven drives and one GSL remain the only engines; graphs make them see
  more.
- **Money follows value, structurally.** EV allocation + reserves + caps means
  budget is a property of the *work graph*, auditable end to end.
- **Peers, not users.** Other residents are principals with capabilities and
  authority scopes, addressed with the same etiquette and the same honesty
  guardrails — federation multiplies the agent without multiplying the rules.
- **Enforcement stays at the boundary.** Every new capability (leases, toolshed,
  sessions) is enforced at the gate or the router, never by convention inside the
  agent.
