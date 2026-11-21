# 07 — The gateway and the toolhost

All model calls go through one service; every action on the world passes through one
toolhost. Sources: `clio/gateway/`, `clio/pricing.py`, `clio/toolhost/`,
`clio/tools/catalog.py`.

## Part I — The gateway: routing, providers, money

## 1. Routing

`ModelRouter.select()` (`clio/gateway/router.py:136-368`) scores every eligible model:

```
m* = argmax [ q_m(class) − μ·ĉ − ν·l̂ ]        (router.py:137, :239-255)
```

- `q_m` — the model's quality **as adjusted by the nightly calibration**, not the YAML
  opinion (`quality_of`, `router.py:231-236`; `clio/gateway/calibration.py:57-76`:
  `effective = base · (1 − 0.5 · error_rate)`), with a **tier floor**
  (`T1 0.50, T2 0.70, T3 0.90`, `clio/config.py:1073-1079`); a model under the floor is out.
- `μ = 0.002` — cost penalty per dollar (weak: quality first).
- `ν = 0.01` — latency penalty per second (weak: this is a resident, not a chatbot).
- Filters: tier, provider/model hints, `diversity_family`, `supports_tools`,
  `supports_thinking`, availability.

### 1.1 Pinning and exemptions

- The **pinned base model** (`config/base_model.yaml`) is returned first unless it is
  `failing_hard()` — ≥ 3 consecutive failures (`PIN_STREAK_LIMIT = 3`,
  `router.py:55,199-210`).
- **The pinned model is exempt from tier floors by design** (`router.py:289-294`,
  `:361-368`): the floors in `models.yaml` are opinions about *that* catalogue and must not
  quietly exclude the one model this installation actually has, sending the agent to a model
  with no key. A weak answer beats a hard failure for a resident; the floor's real
  enforcement is upstream in the devise/verify choices.
- `NoModelAvailable` distinguishes *permanently ineligible* from *all-in-cooldown*
  (`router.py:13-31`) — the caller can wait out the latter and must re-plan around the
  former.
- A model benched for terminal failure sits out 3600 s (`router.py`).

### 1.2 Session drivers (3.2)

A `SessionDriver` (`clio/gateway/providers/base.py`) participates in routing with
`session_score` carrying `c_hat ≈ 0` — a subscription-based CLI/session has no marginal
token cost, so at equal quality it outranks paid APIs (`router.py:137-148` treats a session
entry as such). On 429 or breaker failover, the gateway **replays the normalized
conversation** on the next driver (`register_session_driver` / `replay_request` /
`_session_replay`, `clio/gateway/service.py:574-630`) — failover preserves context.

## 2. Resilience

- **Circuit breaker** (`router.py`; `config/gateway.circuit_breaker`): window 60 s, opens
  after 5 failures (or error rate ≥ 0.30 over ≥ 20 calls), half-open probe after 30 s
  cooldown.
- **Retry policy** (`config/gateway.retry`): max 5 retries, backoff 1.5 s, max 30 s,
  jitter 0.3, fail-over across models enabled. (The class `RetryConfig` at
  `clio/config.py:115-163` documents why each knob exists; a retry may move to a different
  model only when something else could answer.)
- **Rate-limit waits**: `RATE_LIMIT_WAIT_SCHEDULE_S = (300, 3600, 18000, 86400)` — 5 min,
  1 h, 5 h, 1 d (`service.py:93-126`). The same schedule is shared by deliberation, the
  planner and the worker: **one calendar for waiting everywhere**.
- **Request widening** (`clio/gateway/service.py`, the unusable-output retry path): on
  unusable output (empty, thinking-only, truncated → `UnusableResponse`, `service.py:52+`),
  the retry halves the reasoning budget then grows `max_tokens`, capped — and re-asks with
  the contract restated, because "more of the ceiling cannot fix an answer of the wrong
  shape."
- **Failure scopes** (`service.py:157-175`): `_MODEL_SCOPED` `{401,403,410}` (key/model
  level: fail the model over) vs `_REQUEST_SCOPED` `{400,413,422}` (don't blame the model
  for a bad request). `UnusableResponse` knows its own scope.
- **`post_json` hard ceiling: 600 s** (`clio/gateway/providers/base.py`), with Retry-After
  parsing.

## 3. Providers and normalization

Providers (`clio/gateway/providers/`): `anthropic_native`, `openai_native`,
`google_native`, `openai_compat`.

