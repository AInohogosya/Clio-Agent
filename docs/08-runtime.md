# 08 — Runtime: processes, comms, observability

Clio Agent runs as a supervised constellation of processes that share one database and one
event bus. Sources: `clio/core/supervisor.py`, `bootstrap.py`, `runner.py`, `control.py`,
`clio/bus/event_bus.py`, `clio/platform.py`, `clio/comms/`, `clio/observability/`,
`clio/api/app.py`, `deploy/oob/oob_guardian.py`.

## Part I — The process model

## 1. The processes

`Supervisor.build_specs()` (`clio/core/supervisor.py:218-268`):

| Process | Command | Optional | Why it is separate |
|---|---|---|---|
| `clio-gateway` | `gateway` | no | model I/O and money accounting, isolated from the mind |
| `clio-toolhost` | `toolhost` | no | every side effect, behind the guardian |
| `clio-comms` | `comms` | no | all channel listening (exactly one long-poll per door) |
| `clio-workers-{1..N}` | `worker` | count via `workers.count` (default 2) | delegated goal pursuit |
| `clio-api` | `api` | built when enabled | stateless control plane ([10](10-sandboxes-steering-and-policy.md) §11) |
| `clio-core` | `core` **without** `--listen-channels` | no | the life loop; would double-poll channels otherwise |
| `clio-web` | `node web/server/index.mjs` | **yes** (only if `web/node_modules` exists) | interface bridge — "the bridge is optional for one reason only: it is not the agent" (`supervisor.py:255`) |

Ports/sockets: gateway UDS `/run/clio/gateway.sock` or TCP `127.0.0.1:8700`; toolhost
`run_dir/toolhost.sock` or `8701` (`clio/config.py:211-226` — and `8701` not `8710`,
because `8710` is the web WS port and on a socket-less platform both adapters are live);
web HTTP 8720, WS 8710 (`config.py:1315-1316`); webhook receiver `127.0.0.1:8730`
(`config.py:1354`); CLI socket `~/.ethos/run/cli.sock`; core metrics **9710**
(`runner.py:503`); Postgres default
`postgresql://ethos:ethos@127.0.0.1:5433/ethos`; `run_dir = /run/ethos` if writable else
`~/.ethos/run`.

## 2. The supervisor

`clio/core/supervisor.py:161-410`:

- **Dual-tier liveness** (`:56-95`): a fast window (`CLIO_FAST_HEARTBEAT_S`, 15 s) kept
  fresh by a background heartbeat task — so staleness means a *stopped process*, not a slow
  model call — and a 1800-second hard ceiling. A wedged child is recycled and **its
  activity leases are transferred with the fence bumped** (`_transfer_fenced_leases`,
  `:334-358`) so a zombie's late write is refused at the row
  ([10](10-sandboxes-steering-and-policy.md) §9).
- Required children restart **forever** with `backoff_seconds`; restart counts reset after
  60 s of stable life; "there is no give-up path for a required child, deliberately"
  (`:360-375`). The optional bridge is never restarted.
- The heartbeat baseline is touched at child start so a fresh core is not judged by the
  previous core's stale file (`:306-325`).
- Shutdown: reverse-order terminate → grace → kill. `clio stop` sends SIGTERM on POSIX and
  waits 20 s; on Windows it writes a **stop-request file** (CPython's `os.kill` on Windows
  is an unhandleable `TerminateProcess`), then escalates
  (`clio/__main__.py:1015-1105`, `clio/platform.py`).

## 3. Bootstrap and doctor

`bring_up()` (`clio/core/bootstrap.py:440+`), idempotent, once in Python (the 330-line
bash `start.sh` was retired to a shim):

```
ensure Postgres → clio db upgrade → clio db seed (non-fatal)
  → install/build interface → clio doctor
  → detached supervisor (start_new_session)
  → clio await-agent → clio status → open browser
```

- Exit codes `0 / 1 / 130`; 130 = Ctrl-C, and the agent **keeps running** — it was detached
  on purpose.
- **Liveness is a sign of life, not a pid**: `clio status` and `clio await-agent` wait for
  the agent to have actually *answered for itself* within `AGENT_ABSENT_S = 900`
  (`clio/core/status.py:24-35`), matching the interface's `AGENT_ABSENT_MS` — "a supervisor
  whose children have all died is still a live process, and a bridge with the agent down
  serves the agent's last known state perfectly" (`clio/__main__.py:682-738`).
