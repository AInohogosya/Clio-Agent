# Clio Agent 3.1

## v3.1.0-beta.9

Field repair for the day the logs show the guardian causing the loss it exists to make survivable, and the night the agent could not start at all: an `rm` of two virtual-environment directories ended with the whole project directory in the trash (2026-10-27 19:37, restored from the trash twenty minutes later), and from 00:12 to 05:49 the core came back from the dead every two seconds — 10,044 restarts — none of them surviving the import.

- Guardian: the destructive review now reads what a command would *destroy*, not everything it *names*. `find DIR ... -exec rm -rf {} +` names `DIR` as the tree to *walk* and `2>/dev/null` as where stderr goes; `extract_paths` collected both, the gate asked the registry whether the first was protected — commissioned work lives underneath it, so yes — and moved it to the trash on a delete whose operands were two subdirectories of it. `extract_delete_targets` collects the operands of the destructive commands only: a redirect target is never a delete, and a read command's operand (`find`'s root, `ls`'s argument) is a place the command looks, not a thing it ends. The due-process branches move the target that matched protection and record `moved` in the audit entry — the row that named `progress.log` while the trash held the project directory is the lie this closes.
- Guardian: the fast path trashes *every* existing concrete target, not the first. An `rm` of `venv .venv` moved `venv`, replaced the whole command line with a read, and left `.venv` standing for an attempt that would arrive looking nothing like the one the agent wrote. Each move is its own trash entry, so each restores on its own.
- Toolhost: `fs.read` on a directory says so, instead of raising an exception whose text is the bare path — which is exactly what the transformed call returned after the misfire above: an `error:` line that was a path and nothing else, no word about directories or what to do instead, while the agent spent its next steps guessing where its project had gone.
- Social: `ethos/social/deliberation.py` is back. It was deleted from the working tree at 2026-10-27 16:03 together with its reply-path tests, with nothing renamed in its place and thirteen importers still pointing at it. The running core kept going on modules already in memory until the 00:12 restart hit `ModuleNotFoundError` — and then the supervisor's two-second loop spent the night on it. The module is restored and the beta.7 work it carried is reconstructed from the compiled bytecode of the last process that ran it, checked instruction-for-instruction against what was lost: `NO_CAP` and `effective_attempt_caps` in place of the hard-coded `MAX_*` ceilings, the queue reading its caps from configuration, and an uncapped question paced onto the escalation curve after three immediate attempts instead of being asked every cycle. The scenario tests written against that contract pass against the reconstruction.
- Supervisor: the restart backoff is now applied to the restarts. The gate that checks `next_attempt` was an `elif` on `proc is None`, so a child that *died* — which is exactly the child that needs pacing — blew past it every sweep: the delay was computed, logged (`delay: 48.0` on every line), and never slept, and the crash loop above ran at the poll rate. A death now schedules its restart; the sweeps in between wait. A child that ran stably still earns an immediate restart, because its `next_attempt` is already in the past.
- Gateway: an endpoint that refuses the reasoning parameter under its own name is understood. The refusal arrives as `thinking_token_budget is not yet supported` — no substring of `reasoning`, and "not yet supported" is not "not support" — so the no-reasoning trait was never learned and every call paid the 400 again: thirty-nine of them on 2026-10-27, all one purpose. The parameter's known aliases are matched and "not yet support" joins the refusal phrases.
- Pricing: `nvidia/nemotron-3-ultra-550b-a55b` (NVIDIA developer trial API, the live base model since 2026-10-25) is priced by name, so the catalogue-resolution test passes and a day of retries against a free endpoint does not read as money the agent never spent.

## v3.1.0-beta.8

Fixed the live failure behind "it says it is picking up the task but nothing happens" (observed 2026-10-25): the agent spent a whole morning advancing a drive-named placeholder while the commissioned task sat open, and every reply said the real task was being picked up.

- Drives: an impulse whose whole subject is work that already exists (`advance_commitment`, `push_venture`) no longer mints an intention titled after itself when no such work is open. That gap — the moment between real work parking and the sweep reopening it — was the one arbitration where the fallback fired, and what it minted had no subject to plan against: the solver produced bookkeeping theatre ("capture HEAD hash", "create `.clio/advance-repo`", "append timestamped evidence line") whose steps passed trivially satisfiable checks, leaving the placeholder `in_progress` forever. Twenty-nine such rows had accumulated since 2026-10-18. An impulse with no subject now returns no focus, and the drive fires again the moment real work is open.
- Focus: commissioned work now preempts idle impulse work mid-cycle. The commission bypass in `choose_focus` only ran when the focus was empty, and an impulse focus with a pending step never empties — `_settled_focus` hands it back every cycle — so a person's request sat open behind the placeholder indefinitely. While the focus is the drives' own invention (`kind="impulse"`), `decide` asks the store for the oldest open commission (`MemoryStore.next_open_commission`) and hands the pass to it; a commission or a deadline keeps the pass it already had.
- Reopen sweep: a drive-named placeholder is never resurrected. The sweep exists to give real work another chance, and an "advance_commitment" row parked by its own fake loop came back every backoff window — an immortal fake task.
- Pricing: the pinned base model `nvidia/nemotron-3.5-super-vl-120b-a12b` (NVIDIA developer trial API) is priced by name, so the catalogue-resolution test passes and spend reports do not charge it the high-water mark.
- Data: the 29 live placeholder rows were closed as parked with the reason on the row; the running agent's focus settles to nothing and the commission bypass picks up the real work on the next tick.

