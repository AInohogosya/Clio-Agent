from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ethos.config import EthosConfig
from ethos.gateway.normalizer import was_truncated
from ethos.observability.logger import get_logger
from ethos.prompts.deliberation import (
    execution_state_block,
    social_deliberation_messages,
    streak_review_messages,
)
from ethos.schemas.messages import (
    IGNORE_REASONS,
    IgnoreReason,
    Message,
    SocialAction,
    SocialDecision,
    Urgency,
    sender_label,
)
from ethos.schemas.models import ModelMessage, ModelRequest, Role, Tier

logger = get_logger("ethos.social")


# How much the model may say when deciding what to do about one message, and how
# much of that room thinking is allowed to take before the answer has to be
# written.
#
# This was 900, and then 2400, and neither was enough for the job rather than a
# saving on it. The deliberation writes its whole answer as one JSON object, so
# the ceiling has to hold the envelope *and* the words the person is about to
# read -- and on a model that reasons, that reasoning is spent out of this same
# ceiling and is billed inside `completion_tokens`. So the ceiling was never only
# about the answer: it was about the answer plus however long the model thought
# first, which is a quantity this program does not control.
#
# That is not a theory about this repository. Measured against the base model in
# `~/.ethos/base_model.yaml`, on the messages that produced the silence people
# were reporting, an answer failed to arrive 4 times in 6 at 900, about half the
# time at 2400, once in 12 at 8000, and not once in 12 at 8000 with reasoning
# bounded. Every one of those calls was `finish_reason: length` with `content`
# absent and thousands of characters of reasoning in its place: the model spent
# the whole budget thinking and wrote nothing at all.
#
# None of this costs anything to fix. `max_tokens` is a ceiling, not a
# reservation -- only the tokens a model actually generates are billed, and a
# deliberation that answers in 300 tokens still answers in 300 tokens however
# much room it was offered.
DELIBERATION_MAX_TOKENS = 8_000

# What is left for the answer is only left if thinking is told to stop somewhere.
# A deliberation is a small judgement about one message and it does not need to
# think for eight thousand tokens to reach one; the reasoning it does need to get
# there is a few hundred, and the same request with this bound produced an answer
# every time in twelve runs against the same base model.
DELIBERATION_REASONING_BUDGET = 400

# Asked once more, with room, when the first answer did not arrive.
#
# The gateway already retries a reply that came back empty or cut off, but it
# retries the *same* request, so it is re-running the same bet on how long the
# model thinks. This is a different question: the deliberation knows what it needs
# (one small object) and knows a reasoning model may need a great deal of room to
# reach one, so it can ask for more rather than ask the same thing again. Bounded
# to a single extra attempt -- two is a habit, three is a bill.
DELIBERATION_ESCALATED_MAX_TOKENS = 24_000

# And the contract restated at the end of the second ask, because room alone does
# not fix an answer of the wrong shape.
#
# The failure that reaches here most is a model that answered a long message
# directly and in prose, with no object around the words. More of the ceiling
# cannot help that: the answer it gave was not too short, it was not a decision.
# What helps is asking for the object again, at the end of the prompt where the
# person's message is not between the instruction and the answer, and saying that
# the last one could not be read -- which is the whole of what the model was
# about to cost the person by doing it twice.
_ASK_AGAIN = (
    "\n\nThis is a second attempt. The previous answer to this message came back without "
    "that JSON object, so nothing could be read from it and they were told the agent had "
    "not answered. Answer with the object this time, and nothing else."
)

# How many times one message is put to a model before it is left alone.
#
# A turn that could not be decided is put back on the queue rather than closed,
# so a model that recovers answers without the person asking twice. Without a
# ceiling that is a loop -- the same question, every cycle, at two model calls
# apiece, for as long as the agent is running. Three is enough for anything that
# is going to clear, because the two calls inside one deliberation have already
# asked three times before this count moves at all.
MAX_DELIBERATION_ATTEMPTS = 3


CONSEQUENTIAL_MARKERS = ("money", "$", "pay", "buy", "sell", "trade", "wire", "contract",
                         "sign", "delete", "deploy", "credentials", "password", "urgent", "asap")

# Markers that only count as a whole word.
#
# A substring test is the wrong tool for words, because the interesting messages
# are full of longer words that contain them: `design` and `assign` contain
# `sign`, `payload` contains `pay`, `contractor` and `rewire` contain `contract`
# and `wire`, `credentialed` and `password-protected` are ordinary phrases in
# technical writing. A message that talks about software is exactly the message
# most likely to contain them, and being *escalated* on the strength of a letter
# is a false positive in the direction that costs the most.
#
# Word boundaries fix that without losing the match, which is why this is a
# boundary test rather than a list edit: `pay` still fires on "pay the invoice"
# and "wire the funds", and stops firing on "the request payload".
#
# A `$` is only money when it is in front of a number.
#
# `$` on its own is the single most common character in a technical message and
# almost never a request to spend something: `"$schema"`, `$PATH`, `$HOME`, `${VAR}`,
# a shell expansion, a jQuery selector, a price column heading. It is also the
# marker that decided the tier for the 2 600-character message this file's
# comments are about -- one JSON key, `"$schema"`, put a question about config
# files into T3, which is the tier with the highest floor and on a single-key
# deployment the one most likely to have nothing able to serve it.
#
# Requiring digits keeps "$50" and "$1,200" and drops the rest. A bare `$` with no
# amount after it is not a request to spend money; it is a variable.
_MONEY_RE = re.compile(r"\$\s?\d")


def is_consequential_text(text: str) -> bool:
    """
    Whether a message is about something with consequences, by its own words.

    Split out of the class because this is a judgement about text and belongs to
    be tested on text, and because `life` keeps a second, narrower copy of this
    list for its own tier choice -- two lists that have to agree, and so are
    better as one function both call.
    """
    lowered = (text or "").lower()
    if _MONEY_RE.search(lowered):
        return True
    return any(
        re.search(rf"\b{re.escape(marker)}\b", lowered)
        for marker in CONSEQUENTIAL_MARKERS
        if marker != "$"
    )


