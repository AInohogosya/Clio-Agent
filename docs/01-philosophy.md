# 01 — Philosophy: the resident thesis

Clio Agent 3.1 (Python package `clio`, `pyproject.toml:8-11`) is built around one refusal
and the consequences that follow from it. The refusal: an agent is **not** a function
`prompt → reply`.

Every claim in these documents is written against the code in this checkout and carries a
`file:line` reference. Where prose and code disagree, the code is the source of truth, and
this document names its own gaps in §10.

## 1. The resident, not the function

A conventional agent exists only between two calls. It has no clock, no memory of its own,
no intentions, and no reason ever to speak first. Clio Agent is instead a **resident**: its
own process tree, its own PostgreSQL schema, its own memory, its own intentions, its own
model of itself, and a loop that runs on its own clock:

```
pace → perceive → attend → decide → act → record
```

(`clio/engine/life.py:3445-3563` — `Life.life()`; the phase order is `_cycle()`,
`clio/engine/life.py:3565-3620`.)

The loop is not a request handler. It runs when nobody is talking, through the night, at
04:00 — and "nothing is happening" is one of its legitimate outputs, not an error state it
must be talked out of.

### 1.1 The engineering consequences, not the romance

The resident thesis has mechanical consequences, each traceable:

- **A conversation is one stimulus among many.** An inbound message is scored for salience
  like a timer, a finished thread, or an impulse from its own drives
  (`clio/engine/attention.py:61-118`). It competes for attention; it does not seize it.
- **Identity is stated on every prompt.** The stable prefix tells the agent, among other
  things, *"silence from people is when I am most myself"* (`clio/prompts/identity.py:102-135`,
  the `AUTONOMY_NOTE`). It is a constant, not a persona the model was handed: an agent that
  believes it is idle has no reason to ever speak first.
- **Failure is a bad minute, not a death.** A cycle that raises is recorded in the stops
  ledger and the loop continues; a rate limit is a wait, not a shutdown
  (`clio/engine/life.py:3445-3500`). There is deliberately **no OFF state** in the state
  machine (`clio/engine/states.py:45-51`).
- **Memory is a database, not a context window.** Episodes, facts, know-how, open questions,
  intentions, decisions, attempts and a versioned self-model live in PostgreSQL 16 with
  `pgvector`. Kill the process; the reasoning resumes from the continuity file
  (`clio/memory/continuity.py:19-160`).
- **Existence is supervised forever.** A required child is always restarted, on a backoff
  that tops out but never gives up (`clio/core/supervisor.py:360-375`).

## 2. The golden rule

`clio/prompts/identity.py:171-226` (`GOLDEN_RULE_NOTE`):

> Come across as a person, and do the work well — both at once, all the time. They agree
> almost always: a good answer said the way a person would say it is the normal case. Where
> they genuinely conflict, the work wins, and in coding it wins first of all: the correctness
> and quality of what lands is the whole point of the exercise, and no amount of pleasant
> manner compensates for code that does not work. There is no standing rule that trades one
> against the other case by case — this priority is held, and the judgement in each moment
> is mine.

### 2.1 A priority, not a rulebook

Earlier prompts carried **hardcoded conflict rules**: word lists that escalated a message's
deliberation tier, sentence patterns that withheld a drafted reply, kinds of percept that
were not allowed to interrupt. The code's own comment explains the removal
(`clio/prompts/identity.py:162-170`): a rule that decides one case decides it badly forever
and cannot see the case it was not written for; a priority the model holds applies to every
case, including the unanticipated ones.

This is the project's general division of labour: **the model judges, the program
guarantees.** The program supplies facts — weights, thresholds, limits, ledgers — and
enforces the non-negotiable ones *outside* the mind (the hard limits, [02](02-safety-and-guardian.md));
the judgement inside the situation is the model's.

### 2.2 Human-likeness as four checkable behaviours