## v3.1.0-beta.7

The remaining give-up caps are gone: a task stops only when the user asks it to or the goal itself cannot be met. Failure counts now pace retries instead of ending work — an unbounded *number* of attempts, never an unbounded *rate* of them.

- Planning: `gsl.plan_attempts_before_park` defaults to zero — never park. A goal whose plan calls keep failing is degraded after the third failure (unchanged) and re-asked on the backoff curve for as long as the goal is open. A positive value restores the old ceiling.
- Strategies: `gsl.max_strategies` defaults to zero — no ceiling on how many approaches a failing step earns. Because there is no park at the end of the ladder any more, each installed strategy carries its own backoff (growing with its number, capped at `plan_backoff_max_s`), so a hopeless goal is retried every few minutes rather than every tick. A positive value restores the park-after-N behaviour.
- Reopens: `gsl.park_reopen_max` defaults to zero — unlimited reopens of parked work (a goal that parks, reopens and parks again is a goal the agent has not given up on). A positive value bounds the sweep the old way; a negative value disables it.
- Messages: `social.max_deliberation_attempts` / `social.max_transient_attempts` default to zero — a question nobody can answer yet is retried for as long as the agent runs. The first three attempts stay immediate; the rest move onto the shared escalation curve (5 min → 1 h → 5 h → 1 d, capped at a day). The "could not answer after N attempts" notice now only fires when a cap is configured, because with none there is no last attempt.
- Delegate threads: `threads.call_attempts` and `threads.subagent_attempts` default to zero — a failing model call is retried on the worker's backoff for as long as the thread runs, and a crashed sub-agent run is retried with a short gap. One exception is deliberate and unchanged: a failure that says *no model on this deployment could serve the request* ends the task on the spot — the goal is parked and the reopen sweep brings it back, so the work is never lost, only paced at the system level.
- APEX: `subagents.apex_max_steps` defaults to zero — the desktop controller runs under its own maximum (10,000 steps) instead of 60. A positive value is passed through; the per-call `max_steps` argument still wins.
- Tests: the park-at-the-threshold, bounded-retry and bounded-queue tests pin their caps explicitly (they are the configured modes now), and new tests pin the field contract — an unplannable goal never parks and keeps counting, unbounded message retries space out instead of dropping, and unbounded strategy switches schedule their own pacing.

## v3.1.0-beta.6

Field hardening: the agent that stops when something breaks is not a slow agent, it is a stopped one. Every path that ended work, wedged a process, or waited for a person has been made self-recovering.

- Supervisor: required children are never given up on. The five-crash retirement that left a half-alive stack (gateway running, core retired forever) is gone; restarts continue for the life of the supervisor on a backoff that tops out at 48s and resets after a minute of stable life. The operator's stop is `ethos stop`, never a crash count.
- Supervisor watchdog: a core that is alive but wedged — a call that never returns, an await nothing completes — is detected via a heartbeat file the life loop touches every cycle and every parked heartbeat, and is terminated and restarted after `ETHOS_CORE_HEARTBEAT_STALE_S` (default 1800s). Process-death polling alone cannot see a hang.
- Life loop: `wake_up` is retried on a backoff instead of killing the process; an exception from a cycle interrupted by a stop is logged rather than silently discarded.
- Parked work reopens itself: aged parked intentions (crashed delegate threads, exhausted strategies, failed plans) are reopened on a `gsl.park_reopen_backoff_s` backoff (default 30 min) up to `gsl.park_reopen_max` (default 3) times, with a fresh degradation ladder, so owed work is retried without a human. Budget-parked work waits for the budget.
- Thread orphan reaper: a worker that dies before publishing its result no longer holds a delegate slot forever — four orphans used to end delegation entirely. Stale handles (`threads.stale_after_s`, default 2h) are failed, their intentions parked for the reopen sweep, and the slot returned.
- Undecided messages: when a message exhausts its retry cap the person waiting is told — "could not answer after N attempts" — instead of the queue going silently quiet behind a last "retrying..." notice.
- Control plane backstop: the lifecycle row is re-read at most every 30s in addition to files and bus events, so a pause or resume written where no notify can follow still lands — in both directions — and a wedged bus can neither hide a stop nor hold a pause.
- Guardian field mode (`guardian.mode: field`, default; `ETHOS_GUARDIAN_MODE` overrides): the hard-limit pattern block, the capital-at-risk rejection and the due-process model check become audit entries and the action proceeds; the journal, trash and snapshots — the reversibility that makes mistakes survivable — run identically, and oversight (pause/stop, preview) stays effective in every mode.
- Budget soft caps (`hard_limits.on_exceed: warn`, default; `ETHOS_BUDGET_MODE` overrides): a crossed cap is logged loudly once per interval per bucket and shown on the dashboard, and no model call is refused. `block` restores the hard raise.
- Open field defaults: quiet hours off (a zero-length window), interruption budgets sized for an agent that is its owner's only channel, and private-network ranges allowed for the HTTP tool (local dev servers, self-hosted dashboards, NAS).
- Tests: supervisor never-give-up and wedged-core watchdog; the park-reopen lifecycle against the real schema; orphan-slot reclamation; guardian field-mode allow/audit/transform paths end to end; budget warn/block/env override; the control-plane row backstop and the file's authority over it.

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