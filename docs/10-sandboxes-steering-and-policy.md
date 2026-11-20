# Containment, steering and policy: what Clio Agent 3.1 adds

This document covers the subsystems added in the 3.1 upgrade, in the order
they sit in a call's life: where work runs, what may reach it, who may steer
it, what checks it, and what proves it happened. Sources are named per
section; tests live beside each subsystem (`tests/unit/test_sandbox.py`,
`test_steering.py`, `test_guardian_policy.py`, `test_verifier_inspect.py`,
`test_tools_catalog.py`, `test_envelope.py`, `test_api.py`,
`tests/integration/` for the database-backed halves).

## 1. Project sandboxes (`clio/sandbox/provider.py`)

Every project container gets one sandbox, and its `WorkspaceLease` root is
mounted inside at `/workspace`. `MemoryStore.create_project` provisions it
through the pluggable `SandboxProvider`, in capability order:

1. **Rootless Docker/Podman** — `CapDrop: ["ALL"]`, `no-new-privileges`, the
   default seccomp profile, `PidsLimit: 512`, `--runtime runsc` when
   `CLIO_SANDBOX_RUNTIME` names gVisor.
2. **bubblewrap** (Linux) / **Seatbelt** (macOS) — kernel confinement with no
   daemon; the Seatbelt profile is deny-by-default and written beside the
   sandbox so an operator can read what was granted.
3. **The filesystem baseline** — the lease root is the boundary, and the
   sandbox record says exactly that rather than implying confinement that is
   not there.

`clio-supervisor` selects what the machine actually has; naming a backend the
machine lacks is refused loudly, not silently degraded. The
`sandbox_registry` row carries `lease_owner`/`lease_fence` and
`SandboxManager.commit_guarded` refuses a stale owner's commit.

Portable checkpoints (`AgentHomeStore`) archive the workspace, seal it under
the space's envelope key, and store it locally or push it to S3 — the bytes
at rest are ciphertext, and shredding the space's DEK destroys every
checkpoint with it.

## 2. The egress proxy (`clio/sandbox/egress.py`)

A sandbox's network traffic has exactly one path out, and the proxy validates
every hop of it: per-project domain allowlists (exact and suffix), the
metadata service blocked by address (`169.254.169.254` and its v6 twins),
RFC1918/loopback/link-local/ULA refused, every resolved address of a host
checked (not just the first), redirect chains followed hop by hop under the
same rules and cut at five, CONNECT tunnels refused before they open. Every
decision lands in `audit_log`.

## 3. The virtual computer (`clio/sandbox/computer.py`)

Per sandbox: an `Xvfb` framebuffer (1280x800; the platform's offscreen
compositor where Xvfb cannot exist, stated in the record) and a persistent
Chromium profile. Browser actions target **server-issued references** from a
fresh `ariaSnapshot`: the registry stamps elements inside the page, resolves
references server-side at act time, and refuses stale ones with `409`
semantics — generation-bounded and invalidated by navigation, so
prompt-injected content cannot make a click land on an element other than the
one the agent observed. `{{secret:KEY}}` placeholders are substituted at fill
time inside the sandbox; the raw value never appears in a result, a snapshot
or a log.

Live screen takeover streams the display to a person and hands control over
behind an **Action Barrier**: in-flight agent input events drain (bounded)
before the human holds the sandbox, password fields are masked in logs and
recordings, and agent input while a person holds it is refused by name.
`POST /api/sandboxes/{id}/control/take` and `.../hand-back` live in
`clio-api`; the controller itself lives with the virtual computers in the
core, and a stateless replica reaches it over the out-of-band control lane.

## 4. Mid-turn steering (`clio/gsl/steering.py`)

A message bound to the active project's conversation, arriving while that
project's run is under way, is drained into `steering_messages` and published
as `steering.inject` on an out-of-band lane. `GeneralSolverLoop.
prepare_next_step` — before every tool dispatch and step transition — claims
pending rows atomically (`FOR UPDATE SKIP LOCKED`), renders them into the
step context at high priority, merges any `constraint:` lines into the
intention, and marks the rows applied. Stale claims from dead runs are handed
back; a finalized run's guidance is never lost and never replayed twice.