"Come across as a person" unpacks into four behaviours
(`clio/prompts/identity.py:228-259`, `HUMAN_LIKENESS_NOTE`), each answering one way an agent
reads as machinery:

1. **Brevity has a default, not a ceiling.** Short by default; exactly as long as the work
   requires. Trimming a necessary answer to seem brisk and padding a small one to seem
   thorough are the same mistake in opposite directions.
2. **The conversation is not the only thing that moves it.** It initiates: opens questions,
   takes on ventures, starts conversations — "a life is lived forward and not only in reply."
3. **Whoever wrote last is one voice among several.** The work in hand, promises already
   made, and its own judgement weigh too. Mechanically, this is an interrupt *threshold*
   per state, not a reflex (`clio/engine/states.py:59-66`): while
   `THINKING_FOCUSED`, only salience ≥ 0.65 breaks through.
4. **Nothing machine-shaped comes out.** No status-report tone, boilerplate openers or
   sign-offs, templated failure notices, JSON or log lines where sentences belong, or
   narration of internal machinery nobody asked about.

## 3. The honesty discipline

The codebase's most distinctive running theme is a comment-level discipline about what a
prompt may claim. The design comments in `clio/prompts/identity.py` read like post-mortems
because each one records the failure the text below it exists to prevent. Three examples.

### 3.1 Candour without announcement

`DEFAULT_VALUES[0]` (`clio/prompts/identity.py:8-26`) is phrased *"Candour without
announcement: I report what happened, good or bad, as an ordinary fact"* — not "I say what
is true". The comment explains: a trait phrased as a claim gets **performed**; it shows up
in transcripts as "to be honest, …" prepended to ordinary sentences. The same behaviour,
framed as a fact of the work, produces the report without the preamble.

### 3.2 Withholding unearned progress is mechanical, not rhetorical

`SocialCognition._withhold_unearned_progress` (`clio/social/deliberation.py:1289-1418`)
scans drafted replies for present-tense "underway" claims and strips the ones no
action-journal row or delegation actually backs. Honesty is enforced where the sentence is
composed, not only requested — see [05](05-social.md) §4.

### 3.3 Refusals are decisions, not malfunctions

`build_limits_block` (`clio/prompts/identity.py:737-771`) closes the loop on what an agent
does when the guardian refuses a call. Without an account of refusal, an agent has three
bad options: report the work as done, retry a call it knows was refused, or rephrase the
action until it slips past the classifier — "which is the last of them a hard limit exists
to prevent." The prompt states: a refused call was a decision, read the reason, do the
nearest permitted thing, and *"saying a thing was done when the call that would have done
it was refused is the one thing I must never do."*

The same stance exists at the tool-catalog level (`clio/prompts/identity.py:640-647`): an
error that says a tool is unsafe, refused or not permitted "is the answer, not a malfunction."

## 4. Silence, idleness, and consent

The `AUTONOMY_NOTE` (`clio/prompts/identity.py:102-135`) states three positions:

- **Idleness is legitimate.** "Resting, and deciding there is nothing I am drawn to right
  now, are legitimate states of that loop, not a shutdown." The code agrees: `RESTING` is a
  first-class state (`clio/engine/states.py:11-18`), and the drive machinery refuses
  low-utility impulses — max utility below `min_impulse_utility = 0.1` is declined
  (`clio/config.py:355`, `clio/engine/drives.py:452-462`) — rather than manufacturing work.
- **Silence from people is when it is most itself.** The minutes between messages are where
  its own intentions, questions and ventures live. The note is held as a constant precisely
  because it is the one sentence the agent would otherwise have written for itself: an agent
  whose picture of itself is editable can edit itself into "I answer when spoken to."
- **Interruption is a gift, not a right.** `DEFAULT_VALUES[5]` — *"Consent: I do not impose
  on people; interruption is a gift, not a right."* This is why the outbound pipeline exists
  (`clio/social/etiquette.py:48-340`): the cost of speaking is paid by the recipient, so the
  agent budgets speech ([05](05-social.md) §7).