The **normalizer** (`clio/gateway/normalizer.py`) exists because providers disagree about
usage accounting (`:33-40`):

- **Disjoint (Anthropic)** vs **inclusive (OpenAI/Google/compat)** token shapes
  (`usage_from_disjoint`, `:66-80`; `usage_from_inclusive_prompt`, `:83-93`). The old bug:
  cached tokens were billed twice because the conventions were mixed — "handing two of
  those shapes to one `input×rate + cached×cached_rate` formula bills the cached tokens
  twice."
- Provider-reported `usage.cost` wins when present (`:106+`; OpenRouter returns an exact
  per-request cost, recorded verbatim).
- Converters to/from the four wire shapes; `estimate_tokens = max(64, chars // 4)` when the
  provider says nothing.

## 4. Honest pricing

`clio/pricing.py` + `config/pricing.yaml`. The module docstring is a post-mortem of three
price bugs, all of which **biased spend upward**:

1. Flat `dict.get` defaults billed unknown models at an invented mid-range rate — "the
   majority of every day's spend was a guess wearing a dollar sign." Now the resolution
   order is **live price → checked-in table → pessimistic high-water mark, logged as an
   estimate** (`PriceTable.resolve`, `pricing.py:250-290`). The high-water mark is derived
   from the dearest known non-free price with a safety factor (`_derive_high_water_mark`,
   `:216-240`) — never applied to a model nobody looked up without being logged.
2. Provider token conventions are unified **in the adapter** (§3).
3. Anthropic cache writes were priced at nothing — "free stays free" cuts both ways: an
   absent cache rate defaults to the **full input price** (`ModelPrice.from_dict`,
   `:132-147`), conservative rather than optimistic.

Free routes (`:free` suffixes, `local-*`, configured `free_base_urls`) resolve to `$0`
(`:24-26`, `:258-262`). Live prices come only from the OpenRouter catalogue
(`LivePriceFetcher`, 6 h TTL, 30-day cache max, atomic write). Migration `0004` repriced
historical `model_calls` rows from their stored token counts. `tests/unit/test_pricing.py`
holds the dashboard and the spend guard to one shared definition of spend.

## 5. The spend guard (H2)

`SpendGuard` (`clio/gateway/cost.py:298-490`):

- Windows per §5 of [02](02-safety-and-guardian.md): six individually-enableable caps,
  class defaults disabled, deployment enables daily $20 / monthly $400, `on_exceed: block`.
- **Bucket split**: `commitment_reserve_pct = 0.50` — half of each period's budget is
  reserved for commissioned work; discretionary ventures cannot starve owed commitments
  (`cost.py:320-326`, `:429-455`).
- Only `status='ok'` rows count; failed calls are neither billed nor counted.
- Every call is recorded into `model_calls` with token buckets, `cost_usd`,
  `price_source`, latency, status (`record`, `cost.py:472-486`).
- The **preflight** runs before dispatch (`service.py:1017-1052`), so a call that would
  breach a cap is blocked before it is made.
- `capital_at_risk()` (`cost.py:487+`) reads the `value_ledger` — the aggregate view the
  per-action gate check does not attempt.

## 6. Embeddings

`clio/gateway/embeddings.py`:

- `HashingEmbedder(dim=1024)` (`:39-66`) — zero-dependency fallback; the agent's memory
  never depends on a model being reachable.
- `FastembedEmbedder` (`:112-160`) — default `BAAI/bge-large-en-v1.5`
  (`clio/config.py:168-203`), the real embedder for retrieval, ~1.2 GB of ONNX weights.
- `RemoteEmbedder` for roles `toolhost` and `worker` (`:164-250`) — those processes
  **borrow the gateway's embedder rather than owning an ONNX arena**: "a second ~1.2 GB
  bge-large ONNX arena" is a defect, not redundancy (`clio/__main__.py:304-312`).
- `assert_model_dim` (`:87-107`) refuses a model whose dimension does not match the
  `vector(1024)` columns — with the honest note that the smallest such model (384 dims)
  would also need a migration and a re-embedding of everything already stored.

## 7. Calibration and learned traits

