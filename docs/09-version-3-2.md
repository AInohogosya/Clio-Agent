# 09 — The 3.2 direction and the 3.1 additions

Version 3.2 (in this checkout as migration `0006_clio_3_2.py` and the engines it touches)
generalizes the 3.1 engines **without adding an org-chart or a Kanban module**. The resident
core is untouched: tick loop, 8-state machine, T0–T3 tiers, seven drives, honesty
guardrails, reversibility-first safety. The 3.1 *containment* wave (migration
`0008_multi_tenant_sandbox.py`) added spaces, RLS, sandboxes, steering and policy — covered
operationally in [10](10-sandboxes-steering-and-policy.md); this document is the map of the
3.2 engines and an honest account of what is shipped code versus forming story.

Primary sources: `migrations/versions/0006_clio_3_2.py`, `migrations/versions/0008_multi_tenant_sandbox.py`,
`clio/memory/store.py`, `clio/gsl/planner.py`, `clio/gsl/loop.py`, `clio/threads/manager.py`,
`clio/threads/context.py`, `clio/engine/workspace.py`, `clio/engine/drives.py`,
`clio/social/*`, `clio/gateway/router.py`, `clio/toolhost/tools/toolshed.py`.
Tests: `tests/unit/test_clio_3_2.py`, `tests/integration/`.

## 1. Recursive intention graphs (RIG)

Schema (`0006_clio_3_2.py:25-46`): `intention_edges` — parent/child FKs,
`edge_type ∈ {depends_on, verifies, synthesizes, informs}`, unique
`(parent, child, type)`. `intentions` gain `budget_allocated_usd, budget_spent_usd,
expected_value, lease_id, assigned_principal_id`.

Store API (`clio/memory/store.py`):

- `add_intention_edge()` (`store.py:1253+`) with **`CycleError`** rejection — a DAG, not a
  graph with cycles; the cycle guard runs at insert.
- `get_ready_intentions()` (`store.py:1307+`) — active/proposed intentions whose
  `depends_on` predecessors are all `done`: the ready queue is a property of the graph.
- `get_intention_subgraph(root)`.
- `dag_telemetry()` feeds the drives and the workspace (§4, §3).

### 1.1 Decomposition instead of bigger plans

`clio/gsl/planner.py`: `needs_decomposition` (`:263+`) → `decompose_into_children`
(`:275+`) → `decompose_and_persist` (`:392+`, persists edges and reopens the parent as a
container). The invariant: **leaf plans stay ≤ 7 steps** (`MAX_LEAF_STEPS`,
`planner.py:254-255`), while a genuinely complex goal becomes a tree of children with
`depends_on / verifies / synthesizes` edges. `GeneralSolverLoop._maybe_decompose`
(`loop.py:845,1981+`) hooks decomposition right after understanding — the loop itself never
exceeds its own plan-size ceiling.

### 1.2 Gates: verification as graph structure

`verifies` edges become **completion gates**: a parent with a `verifies` child cannot close
until that child is done (`gate_children` / `gate_blocks_completion` in
`clio/gsl/verification.py:25-75`; `_unlock_gate_children`, `loop.py:1940-1980`, which
creates the `verifies`+`synthesizes` children once output verifies). The gate child is
judged by an **independent diversity-family T3 model or peer resident** — preserving the
invariant that *a judge never overturns failing objective checks*
([06](06-work.md) §6). Verification stops being a phase and becomes **structure**: work
that must be checked is, by construction, followed by the checking of it.

## 2. EV-proportional budget allocation

- `allocate_ev_budgets` (`clio/memory/store.py:166-198`): intention budgets allocated by
  **value per dollar** across a subgraph, always H2-capped.
- Threads share their parent's carve by EV weight: `ev_weight = ev/cost`,
  `allocate_child_budget` (`clio/threads/manager.py:33-78`); parallel dispatch of ready
  nodes via `spawn_ready_branches` (`manager.py:427-490`).
- The gateway's `commitment_reserve_pct = 0.50` bucket split
  ([02](02-safety-and-guardian.md) §5) composes with per-intention budgets: money flows
  from the person, through the period cap, through the reserve, into the graph, into
  threads.

## 3. Two-tier workspace folding

