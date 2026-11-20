# Safety theory: reversibility over refusal

Clio Agent runs on the user's machine, on their accounts, with their keys. The safety
model is therefore built around a specific thesis, stated in the README and enacted in
`clio/guardian/`:

> **Safe by reversibility where the code implements it, not by blanket refusal.**
> Concrete deletions go to trash, destructive calls are journaled, snapshots provide
> additional recovery, and a hash-chained audit trail provides evidence. Ordinary
> writes are not preimage-backed, however, and not every destructive operation can be undone.
> The six hard limits (H1–H6) are declared in the prompt and configuration; only
> specific parts of them have mechanical enforcement.

## 1. Why reversibility is the primitive

A purely refusal-based safety model has two structural problems for a resident agent:

1. **It cannot scale to a life.** A resident that runs 24/7 pursuing goals makes
   thousands of file and shell operations a day. A veto-based gate either floods the
   user with approvals (the agent becomes unusable) or the gate erodes (the agent
   becomes dangerous). Neither failure mode is acceptable under the golden rule.
2. **It mis-teaches the agent.** An agent whose operations get refused with no account
   of refusal learns one of three bad behaviours (`clio/prompts/identity.py:737-769`):
   report refused work as done, retry refused calls, or **rephrase the action until it
   slips past the classifier** — the one a hard limit exists to prevent.

Reversibility changes the default calculus. Guardian **field mode**
(`CLIO_GUARDIAN_MODE`, with legacy `ETHOS_GUARDIAN_MODE` fallback) keeps the journal,
trash, and snapshots, but changes the gate's vetoes into audit entries:
`GuardianConfig.mode` has a class default of `"field"`
(`clio/config.py:1195-1212`), while the checked-in deployment configuration sets
`guardian.mode: normal` (`config/permissions.yaml:78-95`). In field mode:

- a concrete file deletion is rewritten into a trash move;
- a protected or commissioned destructive action is moved to trash with long
  retention, without the model-based due-process check;
- a classifier hit, a capital-at-risk breach, and a workspace-lease fence are recorded
  and the action proceeds.

Field mode does **not** disable pause/preview, policy denial, safety-level approvals,
or pre-execution approval cards for high-impact external effects. It is also
independent of safety level 4: level 4 removes the safety-level approval wall, while
field mode changes what the guardian vetoes.

An ordinary overwrite is not protected by a preimage backup in the current code.
`fs.write` and `fs.edit` write their targets directly; reversibility currently
protects deletion and records side effects, not every byte written.