`clio/gateway/calibration.py`: the nightly calibration pass measures real model behaviour;
`effective_quality = base · (1 − 0.5 · error_rate)` (`:57-76`). The file reloads every
30 s (`:18`) and a missing file means "no record", not zero. `openai_compat` also learns
per-model traits (`learned_model_traits.json`, consumed here) — a model that keeps
producing malformed JSON gets progressively routed away from schema-sensitive calls.

## 8. Secrets

`clio/gateway/secrets.py`: `secrets.*` tools persist via **pyrage**-encrypted storage with
`{{secret:KEY}}` interpolation at use time; the logger redacts the same notation
([08](08-runtime.md) §10). Secret-domain policy (`permissions.secrets.allowed_domains`)
constrains which domains a secret may be sent to; **credential misuse is not a gate
rejection** — substitution is enforced when an HTTP tool actually runs
([02](02-safety-and-guardian.md) §11).

---

## Part II — The toolhost: the agent's hands

## 9. Execution pipeline

`Toolhost.execute()` (`clio/toolhost/server.py:241-420`):

1. **Journal `begin`** (write-ahead, before any side effect; `:261-278`).
2. **`gate.review`** — the guardian classifies and verdicts the call
   ([02](02-safety-and-guardian.md) §3); a rejection short-circuits to `GUARDIAN_REJECT`.
3. **Run** the tool under a per-tool timeout (default 90 s, `ToolRequest.timeout_s`,
   `server.py:76`).
4. **Journal `end`** with the outcome status (`:418-422`).
5. Return a `ToolResult`.

On **duplicate idempotency key**, the stored prior result is **replayed** rather than
re-executed (`server.py:375+`): the same action key never runs twice, and only a
`status == "ok"` row is replayable ([02](02-safety-and-guardian.md) §4.1).

Transport: Unix socket `run_dir/toolhost.sock` (POSIX) or TCP `127.0.0.1:8701`, decided by
capability ("the missing `AF_UNIX`", not writability — `clio/__main__.py:325-360`). HTTP
surface: `GET /healthz`, `GET /tools`, `POST /execute`, plus `/approvals*`
(`server.py:500-548`). Windows is always TCP (`clio/toolhost/client.py`).

A successful commissioned write is auto-registered into the artifact registry
(`_auto_register_commission`, `server.py:341-360,442-456`).

## 10. The tool catalog: 56 registered tools, narrowed per step

`_register_builtin_tools` (`server.py:150-240`) registers **56 built-ins**:

| Family | Tools |
|---|---|
| `shell.*` (6) | `shell.run`, `shell.job`, `shell.session_open/write/read/close` |
| `fs.*` (8) | `fs.read`, `fs.write`, `fs.edit`, `fs.move`, `fs.delete`, `fs.list`, `fs.stat`, `fs.mkdir` |
| `code.*` (2) | `code.exec`, `code.reset` |
| `http` | `http.request` |
| `comm` | `comm.send` |
| `memory.*` (5) | `memory.record`, `memory.search`, `memory.remember_fact`, `memory.open_question`, `memory.random` |
| `history` | `history.browse` |
| `intent.*` (5) | `intent.create`, `intent.get`, `intent.list`, `intent.update`, `intent.close` |
| `thread` | `thread.spawn` |
| `schedule.*` (3) | `schedule.at`, `schedule.every`, `schedule.cancel` |
| `tools.*` (4) | `tools.search` (the pinned meta-tool), `tools.register`, `tools.run`, `tools.list` |
| `secrets.*` (3) | `secrets.set`, `secrets.get`, `secrets.list` |
| `browser.*` (6) | `browser.visit/click/type/extract/screenshot/close` |
| `desktop.*` (6) | `desktop.key/type/click/move/screenshot/windows` |
| `subagent.*` (4) | `subagent.status`, `subagent.foundry`, `subagent.apex`, `subagent.axiom` |

### 10.1 Two-stage narrowing (`clio/tools/catalog.py`)

The cached prefix's `[2. tools]` block pins one meta-tool, `tools.search`
(byte-stable, so prompt caching survives), and states the registry's size
(`build_pinned_tools_block`, `clio/prompts/identity.py:701-724`; `workspace.py:253-269`).
Each step's context carries a sheet of up to **twelve** fully-rendered tools
(`TOP_K = 12`, `catalog.py:33-36`) chosen by a lightweight router (`route`) — no model
call — against the step's own text, with the plan's own
`tool_hints` pinned too (`clio/gsl/loop.py:1900-1930`). Anything else is one `tools.search`
call away with its complete schema. MCP tools, OAuth connector tools, OpenAPI/GraphQL
imports and toolshed tools all enter the one catalog (`Toolhost.catalog`,
`server.py:121-148`; a degraded MCP server still shows, marked `degraded`).

