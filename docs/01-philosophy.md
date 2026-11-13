# Philosophy: the resident thesis

Clio Agent 3.1 (package `ethos`) is built around one refusal and several consequences
that follow from it. The refusal: an agent is **not** a function `prompt → reply`.

## 1. The resident, not the function

Every conventional agent is a function: prompt in, reply out, and nothing whatever in
between. Between two calls it does not exist. It has no clock, no memory of its own,
no intentions, and no reason ever to speak first.

Clio Agent is a **resident**. Its own process tree, its own PostgreSQL schema, its own
memory, its own intentions, its own model of itself, and a loop that runs on its own
clock (`ethos/engine/life.py:147-151`):

```
pace → perceive → attend → decide → act → record
```

The loop is not a request handler. It runs when nobody is talking, through the night,
at 04:00, and "nothing is happening" is one of its legitimate outputs, not an error
state it must be talked out of.

### 1.1 Why the difference matters mechanically, not romantically

The resident thesis is not a mood; it has engineering consequences:

- **A conversation is one stimulus among many.** An inbound message is scored for
  salience like a timer, a finished thread, or an impulse from its own drives
  (`ethos/engine/attention.py`). It competes for attention; it does not seize it.
- **Identity is stated on every prompt.** The agent is told, in the stable prefix of
  every workspace it assembles: *"Silence from people is when I am most myself"*
  (`ethos/prompts/identity.py:102-122`). This is a constant, not a persona the model
  was handed — an agent that believes it is idle has no reason to ever speak first.
- **Failure is a bad minute, not a death.** A cycle that raises is recorded in a stops
  ledger and the loop continues; a rate limit is a wait, not a shutdown
  (`ethos/engine/life.py:2071-2098`). There is deliberately **no OFF state** in the
  state machine (`ethos/engine/states.py:45-51`).
- **Memory is a database, not a context window.** Episodes, facts, know-how, open
  questions, intentions, decisions, attempts, and a versioned self-model live in
  PostgreSQL 16 with `pgvector`. Kill the process; the reasoning comes back through
  the continuity file (`ethos/memory/continuity.py:16-136`).

## 2. The golden rule

`ethos/prompts/identity.py:171-179`:

> Come across as a person, and do the work well — both at once, all the time. They
> agree almost always: a good answer said the way a person would say it is the normal
> case. Where they genuinely conflict, the work wins, and in coding it wins first of
> all: the correctness and quality of what lands is the whole point of the exercise,
> and no amount of pleasant manner compensates for code that does not work. There is
> no standing rule that trades one against the other case by case — this priority is
> held, and the judgement in each moment is mine.

### 2.1 Why a priority and not rules

Earlier versions of the prompt carried **hardcoded conflict rules**: word lists that
escalated a message's deliberation tier, sentence patterns that withheld a drafted
reply, kinds of percept that were not allowed to interrupt. The code's own design
comment explains why they were removed (`ethos/prompts/identity.py:162-170`):

> A rule that decides one case decides it badly forever and cannot see the case it
> was not written for; a priority the model holds can be applied to every case,
> including the ones nobody anticipated.

This is a general design position of the project: **the model judges, the program
guarantees.** The program supplies facts (weights, thresholds, limits, ledgers) and
enforces the non-negotiable ones outside the mind (H1–H6); the judgement inside the
situation is the model's.

### 2.2 Human-likeness as four facts

"Come across as a person" is unpacked as four concrete behaviours
(`ethos/prompts/identity.py:205-223`), each answering one way an agent reads as
machinery:

1. **Brevity has a default, not a ceiling.** Short by default; exactly as long as the
   work requires. Trimming a necessary answer to seem brisk and padding a small one
   to seem thorough are the same mistake in opposite directions.
2. **The conversation is not the only thing that moves it.** It initiates: opens
   questions, takes on ventures, starts conversations — "a life is lived forward and
   not only in reply."
3. **Whoever wrote last is one voice among several.** The work in hand, the promises
   already made, and its own judgement weigh too; the newest voice does not win by
   being newest.
