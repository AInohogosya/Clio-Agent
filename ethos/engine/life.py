from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ethos.config import EthosConfig
from ethos.engine.attention import Attention, centroid_of
from ethos.engine.drives import DriveInputs, Motivation
from ethos.engine.rhythm import RhythmController
from ethos.engine.states import State, StateMachine
from ethos.engine.workspace import WorkspaceAssembler
from ethos.observability import metrics
from ethos.observability.logger import bind_context, get_logger
from ethos.schemas.context import TurnKind
from ethos.schemas.events import Event, EventKind, Percept
from ethos.schemas.gsl import Decision, Focus, Outcome, SolverOutcome
from ethos.schemas.reverie import ReverieResult
from ethos.social.conversation import open_conversations, render_conversations

logger = get_logger("ethos.life")

# How long a parked agent waits between heartbeats. Well inside a surface's
# patience and inside the longest tick a working agent has, so "paused" is
# reported as paused rather than as silence.
PARKED_HEARTBEAT_S = 30.0


class LifeDeps:
    """Everything the Self needs. Assembled by ethos.core.runner."""

    def __init__(
        self,
        config: EthosConfig,
        control: Any,
        bus: Any,
        memory: Any,
        self_model: Any,
        attention: Attention,
        motivation: Motivation,
        rhythm: RhythmController,
        gateway: Any,
        gsl: Any,
        social: Any,
        comms_out: Any,
        toolhost: Any,
        audit: Any,
        continuity: Any,
        consolidation: Any,
        embedder: Any,
        context: Any = None,
        reverie: Any = None,
        thread_id: str = "self",
    ):
        self.config = config
        self.control = control
        self.bus = bus
        self.memory = memory
        self.self_model = self_model
        self.attention = attention
        self.motivation = motivation
        self.rhythm = rhythm
        self.gateway = gateway
        self.gsl = gsl
        self.social = social
        self.comms_out = comms_out
        self.toolhost = toolhost
        self.audit = audit
        self.continuity = continuity
        self.consolidation = consolidation
        self.embedder = embedder
        self.context = context
        self.reverie = reverie
        self.thread_id = thread_id


