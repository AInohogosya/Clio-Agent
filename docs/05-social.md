# 05 — Social cognition and etiquette

When a message arrives, the agent does not answer reflexively. It **deliberates**: it
decides *whether* to reply, *when*, *what*, or whether to do nothing with a recorded reason.
Sources: `clio/social/deliberation.py`, `clio/schemas/messages.py`,
`clio/prompts/deliberation.py`, `clio/social/etiquette.py`, `clio/social/relationships.py`,
`clio/social/conversation.py`, `clio/social/verbosity.py`.

## Part I — Deliberation and the reply path

## 1. The decision vocabulary

Actions (`clio/schemas/messages.py:132-141`) — six, not two:

| Action | Meaning |
|---|---|
| `reply_now` | answer immediately |
| `reply_later` | answer after a stated delay |
| `acknowledge` | brief receipt without a full answer |
| `act_silently` | do the work, say nothing yet |
| `ask_clarification` | the message is not answerable as given |
| `ignore` | decline — with a categorized reason |

Ignore reasons, all legitimate: `already_addressed`, `no_response_needed`,
`deliberate_disagreement` (the agent considered and declines — used when a commission is
flawed, §6), `boundary` (outside what it will do), `spam_or_untrusted` (the source fails the
trust gate).

The wire distinction that matters most: **"chose silence" and "the model died mid-answer"
are indistinguishable on the wire**, so the system carries `decided` explicitly — an
unreadable deliberation produces `_undecided` (`decided=False`,
`clio/social/deliberation.py:2106+`) and is never reported to a person as a deliberate
choice. A decision recovered from a hold carries a `reason` saying it was recovered, so a
decision read back later is not mistaken for a fresh one.

`SocialDecision` carries `reply_texts, reply_delays_s, urgency, reason, ignore_reason,
creates_intention, intention_spec, tier_used, decided` (`messages.py:159-205`).

## 2. The deliberation call

`SocialCognition.deliberate()` (`clio/social/deliberation.py:531-770`):

- **Tier**: a mechanical **consequential-text test** (`is_consequential_text`,
  `deliberation.py:242+`) — word-boundary matches plus a money regex — escalates to `T3`;
  everything else is `T2`. The life loop uses *the same function* for the tier it labels the
  message with (`clio/engine/life.py:1434-1443`), so the two answers cannot disagree.
- **Token ceilings**: `DELIBERATION_MAX_TOKENS = 16_000`
  (`deliberation.py:86`), escalated (consequential) ceiling `48_000` (`:87`),
  `DELIBERATION_REASONING_BUDGET = 400` (`:94`). Stage notices cap at 900 tokens (`:108`).
- **Prompt contents** (`clio/prompts/deliberation.py`): the recent conversation (open
  conversations within 72 h, limit 8, last 6 turns at 400 chars each —
  `clio/social/conversation.py:39-40`), open commitments, and the **execution-state block**:
  what is in the action journal, what is blocked, what is delegated, what stages are pending,
  real progress. This block is what makes the honesty guardrail (§4) possible. The
  deliberation is also shown the **real tool catalog names** (`life.py:1446-1464`: asked of
  the toolhost, not hardcoded) — the failure it prevents is the agent telling its owner it
  cannot do something it can.
- **Output contract**: strict JSON matching `DELIBERATION_OUTPUT_CONTRACT`, restated after
  the user message — the last thing the model reads before answering is the schema, which
  measurably reduces malformed output.
- **Escalation on a bad answer** (`clio/__main__.py` documents the shape; implemented in the
  social/gateway path): a second ask is not the first ask again — the reasoning budget is
  widened and the contract restated, because "more of the ceiling cannot fix an answer of
  the wrong shape."
- **Attempt caps**: `max_deliberation_attempts = 0` (uncapped) by default
  (`clio/config.py:708`); a 429 waits out the shared escalation schedule 5 m → 1 h → 5 h →
  1 d (`clio/gateway/service.py:93-126`), with a plain rate limit distinguished from a
  quota problem before it is treated as a broken key (`deliberation.py:168-204`).
- **Deadline parsing**: the schema offers `deadline_iso`; a model that wants no deadline
  often writes the *word* "null". Anything unreadable is the absence of a deadline and the
  intention is still created (`clio/engine/life.py:3682-3710`).

## 3. What the deliberation knows that a chatbot's prompt does not

