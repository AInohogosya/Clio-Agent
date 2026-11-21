# 10 — Sandboxes, steering, policy, and security

This document covers the containment wave added in the 3.1 upgrade (migration
`0008_multi_tenant_sandbox.py`), in the order they sit in a call's life: where work runs,
what may reach it, who may steer it, what checks it, and what proves it happened. Sources
are named per section; tests live beside each subsystem (`tests/unit/test_sandbox.py`,
`test_steering.py`, `test_guardian_policy.py`, `test_verifier_inspect.py`,
`test_tools_catalog.py`, `test_envelope.py`, `test_api.py`, `tests/integration/` for the
database-backed halves).

The honest frame first: **the base agent has full user-level filesystem reach** — the
sandbox layer fences what a *project* provisioned a sandbox for, not the agent as a whole
([02](02-safety-and-guardian.md) §2). Everything in this document is the per-project
containment story on top of that fact.

## 1. Project sandboxes (`clio/sandbox/provider.py`)

Every project container gets one sandbox, and its `WorkspaceLease` root is mounted inside
at `/workspace`. `MemoryStore.create_project` provisions it through the pluggable
`SandboxProvider`, in capability order (`:1-30,87-330`):

1. **Rootless Docker/Podman** — `CapDrop: ["ALL"]`, `no-new-privileges`, the default
   seccomp profile, `PidsLimit: 512`, `--runtime runsc` when `CLIO_SANDBOX_RUNTIME` names
   gVisor (`RootlessDockerSandbox`, `:251-330`; the lease root is the only bind mount).
2. **bubblewrap** (Linux) / **Seatbelt** (macOS) — kernel confinement with no daemon
   (`BubblewrapSandbox :171-208` with `--unshare-all`; `SeatbeltSandbox :210-250`, whose
   profile is deny-by-default and **written beside the sandbox so an operator can read what
   was granted**).
3. **The filesystem baseline** — the lease root is the boundary, and the sandbox record
   says exactly that rather than implying confinement that is not there
   (`FilesystemSandbox`, `:165-168`).

`clio-supervisor` selects what the machine actually has; naming a backend the machine lacks
is refused loudly, not silently degraded. The `sandbox_registry` row carries
`lease_owner`/`lease_fence` and `SandboxManager.commit_guarded` refuses a stale owner's
commit — the fencing half is [08](08-runtime.md) §2's lease transfer.

Portable checkpoints (`AgentHomeStore`) archive the workspace, seal it under the space's
envelope key, and store it locally or push to S3 — the bytes at rest are ciphertext, and
shredding the space's DEK destroys every checkpoint with it (§8).

## 2. The egress proxy (`clio/sandbox/egress.py`)

A sandbox's network traffic has exactly one path out, and the guard validates every hop of
it (`:1-30,121-232`):

- **Per-project domain allowlists** (exact and suffix) — `host_allowed`, `:78-92`.
- **The metadata service is never a destination**: `169.254.169.254`, `fd00:ec2::254`,
  `100.100.100.200` (`METADATA_ADDRESSES`, `:43`); a name that resolves to it is a refusal,
  not a request.
- **Private ranges refused** — RFC1918, loopback, link-local, ULA (`_address_blocked`,
  `:101-118`).
- **Every resolved address is checked, not just the first** (`check`, `:138-165`) — "a host
  with one public and one private address must not pass because the resolver answered
  public first."
- **Redirect chains followed hop by hop under the same rules and cut at five**
  (`MAX_REDIRECT_HOPS`; `check_url_chain`, `:192-229`) — "an allowed host that redirects to
  the metadata service is still a refusal."
- **CONNECT tunnels refused before they open.**
- Every decision lands in `audit_log`: allowed, refused, and why (`audit_connection`,
  `:165-190`) — an operator can answer "what did this sandbox talk to" without reading code.

## 3. The virtual computer (`clio/sandbox/computer.py`)

Per sandbox: an `Xvfb` framebuffer (1280×800; the platform's own offscreen rendering where
Xvfb cannot exist, stated in the record — `VirtualDisplay`, `:83-140`) and a persistent
Chromium profile.