- `clio doctor` (`__main__.py:1106-1323`) probes reality before declaring health: config,
  database, **pgvector presence**, alembic head comparison, **real model requests**
  ("a present key is not a working one"; `--no-probe-models` degrades *honestly* by saying
  the probe was not run, `:1217-1238`), and a **filesystem reach probe** that writes and
  deletes a real file rather than consulting permission bits — "the mode bits say `ok` for
  exactly the directories that are refused" (`:1268-1323`; macOS TCC).
- RLS policy presence is doctor-readable (`clio/db/row_security.py:112-119`).

## 4. The event bus

`EventBus` (`clio/bus/event_bus.py:61-260`) — a transactional Postgres LISTEN/NOTIFY bus:

- `publish()` INSERTs into `events` and issues `pg_notify` **in the same transaction**
  (`:193-215`), so NOTIFY fires only on commit. An event never reaches a listener whose row
  failed to commit.
- A dedicated listener connection dispatches to kind-specific + wildcard handlers; dedupe
  window `SEEN_ID_WINDOW = 16_384` (`:58`).
- **`drain()`** claims unconsumed events via a `consumed_by` array (`:241-260`) — startup
  gap recovery: what happened while the agent was down is not lost
  ([04](04-memory-and-self-model.md) §6).
- **Commission kinds** (`COMMISSION_KINDS`, `:35-46`) with a resident fast path
  (`publish_commission`, `:227-240`) — [09](09-version-3-2.md) §5.
- Event kinds (`clio/schemas/events.py:10-46`): `percept, msg.inbound, msg.outbound,
  timer.fired, thread.request, thread.result, control.update, guardian.undo, thought.new,
  intention.update, presence.update, message.new, message.handled, action.update,
  budget.update, guardian.update, schedule.set, comms.send, consolidate.done,
  verify.request, wake.nudge, steering.inject, commission.*`; trust levels
  `internal | known | untrusted`.
- Core wiring (`clio/core/runner.py:301-310`): `control.update` and `guardian.undo` are
  subscribed **separately** from the percept chain — oversight commands are applied even if
  the percept path is wedged [H1].

## 5. The control plane

`clio/core/control.py:16-200`:

- `CONTROL_KEY "lifecycle"`, rechecked every `ROW_RECHECK_S = 30` s (`:16-27`) — "the bus
  event is the fast path and this is the backstop: … a stop that cannot arrive and, worse, a
  resume that cannot, which is a pause that outlives the reason for it."
- Two-way sync between the `env_entities` row and the `stop`/`paused`/`emergency` files
  (`:102-111`); "files + bus state are truth [H1]".
- `owns_row` semantics: the toolhost passes `owns_row=False` — "without it every restart of
  *this* process would rewrite the row with its own `stopped: false`"
  (`clio/__main__.py:291`).
- `stopped` is a record of how the last run ended, not an order; the durable kill is the
  stop file, re-read every cycle (`control.py:135-160`).
- `preview` is inherited across restarts, unlike `stopped` — the reasoning is spelled out
  at `:125-134`.
- SIGUSR1/SIGUSR2 deliver immediate rechecks.

## 6. Platform abstraction

`clio/platform.py` exists because capability differences split into two kinds:

1. **Absent capabilities** (pty, Unix sockets, X11) — asked and **reported**
   (`available_capabilities`, `:497-546`; `PTY_SUPPORTED`, `:102`). "Nothing is silently
   no-op'd."
2. **Silently different conventions** — answered here, once:
   - `process_is_alive` (`:251`): on Windows `os.kill(pid, 0)` is a *kill*, not a probe;
     the Windows path uses `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`.
   - `restrict_to_user` (`:439`): Windows `icacls` grants are **read back and verified**,
     because `chmod 0o600` semantics do not exist there and any mode-bit check "is
     therefore vacuously true on Windows, which is the worst kind of wrong in a security
     check."
   - `detached_popen_kwargs` (`:194`): Windows needs
     `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP` because CPython "accepts the same keyword
     on Windows and *ignores* it" — the silently-dies-with-terminal bug.
   - `uvicorn_loop` (`:145`): uvloop pinned off on macOS + Python 3.12 (SIGSEGV in
     `uv_tcp_init_ex`).

## 7. Env and configuration loading

`clio/__main__.py:1340-1449`: env files load in order `$CLIO_ENV_FILE` → `./.env` →
checkout `.env`, **the real environment always wins**, guarded by `CLIO_ENV_LOADED=1`. The
parser is a deliberate 20-line `KEY=value` reader — "a parser that grows a syntax nobody
uses is a parser that has to be trusted." `--config-dir` is validated as an existing
directory at startup: "a named directory that is not there is a typo… every later answer
would be confidently wrong."