class _ExecutionState:
    """What the agent has actually done, as far as this turn can tell.

    `readable` is the first field and the most important one. It says whether the
    journal and the intentions were both successfully read, which is what decides
    whether the guardrail below is allowed to withhold anything at all. A
    guardrail that fires on missing evidence is a guardrail that silences the
    agent whenever the database is briefly unreachable, and that is a worse outage
    than the one it is guarding against.
    """

    __slots__ = ("readable", "journaled", "blocked", "window_min")

    def __init__(self, readable: bool, journaled: list[str], blocked: list[str],
                 window_min: float):
        self.readable = readable
        self.journaled = journaled
        self.blocked = blocked
        self.window_min = window_min

    @property
    def something_happened(self) -> bool:
        return bool(self.journaled)

    def block(self, self_name: str) -> str:
        return execution_state_block(
            self_name, self.journaled, self.blocked, self.window_min,
            readable=self.readable,
        )


# Sentences that assert an action is happening right now.
#
# The rule is about the present tense, and the boundary is drawn there on purpose.
# "I'll send the invoice on Friday" is a promise about a future moment and is not
# what this catches; "I'm working on it" is a statement about what is happening
# now, which is a fact about the journal and can be checked against it. Catching
# every future-tense "I'll" would withhold the ordinary promise that a
# commission *is*, which the turn is built to make.
#
# So this matches present progressive and the completed-tense assertions that
# describe work already in flight, and nothing else.
_ONGOING_WORK = re.compile(
    r"\b(?:i'?m|i am)\s+(?:now\s+)?(?:working on|working through|on it|looking into|"
    r"taking a (?:look|crack) at|getting (?:started|on)|started on|started working|"
    r"already (?:started|began|been))\b"
    r"|\b(?:i'?ll|i will)\s+(?:get|start|begin)\s+(?:that|this|it|them|on it|going|right)\b"
    r"|\balready\s+(?:on it|under way|underway|started)\b"
    r"|\bon it\b"
    r"|\bin progress\b",
    re.IGNORECASE,
)


def claims_work_is_underway(text: str) -> bool:
    """Whether a reply asserts that something is being worked on right now."""
    return bool(_ONGOING_WORK.search(text or ""))