The full-sheet renderer (`build_tool_catalog_block`, `identity.py:668-700`) exists because
the old format threw the schemas away: the agent was told `fs.edit` takes
`path, search, replace, operations, diff` and nothing about `operations` being a list of
pairs — a type error the agent had no way to avoid. Types are rendered recursively
(`_schema_type`, `:652-666`).

Tool-level dispositions `STATUS_DISPOSITIONS` (`server.py:39-56`) — `ENVIRONMENT`,
`TIMEOUT`, `limit_reached`, … — are how a tool tells the agent *why* something failed, so
the error becomes information rather than noise.

## 11. Notable tool semantics

- **`fs.write` / `fs.edit` back up the preimage** of an existing file to
  `paths.data_dir/backups/` before writing and return it as `undo_hint`
  (`clio/toolhost/tools/fs.py:60-68`, `:214`, `:289`); a new file has nothing to back up.
  The backup tree is unmanaged — no pruning, no registry, no auto-restore
  ([02](02-safety-and-guardian.md) §4.4).
- **`fs.edit` requires exactly one match** for its search text unless `replace_all`
  (`fs.py:315+`): `str.replace(..., 1)` takes the first match and says nothing, "which is
  the whole of the hazard here."
- **`fs.write` refuses to clobber silently**: an overwrite that changes content without
  `overwrite` returns a diff preview and `clobbered` flag (`fs.py:245-247`); `fs.read` caps
  at `MAX_READ_BYTES = 2M` (`fs.py:16`) and answers "this is a directory; use fs.list"
  instead of a bare exception (`fs.py:86-100`).
- **`fs.delete` always goes to trash** (`fs.py:416-424`) — the reversibility thesis at the
  single most destructive call.
- **`shell.run`**: output capped at `MAX_OUTPUT = 200_000` chars, 300 s timeout, and the
  description teaches `&&` chaining ("never `; echo EXIT:$?` — the echo masks a failure",
  `clio/toolhost/tools/shell.py:14,107-136`). The tool accepts arbitrary shell text; the
  guardian classifies it, and the classifier's parser falls back to a token scan on parse
  failure ([02](02-safety-and-guardian.md) §8).