---

## Part II — Communications: doors, not decisions

Channels are modeled as **doors**: a door being open means the agent *can hear* through it;
speaking is a separate capability. Sources: `clio/comms/host.py`, `adapters/*`,
`webhook.py`, `jwt.py`, `clio/comms/adapters/base.py`.

## 8. The adapter contract

`ChannelAdapter` (`clio/comms/adapters/base.py:93-200`):

- **`start` = can speak, `listen` = can hear** — two separate capabilities: the agent can
  be able to answer on a door it never listens to, and "one listener per channel is one
  listener and one failure" (`:96-110`).
- **Empty allowlist admits nobody.** `accept_from_anyone` is a *separate* switch
  (`:114-156`) — the failure of an allowlist can never be silently papered over by a
  catch-all. The Telegram config comment is blunt (`clio/config.py:1321-1343`): a bot token
  is public, and "a bot with no allowlist answers whoever finds its username — which for an
  agent holding a shell is not a convenience, it is a published endpoint."
- `offer_seen` dedupe bridges restarts rather than keeping history (`:11-24`);
  per-message exception isolation in `emit_inbound` (`:58-90`); `is_transient`
  classification for retry decisions (`:122`).
- `send` refuses an id on some other channel's format — **the address is not the person**;
  `owner` is not somewhere any adapter can post to (`README.md`, sender-identity section).

## 9. The ten adapters

| Adapter | Transport | Auth | Notes |
|---|---|---|---|
| `telegram` | long-poll (`POLL_TIMEOUT_S 5.0`) | bot token | update-id dedupe so a replay cannot become a second message; empty allowlist refuses loudly; groups refused explicitly |
| `cli` | Unix socket, JSON-lines | local | no Windows support and **no TCP fallback** — "a local socket is local by construction" (`adapters/cli.py:32-55`) |
| `web` | loopback websockets | hello token (4401 on failure) | serves the browser interface |
| `email` | IMAP poll / SMTP 587 `start_tls` | credentials | thread-tag regex keeps replies threaded; SEEN UID stored with chunked flags |
| `whatsapp` | Cloud API webhook | HMAC-SHA256 on raw body | E.164 normalization; 24 h window refusals passed through as errors, not swallowed |
| `slack` | signed webhook | v0 signature, `MAX_SIGNATURE_AGE_S 300` | ignores self-echo subtypes; the `"200 with ok:false"` trap handled — HTTP success is not API success; user-name lookup cached, **refusals cached too** (`adapters/slack.py:155-273`) |
| `discord` | gateway socket | bot token, intents | heartbeat 41.25 s ±5; `MAX_MESSAGE_CHARS 2000` split on line/space; **no RESUME — messages during an outage are lost, stated in code** (`adapters/discord.py:91-110`: enabling RESUME without re-requesting history "would replay messages the agent already answered") |
| `line` | signed webhook | base64 HMAC | **no-expiry replay caveat documented in code** (`adapters/line.py:43-67`); push API, not replyToken; user ids only, groups refused |
| `teams` | Bot Framework webhook | JWT `RS256` | mention stripping; `serviceUrl` per-conversation cache with suffix allowlist; **an unsolicited Teams message right after a restart may fail** — the honest consequence is documented (`adapters/teams.py:80-120`) |
| `googlechat` | service-account webhook | JWT-bearer → access token | `scope chat.bot`; threadKey = own message id; space-scoped audience |

Push channels (WhatsApp, Slack, LINE, Teams, GoogleChat) share **one loopback webhook
port** (`channels.webhook`, port 8730), distinguished by path; a push door enabled with no
webhook is a configuration error the config validator reports (`clio/config.py:1719-1729`).

## 10. Webhook security (`clio/comms/webhook.py`)

The order of operations **is** the security:

1. **Auth before parse**: signature (HMAC over the **raw** body; Meta's
   `x-hub-signature-256` sha256, `:39-62`) is verified before any body decoding.
   Constant-time comparison.
2. **404 before body**: unknown paths are rejected before reading the body (`:188`).
3. **Challenge before signature**: provider URL-verification challenges answer before
   signature checks where the provider requires it (`:213-233`) — and a challenge reply is
   a constant, so it writes nothing.
4. **Handler failure still returns 200** to the provider (`:235-247`) — a redelivery storm
   is worse than one lost event, and dedupe is upstream anyway.