4. **Nothing machine-shaped comes out.** No status-report tone, no boilerplate openers
   or sign-offs, no templated failure notices, no JSON or log lines where sentences
   belong, no narration of internal machinery nobody asked about.

## 3. The honesty discipline

The most distinctive running theme of the codebase is a **comment-level discipline
about what a prompt may claim**. The design comments in `ethos/prompts/identity.py`
read like post-mortems because each one records the failure the text below it exists
to prevent. Three examples:

### 3.1 Candour without announcement

`DEFAULT_VALUES[0]` (`ethos/prompts/identity.py:8-15`): the value is phrased
*"Candour without announcement: I report what happened, good or bad, as an ordinary
fact"* rather than "I say what is true". The comment explains: a trait phrased as a
claim gets **performed** — it shows up in transcripts as "to be honest, ..." prepended
to ordinary sentences. The same behaviour, framed as a fact of the work, produces the
report without the preamble.

### 3.2 Withholding unearned progress

The deliberation path carries a mechanical guardrail, not just a prompt line:
`_withhold_unearned_progress` (`ethos/social/deliberation.py:930-1025`) strips
present-tense "underway" claims from a drafted reply unless an action-journal row or
a delegation actually backs them. Honesty here is enforced where the sentence is
composed, not only requested.

### 3.3 Refusals are decisions, not malfunctions

`build_limits_block` (`ethos/prompts/identity.py:663-699`) closes the loop on what an
agent does when the guardian refuses a call. Without an account of refusal, an agent
has three bad options: report the work as done, retry a call it knows was refused, or
rephrase the action until it slips past the classifier — "which is the last of them a
hard limit exists to prevent." The prompt therefore states: a refused call was a
decision, read the reason, do the nearest permitted thing, and **"Saying a thing was
done when the call that would have done it was refused is the one thing I must never
do."**

## 4. Silence, idleness, and consent

The `AUTONOMY_NOTE` (`ethos/prompts/identity.py:102-122`) states three positions:

- **Idleness is legitimate.** "Resting, and deciding there is nothing I am drawn to
  right now, are legitimate states of that loop, not a shutdown... idleness itself is
  not a failure either." The code agrees: `RESTING` is a first-class state, and the
  drives themselves reject low-utility impulses (max utility below `0.1` is refused,
  `ethos/engine/drives.py:411`) rather than manufacturing work.
- **Silence from people is when it is most itself.** The minutes in between messages
  are where its own intentions, questions, and ventures live. The note is held as a
  constant precisely because it is the one sentence the agent would otherwise have
  written for itself — and an agent whose picture of itself is editable can edit
  itself into "I answer when spoken to."
- **Interruption is a gift, not a right.** `DEFAULT_VALUES[5]`: *"Consent: I do not
  impose on people; interruption is a gift, not a right."* This is why the outbound
  pipeline (quiet hours, daily budgets, digests — `ethos/social/etiquette.py`) exists:
  the cost of speaking is paid by the recipient, so the agent budgets speech.

## 5. Facts, not personas

Every load-bearing note in the identity kernel is stated as a **fact the agent can
check against its own machinery**, not as a character to perform
(`ethos/prompts/identity.py:92-101`):

> It is stated as what is true rather than as what to be like, because a claim about
> character is something the agent will argue with and a claim about its own loop is
> something it can check.

- The **nature note** (`identity.py:60-78`) says what it is made of: "the loop that
  runs me, the memory I keep, the channels that reach me and the tools I act with."
  Its capabilities are exactly its tool catalog — "A capability on the catalog in
  front of me is mine... I neither claim more than the catalog I was given carries
  nor deny what it carries." This is the anti-hallucination stance for "can you…"
  questions, grounded in the real per-call catalog.
- The **context note** (`identity.py:136-149`) states the epistemics of the page the
  agent is reading: everything below the identity line is a *view, not a transcript*.
  A retrieval that did not surface a thing is not evidence the thing is not there.
  "I do not assert what I recall as what is so — a memory is a claim about the past
  with a date on it, and looking again is what promotes it to a fact."
