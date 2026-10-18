# Clio Agent 3

## Unreleased

- A reply is however many messages it takes, and each one can arrive at its own
  pace. The reply path always accepted a `reply_texts` list of any length, but
  the prompt's only examples were the two-message shape — an answer and the
  thought that follows it — so replies came out as pairs in practice, and every
  message in a reply went out in one burst with no time between them.
  `SocialDecision` now carries an optional `reply_delays_s`, aligned by index
  with `reply_texts`: the number at index i is the wait in seconds before
  message i is sent, so a reply written as three or ten messages can arrive the
  way a person sends several, with a breath between them. The wait happens
  inside the turn and the message is marked handled only after the last text is
  out, so a restart mid-reply re-deliberates rather than forgetting the tail;
  each gap is clamped to the new `social.reply_gap_max_s` (default 30s), so a
  model that misreads the unit costs seconds, not an afternoon. Blanks in
  `reply_texts` drop with the gap written for them, the
  unearned-progress guard keeps texts and gaps aligned when it withholds a
  claim, and every malformed delay — non-list, non-number, boolean, negative —
  reads as 0, which sends at once. See `ethos/schemas/messages.py`,
  `ethos/social/deliberation.py`, `ethos/engine/life.py` and
  `tests/unit/test_reply_path.py`.
- The agent no longer forgets what it finished, and no longer forgets to say. Two
  complaints, one cause: nothing durable recorded that commissioned work had
  reached a stage the person who commissioned it was waiting for.
  - **It said it had not done things it had done.** The evidence a reply can draw
    on was the `action_journal` inside a fifteen-minute window and the list of
    *open* intentions — so a closed intention appeared in neither, and a person
    asking about work completed an hour ago was answered from a block whose only
    contents were a quiet journal and an empty work list. The prompt says to treat
    that block as a record of what happened, which is right about work in flight
    and wrong about everything finished before the window. `Execution state` now
    carries a third line — work that has already reached a stage — read from the
    new `stage_notices` rows, and says plainly that a quiet journal next to an
    empty blocked list is not evidence the work was not done.
  - **It did not come and say when a stage arrived.** The loop had two ways to
    speak: answer a message that arrived, or be a solver step that happened to
    call `comm.send`. So "tell me when it's done" was heard only when some step's
    model decided to mention it unprompted — which is what *sometimes* looks like
    from outside. `Life.decide` now has a pass for a word somebody is owed, which
    enters the `NOTIFYING` state that existed with nothing behind it, and the
    message is composed by the model against a prompt written for opening a
    conversation rather than answering one. Nothing is announced from the solver
    loop: the debt and its subject are recorded, and the words are the agent's own,
    which is the rule the canned announcement was removed for.
  - A promised update queued behind quiet hours or a spent budget is no longer
    refused by the unanswered gate at flush. `_enqueue_digest` kept neither the
    intention nor the fact that there had been one, so the longest-running entry
    in the system was the one that could not go out — an agent's own "it's done",
    blocked for `awaiting_reply_h` after its own "on it", and blocked again on
    every flush until the person happened to write back.
  - Migration `0005` adds `stage_notices`, unique on `(intention_id, stage)` so a
    stage reached twice is announced once. Retries are on a bounded curve and stop
    at `social.stage_notice_max_attempts`, past which the notice is settled with
    the exhaustion recorded rather than retried at the tick rate forever. See
    `ethos/schemas/gsl.py`, `ethos/memory/store.py`, `ethos/gsl/loop.py`,
    `ethos/engine/life.py`, `ethos/social/deliberation.py`,
    `tests/unit/test_stage_notices.py` and
    `tests/integration/test_stage_notice_store.py`.
- Rebuilt cost accounting end to end, because the budget pane was fiction.
  Models missing from the price table — including the pinned base model and
  every `:free` route — were billed at an invented default, so free traffic
  reported spend; providers that fold cached and reasoning tokens into their
  reported totals were billed for those tokens twice; Anthropic cache writes
  were priced at nothing; and the dashboard read the ledger with a different
  status filter and day boundary than the cap it is drawn against. Prices now
  resolve per route (live provider pricing, cached, over a verified checked-in
  table, over a logged pessimistic high-water mark — no silent default), token
  counts are normalized to disjoint buckets per provider, a provider-reported
  cost is used verbatim, the guard and the pane read through one shared spend
  query, and migration `0004` reprices historical `model_calls` from their
  stored token counts. See `ethos/pricing.py` and `tests/unit/test_pricing.py`.
- Standardized the product name. The product was known variously as "Ethos",
  "Clio Agent 3 Beta" and "Agent"; it is now **Clio Agent 3** everywhere it is
  user-facing. The designation carries no stage suffix — pre-release state is
  recorded by the `3.0.0-beta.3` version in `package.json` and `pyproject.toml`,
  which is unchanged, and by this entry. The `ethos` package, `ethos` command,
  `ETHOS_*` variables, `~/.ethos` home, database and service names are
  identifiers rather than branding and are unchanged.

## v3.0.0-beta.3