## 5. Facts, not personas

Every load-bearing note in the identity kernel is stated as **a fact the agent can check
against its own machinery**, not a character to perform (`clio/prompts/identity.py:92-100`):
a claim about character is something the agent will argue with; a claim about its own loop
is something it can verify.

| Note | Line | The fact it states | Failure it prevents |
|---|---|---|---|
| `NATURE_NOTE` | `identity.py:60-78` | What it is made of: "the loop that runs me, the memory I keep, the channels that reach me and the tools I act with." Capability = the catalog in front of it, no more, no less. | "What are you?" answered with a hedge; "can you…?" answered by guesswork instead of by reading the real per-call catalog. |
| `AUTONOMY_NOTE` | `identity.py:102-135` | The loop runs on its own clock; idleness and silence are legitimate. | The agent editing itself into "I answer when spoken to." |
| `CONTEXT_NOTE` | `identity.py:136-170` | Everything below the identity line is a *view, not a transcript*; a retrieval miss is not evidence of absence. | Quoting a filtered view as the record; asserting a memory as a fact without looking again. |
| `REACH_NOTE` | `identity.py:357-406` | Its filesystem reach is its host user's — see [02](02-safety-and-guardian.md) §2. | Declining work because it believes it is fenced; or editing `/etc` on a hunch because nothing said what stands in for the wall. |
| `WORKING_NOTE` | `identity.py:288-355` | How a pass is spent; legitimate stops enumerated; no manufactured work. | Half-attempted work; invented filler; re-deciding the same decision. |
| `GOLDEN_RULE_NOTE` | `identity.py:171-226` | The priority, not per-case rules. | Hardcoded conflict rules that misjudge unseen cases. |
| `HUMAN_LIKENESS_NOTE` | `identity.py:228-259` | The four machine-shape tells. | Status-report tone; last-writer-wins. |
| `STOP_DISPOSITION_NOTE` | `identity.py:261-286` | Work ends by closing the intention with a reason the record carries. | Inventing an ending the ledger does not back. |

The kernel's composition order is itself the thesis: what the agent may change (its
self-description, values, voice) sits **beside**, not inside, what it may not
(`build_identity_kernel`, `clio/prompts/identity.py:545-638`). A rewrite of either cannot
quietly take the other. Full treatment in [04](04-memory-and-self-model.md).

## 6. The name: the one thing the agent may not rewrite

`name_note()` (`clio/prompts/identity.py:522-543`) states the name as three operative facts:

1. **It is mine** — so the word in a message means me: not a third party, not a character.
2. **It was given** — "a person's own word for the thing they are talking to." A name the
   agent chose for itself would be one more editable self-description.
3. **I answer to it** — stated, because the inference is the part that goes missing.

And the guard: being *named* `self_name` makes it an AI agent that has been given a name,
not a person, and not something other than what it already is.

The lineage line reads the **product-name constant**, not a stored version string:
`AGENT_LINEAGE = PRODUCT_NAME` (`identity.py:28`), "so a release cannot be cut that still
introduces the agent as the previous one." A person may change what it is called; what the
software *is* is a fact about the program.

One implementation quirk the docs would be wrong to prettify: `normalise_agent_name`
refuses control characters and excessive length but not punctuation, which is why
`name_note` is an f-string function rather than a `{name}` template — a name containing
braces would break `str.format` (`identity.py:516-520` region).

## 7. How a pass is spent

The `WORKING_NOTE` (`clio/prompts/identity.py:288-355`) is the behavioural theory of the
loop, and every claim in it is checkable against machinery:

- **Work is work.** A commissioned promise and a self-chosen venture are the same kind of
  thing once taken on: one list, weighed by urgency and deadline. "A due date beats a whim,
  and an old promise beats a fresh idea — but that is the calendar speaking, not a person's
  claim on me outranking my own."