class SocialCognition:
    """Social deliberation for the Self [Section 12].

    Every inbound message becomes a deliberate choice among reply_now,
    reply_later, acknowledge, act_silently, ask_clarification, ignore — with
    the right to ignore exercised only with a categorized reason [D-05].
    Consequential messages deliberate at the T3 capability floor [P4].
    """

    def __init__(
        self,
        config: EthosConfig,
        gateway: Any = None,
        relationships: Any = None,
        audit: Any = None,
        self_model: Any = None,
        db: Any = None,
    ):
        self.config = config
        self.gateway = gateway
        self.relationships = relationships
        self.audit = audit
        self.self_model = self_model
        self.db = db

    def _is_consequential(self, message: Message) -> bool:
        return is_consequential_text(message.text or "")

    def _t0_rules(self, message: Message) -> SocialDecision | None:
        text = (message.text or "").strip()
        if not text:
            return SocialDecision(
                action=SocialAction.ignore,
                reason="empty message",
                ignore_reason=IgnoreReason.no_response_needed.value,
                urgency=Urgency.low.value,
                tier_used="T0",
            )
        if message.person_id is None and ("http://" in text or "https://" in text) and len(text) < 220:
            return SocialDecision(
                action=SocialAction.ignore,
                reason="unsolicited link-only message from unknown sender",
                ignore_reason=IgnoreReason.spam_or_untrusted.value,
                urgency=Urgency.low.value,
                tier_used="T0",
            )
        return None

    async def deliberate(
        self,
        message: Message,
        energy: float = 0.8,
        focus_title: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        preview: bool = False,
    ) -> SocialDecision:
        rule = self._t0_rules(message)
        if rule is not None:
            if self.audit is not None:
                await self.audit.append(
                    actor="self", category="social",
                    summary=f"T0 ignore: {message.person_id or 'unknown'}",
                    details={"reason_category": rule.ignore_reason, "message_id": str(message.id)},
                )
            return rule

        consequential = self._is_consequential(message)
        tier = Tier.T3 if consequential else Tier.T2
        if self.gateway is None:
            logger.warning("social.deliberate_no_gateway")
            return _undecided(
                "no model gateway; the agent had no opinion to offer",
                tier_used=str(tier.value),
            )
        name = self.config.self_name
        if self.self_model is not None:
            try:
                name = await self.self_model.get("name", self.config.self_name)
            except Exception:
                pass
        # What the person actually said, and what the agent is already on the
        # hook for. Both slots of this prompt existed and both were hardcoded
        # empty, so deliberation saw one message in isolation with no memory of
        # the conversation it was in -- which is the whole reason a capable
        # agent answers "as I said" with no idea what "that" was.
        #
        # And, newly, what the agent has actually done. The other three are
        # intentions: what is focused on, what was agreed, what was said. None of
        # them is evidence that any of it happened, and a deliberation asked to
        # write an honest answer with no evidence of its own will fill the gap
        # with the most cooperative thing available, which is a claim that work
        # is underway. The state block below is the only part of this prompt that
        # can contradict it.
        recent = await self._recent_conversation(message)
        commitments = await self._open_commitments()
        state = await self._execution_state(focus_title)
        prompt_messages = social_deliberation_messages(
            self_name=name,
            message=message,
            focus=focus_title or "(none)",
            now=datetime.now(UTC).isoformat(),
            energy=energy,
            commitments=commitments,
            recent=recent,
            tools=tools,
            execution_state=state.block(name),
            preview=preview,
        )
        data, cut_off, ceiling, failure = await self._ask(prompt_messages, tier, consequential)
        if failure is not None:
            logger.warning("social.deliberate_failed", reason=failure)
            return _undecided(failure, tier_used=str(tier.value))
        if cut_off:
            # Said out loud because the two failures look identical from outside
            # and only one of them is the agent's own doing. A response stopped by
            # the ceiling was not declined and not chosen; reading it as silence
            # is how a full reply ended up answered with nothing.
            logger.warning("social.deliberate_truncated", recovered=bool(data))
        if not _formed_a_decision(data):
            # A reply that cannot be read is not a decision, and this used to be
            # read as one. The parse failure fell through to a default action of
            # `reply_now` carrying no text, and the missing text was then filled
            # in with a fixed sentence — so a provider that was out of keys, out
            # of eligible models or simply answering in prose produced a
            # confident reply in the transcript that the agent never chose to
            # say, and nothing anywhere recorded that it was a stand-in. An
            # unreadable answer is logged and ends the turn in silence.
            #
            # "Could not be read" has to mean could not be read *at all*, though,
            # and it did not. An empty dict was the only thing that counted as
            # unreadable, so an answer that parsed into something real and
            # irrelevant — a fragment recovered from a brace in the preamble, an
            # object cut off before it said anything about this turn — sailed
            # through, took the default `reply_now`, carried no text, and was then
            # rewritten below into a silence with a reason naming it. So the
            # person was told the agent had chosen not to answer, on the record,
            # when nothing had been chosen: the same claim a chosen silence
            # makes, made on the agent's behalf by a parser.
            logger.warning("social.deliberate_unreadable", keys=sorted(data)[:8])
            return _undecided(
                "the model's answer could not be read; nothing said in its place",
                tier_used=str(tier.value),
            )
        action_str = str(data.get("action", "reply_now"))
        try:
            action = SocialAction(action_str)
        except ValueError:
            action = SocialAction.reply_now
        ignore_reason = data.get("ignore_reason")
        if action == SocialAction.ignore and ignore_reason not in IGNORE_REASONS:
            ignore_reason = IgnoreReason.no_response_needed.value
        if action != SocialAction.ignore:
            ignore_reason = None
        decision = SocialDecision(
            action=action,
            reply_texts=_reply_texts(data.get("reply_texts")),
            urgency=str(data.get("urgency", Urgency.normal.value)),
            reason=str(data.get("reason", ""))[:300],
            ignore_reason=ignore_reason,
            creates_intention=bool(data.get("creates_intention", False)),
            intention_spec=data.get("intention_spec") if isinstance(data.get("intention_spec"), dict) else None,
            tier_used=str(tier.value),
        )
        if cut_off:
            # The record says what happened, so a decision read back later is not
            # taken for a whole one. The fields the cut removed are bookkeeping
            # this path already defaults, and `reason` is the only one of them a
            # person would miss.
            note = f"the model's answer was cut off at {ceiling} tokens"
            decision.reason = f"{decision.reason}; {note}" if decision.reason else note
        if not decision.reply_texts and action in (
            SocialAction.reply_now, SocialAction.reply_later, SocialAction.acknowledge,
            SocialAction.ask_clarification,
        ):
            # An action that promises the person words, arrived at without any.
            # Whichever way it happened -- the model chose silence and named an
            # action that speaks, or it wrote the action and lost the text --
            # there is no reply here to send, and this used to stand a fixed
            # sentence in for it so the turn would still look answered. Nothing
            # downstream can tell that sentence from a real one: it is stored as
            # an ordinary outbound message, published on the bus and rendered in
            # an agent bubble, so a provider outage reads in the transcript as
            # the agent talking. The turn is kept as the silence it turned out
            # to be, with the breach logged, and the person is told nothing they
            # did not actually hear.
            logger.warning("social.deliberate_reply_action_without_text")
            decision.action = SocialAction.act_silently
            note = "action promised a reply but carried no text; nothing sent"
            decision.reason = f"{decision.reason}; {note}" if decision.reason else note
        self._withhold_unearned_progress(decision, state)
        return decision

    # ---------------------------------------------------------- honesty

    async def _execution_state(self, focus_title: str | None) -> _ExecutionState:
        """What has actually happened, read from the records rather than recalled.

        Two queries, and the order of the fields in the result is the point of
        the method: the journal first, because it is the only thing here that can
        support a claim that work is underway, and the blocked work second,
        because it is the context in which a claim would be false.

        Both are read, not inferred. The journal is `action_journal`, which is
        written for every action the guardian allowed through, so a step that ran
        is there whether or not it succeeded -- and a step that did not run is
        absent, which is exactly the distinction the guardrail needs and the one
        a focus title cannot make.

        A failure to read either is recorded as unreadable rather than as empty.
        Those are different, and conflating them is the failure this whole change
        is about: "nothing was journaled" is evidence, "I could not look" is not,
        and the second one must not be allowed to withhold anybody's words.
        """
        window = max(0.0, float(self.config.social.execution_state_window_min))
        if self.db is None:
            return _ExecutionState(False, [], [], window)
        since = datetime.now(UTC) - timedelta(minutes=window)
        try:
            rows = await self.db.fetch(
                "SELECT tool, status, ts_start FROM action_journal "
                "WHERE ts_start >= $1 ORDER BY ts_start DESC LIMIT $2",
                since, int(self.config.social.execution_state_max_actions),
            )
        except Exception:
            logger.warning("social.execution_state_journal_failed")
            return _ExecutionState(False, [], [], window)
        journaled = [
            f"{row['tool'] or 'unknown'} -> {row['status'] or 'unknown'}" for row in rows
        ]
        blocked = await self._blocked_work(focus_title)
        return _ExecutionState(True, journaled, blocked, window)

    async def _blocked_work(self, focus_title: str | None) -> list[str]:
        """Work that is parked, stalled at planning, or otherwise not moving.

        `parked` is in the query because a parked intention used to be invisible
        here: the status list stopped at `blocked`, so the one row that carried
        the reason the work had stopped was the one row the deliberation could
        not see. An agent whose planning loop had frozen looked, to the only
        thing that writes its replies, like an agent with nothing to report.

        The planning failure count comes out of the intention's own resume state,
        because that is where the bounded loop puts it: an intention part-way
        through its attempts says so, rather than looking the same as one that has
        just started trying.
        """
        if self.db is None:
            return []
        try:
            rows = await self.db.fetch(
                "SELECT title, status, closed_reason, resume_state FROM intentions "
                "WHERE status IN ('proposed','active','in_progress','blocked','parked') "
                "ORDER BY priority DESC NULLS LAST, deadline ASC NULLS LAST LIMIT $1",
                int(self.config.social.execution_state_max_blocked) + 2,
            )
        except Exception:
            logger.warning("social.blocked_work_query_failed")
            return []
        lines: list[str] = []
        for row in rows:
            title = str(row["title"] or "")[:120]
            status = str(row["status"] or "")
            resume = row["resume_state"]
            if isinstance(resume, str):
                try:
                    resume = json.loads(resume)
                except ValueError:
                    resume = {}
            attempts = (resume or {}).get("plan_attempts") or 0
            stalled = ""
            try:
                attempts = int(attempts)
            except (TypeError, ValueError):
                attempts = 0
            if status == "parked":
                stalled = f"parked: {str(row['closed_reason'] or 'no reason recorded')[:200]}"
            elif attempts:
                stalled = f"planning has failed {attempts} time(s) in a row; nothing else has started"
            if status != "parked" and not attempts:
                continue
            if focus_title and title == focus_title:
                stalled = f"this is the current focus; {stalled}" if stalled else "this is the current focus"
            lines.append(f"[{status}] {title}: {stalled}")
        return lines[: int(self.config.social.execution_state_max_blocked)]

    def _withhold_unearned_progress(
        self, decision: SocialDecision, state: _ExecutionState,
    ) -> None:
        """No reply may assert work that no journaled action backs.

        The rule: saying an action is underway is a claim about the journal, and
        the journal is the thing that settles it. This is the last check on the
        turn, after the answer has been found, parsed and shaped, because it is
        the one that can undo an answer that would otherwise go out intact.

        What it does with a claim is remove it rather than replace it. There is
        deliberately no sentence composed here: everything this codebase sends to
        a person is a word the model chose, and a canned sentence inserted here
        would break that while fixing something else -- it would put an
        explanation in somebody's mouth that they never said and the agent never
        deliberated. So the claim goes, and what is left is:

        * any other reply text, kept. A reply can be half claim and half answer,
          and the answer is still the answer;
        * nothing, and `decided=False`, when the claim was all of it. Then the
          turn ends as what it is -- an answer the agent could not make honestly
          -- and the message stays on the queue for another attempt with the
          blockage now visible in its prompt. It is the same path an answer that
          never arrived takes, which is the right one: from the person's side
          those two are the same experience, and this one has a recorded reason.

        Both outcomes are logged. A guardrail that fires silently is a guardrail
        that looks like the model having a bad day.
        """
        if not state.readable or state.something_happened:
            return
        if not any(claims_work_is_underway(text) for text in decision.reply_texts):
            return
        kept = [text for text in decision.reply_texts if not claims_work_is_underway(text)]
        withheld = len(decision.reply_texts) - len(kept)
        note = (
            f"{withheld} reply text(s) claimed work that is underway with nothing "
            f"journaled in the last {state.window_min:g} minutes; withheld"
        )
        if state.blocked:
            note += f" (blocked: {state.blocked[0][:160]})"
        logger.warning(
            "social.unearned_progress_claim", withheld=withheld, kept=len(kept),
            window_min=state.window_min,
        )
        decision.reply_texts = kept
        decision.reason = f"{decision.reason}; {note}" if decision.reason else note
        if not kept:
            # Nothing left to say, and the action cannot stand on its own any
            # more: `reply_now` with no text is already handled above, and this is
            # the same shape of turn seen from the other end.
            decision.action = SocialAction.act_silently
            decision.decided = False
            decision.creates_intention = False
            decision.intention_spec = None

    async def _ask(
        self, prompt_messages: list[dict[str, str]], tier: Tier, consequential: bool,
    ) -> tuple[dict[str, Any], bool, int, str | None]:
        """The decision object, and what went wrong if there was none.

        Two attempts, and the second one is different rather than the same again.
        The gateway retries an empty or cut-off reply, which helps when the model
        had a bad moment and helps not at all when it was asked the same question
        with the same ceiling: a reasoning model that spent the whole budget
        thinking will do it again, and the retry inherits the bet. So this asks
        the same question with more room -- bounded, so a model that never
        answers costs two calls and not the rest of the afternoon.

        More room is the right second question when the answer ran out of it and
        the wrong one when the answer was not the shape asked for. An answer in
        prose is not short, so it is also asked for again -- with the contract
        restated at the end of the prompt rather than only at the top, which is
        what that failure needs, and which is the reason the second ask is not
        the first one repeated.

        A prose answer is kept rather than discarded if even that does not
        produce an object, because those words are the answer and are the only
        ones the person will get. It is asked for the object first: a recovered
        reply carries no `creates_intention` and no `intention_spec`, so an "I'll
        do it" recovered this way is a promise with nothing behind it, which is
        the one thing the prompt above goes to some length to prevent.

        Returns `(data, cut_off, ceiling_used, failure)`. `failure` is the
        sentence that will be recorded against the turn, so it says what actually
        happened: an answer that never arrived is not an answer that could not be
        read, and the person is told which of the two they are looking at.
        """
        last: str = "the model could not be reached; nothing said in its place"
        # The best prose answer seen, if any. Held rather than used immediately
        # so the object gets asked for first, and used at the end so that an
        # answer the model did write is never thrown away.
        prose: str | None = None
        # The same, for an answer that arrived as an object under other names.
        fallback: dict[str, Any] | None = None
        for attempt, ceiling in enumerate((DELIBERATION_MAX_TOKENS, DELIBERATION_ESCALATED_MAX_TOKENS)):
            if attempt and last.startswith("the model could not be reached"):
                # The provider is not answering at all. More room does not
                # change that, and asking a second time spends money to be told
                # the same thing.
                return {}, False, DELIBERATION_MAX_TOKENS, last
            user_text = prompt_messages[1]["content"] + (_ASK_AGAIN if attempt else "")
            request = ModelRequest(
                tier=tier,
                purpose="social.deliberate",
                budget_bucket="discretionary",
                consequence_class="consequential" if consequential else "routine",
                messages=[
                    ModelMessage(role=Role.system, content=prompt_messages[0]["content"]),
                    ModelMessage(role=Role.user, content=user_text),
                ],
                max_tokens=ceiling,
                reasoning_budget=DELIBERATION_REASONING_BUDGET,
            )
            try:
                response = await self.gateway.complete(request)
            except Exception:
                logger.exception("social.deliberate_call_failed", attempt=attempt + 1)
                last = "the model could not be reached; nothing said in its place"
                continue
            data = _parse_json(response.text or "")
            cut_off = was_truncated(response.finish_reason)
            if _formed_a_decision(data):
                # Recovered from a reply the provider stopped: usable, but the
                # record says so, because a decision read back later is not to
                # be taken for a whole one.
                return data, cut_off, ceiling, None
            if not cut_off and _prose_body(response.text) is not None:
                prose = response.text or ""
                last = "the model answered in prose instead of the decision object"
            elif not cut_off and (wrong_keys := _object_as_the_reply(data)) is not None:
                # The object was written, and it is not the one that was asked
                # for: same words, different key. Held rather than used now, for
                # the same reason prose is -- the object is asked for again
                # first, and a model that produces the right one is believed
                # over the wrong one it wrote a moment earlier.
                if fallback is None:
                    fallback = wrong_keys
                last = (
                    "the model wrote the answer inside a differently named "
                    "field rather than in reply_texts"
                )
            elif not (response.text or "").strip():
                last = (
                    "the model thought at length and wrote no answer; "
                    "nothing said in its place"
                )
            elif cut_off:
                last = (
                    f"the model's answer was cut off at {ceiling} tokens; "
                    "nothing said in its place"
                )
            else:
                last = "the model's answer could not be read; nothing said in its place"
            logger.warning(
                "social.deliberate_no_answer",
                attempt=attempt + 1,
                ceiling=ceiling,
                finish_reason=response.finish_reason,
                reasoning=sum(len(tb.text) for tb in response.thinking_blocks),
                cut_off=cut_off,
                prose=len(prose) if prose else 0,
            )
        recovered = _prose_as_the_reply(prose)
        if recovered is not None:
            logger.warning(
                "social.deliberate_prose_answer",
                chars=len(recovered["reply_texts"][0]),
            )
            return recovered, False, DELIBERATION_ESCALATED_MAX_TOKENS, None
        if fallback is not None:
            logger.warning(
                "social.deliberate_wrong_keys",
                chars=len(fallback["reply_texts"][0]),
            )
            return fallback, False, DELIBERATION_ESCALATED_MAX_TOKENS, None
        return {}, False, DELIBERATION_ESCALATED_MAX_TOKENS, last

    async def _recent_conversation(self, message: Message) -> str:
        """The last few turns of this conversation, oldest first.

        Scoped to the conversation key rather than the person, because a person
        reachable on two channels has two conversations and only the key knows
        which one this message is in. The message being answered is excluded --
        it is already in the prompt as the thing to respond to, and repeating it
        would let a long message crowd out the history that explains it.
        """
        if self.db is None or not message.conversation_key:
            return ""
        try:
            rows = await self.db.fetch(
                "SELECT direction, person_id, sender_name, sender_username, text, channel "
                "FROM messages "
                "WHERE conversation_key = $1 AND id <> $2 "
                "ORDER BY ts DESC LIMIT $3",
                message.conversation_key, message.id,
                self.config.memory.context.history_turns,
            )
        except Exception:
            logger.warning("social.conversation_history_failed")
            return ""
        if not rows:
            return ""
        detail = self.config.memory.context.detail_chars
        lines: list[str] = []
        for row in reversed(rows):
            # `sender_label`, not a bare `person_id`: a history in which one turn
            # says `alice (wa:819012345678)` and the next says `tg:819012345678`
            # teaches the model that two people are writing, which is the reading
            # a channel-switched conversation invites. The label is the same
            # function the prompt below it uses, so one turn cannot be more
            # specific than the other.
            who = "me" if row["direction"] == "outbound" else sender_label(
                row["sender_name"], row["sender_username"], row["person_id"],
            )
            text = " ".join((row["text"] or "").split())
            if len(text) > detail:
                text = text[: detail - 1].rstrip() + "…"
            lines.append(f"{who} [{row['channel']}]: {text}")
        return "\n".join(lines)

    async def _open_commitments(self) -> str:
        """What the agent is already on the hook for.

        Read straight from the commitments table rather than from the workspace,
        because a message can arrive on a cycle that never assembles one. Saying
        yes to something already owed is the common way an agent without this
        ends up double-booking itself.
        """
        if self.db is None:
            return ""
        try:
            rows = await self.db.fetch(
                "SELECT title, status, priority, commissioned_by FROM intentions "
                "WHERE status IN ('proposed', 'active', 'in_progress', 'blocked') "
                "ORDER BY priority DESC NULLS LAST, deadline ASC NULLS LAST LIMIT 8",
            )
        except Exception:
            logger.warning("social.commitments_query_failed")
            return ""
        if not rows:
            return ""
        return "\n".join(
            f"- [{row['status']}] {row['title']} "
            f"(priority={row['priority']}, by={row['commissioned_by'] or 'self'})"
            for row in rows
        )

    async def record_ignore(self, person_id: str | None, reason: str | None) -> dict[str, Any]:
        """Counts a silence against the person's streak, once, where it happens.

        Deliberating an ignore and carrying it out are different moments, and the
        as well — so a message re-deliberated after a failed cycle counted its own
        silence twice — while the act called a `record_ignore` that did not exist
        on this class and raised, which left the message unhandled at the head of
        the queue with nothing anywhere saying the agent had chosen to be quiet.

        Returns the relationship store's streak report, whose `review` flag says
        the silence has run long enough to be worth a second look.
        """
        if self.relationships is None:
            return {"count": 0, "review": False}
        return await self.relationships.record_ignore(person_id, reason)

    async def pending_inbound(self, limit: int = 5) -> list[Message]:
        if self.db is None:
            return []
        rows = await self.db.fetch(
            "SELECT id, ts, direction, channel, conversation_key, person_id, text, attachments, "
            "in_reply_to, intention_id, social_decision, delivery, sender_name, sender_username "
            "FROM messages "
            "WHERE direction = 'inbound' AND ("
            "  social_decision IS NULL"
            "  OR (social_decision->>'decided') = 'false'"
            "      AND COALESCE((social_decision->>'attempts')::int, 0) < $2"
            ") ORDER BY ts ASC LIMIT $1",
            limit, MAX_DELIBERATION_ATTEMPTS,
        )
        out: list[Message] = []
        for row in rows:
            out.append(self._row_to_message(row))
        return out

    @staticmethod
    def _row_to_message(row: Any) -> Message:
        attachments = row["attachments"]
        if isinstance(attachments, str):
            attachments = json.loads(attachments)
        social_decision = row["social_decision"]
        if isinstance(social_decision, str):
            social_decision = json.loads(social_decision)
        delivery = row["delivery"]
        if isinstance(delivery, str):
            delivery = json.loads(delivery)
        return Message(
            id=row["id"], ts=row["ts"], direction=row["direction"], channel=row["channel"],
            conversation_key=row["conversation_key"] or "", person_id=row["person_id"],
            text=row["text"] or "", attachments=list(attachments or []),
            in_reply_to=row["in_reply_to"], intention_id=row["intention_id"],
            social_decision=dict(social_decision or {}), delivery=dict(delivery or {}),
            sender_name=row["sender_name"], sender_username=row["sender_username"],
        )

    async def mark_handled(self, message: Message, decision: dict[str, Any] | None = None) -> None:
        if self.db is None:
            return
        payload = json.dumps(decision or {"action": "handled"})
        await self.db.execute(
            "UPDATE messages SET social_decision = $1::jsonb WHERE id = $2 AND "
            "(social_decision IS NULL OR (social_decision->>'decided') = 'false')",
            payload, message.id,
        )

    async def record_failure(self, message: Message, decision: dict[str, Any]) -> dict[str, Any]:
        """Writes down a turn nobody decided, and counts the attempt.

        `mark_handled` retires a message from the queue, which is the right end
        for a decision and the wrong one for a turn in which no model answered:
        that closed the question as though it had been answered with a considered
        silence, permanently, so a model that was briefly out of room left the
        person with nothing and no record that anything had been tried.

        The attempt count lives in the same column rather than in a new one,
        because it is bookkeeping about a decision that was never formed and
        there is no decision to attach it to. `pending_inbound` reads it back to
        decide whether to ask again; once it reaches `MAX_DELIBERATION_ATTEMPTS`
        the row stops being pending and is left as the readable record it is.

        It overwrites a previous failure and nothing else. `mark_handled` writes
        the same column and has to be able to retire a message that failed first,
        or a question nobody could answer would be answered on the second attempt
        and then sit on the queue for two more; but a decision the agent actually
        formed is never replaced by a failure that arrived afterwards.
        """
        attempts = int(message.social_decision.get("attempts", 0) or 0) + 1
        payload = {**message.social_decision, **decision, "attempts": attempts}
        if self.db is None:
            return payload
        await self.db.execute(
            "UPDATE messages SET social_decision = $1::jsonb WHERE id = $2 AND "
            "(social_decision IS NULL OR (social_decision->>'decided') = 'false')",
            json.dumps(payload), message.id,
        )
        return payload

    async def record_inbound(self, message: Message) -> None:
        if self.db is None:
            return
        await self.db.execute(
            "INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, text, "
            "attachments, in_reply_to, intention_id, sender_name, sender_username) "
            "VALUES ($1, $2, 'inbound', $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11) "
            "ON CONFLICT (id) DO NOTHING",
            message.id, message.ts, message.channel, message.conversation_key,
            message.person_id, message.text, json.dumps(message.attachments),
            message.in_reply_to, message.intention_id,
            message.sender_name, message.sender_username,
        )

    async def streak_self_review(self, person_id: str | None, reasons: list[str], n: int,
                                  latest_text: str = "") -> dict[str, Any]:
        if self.gateway is None:
            return {"continue_ignoring": True, "should_message": False, "message": None,
                    "reflection": "no gateway for self-review"}
        name = self.config.self_name
        messages = streak_review_messages(name, person_id or "someone", reasons, n,
                                          latest=latest_text)
        request = ModelRequest(
            tier=Tier.T3,
            purpose="social.streak_review",
            budget_bucket="discretionary",
            messages=[ModelMessage(role=Role.user, content=messages[0]["content"])],
            max_tokens=700,
        )
        try:
            response = await self.gateway.complete(request)
            data = _parse_json(response.text or "")
            if data:
                return data
        except Exception:
            logger.exception("social.streak_review_failed")
        return {"continue_ignoring": True, "should_message": False, "message": None,
                "reflection": "self-review unavailable"}