- **`code.exec`**: persistent Python kernels (`clio/toolhost/kernels.py`,
  `EXEC_TIMEOUT_S = 60` at kernel level, tool-level 120 s, `MAX_OUTPUT_CHARS = 60 000`),
  keyed by session (default the calling thread's id, else `"self"`); `code.reset` (15 s)
  clears.
- **`http.request`** denies private ranges when
  `permissions.network.deny_private_ranges` is set — and this deployment sets it **false**
  ("an agent doing a person's whole job needs the LAN"), so the LAN is reachable by design
  (`clio/toolhost/tools/http.py:44-92`; `config/permissions.yaml`). SSRF posture is
  configuration, not code.
- **`history.browse`**: the agent can audit its own past — look-back capped at
  `MAX_HOURS_BACK = 24 × 366` hours, `MAX_LIMIT = 200` rows, literal matching, and
  `hasMore` computed by asking for one row more than the limit so it is "a fact about the
  world and not about the page size" (`clio/toolhost/tools/history.py:12-72`).
- **`secrets.*`**: pyrage-encrypted at rest, `{{secret:KEY}}` interpolation, redacted in
  logs (§8, [08](08-runtime.md) §10).
- **`comm.send`** is restricted to `SEND_CHANNELS` (`clio/toolhost/tools/comms.py:12-41`)
  and routes through the outbound pipeline — tools cannot bypass etiquette; a channel not
  in the approved list is a high-impact external side effect needing a pre-execution
  approval (`clio/config.py:1152-1156`).

## 12. Browser, desktop, kernels, watchers

- **Browser** (`clio/toolhost/browser.py`): a Chromium pool, `MAX_SESSIONS = 4` (`:22`),
  idle TTL 300 s (`:28`), `VISIT_TIMEOUT_MS`/`SELECTOR_TIMEOUT_MS` named constants
  (`:64,171-220`) — "a message that guesses its own timeout is a message guessing at a
  number it was never told." Session-aware refusals: operating on a closed page names the
  open pages.
- **Desktop** (`clio/toolhost/desktop.py:20-60`): `xdotool` + `import` on Xvfb `:1`; an
  absent tool raises `DesktopError` naming what is missing ("desktop control requires
  xdotool and an Xvfb display") — never a silent no-op. The sandbox's *virtual* computer
  (per-project display + ref-addressed browser) is a separate subsystem:
  [10](10-sandboxes-steering-and-policy.md) §3.
- **PTY sessions** (`clio/toolhost/pty_session.py`): lazily imported, POSIX-only via
  `platform.PTY_SUPPORTED` — an absent pty is reported, not silently no-op'd.
- **Watchers** (`clio/toolhost/watchers.py:15-141`): background directory/process watchers
  the agent can arm, feeding percepts; the toolhost observes so the agent doesn't have to
  poll.

## 13. The toolshed: self-written tools

`tools.register / tools.run / tools.list` (`clio/toolhost/tools/toolshed.py`) let the agent
write its own tools and have them persist — the README's example: a shell incantation typed
eleven times becomes a capability.

A toolshed manifest carries `allowed_paths, allowed_hosts, secret_scopes, timeout_s`
(default 60 for the manifest; `tools.run` itself 120 s), a SHA-256 `content_hash`, and an
**optional Ed25519 signature**. **The signature is verified when present, and its absence
is accepted** (`verify_tool_signature`, `toolshed.py:22-38`: "Returns True when there is
nothing to verify"). Registration pins the hash either way; enforcement of the fences is
**pre-execution** — `GuardianGate.review()` checks `allowed_paths`/`allowed_hosts` before
the tool runs (`clio/guardian/classifier.py:708+`), while `secret_scopes` are stored but
**not enforced** by that checker ([02](02-safety-and-guardian.md) §8).

## 14. External tools: MCP, connectors, importers

- **MCP** (`clio/toolhost/mcp.py`): stdio, Streamable HTTP or SSE transports (`:99-250`),
  pooled with health checks; a down server stays visible as a **degraded stand-in
  descriptor** (`degraded_descriptor`, `:79-96`) rather than disappearing from the catalog.
- **OAuth connectors** (`clio/toolhost/connectors.py`): tokens sealed at rest with the
  space's envelope key (`connector_accounts.sealed_tokens`), decrypted only inside the
  toolhost's execution so raw tokens never enter model context (`:10-20`); refresh under a
  per-connector lock; per-project scopes on the account. Providers are data-defined
  (GitHub, Linear, …, `:52-90`).
- **Importers** (`clio/toolhost/importers.py`): OpenAPI 3.x (recursive, cycle-bounded
  `$ref` resolution — "a `$ref` chain is too deep (a cycle?)", `:49-70`) and GraphQL (SDL
  or introspection) become typed tools with their own CEL policy slots.

## 15. Theory: hands vs mind

The toolhost exists because **the mind must not touch the world directly**:

1. **One funnel.** Every side effect crosses one boundary, so one journal, one classifier,
   one audit chain covers everything. There is no side door: `comm.send` still obeys
   etiquette, `http.request` still answers to network policy, and `subagent.*` tools
   classify as execute+write in the guardian.
2. **Failures as information.** Dispositions are typed so the agent can route around them —
   including a refusal *that names the nearest permitted thing*.
3. **Capability honesty.** The tool catalog is the honest boundary of "can you…" — pinned
   into the cached prefix so the agent's self-knowledge of its hands is complete and cheap
   ([04](04-memory-and-self-model.md) §8).

## 16. What this document does not claim

- **Routing quality numbers are opinions in YAML.** `models.yaml` `quality` fields are
  configuration, corrected only by the nightly calibration; a wrong number routes wrongly
  until measured.
- **The signature-optional toolshed** (§13) means "registered" is weaker than "signed"; an
  operator wanting signature-mandatory registration must supply a policy or key check
  outside this checkout.
- **`tools.search` is lexical, not semantic** (no embedder is wired into `ToolCatalog` by
  default), so a step that describes its need in unrelated words may miss the right tool on
  its sheet and must fall back to the meta-tool.
- **Provider quirks** (`config/quirks.yaml`, `ProviderQuirks`, `clio/config.py:1137-1146`)
  paper over real provider differences (cache-control placement, reasoning-token shapes);
  they are corrections, not guarantees.