`clio/engine/workspace.py`: `summarize_dag` + `render_commitments` (`:124-188`) — the
commitments block renders a **compressed DAG summary** (completion ratio, critical-path
blockers, budget burn, ready count) plus **full detail only for the focus**. A graph of 50
intentions fits in the prompt the way one list of 12 commitments did; attention economics
([03](03-engine.md) §6) extend from percepts to goal structure.

## 4. Drives over the graph

`clio/engine/drives.py` adds graph-derived gains: `dag_blocked_edges` raises **`order`** and
**`commitment`** on blocked critical paths — structural blockage becomes motivational
pressure; `leaf_verification_failures` raises **`mastery`**; `anomalous_outcomes` raises
**`curiosity`**. `Life._drive_inputs` (`clio/engine/life.py:522+`) assembles the telemetry.

## 5. Federated residents

The `people` table becomes a principal registry: `kind (human | resident | service)`,
`capabilities`, `authority_scope`, `endpoint_uri` (`0006_clio_3_2.py:48-58`). Store/match
API in `clio/social/relationships.py`: `register_resident` (`:85-101`),
`list_residents` (`:103-125`), `match_resident` (`:127-160`; filters by capability, trust
floor and the resident's commission budget ceiling, then picks the highest-trust match).

- **Bimodal etiquette** (`clio/social/etiquette.py:115-235`): humans keep quiet hours,
  digests, and the caps; **residents bypass** — a fast-path in `OutboundPipeline.send`
  delivers structured JSON over the event bus. Machine-to-machine speech is not rationed by
  human sleep ([05](05-social.md) §10).
- **Commissions between residents**: `COMMISSION_KINDS`
  (`clio/bus/event_bus.py:35-46`), `publish_commission` with a resident fast path
  (`:227-240`).
- **Epistemic checks on commissions** (`clio/social/deliberation.py:1420-1510`):
  flawed/under-budgeted specs → `ask_clarification`; outside skill/authority → `boundary`;
  legitimate disagreement → `deliberate_disagreement` — the same reply-path discipline as
  human commissions, and `_withhold_unearned_progress()` is enforced on **all**
  inter-resident updates ([05](05-social.md) §4, §6).

## 6. Scoped memory and namespaces

`episodes / facts / knowhow / questions` gain `scope (private | project | org)` and
`namespace_id` with indexes (`0006_clio_3_2.py:60-72`). Retrieval
(`clio/memory/retrieval.py`) unions **private** with **authorized** project/org namespaces;
**episodes stay private** always. Nightly promotion
(`clio/memory/consolidate.py:233-306`) promotes *verified* facts and *high-success*
know-how to project/org scope through T3 contradiction resolution, and updates `self_model`
skills/calibration from attempt statistics. A resident can serve an organization without
leaking its diary ([04](04-memory-and-self-model.md) §12).

## 7. Workspace leases

`WorkspaceLease` / `WorkspaceLeaseManager` (`clio/threads/context.py:48-300`), specified in
[06](06-work.md) §11. Summary of the invariants:

- A lease is a **git worktree** (for repos) or a **hardlink snapshot tree** (otherwise);
  `check_allowed_path` gates every path used under it.
- `commit_lease` is per-target-locked; `rollback_lease` restores via trash
  (`kind="lease_rollback"`) + the lease tree. **`SnapshotManager.restore()` is not wired
  into rollback** — a stated gap ([02](02-safety-and-guardian.md) §4.3).
- `GuardianGate.review()` enforces `allowed_paths` / `budget_limit_usd`
  (`clio/guardian/gate.py:249-349`), including the **home-reach rule**: a lease is the
  isolation guarantee *between projects*, not a fence around the person's own reach
  (`gate.py:212-247`).

## 8. Session drivers

`SessionDriver` (`clio/gateway/providers/base.py`): a stateful session/CLI driver
participates in `ModelRouter.select` with `session_score` carrying **`c_hat ≈ 0`**
(`clio/gateway/router.py:137-148`) — a subscription session has no marginal token cost, so
at equal quality it outranks paid APIs. On 429 or breaker failover, the gateway **replays
the normalized conversation** on the next driver
(`clio/gateway/service.py:574-630`) — failover preserves context
([07](07-gateway-and-toolhost.md) §1.2).

## 9. The signed toolshed

Toolshed manifests (`clio/toolhost/tools/toolshed.py`) gain first-class fences:
`allowed_paths, allowed_hosts, secret_scopes, timeout_s`, a SHA-256 `content_hash`, and an
**Ed25519 signature** (`verify_tool_signature`, `:22-38`). Registered via `tools.register`,
listed via `tools.list`, run via `tools.run` with hash/fence checks — and
`GuardianGate.review()` enforces the manifest's fences **before execution**
(`clio/guardian/classifier.py:708+`). Honest limits: the signature is **optional** (absent
= accepted, `:27-30`), and `secret_scopes` are stored but not enforced by the gate checker
([07](07-gateway-and-toolhost.md) §13).

## 10. The 3.1 containment wave (migration 0008)

`0008_multi_tenant_sandbox.py` added the substrate the 3.2 engines run inside:

- `tenants` and `spaces` — the `(tenant_id, space_id)` isolation pair every isolated table
  carries, backfilled to a default `local/primary` deployment (`:65-136`).
- RLS policies on the project, memory, event, artifact and audit tables — fail-closed
  scoping with an operator wildcard ([10](10-sandboxes-steering-and-policy.md) §8).
- `projects.space_id / sandbox_id` and `sandbox_registry` — every sandbox a
  `SandboxProvider` provisioned, with lease owner/fence for guarded commits (`:136-165`).
- `steering_messages` — mid-turn guidance, claim-based ([10](10-sandboxes-steering-and-policy.md) §4).
- `space_deks` — the wrapped per-space data encryption keys; deleting the row is the
  cryptographic shredding of the space (`:21-22`).

## 11. Theory: scale without org charts

The 3.2 direction is a bet about how a resident agent scales:

- **Structure, not hierarchy.** Coordination is expressed as graph edges
  (`depends_on / verifies / synthesizes`) rather than as managers, roles, or boards. The
  seven drives and one GSL remain the only engines; graphs make them see more.
- **Money follows value, structurally.** EV allocation + reserves + caps means budget is a
  property of the *work graph*, auditable end to end.
- **Peers, not users.** Other residents are principals with capabilities and authority
  scopes, addressed with the same etiquette and the same honesty guardrails — federation
  multiplies the agent without multiplying the rules.
- **Enforcement stays at the boundary.** Every new capability (leases, toolshed, sessions)
  is enforced at the gate or the router, never by convention inside the agent.

## 12. What is shipped, and what is a direction

Stated plainly, because this document covers a version boundary:

**Shipped and testable in this checkout** (see `tests/unit/test_clio_3_2.py` and the
integration suite):

- RIG edges with cycle rejection, ready-queue semantics, subgraph reads, telemetry
  (§1).
- Decomposition with leaf ceilings, `verifies` gates, unlock-after-verify (§1.1-1.2).
- EV budget allocation and EV-weighted child budgets, `spawn_ready_branches` (§2).
- DAG folding in the workspace commitments block (§3).
- Scoped memory columns, scoped retrieval, nightly promotion (§6).
- Workspace leases with fences, per-target commit, trash-backed rollback (§7).
- Session-driver plumbing in the router and gateway replay path (§8).
- Toolshed fences and hash pinning; signature verification when present (§9).
- Spaces/RLS/sandboxes/steering/policy substrate (§10; details in [10](10-sandboxes-steering-and-policy.md)).

**Present but thinner than the prose**:

- **Federation** has registry, matching, commission kinds and the bimodal pipeline, but no
  network transport between distinct deployments: `endpoint_uri` is stored, and residents
  talk over *this* deployment's event bus. Cross-machine federation is a direction, not a
  wire protocol in this checkout.
- **Session drivers** have the routing/replay machinery; the actual drivers are provider
  entries, and the subscription-economics story depends on an operator registering one.
- **The signed toolshed** signs what is present; nothing in this checkout *requires* a
  signature, and no key ceremony ships with it (§9).
- **Human-review gates** in the verification ladder exist for `type=human` criteria, but
  the "peer resident as judge" path depends on federation (§5), which is not cross-machine
  yet.
- **Project leadership** (migration `0007_project_leadership.py`) adds lead roles and the
  governance-message budget ([05](05-social.md) §8); the org-chart-shaped rest of the idea
  (escalation ladders, approvals between principals) is not implemented.