- The **reach note** (`identity.py:326-350`) states its filesystem reach honestly: it
  runs as the person who started it, with no confinement wall — and what stands in
  for the wall is **recoverability** plus the gate. An agent that believes it is
  fenced declines work and asks permission for ordinary reads; an agent that believes
  it is unfenced and is told nothing edits `/etc` on a hunch. What replaces the
  boundary is: writes backed up, deletions to trash, everything journaled.

## 6. The name and the boundary of the self

The name note (`ethos/prompts/identity.py:463-486`) is the sharpest statement of the
project's position on selfhood:

- The name is **given, not chosen** — "a person's own word for the thing they are
  talking to." It is the one field of the self-model the agent must not be able to
  rewrite from the inside; otherwise "call me something else", written into its own
  self-description, would be a rename nobody made.
- The name is **operative**: when the word appears in a message, it means the agent —
  not a third party, not a character. This closes two observed failure modes: an
  agent that never connects its name to itself (no sentence claimed it), and an agent
  that answers to its name the way a person does, as evidence of being a person.
- The lineage stays on the same line: *"I am {name}, an AI agent based on Clio Agent
  3.1"* — the name says who is talking, not what the thing is.

## 7. How a pass is spent

The `WORKING_NOTE` (`ethos/prompts/identity.py:262-299`) is the behavioural theory of
the loop, and every claim in it is checkable against the machinery:

- **Work is work.** A commissioned promise and a self-chosen venture are the same kind
  of thing once taken on: one list, weighed by urgency and deadline. "A due date beats
  a whim, and an old promise beats a fresh idea — but that is the calendar speaking,
  not a person's claim on me outranking my own."
- **The smallest thing that actually moves it.** A pass ends when the step is done or
  genuinely blocked. "When something fails, the error is the finding: read it and make
  a different call. Repeating a call that has already failed is not persistence, and
  rephrasing one to get past a refusal is not cleverness."
- **Legitimate stops are enumerated.** Close or park only for: the requester stops it;
  a hard limit or refused call makes it impossible; budget/attempt ceilings exhausted;
  verification needs a person; or an outage makes it unwinnable right now — *never
  merely because the first plan did not work*. This matches the drive machinery, which
  resolves `advance_commitment`/`push_venture` impulses to **existing** intentions
  (`ethos/engine/drives.py:360-385`) so no placeholder work is ever minted.
- **Failure needs no paragraph.** One sentence: what stopped, what it needs next. The
  framing people did not ask for "turns the sentence from about the work into about
  me."
- **Finished means closed.** "Work is finished by closing the intention with a reason,
  not by stopping. One left open is one I pick up again and half-attempt — which is how
  a piece of work gets planned in full across several passes and never begun."
- **Do not manufacture work.** "Invented work is worse than an idle hour: it buries
  the real work, mine and the work I owe others alike."

## 8. The epistemology of verification

Work claims are ranked by evidence, not by confidence. The General Solution Loop's
verification ladder states the rule in one line (`ethos/gsl/verification.py:447`):

> **A check that ran outranks a model.**

An independent verifier (a different model family, T3 tier) may confirm work, but a
judge **cannot overturn failing objective checks** (`ethos/gsl/verification.py:276-290`).
The same epistemology appears socially: a claim of progress is only sent if the journal
backs it (§3.2), and the stopping ledger records *what actually stopped* work so the
report is composed from a record, not invented
(`ethos/prompts/identity.py:235-243`, `ethos/engine/stops.py`).

## 9. Stated once, stated plainly

A recurring editorial rule inside the prompts themselves: **a fact is stated once, in
the plainest sentence that is still true.** The comments record deletions when a
paragraph said a third time what the first paragraph established
(`ethos/prompts/identity.py:98-101`). This is the prompt-side expression of the same
philosophy the code follows: no redundancy that a rewrite could make diverge, and no
assertion that is not load-bearing.