def _undecided(reason: str, tier_used: str) -> SocialDecision:
    """A turn with no decision in it, and therefore nothing to say.

    Silence is one of the agent's own choices [D-05] and this is not one: no
    model answered, so no opinion was formed. The two are still kept apart in
    the record rather than smoothed into each other, because `act_silently`
    does not count against the person's streak and does not claim the agent
    considered the question -- it only says nothing went out, and why.

    `decided=False` is what keeps that apart all the way to the person. The
    action alone could not: a surface reading `act_silently` for the message it
    just asked about says the agent heard the question and declined it, and that
    is what a model that ran out of room before writing anything was reported
    as -- so somebody was told the agent had chosen silence when the truth was
    that it had not managed to think. The reason travels with the decision so
    the agent's own context and the `social_decision` row both say what
    happened, and nothing is invented for the person to read: an empty
    `reply_texts` reaches the transcript as no message at all, which is the only
    honest version of an answer that does not exist.
    """
    return SocialDecision(
        action=SocialAction.act_silently,
        reply_texts=[],
        urgency=Urgency.normal.value,
        reason=reason,
        tier_used=tier_used,
        decided=False,
    )


def _formed_a_decision(data: dict[str, Any]) -> bool:
    """Whether what came back says anything at all about what to do.

    Two members carry that: which action to take, and what to say for it. An
    answer with neither is not a decision in a worse state than an empty one --
    it is an answer this parser could not make sense of, and reading it as a
    decision is what turned a parse failure into a silence the agent was recorded
    as having chosen. The default action is kept for an answer that names an
    action this build does not know, which is a different thing entirely: the
    model chose, and the choice is not in the vocabulary.
    """
    return bool(data) and ("action" in data or "reply_texts" in data)


