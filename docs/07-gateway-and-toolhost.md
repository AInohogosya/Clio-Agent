# The gateway: routing, providers, money

All model calls go through one service. Sources: `clio/gateway/service.py`,
`router.py`, `normalizer.py`, `pricing.py`, `cost.py`, `embeddings.py`,
`calibration.py`, `config.py:101-195`.

## 1. Routing

`ModelRouter.select()` (`clio/gateway/router.py:207-319`) scores every eligible
model:

```
m* = argmax [ q_m(class) − μ·ĉ − ν·l̂ ]        (router.py:125-126,193-205)
```

- `q_m(class)` — the configured **quality** of model m for the requested class, with
  a **tier floor** (`T1 0.50, T2 0.70, T3 0.90`, `config.py:1038-1042`); a model
  under the floor scores `−inf` and is out.
- `μ = 0.002` — cost penalty per dollar (weak: quality first).
- `ν = 0.01` — latency penalty per second (weak: this is a resident, not a chatbot).
- Filters: tier, provider/model hints, `diversity_family`, `supports_tools`,
  `supports_thinking`, availability.

### 1.1 Pinning and exemptions

- The **pinned base model** (`config/base_model.yaml`: `openai_compat`,
  `z-ai/glm-5.3`, `https://integrate.api.nvidia.com/v1`, `diversity_family
  "base:<provider>"`) is returned first unless it is `failing_hard()` — ≥ 3
  consecutive failures (`PIN_STREAK_LIMIT = 3`, `router.py:54,176-187`).
- If **nothing** clears the tier floor, the pinned model is returned anyway as an
  exemption (`router.py:310-318`): a weak answer beats a hard failure for a resident,
  and the tier floor's real enforcement is upstream in `devise`/`verify` choices.
- `NoModelAvailable(transient=…)` distinguishes *permanently ineligible* from
  *all-in-cooldown* (`router.py:267-271`) — the caller can wait out the latter and
  must re-plan around the former.

### 1.2 Session drivers (3.2)

A `SessionDriver` (`clio/gateway/providers/base.py`) participates in routing with
`session_score` carrying `c_hat ≈ 0` (`clio/gateway/router.py` `is_session_entry`,
`marginal_cost`) — a subscription-based CLI/session has no marginal token cost, so it
outranks paid models at equal quality. On 429 or breaker failover, the gateway
**replays the normalized conversation** on the next driver (`service.py:
register_session_driver, replay_request, _session_replay`).

## 2. Resilience

- **Circuit breaker** (`router.py:78-112`, `config.py:106-111`): window 60 s,
  opens after 5 failures (or error rate ≥ 0.30 over ≥ 20 calls), half-open probe
  after 30 s cooldown. Terminal bench failure benches a model for 3600 s
  (`router.py:64,164-174`).
- **Retry policy** (`config.py:114-160`): `max_retries 20`, backoff 1.5 s,
  max 300 s, jitter 0.3, fail-over across models enabled.
- **Rate-limit waits**: `WAIT_SCHEDULE = (300, 3600, 18000, 86400)` — 5 min, 1 h,
  5 h, 1 d — capped at 86400 s (`service.py:82-132`). The same schedule is shared by
  deliberation, the planner, and the worker: **one calendar for waiting everywhere**.
- **Request widening** (`service.py:577-631`): on unusable output (empty,
  thinking-only, truncated → `UnusableResponse`, `service.py:435-482`), the retry
  halves the reasoning budget then grows `max_tokens ×1.5`, capped at 65536.
