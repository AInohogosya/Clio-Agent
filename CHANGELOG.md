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

## v3.0.0-beta.3