def _reply_texts(raw: Any) -> list[str]:
    """The reply the model actually wrote, and nothing else.

    Two shapes of a perfectly ordinary model response used to end badly here.
    An explicit `"reply_texts": null` raised `TypeError` out of the
    deliberation, taking the life loop's turn with it; and a bare string in
    place of the list was iterated character by character, so a one-word
    answer left the person receiving it one letter at a time. Whitespace-only
    entries are dropped for the same reason the text check below depends on:
    an entry with no words in it is not a reply, and counting it as one is
    what let a blank answer through as though it were a real one.
    """
    if not isinstance(raw, list):
        return []
    texts: list[str] = []
    for entry in raw[:3]:
        if isinstance(entry, str) and entry.strip():
            texts.append(entry.strip())
    return texts


_JSON_WHITESPACE = " \t\r\n"

_DECODER = json.JSONDecoder()


def _parse_json(text: str) -> dict[str, Any]:
    """The decision object in a model answer, wherever in the answer it sits.

    This used to be the first `{` in the response through the last `}` in it, on
    the assumption that a model answering "strict JSON only" puts its object at
    the very beginning and writes nothing else. That assumption is false for
    every model that thinks before it answers, and the cost of it was paid by the
    person waiting: `find("{")` landed on a brace inside the preamble — a
    "let me weigh `{action}` against `{reply_texts}`", a schema echoed back before
    the answer, a code fragment quoted from the message — so the slice ran from
    the wrong opening brace to the last closing one, `json.loads` refused it, and
    the whole answer was discarded. Not the tail of it and not the reply: the
    answer, which had been written out complete, with `action` and the sentence
    the person was about to read.

    Nothing was found, so the turn ended in `_undecided` — a recorded silence, a
    handled message, nothing to retry — and the reason on the message said the
    model's answer could not be read. Which is what the transcripts of this agent
    said, over and over, for answers that were sitting there complete.

    So the object is *found* rather than assumed. Every brace in the answer is
    tried as an opening one, in order, and the last object that decodes whole and
    names an action is the decision: last, because a model that drafts and then
    revises writes the answer it settled on last, and the draft is not what it
    chose. An answer the ceiling cut off never decodes whole at all, so those
    fall through to `_parse_cut_off`.
    """
    found: list[dict[str, Any]] = []
    index = 0
    while (start := text.find("{", index)) >= 0:
        try:
            parsed, end = _DECODER.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        index = max(end, start + 1)
        if isinstance(parsed, dict):
            found.append(parsed)
    for candidate in reversed(found):
        if "action" in candidate:
            return candidate
    if not found:
        # Nothing in the answer closed, which is what the ceiling leaves behind.
        return _parse_cut_off(text)
    return found[-1]