- **Ref-addressed actions**: browser actions target **server-issued references** from a
  fresh `ariaSnapshot` (`RefRegistry`, `:158-193`). The registry stamps elements inside the
  page, resolves references server-side at act time, and refuses stale ones with `409`
  semantics (`StaleRefError`, `:51-58`) — generation-bounded (`MAX_REF_GENERATIONS`, `:45`)
  and invalidated by navigation, so prompt-injected content cannot make a click land on an
  element other than the one the agent observed.
- **Zero-exposure credentials**: `{{secret:KEY}}` placeholders are substituted at fill time
  inside the sandbox; the raw value never appears in a result, a snapshot or a log. Fields
  whose names match `_SENSITIVE_KEY` (`:48`) are masked in logs and recordings.
- **Live screen takeover** streams the display to a person and hands control over behind an
  **Action Barrier** (`TakeoverController`, `:378-460`): in-flight agent input events drain
  (bounded) before the human holds the sandbox, agent input while a person holds it is
  refused by name, and hand-back returns control cleanly. `POST
  /api/sandboxes/{id}/control/take` and `.../hand-back` live in `clio-api`
  ([08](08-runtime.md) §13); the controller itself lives with the virtual computers in the
  core, and a stateless replica reaches it over the out-of-band control lane.

## 4. Mid-turn steering (`clio/gsl/steering.py`)

A message bound to the active project's conversation (`conversation_project_bindings`),
arriving while that project's run is under way, is drained into `steering_messages` by the
life loop (`clio/engine/life.py:1494-1544`) and published as `steering.inject` on an
out-of-band lane. `GeneralSolverLoop.prepare_next_step` (`clio/gsl/loop.py:1250-1310`) —
before every tool dispatch and step transition — claims pending rows atomically
(`FOR UPDATE SKIP LOCKED`, `steering.py:86-123`), renders them into the step context at
high priority (`render_for_step`, `:196-216`), merges any `constraint:` lines into the
intention, and marks the rows applied.

The queue is **claim-based, never fire-and-forget** (`:19-22`): a claimed row is marked
`claimed` with the step that took it, and only `mark_applied` after the step's context
actually carries it. A run that dies between claim and apply leaves the row claimed by a
dead step, and `expire_stale` (`:134-143`) hands those back — "a finalized run's guidance is
never lost and never replayed twice."

The **fast-lane micro-slot** (`CLIO_FAST_LANE_BUDGET_S = 90`,
`clio/config.py` `projects.fast_lane_budget_s`; `clio/engine/life.py:1546-1621`): at a
clean boundary during a locked focus quantum, a quick message in a secondary conversation is
answered read-only (tier capped at T2, dialogue tools only), without changing
`active_project_id` or touching the primary project's sandbox. A message that asks for real
work is left for the full social turn — deliberately, with its commissioning machinery.

## 5. Two-stage tool narrowing (`clio/tools/catalog.py`)

The cached prefix's `[2. tools]` block pins one meta-tool, `tools.search` (byte-stable, so
prompt caching survives), and the registry's size (`clio/prompts/identity.py:701-724`;
`clio/engine/workspace.py:253-269`). Each step's context carries a sheet of up to twelve
fully-rendered tools chosen by a lightweight router (`route`, no model call) against the
step's own text, plus the plan's `tool_hints` (`clio/gsl/loop.py:1900-1930`); anything else
is one `tools.search` call away with its complete schema. MCP tools, OAuth connector tools,
OpenAPI/GraphQL-imported tools and toolshed tools all enter the one catalog
(`Toolhost.catalog`, `clio/toolhost/server.py:121-148`).

Full treatment including the honest limits (lexical routing, full sheet for thread workers)
is [07](07-gateway-and-toolhost.md) §10.

## 6. Layer-1 policy and assertions (`clio/guardian/policy.py`)

Before every tool call the gate evaluates CEL policies from `cel_policies`
(`clio/guardian/gate.py`, policy section):