class Life:
    """The Self: perceive -> attend -> reflect -> decide -> act -> record.

    Waiting is a first-class state; the process never exits [P1, 4.1].
    """

    def __init__(self, deps: LifeDeps):
        self.deps = deps
        self.config = deps.config
        self.control = deps.control
        self.bus = deps.bus
        self.memory = deps.memory
        self.states = StateMachine(
            initial=State.RESUMING,
            thresholds=self.config.rhythm.attention.interrupt_thresholds,
        )
        self.assembler = WorkspaceAssembler(self.config)
        self.context = deps.context
        self.reverie = deps.reverie
        if self.context is None:
            # Never absent. A cycle with no context to carry is the failure this
            # whole layer exists to remove, so a Life built without one gets a
            # working buffer rather than an empty slot.
            from ethos.memory.context import ContextLayer

            self.context = ContextLayer(
                deps.config, memory=deps.memory, gateway=deps.gateway,
                thread_id=deps.thread_id,
            )
        self.focus: Focus | None = None
        self.spent_discretionary_session = 0.0
        self.cycles = 0
        self._percept_queue: Any = None
        self._thoughts: list[str] = []
        self._consolidation_done_tonight: str | None = None

    # ------------------------------------------------------------- wake

    async def wake_up(self) -> None:
        import json as _json

        report = await self.deps.continuity.wake_up()
        snapshot = report.get("snapshot") or {}
        focus_state = snapshot.get("focus")
        if isinstance(focus_state, str):
            try:
                focus_state = _json.loads(focus_state)
            except ValueError:
                focus_state = None
        if focus_state:
            try:
                restored = Focus.model_validate(focus_state)
                if restored.intention_id is not None:
                    intention = await self.memory.get_intention(restored.intention_id)
                    if intention is not None and intention.is_open():
                        self.focus = restored
                        logger.info("life.focus_restored", intention_id=str(restored.intention_id))
            except Exception:
                logger.exception("life.focus_restore_failed")
        # Pick the thread back up where it was left, so waking is not the same
        # as starting over. The narrative survives in the snapshot; the raw turns
        # are the newest few, which is what resuming mid-thought needs.
        self.context.restore(snapshot.get("context"))
        self.deps.rhythm.mark_awake()
        self.states.transition(State.THINKING_REFLECTING, "wake")
        await self.deps.audit.append(
            actor="self", category="lifecycle", summary="woke up",
            details={"report": {k: v for k, v in report.items() if k != "snapshot"}},
        )
        bind_context(thread_id=self.deps.thread_id)
        await self._publish_presence()

    # -------------------------------------------------------- perceiving

    def attach_queue(self, queue: Any) -> None:
        self._percept_queue = queue

    async def perceive(self) -> list[Percept]:
        percepts: list[Percept] = []
        queue = self._percept_queue
        if queue is None:
            return percepts
        while not queue.empty():
            try:
                item = queue.get_nowait()
            except Exception:
                break
            if isinstance(item, Percept):
                percepts.append(item)
            elif isinstance(item, Event):
                percepts.append(self._percept_from_event(item))
        return percepts

    def _percept_from_event(self, event: Event) -> Percept:
        from ethos.schemas.events import percept_from_event

        return percept_from_event(event)

    # ---------------------------------------------------------- bookmark

    async def bookmark(self, focus: Focus | None) -> None:
        if focus is None or focus.intention_id is None:
            return
        from ethos.schemas.gsl import Bookmark

        await self.memory.set_bookmark(
            focus.intention_id,
            Bookmark(
                next_planned_step=focus.bookmark.get("next_planned_step"),
                mental_context=focus.bookmark.get("mental_context") or focus.why,
            ),
        )

    # ----------------------------------------------------- drive context

    async def _drive_inputs(self, percepts: list[Percept]) -> DriveInputs:
        # Deliberately not `list_intentions`. That asks for the top N by priority,
        # which is the wrong question here: a priority backlog can rank a real
        # commissioned deadline below the cut and hide it completely, and the
        # drives then weigh only the work they can see. See
        # `MemoryStore.list_attentionable_intentions`.
        intentions = await self.memory.list_attentionable_intentions(limit=50)
        questions = await self.memory.open_questions(limit=50)
        attempts_failed = 0
        if self.focus is not None and self.focus.intention_id is not None:
            attempts = await self.memory.list_attempts(self.focus.intention_id, limit=5)
            attempts_failed = sum(1 for a in attempts if a.outcome == "failed")
        last_contact = await self.memory.db.fetchrow(
            "SELECT MAX(ts) AS last FROM messages WHERE direction = 'inbound'"
        ) if getattr(self.memory, "db", None) is not None else None
        last_contact_age_h = None
        if last_contact is not None and last_contact["last"] is not None:
            last_contact_age_h = (datetime.now(UTC) - last_contact["last"]).total_seconds() / 3600.0
        return DriveInputs.from_intentions(
            intentions,
            open_questions=len(questions),
            failed_recent=attempts_failed,
            active_ventures=sum(1 for i in intentions if i.kind == "venture"),
            unread_events=len(percepts),
            last_contact_age_h=last_contact_age_h,
            energy=self.deps.rhythm.energy(self.spent_discretionary_session),
        )

    async def _choose_focus(self, percepts: list[Percept]) -> Focus | None:
        inputs = await self._drive_inputs(percepts)

        async def _create(**kwargs: Any) -> Any:
            return await self.memory.create_intention(**kwargs)

        focus, _pressures = await self.deps.motivation.choose_focus(
            inputs, self.deps.rhythm.energy(self.spent_discretionary_session),
            create_intention_fn=_create,
        )
        return focus

    async def _roll_on_focus_change(self, before: Focus | None, after: Focus | None) -> None:
        """When the agent switches what it is working on, say so.

        A change of focus is the natural seam in a life: the work just finished,
        so the turns it produced have an end, and writing them down now is both
        cheaper and clearer than waiting for the buffer to happen to fill. It is
        also the point where the agent is most likely to be asked what it was
        just doing, and is least likely to be able to say.
        """
        if not self.config.memory.context.narrate_on_focus_change:
            return
        if before is None and after is None:
            return
        if before is not None and after is not None and before.title == after.title:
            return
        before_title = before.title if before is not None else "(none)"
        after_title = after.title if after is not None else "(none)"
        try:
            await self.context.append(
                TurnKind.event.value,
                f"moved from {before_title} to {after_title}",
                focus=after_title,
            )
            await self.context.roll(after_title)
        except Exception:
            logger.warning("life.focus_roll_failed")

    # ---------------------------------------------------------- workspace

    async def _assemble_workspace(self, percepts: list[Percept]) -> Any:
        identity_doc = await self.deps.self_model.identity_document(
            hard_limits=[], self_name_hint=self.config.self_name,
        )
        tools = []
        if self.deps.toolhost is not None:
            try:
                tools = self.deps.toolhost.catalog()
            except Exception:
                tools = []
        budget = "unknown"
        try:
            state = await self.deps.gateway.budget()
            budget = (
                f"day ${state['day_total']:.2f}/${state['daily_cap']:.2f}, "
                f"commitment ${state['day_commitment']:.2f}, discretionary ${state['day_discretionary']:.2f}"
            )
        except Exception:
            budget = f"session ${self.spent_discretionary_session:.2f}"
        commitments = await self.memory.list_intentions(limit=12)
        memories = []
        query = self.focus.title if self.focus else "recent context"
        try:
            memories = await self.memory.search(query, k=12)
        except Exception:
            memories = []
        thought_texts = self._thoughts[-10:]
        thread, recent = self.context.render_blocks()
        conversations = await self._conversations_block()
        return self.assembler.assemble(
            identity_doc=identity_doc,
            tools=tools,
            focus=self.focus,
            percepts=percepts,
            state=self.states.state.value,
            energy=self.deps.rhythm.energy(self.spent_discretionary_session),
            budget_summary=budget,
            commitments=[
                {
                    "status": c.status, "title": c.title, "priority": c.priority,
                    "deadline": c.deadline.isoformat() if c.deadline else None,
                    "commissioned_by": c.commissioned_by,
                }
                for c in commitments
            ],
            memories=memories,
            thoughts=thought_texts,
            thread=thread,
            recent=recent,
            conversations=conversations,
            now=datetime.now(UTC),
        )

    async def _conversations_block(self) -> str:
        """What has been said to the people, and who owes the next word.

        A cycle could previously assemble nine blocks and not one of them said
        that the agent's last message on a conversation was still unanswered. So
        the working buffer that was supposed to carry that held a paraphrase of
        each turn, which was narrated into one line and then dropped — and a
        person holding the full transcript, able to scroll back through every
        message the agent has sent them this week, was talking to something that
        could not read any of it. It is read from the messages table rather than
        from the buffer, because the buffer is the agent's memory of itself and
        this is the record of what actually went out.
        """
        db = getattr(self.memory, "db", None)
        cfg = self.config.memory.context
        try:
            states = await open_conversations(
                db,
                within_h=cfg.conversations_within_h,
                limit=cfg.conversations_max,
                turns=cfg.history_turns,
                detail_chars=cfg.detail_chars,
            )
        except Exception:
            logger.warning("life.conversations_block_failed")
            return ""
        if not states:
            return ""
        return "## Conversations\n" + render_conversations(states, preview=self._preview())

    def _preview(self) -> bool:
        """Whether the owner has this agent in preview mode, as of this cycle.

        Read through `getattr` because `control` is a plain dependency that tests
        build out of anything with the right shape, and the cross-talk rule must not
        be the thing that turns a missing attribute into a failed cycle. Unknown
        means not previewing, which is the lenient text — the safe reading of an
        ambiguous flag is the one that does not refuse the agent's own ordinary
        conversation, since the strict half costs nothing but candour and the
        lenient half costs a stranger's business.
        """
        return bool(getattr(self.control, "preview", False))

    # ------------------------------------------------------------ decide

    async def decide(self, ws: Any) -> Decision:
        inbound = await self._pending_inbound_messages()

        tier = "T1"
        if inbound:
            tier = "T3" if self._message_is_consequential(inbound[0]) else "T2"
            social_decision = await self.deps.social.deliberate(
                message=inbound[0],
                energy=self.deps.rhythm.energy(self.spent_discretionary_session),
                focus_title=self.focus.title if self.focus else None,
                tools=self._tool_names(),
                preview=self._preview(),
            )
            return Decision(
                kind="social", tier=tier, social=social_decision.model_dump(mode="json"),
                rationale=f"social deliberation for {inbound[0].person_id}",
            )

        if self.focus is not None and self.focus.intention_id is not None:
            return Decision(kind="advance_goal", tier="T2", rationale="advance current intention")

        if self.focus is not None and self.focus.kind == "social":
            return Decision(
                kind="social", tier="T2", social=None,
                rationale="social focus without pending message; nothing to do",
            )

        if self.focus is not None and self.focus.kind == "impulse":
            intention = await self.memory.get_intention(self.focus.intention_id) if self.focus.intention_id else None
            if intention is None:
                return Decision(kind="mind_wander", tier="T1", rationale="impulse needs an intention")
            return Decision(kind="advance_goal", tier="T2", rationale="advance impulse intention")

        if self.reverie is not None and self.deps.rhythm.reverie_due():
            return Decision(
                kind="reverie", tier="T1",
                rationale="idle: choosing what to be drawn to",
            )

        if self.deps.rhythm.mind_wander_due():
            return Decision(kind="mind_wander", tier="T1", rationale="idle: mind-wandering glance")

        return Decision(kind="none", tier="T0", rationale="nothing pending; wait")

    def _message_is_consequential(self, message: Any) -> bool:
        # The same judgement `SocialCognition` makes, for the same reason: this
        # picks the tier the deliberation is about to ask for, and the two
        # answers have to be one answer. It used to be a second, narrower list
        # of trigger words held in this file, so the tier a message was
        # deliberated at and the tier it was labelled with could disagree.
        from ethos.social.deliberation import is_consequential_text

        return is_consequential_text(message.text or "")

    def _tool_names(self) -> list[dict[str, Any]]:
        """The registered tools, for the deliberation that writes the reply.

        Asked of the toolhost rather than hardcoded, so the names the agent is
        told it has are the names it has. A list written into the prompt is a
        second thing that can be wrong, and the failure of getting it wrong is
        the agent telling its owner it cannot do something it can -- see
        `ethos.prompts.deliberation.tool_names_line`.
        """
        toolhost = self.deps.toolhost
        if toolhost is None:
            return []
        try:
            return list(toolhost.catalog())
        except Exception:
            return []

    async def _pending_inbound_messages(self) -> list[Any]:
        return await self.deps.social.pending_inbound(limit=3)

    # --------------------------------------------------------------- act

    async def act(self, decision: Decision) -> Outcome:
        started = time.monotonic()
        outcome = Outcome()
        if decision.kind == "advance_goal" and self.focus is not None:
            result = await self._advance_goal()
            outcome.ok = result.outcome not in (
                SolverOutcome.goal_failed.value, SolverOutcome.parked.value,
            )
            outcome.results.append(result.model_dump(mode="json"))
            outcome.cost_usd += result.cost_usd
            self.spent_discretionary_session += result.cost_usd
        elif decision.kind == "social" and decision.social is not None:
            commissioned = await self._act_social(decision.social)
            outcome.commission = commissioned
        elif decision.kind == "mind_wander":
            await self._mind_wander()
        elif decision.kind == "reverie":
            result = await self._reverie()
            outcome.ok = result.refused == "" and result.committed_id is not None
            outcome.results.append(result.model_dump(mode="json"))
            outcome.cost_usd += result.cost_usd
            self.spent_discretionary_session += result.cost_usd
        if decision.thoughts:
            self._thoughts.extend(decision.thoughts)
            for thought in decision.thoughts:
                await self.memory.record_episode(
                    kind="thought", summary=thought, importance=0.3, half_life_h=12,
                )
                await self._append_turn(
                    TurnKind.thought.value, thought,
                    intention_id=self.focus.intention_id if self.focus else None,
                    focus=self.focus.title if self.focus else None,
                )
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        return outcome

    async def _advance_goal(self) -> Any:
        assert self.focus is not None and self.focus.intention_id is not None
        intention = await self.memory.get_intention(self.focus.intention_id)
        if intention is None:
            from ethos.schemas.gsl import SolverResult

            return SolverResult(outcome=SolverOutcome.goal_failed,
                                 thoughts=["intention vanished"])
        if intention.status in ("done", "cancelled", "failed", "parked"):
            from ethos.schemas.gsl import SolverResult

            return SolverResult(outcome=SolverOutcome.goal_done,
                                thoughts=[f"intention already {intention.status}"])
        ws = await self._assemble_workspace([])
        self.states.transition(State.THINKING_FOCUSED, "goal focus")
        result = await self.deps.gsl.advance(intention, ws)
        for thought in result.thoughts[:3]:
            self._thoughts.append(thought)
        self.spent_discretionary_session += result.cost_usd
        if result.cost_usd and intention.budget_usd is not None:
            await self.memory.add_spend(intention.id, result.cost_usd)
        return result

    async def _act_social(self, social: dict[str, Any]) -> dict[str, str] | None:
        """Carry out one turn's social decision.

        Returns the work this turn committed to, as `{"intention_id", "title"}`, or
        None. The return is the whole point of the change: an instruction to do
        something was answered here and the answer went out promising the work, and
        the intention behind that promise was then dropped on the floor of this
        method. Nothing in the cycle asked for it again, and the next thing the
        agent chose to do came from the drives instead — so the agent said it would
        write the function and went off to maintain its machine. Every surface funnels
        into this method, so this is the one place where a person's request for work
        becomes work or does not.
        """
        from ethos.schemas.messages import SocialDecision

        decision = SocialDecision.model_validate(social)
        # The row's `social_decision` is the only durable record of what the
        # agent chose for this message. `mark_handled` without the decision
        # wrote `{"action": "handled"}` for a reply and for an ignore alike,
        # so the table could say that every message had been dealt with and
        # never what dealing with it had been.
        decided = decision.model_dump(mode="json")
        message = None
        pending = await self.deps.social.pending_inbound(limit=1)
        if pending:
            message = pending[0]
        if message is None:
            return None
        reply_to = str(message.id)
        await self._record_inbound_turn(message)
        if decision.action.value == "ignore":
            streak = await self._record_ignore(message, decision)
            override = await self._review_silence(message, decision, streak)
            if override is not None:
                # The streak review is the agent's own second look at its own
                # silence [D-05], and it has decided this silence is not
                # defensible. What breaks it is an answer to the message being
                # declined — a reply, delivered now — not one more silence with
                # a note attached, so it goes out under the message's id and the
                # wait that was about to be told "declined" is answered instead.
                await self.deps.comms_out.send(
                    person_id=message.person_id,
                    channel=message.channel,
                    text=override,
                    urgency=decision.urgency,
                    conversation_key=message.conversation_key,
                    in_reply_to=message.id,
                )
                await self._record_outbound_turn(message, override)
                await self.deps.audit.append(
                    actor="self", category="social",
                    summary=f"answered {message.person_id} after reviewing own silence",
                    details={"message_id": reply_to, "streak": streak.get("count", 0),
                             "reason": decision.reason, "reviewed_category": decision.ignore_reason},
                )
                await self.deps.social.mark_handled(message, decided)
                return None
            await self._record_silence_turn(message, decision)
            await self.deps.audit.append(
                actor="self", category="social",
                summary=f"ignored message from {message.person_id}",
                details={"reason_category": decision.ignore_reason, "reason": decision.reason,
                         "message_id": reply_to, "streak": streak.get("count", 0)},
            )
            await self.deps.social.mark_handled(message, decided)
            await self._announce_decline(message, decision)
            return None
        commission: dict[str, str] | None = None
        if decision.creates_intention and decision.intention_spec:
            spec = decision.intention_spec
            title = str(spec.get("title") or "commission").strip() or "commission"
            intention_id = await self.memory.create_intention(
                title=title,
                desired_end_state=spec.get("desired_end_state", ""),
                kind="commitment",
                origin="commission",
                commissioned_by=message.person_id,
                deadline=_deadline_from_spec(spec.get("deadline_iso")),
            )
            commission = {"intention_id": str(intention_id), "title": title}
        # What this turn has to say, as one message.
        #
        # A deliberation writes its reply as a list of short texts, and each entry
        # used to be handed to `comms_out.send` on its own. So a question the agent
        # had answered once went out as two or three deliveries, two or three rows
        # in the transcript and two or three bubbles on the surface — and nothing
        # downstream can fold them back together: a surface's wait matches the
        # *first* outbound message after the question, so every extra text arrived
        # as a message answering nothing. From the other end of the conversation
        # that reads as the agent sending the same reply twice.
        #
        # Joining keeps the whole reply and its one delivery: nothing is dropped
        # and nothing is decided twice — what the agent chose to say arrives once,
        # as one message, against the message it answers. An entry with no words in
        # it is not a message, so it is not sent as one.
        texts = [text.strip() for text in decision.reply_texts[:3] if text and text.strip()]
        # Nothing to say is the same decision either way round: whether the action
        # was `act_silently` or a reply that turned out to be blank, the turn ends
        # in a declared silence and nothing goes out.
        if not texts:
            await self._record_silence_turn(message, decision)
            if decision.decided:
                await self.deps.social.mark_handled(message, decided)
            else:
                # Nothing was decided, so nothing is finished with. Retiring the
                # row is what closed the question as though the agent had
                # answered it, and the agent answered nothing.
                await self._hold_undecided(message, decision)
            await self._announce_decline(message, decision)
            return commission
        outbound = "\n\n".join(texts)
        await self.deps.comms_out.send(
            person_id=message.person_id,
            channel=message.channel,
            text=outbound,
            urgency=decision.urgency,
            conversation_key=message.conversation_key,
            in_reply_to=message.id,
        )
        await self._record_outbound_turn(message, outbound)
        await self.deps.social.mark_handled(message, decided)
        return commission

    # ------------------------------------------------- conversation context

    async def _record_inbound_turn(self, message: Any) -> None:
        """What a person said, kept as something the agent can read back.

        Held with its own words rather than a summary of them: a message the
        agent has paraphrased is a message it has already partly forgotten, and
        the details that matter later — what exactly was asked, in what tense,
        after what — are the ones a summary drops.
        """
        who = message.person_id or "someone"
        await self._append_turn(
            TurnKind.inbound.value,
            f"{who} via {message.channel}: {' '.join((message.text or '').split())[:200]}",
            detail={
                "text": message.text or "",
                "person_id": message.person_id,
                "channel": message.channel,
                "message_id": str(message.id),
            },
            conversation_key=message.conversation_key,
        )

    async def _record_outbound_turn(self, message: Any, text: str) -> None:
        await self._append_turn(
            TurnKind.outbound.value,
            f"replied to {message.person_id or 'someone'}: {' '.join(text.split())[:200]}",
            detail={
                "text": text,
                "person_id": message.person_id,
                "channel": message.channel,
                "in_reply_to": str(message.id),
            },
            conversation_key=message.conversation_key,
        )

    async def _record_silence_turn(self, message: Any, decision: Any) -> None:
        """A silence is a choice with a reason, and it is a choice worth keeping.

        Silence is the one social action with no outbound trace, so nothing
        downstream can tell it from a message that never arrived. Held here, the
        agent can see that it already chose not to answer this person, which is
        the difference between a boundary and an oversight.
        """
        who = message.person_id or "someone"
        reason = decision.ignore_reason or decision.reason or "no reason given"
        if getattr(decision, "decided", True) is False:
            # "chose not to answer" is a claim about the agent, and this turn it
            # is false: no opinion was formed. The agent's own memory of why it
            # said nothing has to survive a model outage, and it is the first
            # thing it reads when it comes back to this conversation -- so it is
            # written as what happened, in the same place a real silence is.
            summary = f"could not answer {who} ({reason})"
        else:
            summary = f"chose not to answer {who} ({reason})"
        await self._append_turn(
            TurnKind.decision.value,
            summary,
            detail={
                "text": (message.text or "")[: self.config.memory.context.detail_chars],
                "person_id": message.person_id,
                "action": getattr(decision.action, "value", str(decision.action)),
                "ignore_reason": decision.ignore_reason,
                "reason": decision.reason,
            },
            conversation_key=message.conversation_key,
        )

    async def _hold_undecided(self, message: Any, decision: Any) -> None:
        """Keeps a message nobody managed to decide about, for a later attempt.

        No model answered, so the person asked a question that was never heard
        and the turn was closed as though it had been answered with a considered
        silence. It was not: the agent did not choose this and it is not a
        choice it can be asked to defend. So the message is counted, written down
        with its reason, and left on the queue -- where a model that has recovered
        will pick it up on a later cycle and the person gets their answer without
        having asked twice.

        Bounded, because the alternative is a loop: a model that can never answer
        this message would otherwise be asked again every cycle forever, and each
        ask costs money. After the last attempt the row is left exactly as it is,
        which is a permanent, readable record that the agent tried and could not.
        """
        await self.deps.social.record_failure(message, {
            "action": decision.action.value,
            "reason": decision.reason,
            "decided": False,
        })

    async def _append_turn(self, kind: str, summary: str, **kwargs: Any) -> None:
        try:
            await self.context.append(kind, summary, **kwargs)
        except Exception:
            logger.warning("life.context_append_failed")

    async def _record_ignore(self, message: Any, decision: Any) -> dict[str, Any]:
        """Counts the silence, and never lets counting it fail the decline.

        The choice to be quiet has already been made; a bookkeeping error here
        used to raise past the handling of the message, which left it pending
        forever with every surface reading one deliberate silence as a dead
        agent. A streak that cannot be kept is reported as one that asks for
        no review, and the decline proceeds.
        """
        try:
            return await self.deps.social.record_ignore(
                message.person_id, decision.ignore_reason,
            )
        except Exception:
            logger.exception("life.ignore_record_failed")
            return {"count": 0, "review": False}

    async def _review_silence(self, message: Any, decision: Any,
                             streak: dict[str, Any]) -> str | None:
        """The second look the agent takes at a silence that keeps repeating.

        A single ignore is nobody's business but the agent's own [D-05]. Three
        in a row to the same person is a pattern, and the pattern is what the
        review is shown: the reasons so far, and the message about to join
        them. The review may uphold the silence — a boundary genuinely being
        tested should be — or overrule it, and `None` here means either
        "upheld" or "not due", which the caller treats identically.

        Returns the text of an answer that must be delivered, or None.
        """
        if not streak or not streak.get("review"):
            return None
        reasons = [str(r) for r in streak.get("reasons", [])]
        try:
            review = await self.deps.social.streak_self_review(
                message.person_id, reasons, int(streak.get("count", 0)),
                latest_text=message.text or "",
            )
        except Exception:
            logger.exception("life.streak_review_failed")
            return None
        if not isinstance(review, dict) or not review.get("should_message"):
            return None
        return str(review.get("message") or "").strip() or None

    async def _announce_decline(self, message: Any, decision: Any) -> None:
        """Says that a question was heard and answered with nothing.

        The right to answer in silence is the agent's [D-05], and this is how a
        surface learns it was exercised. Without it the only thing a reader can
        observe is the absence of a reply, and absence has to be read as a broken
        link — so a considered `ignore` was reported as a stall, and the person
        waiting was told the agent had stopped when it had decided not to speak.
        The id of the message is what makes the two tellable apart, so the
        declaration names it and says nothing about any other turn.

        `decided` is in the payload for the same reason the id is. A surface
        cannot read the difference between the agent declining to answer and the
        agent never getting to answer — both are `act_silently` and both produce
        no outbound row — so it is told which one this was, and says the second
        one as a failure rather than reporting a decision the agent never made.
        """
        if self.bus is None:
            return
        try:
            await self.bus.publish(EventKind.MESSAGE_HANDLED, {
                "message_id": str(message.id),
                "person_id": message.person_id,
                "channel": message.channel,
                "action": decision.action.value,
                "reason": (decision.ignore_reason or decision.reason or "")[:200],
                "decided": getattr(decision, "decided", True),
            })
        except Exception:
            logger.exception("life.decline_publish_failed")

    async def _reverie(self) -> ReverieResult:
        """An idle mind choosing its own subject, and then going after it.

        Deliberating is only half of the mechanism. The other half is that a
        chosen subject has to become a focus, or it is a sentence in a log: the
        venture it was filed under would sit open and wait for an arbitration
        that idle drive pressures cannot win, so the agent would invent an
        interest every few minutes and never return to any of them.

        So a venture taken up here becomes the focus on the spot. The agent
        decided it was drawn to something and the next thing it does is that
        thing — which is the whole difference between an interest and a note
        about having had one.
        """
        window = self.config.rhythm.reverie.recent_window
        result = await self.reverie.muse(
            recent_thoughts=self._thoughts[-window:],
            energy=self.deps.rhythm.energy(self.spent_discretionary_session),
        )
        if result.thought:
            self._thoughts.append(result.thought)
            # Kept as an episode whether or not anything was taken on: "I
            # considered this and did not want it" is a real observation about
            # what the agent is drawn to, and at a low half-life it costs
            # nothing to keep a running record of.
            await self.memory.record_episode(
                kind="thought", summary=result.thought,
                importance=0.3 if result.committed_id else 0.2,
                half_life_h=12 if result.committed_id else 6,
            )
        if result.committed_id is None:
            return result

        await self._append_turn(
            TurnKind.thought.value, result.thought,
            focus=result.subject.subject,
        )
        if result.committed_kind != "venture":
            # A question has no steps to take; it raises the curiosity pressure
            # that brings the agent back to it on its own.
            return result
        previous = self.focus
        self.focus = Focus(
            title=result.subject.subject,
            kind="venture",
            why=result.subject.why or result.subject.first_step,
            intention_id=result.committed_id,
        )
        await self._roll_on_focus_change(previous, self.focus)
        return result

    async def _mind_wander(self) -> None:
        memories = await self.memory.random_memories(self.config.rhythm.mind_wandering.sample_memories)
        questions = await self.memory.open_questions(limit=3)
        fragments = [m.summary for m in memories[:self.config.rhythm.mind_wandering.sample_memories]]
        fragments += [f"open question: {q['text']}" for q in questions]
        glance = " ; ".join(fragments)[:500]
        thought = f"mind-wander: {glance}"
        self._thoughts.append(thought)
        await self._append_turn(TurnKind.thought.value, thought)
        await self.memory.record_episode(
            kind="thought", summary=thought, importance=0.3, half_life_h=12,
        )

    # ------------------------------------------------------------ record

    async def record(self, ws: Any, decision: Decision, outcome: Outcome) -> None:
        self.cycles += 1
        duration_ms = outcome.duration_ms
        digest = ws.digest() if ws is not None else ""
        try:
            if getattr(self.memory, "db", None) is not None:
                await self.memory.db.execute(
                    "INSERT INTO cycles (ts, thread_id, state, focus, tier, decision, cost_usd, "
                    "duration_ms, workspace_digest) VALUES (now(), $1, $2, $3::jsonb, $4, $5::jsonb, $6, $7, $8)",
                    self.deps.thread_id, self.states.state.value,
                    _focus_json(self.focus), decision.tier,
                    _decision_json(decision), outcome.cost_usd, duration_ms, digest,
                )
        except Exception:
            logger.exception("life.record_failed")
        await self.deps.continuity.save({
            "thread_id": self.deps.thread_id,
            "cycle": self.cycles,
            "state": self.states.state.value,
            "focus": _focus_json(self.focus),
            "energy": self.deps.rhythm.energy(self.spent_discretionary_session),
            "spent_session": self.spent_discretionary_session,
            # Continuity is state, not transcript [P3]: the thread as written,
            # plus the newest raw turns so a restart resumes mid-thought rather
            # than mid-sentence.
            "context": self.context.snapshot(),
            "updated_at": datetime.now(UTC).isoformat(),
        })
        metrics.observe_cycle(self.deps.thread_id, self.states.state.value, duration_ms / 1000.0)
        await self._publish_presence()
        await self._publish_budget()

    async def _publish_presence(self) -> None:
        if self.bus is None:
            return
        try:
            await self.bus.publish(EventKind.PRESENCE_UPDATE, {
                "state": self.states.state.value,
                "energy": round(self.deps.rhythm.energy(self.spent_discretionary_session), 3),
                "focus": _focus_json(self.focus),
                "cycles": self.cycles,
            })
        except Exception:
            logger.exception("life.presence_publish_failed")

    async def _publish_budget(self) -> None:
        if self.bus is None:
            return
        try:
            state = await self.deps.gateway.budget()
            await self.bus.publish(EventKind.BUDGET_UPDATE, state)
        except Exception:
            pass

    # -------------------------------------------------------- next focus

    def next_focus(self, decision: Decision, outcome: Outcome) -> Focus | None:
        if decision.kind == "advance_goal" and outcome.results:
            result = outcome.results[0]
            result_outcome = result.get("outcome")
            if result_outcome in (SolverOutcome.goal_done.value, SolverOutcome.parked.value):
                return None
            return self.focus
        if decision.kind == "none":
            return None
        if decision.kind == "social":
            # A social focus exists to answer one message, and it is spent the
            # moment that message is handled. Holding it afterwards is what
            # parked the agent: every later cycle took the "social focus
            # without a pending message; nothing to do" branch in `decide`,
            # which returns before the drives and before the reverie below it,
            # so an agent that had once answered a person could not think again
            # until something restarted it. 2235 cycles were spent that way.
            #
            # Work the message asked for is the exception, and it has to be named
            # here or nowhere. The social focus is spent, yes — but the agent has
            # just told the person it is doing the thing, and the next thing it
            # picked up came from the drives rather than from that promise. On a
            # live agent that is one turn's work among everything else, and in
            # practice the drives picked something else and the promise was never
            # kept. So the commission is carried straight into the next cycle's
            # focus, and the work the reply announced starts on the following tick.
            focus = _commission_focus(outcome.commission)
            return focus
        return self.focus

    # -------------------------------------------------- pace and upkeep

    async def _pace(self) -> None:
        """The loop's own housekeeping between cycles: upkeep, and a daily pass.

        This used to be `_maybe_sleep`, and the branch that mattered was the one
        that put the agent to bed for the night. It is gone, and both of the
        things that hung off it had to be carried over rather than dropped:

          * consolidation, which ran on the way into the sleep window and so
            would have run exactly never once there was no sleep window. It is a
            job the agent does *to itself*, and an agent that is awake at 04:00
            is an agent that can do it — so it is keyed to
            `memory.consolidation.run_at` in the owner's timezone instead.
          * rest, which was the other half of that method and is untouched:
            low energy still buys a micro-break, and a micro-break is still five
            minutes of settling rather than an absence.

        Note what is not here: any hour of the day at which the agent declines
        to think. The tick below is the same length at 04:00 as at 16:00.
        """
        await self._maybe_consolidate()
        energy = self.deps.rhythm.energy(self.spent_discretionary_session)
        if self.deps.rhythm.should_rest(energy, max_utility=0.5) and self.states.state != State.RESTING:
            if self.states.transition(State.RESTING, "low energy"):
                self.deps.rhythm.add_rest(self.config.rhythm.focus.micro_break_min)
                self.states.transition(State.THINKING_REFLECTING, "rested")

    async def _maybe_consolidate(self) -> None:
        """Nightly memory consolidation, on a clock rather than on going to sleep.

        Written down by date so a restart does not run it twice, and keyed to a
        time of day rather than to a window that has closed, so the days the
        agent was asleep — there are none now, and there used to be — cannot
        leave it stale.
        """
        if self.deps.consolidation is None:
            return
        if not self.deps.rhythm.due_daily(
            self.config.memory.consolidation.run_at, self._consolidation_done_tonight,
        ):
            return
        try:
            await self.deps.consolidation.run_nightly()
            self._consolidation_done_tonight = self.deps.rhythm.local_time().date().isoformat()
        except Exception:
            logger.exception("life.consolidation_failed")

    async def _parked(self) -> None:
        """Waits out a pause without going deaf.

        Blocking on the resume event alone is what made a paused agent
        indistinguishable from an absent one: for the whole pause it published
        nothing, recorded nothing, and re-read nothing — so every surface holding a
        question reported a deliberate pause as an agent that had stopped
        existing, and the kill switch went unread for exactly as long. The wait is
        bounded for both reasons. Each pass says the agent is still here and is
        waiting, which is a state a surface can name rather than guess at.
        """
        while self.control.paused and not self.control.stopped:
            await self._publish_presence()
            try:
                await asyncio.wait_for(self.control.wait_resume(), timeout=PARKED_HEARTBEAT_S)
            except TimeoutError:
                continue
            # The resume may have come from a file rather than from a surface, so
            # the flags are read again before the loop trusts them.
            await self.deps.control.refresh()

    # --------------------------------------------------------------- life

    async def life(self) -> None:
        await self.wake_up()
        while True:
            # The files before the flags: a stop written by the host has to be
            # what *this* pass reads, not what the next start reads. `load` does it
            # once, at startup, so a kill switch pressed after the process was
            # already running was only ever a note in a file nothing read again —
            # the one switch H1 calls always effective, effective only until
            # restart [H1].
            await self.deps.control.refresh()
            if self.control.stopped:
                break
            if self.control.paused:
                self.states.transition(State.PAUSED, "control pause")
                await self._parked()
                self.states.transition(State.RESUMING, "control resume")
                self.states.transition(State.THINKING_REFLECTING, "resumed")
                continue

            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A cycle that raises is a cycle that failed, not a reason to stop
                # existing. The life loop's first invariant is that the process
                # does not exit, and a single bad model call — a rate limit, an
                # expired key, a provider with nothing eligible for the tier —
                # used to end the agent outright. It then read as silence to
                # every surface waiting on it, with nothing in the world to say
                # the agent had stopped: it was simply gone.
                logger.exception("life.cycle_failed")
                await self._publish_presence()
                # The tick at the end of a good cycle is what keeps a failing
                # one from becoming a spin loop. A cycle that raises never
                # reaches it, so the pause is owed here instead.
                await self.deps.rhythm.wait_until_next(self.states.state)

        # The loop only leaves by control: an oversight stop, or a signal. This
        # block used to live at the tail of `_cycle`, where it ran after every
        # pass — so a running agent saved `state: "stopped"` into its continuity
        # snapshot and audited `life loop stopped` on every cycle, and a process
        # that died mid-flight was indistinguishable from one that had shut
        # down cleanly, because the last cycle had already said so. Saying it
        # only here makes the snapshot's claim survive long enough to mean it.
        await self.bookmark(self.focus)
        await self.deps.continuity.save({
            "thread_id": self.deps.thread_id,
            "state": "stopped",
            "focus": _focus_json(self.focus),
            # The thread has to be here too. This snapshot replaces the last
            # per-cycle one, so leaving the context out makes shutting down the
            # one thing that reliably erases it -- the agent's final act on its
            # way out would be to forget everything it had been doing.
            "context": self.context.snapshot(),
            "stopped_at": datetime.now(UTC).isoformat(),
        })
        await self.deps.audit.append(
            actor="self", category="lifecycle", summary="life loop stopped", details={},
        )

    async def _cycle(self) -> None:
        """One pass: pace, perceive, attend, decide, act, record.

        Split out so the loop can treat a raised cycle as a failed cycle. The
        body is the old loop body unchanged, apart from `_pace`, which used to be
        `_maybe_sleep` and whose return value used to cut the pass short: a night
        of that was a night of no cycles at all.
        """
        await self._pace()
        percepts = await self.perceive()
        recent_thought_vecs = []
        appraisal = await self.deps.attention.appraise(
            percepts, self.focus,
            recent_centroid=centroid_of(recent_thought_vecs) or None,
            thresholds_by_state=self.config.rhythm.attention.interrupt_thresholds,
            state_name=self.states.state.value,
        )
        previous_focus = self.focus
        if appraisal.interrupt:
            await self.bookmark(self.focus)
            self.focus = appraisal.new_focus
            await self._roll_on_focus_change(previous_focus, self.focus)
        elif self.focus is None:
            self.focus = await self._choose_focus(percepts)
            await self._roll_on_focus_change(previous_focus, self.focus)

        ws = await self._assemble_workspace(percepts)
        decision = await self.decide(ws)
        outcome = await self.act(decision)
        await self.record(ws, decision, outcome)
        settled = self.next_focus(decision, outcome)
        if settled is not self.focus:
            await self._roll_on_focus_change(self.focus, settled)
        self.focus = settled

        if self.focus is None and self.states.state != State.WAITING:
            self.states.transition(State.WAITING, "no focus")
        elif self.focus is not None and self.states.state == State.WAITING:
            self.states.transition(State.THINKING_REFLECTING, "focus acquired")

        await self.deps.rhythm.after_cycle(outcome.model_dump(mode="json"))
        await self.deps.rhythm.wait_until_next(self.states.state)

    async def run(self) -> None:
        await self.life()