def _parse_cut_off(text: str) -> dict[str, Any]:
    """The members of an object a cut-off answer had already finished writing.

    A deliberation puts what to do and what to say first, so a response the
    provider stopped in the middle of has very often already said both: the run
    that motivated this ended on `"ignore_reason": [], "` with a complete action
    and a complete reply in hand, and discarding it because the last brace never
    arrived sent the person silence instead of the sentence the model had
    written for them.

    So the members that closed are kept and the one being written is not. Every
    value goes through the stdlib parser, which is what makes this safe to trust:
    a half-written string or a half-written list fails to decode and is simply
    left out rather than guessed at, so the reply a person receives is never a
    fragment. Nothing is invented and nothing is completed -- an object cut off
    before its first member yields nothing, and the caller still ends the turn in
    a recorded silence.

    The whole answer is passed in and the opening brace is found here rather than
    by the caller, because the caller was finding it the same wrong way this used
    to: at the first `{` in the response, which on a cut-off answer preceded by
    any preamble at all is a brace inside that preamble. Each brace in turn is
    tried and the first one that yields a member is the object, so the recovery
    does not inherit the bug it exists to cover.
    """
    index = 0
    while (start := text.find("{", index)) >= 0:
        recovered = _closed_members(text, start)
        if recovered:
            return recovered
        index = start + 1
    return {}