- **Failure scopes** (`service.py:162-178`): `_MODEL_SCOPED_STATUSES = {401,403,410}`
  (key/model-level: fail the model over) vs `_REQUEST_SCOPED_STATUSES =
  {400,413,422}` (don't blame the model for a bad request).
- **`post_json` hard ceiling: 600 s** (`providers/base.py:107-127`), with Retry-After
  parsing.

## 3. Providers and normalization

Providers (`clio/gateway/providers/`): `anthropic_native`, `openai_native`,
`google_native`, `openai_compat` (which learns per-model traits into
`learned_model_traits.json`, `service.py:388`, consumed by `calibration.py` —
`RELOAD 30 s`, `PENALTY_WEIGHT 0.5` — so a model that keeps producing malformed JSON
gets progressively routed away from schema-sensitive calls).

The **normalizer** (`clio/gateway/normalizer.py`) exists because providers
disagree about usage accounting:

- **Disjoint (Anthropic)** vs **inclusive (OpenAI/Google/compat)** token shapes
  (`normalizer.py:22-94`). The old bug: cached tokens were billed twice because the
  conventions were mixed.
- Provider-reported `usage.cost` wins when present (`normalizer.py:97-117`).
- Converters to/from `anthropic / openai_responses / google / openai_compat`
  wire shapes (`normalizer.py:241-723`).
- `estimate_tokens = max(64, chars // 4)` when the provider says nothing
  (`normalizer.py:778-781`).

## 4. Honest pricing

`clio/pricing.py:460` — the docstring is a post-mortem of three price bugs, all of
which **biased spend upward**:

1. Flat `dict.get` defaults billed unknown models at an invented mid-range rate —
   "the majority of every day's spend was a guess wearing a dollar sign." Now:
   **live → checked-in table → pessimistic high-water mark, logged as an estimate,
   not passed off as fact** (`PriceTable`, `pricing.py:188-295`).
   The high-water mark is `max(known non-free prices) × 1.5`
   (`_derive_high_water_mark`, `pricing.py:216-236`).
2. Provider token conventions are unified **in the adapter** (§3).
3. Anthropic cache writes were priced at nothing — **"free stays free"**
   (`pricing.py:24`) cuts both ways: an absent cache rate defaults to the **full
   input price** (`ModelPrice.from_dict`, `pricing.py:132-147`), conservative
   rather than optimistic.

Free routes (`:free` suffixes, `local-*`, local base URLs) resolve to `$0`
(`_is_free_route`, `pricing.py:309-323`). Live prices come only from the OpenRouter
catalogue (`LivePriceFetcher`, 6 h TTL, 30-day cache max, atomic write,
`pricing.py:325-389`).

## 5. The spend guard (H2)

`SpendGuard` (`clio/gateway/cost.py:213-351`) enforces the numeric half of H2:

- Caps: `H2_daily_spend_usd = 20.0`, `H2_monthly_spend_usd = 400.0`,
  `H2_capital_at_risk_usd = 100.0` (`config.py:1172-1185`), `on_exceed="block"`.
- **Bucket split**: `commitment_reserve_pct = 0.50` (`config.py:163-164`) — half of
  each period's budget is reserved for commissioned work, so discretionary
  ventures cannot starve owed commitments.
- Only `status='ok'` model-call rows count toward spend (`cost.py:55-59`) — failed
  calls are not billed and not counted.
- Every call is recorded into `model_calls` with token buckets, `cost_usd`,
  `price_source`, latency, status (`cost.py:324-337`).
- The **cheapest-cost spend preflight** runs before dispatch (`service.py:863+`),
  so a call that would breach a cap is blocked before it is made.

## 6. Embeddings

`clio/gateway/embeddings.py:302`:

- `HashingEmbedder(dim=1024)` (`:39-66`) — zero-dependency fallback; the agent's
  memory never depends on a model being reachable.
- `FastembedEmbedder` (`:112-161`) — default `BAAI/bge-large-en-v1.5`
  (`config.py:167-195`), the real embedder for retrieval.
- `RemoteEmbedder` for roles `toolhost` and `worker` (`:30,164-247`) — those
  processes **borrow the gateway's embedder rather than owning an ONNX arena**:
  "a second ~1.2 GB bge-large ONNX arena" is a defect, not redundancy
  (`clio/__main__.py:304-310`).

## 7. Theory: money as a first-class dimension

The gateway's design position: an agent with a wallet needs **honest accounting**
before it needs clever routing. Hence the ordering of concerns:

1. **Know what you spent** (normalizer + pricing post-mortems) — most systems fail
   here first, quietly.
2. **Refuse what you can't afford** (SpendGuard, preflight, reserve buckets).
3. **Only then optimize** (routing score, breakers, session drivers).

The routing equation itself encodes the golden rule's economics: quality floors are
absolute (a T3 verification never routes to a weak model even to save money), while
cost and latency penalties are deliberately small (`μ=0.002, ν=0.01`) — the agent is
allowed to be expensive when being cheap would be wrong.
## Part II — The toolhost: the agent's hands

Every action the agent takes on the world passes through the toolhost
(`clio/toolhost/server.py`), a separate supervised process behind a guardian gate.
Sources: `server.py`, `clio/toolhost/tools/*`, `kernels.py`, `browser.py`,
`desktop.py`, `watchers.py`, `pty_session.py`.

## 1. Execution pipeline

`Toolhost.execute()` (`server.py:74-483`):

1. **Journal `begin`** (write-ahead, before any side effect).
2. **`gate.review`** — the guardian classifies and verdicts the call
   ([02-safety-and-guardian.md](02-safety-and-guardian.md)); a rejection short-circuits to
   `GUARDIAN_REJECT`.
3. **Run** the tool under a per-tool timeout (default 90 s).
4. **Journal `end`** with the outcome status.
5. Return a `ToolResult`.

On **duplicate idempotency key**, the stored prior result is **replayed** rather
than re-executed (`server.py:322-366`) — the same action key never runs twice.

Transport: Unix socket `run_dir/toolhost.sock` (POSIX) or TCP `127.0.0.1:8701`
with fallback decided by capability, not convention (`clio/__main__.py:325-360` —
"the missing `AF_UNIX`", not writability, is the question). HTTP surface:
`GET /healthz`, `GET /tools`, `POST /execute` (`server.py` `create_toolhost_app`).
Windows is always TCP (`clio/toolhost/client.py:41-46`).

## 2. The 55 tools

Registered at `server.py:186-210`:

| Family | Tools |
|---|---|
| `shell.*` (6) | `shell.run`, `shell.job`, `shell.session_open`, `shell.session_write`, `shell.session_read`, `shell.session_close` |
| `fs.*` (8) | `fs.read`, `fs.write`, `fs.edit`, `fs.move`, `fs.delete`, `fs.list`, `fs.stat`, `fs.mkdir` |
| `code.*` (2) | `code.exec`, `code.reset` |
| `http` | `http.request` |
| `comm` | `comm.send` |
| `memory.*` (5) | `memory.record`, `memory.search`, `memory.remember_fact`, `memory.open_question`, `memory.random` |
| `history` | `history.browse` |
| `intent.*` (5) | `intent.create`, `intent.get`, `intent.list`, `intent.update`, `intent.close` |
| `thread` | `thread.spawn` |
| `schedule.*` (3) | `schedule.at`, `schedule.every`, `schedule.cancel` |
| `tools.*` (3) | `tools.register`, `tools.run`, `tools.list` — the toolshed |
| `secrets.*` (3) | `secrets.set`, `secrets.get`, `secrets.list` |
| `browser.*` (6) | `browser.visit`, `browser.click`, `browser.type`, `browser.extract`, `browser.screenshot`, `browser.close` |
| `desktop.*` (6) | `desktop.key`, `desktop.type`, `desktop.click`, `desktop.move`, `desktop.screenshot`, `desktop.windows` |
| `subagent.*` (4) | `subagent.status`, `subagent.foundry`, `subagent.apex`, `subagent.axiom` |

Tool-level dispositions `STATUS_DISPOSITIONS` (`server.py:39`) — `ENVIRONMENT`,
`TIMEOUT`, etc. — are how a tool tells the agent *why* something failed, so the
error becomes information rather than noise ("the error is the finding").

## 3. Notable tool semantics

- **`fs.edit` requires exactly one match** for its search text (`tools/fs.py:309`);
  ambiguity is an error, not a guess.
- **`fs.write` backs up the preimage** (sha256-verified) before writing
  (`tools/fs.py:60`); `fs.read` caps at `MAX_READ_BYTES = 2M`.
- **`fs.delete` always goes to trash** (`tools/fs.py:403`) — the reversibility
  thesis at the single most destructive call.
- **`shell.run`**: output capped at `MAX_OUTPUT = 200k` chars, 300 s timeout, `&&`
  as the only chaining operator (`tools/shell.py:115`) — simple chains are
  parseable by the guardian classifier; the exotic forms are what a hostile payload
  would use.
- **`code.exec`**: persistent Python kernels (`kernels.py`, `EXEC_TIMEOUT_S = 60`
  in tools / 120 s per code.py, `MAX_OUTPUT_CHARS = 60000`), keyed by session
  (default the calling thread's id, else `"self"`); `code.reset` (15 s) clears.
- **`http.request`** denies the 8 private IP ranges (`tools/http.py`) — SSRF is
  handled at the tool, not by prompt discipline.
- **`history.browse`** (`tools/history.py`): max look-back 24×366 hours, default
  24 h / 50 rows, `MAX_LIMIT 200`, literal `ILIKE` matching, `hasMore` computed via
  limit+1, text clipped to 4000 chars — the agent can audit its own past.
- **`secrets.*`** persist via pyrage-encrypted storage with `{{secret:KEY}}`
  interpolation (`clio/gateway/secrets.py`) and the logger redacts `{{secret:}}`
  patterns ([08-runtime.md](08-runtime.md)).
- **`comm.send`** is restricted to the `SEND_CHANNELS` list (`tools/comms.py:78`)
  and routes through the outbound pipeline — tools cannot bypass etiquette.

## 4. Browser and desktop

- **Browser** (`browser.py:381`): a Chromium pool, `MAX_SESSIONS = 4`, idle TTL
  300 s, visit timeout 45 s. Session-aware refusals: operating on a closed page
  returns an `ENVIRONMENT` disposition that names the open pages
  (`tools/browser_tools.py:14-45`) — the error tells the agent what *is* open.
- **Desktop** (`desktop.py:91`, Xvfb `:1` on Linux): `desktop.key / type / click /
  move / screenshot / windows` with timeouts 30–60 s. On macOS this drives the real
  desktop; capabilities are reported by `clio doctor` (`clio/platform.py`
  `_desktop_possible`).
- **PTY sessions** (`pty_session.py:278`): lazily imported, POSIX-only via
  `platform.PTY_SUPPORTED` — an absent pty is reported, not silently no-op'd.

## 5. The toolshed: self-written tools

`tools.register / tools.run / tools.list` (`tools/toolshed.py:223`) let the agent
**write its own tools** and have them persist — the README's example: a shell
incantation typed eleven times becomes a capability.

A toolshed manifest carries:

- `allowed_paths, allowed_hosts, secret_scopes, timeout_s` (default 60 s for the
  manifest; `tools.run` itself 120 s)
- `content_hash` (SHA-256) — the registered source is pinned
- an **Ed25519 signature**, verified at registration (`toolshed.py:22`)

Enforcement is **pre-execution**: `GuardianGate.review()` checks the manifest's
path/host/secret fences (`clio/guardian/classifier.py:check_manifest_paths`)
before the tool runs — a registered tool cannot exceed what its signature vouched
for.

## 6. Watchers

`clio/toolhost/watchers.py:175` — background file/process watchers the agent can
arm, feeding percepts; the toolhost observes so the agent doesn't have to poll.

## 7. Theory: hands vs mind

The toolhost exists because **the mind must not touch the world directly**:

1. **One funnel.** Every side effect crosses one boundary, so one journal, one
   classifier, one audit chain covers everything. There is no side door: `comm.send`
   still obeys etiquette, `http.request` still refuses private ranges, and
   `subagent.*` tools classify as execute+write in the guardian
   (`classifier.py:623-676`).
2. **Failures as information.** Dispositions (`ENVIRONMENT`, `TIMEOUT`, refused)
   are typed so the agent can route around them — including refusing *and then
   naming the nearest permitted thing*.
3. **Capability honesty.** The tool catalog is the honest boundary of "can you…" —
   rendered into the cached prompt prefix so the agent's self-knowledge of its hands
   is complete and cheap ([04-memory-and-self-model.md](04-memory-and-self-model.md) §2).