- **Deny strictly before allow; fail closed** (`policy.py:1-20`): an expression that will
  not parse, names a missing attribute or raises **denies** (`PolicyError` → deny,
  `:51-55,385-410`); a deny rule that cannot be evaluated denies and names itself; an allow
  rule that cannot be evaluated never allows (`evaluate_or_none`, `:390-410`).
- Rules **reload live over LISTEN/NOTIFY** (`PolicyEngine`, `:459-510`) — an operator
  tightening a rule does not restart the agent.
- **Signed `runAssertion` tokens** bind project, space, initiator kind and delegation
  depth; invalid, expired or wrongly-signed tokens are refused before anything else about
  the call is trusted (`clio/guardian/gate.py:121-146`), and **depth can only shrink as
  work is handed down**.
- **High-impact external side effects** (production deploys, host-boundary mounts, messages
  over unapproved channels, external writes to production endpoints) route to
  **Pre-Execution Approval Cards** unless a policy rule pre-authorized the call
  (`call_environment` / `high_impact_external_side_effect`, `:443-457`). This approval path
  stays active at safety level 4 and in field mode ([02](02-safety-and-guardian.md) §1.1, §7).

The gate's evaluation order, including where policy sits relative to the classifier, the
spend cap and the lease fence, is the table in [02](02-safety-and-guardian.md) §3.

## 7. Full-artifact verification (`clio/gsl/verifier_inspect.py`)

Rung 2 chunks every modified artifact into **whole-covering slices** (`chunk_file`,
`:133-180` — every character of every modified file lands in exactly one chunk; a single
line longer than the target chunk is its own chunk), judges each with the
diversity-family verifier, and reduces under a fixed rule: **any `fail` fails the
deliverable, any `needs_human` demands a human, `pass` only when every chunk passed** —
every issue carrying its chunk's provenance. No `artifact[:16000]` window remains in the
loop's own verification path. The rung-1 invariant is unchanged: **a model judge can never
overturn a failing objective check** (`clio/gsl/verification.py:54-75`). The thread-based
verifier still reads a 6 000-char artifact head — both paths exist
([06](06-work.md) §15).

## 8. Envelope encryption and RLS (`clio/security/envelope.py`, `clio/db/row_security.py`)

- **Envelope encryption** (`envelope.py:1-140`): per-space DEKs (AES-256-GCM, `:37`)
  wrapped by an **age identity** (`AgeMasterKey`, `:56-90`) or `CLIO_MASTER_KEY`
  (`EnvMasterKey`, `:92-140`, context-split wrapping keys). Episode bodies, fact
  provenance, journal arguments and continuity checkpoints seal into `sealed_payload`
  columns with a `{"sealed": true}` stub (`SEALED_STUB`, `:35`).
  **`shred_space` deletes the space's wrapped DEK and every payload becomes permanently
  unreadable, backups included** (`ShreddedSpaceError`, `:44-45`) — deleting a space or
  destroying its sandbox shreds it this way rather than hoping a filesystem delete is
  enough.
- **RLS** (`row_security.py:1-120`): migration 0008 adds `(tenant_id, space_id)` plus
  `USING`/`WITH CHECK` policies across the project, memory, event, artifact and audit
  tables (`RLS_TABLES`, `:90-96`; policy SQL in the migration `:121-136`). An unscoped
  session **sees nothing** (fail closed); `WITH CHECK` stops cross-space writes; an
  operator wildcard (`*`, `:28-31`) exists for supervision and is set only by the
  supervisor and migration tooling (`operator_scope`, `:59-70`).
- **The stated limit, in the module's own words** (`:13-17`): RLS applies per role, and a
  table owner bypasses its RLS unless the table says `FORCE ROW LEVEL SECURITY` — the
  agent's database role is the personal deployment's owner role, so **the personal
  deployment is not RLS-protected against itself**; `force_rls` (`:98-106`) exists and the
  migration applies it, but the honest threat model is cross-*space* isolation on shared
  deployments, not protection from the operator.

