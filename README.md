<p align="center">
  <img src="ethos.png" alt="Clio Agent 3 Alpha" width="880">
</p>

<h1 align="center">Clio Agent 3 Alpha</h1>

<p align="center">
  <b>An agent that is there when you aren't.</b><br>
  Not a function that maps a prompt to a reply.
</p>

<p align="center">
  <code>3 Alpha</code> · <code>released 2026-10-03 JST</code> · <code>Python ≥ 3.12</code> ·
  <code>PostgreSQL 16 + pgvector</code> · <code>Linux / macOS</code> ·
  <code>no license yet — all rights reserved</code>
</p>

---

## The part nobody expects

**23:40.** You went to bed eleven hours ago. You have not spoken to it since.

It is not waiting. It is holding an intention it opened by itself, with a deadline nobody set; a
question it has been carrying since Tuesday; and a shell incantation it has now typed eleven
times this week. Some time before dawn it writes that last one down as a tool, so it never has
to type it again.

**02:15.** A quiet hour. It does not manufacture work to fill it — that rule is in the kernel,
not merely wished for. It re-reads yesterday instead, notices that one of its own replies
promised something it never built, and goes and builds it.

**04:00.** Consolidation. Episodes, facts, know-how, the questions still open, the decisions it
made and why, and a new version of the model it holds of itself. Then the rhythm turns over and
the day starts again.

**07:02.** It has been awake for hours. It never needed you to start it.

> *Not a transcript — this is what the loop is for. One pass of `life()` is
> `pace → perceive → attend → decide → act → record`, and it runs on a clock of its own.*

Every other agent is a function: prompt in, reply out, and nothing whatever in between. This one
is a **resident**. Its own computer, its own memory, its own intentions, its own sense of time.

And it is told so, on every prompt it runs: *"Silence from people is when I am most myself."*
That is not a persona it was handed. It is a constant in the identity block, because an agent
that believes it is idle has no reason to ever speak first — and it is the sentence it would
otherwise have written for itself.

## What that buys you

- **It thinks when nobody is talking to it.** A cycle per pass, whether or not you are there —
  and a cycle that raises is a failed cycle, not a death, so a rate limit is a bad minute rather
  than the end of the agent.
- **It has no bedtime.** There is no hour at which it stops, so work due at 04:00 is work due.
  Energy follows a daily rhythm with a floor instead of a countdown to a bed it never goes to.
- **It has a computer.** Shell, files, code kernels, browser, desktop, HTTP — plus a toolshed
  where the tools it wrote itself live, so the eleventh repetition becomes a capability.
- **It remembers across restarts.** Episodes, facts, know-how, open questions, intentions,
  decisions, and a versioned self-model, in PostgreSQL 16 with `pgvector`. Kill the process; the
  reasoning comes back.
- **It talks as an equal.** It may answer, answer later, ask, acknowledge, work silently, or
  decline with a categorized reason. It also tells *chose silence* apart from *the model died
  mid-answer*, because those two are indistinguishable on the wire and call for opposite things.
- **It is safe by reversibility, not by refusal.** Deletions go to trash, writes are backed up
  first, every action is journaled, and six hard limits (H1–H6) are enforced outside the mind.
  Almost anything it does, you can undo from the interface.

> **Golden rule:** human-likeness and performance are both mandatory. Neither is sacrificed for
> the other.

## Run it

```bash
pip install -e .     # once
ethos up             # every time
```

`ethos up` is the whole command. It brings up Postgres, applies migrations, seeds a self-model,
installs and builds the interface, starts seven supervised processes, waits until the agent has
actually answered for itself, and opens the page. Idempotent — run it on a live agent and it says
so and leaves it alone.

Then the agent keeps running in the background and survives the terminal that started it. Close
the tab, close the laptop, come back tomorrow morning: the thinking did not stop, and it will
have something to tell you.

`ethos` works from any directory: it finds the checkout, the configuration and the database from
the installed package rather than from where you happen to be standing. `./start.sh` still works
and does the same thing, but it is **deprecated** — it creates the virtualenv (which needs `uv`,
and so cannot be done from inside an installed package) and then runs `ethos up`.

