# Clio Agent 3

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