## 9. Liveness and fencing (`clio/core/supervisor.py`, `clio/core/leases.py`)

The supervisor watches two tiers per child: the fast window
(`CLIO_FAST_HEARTBEAT_S = 15`) — kept fresh by a background heartbeat task so staleness
means a stopped process, not a slow model call — and the 1800-second hard ceiling
(`supervisor.py:56-95`). A wedged child is recycled and **its activity leases are
transferred with the fence bumped** (`_transfer_fenced_leases`, `:334-358`); every guarded
state commit verifies `lease_owner` + `lease_fence`, so **a zombie's late write is refused
at the row**. The row itself carries the fence (`sandbox_registry`, migration 0008), which
is what makes the check atomic rather than conventional.

## 10. External tools (`clio/toolhost/mcp.py`, `connectors.py`, `importers.py`)

- **MCP servers** connect over stdio, Streamable HTTP or SSE, pooled and health-checked
  (`mcp.py:1-20,99-250`); a down server stays visible as a **degraded stand-in descriptor**
  (`degraded_descriptor`, `:79-96`) rather than vanishing from the catalog mid-run.
- **OAuth connectors** (`connectors.py:1-90`): the callback exchanges the code for tokens
  and **seals them at rest with the space's envelope key** (`connector_accounts.sealed_tokens`);
  access and refresh tokens are decrypted **inside the toolhost's execution**, so raw
  tokens never enter model context; refresh runs under a per-connector lock (rotating and
  re-sealing when the provider rotates); per-project scopes are recorded on the account.
- **Importers** (`importers.py`): OpenAPI 3.x (recursive, cycle-bounded `$ref` resolution —
  "a `$ref` chain is too deep (a cycle?)", `:49-70`) and GraphQL (SDL or introspection)
  import into typed tools with their own CEL policy slots.

## 11. The control plane (`clio/api/app.py`)

`clio-api` is stateless: lifecycle verbs on the out-of-band lane, read models from
PostgreSQL under RLS, ClioUI components and their action endpoint, takeover endpoints, and
the OAuth callback (`clio/api/app.py:1-30,130-278`). Any number of replicas can serve it;
the facade delegates the two genuinely in-process operations (takeover, connector exchange)
over the bus or to its own database-backed broker (`_bus_round_trip`, `:58-84`).
A compromised API replica can post a control verb and read RLS-scoped state — it holds no
keys and reaches no provider (`:22`).

## 12. What this document does not claim

1. **Sandboxes are per-project opt-in.** A project provisioned without a sandbox backend
   gets the filesystem baseline — the lease root is the boundary, and the record says so
   (§1). The base agent's reach is unchanged ([02](02-safety-and-guardian.md) §2).
2. **Egress enforcement is at the guard, not the kernel.** `EgressGuard` checks names and
   addresses on the path it is given; a sandbox backend without network isolation (the
   filesystem baseline) does not route through it. `deny_private_ranges: false` in this
   deployment is a deliberate field setting, and it applies to `http.request` too
   ([07](07-gateway-and-toolhost.md) §11).
3. **Ref-addressed browsing is generation-bounded, not oracle-proof**: a stale ref is
   refused with 409 and a re-observe is required; a page that re-renders between snapshot
   and act can still change *within* the same generation, which the design answers with
   freshness windows, not proofs.
4. **RLS protects spaces from each other, not operators from themselves** (§8).
5. **The envelope shredding is only as durable as the DEK row**: anything that preserved a
   copy of the wrapped DEK (a backup taken before shredding) preserves the ciphertext's
   future — the design's answer is that backups of *sealed payloads* are unreadable without
   the DEK, which is exactly what shredding deletes.
6. **Policy files are operator-authored.** The engine fails closed and reloads live, but
   this checkout ships no rule set beyond the defaults in `config/`; a deployment's posture
   is its `cel_policies` rows.
7. **The takeover Action Barrier drains in-flight events "bounded"**: a tool mid-action that
   ignores the drain window still races the hand-back; the refusal of *subsequent* agent
   input is the enforcement, not the drain.
