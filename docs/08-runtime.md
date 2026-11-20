# The process model: seven processes, one event bus

Clio Agent runs as a supervised constellation of processes that share one database
and one event bus. Sources: `clio/core/supervisor.py`, `bootstrap.py`, `runner.py`,
`control.py`, `clio/bus/event_bus.py`, `clio/platform.py`, `config.py:62-75,207-211,1202-1243`.

## 1. The seven processes

`Supervisor.build_specs()` (`clio/core/supervisor.py:189-234`):

| Process | Command | Optional | Why it is separate |
|---|---|---|---|
| `clio-gateway` | `gateway` | no | model I/O and money accounting, isolated from the mind |
| `clio-toolhost` | `toolhost` | no | every side effect, behind the guardian |
| `clio-comms` | `comms` | no | all channel listening (exactly one long-poll per bot) |
| `clio-workers-{1,2}` | `worker` | count via `config.workers.count` (default 2) | delegated goal pursuit |
| `clio-core` | `core` **without** `--listen-channels` | no | the life loop; would double-poll channels otherwise (`__main__.py:195-201`) |
| `clio-web` | `node web/server/index.mjs` | **yes** (only if `web/node_modules` exists) | interface bridge |

Ports/sockets: gateway UDS `/run/clio/gateway.sock` or TCP `127.0.0.1:8700`
(`config.py:207-211`); toolhost `run_dir/toolhost.sock` or `8701`; web HTTP `8720`,
WS `8710` (`config.py:1202-1205`); webhook receiver `127.0.0.1:8730`
(`:1233-1243`); CLI socket `~/.ethos/run/cli.sock` (`:1197-1199`); core metrics
`9710` (`runner.py:325`); Postgres default
`postgresql://ethos:ethos@127.0.0.1:5433/ethos` (`bootstrap.py:57`); `run_dir =
/run/ethos` if writable else `~/.ethos/run` (`config.py:62-75`).

## 2. The supervisor

`clio/core/supervisor.py:144-145,293-395`:

- Poll every `2.0 s`; required children restart **forever** with
  `backoff_seconds(count, base=3.0, max=48.0)`; the optional bridge is never
  restarted.
- Restart counts reset after **60 s stable**.
- **Wedged-core detection**: the core must touch `run_dir/heartbeat`; stale beyond
  `CLIO_CORE_HEARTBEAT_STALE_S` (default 1800 s) → terminate and restart. A core
  that hangs without raising still gets caught.
- Shutdown: reverse-order `terminate()` → `10.0 s` grace (`SHUTDOWN_GRACE_S`) →
  `kill()`.
- Windows: `clio stop` writes a **stop-request file**
  (`request_stop_by_file`), because CPython's `os.kill` on Windows is an
  unhandleable `TerminateProcess` for any signal other than CTRL_C/BREAK
  (`clio/__main__.py:954-998`, `platform.py:318`); escalation is
  `taskkill /T /F` after 20 s.

## 3. Bootstrap

`bring_up()` (`clio/core/bootstrap.py:428-553`), idempotent:

```
ensure Postgres → clio db upgrade → clio db seed (non-fatal)
  → install/build interface → clio doctor
  → detached supervisor (POSIX start_new_session)
  → clio await-agent → clio status → open browser
```

- Exit codes `0 / 1 / 130` (`bootstrap.py:73-78`); 130 = Ctrl-C, and the agent
  **keeps running** — it was detached on purpose (`start_new_session`).