The same cycle order gained the **fast-lane micro-slot**
(`CLIO_FAST_LANE_BUDGET_S = 90`): at a clean boundary during a locked focus
quantum, a quick message in a secondary conversation is answered read-only
(tier capped at T2, dialogue tools only), without changing
`active_project_id` or touching the primary project's sandbox. A message
that asks for real work is left for the full social turn.

## 5. Two-stage tool narrowing (`clio/tools/catalog.py`)

The cached prefix's `[2. tools]` block pins one meta-tool, `tools.search`
(byte-stable, so prompt caching survives), and the registry's size. Each
step's context carries a sheet of up to twelve fully-rendered tools chosen by
a lightweight router (no model call) against the step's own text, and
anything else is one `tools.search` call away with its complete schema. MCP
tools, OAuth connector tools, OpenAPI/GraphQL-imported tools and toolshed
tools all enter the one catalog (`Toolhost.catalog`).

## 6. Layer-1 policy and assertions (`clio/guardian/policy.py`)

Before every tool call the gate evaluates CEL policies from `cel_policies`:
deny rules strictly before allow, deny-before-allow, fail-closed — an
expression that will not parse, names a missing attribute or raises denies; a
deny rule that cannot be evaluated denies and names itself; an allow rule
that cannot be evaluated never allows. Rules reload live over
LISTEN/NOTIFY. Signed `runAssertion` tokens bind project, space,
initiator kind and delegation depth; invalid, expired or wrongly-signed
tokens are refused before anything else about the call is trusted, and depth
can only shrink as work is handed down. High-impact external side effects
(production deploys, host-boundary mounts, messages over unapproved channels,
external writes to production endpoints) route to Pre-Execution Approval
Cards unless a policy rule pre-authorized the call.

## 7. Full-artifact verification (`clio/gsl/verifier_inspect.py`)

Rung 2 chunks every modified artifact into whole-covering slices, judges each
with the diversity-family verifier, and reduces under a fixed rule: any
`fail` fails the deliverable, any `needs_human` demands a human, `pass` only
when every chunk passed, every issue carrying its chunk's provenance. No
`artifact[:16000]` window remains. The rung-1 invariant is unchanged: a model
judge can never overturn a failing objective check.

## 8. Envelope encryption and RLS (`clio/security/envelope.py`, `clio/db/row_security.py`)

Per-space DEKs (AES-256-GCM) wrapped by an age identity or
`CLIO_MASTER_KEY`; episode bodies, fact provenance, journal arguments and
continuity checkpoints seal into `sealed_payload` columns with a
`{"sealed": true}` stub. Shredding a space deletes its wrapped DEK and every
payload becomes permanently unreadable, backups included. Migration 0008 adds
`(tenant_id, space_id)` plus RLS policies across the project, memory, event,
artifact and audit tables: an unscoped session sees nothing (fail closed),
`WITH CHECK` stops cross-space writes, and an operator wildcard exists for
supervision.

## 9. Liveness and fencing (`clio/core/supervisor.py`, `clio/core/leases.py`)

The supervisor watches two tiers per child: the fast window
(`CLIO_FAST_HEARTBEAT_S = 15`) — kept fresh by a background heartbeat task so
staleness means a stopped process, not a slow model call — and the
1800-second hard ceiling. A wedged child is recycled and its activity leases
transferred with the fence bumped; every guarded state commit verifies
`lease_owner`+`lease_fence`, so a zombie's late write is refused at the row.

## 10. External tools (`clio/toolhost/mcp.py`, `connectors.py`, `importers.py`)

MCP servers connect over stdio, Streamable HTTP or SSE, pooled and
health-checked; a down server stays visible as a degraded stand-in
descriptor. OAuth connectors hold tokens sealed at rest, refresh under a
per-connector lock, enforce per-project scopes, and execute inside the
toolhost so raw tokens never enter model context. OpenAPI 3.x (recursive,
cycle-bounded `$ref` resolution) and GraphQL (SDL or introspection) import
into typed tools with their own CEL policy slots.

## 11. The control plane (`clio/api/app.py`)

`clio-api` is stateless: lifecycle verbs on the out-of-band lane, read models
from PostgreSQL under RLS, ClioUI components and their action endpoint,
takeover endpoints, and the OAuth callback. Any number of replicas can serve
it; the facade delegates the two genuinely in-process operations (takeover,
connector exchange) over the bus or to its own database-backed broker.
