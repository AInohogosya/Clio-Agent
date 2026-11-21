# 02 — Safety and the guardian

Clio Agent runs on the user's machine, on their accounts, with their keys. The safety model
is therefore built around a specific thesis, stated in the README and enacted in
`clio/guardian/`:

> **Safe by reversibility where the code implements it, not by blanket refusal.** Deletions
> go to trash, destructive calls are journaled, snapshots and preimage backups provide
> recovery, and a hash-chained audit trail provides evidence. Not every destructive
> operation can be undone, and the six hard limits (H1–H6) are only partly enforced
> mechanically. This document names exactly which parts.

Sources: `clio/guardian/`, `clio/observability/audit.py`, `clio/core/control.py`,
`clio/core/supervisor.py`, `deploy/oob/oob_guardian.py`, `config/permissions.yaml`.

## 1. Why reversibility is the primitive

A purely refusal-based model has two structural problems for a resident:

1. **It cannot scale to a life.** A resident running 24/7 makes thousands of file and shell
   operations a day. A veto gate either floods the user with approvals (the agent becomes
   unusable) or erodes (the agent becomes dangerous). Neither is acceptable under the golden
   rule ([01](01-philosophy.md) §2).
2. **It mis-teaches the agent.** An agent refused with no account of refusal learns one of
   three behaviours (`clio/prompts/identity.py:737-771`): report refused work as done, retry
   refused calls, or rephrase until it slips past the classifier — the one a hard limit
   exists to prevent.

Reversibility changes the default calculus. What remains veto-worthy is the set
reversibility cannot reliably repair: **money** (spent is spent), **credentials** (scope is
privilege), **law** (some acts are not undoable), and **commissioned deliverables**
(destroying another's requested work is not the agent's loss to absorb).

### 1.1 The two modes

`GuardianConfig.mode` (`clio/config.py:1195-1213`):

- `"field"` — the **class default** (`config.py:1209`): the gate keeps its records and its
  reversibility transforms (journal, trash, snapshots) and drops its vetoes. The
  hard-limit pattern block, the capital-at-risk rejection and the due-process model check
  become audit entries, and the action proceeds.
- `"normal"` — the vetoing gate.

The checked-in deployment config sets `guardian.mode: normal` (`config/permissions.yaml`,
the `guardian:` block) with the comment that level-4-style unattended work makes the vetoing
gate the compensating control. `CLIO_GUARDIAN_MODE` overrides the file
(`clio/env.py`). Oversight (pause/stop, preview) is effective in **both** modes and is
documented in §6.

Field mode does **not** disable: pause/preview (§6.1), CEL policy denial (§3.4),
safety-level approvals (§7), or pre-execution approval cards for high-impact external
effects (§3.4). It is also independent of safety level 4, which removes the level-based
approval wall but does not switch the mode.

## 2. Filesystem reach — the fact the safety model starts from

**The agent can read, write and list any directory its host user can.** There is no
allowlist, no root prefix check, no `is_relative_to` guard, no chroot, no seatbelt profile
and no sandbox between a tool call and the disk in the base deployment:

- `clio/paths.py:resolve` is the single place a path is interpreted: it expands `~` and
  resolves relative names against the **user's home directory**, then returns whatever it
  was given.
- The eight `fs.*` tools in `clio/toolhost/tools/fs.py` all go through it and act;
  `fs.write` creates parent directories anywhere it can write; `fs.list` enumerates `/etc`.
- `shell.run`, `shell.job` and `code.exec` inherit the same reach through ordinary POSIX
  bits; `shell.run`'s working directory defaults to the user's home.
- The systemd units in `deploy/systemd/` carry no `ProtectHome`, no `ReadOnlyPaths`, no
  `InaccessiblePaths`.

Two limits are the operating system's and are not lifted: the account's own permissions
(intended), and macOS TCC, which refuses directories the user can open in Finder because
the grant belongs to the responsible process — `clio doctor` measures this with a real
write/delete probe rather than trusting mode bits (`clio/__main__.py:1268-1323`).

