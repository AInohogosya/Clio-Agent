# Social cognition: deliberation and the reply path

When a message arrives, the agent does not answer reflexively. It **deliberates**:
it decides *whether* to reply, *when*, *what*, or whether to do nothing with a
recorded reason. Sources: `clio/social/deliberation.py`, `clio/schemas/messages.py`,
`clio/prompts/deliberation.py`.

## 1. The decision vocabulary

Actions (`clio/schemas/messages.py:132-138`) — six, not two:

| Action | Meaning |
|---|---|
| `reply_now` | answer immediately |
| `reply_later` | answer after a stated delay |
| `acknowledge` | brief receipt without a full answer |
| `act_silently` | do the work, say nothing yet |
| `ask_clarification` | the message is not answerable as given |
| `ignore` | decline — with a categorized reason |

Ignore reasons (`messages.py:141-149`), all legitimate:

- `already_addressed` — this was answered elsewhere
- `no_response_needed` — there is nothing to say back
- `deliberate_disagreement` — the agent considered and declines (used when a
  commission is flawed; see §5)
- `boundary` — outside what it will do
- `spam_or_untrusted` — the source fails the trust gate

The wire distinction that matters most: **"chose silence" vs "the model died
mid-answer"** are indistinguishable on the wire, so the system carries `decided`
explicitly — an unreadable deliberation produces `_undecided` (`decided=False`,
`deliberation.py:517-541`) and is never reported to a person as a deliberate choice.

`SocialDecision` carries `reply_texts, reply_delays_s, urgency, reason,
ignore_reason, creates_intention, intention_spec, tier_used, decided`
(`messages.py:152-182`).

## 2. The deliberation call

`SocialCognition.deliberate()` (`clio/social/deliberation.py:394-1025`):

- **Tier**: a mechanical **consequential-text test** (`is_consequential_text`,
  `deliberation.py:201-249`) — word-boundary matches plus a `$<digit>` money regex —
  escalates to `T3`; everything else is `T2` (`deliberation.py:457-458`).
- **Token ceilings**: `DELIBERATION_MAX_TOKENS = 16_000`, escalated (consequential)
  first-chance budget `48_000`, `REASONING_BUDGET = 400`
  (`deliberation.py:77-99`; consequential gets the escalated ceiling first,
  `:422-439`). Stage notices cap at 900 tokens.
- **Prompt contents** (`clio/prompts/deliberation.py:65-252`): the recent
  conversation (open conversations within 72 h, limit 8, last 6 turns at 400 chars
  each — `clio/social/conversation.py:36`), open commitments, and the
  **execution-state block** (`deliberation.py:469`): what is in the action journal,
  what is blocked, what is delegated, what stages are pending, real progress. This
  block is what makes the honesty guardrail possible (§4).
- **Output contract**: strict JSON matching `DELIBERATION_OUTPUT_CONTRACT`, and the
  contract is **restated after the user message** (`prompts/deliberation.py:247-252`)
  — the last thing the model reads before answering is the schema, which measurably
  reduces malformed output.
- **Attempt caps**: `max_deliberation_attempts = 0` (uncapped) by default
  (`config.py:707-708`); a 429 escalates along the shared wait schedule
  `5m → 1h → 5h → 1d` (`deliberation.py:138-147`).

## 3. The honesty guardrail

`_withhold_unearned_progress` (`deliberation.py:930-1025`, referenced as `:1080` in
the module map) is the mechanical half of "candour without announcement":

- It scans drafted replies for **present-tense "underway" claims**.
- A claim is kept only if an action-journal row or a delegation actually backs it.
- Unsupported claims are stripped, and the reply goes out with what is true.

This is the codebase's answer to the classic agent failure of promising progress in
the syntax of a status update. The design comment behind it: *"everything sent to a
person is a word the model chose"* — so the last mile of every reply is mechanically
audited against the work record.

## 4. Speaking first: interruption gates and stage notices

The reply path also covers **initiated** contact:

- **Stage notices** — "tell me when it's done": a commissioned intention reaching
  `done / parked / awaiting_review` produces a notice to whoever is waiting
  (`clio/gsl/loop.py:2022`, `clio/memory/store.py:563-711`), decided at `notify`
  priority in `decide()` ([03-engine.md](03-engine.md) §5) and rendered through
  a small dedicated prompt (`prompts/deliberation.py:254-295`, `{"text"}`).
- **Ignore-streak review**: if the agent has ignored ≥3 messages from one person,
  the relationship store triggers a `T3` review (`clio/social/relationships.py`,
  `STREAK_REVIEW_THRESHOLD = 3`) — persistent silence is itself a signal worth
  deliberating about.

## 5. Commissions: epistemic checks before work

`evaluate_commission` / `deliberate_commission` (`deliberation.py:1204+`) screen
incoming commissions before they become intentions:

- A **flawed or under-budgeted spec** → `ask_clarification`.
- A spec **outside its skill or authority** → `boundary`.
- A spec it **legitimately disagrees with** → `deliberate_disagreement`.
- A clean spec routes into normal `deliberate()` handling, with a `COMMISSION_NOTE`
  in the prompt (`clio/prompts/deliberation.py:624`) covering money thresholds and
  disclosure obligations (H2/H4).

The agent is an equal party: it may decline work with a reason, and "declining with
a categorized reason" is one of the six documented behaviours of the product.

## 6. Cross-talk awareness