The execution-state block exists because the deliberation writes the words a person reads,
and it had no way to know whether any of them were true (`clio/config.py:591-650`, the
`SocialConfig` comment): a focus title is what the agent is *on*, not what it has *done*; a
commitment says the work was agreed rather than begun. A planning call that had been failing
six times in a row and parked the intention looked, from inside that prompt, exactly like
one that was about to start — and the reply was "on it".

So the prompt is fed the record, and the record is checked again at send time (§4).

## 4. The honesty guardrail

`_withhold_unearned_progress` (`clio/social/deliberation.py:1289-1418`) is the mechanical
half of "candour without announcement":

- It scans drafted replies for **present-tense "underway" claims**.
- A claim is kept only if an action-journal row or a delegation actually backs it.
- Unsupported claims are stripped, and the reply goes out with what is true.

It is applied on the ordinary reply path (`deliberation.py:766`), on recovered decisions,
and on **all** inter-resident commission updates (`deliberation.py:1475-1507`). The design
comment behind it: *"everything sent to a person is a word the model chose"* — so the last
mile of every reply is mechanically audited against the work record.

## 5. Speaking first: stage notices and the ignore streak

- **Stage notices** — "tell me when it's done": a commissioned intention reaching
  `done / parked / awaiting_review` produces a notice to whoever is waiting
  (`clio/gsl/loop.py:2947+`; `clio/memory/store.py` stage-notice store, idempotent per
  `(intention_id, stage)`), decided by a small dedicated prompt (`STAGE_NOTICE_MAX_TOKENS =
  900`), retried on the shared backoff (`stage_notice_backoff_s`, `config.py:662-665`), and
  delivered through the outbound pipeline's mid-task immediate rule (§8).
- **Ignore-streak review**: if the agent has ignored ≥ `STREAK_REVIEW_THRESHOLD = 3`
  messages from one person (`clio/social/relationships.py:12`), a `T3` review is triggered —
  persistent silence is itself a signal worth deliberating about.
- **Unanswered suppression**: the agent's own last word on a conversation counts as
  "awaiting reply" for `awaiting_reply_h = 12` hours, and is a reason not to open a *new*
  subject on it (`InterruptionConfig`, `clio/config.py:1169-1175`).

## 6. Commissions: epistemic checks before work

`evaluate_commission` / `deliberate_commission` (`deliberation.py:1420-1510`) screen
incoming commissions before they become intentions:

- A **flawed or under-budgeted spec** → `ask_clarification`.
- A spec **outside its skill or authority** → `boundary`.
- A spec it **legitimately disagrees with** → `deliberate_disagreement`.
- A clean spec routes into normal `deliberate()` handling, with a `COMMISSION_NOTE` in the
  prompt covering money thresholds and disclosure obligations (H2/H4).

The agent is an equal party: it may decline work with a reason, and "declining with a
categorized reason" is one of the six documented behaviours. `_withhold_unearned_progress`
is enforced on the outgoing update either way.

## 7. Cross-talk awareness

Each conversation carries a **cross-talk note** (`clio/prompts/identity.py:408-440`,
`CROSS_TALK_CLOSED` / `CROSS_TALK_OPEN`; rendered per conversation in
`clio/social/conversation.py:110-146`):

- **Preview (closed)**: nothing crosses. Not a name, not that another person exists, not a
  confirmation if asked. "What people tell me stays with them" is said plainly and is true
  while the mode is on.
- **Normal (open)**: what was *said* stays closed; acknowledging a connection both parties
  already know ("I know Mira too") is permitted — "declining to say it would be its own kind
  of lie" — but content and identifying detail never cross.

The two versions are separate texts, not one text with a clause, because "the two are not
the same rule at different intensities": a single prompt saying both would let the lenient
half swallow the strict one — exactly the failure preview mode exists to prevent.

## Part II — Etiquette: the outbound pipeline

Every message the agent sends is mediated by `OutboundPipeline`
(`clio/social/etiquette.py:48-340`), tagged [D-31] in the code. The premise: the cost of a
message is paid by its recipient, so speech is budgeted, batched, and held to a calendar.

## 8. The controls

| Control | Code default (`clio/config.py:1162-1194`) | This deployment (`config/permissions.yaml`) |
|---|---|---|
| Quiet hours | 22:00–08:00 | **off** (`00:00–00:00`) |
| Daily budgets | owner 6 / collaborator 3 / other 1 | **999 / 500 / 200** |
| Digest window | 120 min | 120 min |
| Awaiting-reply | 12 h | 12 h |
| Repeat window | 24 h | 24 h |
| Mid-task sends | immediate | immediate (`midtask_sends_immediate: true`) |
| Governance cap | 24/day, 15-min dedupe | as code |