5. `MAX_HEADERS 64`, max body 262 144 bytes, receiver loopback-only ("Never `0.0.0.0`",
   `:64-100`).
6. **A route with no secret is a refusal, not a pass** (`:89-100`) — an unconfigured door
   admits nobody (503, not 200).

## 11. JWT verification (`clio/comms/jwt.py`)

For Teams and Google Chat — the two doors whose inbound proof is a bearer token:

- Algorithm **pinned to `RS256`** (`:59-65`): "read from the header instead of pinned here,
  an attacker picks the algorithm."
- Parse order: **shape → signature → audience → time → identity** (`:370-430`) — a verifier
  that checked the audience before the signature would be answering the wrong question.
- Audience matching is exact, not substring (`:427` region).
- **`exp` absent = refusal** — "a token that never expires is a token that cannot be
  trusted."
- `KEY_TTL_S 3600`, `CLOCK_SKEW_S 60` (`:75-85`).

## 12. The comms host: doors follow configuration

`CommsHost` (`clio/comms/host.py:170-1070`):

- **`watch_channels`** (`:1000-1060`): re-reads channel config live — a stamp on the config
  files every 2 s (`WATCH_INTERVAL_S`, `:45`), a full rescan every 8 polls
  (`RESCAN_EVERY_POLLS`, `:52`), with inode-aware stamping. "A token pasted into a settings
  screen is a token this process is holding a poller for within a couple of seconds."
- **`plan()`** (`:264+`): builds the desired door set, fingerprinted by sha256, resolving
  credentials exactly once.
- **Reconciliation is close-before-open** (`:763-830`): a changed door is closed, then
  reopened with the new config; a token rotation never has two live pollers.
- **Retry curve** (`:58-90`): base 2.0, max 300.0, permanent after 60 s of failures — the
  Telegram-502 lesson: transient errors retry, dead doors are marked permanently shut and
  reported.
- **`EADDRINUSE` + not-listening** → "door held elsewhere" info log (`:849`) — the
  diagnosed state, not a crash.
- **`inbound_to_bus`**: exhaustive log entry, `social.record_inbound`, publish
  `INBOUND_MSG` with text truncated to 4000 chars, urgency seeded 0.3 (known) / 0.2
  (untrusted) before attention re-scores it.

## Part III — Observability and the control plane

## 13. The API (`clio/api/app.py`)

`clio-api` is **stateless**: lifecycle verbs (`pause`, `resume`, `stop`,
`emergency_stop`) on the out-of-band control lane (`apply_control`, `:130-140`), read
models from PostgreSQL under RLS, ClioUI components and their action endpoint
(`/api/components/action`, `:193-226`), takeover endpoints (`take_screen` / `hand_back`,
`:227-260`), and the OAuth callback (`:261-278`). Any number of replicas can serve it; the
facade delegates the two genuinely in-process operations (takeover, connector exchange) over
the bus (`_bus_round_trip`, `:58-84`). Routes: `/api/health`, `/api/status`,
`/api/control/{action}`, `/api/projects`, `/api/components/...`,
`/api/sandboxes/{id}/control/take|hand-back`, `/api/oauth/...`.

The interface bridge serves the build with a strict document policy
(`connect-src 'self'`, `script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`);
the page never holds a credential and is not permitted to reach a provider itself.

## 14. Metrics

`clio/observability/metrics.py` (Prometheus, core on port **9710**, `runner.py:503`;
`start_metrics_server` never raises):

- **`MODEL_CALLS`** labelled by purpose — the plan-vs-step distinction exists because "one
  number" for model calls hides whether spend went to planning or execution (`:9-25`).
- **`MODEL_FAILURES`** plus **`MODEL_FAILURE_DISPOSITIONS`** (`:27-45`) — failures typed by
  what they mean (rate limit, unusable, model-scoped, …), matching the gateway's
  failure-scope routing.
- **`MODEL_RETRIES`** with a `changed_request` label (`:46-65`) — a retry that widened the
  request is counted separately from a blind retry.
- **`MODEL_CALL_COST`** histogram; free-vs-unused calls observe `0.0` explicitly (`:67`)
  rather than being absent — "zero spend" and "no data" are different facts.

## 15. The audit chain

`clio/observability/audit.py` (full treatment in [02](02-safety-and-guardian.md) §9):
hash-chained entries, genesis = 32 zero bytes (`:17`), Postgres sink under advisory lock
`918_273_645` (`:18,58-96`), file sink fsync (`:98-136`), `verify_chain` re-walks up to
1 000 entries (`:185-196`). The core **anchors** the head every 600 s — publishes
`audit.anchor` and writes `anchor.json` atomically (`clio/core/runner.py:431-445`) — and
the OOB guardian ingests it into a log the agent cannot write.