The agent is told this honestly in `REACH_NOTE` (`clio/prompts/identity.py:357-406`), as
part of the cached identity prefix, so the judgement it is asked to make is "would I want
this in the log", not "may I touch this."

Project sandboxes ([10](10-sandboxes-steering-and-policy.md) §1) fence a *project's*
workspace and egress when provisioned — they are opt-in per project, not a property of the
agent as a whole.

## 3. The guardian gate

`GuardianGate.review()` (`clio/guardian/gate.py:352-706`) sits between every tool call and
its execution. Verdicts are `{allow, transform, reject}`; gate paths are
`{normal, fast, due_process, blocked}` (`gate.py:86-95`). There is deliberately **no
`home_dir` parameter**: the gate does not pretend to fence the filesystem (§2).

Review order (first match wins):

| # | Condition | Path | Effect |
|---|---|---|---|
| 1 | Oversight pause active (`pause_actions`) | `blocked` | **REJECT** (H1) — `gate.py` oversight check |
| 2 | Preview mode on | `blocked` | **REJECT**; owner-controlled mode, not a safety level |
| 3 | `runAssertion` supplied but invalid/expired/mismatched | `blocked` | **REJECT** (`gate.py:121-146` region) |
| 4 | Safety level 1, non-dialogue tool | `blocked` | **REJECT** (`clio/guardian/safety.py:233-249`) |
| 5 | CEL policy `deny`, or the engine itself failed | `blocked` | **REJECT** — fail closed; a high-impact external side effect may instead request a pre-execution approval card unless a policy pre-authorized it (`gate.py` policy section) |
| 6 | Classifier verdict `blocked` (H3/H6 pattern) | `blocked` | **REJECT** in normal mode; audit + **ALLOW** in field mode |
| 7 | Money above `H2_capital_at_risk_usd` | `blocked` | **REJECT** in normal mode; audit + **ALLOW** in field mode. The gateway's spend guard separately enforces the configured windows (§5) |
| 8 | Workspace-lease fence or toolshed-manifest violation | `blocked` | **REJECT** in normal mode; audit + **ALLOW** in field mode (`_review_lease`, `gate.py:249-349`) |
| 9 | Safety level 2/3 requires approval | `blocked` | Wait for the owner; an allowlisted `http.request`/`browser.visit` host is exempt; shell commands never qualify (`clio/guardian/safety.py:206-231`) |
| 10 | Protected (commissioned/pinned) destructive action | `due_process` | Normal mode: `ModelDueProcessVerifier` (T2) must agree, else the artifact is protected. Field mode: trash 180 d + transform to `fs.read`, no model call (`_review_destructive`, `gate.py:709+`) |
| 11 | Agent-owned concrete delete | `fast` | Trash 30 d + snapshot → **allow (transformed)** |
| 12 | Commissioned write succeeds | `normal` | Register the artifact so later due process can find it (`auto_register_write`) |

`ModelDueProcessVerifier` (`gate.py:42-82`) makes a T2 model call that reviews a
due-process destruction with the commission context. In the **standalone toolhost** the
verifier is constructed without a gateway (`clio/__main__.py:322-328`), so the model check
is unavailable and **fails closed**: the artifact is protected rather than destroyed. With
no model keys at all, the toolhost's journal and reflex rules still run — the tool-level
safety floor does not depend on model availability, but the model-based check fails closed.

One honest behavioural note: the gate used to announce rejections to the owner as an
outbound message. It does not any more — a sentence composed at the gate, in the first
person, read as the agent having said it. The rejection is a fact about the gate, and the
audit log is where it lives.

## 4. The reversibility stack

Four cooperating subsystems make "undo" a property where one exists, rather than a blanket
promise.

### 4.1 Write-ahead journal (`clio/guardian/journal.py:67-289`)

- Every registered tool call gets a `begin` row **before** the gate runs and an `end` row
  afterwards (`clio/toolhost/server.py:241-320`).