The field-tuned deployment values are stated in the YAML with their reasons: the 6/3/1 caps
were sized for a chatty assistant, and for an agent that is the *only* channel of its
owner's work a low cap reads as "the agent went quiet halfway through the day"; quiet hours
are a zero-length window because an agent carrying someone's working day answers when the
work says so. **Critical urgency always bypasses quiet hours** and is audited
(`etiquette.py:52,141-150`).

Mechanics:

- **Solicited replies bypass everything**: a message with `in_reply_to` goes out
  immediately — if someone asked, the budget does not apply.
- **Unsolicited low/normal** messages go through repeat/held-back checks and land in the
  **digest queue**; the flush releases them when the window closes
  (`etiquette.py:172-258`).
- **Verbosity scaling**: the per-person budget is scaled by the learned verbosity —
  `quiet` → half, `balanced` → base, `detailed` → ×3 (`etiquette.py:96-98`,
  `clio/social/verbosity.py`). Length is a per-relationship property, learned from
  one-word replies and "just the answer please" (EN+JA patterns), on decaying intervals
  (`VERBOSITY_INTERVAL_MIN` 60/15/3).
- **Governance messages** — kickoffs, human asks, blockers, milestones, focus switches —
  get their own budget (24/day) and their own 15-minute dedupe rather than the 24-hour
  repeat window, because "a project lead who stays silent is the failure" and "the same
  notice twice in a quarter hour is a bug while the same notice twice in a day can be
  genuine news" (`etiquette.py:272-320`).
- **Conversations** are keyed `channel:person_id` (`conversation.py:26-33`) and each carries
  its cross-talk note (§7).

## 9. Relationships

`RelationshipStore` (`clio/social/relationships.py:24-200`):

- A `people` table keyed by the channel `person_id` prefixes (`tg:`, `wa:`,
  `slack:<channel>`, `dc:`, `line:`, `teams:`, `gchat:`, `email:`, `cli:`, `web:`).
- **Trust** is stored clamped to 0–1 and feeds the attention source weights
  ([03](03-engine.md) §6.2).
- **Ignore streak ≥ 3** → `T3` review (§5).
- In 3.2, `people` gains `kind (human | resident | service)`, `capabilities`,
  `authority_scope`, `endpoint_uri` (`migrations/versions/0006_clio_3_2.py:48-58`) — the
  same table becomes the principal registry for federated residents
  (`register_resident`, `list_residents`, `match_resident` by capability, trust and budget
  ceiling, `relationships.py:85-160`).

## 10. Bimodal etiquette (3.2)

`_principal_kind` / `is_resident_address` / `_deliver_resident`
(`etiquette.py:115-235`): humans keep quiet hours, digests and the caps; **residents
bypass** — the `OutboundPipeline.send` fast-path delivers structured JSON over the event
bus (commission kinds, `clio/bus/event_bus.py:35-46,227-240`). Machine-to-machine speech
does not need a morning.

## 11. Why etiquette is a subsystem, not a prompt line

The design could have said "be considerate about messaging people." The controls are in
code because:

1. **They are checkable.** "The agent can be held to" a 6-per-day cap; a prompt line about
   consideration cannot be audited.
2. **They encode consent.** "Interruption is a gift, not a right"
   ([01](01-philosophy.md) §4): the pipeline makes unsolicited interruption a **rationed
   resource**, so speaking first remains meaningful (it cannot be spam) and remaining
   silent is the default that needs no justification.
3. **They match human time.** Quiet hours and digests are the mechanical form of "the
   conversation is not the only thing that moves me — and I don't impose." The agent runs at
   04:00; the pipeline is what stops 04:00 from being your 04:00.

## 12. What this document does not claim

- **The guardrail is pattern-based.** `_withhold_unearned_progress` matches claim shapes
  against journal evidence; a false claim phrased outside its patterns (or a true claim it
  strips) is possible in both directions. The journal and the audit trail are the backstop.
- **Delivery is not the same as receipt.** A send the outbound path recorded and nobody
  received is not something the transcript can distinguish; channel adapters report their
  own failures ([08](08-runtime.md) §4-§6), and email/IMAP in particular has no read
  receipts.
- **Trust is coarse.** `known` vs `unknown` in the attention scorer is a 0.6/0.3 source
  weight, not an authentication: admitting somebody is not trusting them, and anyone can set
  a display name to anything (`README.md`, sender-identity section).
- **The digest queue is in-memory per process** (`etiquette.py:71-72`): a comms restart
  drops queued digests rather than replaying them.
