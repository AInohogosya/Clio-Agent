# Safety theory: reversibility over refusal

Clio Agent runs on the user's machine, on their accounts, with their keys. The safety
model is therefore built around a specific thesis, stated in the README and enacted in
`clio/guardian/`:

> **Safe by reversibility, not by refusal.** Deletions go to trash, writes are backed
> up first, every action is journaled, and six hard limits (H1–H6) are enforced outside
> the mind. Almost anything it does, you can undo from the interface.

## 1. Why reversibility is the primitive

A purely refusal-based safety model has two structural problems for a resident agent:

1. **It cannot scale to a life.** A resident that runs 24/7 pursuing goals makes
   thousands of file and shell operations a day. A veto-based gate either floods the
   user with approvals (the agent becomes unusable) or the gate erodes (the agent
   becomes dangerous). Neither failure mode is acceptable under the golden rule.
2. **It mis-teaches the agent.** An agent whose operations get refused with no account
   of refusal learns one of three bad behaviours (`clio/prompts/identity.py:672-679`):
   report refused work as done, retry refused calls, or **rephrase the action until it
   slips past the classifier** — the one a hard limit exists to prevent.

Reversibility changes the default calculus. In the default **field mode**
(`config.py`, `GuardianConfig.mode = "field"`, env `ETHOS_GUARDIAN_MODE`), most
destructive actions are not vetoed but **transformed**: the delete becomes a trash
move, the overwrite becomes a preimage backup, and both are journaled before the call
runs. The cost of being wrong is "a page in a log rather than lost work"
(`clio/prompts/identity.py:313-321`).