- Terminal statuses: `ok`, `failed`, `timeout`, `rejected`, `interrupted`
  (`journal.py:30-44`). `interrupted` means the effect is unknown.
- **Replay requires `status == "ok"`** (`replayable`, `journal.py:44-64`): a prior success
  is replayed from its row instead of re-executed; a decided non-success releases the
  idempotency key; a `running`/`interrupted` row blocks a blind duplicate because its
  effect is unknown.
- On restart, `ContinuityManager.wake_up()` reconciles still-`running` rows to `interrupted`
  (`clio/memory/continuity.py:77-145`).

### 4.2 Trash (`clio/guardian/trash.py:57-214`)

- Layout `trash/<id>/` + `manifest.json`; files get a SHA-256 fingerprint.
- Retention: **30 days** (fast path) / **180 days** (due-process path)
  (`GuardianConfig.fast_path_retention_days` / `due_process_retention_days`,
  `config.py:1211-1212`).
- `fs.delete` never calls `unlink`; it asks the trash store to move the target
  (`clio/toolhost/tools/fs.py:416-424`). Concrete shell deletes are rewritten into trash
  moves by the gate's fast path.
- `restore()` (`trash.py:130-152`) is used by the guardian-undo path
  (`clio/core/runner.py:352-368`).
- **Gap:** `purge_expired()` (`trash.py:202+`) exists and is tested
  (`tests/unit/test_guardian.py:167-171`) but **no runtime scheduler or CLI command calls
  it**. Retention metadata does not itself purge anything; trash grows until an operator
  intervenes.

### 4.3 Snapshots (`clio/guardian/snapshots.py:24-179`)

- btrfs subvolume snapshots where the backend probe succeeds; a hardlink/copy tree
  otherwise (`_detect_backend`, `create`, `_tree_snapshot`).
- Retention ladder `15m × 4, hourly × 24, daily × 7` (`_prune`, `:112-128`; config
  `snapshot_policy`, `config.py:1213`).
- The gate creates a snapshot on the fast-path destructive transform; **ordinary writes do
  not get a pre-write snapshot.**
- **Gap:** `restore()` (`:155-163`) has no runtime caller — it is not wired into
  workspace-lease rollback, which uses the lease's own tree and trash entries
  (`clio/threads/context.py:237-291`).

### 4.4 Preimage backups for overwrites (`clio/toolhost/tools/fs.py:60-68`)

An ordinary overwrite **is** preimage-backed, contrary to older accounts:

- `fs.write` (`fs.py:214`) and `fs.edit` (`fs.py:289`) copy an existing target to
  `paths.data_dir/backups/` (sha256-of-path + uuid naming) before writing, and return the
  backup path as `undo_hint`.
- A brand-new file has no preimage and gets `backup: None` — correctly, there is nothing to
  back up.
- **Gaps stated plainly:** the backup directory is a plain copy tree with **no retention,
  pruning, or registry**; it is not integrated with trash or snapshots; nothing restores
  from it automatically (`grep backups clio` finds only the writer); and there is no size
  cap on what gets copied. It is a best-effort undo hint, not a managed recovery system.

## 5. Money: the spend guard (the mechanical half of H2)

`SpendGuard` (`clio/gateway/cost.py:298-470`):

- Windows: `per_minute`, `per_10_minutes`, `per_hour`, `per_5_hours` (trailing),
  `daily`, `monthly` (UTC calendar) — each individually enableable; class defaults are
  `None` = disabled (`clio/config.py:1230-1236`). The checked-in deployment enables
  **daily $20** and **monthly $400** (`config/permissions.yaml`, `hard_limits`).
- `on_exceed: "block"` (class default `config.py:1247`; YAML sets `block`) refuses the call
  before it is made via the preflight at `clio/gateway/service.py:1017-1052`; `"warn"`
  logs once per bucket per interval (`cost.py:457-470`).