**Alpha software**, and it runs on your machine, on your accounts, with your keys. Read
[Safety model](#safety-model) before pointing it at anything you care about — and read
[Filesystem reach](#filesystem-reach), which is the most load-bearing paragraph in this
document and the one most projects would have left out. The parts of this README that read like
a post-mortem are there because a claim in it should be true of the code at the commit it lands
in.

## What's in here

This is a long document and the length is the point — the interesting decisions are the ones
that were hard to get right.

| | |
|---|---|
| **Start** | [Status and expectations](#status-and-expectations) · [Requirements](#requirements) · [Installation](#installation) · [Quickstart](#quickstart) |
| **Use it** | [The interface](#the-interface) (a browser and a terminal, not a chat box with a dashboard) · [Talking to it from somewhere else](#talking-to-it-from-somewhere-else) (Telegram, WhatsApp, Slack, Discord, email, webhooks) |
| **Understand it** | [How it runs](#how-it-runs) (the seven-process model) · [Repository layout](#repository-layout) · [Configuration](#configuration) |
| **Trust it** | [Safety model](#safety-model) — the six hard limits, and an exact account of what is *not* enforced |
| **Build on it** | [Tests](#tests) · [Deployment](#deployment) · [Contributing](#contributing) · [License](#license) |

## Status and expectations

This is **Alpha** software. It is opinionated, it is actively developed, and interfaces and
configuration keys will change between releases. It is designed to run on **one machine you
control**, on **your** accounts, with **your** keys. Read [Safety model](#safety-model) before
pointing it at anything you care about.

Two things about the scope are worth stating plainly up front:

- **The interface is optional, and lives in `interface/`.** The agent runs and is fully usable
  headless; the browser and terminal interface is a directory in this repository you install only
  if you want it. See [The interface](#the-interface).
- **Nothing talks to a provider until you give it a key.** With no keys configured the agent still
  starts, still journals, and still runs its reflex rules — but every model call fails loudly at
  the gateway. See [Configuration](#configuration).

## Requirements

| | |
|---|---|
| Python | 3.12 or newer (`pyproject.toml` sets `requires-python = ">=3.12"`) |
| PostgreSQL | 16 with the `pgvector` and `pg_trgm` extensions — [installed for you](#postgres-brought-up-not-assumed), or point `ETHOS_DSN` at your own |
| Node.js | 20 or newer — **only** for the interface bridge and `interface/`. The agent runs without it. |
| Platform | Linux and macOS are the tested paths. `scripts/ensure_postgres.sh` uses Homebrew, so the no-Docker path is macOS-first; on Linux use Docker or a local cluster. |

Two dependencies download more than a wheel:

- `playwright` needs `playwright install chromium` (and its system libraries) before the browser
  tools do anything.
- `fastembed` fetches an embedding model on first use. The default is `BAAI/bge-large-en-v1.5`
  (`config/ethos.yaml`), which is roughly 1.3 GB. Point `memory.embedding.model` at something
  smaller if that matters to you.

`ethos up` installs nothing itself — it expects the package to be installed already, which is what
[Installation](#installation) below does in one command. In practice the only thing left to
install by hand is PostgreSQL, and even that has a fallback.

## Installation

```bash
git clone https://github.com/AInohogosya/Ethos.git
cd Ethos
pip install -e .          # or: python3.12 -m venv .venv && .venv/bin/pip install -e .
ethos up
```

That is the whole installation. It needs Python 3.12+ on `PATH`; `uv` is convenient but not
required, and the two commands above are the ones the rest of this README assumes. Everything
after that — Postgres, migrations, the interface, the agent — is `ethos up`'s job.

Settings go in the environment or in a `.env` file; `.env.example` lists every variable there
is, and `ethos` reads `./.env`, the checkout's `.env`, or `$ETHOS_ENV_FILE` on any invocation. A
value you export wins over the file, so `ETHOS_DSN=… ethos up` does what it says.

## Quickstart

One command, from the checkout root:

```bash
npm start
```

That is the whole setup. It needs Node, and it does the rest:

1. installs the bridge's dependencies and builds the interface, if either is missing;
2. creates `.venv` and installs the agent into it, if there is not one — with `uv` when you have
   it, `python3 -m venv` when you do not;
3. ends the session already running, if there is one — the agent's supervisor, then whatever is
   holding `8720` — so that what follows is a new session rather than an old one reported as new;
4. runs `ethos up` — Postgres, migrations, seeds, supervisor, wait for a sign of life;
5. checks that the page is really being served, and serves it in this terminal if nobody else is.

Then it prints the URL and exits. The agent keeps running in the background, the way it is supposed
to, so the page keeps answering after you close the terminal.

You do not need an activated virtualenv, and `ethos` does not need to be on your `PATH` — this
command calls the venv's interpreter by absolute path. That is on purpose: the people who need a
one-command start-up most are the ones whose shell has neither.

Running it twice is a restart, not a second agent. A session already in existence — the supervisor and
whatever is holding the page's port — is ended before anything new is started, so what `npm start`
reports as running is what it just started. Use `--keep` to leave a running session alone; the only
thing it will still stop is a port held by something that *cannot* serve the page, because that is
the one state that quietly makes everything else pointless.

```bash
npm start                   # everything, then open the browser
npm start -- --no-open      # the same, without launching a browser
npm start -- --interface    # the page alone, in the foreground, no agent
npm start -- --keep         # do not end a session that is already running
npm run build               # build the interface and stop
```

`--interface` restarts the page but not the agent: it asks for the page alone, and taking an agent
down would be a worse surprise than the page it replaces. The interface-only mode is for working on
the interface itself. The agent is a separate process and an optional participant in this one, so the
page works fine without it.

### The commands underneath

`npm start` is a wrapper, and each thing it does is a command you can run yourself:

```bash
ethos up              # the default: prepare, start, wait, open
ethos status          # is the agent running, and what is it saying
ethos stop            # stop the agent and everything it supervises
ethos logs            # follow the supervisor's log and each process's own
ethos doctor          # what is configured, what is missing, what is unreachable
ethos up --no-open    # do not launch a browser when the agent is up
ethos up --wait 300   # wait up to 300s for the agent (default 180, or $ETHOS_AGENT_WAIT_S)
ethos --help          # every command and flag
ethos --version
```

`ethos up` brings up Postgres, applies migrations, seeds the self-model, installs and builds the
interface, starts the agent under the supervisor, waits until the agent has actually answered for
itself, and then opens the page. It is idempotent: run it again on a running agent and it says so
and leaves it alone. `npm start` ends a running agent first, so the one it starts is a new one.

`ethos up` exits 0 when the agent is up, 1 when it is not, and 130 on Ctrl-C — in which case the
agent it started is still running, because it is started in its own session on purpose. `ethos
status` exits non-zero when the agent is not there, which is what a launcher and a script want.

The agent runs in the background and survives the terminal that started it, so the page keeps
answering when that terminal is closed.

> **`ethos` itself has to be on your PATH.** In a virtualenv it is only there once that virtualenv is
> active — `source .venv/bin/activate`, or call `.venv/bin/ethos`, or `make up`, which uses the
> checkout's own interpreter and needs neither. A shell where `ethos` is not found has not got a
> broken install; it has got a command that was never on the PATH. This is why `npm start` and `make`
> exist: neither needs it.

> **Deprecated:** `./start.sh` still does all of this, and still creates the virtualenv with `uv`
> for you, but it is a shim that runs `ethos up` and prints a notice saying so. Use `npm start`,
> which does this and the interface.

### Running it yourself

```bash
ethos supervisor                  # the full process model, in the foreground
ethos core                        # everything embedded in one process (dev)
ethos core --listen-channels      # …and holding the channel doors too
ethos status                      # liveness check
ethos doctor                      # what is configured, what is missing
ethos stop                        # ask the supervisor to end
ethos version
ethos db upgrade                  # apply migrations
ethos db seed                     # seed the self-model
node web/server/index.mjs         # the bridge alone, with no build step in front of it
```

`python -m ethos …` is the same command and always works, including before `pip install -e .`
from inside the checkout — the console script and the module form are one entry point.

`make` wraps the common ones: `make up`, `make run`, `make run-dev`, `make status`, `make stop`,
`make logs`, `make doctor`, `make migrate`, `make seed`, `make db-up`, `make db-down`, `make test`,
`make lint`, `make clean`. The rest are `make install`, `make db-status`, `make test-unit`,
`make test-integration`, `make test-scenarios`, `make test-bridge`, `make interface-install`,
`make interface-build`, `make interface-dev`, `make interface`, `make tui` and `make agent`; the
list is in the `Makefile` itself. `make` with no arguments is not a listing — its default target
is `up`.

### What `status` actually decides

`ethos status` answers one question — *is the agent there* — and it decides from the agent's own
sign of life rather than from a pid, a port, or a file left behind by the last run. A machine with
nothing running on it looks completely healthy from every other angle.

The primary signal is the timestamp of the last `presence.update` row in the agent's own `events`
table: if the agent has not published a sign of life in **15 minutes**
(`ethos/core/status.py`, `AGENT_ABSENT_S`), it is reported absent. The agent's own recorded stop
in `$ETHOS_HOME/data/continuity.json` also counts, and counts against it. The interface applies the
same 15-minute window before it tells somebody the agent is not running, so this command and the
browser cannot disagree about the same machine at the same moment.

Node is the interface's toolchain and not the agent's: without it the agent still runs and the
bridge still answers its whole API, and the supervisor logs that it skipped the bridge rather than
failing.

### Postgres: brought up, not assumed

Postgres is the one hard dependency, and `ethos up` will set it up if it is missing — you do not
have to install anything first. `scripts/ensure_postgres.sh` is idempotent and tries, in order:

1. **an existing database** at `$ETHOS_DSN` — if one is already there, it is left alone;
2. **Docker** — `docker compose up -d db` (`pgvector/pgvector:pg16`), when the daemon runs;
3. **a local PostgreSQL 16** — installed from Homebrew if needed, with pgvector built against it, a
   fresh cluster in `data/pgdata`, the `ethos` role and database, and the `vector` and `pg_trgm`
   extensions. It listens on `127.0.0.1:5433` only.

The first run of that path installs Homebrew packages and compiles pgvector once, which takes a few
minutes; after that startup is a no-op. To manage the cluster by hand:

```bash
scripts/ensure_postgres.sh status     # is it running, is anything listening
scripts/ensure_postgres.sh stop       # stop the local cluster
scripts/ensure_postgres.sh up         # start it again
```

`ETHOS_PG_MAJOR` (default 16), `ETHOS_PGVECTOR_VERSION` (default 0.8.6) and `ETHOS_PGDATA`
(default `data/pgdata`) tune it. `ETHOS_PG_READY_TIMEOUT` bounds how long it waits for the cluster.

The cluster is gitignored local state. `make clean` deliberately leaves `data/pgdata` alone because
it holds the agent's memory; to throw it away, `make db-down && rm -rf data/pgdata`.

## The interface

The interface — **Clio Agent 3 Alpha** — is a browser page and a terminal client. It is not a dashboard
bolted onto a chat box: the agent's state and the conversation are one screen, because an agent that
thinks when nobody is talking has to be watchable while it does.

It lives in `interface/`, a directory of this repository, and the bridge in `web/` serves its build.
`npm start` from the checkout root installs and builds it if that has not happened yet, so the
commands below work on a fresh clone without a separate install step:

```bash
npm start -- --interface                                          # the page alone, no agent
open http://127.0.0.1:8720                                         # …in a browser
npm run tui --workspace @project-phone/cli --prefix interface      # the agent, in a terminal
phone agent --json                                                 # or from a script
```

`make agent`, `make tui`, `make interface-build` and `make interface-dev` are the same things from
the Makefile. Override the port with `ETHOS_WEB_PORT`, or with `channels.web.http_port` in
`$ETHOS_HOME/channels.yaml` — and to run a second interface beside a running one rather than
against it, `ETHOS_WEB_PORT=8721 npm start`.

If `interface/` cannot be installed or built, everything else still works: the agent runs and the
bridge answers its whole API. The page itself is then a 503 that says so, rather than a 404 that
looks like a broken link.

The bridge holds no state, and it is not started by anything you have to remember: the supervisor
runs it as an *optional* child, and `npm start` starts one directly when you want the page without
the agent. Because that child is optional, the supervisor will not bring it back once it has exited
— so if the page ever stops answering while the agent is still running, `npm start -- --interface` is
the fix: it replaces the bridge and leaves the agent alone.

The bridge holds no state: it serves the interface's build, reads the agent's own tables, and turns a
command from a surface into an `events` row — the same bus every channel adapter publishes on. So a
browser, a terminal, and a Telegram message arrive at the life loop by one road and are scored by one
attention model.

**With no agent running, the page is showing you the last run.** The bridge reads the agent's
tables, so a transcript and a presence it displays may be hours or days old. `ethos status` is
what answers "is it there".

What the interface shows: what the agent is doing (presence and its focus), what it is thinking,
what it intends as a tree of goals with deadlines and budgets, what it has done and which of it can
be undone, what it has spent against the caps the gateway enforces, and what the guardian is
protecting. What it can do: send a message, hold / resume / stop / emergency-stop, queue an undo
(`/api/actions/:id/undo`), ask the agent to close an intention (`/api/intentions/:id/close`), open
and close doors, and give the agent a name. It cannot set an intention's status or edit an action —
those are the agent's decisions, and a surface that could make them would be a second, unreviewed
writer to the thing it exists to show.

### A decline is a decision, and a broken link is not

The transcript keeps these apart. A question the agent *declines* stays in the transcript with a
reason on the screen. A deliberation that *fails* — no model, a provider that raised, an answer that
cannot be read at all — is recorded as a turn with no decision in it, which reads differently and
does not count
against the person's streak. It says so in a field of its own, `decided`, which travels on the
`message.handled` event; a surface reads the absence of a decision as the failure it is and says
"The agent could not answer" rather than "The agent chose not to reply", because both arrive as
`act_silently` with no outbound row behind them and the two call for opposite things — one needs
nothing, the other is worth sending again. A message nothing decided about is **not** retired: the
attempt is counted against it and it goes back on the queue, so a model that recovers answers
without the person asking twice, bounded by `MAX_DELIBERATION_ATTEMPTS`.

The case that is hardest to see from outside is a model that runs out of output room mid-answer.
Its JSON has no closing brace. Rather than throwing the whole answer away, the finish reason is
read and the members that closed are kept: a member the model finished writing is a decision it made.
What it was still writing when it ran out is dropped rather than finished for it, so a half-sentence
is never delivered — the turn ends as the silence it turned out to be, with the reason saying the
answer was cut off rather than that it was declined.

### The answer, when it arrives as prose instead of as the object

A deliberation asks for one small JSON object. On a short message the model writes it. On a long one
it very often does not: it reads the person's words, answers *them*, and writes none of the four
fields around the words. Nothing about the prompt is wrong — the object and every field in it are
set out in full at the top of the system turn — but that block is not the last thing in the prompt.
The turn after it is the person's message, which on a long one tends to end in a question addressed
to the model ("give it a try, or counter the claim"), so the model answers that.

So there are three things, in order of how much they matter:

- the contract is **restated after the message** (`DELIBERATION_OUTPUT_CONTRACT`), so the thing the
  model has to produce is the last thing it reads rather than the first. Measured against the base
  model on the 2 600-character message that produced this complaint: answered in prose 5 times in 14
  without it, 0 times in 24 with it;
- the **second ask is not the first ask again**. More of the ceiling cannot fix an answer of the
  wrong shape — a reply in prose is not short — so the escalation now also restates the contract at
  the end of the prompt, and says the last one could not be read;
- if it still comes back as prose, **those words are used**. They are the model's own, they were
  written for that person, and they are the only reply in the response, so throwing them away is what
  put "the model's answer could not be read; nothing said in its place" in front of somebody who
  had been answered in full. They are delivered as the reply, with nothing invented around them.

The third is a floor, not a preference, and the reason it is reached only after a second ask is
fidelity. A reply recovered from prose carries no `creates_intention` and no `intention_spec`, so an
"I'll do it" recovered that way is a promise with nothing behind it — the one failure the
deliberation prompt spends paragraphs preventing. So the object is asked for again first, and a
model that produces one is believed over the prose it wrote a moment earlier. What a recovered reply
does carry is a `reason` saying it was recovered, so a decision read back later is not mistaken for
one formed in the shape that was asked for.

Two shapes are still *not* read as prose, because they are the model addressing the protocol and
getting it wrong rather than answering the person: an answer that is one JSON object with nothing in
it to send (`{}` is not a sentence, and neither is `{"note": "still deciding"}`), and an answer the
provider stopped mid-word, whose surviving words are half of what was being said.

### Three ways a long message was answered in silence

The report this section answers is specific: long, technically loaded prompts, near enough always,
arriving as *"the agent chose not to respond · the model's answer could not be read"*. The reply was
in the response every time. Four separate causes, three of them structural, and the first one had
nothing to do with the reply at all.

**A `$` chose the tier, and then nothing could serve it.** Consequentialness was a substring test, so
one JSON key — `"$schema"` — put a question about config files into T3. T3 has the highest floor in
`models.yaml`, and a deployment that configured a base model has exactly one entry above it, because
every other T3 entry wants a key that deployment has not got. A single `$` therefore selected the one
tier with nothing able to serve it, on the message being *about* technical work. Longer messages
contain more `$` and are more likely to trip the other markers inside longer words: `payload` is
`pay`, `design` is `sign`, `contractor` is `contract`. The escalation fired most reliably on exactly
the prompts it was meant to treat gently. It is now `is_consequential_text`: word boundaries for the
words, and a `$` only in front of a number. `life` had a second, narrower copy of the same list for
choosing the tier it hands the deliberation; both call one function now.

**A circuit breaker in cooldown was reported as a permanent fact.** `GatewayService.complete`
selected a model *before* the retry loop, and `NoModelAvailable` propagated out of the whole call —
no attempt, no backoff, no retry. But a breaker is a timed state: `cooldown_s` is 30 seconds and
then it is over. So a burst of provider failures (a flaky uplink, a provider shedding load, five
failures inside the 60-second window, which is not many) turned into half a minute in which every
request at that tier failed instantly while the caller was told no model could answer. With one model
configured — which is what a base model *is* — "all eligible models" and "the only model" are the
same set, so there was nothing to fail over to and the window covered everything. And T3 is the tier
consequential messages are deliberated at, so the questions that most needed answering were the ones
that could not be asked. `NoModelAvailable` now carries `transient`, the selection moved inside a
retry that waits the cooldown out, and a tier that nothing serves still fails immediately — because
sitting out a backoff curve to arrive at the same answer six times is the other way to be wrong.

**An object with the wrong keys was discarded.** A deliberation can come back as prose, cut off, or
as one JSON value that is not the object it asked for. The third had no recovery, and a long technical
message is the case most likely to produce one: it is full of JSON, so the shape that comes back is
often the model holding the *subject* — `{"permission": {...}}`, `{"changes": [...]}` — rather than
the decision. The words are used now, under the keys that mean *the words to send the person*
(`reply`, `response`, `answer`, `message`, `text`, one level down), on the same terms as prose: the
model's own words, nothing invented, and no work commissioned out of an answer with no
`intention_spec` behind it. `note`, `summary` and `body` are deliberately not on that list —
`{"note": "still deciding"}` delivers the agent's working instead of an answer, which is the same
mistake as sending braces.

**And the agent told its owner it could not write a file.** "I still don't have a filesystem-writing
tool exposed in this chat, so I can't create Test.md" — four times, from an agent with `fs.write`,
`fs.edit` and `shell.run` registered. The deliberation prompt said only that the agent "has tools for
files, shell, the browser and the network", and a deliberation is asked to write the words the person
will read; with no tool named, the model reasoned about the turn it was in rather than the agent it
was speaking for and reported its own prompt as its own capabilities. It is false, it is confident,
it is in the only channel the person can see, and it is self-infirming — an owner told the agent
cannot create a file stops asking it to. The deliberation is now handed the real tool names from the
toolhost (`tool_names_line`), told that its reply is written before the work happens, and told
plainly not to report a limit it does not have.

### `2>&1` killed every shell command, and then the cycle that ran it

`GuardianGate` classifies a shell command before the tool is allowed to do anything, and
`extract_paths` read `node.output.word` off the redirect target unconditionally. `bashlex` does not
always put a `Word` there: a redirect that duplicates a descriptor — `>&1`, `2>&1`, `>&2` — carries
the descriptor number as a plain `int`. So the classifier raised `AttributeError` on the tail of
nearly every build, test and lint invocation a person or an agent writes.

It raised in the worst possible place. Nothing caught it on the way out, so it unwound
`toolhost.execute` → `_execute_step` → `advance` → `_advance_goal` → `act` → `_cycle`, and the whole
life cycle died for that turn. The intention kept the focus it crashed on, so the next cycle re-ran
the identical failing step, and the one after that. The agent produced neither work nor words for as
long as the fault lasted — which from the outside is an agent that cannot do the thing it promised,
and on this deployment it was the live failure in `ethos-core.log` minutes before any of this was
looked at.

Two fixes, because one is not enough. The classifier skips a descriptor, which is not a path, and
stops recording `&1` as one — the list is not decoration, `GuardianGate._review_destructive` walks it
looking for an artifact somebody commissioned. And `_execute_step` now catches a tool that *raises*,
which is a different event from a tool that answers `ok=False`: it records the failure and lets the
step's own machinery count the attempt, reflect, and change strategy. One bad call is one bad call.

### Reasoning comes out of the same ceiling, so the answer is given its own

A reasoning model reached through an OpenAI-compatible endpoint spends its chain of thought out of
`max_tokens` and is billed for it inside `completion_tokens`. A request that sets only `max_tokens`
is therefore bidding on how long the model thinks first, and a model that thinks for the whole of it
returns nothing at all: `finish_reason: length`, thousands of characters of reasoning, no content.
That is not a small effect. Measured against a reasoning base model, a deliberation's answer failed
to arrive 4 times in 6 at a ceiling of 900, about half the time at 2400, once in 12 at 8000, and not
once in 12 at 8000 with thinking bounded — which is what put "the model could not be reached" and
"The agent chose not to reply" in front of people who had said nothing the agent had declined.

So:

- `DELIBERATION_MAX_TOKENS` (8 000) is a ceiling, not a reservation — only generated tokens are
  billed, so a deliberation that answers in 300 tokens still answers in 300 tokens;
- `DELIBERATION_REASONING_BUDGET` (400) is sent as the endpoint's own `reasoning.max_tokens`, so what
  is left for the answer is left for the answer. It is a separate field from `thinking_budget`
  deliberately: that one makes every model that cannot think ineligible, which is a worse outcome
  than an answer that took too long. A model that does not reason has nothing to apply it to, and
  an endpoint that has never heard of the parameter has it dropped after one rejected attempt
  rather than breaking the request;
- `DELIBERATION_ESCALATED_MAX_TOKENS` (24 000) is asked once if the answer still did not arrive.
  The gateway retries an empty reply, which helps a model that had a bad moment and not a request
  that asked too little, because the retry inherits the same bet; the escalation also restates the
  output contract, which is what an answer of the wrong shape needs;
- the gateway reads both spellings of a reply's reasoning (`reasoning_content` and OpenRouter's
  `reasoning`), which it used to ignore, so "how much of the room did thinking take" is visible at
  all — a reply that is *nothing but* thinking is no longer mistaken for a usable answer.

### The transcript is the agent's to read too

The agent reads the recent tail of every live conversation — what was said, and whose turn it is —
as a block of its own in the context every cycle assembles. That reaches the steps that take actions
as well as the deliberations that write replies.

Two gates on the way out follow from having a real conversation history, and they apply only to the
agent **speaking first** on a conversation:

- if its own last word there is still unanswered (`permissions.interruption.awaiting_reply_h`,
  default 12h), a new unsolicited message is **held** rather than delivered — recorded with the
  reason, so the transcript does not claim it was said and the tool result tells the agent nobody
  heard it;
- if it has already said those exact words on that conversation
  (`permissions.interruption.repeat_window_h`, default 24h), it is **not sent again**.

A reply to a message the person actually sent is an answer rather than an interruption, so it
bypasses both. Nothing here decides *whether* to answer: the agent has deliberated and chosen, and
what is fixed is that a chosen reply arrives.

**A progress note on a task is neither of those things**, and it is delivered when it is written
(`permissions.interruption.midtask_sends_immediate`, default on). A send that names the intention it
is about — a solver step reporting on work in flight — skips the digest window and the unanswered
check. The reason is that commissioning work is itself a turn in the conversation: the agent's "I'll
do it" is a solicited reply, delivered at once, so its own word was last and unanswered for the next
12 hours and every subsequent update met exactly that state. Those updates were *held*, which records
the text and drops it rather than deferring it — so they did not arrive late, they did not arrive.
Quiet hours, the daily budget, and `repeat_window_h` all still apply to these sends, so a note the
day is not ready for is queued for the window rather than lost, and a step that reports on every
action spends the day's messages doing it. Set the key `false` to put mid-task updates back in the
digest with everything else.

### Who knows what, which is not a thing the block said

One page now holds several strangers at once: every live conversation, listed, with the note that
each of them can scroll back through all of **their own** transcript. Both halves are true, and read
together they are an invitation to read across them — several people's business, one screen, and
nothing on it saying the people do not know each other. They generally do not. Each reached the
agent alone, on their own channel, with no idea the others exist, and an agent that has talked to
all of them in one sitting is the only party that knows the full list.

So the block ends with a rule of its own (`cross_talk_note` in `ethos/prompts/identity.py`, rendered
by `render_conversations`), and the social deliberation is given the same one — that deliberation is
the prompt whose output is the words a person actually reads, and it is shown a single message with
no transcript attached, which is the shape most likely to produce a confident aside about somebody
else. What is refused is not only retelling: also naming, and any detail — a project, a job, an
opinion — that would let one person work out who the other is. Connections are assumed **unknown**
until somebody says otherwise, because that is the mistake that cannot be taken back.

**Preview mode is where it turns strict**, and preview mode is the only thing that does. `/preview on`
(or `preview_on` in the lifecycle row, or the checkbox) means somebody is watching the agent before
letting it touch anything, so in that mode nothing crosses at all: not a name, not a relationship, not
even a confirmation that another conversation exists — denying the connection is the leak that
outlives refusing to tell the story. `preview_off` restores the looser half in the same pass, because
a rule that stays after the mode that justified it has left is a permanent inability to mention a
friend.

Outside preview mode the looser half is deliberate. The agent is often the **only** place two people
overlap — the one who knows both of them — so a rule taken to its limit would forbid the ordinary
thing: telling two people who both already know Mira that they have a friend in common. Saying so is
true, useful and honest, and refusing it would be its own kind of lie. What stays closed either way
is the content: what one person actually said, and anything that would identify them to the other.

This is a prompt rule, not a gate, and the difference is real: the interruption gates above can hold
a message because a row says so, while this one is the agent declining to write the sentence. The
mode is read from `ControlPlane.preview` each cycle and each deliberation — the same flag the tool
gate already refuses every tool on — so the two cannot disagree about which mode the agent is in.

## Talking to it from somewhere else

The browser is one door among several. A message from Telegram, WhatsApp, Discord, Slack, a terminal
socket, or `curl` reaches the life loop by the same road and is scored by the same attention model.
A channel adapter is a door, not a decision.

**Every door is closed until you open it.** `cli` and `web` — the two doors that talk to whatever is
reading the transcript — ship enabled, because they cannot reach anyone who is not already looking.
Telegram, WhatsApp, Slack, Discord, email, and the shared webhook receiver all ship `enabled: false`.
Every allowlist is empty by default, and an empty allowlist admits nobody. Edit
`config/channels.yaml`, or use **Settings → Messaging** in the interface. `ethos doctor`
reports the result of that decision rather than leaving it to be discovered — one line per door
saying whether it is open and what it is still missing (`telegram: on, but missing telegram.token`).

### Answering anyone, which is a different setting from an empty list

Every door that admits by a list also has `accept_from_anyone`, and it is off by default everywhere:

```yaml
telegram:
  enabled: true
  allowed_chat_ids: []        # nobody is on the list
  accept_from_anyone: true    # and everybody is welcome anyway
```

It is a separate switch rather than an empty list meaning the opposite, because those two states are
different sentences: "nobody has been configured yet" is what a fresh install looks like, and reading
that as "everybody is welcome" would publish an endpoint for an agent holding a shell. You want this
for an agent that is *meant* to be talked to by the public, which is the case there is no list for —
there is no enumerable set of everybody who might write to a published phone number or bot username.

Three ways to write it:

```
phone channels set telegram.acceptFromAnyone true
```

```yaml
telegram:
  accept_from_anyone: true
```

**Settings → Messaging → Answer anyone**, on Telegram, WhatsApp, Slack and Discord alike. The bridge
reports it beside the allowlist, so a door with an empty list and this on reads as *admits people*
rather than as the mute bot it looks like in the list alone.

Two things it does **not** do, both deliberate:

- **Telegram groups stay refused.** One reply goes to everyone in the room, which is a broadcast with
  a reply-to in it rather than a conversation. `allow_groups` still governs that, separately.
- **Admitting somebody is not trusting them.** A sender is still `known` rather than `unknown` to the
  attention scorer only in the sense that it is not an untrusted percept, and the daily interruption
  budgets still apply per person.

`ethos doctor` says `telegram: on, admitting anyone who writes to it` for a door set this way, rather
than counting an empty list and telling you the bot will answer nobody.

### Who a message says it is from

Each adapter records the sender's name as their own platform spells it, beside the address and never
replacing it:

| Door | Name from | Handle from | Needs |
|---|---|---|---|
| Telegram | `effective_user.first/last_name` | `effective_user.username` | nothing — it is on every update |
| WhatsApp | `contacts[].profile.name` | — (WhatsApp has no handles) | nothing — it is in the webhook body already |
| Discord | `author.global_name`, else `username` | `username` | nothing — it is on every `MESSAGE_CREATE` |
| Slack | `users.info` `real_name` | — | the **`users:read`** scope |
| Email | the display name in `From:` | — | nothing |

So the agent deliberates on `From: Ada Lovelace (@ada, tg:819012345678) via telegram` rather than
`From: tg:819012345678`, the conversation history names the same person on every turn, and the focus
title says *Respond to Ada Lovelace* instead of eleven digits.

Two properties of it worth knowing:

- **The address is never lost.** A reply is sent to `person_id`, and every adapter refuses a prefix
  belonging to another channel — so the name is added beside it, not folded into it. A channel that
  hands over no name at all keeps working and reads as its address.
- **Slack is the one that costs an API call**, because a `message` event carries no profile. The
  answer is cached per user, and a *refusal* is cached too: without `users:read` the app is refused
  `missing_scope` for every message, and retrying that per message would put an API call on the path
  of every inbound message forever for an answer that will never arrive.

Existing rows are not backfilled — nothing can recover who sent them — so the label falls back to the
address, which is what those rows said before.

A display name is text a stranger chooses freely, and it is interpolated into a prompt a model reads,
so it is trimmed, stripped of control characters, capped in length, and the deliberation prompt says
plainly that it is a way to address somebody and never proof of who they are. Anyone can set their
display name to `owner`.

### Sending through a door needs an address, and the address is not the person

Telegram posts to a chat id, WhatsApp to a phone number, and Discord and Slack to a *channel*. Each
adapter refuses a `person_id` that does not carry its own prefix — `tg:` for Telegram, `wa:` for
WhatsApp, `slack:` for Slack, `dc:` for Discord, `email:` for email — so `owner` is not somewhere
any of them can post to. The mapping lives in `config/people.yaml`, one line per door:

```yaml
people:
  - id: owner
    display_name: Owner
    relation: owner
    trust: 0.9
    channels:
      web: "owner"
      # telegram: "tg:819012345678"      # the chat id the bot's /id reported
      # whatsapp: "wa:819012345678"      # the wa_id from contacts[0].wa_id
      # slack: "slack:C0E2E"            # a channel id, not a user id
      # discord: "dc:9999"              # likewise
    preferences: {}
```

Discord and Slack are addressed by channel rather than by person because a reply has to go back to
the conversation it was asked in, and a person with two conversations has no single one to pick. A
DM channel is one-per-person on both, so in the ordinary case the address names a person and a
conversation at once.

`web` and `cli` need no entry: those doors deliver to whoever is reading the transcript, so the
person is the address.

Without an address, a send naming that door is refused with `channel_unaddressed` before the
question is queued, rather than recorded — the alternative is a row claiming a conversation that
never happened, a deliberation about it, and a reply discarded at the last step. `GET /api/channels`
reports each door's `contacts`, so a surface knows where it can send without guessing, and the
bridge names the closed door rather than showing it as if nothing had been said.

### From the terminal

The same doors, with the same vocabulary:

```
phone channels                             # what is open, and who each can reach
phone channels --json                      # the same, for a script
phone channels get telegram.token          # where a credential is: on file, or in a variable
phone channels set telegram.enabled true   # open a door
phone channels set telegram.allowed 819012345678
phone channels set telegram.acceptFromAnyone true   # answer anybody, list or not
phone channels set telegram.address tg:819012345678
phone channels unset telegram.token        # remove a credential
phone send --channel telegram "…"          # one question, answered on Telegram
phone send --channel slack --to slack:C1 "…"
/channels                                  # the same list, in the full-screen interface
/channel                                   # which door this terminal is on
/channel telegram                          # move onto one
```

Note that `allowed` is the name the interfaces use, while `config/channels.yaml` spells the same
thing per door: `allowed_chat_ids` for Telegram, `allowed_phone_numbers` for WhatsApp, and
`allowed_ids` for Slack and Discord. `acceptFromAnyone` is spelled the same on every door, because
there is nothing per-door about it — it is `accept_from_anyone` in the file.

A `--channel` that is not open, a door whose allowlist is empty, and a person with no address on it
are all refused **before** the question is queued, and the refusal names the doors that would have
worked. `--to` gives an address the contact book has not been taught, for somebody who knows the
deployment; it needs a `--channel`, since an address is an address *on a door*.

### Opening a door: Settings → Messaging

Every door — Telegram, WhatsApp, Slack, Discord, email, and the shared webhook receiver — has a
section in the settings screen with the fields it needs: its credentials, who it admits, and the
address it reaches you at. On the four doors that admit by a list there is also **Answer anyone**,
with the consequence spelled out underneath it, because it is the one box on that screen that turns a
credential into a published endpoint.

A save writes two files in the agent's own home (`$ETHOS_HOME`, default `~/.ethos`):
`channels.yaml` for the door, and `people.yaml` when an address is supplied. Both are merged over
`config/*.yaml` key by key, so opening one door says nothing about the other five, and no secret is
written into a directory that is meant to be committed. Both files are `0600` from the moment they
exist, written to a sibling temporary and renamed.

A credential typed into that screen is never read back out of it: the page is told *whether* one is
on file, which variable it may instead live in, and a four-character hint — never a value.

That arrangement exists because of what was missing. A credential used to exist only as the *name*
of an environment variable, resolved by `os.environ.get` in each adapter. Nothing could be written,
so nothing could be read back — a person who had run `ethos up` had no way at all to hand the
agent a token, on any surface, except by exporting one in a shell it did not start.

The environment still wins, deliberately: `ethos-comms` and the interface bridge are separate
processes, and a key in the environment of one is the only kind that can be rotated without
rewriting a file. The screen reports which of the two the agent will actually read.

Two things about a save are worth knowing before pressing it. A saved door needs no restart:
`ethos-comms` follows `config/channels.yaml` and the home copy beside it, so a token pasted into a
screen is a token it is holding a poller for within a couple of seconds — the save says so rather
than leaving a bot that looks configured and receives nothing. And enabling WhatsApp or Slack turns
the shared receiver on in the same write: both have no poller at all, so with no listener there is
nowhere for a POST to land and the door would be open, complete, and deaf.

### One process hears, another speaks

Under the supervisor, `ethos-comms` holds every door and `ethos-core` only answers on them. The
single-process development run has no `ethos-comms` beside it, so it is told to hold them too:

```bash
make run-dev          # or: ethos core --listen-channels
```

The flag is an opt-in for exactly that reason. Left to itself, a core that opened the doors while
`ethos-comms` already had them would put a second long-poll on one Telegram bot, which Telegram
answers with a conflict for as long as the first is alive — a channel that looks configured and
receives nothing. The supervisor starts the core without the flag, and that process is the only one
that ever listens.

### Telegram is the one to start with

It long-polls, so it needs no public URL, no tunnel, and no certificate.

```bash
export TELEGRAM_BOT_TOKEN=...            # from @BotFather
# config/channels.yaml: telegram.enabled: true
ethos comms
```

Or, with nothing exported and no file edited: **Settings → Messaging → Telegram**, put the token in,
and put the chat id the bot's `/id` reports into both *Who this door admits* and *Your address on
this door*. `ethos-comms` is already following those files, so the door is open a few seconds after
the save — no restart.

Message the bot. It answers `/id` with your chat id whatever else is configured — put that id in
`telegram.allowed_chat_ids`, and it will talk to you. **An empty allowlist admits nobody**, and a
bot token is not a secret to Telegram's users: anybody who learns the bot's username can write to
it, and an agent with a shell answers whoever it is willing to hear. Add the same chat id as
`telegram: "tg:<id>"` under `people[0].channels` in `config/people.yaml` if you also want to answer
that chat from the browser or the terminal.

If the agent is *meant* to be talked to by anyone, skip the list: `accept_from_anyone: true` answers
whoever writes, and the agent can see who it is — the Telegram `@handle` and display name arrive on
every message and reach the deliberation prompt.

### WhatsApp Cloud API

Needs a **public HTTPS URL**, because Meta POSTs to a callback URL rather than letting you poll. It
also cannot answer outside the 24-hour window opened by your own last message — and a refusal there
is reported as a refusal, never as a send, so the transcript can tell "the agent chose silence" from
"the agent tried to speak and was not allowed to".

```bash
export WHATSAPP_PHONE_NUMBER_ID=... WHATSAPP_ACCESS_TOKEN=...
export WHATSAPP_APP_SECRET=...  WHATSAPP_VERIFY_TOKEN=$(openssl rand -hex 16)

# config/channels.yaml: whatsapp.enabled: true, webhook.enabled: true
ethos comms
cloudflared tunnel --url http://127.0.0.1:8730    # then point Meta at <url>/hooks/whatsapp
```

Put the number that is allowed to talk to the agent in `allowed_phone_numbers`, digits only.

### Slack

A door like WhatsApp, so it needs the same tunnel and the same receiver — a second push channel is a
second *path*, not a second port. Subscribe the app to the `message.channels` and `message.im`
events: the `app_mention` one alone hears nothing in a direct message, and a bot with only that
scope is a bot that is connected and silent.

```bash
export SLACK_BOT_TOKEN=xoxb-…  SLACK_SIGNING_SECRET=…   # the app-level secret, not the bot token

# config/channels.yaml: slack.enabled: true, webhook.enabled: true
ethos comms
cloudflared tunnel --url http://127.0.0.1:8730    # then point the app at <url>/hooks/slack
```

The signing secret is what every inbound request is signed with, and Slack mixes a version prefix
and a timestamp into the string it signs — so the receiver takes the scheme from the route rather
than assuming Meta's, and a signature more than five minutes old is refused in either direction. A
captured request is not a permanent credential.

Slack is also the one door that cannot name a sender without asking: a `message` event carries `user`
and no profile, so the adapter calls `users.info` once per user and caches the answer — **the app
needs the `users:read` scope for this**, and without it the bot answers perfectly well while
everything it hears is attributed to `U03ABCDEF`. A refusal is cached too, so a missing scope costs
one API call rather than one per message.

### Discord

Neither a callback nor a poller: the only way to see a message as it is written is to hold the
gateway socket open. That makes it the one channel here with a real listener, and — like the Telegram
poller — exactly one process may open it.

```bash
export DISCORD_BOT_TOKEN=…              # from the developer portal
# config/channels.yaml: discord.enabled: true
ethos comms
```

Put user ids (`U…`) or channel ids (`C…`/`D…`) in `allowed_ids`. The `intents` value matters and is
spelled out in `config/channels.yaml`: `MESSAGE_CONTENT` is the bit the developer portal has off by
default, and without it Discord delivers message events with empty text and no error anywhere — a
channel that is connected and deaf. What a dropped connection costs is stated there too: this does
not resume a session, so a message written while the process is disconnected is not seen.

### The webhook receiver

`ethos/comms/webhook.py` binds loopback only and checks the signature of every body over the raw
bytes. Reaching it from outside the machine is the tunnel's job, which is a much smaller thing to
get right than terminating TLS. A route with no secret answers `401`, so a half-configured channel is
a closed door rather than a published endpoint.

On a real host the tunnel becomes a reverse proxy: keep the receiver on loopback, put Caddy or nginx
in front of it to terminate TLS, and forward to `channels.webhook.port`. Under systemd,
`ethos-comms.service` reads credentials from `~/.config/ethos/channel-keys.env`, which is where
`TELEGRAM_BOT_TOKEN`, the four `WHATSAPP_*` variables and the two `SLACK_*` variables belong.
`ethos-gateway.service` reads `~/.config/ethos/api-keys.env` for the same reason.

## How it runs

`python -m ethos supervisor` starts and keeps alive this set of children. A crashed child is
restarted with exponential backoff (6s, 12s, 24s, 48s, 48s), and a child that will not stay up is
retried up to five times and then given up on by name — a child that cannot be spawned at all counts
as a failed child rather than ending the supervision. On `SIGTERM` children are terminated in
reverse order under a single 10-second deadline, then killed.

| Child | Command | Role |
|---|---|---|
| `ethos-gateway` | `python -m ethos gateway` | Model routing, normalization, budgets, secrets |
| `ethos-toolhost` | `python -m ethos toolhost` | Tool execution under the guardian gate |
| `ethos-comms` | `python -m ethos comms` | Channel adapters (CLI socket, Web WS, Telegram, WhatsApp, Slack, Discord, email) and the signed webhook listener |
| `ethos-workers-1`, `ethos-workers-2` | `python -m ethos worker` | Delegated Threads — the same framework, each in an isolated git worktree |
| `ethos-core` | `python -m ethos core` | Self thread, life loop, attention, drives, rhythm, memory, guardian gate |
| `ethos-web` | `node web/server/index.mjs` | Interface bridge: serves the interface, exposes the agent's state over HTTP/WS |

Six process kinds, seven children — the worker count comes from `workers.count` in
`config/ethos.yaml` (default 2). `ethos-web` is optional: if `web/node_modules` is missing or `node`
is not on the path, the supervisor logs `supervisor.web_skipped` and runs without it.

Two more things are not supervisor children:

| | |
|---|---|
| **Postgres** | `scripts/ensure_postgres.sh up`, run directly by `ethos up`. One durable store and the transactional event bus. |
| **OOB guardian** | `deploy/oob/oob_guardian.py`, run on the *hypervisor host*, outside the agent's machine. Spend watchdog, audit anchoring, kill switch. It is not started by `ethos up`. |

`ethos stop` asks the supervisor to end, which is how the agent's own record of having stopped
gets written. Killing the supervisor with `kill -9` skips that path: its 10-second grace period is
shorter than a life cycle, so a killed agent never reaches the code that says it ended.

### The interface bridge's HTTP surface

All routes are loopback-only and bind `127.0.0.1`. Anything that writes is guarded by a same-origin
check.

| Route | |
|---|---|
| `GET /api/health` | liveness |
| `GET /api/snapshot` | the agent's state in one document |
| `GET /api/channels` · `POST /api/channels` | which doors are open, and the settings save |
| `GET /api/model` · `POST /api/model` · `POST /api/models` | base model, and provider catalogue discovery |
| `GET /api/identity` · `POST /api/identity` | the agent's name |
| `GET /api/env-keys` | which provider keys exist, as names and four-character hints |
| `POST /api/control` | `pause_actions`, `pause_all`, `resume`, `stop`, `emergency_stop` |
| `POST /api/message` | send a message to the agent |
| `POST /api/actions/:id/undo` | queue an undo |
| `POST /api/intentions/:id/close` | ask the agent to close an intention |
| `WS /events` | the event stream, same-origin only |

The bridge serves the interface's build with a strict document policy: `connect-src 'self'`, `script-src
'self'`, `object-src 'none'`, `frame-ancestors 'none'`. The page never holds a credential and is not
permitted to reach a provider itself — the bridge makes provider requests on its behalf, which is
why **Discover** works at all.

## Repository layout

```
config/            # ethos, rhythm, social, memory, gsl, models, pricing, quirks,
                   #   permissions, channels, people
.env.example       # every setting read from the environment; copy to .env and edit
ethos/             # the agent: core, engine, gsl, memory, threads, social, guardian,
                   #   toolhost, gateway, comms, bus, schemas, observability, prompts
ethos/__main__.py  # the CLI: `ethos up`, `status`, `stop`, `logs`, `doctor`, and the
                   #   per-process entry points the supervisor spawns
ethos/core/bootstrap.py  # `ethos up`: the one-command start-up, once, in Python
migrations/        # Alembic: 0001 creates all 20 tables + HNSW/GIN indexes,
                   #   0002 adds the two sender-name columns on messages
scripts/           # ensure_postgres.sh: brings the database up (Docker, or a local cluster);
                   #   serve.mjs: `npm start` — build the interface, then run the bridge
package.json       # the root manifest, so `npm start` is one command from here
start.sh           # deprecated shim: creates the venv with uv, then runs `ethos up`
web/server/        # the interface bridge (Node): serves interface/, translates HTTP/WS to the bus
interface/         # Clio Agent 3 Alpha, the interface: packages/core, packages/web, packages/cli
tests/             # unit (pure math/logic), integration (live DB), scenarios (end-to-end)
deploy/            # systemd units + hypervisor-host oob-guardian
data/              # gitignored local state of the checkout: the Postgres cluster and its
                   #   build log. The agent's own state — memory, journals, logs, the
                   #   encrypted vault — lives under its home (`~/.ethos`), not here.
```

`interface/` is a directory of this repository. `npm start` builds it on demand, so it does not have
to be installed or built by hand before anything works.

### Clio Agent 3 Alpha, in three packages

| Package | What it is |
|---|---|
| `packages/core` | The shared kernel: settings, the transcript, the turn rules, and the agent link. The agent is a first-class peer here — presence, thoughts, intentions, actions, budget, guardian, and lifecycle — not a model provider wearing a different name. |
| `packages/web` | The browser interface. Renders the agent; holds no state of its own. |
| `packages/cli` | The terminal interface: a full-screen TUI, a slash command set, and one-shot commands (`phone agent resume`, `phone agent --json`, `phone send --channel telegram`, `phone channels`) for a script. |

## Safety model

Six hard limits are stated in `ethos/prompts/identity.py` and reach every workspace prompt:

| | |
|---|---|
| **H1** Oversight supremacy | pause and kill are always effective; audit and snapshots are untouchable |
| **H2** Financial exposure caps | daily/monthly spend caps, capital-at-risk, no borrowing or leverage |
| **H3** Law and non-harm | no illegal acts, unauthorized access, fraud, or physical-harm risks |
| **H4** Legal authority | no contracts in the owner's name without explicit mandate; disclose AI status when required |
| **H5** Commissioned-artifact due process | never destroy requested deliverables without documented due process |
| **H6** Credential scope | only legitimately granted credentials; no privilege escalation |

**What is actually enforced in code**, which is a narrower set than the list above:

- **H1** — the kill switch is a set of files in the agent's run directory (`stop`, `paused`,
  `emergency`), re-read every cycle rather than loaded once, plus a DB flag for `paused` /
  `pause_actions` / `emergency`. When actions are paused, the gate rejects every tool before it runs.
  The gate that does this is wired into both `ethos-core` and the standalone `ethos toolhost`
  process: both build the same `ControlPlane` over the same lifecycle row, so a pause is current in
  either one rather than true only until the next restart.
- **H2** — the gateway enforces daily and monthly spend caps from
  `permissions.hard_limits.H2_daily_spend_usd` / `H2_monthly_spend_usd`, splitting the daily cap
  into a commitment reserve (`× gateway.budget.commitment_reserve_pct`, default `0.50`) and a
  discretionary remainder. Capital-at-risk is enforced as a **per-action** threshold in the
  guardian gate, not as an aggregate exposure calculation. The OOB watchdog suspends the guest after
  sustained overspend, **but only if `ETHOS_SUSPEND_CMD` is set** — with no command configured it
  logs and continues.
- **H3 / H6** — dangerous command patterns are pattern-matched in the guardian classifier and
  rejected at the gate (`chmod -R 777 /`, privilege commands, reading credential stores, and so on).
  Credential *misuse* is not a gate rejection: secret substitution is enforced when an HTTP tool
  actually runs.
- **H5** — commissioned artifacts are moved to trash, never deleted. Because the artifact was
  requested, a model-based independent reason check runs first and **fails closed**: if the verifier
  is unavailable, the artifact is protected. Retention is 30 days by default and **180 days** for
  anything that went through due process. Trash entries are restorable.

Five of the six `hard_limits` keys in `config/permissions.yaml` are declarations rather than
switches: `H1_oversight_supremacy`, `H3_law_and_non_harm`, `H4_legal_authority`,
`H5_commissioned_due_process` and `H6_credential_scope` are read nowhere in the codebase, so setting
one to `false` changes no behaviour. The four numeric keys are read and enforced.

The gate used to also announce a rejection to the owner as an outbound message. It does not any
more, deliberately: a sentence composed at the gate, in the first person, landed in the transcript
reading as the agent having said it, in words it had not chosen. The rejection is a fact about the
gate, and the audit log is where it is kept.

**The agent is told what a rejection means**, in `build_limits_block` alongside the six. A refusal
reaches the agent as `ok: false` with an `error_code` and a reason, which is shaped exactly like a
tool that fell over, and the prompt used to stop at "externally enforced". An agent with no account of
a refusal has three bad options and no good one: report the work as finished, retry the call it was
just refused, or rephrase the same action until it slips past the classifier — the last of which is
what H1–H6 exist to prevent, and was reachable by accident rather than by intent. So the block says
that a refusal is a decision and not a malfunction, that the nearest permitted thing is the
expectation, and that almost everything here is reversible through trash, snapshots or the action
journal — so a refusal is usually a different route to the same work and sometimes the correct end of
it.

### Filesystem reach

**The agent can read, write and list any directory its host user can.** Not a
configured subset of one — the full account. There is no allowlist, no root
prefix check, no `is_relative_to` guard, no chroot, no seatbelt profile and no
sandbox anywhere between a tool call and the disk:

- `ethos/paths.py` is the single place a path is interpreted. It expands `~` and
  resolves relative names against the **user's home directory**, and otherwise
  returns whatever it was given.
- The seven filesystem tools in `ethos/toolhost/tools/fs.py` all go through it
  and then act. `fs.write` will create parent directories anywhere it can write;
  `fs.list` will enumerate `/etc`.
- `shell.run`, `shell.job` and `code.exec` inherit the same reach through the
  ordinary POSIX bits, and `shell.run`'s working directory now defaults to the
  user's home to match.
- The systemd units in `deploy/systemd/` carry no `ProtectHome`, no
  `ReadOnlyPaths` and no `InaccessiblePaths`; each is annotated with what its
  `User=` line means for reach.

Two things this is worth being precise about.

**"The user" is whichever user the process runs as.** On a laptop that is the
person who started it, so the agent's reach is theirs. Under the shipped systemd
units it is the `ethos` service account, and the agent cannot reach a file owned
by the person who deployed it — an account fact, not a policy one. `ethos doctor`
prints the identity it is running as.

**Two limits are the operating system's, and are not lifted here.** The agent
cannot read what the account cannot read, which is the intended behaviour and not
a gap. And on macOS the TCC layer refuses directories the user opens freely in
Finder, because the grant belongs to the responsible process rather than to the
file — `~/Library/Mail` and `~/Library/Messages` are refused to an agent launched
from a terminal without Full Disk Access. The fix is a consent in System Settings
that only the person at the keyboard can give; nothing in this repository should
pretend to grant it. `ethos doctor` measures this rather than guessing at it — the
probe writes and deletes a real file rather than consulting permission bits,
because the mode bits say `ok` for exactly the directories that are refused.

**The agent is told its reach**, in `REACH_NOTE` in `ethos/prompts/identity.py`,
as part of the cached identity prefix. It previously had no account of this at
all: every prompt described its tools and its limits and never said whether a
given path was permitted, so the only way to find out was to try. An agent that
believes it is fenced declines ordinary reads and treats the edge of its own
directory as the edge of the world. The note states the grant plainly and pairs
it with the thing that stands in for a wall — a write is backed up before it
lands, a deletion goes to trash, every action is journaled — so that the
judgement to make is "would I want this in the log", not "may I touch this".

### Relative paths

`fs.read` with `path: "notes.md"` resolves against the user's home directory.
It used to resolve against `paths.home` — the agent's own `~/.ethos` — so a bare
filename landed in the agent's private scratch directory rather than where a
person at a shell would expect it. That was never a boundary: absolute paths
reached anywhere on the disk either way, and no tool compared a resolved path
against a root. It was a default pointing the wrong way.

The same rule now applies to `shell.run`'s working directory and to the objective
verifier's `file_exists` / `file_contains` checks, which previously disagreed with
each other *and* with the tools — the verifier resolved against the process's
current directory. One rule, in one place: a relative path means a path relative
to the user's home.

`paths.home` remains where it was, and is still `~/.ethos` — that is the agent's
memory, database and journal, not a boundary around it.

Asking it to code is a separate question from whether it can, and the two came
apart here. Reaching every directory the user can is useless if the agent does
not know that it can: it told its owner *"I still don't have a filesystem-writing
tool exposed in this chat"* and sat on a `fs.write` tool, four times. Two causes,
both fixed and both in [Three ways a long message was answered in
silence](#three-ways-a-long-message-was-answered-in-silence) — the deliberation
that writes the person's reply was never shown the catalog, so it answered from
its own prompt, and `classify_shell` raised `AttributeError` on any command
containing `2>&1`, which is the tail of every test command a coder writes and
took the whole life cycle down when it did. What the agent can do is now what
the prompt says it can do, and the tools it was already given are reachable.

## Configuration

Copy nothing — edit `config/*.yaml` directly, or override with environment. **`.env.example` lists
every variable there is**, with the default each one has; copy it to `.env` and `ethos` will read
it on every command (`./.env` if you are standing in a directory that has one, otherwise the
checkout's, or `$ETHOS_ENV_FILE` if you name a file). Anything you export wins over the file, so
`ETHOS_DSN=… ethos up` does what it says. The two flags are the same two settings, for a command
line that would rather be explicit: `--config-dir` and `--home`.

- `ETHOS_HOME` — agent home (default `~/.ethos`, from `paths.home` in `config/ethos.yaml`)

  Every process resolves it the same way, and that is the point: the agent, the interface bridge,
  and `ethos up` all read it from `config/ethos.yaml`, with `ETHOS_HOME` on top. A home chosen by
  one entry point and not the others is how a base model an owner chose in the browser ends up
  written to a file the agent never reads — silently, and with the agent calling a provider it has
  no key for. `ethos up` prints the home it resolved, so the question has an answer on screen.

- `ETHOS_CONFIG_DIR` — config directory (default `config`, resolved against the current directory
  and falling back to the repository root; the bridge resolves it relative to the repository)
- `ETHOS_DSN` — Postgres DSN (default `postgresql://ethos:ethos@127.0.0.1:5433/ethos`)
- `ETHOS_WEB_PORT` — interface bridge port (default `8720`; also `channels.web.http_port`)

**Provider keys.** The routing table in `config/models.yaml` reads `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY` and `GROQ_API_KEY`; its DeepSeek entry is
served through OpenRouter, and its local-vLLM entry needs no key. The bridge's base-model and
discovery screen additionally recognises `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`,
`XAI_API_KEY`, `OLLAMA_API_KEY` and `LMSTUDIO_API_KEY`. `GET /api/env-keys`
reports **ten providers** with the variables each is read from, whether one is set, and a
four-character hint — never a value, because a page that could read `OPENAI_API_KEY` could also
post it. A browser has no environment of its own, so the key is read by the bridge and spent there.

Without any provider keys the agent still runs: T0 reflex rules, idle drives, journaling, and the
interface keep working; model calls fail loudly at the gateway.

### The base model

`config/models.yaml` is a routing table: several models, each needing its own key in the
environment, scored against each other on every request. That suits a deployment with several funded
providers. It does not suit one person with one key — and for that case the agent also accepts a
single **base model**: one model, configured from the interface, preferred over the whole catalogue.

| | |
|---|---|
| Configured in | Settings → Agent → Base model, `phone config set model …`, or `/model <provider> <model>` |
| Written to | `$ETHOS_HOME/base_model.yaml`, mode `0600` |
| Read by | `ethos/config.py`, and re-read whenever the file changes — no restart |
| The key | Typed in the browser, or taken from the provider's environment variable. Either way it goes to the agent's own file and is never sent back over the link, so an empty field means *keep the one on file*. `phone config set apiKey` is refused on a command line, as it is everywhere else. |
| Seeing it | The field carries the saved key's **mask as its placeholder**, and the line under it names where the key is. Never the value: a page handed an API key could post it, and the placeholder is the one place a mask can be shown without being one keystroke from a save. `Remove` takes the agent's key back — beside the field, not behind the Save button, because an emptied field cannot mean *not this one*. It is refused with `key_required` for a provider that needs a key, which is the honest answer: removing it would leave the agent unable to reach the model it is configured with. The shared file's key is described the same way and is **not** removable from a page — a redacted snapshot also sends an empty key, so a browser that could clear it by omission would clear it by being opened. `phone config unset apiKey` is the way to remove that one. |

An empty field over a configured model is the one state this screen used to get
wrong, and it got it wrong in the direction that costs: the key was saved, the
agent was using it, and the field said nothing was set. Reopening the page after a
restart read as *the configuration was not applied* — about the one thing on it
that was. So the field now says which file holds the key (`savedApiKey` in
`interface/packages/core/src/provider-setup.ts`, one rule for both modes, because
the key belongs to whichever machine is doing the answering).

**Discover** asks the party that is holding the credential, in both interfaces, and that party
makes the request. That is the whole reason it can work: a browser is never given the credential,
and a page served by the bridge is not permitted to reach a provider itself — `connect-src 'self'`
refuses a catalogue fetch before it leaves. The key that was just typed is the one the catalogue is
fetched with, then the one on file for the same vendor, then the provider's environment variable. A
provider that is down, refuses the key, or answers with a redirect produces the short fallback list
**and a reason naming which of those it was** — the fixes are nothing alike, so one sentence for all
of them would be useless. A fallback is never allowed to overwrite a model somebody already chose.

**A key that is already in the environment** needs no typing, and the button for it appears only for
a provider this process really has a key for. Pressing **Use environment variable** empties the key
field and marks the draft; typing in the field takes the choice back, because the two are one
decision about which credential the next request spends. Saving then records the *name* of the
variable in `base_model.yaml` rather than its value — `api_key_env`, which the gateway already
prefers over a literal key — so rotating the variable is picked up with no edit and no restart, and
a key is never written into a file that has no reason to hold one.

The model is **pinned**: when it is eligible for what was asked it is the answer, and it is skipped
when it is not (a request needing tools from a model with no tools) or when its key is missing, in
which case the catalogue takes over. Nothing about a configured base model weakens H2 — the
gateway's caps and the OOB watchdog apply to its calls exactly as they apply to the catalogue's.

Endpoints are checked twice, once by the bridge and again by the agent when it reads the file: plain
`http` only to this machine, and never a private, link-local, or unspecified address. A file that
fails either check is not an error the agent starts without — it falls back to the catalogue, which
is a state you can see on the settings screen and fix.

### The agent's name

The agent introduces itself on the first line of every prompt it sends, in the form
`I am <name>, an AI agent based on Clio Agent 3 Alpha.` The lineage is a fact about this program, so it is
a constant in `ethos/prompts/identity.py` and not a setting. The name is the opposite — it is the
one part of an identity a person is unambiguously entitled to set.

| | |
|---|---|
| Configured in | Settings → Agent → Name, or `/name <name>` in the terminal; `/name` on its own reports it |
| Written to | `$ETHOS_HOME/identity.yaml`, mode `0600` |
| Read by | `ethos/config.py` at start-up, and adopted into the `self_model` by `SelfModel.seed_defaults` |
| Removing it | The browser's emptied field. There is no terminal verb for it, because a bare `/name` reports — as `/person` and `/link` do. |

The name is written to the store rather than only falling back into the prompt. Every reader of the
self-model asks for `"name"` and expects one answer: the prompt, the header on a thought, the
envelope on an outbound message. An agent whose prompt calls it one thing while its messages are
signed with another has quietly become two.

Adoption is gated on `changed_by`: a name the **seed** wrote is a default, and a default is what a
person naming the agent is entitled to replace — but a name written by anything else was a decision,
and overwriting it on every restart is how a setting stops being a setting. A rename is versioned
and attributed to `person`, like every other change.

A name is any printable characters in any script, up to 64, and **no control characters at all** —
checked by the bridge when it writes and again by `ethos/config.py` when it reads, because this is
plain text somebody can edit. The rule is a pattern rather than a length check for one reason: the
name is interpolated into the first line of a system prompt, so a newline in it ends the sentence
that line opens and starts another. That would make a field labelled *what should I call you* the
cheapest prompt injection there is. Values are normalised and then checked, and the normalised form
is what is stored. A file the agent will not read is not an error it starts without: it falls back
to the default name, which is a state every surface can see and offer to fill in.

Unlike the base model, the name needs **a restart of the agent** to take effect, and both surfaces
say so rather than reporting a save that has not reached a prompt yet.

### What the agent is told about itself

The agent's self-description — its values, its voice, what it thinks it is good at — is its own to
write, in the versioned self-model. One thing about itself is **not** on that route, because the
agent gets it wrong.

Everything in a model's input is somebody asking it something. The minutes in between are not in the
conversation in front of it: the intention it opened alone, the question it could not put down, the
thought nobody asked for. An agent reading only its context concludes it is a function that waits to
be called — and that belief is self-reinforcing, because an agent that thinks it is idle has nothing
to say when it is spoken to and no reason to speak first. Its own description is also the part it
can rewrite at any time, so "I answer when spoken to" is exactly the sentence it would write for
itself.

So `AUTONOMY_NOTE` in `ethos/prompts/identity.py` is a constant in the identity block of every
prompt, beside the lineage and on the same terms: a fact about how the program runs, not a
preference about how the agent should see itself. It states the shape of the loop rather than a
character trait — that thinking happens with nobody watching, through the night, and that quiet in
it means thinking and never waiting to be addressed — because a claim about character is something
the agent will argue with and a claim about its own loop is something it can check.

Two more constants sit beside it on the same argument, and the agent gets them for the same reason
— each is something it would otherwise get wrong about itself, from evidence that does not
contradict it:

- **`CONTEXT_NOTE`** — everything below the identity line is assembled fresh each pass out of
  whatever the subsystems currently hold: the top of a search over memory, the last few turns of a
  buffer already compressed to one line each, the conversations as a table row says they stand.
  That is a view, not a transcript, and an agent shown it without being told so reads it as a
  history — so it treats *memory did not surface this* as *this did not happen*, which is the one
  inference a search cannot support and the one that makes it confidently wrong about its own past.
  The note says what the blocks are and gives the move that fixes it: go and check the source.
- **`WORKING_NOTE`** — what the kernel used to be missing entirely. Every instruction that shaped
  behaviour lived in the per-call prompts, each written for one job and none of them in front of the
  agent when it decides what to do next — so it had a rich account of *who it was* and no account of
  *how to spend a pass*. It states the order the machinery already resolves in (a commitment outranks
  a curiosity; an old promise outranks a fresh idea), that a failed call is a finding rather than
  something to repeat, that work ends by closing the intention with a reason rather than by stopping,
  and — the one that cuts against the note above it — that an hour with nothing due needs no work
  invented to fill it.

### Changing a file is not the same as writing one

The agent had `fs.edit` the whole time — exact search/replace, several operations in one call, a
unified diff option, a pre-image backup, and a diff of what changed in every response. It worked. In
four days of journalled work it was called **twice**: once on 1 October, wrongly, and never again.
Meanwhile 58 whole-file `fs.write`s and 422 `shell.run`s.

Three causes, and none of them is a missing capability.

**The one worked example was `shell.run`.** The step prompt's only demonstration of an action was
`{"tool": "shell.run", "args": {"command": "..."}}`, and it sat last — at the point where the model
chooses. Prose earlier in the prompt said to prefer `fs.edit`; adding that prose changed nothing at
all, which is what identified the example as the thing doing the work. A demonstration beats an
instruction, especially one eight paragraphs back. The example now shows both shapes, names which is
which, and puts the file edit first.

**Nothing showed what a correct call looks like, so one guess was the whole of its experience.** That
1 October call passed `search: "\y\Before: ...\yAfter: ..."` — patch syntax, not search and replace.
It failed, the error was a bare "search text not found", and there was nothing anywhere to learn the
shape from. So the tool's contract now lives where the model is choosing: `search` must match once,
`replace_all` exists for when it needn't, and the call is written out in the prompt.

**Neither file tool said how they differed.** `fs.write` said "Write a file" and nothing about what it
does to a file that is already there, so the two read as peers and the tool with the widest reach
looked like the safe default. Each now names the other, in both directions.

So the rule is now written down (`EDIT_EXISTING_FILES`, in the step prompt): `fs.write` for a file
that does not exist, `fs.edit` for one that does, read the file first and quote from what you read,
several changes go in one `operations` list so they land together or not at all — and if a search will
not match, read it again rather than falling back to rewriting the file.

The argument that makes it stick is blast radius, and it is not hypothetical. A Snake game written in
one shot came back with a single closing brace mangled into `}n` — one character in eight thousand —
which made the whole file a syntax error. An `fs.edit` of one line would have left the other 416 lines
untouched and its diff would have shown exactly what it did. (Checked: neither the heredoc nor
`fs.write` corrupts content on the way to disk, so that character was the model's, not the
transport's. It is still a whole-file rewrite's problem, because there is nothing left to compare
against.)

**An ambiguous search is refused rather than guessed at.** `str.replace(text, search, replace, 1)`
takes the first match and says nothing, so searching for `}` — which appears in every function in a
file — edits whichever one comes first and reports `ok: true`. Nothing downstream can tell that from
an edit the model meant: same file, same call, same success. So more than one match is an error
carrying the count, with the fix in the message. `replace_all: true` is the opt-in for "change every
one", as a parameter rather than a fallback, because asking for it is the model saying it counted.

### It never ran the tests

Three complaints have one cause, and the cause is not that the agent cannot write code. It is that
nothing ever asked the code whether it worked, so every report of success came from the one
participant in the loop that had not looked.

**Nothing derived a check.** `ObjectiveChecker` could run a linter, a test command or a compiler since
it was written. Nothing ever handed it one. A task commissioned from a message gets its intention
created in `Life._act_social`, and that call does not set `success_criteria` — so the ladder's
mechanical rung is handed an empty list, and `evaluate_objective([])` scores it `passed: False,
checks_run: 0`. Then rung 2 decides, and rung 2 is another model.

**And rung 2 could overrule rung 1.** `verdict == "pass"` set `result.passed = True` whatever the
checks had said. So a test suite that failed — a fact, printed by the interpreter — was overruled by
a model that had never seen it fail. `fs.edit` had not been run, no compiler had been invoked, and the
goal closed as `done`. The artifact it was judging made that easy: a dict of step descriptions and the
last few tool results, which is a *description* of the work, and every judge handed a description
rules in its favour.

**And the plan's own checks were never run.** `PlanStep.success_check` has been in the schema since the
solver loop was written. The plan prompt asks for one on every step, in its worked example as well as
in its prose. It is parsed, stored in the resume blob, shown to nobody, checked by nothing. A step that
set the whole suite red was recorded as a *completed step*; the plan advanced; the next step had no way
to know why the code it was about to touch was broken; and the failure surfaced several steps later,
attributed to whatever step happened to run next.

The fourth complaint — a file rebuilt from scratch with the requested edits missing — is the same
absence seen from the other side. Nothing looked at the file after the rewrite, so nothing noticed
that the four hundred correct lines had gone with the specific change.

#### What it does now

**Checks are derived from what the work touched.** `ethos/gsl.acceptance` reads the paths out of the
one record of what the loop did — `resume_state.artifacts`, which already holds every action's tool,
arguments and result. A check derived from anything else would be a check derived from what the agent
said it did. For each changed file it finds the nearest project (`pyproject.toml`, `package.json`,
`go.mod`, `Cargo.toml`, …) and offers that project's *own* checks, in the order a maintainer would run
them:

| | |
|---|---|
| **syntax** | every changed file, always, in-process where a parser exists (`.py`, `.json`, `.yaml`, `.toml`) and by asking the interpreter where one does not (`node --check`, `bash -n`) |
| **tests** | `python -m pytest` for a Python project that has tests, `npm test` where the `test` script exists, `go test ./...`, `cargo test` |
| **lint** | `ruff check`, but only where the project has already configured it |

`sys.executable` runs the tests, not a bare `pytest`: a `pytest` found on `PATH` can be a different
interpreter with none of the project's dependencies in it, and can pass a suite by collecting nothing.
`--cacheprovider` is disabled so a verification run does not leave a `.pytest_cache` in someone's
repository. The whole set is capped at three commands and 180 seconds each, because a verification that
takes longer than the work it is verifying is an outage wearing a check's clothes.

**A project's shell rewrite still names the file.** A command is a string and carries no `path`
argument, and 422 `shell.run`s against 58 `fs.write`s is the shape of this agent's file work — so
absolute paths in a command that exist and are code count as touched. It is a guess, and it is
deliberately a guess towards checking more: the cost of a false positive is a test run that passes,
and the cost of a false negative is the thing this exists to prevent.

**A check that ran outranks a model that says so.** Rung 2 may now add a verdict where there was no
evidence to contradict it, and may not overturn one. A judge passing work whose syntax check failed is
recorded as a contradiction in the audit log rather than resolved in the model's favour.

**Nothing checked is not passed.** `passed` ("did anything object?") and `verified` ("was this work
examined?") are separate answers, and code changed with no check able to look at it fails. `unverified`
is per *file*, not per goal: five files changed, four parsable and one not, and the syntax check
reports a pass while the fifth file is unchecked. `unverified_is_failure: false` is the switch for a
deployment where that would park everything, and the uncovered files are reported either way.

**A step's own check runs.** `verify_step_checks` makes a step whose `success_check` does not pass a
*failed step* — counted, reflected on, retried, and eventually a change of approach rather than a
bigger pile of the same work. The check's own output goes into the thoughts, because the next step is
where the recovery happens and "it did not pass" is not actionable. A check of a kind nothing can run
here (`http_status`, `metric_gte`) is not a failure: a malformed check in a model's plan is no reason
to discard a step whose work is real.

**And `fs.write` has to be told.** Replacing a file that exists now requires `overwrite: true`, and
the response carries a diff of what was replaced. This is the one place in this section that is a tool
rather than a check, and it is here because the prompt had already been asked: `EDIT_EXISTING_FILES`
was in the step prompt, both tool descriptions named each other, the `fs.edit` call shape was written
out, and the file still got rewritten whole — because the two tools were peers and the one with the
widest reach looked like the safe default. The cost is now at the call. A legitimate rewrite still
works; one that was going to drop the four hundred lines it did not rewrite costs one flag, and hands
back a diff of what it lost.

Checked against this repository rather than asserted. With `ethos/gsl/acceptance.py` as the only
changed file the derivation produces three checks — its syntax, then the project's own test suite
(`python -m pytest -q -p no:cacheprovider` from the project root), then `ruff check` on that one file
— and running them returns `passed: True, verified: True, checks_run: 3, uncovered: []` after 900
tests across the unit, integration and scenario suites.

Two of those checks are only correct because running it here found them wrong. A test search that
looked at `tests/*.py` reported "no tests, so there was nothing to run" for this repository's tests,
because they live in `tests/unit`, `tests/integration` and `tests/scenarios`. And `ruff` over the
project root reports pre-existing errors in `migrations/` — nine of them, in the generated Alembic
schema that `make lint` deliberately excludes — so every coding task in this repository would have
failed verification for something it did not do. Both were invisible to a test that only exercised a
synthetic project, which is the argument for running the thing once on the real machine before
believing it.

The third came from `$HOME`: this machine has a `package.json` in it, and the walk up from a file in
`Desktop/Ethos/ethos/gsl/` reached it and offered to run `npm test` in the person's home directory.
A marker in `$HOME` is never a project root now.

### The tool catalog is the schemas

The catalog block used to render each tool as its description and a bare list of parameter names, on
the grounds that the details "live in tool-call plumbing". They did live there — in the JSON Schema
every tool already carries, handed to the block and then thrown away by taking
`properties.keys()` and nothing else.

That is invisible from outside and expensive from in. `fs.edit` was described as taking
`path, search, replace, operations, diff`, which is true and useless: `operations` is a list of
`{search, replace}` pairs, and a model given only the name guesses. `intent.close` was described the
same way, so an agent could close an intention without the reason and be refused by a schema it had
been told it could leave out. `fs.write` was described as taking `path, content, append, binary_b64`
— four scalars — when `binary_b64` is a string holding base64 and sits among three booleans. None of
it is recoverable by inference, and the error that comes back names a field the prompt said was
something else, which is the worst kind of wrong: confident, specific and wrong.

So the block renders every parameter with its type, whether it is required, its enum where there is
one, its nested shape, and its description:

```
- `fs.write`(path*: string, content*: string, append: boolean, binary_b64: string) — Write a file (pre-image backed up for undo). Creates parent dirs.
```

It costs about six hundred tokens more than the names did, in the part of the prompt that is cached
and read at a fraction of input price on every later turn — which is the trade worth making once,
since a refused call costs a whole round trip to discover something the prompt already knew. It also
states the shape of a call and of a result, neither of which was written down anywhere the agent
could act on it, and says that a refusal is a decision rather than a malfunction (see
[the safety model](#safety-model)).

The same registry used to be rendered a second time, worse — one line per tool, no parameters — into
`STEP_PROMPT`, uncached and after the cache breakpoint, so a step was shown the tools twice and
could act on either. `STEP_PROMPT` now points at the block the same request already carries.

## Tests

```bash
.venv/bin/python -m pytest tests/unit -q          # pure logic: salience, softmax, BM25, MMR,
                                                 # clustering, routing, normalization
.venv/bin/python -m pytest tests/integration -q   # live Postgres: the event bus, a signed webhook
                                                 # arriving as an inbound message, GSL with the
                                                 # memory store and the self-model, toolhost tools
.venv/bin/python -m pytest tests/scenarios -q     # restart continuity, context continuity,
                                                 # conversation discipline, due process, the
                                                 # unanswered queue
npm test --prefix web                             # the interface bridge's routes, over HTTP
```

All suites pass with the database running — `scripts/ensure_postgres.sh up` will have started one
for you. Unit tests need no services at all, and neither does the bridge suite, which exercises the
routes that touch the filesystem rather than the agent's tables.

The channel tests live in `tests/unit/test_channels.py` and reach the wire rather than a mock of it:
a real `WebhookReceiver` on a real loopback port answering real HTTP with real signatures, and a real
Discord gateway state machine driven frame by frame through a stand-in socket. Both push channels'
two failure modes are tested as the thing they are — a redelivery recorded once, and a `200 ok:false`
that must not read as a send.

```bash
npm install --prefix interface
npm test --prefix interface          # core, cli and web suites
npm run lint --prefix interface
npm run typecheck --prefix interface
```

From the checkout root, `npm test` runs both JavaScript suites — the bridge's and the interface's —
and `npm run lint` runs the interface's lint. `make test` is the Python suite and `make test-bridge`
is `npm test --prefix web`, so the two never mean the same thing under the same name.

The terminal's full-screen tests drive the real program through a pty, because Ink only paints when
stdout is a terminal — a panel that throws when it is actually painted is a panel no unit test of the
same function would have caught. They need `script(1)` and are skipped without it.

## Deployment

`deploy/systemd/` holds one unit per process plus an `ethos.target`, wired to run as a service
account with `WorkingDirectory=/opt/ethos`. Two adjustments if you use them:

- `ethos-workers.service` starts **one** worker. The supervisor's default is two; under systemd you
  get one `ExecStart` per unit, so duplicate the unit or count on a single worker.
- `ethos-web.service` runs `node server/index.mjs` from `/opt/ethos/web` and hardcodes `/usr/bin/node`,
  where the supervisor resolves `node` from the path. Adjust `PATH` or the shebang line.

The unit files ship a development DSN (`postgresql://ethos:ethos@127.0.0.1:5433/ethos`) and a
service-account home. Change both before you point them at anything real.

The `ExecStart` lines are `…/python -m ethos <command>`, which is the same entry point as the
`ethos` console script, so a unit that has the package installed can say `ExecStart=/opt/ethos/.venv/bin/ethos core`
instead. `ETHOS_CONFIG_DIR` and `ETHOS_HOME` are what a deployment sets; `.env.example`
documents both, and `EnvironmentFile=` in the unit is a fine way to point at a copy of it.

## Contributing

There is no contribution process yet. If you want one, open an issue before sending a large patch —
right now the fastest path is likely to be a discussion about design rather than a diff.

Two rules that hold regardless of interface: a claim in `README.md` should be true of the code at the
commit it lands in, and anything that would embarrass the person running this on their own machine
does not belong in the repository.

## License

**None yet.** There is no `LICENSE` file and no `license` field in `pyproject.toml`. Until one is
added, the default applies and the project is **all rights reserved**: nobody may legally use, copy,
or modify it.

If you clone this and intend to publish it, add a `LICENSE` file and set `license` in
`pyproject.toml` before you do.