def _closed_members(body: str, start: int) -> dict[str, Any]:
    """The members of the object opening at `start` whose values closed."""
    index = start + 1
    recovered: dict[str, Any] = {}
    while True:
        while index < len(body) and body[index] in _JSON_WHITESPACE + ",":
            index += 1
        if index >= len(body) or body[index] == "}":
            break
        try:
            key, index = _DECODER.raw_decode(body, index)
        except ValueError:
            break
        if not isinstance(key, str):
            break
        while index < len(body) and body[index] in _JSON_WHITESPACE:
            index += 1
        if index >= len(body) or body[index] != ":":
            break
        index += 1
        while index < len(body) and body[index] in _JSON_WHITESPACE:
            index += 1
        try:
            value, index = _DECODER.raw_decode(body, index)
        except ValueError:
            break
        recovered[key] = value
    return recovered


_FENCE_RE = re.compile(r"\A```[^\n]*\n(.*?)\n?```\Z", re.DOTALL)


def _prose_body(text: str | None) -> str | None:
    """The words of an answer that has no JSON object in it, or None if it has one.

    The distinction that matters here is not "does it parse" -- it does not --
    but "is there a JSON object in it at all". An answer that is one object and
    nothing else has said something about the protocol and got it wrong, and the
    person is owed the truth about that rather than a sentence lifted out of the
    middle of a literal `{}`. An answer with no object anywhere in it is prose,
    and prose in a deliberation is the reply.

    Checked by decoding the answer whole: if a single JSON value accounts for all
    of it, it *is* the answer and there is nothing else to read. A brace inside a
    string does not count, and neither does a brace in prose that happens to look
    like one -- `raw_decode` fails on the text before that brace, which is what
    puts it in the prose branch, and it is the right place for it.

    A fence goes with it, because a fence is something the answer was wrapped in
    for a reader and not something to send to a person.
    """
    if not text or not text.strip():
        return None
    fenced = _FENCE_RE.match(text.strip())
    body = (fenced.group(1) if fenced else text).strip()
    if not body:
        return None
    try:
        _, end = _DECODER.raw_decode(body, 0)
    except ValueError:
        return body
    return None if not body[end:].strip() else body