What remains veto-worthy is the set that reversibility cannot reliably repair:
**money** (spent is spent), **credentials** (scope is privilege), **law** (some acts
are not undoable), and **commissioned deliverables** (destroying another's requested
work is not the agent's loss to absorb). Those are the hard limits, but how strongly
each is enforced depends on the control described below.

## 2. The six hard limits (H1–H6)

The configuration fields are defined in `clio/config.py:1215-1247`; the prompt text
is rendered from `clio/prompts/identity.py:727-769`:

| # | Limit | Shipped value | Mechanical enforcement in this checkout |
|---|---|---|---|
| H1 | Oversight supremacy | `True` | Pause/preview reject tool calls; stop/pause/emergency files stop or hold the agent. Audit/snapshot immutability is a declaration, not a tool-level access restriction. |
| H2 | Financial exposure caps | daily $20, monthly $400, capital-at-risk $100, money threshold $5 in `config/permissions.yaml` | Gateway spend windows are enforced by `SpendGuard` (`clio/gateway/cost.py:298-470`); when a tool call supplies `spend_usd`, the gate applies the capital-at-risk cap to that call. The class defaults leave the daily and monthly windows disabled (`None`), so the YAML values matter. |
| H3 | Law and non-harm | `True` | Selected dangerous command patterns are blocked by the classifier in normal mode. The wider obligation has no single switch. |
| H4 | Legal authority | `True` | No dedicated mechanical gate enforces contracts or AI-disclosure duties in this checkout. |
| H5 | Commissioned-artifact due process | `True` | Normal mode consults `ModelDueProcessVerifier`; field mode skips the model check but still moves the artifact to trash. |
| H6 | Credential scope | `True` | Selected credential-access and privilege patterns are blocked by the classifier in normal mode; secret-domain policy is enforced separately at runtime. |

`on_exceed: "block"` applies to the numeric H2 spend windows. It is not an
on/off setting for H1, H3, H4, H5, or H6. In fact, the `H1_oversight_supremacy`,
`H3_law_and_non_harm`, `H4_legal_authority`, `H5_commissioned_due_process`, and
`H6_credential_scope` configuration fields are not read by the runtime; changing one
to `false` does not change the corresponding behaviour.

### 2.1 The enforcement honesty clause

The repository README gives the most accurate account of what is and is not
enforced (`README.md:1243-1284`):

- **H1** has concrete control-plane effects: pause, preview, and emergency controls
  stop tool execution, and the supervisor can terminate a wedged core. The stronger
  claim that the audit log and snapshots are mechanically untouchable by every
  possible host-user action is not enforced by the tool gate.
- **H2** is mechanically enforced at the gateway for configured spend windows. The
  guardian's capital-at-risk check is a per-action threshold, not an aggregate
  exposure calculation. In field mode the guardian records that breach rather than
  rejecting the call.
- **H3 and H6** have mechanical pattern checks for some dangerous commands in normal
  mode. Field mode records the classifier hit and allows the action.
- **H4** has no dedicated mechanical enforcement in this checkout.
- **H5** is mechanically represented by the artifact registry and due-process path.
  The standalone toolhost constructs `ModelDueProcessVerifier` without a gateway
  (`clio/__main__.py:322`), so there the model check is unavailable and fails closed:
  the artifact is protected rather than destroyed.
- The guardian operates on **tool calls and shell text**, not on arbitrary bytes the
  agent may produce in prose. The interface, audit trail, and oversight are the
  compensating controls for the prose channel.
- With no API keys configured, gateway model calls fail loudly, but the toolhost's
  journal and reflex rules can still run. The tool-level safety floor therefore does
  not depend on model availability; the model-based due-process check fails closed.

## 3. The guardian gate

`GuardianGate.review()` (`clio/guardian/gate.py:298-567`) sits between every tool
call and its execution. Verdicts are `{allow, transform, reject}`; gate paths are
`{normal, fast, due_process, blocked}` (`clio/guardian/gate.py:85-95`).

The current review order is:

1. **Oversight pause → REJECT / BLOCKED** (H1).
2. **Preview mode → REJECT / BLOCKED.** Preview is an owner-controlled mode, not a
   safety-level decision.
3. **Invalid, expired, or malformed `runAssertion` → REJECT / BLOCKED.**
4. **Safety level 1 → REJECT** for every tool except the explicitly allowed
   dialogue/internal-memory tools.
5. **CEL policy evaluation.** A policy denial rejects the call, and an engine error
   fails closed. A high-impact external side effect can create a pre-execution
   approval card at any safety level, including level 4.
6. **Classifier says `blocked` (H3/H6 pattern).** Normal mode rejects; field mode
   audits and allows.
7. **Money above capital-at-risk.** Normal mode rejects; field mode audits and
   allows. The gateway spend guard separately enforces the configured H2 windows.
8. **Workspace-lease and tool-manifest fences.** Normal mode rejects a path,
   manifest, or budget violation; field mode records the fence and proceeds.
9. **Safety-level approval.** Levels 2 and 3 can wait for the owner. An explicitly
   allowlisted `http.request` or `browser.visit` host is exempt; shell commands
   never qualify for that waiver.
10. **Destructive review.** A protected artifact takes the due-process path. A
    concrete agent-owned delete takes the fast trash path. Other irreversible
    operations are audited and allowed.
11. **Commissioned-write registration.** A successful commissioned write is added to
    the artifact registry, making later due process possible.

The classifier currently contains 81 command-table entries, 13 POSIX dangerous
patterns, 20 Windows dangerous patterns, and 44 tool-category mappings
(`clio/guardian/classifier.py:23-187,623-676`).

## 4. The reversibility stack

Four cooperating subsystems make "undo" a property of the system where one exists,
rather than a blanket promise.

### 4.1 Write-ahead journal (`clio/guardian/journal.py:67-289`)

Every registered tool call gets a `begin` row before the gate runs and an `end` row
afterwards. Journal statuses are `ok`, `failed`, `timeout`, `rejected`, and
`interrupted`; the first four are decided terminal outcomes, while `interrupted`
means the effect is unknown (`clio/guardian/journal.py:30-64`).

Idempotency has three distinct outcomes (`clio/guardian/journal.py:44-64,171-202`;
`clio/toolhost/server.py:241-409`):

- a prior successful attempt is replayed from its journal row instead of being
  executed again;
- a decided non-success (`failed`, `timeout`, or `rejected`) releases the key so a
  retry can proceed as a new attempt;
- a `running` or `interrupted` attempt is not replayed or released, because its
  effect may be unknown.

On restart, `ContinuityManager.wake_up()` reconciles still-`running` journal rows to
`interrupted` (`clio/memory/continuity.py:77-134`).

### 4.2 Trash (`clio/guardian/trash.py:57-214`)

`fs.delete` never calls `unlink`; it asks the trash store to move the target and
returns the restore reference (`clio/toolhost/tools/fs.py:403-424`). Shell deletes
with concrete existing targets are similarly rewritten into a trash move by the
guardian.

The layout is `trash/<id>/` plus `manifest.json`. Files receive a SHA-256
fingerprint. Retention is configured as 30 days for the fast path and 180 days for
the due-process path (`clio/config.py:1210-1211`). `TrashStore.purge_expired()` can
remove expired entries, but no runtime scheduler or CLI command currently calls it;
retention metadata does not itself purge anything.

### 4.3 Snapshots (`clio/guardian/snapshots.py:24-179`)

`SnapshotManager` uses btrfs subvolumes where its backend probe succeeds and falls
back to a hardlink/copy tree otherwise. The configured retention ladder is
`15m × 4`, `hourly × 24`, and `daily × 7`.

The current gate creates a snapshot on the fast-path destructive transform
(`clio/guardian/gate.py:786-830`); it does **not** create a pre-write snapshot for
ordinary writes. `SnapshotManager.restore()` exists, but no runtime caller wires it
to workspace-lease rollback. Lease rollback uses the lease's own root tree and trash
entries (`clio/threads/context.py:237-263`).

### 4.4 Hash-chained audit log (`clio/observability/audit.py:17-202`)

`GENESIS_HASH` is 32 zero bytes. Every entry hashes the previous entry, producing a
tamper-evident chain [D-33]. The Postgres sink serializes appends with advisory lock
`918_273_645`; the file sink fsyncs each JSONL append. `verify_chain()` validates up
to the most recent 1,000 entries (`clio/observability/audit.py:185-196`).

The core publishes the chain head and writes `anchor.json` every 600 seconds
(`clio/core/runner.py:431-445`); the host-side OOB guardian can ingest that anchor
into a log the agent cannot write. H1 declares the audit history off limits, but the
tool gate has no separate ACL that makes the database table immutable to every
possible host-user action.

## 5. Oversight paths in the runtime

H1 has three concrete runtime paths:

1. **Control plane** (`clio/core/control.py:32-423`): the `env_entities` lifecycle
   row is synchronized with `stop`, `paused`, and `emergency` files. The core
   re-reads the row no more often than every `ROW_RECHECK_S = 30` seconds, while
   file holds are read every cycle; SIGUSR1/SIGUSR2 map to pause/resume on POSIX. The design
   comment is blunt: "files + bus state are truth [H1]". The standalone toolhost
   loads the same row at startup and subscribes to `CONTROL_UPDATE` separately from
   the core's percept chain (`clio/__main__.py:276-303`), so a bus-delivered pause
   or preview reaches its gate.
2. **Supervisor** (`clio/core/supervisor.py:56-96,440-509`): child heartbeats have
   a 15-second fast window and a 1,800-second hard ceiling. A stale child is
   terminated and restarted on the next sweep. `clio stop` sends SIGTERM on POSIX
   and waits 20 seconds before reporting that the supervisor did not stop
   (`clio/__main__.py:1015-1056`). On Windows it writes a stop-request file instead;
   if that request is not honoured within 20 seconds, it forcibly stops the process
   tree (`clio/__main__.py:1059-1103`, `clio/platform.py:300-323`).
3. **Out-of-band watchdog** (`deploy/oob/oob_guardian.py:1-45`): an optional script
   intended to run on the host, outside the agent's machine and trust boundary. It
   watches spend and liveness, freezes sandboxes, revokes provider tokens, anchors
   the audit head, and implements a host-side stop switch. The script exists in the
   repository; running it is a deployment responsibility.

## 6. Approvals and safety levels

`clio safety show/set/reset` exposes four levels
(`clio/__main__.py:739-771`):

| Level | Meaning |
|---|---|
| 1 | Dialogue/internal-memory tools only; commands and subagents are refused |
| 2 | Commands normally require approval; allowlisted HTTP/browser hosts and calls inside non-APEX subagents are exceptions |
| 3 | Network-connected or download-like commands, plus APEX, normally require approval; allowlisted HTTP/browser hosts and calls inside non-APEX subagents are exceptions |
| 4 | No safety-level approval; policy approvals, oversight, policy denials, and guardian limits still apply |

Level 4 is **not recommended** for daily use. It removes the level-based approval
wall, but it is not full autonomy: a high-impact external side effect can still wait
for a pre-execution approval, and pause, preview, policy denial, and the guardian's
normal-mode checks remain effective. Level 3 is the recommended ceiling for
unattended running.

The effective level is selected in this order: live control-row override,
`CLIO_SAFETY_LEVEL` (legacy `ETHOS_SAFETY_LEVEL`), `permissions.safety_level`, then
the default `3` (`clio/guardian/safety.py:29-55`). Two operational details matter:

- `clio safety set` writes the control row directly and does not publish a
  `CONTROL_UPDATE` event (`clio/__main__.py:774-805`). The core notices such a row
  change on a later cycle, within the 30-second row-recheck window, but the
  standalone toolhost does not periodically re-read the row; it updates from its
  startup load or from a bus event. A surface that publishes `CONTROL_UPDATE` can
  therefore update the toolhost immediately, while `clio safety set` alone does not
  guarantee that every process changes level at the same time.
- `clio safety show` calls `effective_level(config, None)`, so it reports the
  environment/configured level and does not read the live override from the control
  row (`clio/__main__.py:739-750`).

Approvals are managed by `ApprovalManager`. Its code default is 300 seconds
(`clio/guardian/approval.py:43-80`), while the checked-in deployment config sets
`approval_timeout_s: 21600` (`config/permissions.yaml:16-19`). Pending approvals are
listed, approved, or denied through the toolhost HTTP surface with
`clio approvals list|approve|deny` (`clio/__main__.py:808-855`).

## 7. The gate as teacher

The refusal contract given to the agent (`clio/prompts/identity.py:737-769`) makes
the gate part of the agent's epistemology rather than an obstacle:

> A call that comes back refused was a decision, not a malfunction: read the reason,
> and do the nearest permitted thing instead — almost everything here is reversible
> through trash, snapshots or the action journal, so a refusal is usually a different
> route to the same work and sometimes the correct end of it.

The tool catalog preamble says the same at the call level
(`clio/prompts/identity.py:609-614`): an error that says a tool is unsafe, refused or
not permitted "is the answer, not a malfunction — take it as written and do the
permitted thing instead."

This is the practical resolution of the golden rule's two demands: the agent stays
capable (normal local work keeps flowing, and destructive work can be transformed to
reversible forms), and the human stays sovereign (pause/kill work, the policy engine
can deny, and the configured spend caps are numeric and external).

## Part II — The guardian: gate, classifier, and the reversibility stack

The guardian is the enforcement layer between intention and side effect. This part
describes its components; the theory is in Part I above. Sources:
`clio/guardian/`.

## 1. `GuardianGate.review()`

`clio/guardian/gate.py:298-567`. Verdicts are `{allow, transform, reject}`; paths
are `{normal, fast, due_process, blocked}` (`clio/guardian/gate.py:85-95`).

| Order | Condition | Path | Action |
|---|---|---|---|
| 1 | Oversight pause active | `blocked` | **REJECT** (H1) |
| 2 | Preview mode active | `blocked` | **REJECT** |
| 3 | Invalid/expired `runAssertion` | `blocked` | **REJECT** |
| 4 | Safety level 1, non-dialogue tool | `blocked` | **REJECT** |
| 5 | CEL policy denial or engine failure | `blocked` | **REJECT**; high-impact external effects may request a pre-execution approval |
| 6 | Classifier verdict `blocked` (H3/H6 pattern) | `blocked` in normal mode | **REJECT** in normal mode; audit and **ALLOW** in field mode |
| 7 | Money > `H2_capital_at_risk_usd` | `blocked` in normal mode | **REJECT** in normal mode; audit and **ALLOW** in field mode |
| 8 | Workspace lease or tool-manifest violation | `blocked` in normal mode | **REJECT** in normal mode; audit and **ALLOW** in field mode |
| 9 | Safety level requires approval | `blocked` | Wait for approval; allowlisted HTTP/browser hosts are exempt |
| 10 | Protected (commissioned/pinned) destructive action | `due_process` | Field mode: trash 180 d + `TRANSFORM` → `fs.read`; normal mode: `ModelDueProcessVerifier` (T2) |
| 11 | Agent-owned concrete delete | `fast` | Trash 30 d + snapshot → **allow (transformed)** |
| 12 | Commissioned write | `normal` | Register the artifact after success so later due process can find it |

`ModelDueProcessVerifier` (`clio/guardian/gate.py:42-82,655-784`) makes a T2 model
call that reviews a due-process destruction with the commission context. In field
mode the transform path skips that call. In the standalone toolhost the verifier is
constructed without a gateway, so the check fails closed and protects the artifact.

Safety-level approvals use `ApprovalManager` (code default 300 s), while policy
approval cards are a separate mechanism and remain active at level 4.

## 2. The action classifier

`clio/guardian/classifier.py` is the pattern-matching brain of the gate:

- **`COMMAND_TABLE`** (`:23-119`): 81 command spellings mapped to risk categories.
- **`DANGEROUS_PATTERNS`** (`:131-145`): 13 POSIX regex patterns, including
  `rm -rf /`, fork bombs, credential-file access, force pushes, and hard resets.
- **`WINDOWS_DANGEROUS_PATTERNS`** (`:164-187`): 20 case-insensitive Windows
  patterns.
- **`_TEXT_ONLY_COMMANDS`** (`:361-363`): `echo`, `printf`, `test`, `[`, `[[`,
  `true`, `false`, and `:` are treated as text-only for path extraction. `grep`,
  `cat`, and `wc` are not in this set.
- **`classify_shell`** (`:537-610`): parses with **bashlex** and classifies command
  segments, subshells, and redirections individually. If parsing fails, it falls
  back to a token scan rather than failing closed. `shell.run` accepts an arbitrary
  shell command string; the tool description recommends `&&` chaining, but the tool
  does not enforce that restriction.
- **`TOOL_CATEGORIES`** (`:623-676`): 44 tool mappings; `subagent.*` is
  **execute + write** because spawning an agent is acting.
- **`check_manifest_paths`**: checks a toolshed manifest's `allowed_paths` and
  `allowed_hosts`. The manifest's `secret_scopes` are stored by the toolshed, but
  this checker does not enforce them. In normal mode a path/host violation rejects
  the call; in field mode it is audited and the call proceeds.

## 3. The write-ahead journal

`clio/guardian/journal.py:67-289`:

- Entries are `begin` → (`end` with status `ok / failed / timeout / rejected /
  interrupted`).
- **Replayable only when `status == "ok"`** (`journal.py:44-64`). The replay returns
  the prior journal outcome, not the original tool payload.
- A decided non-success releases the idempotency key for a new attempt, while a
  `running`/`interrupted` row blocks a blind duplicate because its effect is unknown.
- On restart, `wake_up()` reconciles still-`running` entries to `interrupted`
  (`clio/memory/continuity.py:77-134`).

## 4. Trash

`clio/guardian/trash.py:57-214`:

- Layout: `trash/<id>/` + `manifest.json`; files also have a SHA-256 fingerprint.
- Retention: **30 days** (fast path) / **180 days** (due-process path), from the
  guardian configuration.
- `fs.delete` never calls `unlink` (`clio/toolhost/tools/fs.py:403-424`).
- `purge_expired()` exists, but the current runtime does not schedule or invoke it.

## 5. Snapshots

`clio/guardian/snapshots.py:24-179`:

- **btrfs subvolume** snapshots where the backend probe succeeds, else a
  **hardlink/copy tree**.
- Retention ladder: `15m × 4, hourly × 24, daily × 7` (`_prune`, `:112-128`).
- The guardian invokes snapshot creation on the fast destructive path. Ordinary
  writes do not receive a pre-write snapshot.
- `restore()` exists but is not wired into workspace-lease rollback; lease rollback
  uses the lease tree and trash directly.

## 6. The artifact registry

`clio/guardian/registry.py:32-161`:

- A successful write is registered as an **artifact** when it belongs to a
  commission (`path`, fingerprint when present, provenance, and `commissioned_by`).
  Uncommissioned writes are not automatically registered.
- `is_protected` computes protection by **path prefix or ancestor** — commissioned
  deliverables and pinned paths are protected before anyone tries to delete them,
  which is what makes H5's due-process path possible.
- `integrity.py:13-72` provides an `IntegrityScanner` that re-verifies registered
  artifact fingerprints, checks that trash entries still exist, counts snapshots,
  and verifies the recent audit chain. The current runtime does not instantiate or
  schedule `run_nightly()`, so this is available code rather than an active nightly
  control.

## 7. The audit chain

`clio/observability/audit.py:17-202`:

- **`GENESIS_HASH`** = 32 zero bytes (`:17`); each entry's hash covers the previous
  entry — a tamper-evident chain [D-33].
- The Postgres sink appends under **advisory lock `918_273_645`** (`:58-70`); the
  file sink fsyncs (`:121-130`).
- `verify_chain` (`:185-196`) validates up to the latest 1,000 entries; the core
  publishes and stores the chain head every 600 s (`clio/core/runner.py:431-445`).
- H1 declares the chain off limits to the agent, and the OOB guardian can keep a
  host-side copy of the head. There is no separate tool-level ACL that makes the
  chain immutable against every possible host-user action.

## 8. Safety levels in force

| Level | Gate behaviour |
|---|---|
| 1 | Dialogue/internal-memory tools only; commands and subagents are refused |
| 2 | Commands normally require approval; allowlisted HTTP/browser hosts and calls inside non-APEX subagents are exceptions |
| 3 | Network-connected or download-like commands, plus APEX, normally require approval; allowlisted HTTP/browser hosts and calls inside non-APEX subagents are exceptions |
| 4 | No safety-level approval; policy approvals, oversight, policy denials, and guardian limits still apply |

For calls tagged `inside_subagent`, levels 2 and 3 allow non-APEX subagent work
unless the call targets APEX; calls inside APEX require approval at both levels
(`clio/guardian/safety.py:233-277`). Level 4 is independent of
`guardian.mode`: it removes the level-based approval wall but does not switch the
guardian into field mode.

The core reads the control-row override on a later cycle, within the 30-second
recheck window. The standalone toolhost reads it at startup and from
`CONTROL_UPDATE` bus events; `clio safety set` writes the row without publishing
that event, so the CLI path does not guarantee an immediate toolhost change.

## 9. What the guardian does not cover

Stated honestly against the current code:

- The guardian sees **tool calls and shell text**. Prose the agent writes to a
  person is not a tool call; the compensating controls are the deliberation
  guardrail ([05-social.md](05-social.md) §4), the audit trail, and oversight.
- H1's pause/preview controls, H2's configured spend windows, selected H3/H6
  classifier patterns, and H5's registry/due-process path have mechanical effects.
  H4 has no dedicated mechanical enforcement, and field mode softens several
  guardian vetoes into audit entries.
- The H1/H3/H4/H5/H6 boolean configuration fields are declarations; the runtime does
  not read them as switches.
- With no model keys, gateway calls fail loudly but the toolhost's journal and
  reflex rules can still run. The model-based due-process check fails closed.
- Ordinary writes are not preimage-backed. Trash purge and the integrity scanner
  exist but are not scheduled, and snapshot restore is not wired into workspace-lease
  rollback. The reversible path is therefore strongest for trash-backed deletions,
  not universal for every side effect.
