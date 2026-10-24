# Clio Agent 3.1

## v3.1.0-beta.4

Beta 4 fixes the failures behind the 2026-10-22 Docs task and the false "not started" report on the 30K training task.

- Filesystem: added `fs.mkdir` for creating folders (including missing parents). A directory that already exists is an idempotent success; a file with that name is refused rather than treated as the folder. `fs.write` now refuses a directory path with an `IsADirectoryError` that names `fs.mkdir`, and refuses a file as its parent with a `NotADirectoryError`, instead of surfacing a bare `FileExistsError` after the model has already committed to the action.
- Planning: restored `gsl.min_real_plan_steps` from 3 to 1. A valid one-step plan is no longer rejected as "too short", and the plan prompt asks for as many steps as the work needs up to seven instead of starting at three.
- GSL checks: folder creation is an action (`fs.mkdir`), not a success check. Checks may inspect a folder with `test -d`; a check that creates the folder is still rejected, but the prompt no longer suggests the rejected shape.
- Social context: the deliberation's execution-state block now lists open work with its last recorded action and current plan step, alongside the short journal window. A quiet journal no longer reads as "this task never started" when its last action was forty minutes ago.
- Responsiveness: the life loop's inter-cycle wait is cut short the moment a percept arrives, so a message sent via Telegram (or any channel) is perceived and answered on the sender's clock rather than on the next tick — up to eight seconds waiting and sixty resting of delay removed. The tick still paces an idle agent, and the percept that ended the wait is held for the pass rather than dropped.
- Tests: filesystem coverage for folder creation and the directory/file confusion, plan-floor and prompt assertions, and the new open-work progress line.

## v3.1.0

Aligned with the NEXT AGENT philosophy requirements (NA-P1 through NA-P3, NA-H1 through NA-H5).

- Golden Rule embedded in the identity kernel (NA-P1): human-likeness and performance are pursued together, and where they genuinely conflict, performance — especially coding quality — wins. Stated as one standing priority rather than per-case rules.
- Human-likeness instructions in the kernel (NA-H1–NA-H4): concise by default with length following the work; self-initiated conduct; whoever wrote last is one input among many; nothing machine-shaped goes out (no status-report tone, boilerplate, or templated failure notices).
- Hardcoded heuristics that overrode model judgment removed (NA-P1.3, NA-H5): the deliberation's T0 ignore rules (empty-message and unsolicited-link regex ignores) are gone — every message is deliberated; the attention layer's percept-kind whitelist no longer vetoes an interrupt above threshold.
- Deliberation token ceilings lifted and made configurable (NA-H1): `social.deliberation_max_tokens` (16k) and `social.deliberation_escalated_max_tokens` (48k); consequential messages — coding and multi-step work — take the lifted ceiling on the first ask.
- Silent task drops eliminated (NA-P2.1): provider-side planning failures (rate limits, no eligible model) no longer count toward the park threshold — a task stops only when the user asks or the goal is genuinely impossible.
- Verification blocks premature completion (NA-P2.2): stated requirements that no check ever ran on now block a `done` transition, even when the independent verifier says pass and no code file was touched.
- Interruption persistence (NA-P2.3): bookmarks now carry the original goal (title, desired end state, status); wake-up restores the focus, says the resumption in the agent's own thread, and the continuity wake report names the resumed goal and restored stop records.
- Disposition-driven observable stops (NA-P3): every task termination (completed / stopped / limit reached / model stop / error) and every model or tool failure is recorded with a distinguishable kind — gateway failures carry a disposition in the model-call record and a `ethos_model_failure_dispositions_total` metric; tool-journal digests and action-update events carry dispositions; the Life loop's stops ledger persists across restarts.
- Stop states flow into the workspace (NA-P3.3): a "How recent work ended" block renders the ledger, and the identity prompt instructs the agent to report stops and their reasons in its own words — no hardcoded error templates.

## v3.0.0-beta.4

- New exhaustive JSONL log of everything the agent does: user actions, settings changes, and every model request, response, and failure, with redaction
- The web bridge records its own writes in the exhaustive log: base-model changes, identity changes, and every message sent from a surface
- Test runs no longer write forged records into the agent's real exhaustive log
- Price entry for `stepfun-ai/step-5-preview`
- Durable stage notices for commissioned work, announced by the agent when reached; bounded retry curve (migration `0005`)
- Replies can be paced across several messages via `reply_delays_s`
- Rebuilt cost accounting: per-route price resolution, no invented defaults, repriced history (migration `0004`)
- Learned model traits (refused parameters, think-by-default models) persist across restarts and reloads
- Dead local endpoints are probed at startup and removed from the failover ladder
- Sub-agent delegations are tracked and grounded in the deliberation prompt; sub-agent claims are verified against what actually ran
- Secret redaction covers Telegram token shapes and standard-library log records, including httpx request URLs
- Product name standardized on "Clio Agent 3"

## v3.0.1

- Exhaustive JSONL logging now captures the full agent lifecycle: user actions, settings changes, model requests/responses, and failures with full redaction
- Stage notices: durable notices for commissioned work with bounded retry curve (migration `0005`)
- Paced replies via `reply_delays_s` allow replies to be split across multiple messages
- Cost accounting rebuilt: per-route price resolution, no invented defaults, and historical `model_calls` repriced via migration `0004`
- Learned model traits (refused parameters, think-by-default models) now persist across restarts and reloads
- Dead local endpoints probed at startup and removed from the failover ladder
- Sub-agent delegations tracked and grounded in the deliberation prompt; claims verified against actual execution
- Secret redaction extended to cover Telegram token shapes and standard-library log records including httpx request URLs
- Product name fully standardized on "Clio Agent 3"
- APEX submodule updated to `066b638` (ethos gateway provider, `--goal stdin` fix)
- New comprehensive test suite: stage notices, subagent tracking, channel reload, gateway, GSL budgets, reply path, secret redaction, and fs edits
- Config: `models.yaml` and `pricing.yaml` updated; `Makefile` venv path fixed; `.env.example` documents exhaustive logging defaults
- Engine: `life.py` overhauled, `ethos/engine/attention.py` reworked, `ethos/gsl/planner.py` updated
- Observability: `exhaustive.py` and `logger.py` added; `ethos/observability/` expanded
- Gateway: `openai_compat.py`, `router.py`, `secrets.py`, `service.py` revised
- Social: `deliberation.py` and `etiquette.py` revised
- Migrations: `0005_stage_notices.py` added
- Foundry bridge: assistant tool-call notes are now built as `ToolCall` objects in the constructor, fixing `AttributeError: 'dict' object has no attribute 'id'` that failed every in-process sub-agent turn after the first tool call
- Gateway: OpenAI-compatible provider read timeout raised from 180s to 600s, ending the retry churn that regenerated slow completions from scratch
- Memory: nightly consolidation no longer distills its own bookkeeping episodes into junk facts
- Retry config: `max_retries` 5 to 20, `max_backoff_s` 30s to 300s
- Sub-agent budgets: Foundry and APEX tool timeouts raised to 86400s so sub-agents report their own verdicts
- Foundry sub-agent failure summaries now include the task excerpt
- Architecture document translated from Japanese to English