Each conversation carries a **cross-talk note** (`clio/prompts/identity.py:377-419`,
selected by preview mode; rendered per conversation in
`clio/social/conversation.py:110-146`):

- **Preview (closed)**: nothing crosses. Not a name, not that another person exists,
  not a confirmation if asked. "What people tell me stays with them" is said plainly
  and is true while the mode is on.
- **Normal (open)**: what was *said* stays closed; acknowledging a connection both
  parties already know ("I know Mira too") is permitted — "declining to say it would
  be its own kind of lie" — but content and identifying detail never cross.

The two versions are a parameter, not one text with a clause, because "the two are
not the same rule at different intensities" (`identity.py:410-419`): a single prompt
saying both would let the lenient half swallow the strict one — exactly the failure
preview mode exists to prevent.

## 7. Theory: reply as decision, not reflex

The deliberation layer implements a specific theory of conversation:

1. **A reply is an act with a cost to its recipient** — hence `act_silently`,
   `reply_later`, `ignore` are peers of `reply_now`, and the outbound pipeline
   ([05-social.md](05-social.md)) budgets all of them.
2. **Silence is typed** — the difference between "deliberately not answering" and
   "crashed mid-answer" is carried in data (`decided`), because they call for
   opposite follow-ups.
3. **Claims are audited against work** — the guardrail makes progress claims
   verifiable at send time.
4. **The agent can be an equal** — clarification, disagreement and boundary are
   first-class actions with reasons, which is what makes commissions negotiable
   rather than commands.
## Part II — Etiquette: the outbound pipeline

Every message the agent sends is mediated by `OutboundPipeline`
(`clio/social/etiquette.py:40-412`), tagged [D-31] in the code. The premise: the
cost of a message is paid by its recipient, so speech is budgeted, batched, and
held to a calendar. Sources: `etiquette.py`, `config.py:1123-1145`,
`clio/social/relationships.py`, `conversation.py`.

## 1. The controls

| Control | Default | Purpose |
|---|---|---|
| Quiet hours | `22:00–08:00` | Unsolicited sends wait for morning; the agent has no bedtime but people do |
| Daily budgets | owner 6 / collaborator 3 / other 1 | Per-person send caps per day |
| Digest window | 120 min | Low-urgency unsolicited messages batch into digests |
| Awaiting-reply | 12 h | Don't re-ping someone who hasn't answered for 12 h |
| Repeat window | 24 h | Same subject to same person, once per day |
| Mid-task exception | immediate | A send that completes a commissioned stage goes out now |

Mechanics:

- **Solicited replies bypass everything**: a message with `in_reply_to` goes out
  immediately (`etiquette.py:174-177`) — if someone asked, the budget does not apply.
- **Unsolicited low/normal** messages go through repeat/held-back checks and land in
  the **digest queue**; `flush_due_digests` (`etiquette.py:296-353`) releases them
  when the window closes.
- **Conversations** are keyed `channel:person_id` (`clio/social/conversation.py:26-33`),
  and each carries its cross-talk note (`conversation.py:110-146`), strict in preview
  mode.

## 2. Relationships

`RelationshipStore` (`clio/social/relationships.py:15-102`):

- A `people` table keyed by the channel `person_id` prefixes
  (`tg:`, `wa:`, `slack:<channel>`, `dc:`, `line:`, `teams:`, `gchat:`, `email:`,
  `cli:`, `web:`).
- **Trust** is stored clamped to `0–1` and feeds the attention source weights
  (`known 0.6`, `unknown 0.3` — [03-engine.md](03-engine.md) §2).
- **Ignore streak ≥ 3** → `T3` review ([05-social.md](05-social.md)
  §4): the agent examines why it keeps not answering this person.
- In 3.2, `people` gains `kind (human | resident | service)`, `capabilities`,
  `authority_scope`, `endpoint_uri` — the same table becomes the principal registry
  for federated residents ([09-version-3-2.md](09-version-3-2.md)).

## 3. Bimodal etiquette (3.2)

`_principal_kind` / `is_resident_address` / `_deliver_resident`
(`etiquette.py`): humans keep quiet hours, 120-minute digests and the 6/3/1 caps;
**residents bypass** — the `OutboundPipeline.send` fast-path delivers structured
JSON over the event bus (`publish_commission` kinds, `clio/bus/event_bus.py:19,208`).
Machine-to-machine speech does not need a morning.

## 4. Verbosity learning

`clio/social/verbosity.py` (`VERBOSITY_INTERVAL_MIN 60/15/3`, EN+JA patterns) tracks
reply-length feedback signals per person on decaying intervals: one-word replies,
"just the answer please", or their Japanese equivalents are signals that this
conversation wants terseness. Length is a per-relationship property, learned, not a
global setting.

## 5. Why etiquette is a subsystem, not a prompt line

The design could have said "be considerate about messaging people." Instead the
controls are in code because:

1. **They are checkable.** "The agent can be held to" a 6-per-day cap; a prompt line
   about consideration cannot be audited (`clio/prompts/identity.py:362-366` states
   the same principle for cross-talk).
2. **They encode consent.** "Interruption is a gift, not a right"
   (`DEFAULT_VALUES[5]`): the pipeline makes unsolicited interruption a **rationed
   resource**, so speaking first remains meaningful (it can't be spam) and remaining
   silent is the default that needs no justification.
3. **They match human time.** Quiet hours and digests are the mechanical form of
   "the conversation is not the only thing that moves me — and I don't impose."
   The agent runs at 04:00; the pipeline is what stops 04:00 from being your 04:00.