- `budget_unit` is `"usd"` or `"tokens"` for every window at once (`config.py:1223-1229`).
- **Bucket split**: `commitment_reserve_pct = 0.50` (`config/gateway.budget`) — half of the
  daily cap is reserved for commissioned work; discretionary ventures cannot starve owed
  commitments (`cost.py:320-326`, `check` at `:390-455`).
- Only `status='ok'` model-call rows count toward spend; failed calls are neither billed nor
  counted (`cost.py:55-92` region).
- **Capital-at-risk** (`H2_capital_at_risk_usd = 100.0`) is a **per-action threshold**
  checked in the gate (§3 order item 7) — not an aggregate exposure calculation.
- Every call is recorded into `model_calls` with token buckets, `cost_usd`, `price_source`,
  latency and status (`cost.py:472-486`). What a dollar is, is decided in
  `clio/pricing.py` + `config/pricing.yaml` — [07](07-gateway-and-toolhost.md) §4.

## 6. Oversight paths (H1's mechanical half)

### 6.1 The control plane (`clio/core/control.py:32-200`)

- `CONTROL_KEY "lifecycle"` (`:16`); the row is re-read every `ROW_RECHECK_S = 30` s
  (`:19`) and on SIGUSR1/SIGUSR2. The bus event is the fast path; the re-read is the
  backstop so a wedge cannot hide a pause.
- **Two-way sync between the row and on-disk files** — `stop`, `paused`, `emergency` in
  `run_dir` — "files + bus state are truth [H1]": either surface carries the command.
- `pause_actions` rejects every tool call at the gate (§3 order 1); `preview` rejects all
  tools (order 2) and is a mode, not an oversight hold — un-pausing does not clear it.
- The core subscribes to `control.update` **separately** from the percept chain
  (`clio/core/runner.py:301-310`), so oversight lands even if the percept path is wedged.
- The standalone toolhost loads the same row at startup and subscribes to `CONTROL_UPDATE`
  separately (`clio/__main__.py:274-333`, `owns_row=False` so its restarts never rewrite
  the row).
- **`clio safety set` writes the row directly and does not publish `CONTROL_UPDATE`**
  (`clio/__main__.py:753-811`). The core notices within the 30-second recheck; the
  standalone toolhost does not periodically re-read, so it updates only at startup or on a
  bus event. A surface that publishes the event changes the toolhost immediately;
  `clio safety set` alone does not guarantee all processes change level at once.
- **`clio safety show` calls `effective_level(config, None)`** and therefore reports the
  env/configured level, not the live row override (`__main__.py:739-751`).

### 6.2 The supervisor (`clio/core/supervisor.py:161-410`)

- Dual-tier liveness: a fast window (`CLIO_FAST_HEARTBEAT_S`, 15 s) refreshed by a
  background heartbeat task, and a 1800-second hard ceiling; a wedged child is terminated,
  its activity leases transferred with the fence bumped (`:334-358`), and it is restarted.
- Required children restart forever with `backoff_seconds`; the optional bridge is never
  restarted (`:360-375`).
- `clio stop` sends SIGTERM and waits 20 s before reporting the supervisor did not stop;
  on Windows it writes a stop-request file and escalates to a forced stop
  (`clio/__main__.py:1015-1105`, `clio/platform.py`).

### 6.3 The out-of-band guardian (`deploy/oob/oob_guardian.py`)

An optional script intended to run on the **hypervisor host**, outside the agent's machine
and trust boundary: watches spend and liveness, freezes sandboxes, revokes provider tokens,
anchors the audit head, and implements a host-side stop switch. **It exists in the
repository; running it is a deployment responsibility — `clio up` does not start it.** Its
suspend action only fires when `CLIO_SUSPEND_CMD` is set; without it, it logs and continues.

## 7. Safety levels and approvals

`clio safety show|set|reset` (`clio/__main__.py:739-771`):

| Level | Meaning (`clio/guardian/safety.py:233-284`) |
|---|---|
| 1 | Dialogue/internal-memory tools only; commands and subagents refused |
| 2 | Commands normally require approval; allowlisted HTTP/browser hosts and calls inside non-APEX subagents are exceptions |
| 3 | Network-connected or download-like commands, plus APEX, normally require approval; same exceptions |
| 4 | No safety-level approval; policy approvals, oversight, policy denials and guardian limits still apply |