def _focus_json(focus: Focus | None) -> str:
    import json

    if focus is None:
        return json.dumps(None)
    return json.dumps(focus.model_dump(mode="json"))


def _deadline_from_spec(value: Any) -> datetime | None:
    """The deadline a deliberation asked for, or none at all.

    The social schema offers `"deadline_iso": "..."|null`, and a model that wants
    no deadline often writes the *word* — `"null"` — instead of the JSON null.
    Read as a date that is a `ValueError`, and it was raised from the middle of
    carrying out a decision: the act died, the message stayed at the head of the
    pending queue, and the next cycle deliberated the same question again — so an
    optional field the model filled in wrongly could cost the person a second
    answer to a question already answered.

    A deadline is optional, so anything unreadable is the absence of one. The
    intention is still created; only its due date is left open.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"null", "none", "nil", "n/a", "-"}:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        logger.warning("life.deadline_unreadable", value=text[:60])
        return None


def _decision_json(decision: Decision) -> str:
    import json

    return json.dumps(decision.model_dump(mode="json"))


def _commission_focus(commission: dict[str, str] | None) -> Focus | None:
    """The focus for work this turn committed to, from what `_act_social` returned.

    `None` for a turn that committed to nothing, which is the ordinary case and
    keeps the social focus spent as it always was.

    An id that will not parse is reported rather than raised from here: this runs
    at the end of a cycle that has already answered a person, and a focus that
    cannot be built is no reason to make the reply cost a second one. Losing the
    commission here degrades to the behaviour that existed before — the work is
    open and the drives can still find it — which is not nothing.
    """
    if not commission:
        return None
    raw = str(commission.get("intention_id") or "").strip()
    if not raw:
        return None
    try:
        intention_id = UUID(raw)
    except (TypeError, ValueError):
        logger.warning("life.commission_focus_unreadable", intention_id=raw[:60])
        return None
    return Focus(
        intention_id=intention_id,
        title=str(commission.get("title") or "commission"),
        kind="commitment",
        why="work asked for in the message just answered",
    )