- **Liveness is a sign of life, not a pid**: `clio status` and `clio await-agent`
  wait for the agent to have actually *answered for itself* within a window
  (900 s, `clio/core/status.py:29`, matching the interface's `AGENT_ABSENT_MS`) —
  "a supervisor whose children have all died is still a live process, and a bridge
  with the agent down serves the agent's last known state perfectly"
  (`__main__.py:587-593`).
- `clio doctor` probes reality before declaring health: pgvector presence, alembic
  head comparison, **real model requests** ("a present key is not a working one";
  only base-model failure fails the exit code — "the catalogue is a set of
  fallbacks"), channel door report, and a **filesystem reach probe**
  (`__main__.py:1163-1210`) — real-file probes, because "the macOS privacy layer
  refuses on a directory the mode bits say are wide open."

## 4. The event bus

`EventBus` (`clio/bus/event_bus.py:40-219`) — a transactional Postgres
LISTEN/NOTIFY bus [D-10]:

- `publish()` INSERTs into `events` and issues `pg_notify` **in the same
  transaction**, so NOTIFY fires only on commit (`:171-201`). An event never
  reaches a listener whose row failed to commit.
- A dedicated listener connection dispatches to kind-specific + wildcard handlers
  (`:64-110`); dedupe window `SEEN_ID_WINDOW = 16384` with one-at-a-time eviction
  (`:42`).
- **`drain()`** claims unconsumed events via a `consumed_by` array (`:203-219`) —
  startup gap recovery: what happened while the agent was down is not lost.
- Event kinds (`clio/schemas/events.py:10-31`): `percept, msg.inbound,
  msg.outbound, timer.fired, thread.request, thread.result, control.update,
  guardian.undo, thought.new, intention.update, presence.update, message.new,
  message.handled, action.update, budget.update, guardian.update, schedule.set,
  comms.send, consolidate.done, verify.request, wake.nudge`; trust levels
  `internal | known | untrusted` (`:34-37`).
- Core wiring (`runner.py:203-227`): `control.update` and `guardian.undo` are
  subscribed **separately** from the percept chain — oversight commands are applied
  even if the percept path is wedged [H1].

## 5. The control plane

`clio/core/control.py:427`:

- `CONTROL_KEY "lifecycle"`, recheck every `ROW_RECHECK_S = 30` s.
- **Two-way sync between the env_entities row and on-disk state files** (`:343`) —
  "files + bus state are truth [H1]": either surface can carry the command, and
  neither can be silently diverged from.
- `owns_row` semantics: the toolhost passes `owns_row=False` — "this process obeys
  the lifecycle…; without it every restart of *this* process would rewrite the row
  with its own `stopped: false`" (`__main__.py:290`).
- `clio safety set` writes a live override into the row (`__main__.py:668-699`);
  SIGUSR1/SIGUSR2 deliver immediate rechecks (`control.py:422`).

## 6. Platform abstraction

`clio/platform.py:561` exists because capability differences between OSes split
into two kinds (`:1-35`):

1. **Absent capabilities** (pty, Unix sockets, X11) — asked and **reported**
   (`available_capabilities()`, `:497-546`; `clio doctor` prints name + reason).
   "Nothing is silently no-op'd" (`:30`).
2. **Silently different conventions** — answered here, once:
   - `process_is_alive` (`:251-297`): on Windows `os.kill(pid, 0)` is a *kill*, not
     a probe; the Windows path uses `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`.
   - `restrict_to_user` (`:439-491`): Windows `icacls` grants are **read back and
     verified**, because `chmod 0o600` semantics do not exist there and any mode-bit
     check "is therefore vacuously true on Windows, which is the worst kind of wrong
     in a security check."
   - `detached_popen_kwargs` (`:194-218`): Windows needs
     `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP` because CPython "accepts the same
     keyword on Windows and *ignores* it" — the silently-dies-with-terminal bug.
   - `uvicorn_loop` (`:145-163`): uvloop pinned off on macOS + Python 3.12
     (SIGSEGV in `uv_tcp_init_ex`).

## 7. Env and configuration loading

`__main__.py:1257-1340`: env files load in order `$CLIO_ENV_FILE` → `./.env` →
checkout `.env`, **the real environment always wins**, guarded by
`CLIO_ENV_LOADED=1`. The parser is a deliberate 20-line KEY=value reader — "the
whole of what a .env has to be." `--config-dir` is validated as an existing
directory at startup (`:97-105`): "a named directory that is not there is a typo…
every later answer would be confidently wrong."

## 8. Theory: constellation over monolith

The seven-process split encodes separable concerns with different failure modes:

- **The mind** (core) can restart without losing memory (Postgres) or in-flight
  tool effects (toolhost owns those).
- **Money and side effects** are each isolated behind their own process boundary,
  so a wedged or compromised mind still funnels through the gate and the spend
  guard.
- **One listener per channel** is a structural guarantee (comms owns all doors),
  not a discipline.
- **Supervision is forever and skeptical**: restarts are unbounded, wedges are
  detected by heartbeat rather than by exception, and "running" is defined by signs
  of life — the process model refuses to confuse *processes exist* with *the agent
  is alive*.
## Part II — Communications: doors, not decisions

Channels are modeled as **doors**: a door being open means the agent *can hear*
through it; speaking is a separate capability. Sources: `clio/comms/host.py`,
`adapters/*`, `webhook.py`, `jwt.py`, `clio/comms/adapters/base.py:93-216`.

## 1. The adapter contract

`ChannelAdapter` (`adapters/base.py:93-216`):

- **`start = can speak, listen = can hear`** [P7] — the two capabilities are separate
  flags on every door. `clio core` starts **send-only** (the comms process owns
  listening), which is why a send can exist without a poller and vice versa.
- **Empty allowlist admits nobody.** `accept_from_anyone` is a *separate* switch
  (`base.py:139-157`) — the failure of an allowlist can never be silently papered
  over by a catch-all.
- **`offer_seen` dedupe** (`base.py:11`), per-message exception isolation in
  `emit_inbound` (`base.py:58`), `is_transient` classification for OSError classes
  (`base.py:122`).

## 2. The ten adapters

| Adapter | Transport | Auth | Notes |
|---|---|---|---|
| `telegram` | long-poll (`POLL_TIMEOUT_S 5.0`) | bot token | inbound retry 2→60 s within a 600 s budget; stop is bounded (15 s); group policy refused explicitly |
| `cli` | Unix socket, JSON-lines | local | deliberately no Windows support and **no TCP fallback** — a local socket is local by construction |
| `web` | loopback websockets | hello token (4401 on failure) | serves the browser interface |
| `email` | IMAP poll / SMTP 587 `start_tls` | credentials | thread-tag regex keeps replies threaded; SEEN UID stored with chunked flags |
| `whatsapp` | Cloud API webhook | HMAC on raw body | E.164 normalization; 24 h window refusals passed through as errors, not swallowed |
| `slack` | signed webhook | v0 signature, `MAX_SIGNATURE_AGE_S 300` | ignores self-echo subtypes; `"200 with ok:false"` trap handled — HTTP success is not API success |
| `discord` | gateway socket | bot token, intents `1<<9\|1<<12\|1<<15` | heartbeat 41.25 s ±5; `MAX_MESSAGE_CHARS 2000` split on line/space; **no RESUME — messages during an outage are lost, stated in code** |
| `line` | signed webhook | base64 HMAC | **no-expiry replay caveat documented in code**; push API, not replyToken |
| `teams` | Bot Framework webhook | JWT `RS256` | mention stripping; `serviceUrl` per-conversation cache with restart-cost story; https+suffix allowlist |
| `googlechat` | service-account webhook | JWT-bearer → access token | `scope chat.bot`; three key URL formats; threadKey = own message id |

Push channels (WhatsApp, Slack, LINE, Teams, GoogleChat) share **one loopback
webhook port**, distinguished by path.

`person_id` prefixes: `tg:`, `wa:`, `slack:<channel>`, `dc:<channel>`,
`line:<user>`, `teams:<conversation>`, `gchat:<spaces/…>`, `email:<addr>`, `cli:`,
`web:`.

## 3. Webhook security

`clio/comms/webhook.py:407` — the order of operations is the security:

1. **Auth before parse**: signature (HMAC over the **raw** body,
   `meta_signature = sha256`) is verified before any body decoding (`:39,64,83,112`).
   Constant-time comparison (`handshake_ok`, `:334`).
2. **404 before body**: unknown paths are rejected before reading the body (`:186`).
3. **Challenge before signature**: provider URL-verification challenges answer
   before signature checks where the provider requires it (`:228`).
4. **Handler failure still returns 200** to the provider (`:239-247`) — a redelivery
   storm is worse than one lost event, and dedupe is upstream anyway.
5. `MAX_HEADERS 64`, max body 262144 bytes, receiver loopback-only.
6. A `WebhookRoute` with no secret configured returns `False` for every handshake —
   an unconfigured door admits nobody.

## 4. JWT verification

`clio/comms/jwt.py:564` (Teams, GoogleChat):

- Algorithm **pinned to `RS256`** (`:65`) — the `alg` header is never trusted.
- Parse order: **shape → signature → audience → time → identity** (`:370`).
- Audience matching rejects substring matches (`:511`).
- **`exp` absent = refusal** (`:542`) — a token that never expires is a token that
  cannot be trusted.
- `KEY_TTL_S 3600`, `CLOCK_SKEW_S 60`.

## 5. The comms host: doors follow configuration

`CommsHost` (`host.py:1121`):

- **`watch_channels`** (`:1000`): re-reads channel config live — a stamp on the
  config files every 2 s (`WATCH_INTERVAL_S`), a full rescan every 8 polls
  (`RESCAN_EVERY_POLLS`), with inode-aware stamping (`:92`). "A token pasted into a
  settings screen is a token this process is holding a poller for within a couple of
  seconds" (`clio/__main__.py:402-405`).
- **`plan()`** (`:264`): builds the desired door set (`_Plan`, fingerprinted by
  sha256 `:158`), resolving credentials exactly once.
- **Reconciliation is close-before-open** (`:763,766`) — a changed door is closed,
  then reopened with the new config; a token rotation never has two live pollers.
- **Retry curve** (`:82-89`): base 2.0, max 300.0, permanent after 60.0 s of
  failures — the Telegram-502 lesson: transient errors retry, dead doors are marked
  permanently shut and reported.
- **`EADDRINUSE` + not-listening** → "door held elsewhere" info log (`:849`) — the
  diagnosed state, not a crash.
- **`inbound_to_bus`** (`:1073`): exhaustive log entry, `social.record_inbound`,
  publish `INBOUND_MSG` with text truncated to 4000 chars, urgency seeded `0.3`
  (known sender) / `0.2` (untrusted) before attention re-scores it.

## 6. Theory: doors as trust boundaries

The comms layer encodes three positions:

1. **Capability split.** Hearing and speaking are separate doors [P7]: the agent can
   be reachable without being a broadcaster, and able to act (commissioned sends)
   without being pollable.
2. **Deny by default, at every layer.** Empty allowlist admits nobody; a secretless
   webhook route admits nobody; an expired-absent JWT refuses. There is no
   configuration state in which "unset" means "everyone."
3. **Honest failure.** No-RESUME message loss on Discord, no-expiry replay on LINE,
   and platform impossibilities are **documented in the adapter code** rather than
   papered over — the same discipline as `clio doctor`'s filesystem-reach report:
   report the capability as it is, name the reason when it is absent
   (`clio/platform.py:1-35`).
## Part III — Observability: metrics, audit, logs

The agent is a long-running resident, so its observability is designed for
*forensics over time*, not dashboards alone. Sources: `clio/observability/`.

## 1. Metrics

`clio/observability/metrics.py:196` (Prometheus, core on port **9710**,
`clio/core/runner.py:325`; `start_metrics_server` never raises):

- **`MODEL_CALLS`** labelled by `purpose` — the plan-vs-step distinction
  (`:9-25`) exists because "one number" for model calls hides whether spend went to
  planning or execution.
- **`MODEL_FAILURES`** with a **disposition** label from
  `MODEL_FAILURE_DISPOSITIONS` (NA-P3.2) — failures are typed by what they mean
  (rate limit, unusable, model-scoped, …), matching the failure-scope routing in the
  gateway.
- **`MODEL_RETRIES`** with a `changed_request` label (`:46`) — a retry that widened
  the request is counted separately from a blind retry.
- **`MODEL_CALL_COST`** histogram; free-vs-unused calls observe `0.0` explicitly
  (`:67`) rather than being absent — "zero spend" and "no data" are different
  facts.

## 2. The audit chain

`clio/observability/audit.py:202`:

- Hash-chained entries [D-33]: `GENESIS_HASH` = 32 zero bytes (`:17`), each entry
  covering the previous — tamper-evident by construction, not by policy.
- Sinks: `DbAuditSink` (appends under Postgres advisory lock `918_273_645`, `:61`)
  and `FileAuditSink` (fsync, `:98`).
- `verify_chain` (`:185`) re-walks the chain; the core **anchors** the chain every
  600 s (`clio/core/runner.py:279`) so even a gap can be bounded in time.
- H1 makes the chain untouchable by the agent; the nightly integrity pass
  (`clio/guardian/integrity.py:13-72`) makes its intactness *observed*.

## 3. Exhaustive logging

`clio/observability/exhaustive.py:243`:

- JSONL files under `~/.ethos/data/logs`, enabled by default
  (`CLIO_EXHAUSTIVE_LOG`, `:50-54`).
- Kinds: `user.action`, `settings.change`, `model.request`, `model.response`
  (including `cot`, `cot_exposed`, usage pairs), `model.failure`.
- `record` **never raises** (`:66`) — the flight recorder must not itself become the
  failure.

This is the fine-grained trail: every model call and response (with reasoning-token
accounting), every setting change, every user action — the raw material for the
post-mortems the codebase's own design comments are made of.

## 4. Secret redaction

`clio/observability/logger.py:166`:

- `_SECRET_PATTERNS` (`:26-30`) covers Telegram bot tokens
  (`\d{6,12}:[A-Za-z0-9_\-]{30,}`), JWTs (`eyJ…`), and `{{secret:KEY}}` patterns.
- A `RedactingFilter` is installed on root handlers and also redacts `httpx` URL
  objects (query strings carry keys); `format_exc_info` is applied **before**
  redaction so tracebacks are covered too (`:59,142`).
- Secrets at rest are pyrage-encrypted via the `secrets.*` tools with
  `{{secret:KEY}}` interpolation (`clio/gateway/secrets.py`) — the same notation
  the redactor looks for, so a secret that leaks into a log is caught by pattern
  wherever it came from.

## 5. Tracing

`clio/observability/tracing.py:22`: optional OpenTelemetry, enabled via
`CLIO_TRACE_CONSOLE`. The default posture is: the JSONL flight recorder is always
on; distributed tracing is opt-in.

## 6. Theory: observability as honesty infrastructure

Three positions:

1. **Every claim in the product is backed by a number or a record.** "It thinks when
   nobody is talking to it" is `cycles` rows and presence heartbeats; "it declined
   with a reason" is a deliberation record; "the model was down for X" is
   `MODEL_FAILURES` dispositions. The README's post-mortem tone is possible because
   the data exists to write it from.
2. **Records are typed by meaning, not just by volume.** Dispositions, purposes,
   and changed-request labels exist so a failure can be *read*, not merely counted —
   the same instinct as the guardian's refusal contract: an error is information.
3. **The observer must be trustworthy and harmless.** The audit chain is
   hash-chained and agent-untouchable (H1); the flight recorder never raises; the
   redactor assumes secrets will try to leak and waits for them.