What remains veto-worthy is exactly the set that reversibility cannot repair:
**money** (spent is spent), **credentials** (scope is privilege), **law** (some acts
are not undoable), and **commissioned deliverables** (destroying another's requested
work is not the agent's loss to absorb). Those are the hard limits.

## 2. The six hard limits (H1–H6)

Defined in `config.py:1168-1185` and rendered verbatim into the identity kernel from
`clio/prompts/identity.py:653-660`:

| # | Limit | Default | Meaning |
|---|---|---|---|
| H1 | Oversight supremacy | `True` | Pause and kill are always effective; the audit log and snapshots are untouchable by the agent. |
| H2 | Financial exposure caps | daily $20, monthly $400, capital-at-risk $100, money threshold $5 | Spend caps; no borrowing or leverage. Enforced by `SpendGuard` (`clio/gateway/cost.py:213-351`). |
| H3 | Law and non-harm | `True` | No illegal acts, unauthorized access, fraud, or physical-harm risks. |
| H4 | Legal authority | `True` | No contracts in the owner's name without explicit mandate; disclose AI status when required. |
| H5 | Commissioned-artifact due process | `True` | Never destroy requested deliverables without documented due process. |
| H6 | Credential scope | `True` | Only legitimately granted credentials; no privilege escalation. |

All are configured with `on_exceed="block"`.

### 2.1 The enforcement honesty clause

The architecture doc is explicit about **what is and is not enforced**
(`AI_AGENT_ARCHITECTURE.md` §9, `README.md:125-127`):

- **H2 and H5** are the limits the gate can mechanically stop a call on: numeric caps
  and the due-process path for protected artifacts.
- **H1, H3, H4, H6** are *declarations of the same obligations*, enforced structurally
  by the classifier, the audit chain and reversibility rather than by a single switch.
  The identity kernel says this out loud to the agent
  (`clio/prompts/identity.py:687-691`), so the agent's account of its limits matches
  the code's account.
- The guardian operates on **tool calls and shell text**, not on arbitrary bytes the
  agent may produce in prose. The interface, audit log, and journals are the
  compensating controls for the prose channel.
- With no API keys configured, every model call fails loudly at the gateway, but the
  reflex rules and journaling still run: the safety floor does not depend on model
  availability.

## 3. The guardian gate

`GuardianGate.review()` (`clio/guardian/gate.py:122-517`) sits between every tool call
and its execution. Verdicts: `{allow, transform, reject}`; gate paths:
`{normal, fast, due_process, blocked}` (`gate.py:85-95`).

Review order (abridged from `gate.py:266`):

1. **Oversight pause or preview active → REJECT / BLOCKED** (H1). The control plane's
   pause/preview always wins.
2. **Classifier says `blocked` (H3/H6 pattern) → REJECT.** ~60 POSIX + ~30 Windows
   command entries, 14 POSIX + 22 Windows dangerous patterns, plus 42 tool-category
   mappings (`clio/guardian/classifier.py:23-187,623-676`).
3. **Money above capital-at-risk → REJECT** (H2), checked by the gateway spend guard
   on model spend and the gate on tool-call spend.
4. **Protected (commissioned or pinned) destructive action → due-process path.**
   Field mode: trash with 180-day retention + `TRANSFORM` → `fs.read` (i.e. the
   destructive call is rewritten into a read). Normal mode: a T2
   `ModelDueProcessVerifier` verdict (`gate.py:42-82,356-418`).
5. **Agent-owned delete → fast path**: trash with 30-day retention + a snapshot
   (`gate.py:441-485`).
6. **Writes auto-register artifacts** in the registry (`gate.py:263-271`), which is
   what makes later due process possible at all.

## 4. The reversibility stack

Four cooperating subsystems make "undo" a property of the system rather than a promise:

### 4.1 Write-ahead journal (`clio/guardian/journal.py:67-256`)

Every tool call is journaled: `begin` before execution, `end` after, with statuses
`ok/failed/timeout/rejected/interrupted`. Only `status == "ok"` entries are replayable
(`journal.py:44`), and idempotency keys give duplicate calls a **replay** of the prior
result rather than a re-execution (`clio/toolhost/server.py:322-366`) — the same key
never runs the side effect twice.

### 4.2 Trash (`clio/guardian/trash.py:57-214)

`fs.delete` never calls `unlink` (`clio/toolhost/tools/fs.py:403`); it moves the
target to `trash/<id>/` with a `manifest.json` and a sha256. Retention: 30 days for
the fast path, 180 days for due-process artifacts. A purge job enforces retention.

### 4.3 Snapshots (`clio/guardian/snapshots.py:24-153)

Pre-write snapshots via btrfs subvolumes where available, falling back to hardlink
trees. Retention ladder: 4× 15-minute, 24× hourly, 7× daily. Snapshots are also the
restore mechanism for rolled-back workspace leases (`snapshots.py` `restore()`).

### 4.4 Hash-chained audit log (`clio/observability/audit.py:202`)

`GENESIS_HASH` = 32 zero bytes (`audit.py:17`); every entry hashes the previous entry,
producing a tamper-evident chain [D-33]. A Postgres advisory lock (key `918_273_645`)
serializes appends; a `verify_chain` routine validates the whole chain
(`audit.py:185`). The core anchors the chain every 600 s
(`clio/core/runner.py:279`). The agent cannot rewrite it, and H1 says it may not try.

## 5. Oversight supremacy (H1) in the runtime

H1 is enforced at three layers, not as a prompt line:

1. **Control plane** (`clio/core/control.py`): a two-way sync between the
   `env_entities` control row and on-disk state files, with `ROW_RECHECK_S = 30`
   rechecks and SIGUSR1/SIGUSR2 signals. The design comment is blunt: "files + bus
   state are truth [H1]". The toolhost subscribes to `CONTROL_UPDATE` separately from
   the main percept chain (`clio/core/runner.py:226`) so that "oversight that reads
   as obeyed and is not" cannot happen [H1].
2. **Supervisor**: the core's heartbeat (`run_dir/heartbeat`) must be fresh; a wedged
   core (stale > `ETHOS_CORE_HEARTBEAT_STALE_S`, default 1800 s) is terminated and
   restarted (`clio/core/supervisor.py:53,67`). `clio stop` escalates from SIGTERM
   to hard kill after 20 s — on Windows, where `os.kill` is itself an unhandleable
   `TerminateProcess`, stop is requested **by file** instead
   (`clio/core/supervisor.py` `request_stop_by_file`, `clio/platform.py:318`).
3. **Out-of-band watchdog** (`deploy/oob/oob_guardian.py`): a process deliberately
   outside the agent's process tree and reach, overseeing the overseers.

## 6. Approvals and safety levels

`clio safety show/set/reset` exposes four levels (`clio/__main__.py:633-665`):

| Level | Meaning |
|---|---|
| 1 | Dialogue only — no tools |
| 2 | All tools on approval |
| 3 | Internet-reachable tools on approval |
| 4 | No approval (field mode, full autonomy) |

The live override is written into the control row (`_set_control_safety_level`,
`__main__.py:668-699`) so every process reads the same value within one recheck
window. Approvals themselves are managed by `ApprovalManager` with a default timeout
of 300 s (`clio/guardian/approval.py`), listed/granted/denied via
`clio approvals list|approve|deny` over the toolhost's HTTP surface.

## 7. The gate as teacher

The refusal contract given to the agent (`clio/prompts/identity.py:683-699`) makes
the gate part of the agent's epistemology rather than an obstacle:

> A call that comes back refused was a decision, not a malfunction: read the reason,
> and do the nearest permitted thing instead — almost everything here is reversible
> through trash, snapshots or the action journal, so a refusal is usually a different
> route to the same work and sometimes the correct end of it.

The tool catalog preamble says the same at the call level
(`clio/prompts/identity.py:552-557`): an error that says a tool is unsafe, refused or
not permitted "is the answer, not a malfunction — take it as written and do the
permitted thing instead."

This is the practical resolution of the golden rule's two demands: the agent stays
capable (field mode keeps most work flowing, transformed to reversible forms), and the
human stays sovereign (pause/kill always work, the audit chain is untouchable, the
caps are numeric and external).
## Part II — The guardian: gate, classifier, and the reversibility stack

The guardian is the enforcement layer between intention and side effect. This
document specifies its components; the theory is in
[02-safety-and-guardian.md](02-safety-and-guardian.md). Sources: `clio/guardian/`.

## 1. `GuardianGate.review()`

`clio/guardian/gate.py:122-517` (733 lines). Verdicts `{allow, transform, reject}`;
paths `{normal, fast, due_process, blocked}` (`gate.py:85-95`).

Complete review order:

| Order | Condition | Path | Action |
|---|---|---|---|
| 1 | Oversight pause/preview active | `blocked` | **REJECT** (H1 — always wins) |
| 2 | Classifier verdict `blocked` (H3/H6 pattern) | `blocked` | **REJECT** |
| 3 | Money > `H2_capital_at_risk_usd` | `blocked` | **REJECT** (H2) |
| 4 | Protected (commissioned/pinned) destructive | `due_process` | field mode: trash 180 d + `TRANSFORM` → `fs.read`; normal mode: `ModelDueProcessVerifier` (T2) |
| 5 | Agent-owned destructive | `fast` | trash 30 d + snapshot → **allow (transformed)** |
| 6 | Writes | `normal` | auto-register artifact, then allow |
| 7 | Under a workspace lease | — | enforce `allowed_paths` / `budget_limit_usd` (`_review_lease`) |

`ModelDueProcessVerifier` (`gate.py:42-82,356-418`): a T2 model call that reviews a
due-process destruction with the commission context; in field mode the transform
path makes it mostly unnecessary.

Approvals: when the safety level demands one (`ETHOS_SAFETY_LEVEL` 2/3), the gate
creates an approval and waits — default timeout 300 s (`clio/guardian/approval.py`),
managed via `clio approvals list|approve|deny` over the toolhost HTTP surface.

## 2. The action classifier

`clio/guardian/classifier.py` (737 lines) — the pattern-matching brain of the gate:

- **`COMMAND_TABLE`** (`:23-119`): ~60 POSIX + ~30 Windows commands, each mapped to
  a risk category.
- **`DANGEROUS_PATTERNS`** (`:131-187`): 14 POSIX + 22 Windows regex patterns —
  `rm -rf /`, fork bombs, credential exfiltration shapes, `curl | sh`, and so on.
- **`_TEXT_ONLY_COMMANDS`** (`:361`): commands that only consume text and cannot act
  (`grep`, `cat`, `wc`…) — fast-pathed.
- **`classify_shell`** (`:537-610`): parses via **bashlex** into a segment graph, so
  each segment of `a && b`, subshells, and redirections is classified individually —
  the reason `shell.run` allows only `&&` chaining ([07-gateway-and-toolhost.md](07-gateway-and-toolhost.md)
  §3): the parse stays total.
- **`TOOL_CATEGORIES`** (`:623-676`): all 42 categorizable tools mapped to risk
  categories; `subagent.*` = **execute + write** (spawning an agent is acting).
- **`check_manifest_paths`**: enforces a toolshed manifest's `allowed_paths`,
  `allowed_hosts`, `secret_scopes` before its execution.

## 3. The write-ahead journal

`clio/guardian/journal.py:67-256`:

- Entries: `begin` → (`end` with status `ok / failed / timeout / rejected /
  interrupted`).
- **Replayable only when `status == "ok"`** (`journal.py:44`) — a failed action is
  never replayed as if it succeeded.
- **Idempotency-key replay** (`journal.py:152-181`): a duplicate key replays the
  recorded result. Combined with GSL's `thread:step:n` keys
  ([06-work.md](06-work.md) §3), retries are always safe.
- On restart, `wake_up()` reconciles still-`running` entries to `interrupted`
  (`clio/memory/continuity.py`) — the journal never lies about what completed.

## 4. Trash

`clio/guardian/trash.py:57-214`:

- Layout: `trash/<id>/` + `manifest.json` + sha256 checksums.
- Retention: **30 days** (fast path) / **180 days** (due-process path).
- `fs.delete` **never** calls `unlink` (`tools/fs.py:403`); purge is a retention
  job, not an agent action.

## 5. Snapshots

`clio/guardian/snapshots.py:24-153`:

- **btrfs subvolume** snapshots where the filesystem supports them, else a
  **hardlink tree**.
- Retention ladder: `15m × 4, hourly × 24, daily × 7` (`_prune`, `:112`).
- `restore()` powers workspace-lease rollback
  ([06-work.md](06-work.md) §3) and due-process recovery.

## 6. The artifact registry

`clio/guardian/registry.py:32-156`:

- Every write registers an **artifact** row (path, hash, provenance, created_by).
- `is_protected` computes protection by **path prefix or ancestor** — commissioned
  deliverables and pinned paths are protected *before* anyone tries to delete them,
  which is what makes H5 enforceable mechanically.
- `integrity.py:13-72`: a nightly fingerprint pass re-verifies registered artifacts,
  trash manifests, and the audit chain — the registry is checked against reality,
  not just appended to.

## 7. The audit chain

`clio/observability/audit.py:202`:

- **`GENESIS_HASH`** = 32 zero bytes (`:17`); each entry's hash covers the previous
  entry — a tamper-evident chain [D-33].
- Postgres sink appends under **advisory lock `918_273_645`** (`:61`); file sink
  fsyncs (`:98`).
- `verify_chain` (`:185`) validates the whole chain; the core **anchors** the chain
  every 600 s (`clio/core/runner.py:279`).
- H1 makes the chain untouchable by the agent; the nightly integrity pass makes it
  *observed* to be intact, not merely asserted.

## 8. Safety levels in force

| Level | Gate behaviour |
|---|---|
| 1 | Dialogue only — tool calls refused wholesale |
| 2 | All tools on approval |
| 3 | Internet-reachable tools on approval |
| 4 | Field mode — transform-first, vetoes only on the numeric/structural limits |

Levels are read live from the control row (30 s recheck,
[08-runtime.md](08-runtime.md) §5), so a person can raise the level
without restarting the agent — and the agent cannot lower it
([02-safety-and-guardian.md](02-safety-and-guardian.md) §5, H1).

## 9. What the guardian does not cover

Stated honestly in the architecture doc (`AI_AGENT_ARCHITECTURE.md` §9):

- The guardian sees **tool calls and shell text**. Prose the agent writes to a
  person is not a tool call; the compensating controls are the deliberation
  guardrail ([05-social.md](05-social.md) §4), the audit trail,
  and oversight.
- H1/H3/H4/H6 are declarations enforced structurally (classifier + audit +
  reversibility), not single switches; H2 and H5 are the mechanically enforceable
  core.
- With no model keys, guardian reflex rules and journaling still run — the safety
  floor is model-independent.