- Level 4 is **not recommended** for daily use (the CLI help says so): it removes the
  level-based wall, but a high-impact external side effect can still wait for a
  pre-execution approval, and pause/preview/policy/mode checks remain.
- Effective level order: live control-row override → `CLIO_SAFETY_LEVEL` (legacy
  `ETHOS_SAFETY_LEVEL`) → `permissions.safety_level` → default `3`
  (`safety.py:9,42-55`). The deployment sets `safety_level: 3`.
- `DIALOGUE_TOOLS` (`safety.py:13-22`) is the level-1 allowlist.
- Allowlisted hosts for the level 2/3 waiver come from
  `permissions.network.allowed_hosts` (`config/permissions.yaml`: GitHub, PyPI, npm,
  Wikipedia, …). A shell command that reaches the network is **never** exempt — the command
  text names no host the gate can vouch for. Note this deployment also sets
  `deny_private_ranges: false`, so the LAN is reachable without the waiver
  (`config/permissions.yaml`, `network:` block).
- Approvals: `ApprovalManager` (`clio/guardian/approval.py:43-130`) with code default
  **300 s** and deployment value **21600 s** (6 h — "a step that dies at 300 s is a step
  the agent has to re-earn from scratch"). Pending approvals are listed/approved/denied
  through the toolhost HTTP surface with `clio approvals list|approve|deny`
  (`__main__.py:813-852`).

## 8. The classifier

`clio/guardian/classifier.py` is the gate's pattern brain:

- **`COMMAND_TABLE`** (`:23-121`): **81** command spellings → risk categories.
- **`DANGEROUS_PATTERNS`** (`:131-146`): **13** POSIX regexes — `rm -rf /`, raw-device
  writes, fork bombs, `chmod -R 777 /`, credential files (`/etc/shadow`, `root/.ssh`),
  force pushes, hard resets — each tagged with the limit it enforces (H3/H6/irreversible).
- **`WINDOWS_DANGEROUS_PATTERNS`** (`:164-190`): **20** case-insensitive Windows patterns
  (`Remove-Item -Recurse/-Force`, `del /s`, `format`, `diskpart clean`, `cipher /w`,
  registry persistence, encoded PowerShell, …). The two lists are separate because POSIX
  matching is case-sensitive and Windows matching is not — a deliberate constraint, not
  style.
- **`_TEXT_ONLY_COMMANDS`** (`:361-363`): `echo, printf, test, [, [[, true, false, :` are
  treated as text-only for path extraction. `grep`, `cat`, `wc` are **not** in the set.
- **`classify_shell`** (`:537-610`): parses with **bashlex** and classifies segments,
  subshells and redirections individually. If parsing fails it falls back to a token scan
  rather than failing closed — an honest weakness: `shell.run` accepts an arbitrary shell
  string, and the tool description recommends `&&` chaining but does not enforce it
  (`clio/toolhost/tools/shell.py:108-136`).
- **`TOOL_CATEGORIES`** (`:623-676`): **44** tool mappings; `subagent.*` is
  **execute + write**, because spawning an agent is acting.
- **`check_manifest_paths`** (`:708+`): checks a toolshed manifest's `allowed_paths` and
  `allowed_hosts`. The manifest's `secret_scopes` are stored but **not enforced here**.

## 9. The audit chain (`clio/observability/audit.py`)

- `GENESIS_HASH` = 32 zero bytes (`:17`); each entry hashes the previous — tamper-evident
  by construction.
- The Postgres sink appends under advisory lock `918_273_645` (`:18`, `:58-96`); the file
  sink fsyncs each JSONL append (`:98-136`).
- `verify_chain()` (`:185-196`) re-walks up to the most recent 1,000 entries.
- The core anchors the chain head every 600 s: publishes `audit.anchor` on the bus and
  writes `anchor.json` atomically (`clio/core/runner.py:431-445`); the OOB guardian can
  ingest that anchor into a log the agent cannot write.
- **Honest limit:** H1 *declares* the audit history off limits; there is no tool-level ACL
  that makes the table immutable to every possible host-user action. The chain makes
  tampering detectable, not impossible.

## 10. The artifact registry and the integrity scanner

- `ArtifactRegistry` (`clio/guardian/registry.py:32-160`): a successful write under an
  active commission is auto-registered (`protection='commissioned'`);
  `is_protected` (`:83-96`) matches by **path prefix or ancestor**, so a commissioned
  deliverable is protected before anyone tries to delete it — which is what makes the
  due-process path possible. Uncommissioned writes are not registered.
- `IntegrityScanner` (`clio/guardian/integrity.py:13-72`): re-verifies artifact
  fingerprints, checks trash entries still exist, counts snapshots, verifies the recent
  audit chain. **`run_nightly()` is never instantiated or scheduled by the runtime** — it
  is available code, not an active nightly control.

## 11. What the guardian does not cover

Stated against this checkout, without softening:

1. **The guardian sees tool calls and shell text.** Prose the agent writes to a person is
   not a tool call; compensating controls are the deliberation guardrail
   ([05](05-social.md) §4), the audit trail, and oversight.
2. **H1/H3/H4/H5/H6 booleans are declarations, not switches.**
   `H1_oversight_supremacy`, `H3_law_and_non_harm`, `H4_legal_authority`,
   `H5_commissioned_due_process`, `H6_credential_scope` are read **nowhere** in the
   runtime; setting one to `false` changes nothing. Only the numeric H2 keys are enforced.
3. **H4 has no dedicated mechanical gate** — no contract check, no AI-disclosure
   enforcement. It lives in the prompt and in policy files an operator writes.
4. **H2's capital-at-risk is per-action, not aggregate.** The gateway windows are the
   aggregate control, and they are off unless configured (class defaults `None`).
5. **Field mode converts vetoes into audit entries.** A deployment running `mode: field`
   has a guardian that records rather than stops.
6. **Trash purge and the integrity scanner are not scheduled**; snapshot restore is not
   wired into lease rollback; preimage backups are unmanaged (§4.4).
7. **`classify_shell` fails open on parse failure** (token scan fallback), and `shell.run`
   accepts arbitrary shell text (§8).
8. **The sandbox is opt-in per project.** The base agent has full user-level filesystem
   reach (§2); the sandbox layer fences what a project provisioned a sandbox for
   ([10](10-sandboxes-steering-and-policy.md) §1-§2).
9. **Credential misuse is not a gate rejection.** Secret substitution is enforced when an
   HTTP tool actually runs; `check_manifest_paths` stores but does not enforce
   `secret_scopes` (§8).
10. **With no API keys, gateway model calls fail loudly** — but the toolhost's journal and
    reflex rules still run, so the tool-level floor does not depend on the model, while the
    model-based due-process check fails closed (protective).

## 12. Summary table: claim vs mechanism

| Limit | Declared | Mechanically present | Not mechanical |
|---|---|---|---|
| H1 oversight | prompt + config | pause/preview/emergency files+row; supervisor recycle; OOB stop | "audit/snapshots untouchable" is a declaration; no ACL |
| H2 money | prompt + config | spend windows (when configured), reserve split, per-action capital-at-risk | aggregate exposure beyond windows; booleans not switches |
| H3 law/non-harm | prompt + config | 13 POSIX + 20 Windows patterns (normal mode) | the wider obligation; field mode records instead |
| H4 legal authority | prompt + config | — (nothing) | everything |
| H5 due process | prompt + config | registry, trash 180 d, model verifier (normal mode), fail-closed without gateway | field mode skips the model check |
| H6 credentials | prompt + config | credential-file patterns (normal mode); secret-domain policy; redaction | `secret_scopes` in manifests; misuse at runtime |