def _prose_as_the_reply(text: str | None) -> dict[str, Any] | None:
    """An answer written as prose, in the shape it plainly meant to be in.

    A deliberation asks for a small object and gets prose instead often enough
    to matter on long messages: the model reads the person's words, answers
    them, and writes none of the four fields around them. The words it wrote
    *are* the reply -- they are the only answer anybody is going to get, and
    they were chosen for the person rather than assembled here.

    So they are used, as a reply, and nothing else is invented: no action was
    weighed, no urgency was assigned, and no work is commissioned out of an
    answer that carries no `intention_spec` to commission it from. That last
    part is why the object is asked for again before this is reached -- an "I'll
    do it" recovered from prose is a promise with nothing behind it, which is
    the one thing the prompt above is built to avoid -- and why the reason says
    so on the turn: the row records that this reply was recovered, so nobody
    reads it later as a decision the agent formed in the shape it was asked for.
    """
    body = _prose_body(text)
    if body is None:
        return None
    return {
        "action": "reply_now",
        "reply_texts": [body],
        "urgency": Urgency.normal.value,
        "reason": (
            "answered in prose rather than the decision object, so these are the "
            "model's own words and nothing was commissioned from them"
        ),
        "creates_intention": False,
    }


# The names a model reaches for when it means `reply_texts`.
#
# The object asked for is `{action, reply_texts, urgency, reason, ignore_reason,
# creates_intention, intention_spec}` and a model that gets the shape wrong most
# often gets it *nearly* right: it writes the words it would have sent and calls
# the field `reply`, or `response`, or `answer`, and leaves the rest out. That
# answer is not unreadable. It is complete, and it is the only reply there is.
#
# These are names that mean *the words to send the person*, and nothing else
# qualifies. `note`, `comment`, `summary`, `explanation` and `body` are names for
# a description of an answer rather than the answer, and this file already draws
# that line deliberately: `{"note": "still deciding"}` is an object that says
# nothing about the turn, and reading "still deciding" out of it and sending it
# would be the same failure as rendering the braces -- a person given the agent's
# working, or a non-answer, in place of a reply. The same goes for the opposite
# mistake of scanning every string in the object: a model that echoed the config
# it was discussing (`{"permission": {"edit": "allow"}}`) would have "allow"
# delivered as a reply, and the turn marked answered.
_OBJECT_TEXT_KEYS = (
    "reply_texts", "reply_text", "reply", "replies",
    "response", "answer", "message", "text",
)


def _texts_under(data: dict[str, Any], depth: int = 0) -> list[str] | None:
    """The words under a reply-ish key, or None if there are none.

    Two passes at each level, and the order matters. First the keys this module
    recognises as meaning "the words to send"; then, only if there were none, a
    descent into the object's own sub-objects -- because the same slip one step
    down is still the same slip, and `{"result": {"answer": "..."}}` is an answer
    keyed wrongly rather than a different kind of answer.

    One level deep and no further. Deeper than that is guessing at a structure
    nobody agreed on, and the whole point of restricting this to recognised keys
    is that the difference between an answer and a non-answer stays a fact
    rather than a guess.
    """
    for key in _OBJECT_TEXT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        if isinstance(value, list):
            texts = [e.strip() for e in value if isinstance(e, str) and e.strip()]
            if texts:
                return texts
    if depth < 1:
        for value in data.values():
            if isinstance(value, dict):
                nested = _texts_under(value, depth + 1)
                if nested:
                    return nested
    return None


def _object_as_the_reply(data: dict[str, Any]) -> dict[str, Any] | None:
    """An answer that arrived as an object under other names, in the right shape.

    The third way a deliberation's answer comes back wrong, after "as prose" and
    "cut off": a single JSON value that parses, that is not the object asked for,
    and that has words in it. It used to end the turn as "the model's answer
    could not be read", which is what the person was shown -- on a question that
    had been answered in full.

    It is likelier the longer the message, and that is the part worth stating.
    A short prompt gives a model nothing to be tempted by. A long technical one
    is full of JSON, and the shape most likely to come back is the model holding
    the *subject* of the conversation rather than the decision: the 2 600-character
    message behind this file's comments is a discussion of an `opencode.json`
    permission block, so an answer that came back as `{"permission": {...}}` or
    `{"changes": [...]}` was the model being wrong about the envelope, not silent.

    What is *not* recovered is the case this file already draws: an object with
    no words in it. `{}` and `{"draft": {"x": 1}}` stay unreadable, because there
    is nothing there to send and a person who gets a turn marked answered with
    no words in it is worse off than one told nothing arrived.

    As with prose, nothing is invented: the words are the model's own, no action
    was weighed, and no work is commissioned out of an answer with no
    `intention_spec` behind it -- which is why the object is asked for again
    before this is reached, and why the reason on the turn says where the reply
    came from.
    """
    if not isinstance(data, dict) or not data:
        return None
    texts = _texts_under(data)
    if not texts:
        return None
    return {
        "action": "reply_now",
        "reply_texts": texts[:3],
        "urgency": Urgency.normal.value,
        "reason": (
            "answered inside a differently named field rather than in "
            "reply_texts, so these are the model's own words and nothing was "
            "commissioned from them"
        ),
        "creates_intention": False,
    }