## 16. Exhaustive logging

`clio/observability/exhaustive.py`: JSONL files under the agent's data dir, enabled by
default (`CLIO_EXHAUSTIVE_LOG`). Kinds: `user.action` (everything a person did to the
agent), `settings.change`, `model.request` (every call, verbatim), `model.response`
(including reasoning-token accounting), `model.failure`. `record` **never raises** (`:67`)
— the flight recorder must not itself become the failure.

## 17. Secret redaction

`clio/observability/logger.py`:

- `_SECRET_PATTERNS` (`:9-30`) covers Telegram bot tokens, JWTs, generic
  `key/token/password/secret = value` assignments, and `{{secret:KEY}}` patterns.
- A `RedactingFilter` is installed on **root handlers** — not on the `httpx` logger,
  because "a logger's own filters run only for records logged *to that logger*" — and
  redacts `httpx.URL` *objects* (query strings carry keys) by replacing the argument with
  redacted text when a secret was found (`:59-92`).
- `format_exc_info` runs **before** redaction so tracebacks are covered
  (`:142-143`).
- Secrets at rest are pyrage-encrypted via the `secrets.*` tools
  ([07](07-gateway-and-toolhost.md) §8).

## 18. Tracing

`clio/observability/tracing.py`: optional OpenTelemetry, enabled via `CLIO_TRACE_CONSOLE`.
Default posture: the JSONL flight recorder is always on; distributed tracing is opt-in.

## 19. The out-of-band guardian (`deploy/oob/oob_guardian.py`)

Stdlib-only, on purpose ("a watchdog that needs the packages it is watching for is a
watchdog that goes down with them"). Runs on the **hypervisor host**, outside the agent's
trust boundary. Responsibilities:

1. **Spend watchdog (H2)**: polls the agent's metrics; sustained or monthly overspend
   freezes sandboxes and revokes the gateway's provider tokens out of band, then optionally
   suspends the VM (`CLIO_SUSPEND_CMD` — **without it, it logs and continues**).
2. **Liveness watchdog (H1)**: heartbeat silence past the hard ceiling → freeze sandboxes.
3. **Sandbox freeze**: `docker/podman pause` — "freezing is the verb, not killing: the state
   stays for forensics."
4. **Audit anchoring**: ingests `anchor.json` into a host-side append-only log and flags
   chain regressions.
5. **Kill switch**: `${OOB_DIR}/stop` freezes everything and suspends the guest.
6. **Host snapshots** on a schedule via a configured template.

It is not started by `clio up`; running it is a deployment responsibility
([02](02-safety-and-guardian.md) §6.3).

## 20. Theory: constellation over monolith

The process split encodes separable concerns with different failure modes:

- **The mind** (core) can restart without losing memory (Postgres) or in-flight tool
  effects (the toolhost owns those).
- **Money and side effects** are each isolated behind their own process boundary, so a
  wedged or compromised mind still funnels through the gate and the spend guard.
- **One listener per channel** is a structural guarantee (comms owns all doors), not a
  discipline.
- **Supervision is forever and skeptical**: restarts are unbounded, wedges are detected by
  heartbeat rather than by exception, and "running" is defined by signs of life — the
  process model refuses to confuse *processes exist* with *the agent is alive*.
- **Deny by default, at every layer**: an empty allowlist admits nobody; a secretless
  webhook route admits nobody; an `exp`-less JWT refuses. There is no configuration state in
  which "unset" means "everyone."

## 21. What this document does not claim

- **Discord loses messages during outages** (no RESUME) and **LINE accepts replays without
  expiry** — both stated in the adapters themselves (§9); the dedupe window narrows but does
  not close either gap.
- **`clio safety set` does not publish `CONTROL_UPDATE`** and **`clio safety show` ignores
  the live override** ([02](02-safety-and-guardian.md) §6.1) — the CLI surface lags the
  runtime.
- **The webhook receiver is loopback-only**: reaching it from outside requires a reverse
  proxy the deployment provides; nothing in this checkout terminates TLS.
- **The digest queue is in-memory** ([05](05-social.md) §12): a comms restart drops queued
  digests.
- **Metrics are per-process**: only the core opens 9710; the gateway and toolhost expose
  their counts through the database and the audit/exhaustive logs, not a second scraper.