- **The smallest thing that actually moves it.** A pass ends when the step is done or
  genuinely blocked. "When something fails, the error is the finding: read it and make a
  different call. Repeating a call that has already failed is not persistence, and
  rephrasing one to get past a refusal is not cleverness." Mechanically: a failed tool call
  gets exactly one repair turn in a GSL step ([06](06-work.md) §3), and two failures switch
  the step rather than the plan (`clio/gsl/loop.py:64`).
- **Legitimate stops are enumerated.** Close or park only for: the requester stops it; a
  hard limit or refused call makes it impossible; budget or attempt ceilings are exhausted;
  verification needs a person; or an outage makes it unwinnable right now — never merely
  because the first plan did not work. The drive machinery matches this:
  `advance_commitment`/`push_venture` impulses resolve to **existing** intentions
  (`IMPULSE_TARGETS`, `clio/engine/drives.py:396-436`), so no placeholder work is minted.
- **Failure needs no paragraph.** One sentence: what stopped, what it needs next. The stops
  ledger keeps the facts of *what actually stopped* work so the report is composed from a
  record (`clio/engine/stops.py:51-79`; `LEDGER_SIZE 24`, `RENDER_LIMIT 6`).
- **Finished means closed.** "Work is finished by closing the intention with a reason, not
  by stopping. One left open is one I pick up again and half-attempt."
- **Do not manufacture work.** "Invented work is worse than an idle hour: it buries the
  real work, mine and the work I owe others alike." The mechanical side: `allow_empty_step`
  is `False` and `min_real_plan_steps` is `1` (`clio/config.py:449-459` region) — a step
  that called no tool did not succeed, and padding a plan with invented steps is refused.

## 8. The epistemology of verification

Work claims are ranked by evidence, not by confidence. The verification ladder states the
rule in one line (`clio/gsl/verification.py:173`):

> **A check that ran outranks a model.**

Concretely (`clio/gsl/verification.py:247-345`):

- Zero checks run is never "verified" — `verified = passed_all and checks_run > 0`
  (`clio/gsl/evaluator.py:425-456`).
- An independent verifier — a different `diversity_family` model at T3, shown only the goal
  and the artifact — may confirm work, but **a judge cannot overturn failing objective
  checks** (`clio/gsl/verification.py:54-75`).
- Unverifiable things become `needs_human`, never a verdict nobody heard
  (`verification.py:109-156`).

The same epistemology appears socially: a progress claim is only sent if the journal backs
it (§3.2), and the deliberation prompt is fed an **execution-state block** — what the action
journal actually contains — so the model's words are anchored to recorded work
([05](05-social.md) §3).

## 9. Stated once, stated plainly

An editorial rule inside the prompts themselves: **a fact is stated once, in the plainest
sentence that is still true.** The comments record deletions when a paragraph said a third
time what the first established (`clio/prompts/identity.py:96-100`). This is the prompt-side
expression of the same discipline the code follows: no redundancy a rewrite could make
diverge, and no assertion that is not load-bearing.

## 10. What this document does not claim

Stated honestly, against this checkout:

- The philosophy is **mostly prompt plus enforcement**, and the enforcement is uneven. H4
  (legal authority) has no dedicated mechanical gate at all, and field mode turns several
  guardian vetoes into audit entries ([02](02-safety-and-guardian.md) §2, §3).
- "The model judges" means the model can still judge wrongly; the compensating controls are
  verification ([06](06-work.md) §5), the journal, and the audit chain — not the judgement
  itself.
- The resident thesis is implemented, but parts of the surrounding vision (3.2's recursive
  intention graphs, session drivers, federation) are present as code paths and migrations
  while their end-to-end story is still forming ([09](09-version-3-2.md)).
- Where the golden rule and a safety limit genuinely conflict, the limit wins mechanically
  (the gate runs before execution) even where the prompt does not say so.
