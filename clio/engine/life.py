from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from clio.config import ClioConfig
from clio.engine.attention import Attention
from clio.engine.drives import IMPULSE_TARGETS, DriveInputs, Motivation
from clio.engine.rhythm import RhythmController, backoff_seconds
from clio.engine.states import State, StateMachine
from clio.engine.stops import StopKind, StopsLedger
from clio.engine.workspace import WorkspaceAssembler
from clio.gateway.service import format_wait as _format_retry_wait
from clio.gsl.loop import PLAN_ATTEMPTS, PLAN_DEGRADED, PLAN_RETRY_AFTER
from clio.observability import metrics
from clio.observability.logger import bind_context, get_logger
from clio.schemas.context import TurnKind
from clio.schemas.events import Event, EventKind, Percept
from clio.schemas.gsl import Decision, Focus, IntentionStatus, Outcome, SolverOutcome, StageNotice
from clio.schemas.messages import Urgency
from clio.schemas.models import ModelMessage, ModelRequest, Role, Tier
from clio.schemas.reverie import ReverieResult
from clio.social.conversation import open_conversations, render_conversations
from clio.social.deliberation import NO_CAP, effective_attempt_caps
from clio.social.verbosity import normalise_verbosity
from clio.social.verbosity import verbosity_interval_min as _verbosity_interval_min

logger = get_logger("clio.life")

# How long a parked agent waits between heartbeats. Well inside a surface's
# patience and inside the longest tick a working agent has, so "paused" is
# reported as paused rather than as silence.
PARKED_HEARTBEAT_S = 30.0

# How often the life loop looks for parked intentions old enough to reopen.
# A query per cycle would be a query per four seconds for a fact that changes
# on the scale of the park backoff; a sweep that is late costs a late reopen,
# which against half an hour of parking is nothing.
PARK_REOPEN_CHECK_S = 300.0

# How long `in_progress` work may sit with no focus and no update before the
# zombie sweep parks it. Two hours: long enough that a genuinely running
# task (which updates its intention on every step) never trips it, short
# enough that a stranded goal becomes a visible, noticed park the same
# evening instead of a silent loss.
ZOMBIE_STALE_S = 2 * 3600.0

# Words that sequence new work behind work already in hand ("once that is
# finished, review X"). A message carrying one marks the commission as
# following the suspended focus rather than preempting it.
_SEQUENCED_RE = re.compile(
    r"\b(?:once|after|when|following|next|then|upon)\b.{0,60}\b"
    r"(?:finished|done|complete|over|built|ready|working|running|in progress)\b"
    r"|\b(?:once|after)\s+(?:that|this|it|the\s+\w+)\b",
    re.IGNORECASE,
)

# How many thoughts the running buffer keeps.
#
# Everything that reads `_thoughts` reads a tail of it — the last 10 for the
# wake-up report, the last `reverie.recent_window` (8) for musing — and nothing
# counts it, sums it or indexes from the front. It was a plain list that only
# ever grew, so a long-lived process accumulated one string per decision for as
# long as it was left running, and the only thing that ever freed any of it was
# the process exiting. Those two readers want 8 and 10.
#
# 256 is a wide margin over both, sized so the buffer still holds a working day
# of the agent's own reasoning if something ever wants a longer look, while
# being a number of strings rather than an unbounded number of them.
THOUGHT_BUFFER = 256


def _discard(task: asyncio.Future[Any]) -> None:
    """Take an abandoned task's exception, so it is not reported as an orphan."""
    if not task.cancelled():
        task.exception()


def _stage_as_dict(notice: StageNotice) -> dict[str, Any]:
    """A stage notice as the plain dict `Decision.stage` carries.

    Not `as_dict()`, which is the one-line rendering for a prompt and drops the
    address. The decision has to cross into `act` and come back out to the store
    and to the outbound pipeline, so it needs `notice_id` to settle the row,
    `person_id` and `channel` to deliver on, and `attempts` to work out the wait —
    the delivery address is the point, not a detail of the rendering.
    """
    return {
        "notice_id": str(notice.id),
        "intention_id": str(notice.intention_id),
        "stage": notice.stage,
        "title": notice.title,
        "detail": notice.detail,
        "person_id": notice.person_id,
        "channel": notice.channel,
        "conversation_key": notice.conversation_key,
        "attempts": notice.attempts,
    }


def _tail(buffer: deque[str], n: int) -> list[str]:
    """The last `n` entries of a bounded ring, as a plain list.

    `deque` has no slice syntax — `d[-10:]` is a `TypeError`, not an empty
    result — so both of the reads that were list slices go through here. The copy
    is bounded by `len(buffer)`, which is what the `maxlen` is for; nothing about
    it grows with how long the agent has been running.
    """
    if n <= 0:
        return []
    items = list(buffer)
    return items[-n:] if n < len(items) else items


class LifeDeps:
    """Everything the Self needs. Assembled by clio.core.runner."""

    def __init__(
        self,
        config: ClioConfig,
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
        leases: Any = None,
        steering: Any = None,
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
        # The workspace lease manager, when the deployment has one: project
        # containers take their isolation from it and their evergreen
        # milestones commit through it.
        self.leases = leases
        # Live mid-turn steering (clio/gsl/steering.py), when the runtime built
        # one: messages bound to the active project's conversation, arriving
        # while that project's run is under way, are drained into it and
        # claimed at the next step boundary rather than queued for a turn.
        self.steering = steering


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
            from clio.memory.context import ContextLayer

            self.context = ContextLayer(
                deps.config, memory=deps.memory, gateway=deps.gateway,
                thread_id=deps.thread_id,
            )
        self.focus: Focus | None = None
        # Work suspended for an interruption, as a stack rather than one slot.
        # One slot meant a second interruption — or a new commission taking the
        # pass — silently discarded the first suspended goal: it stayed
        # `in_progress` in the store with no focus, no attempt closure and
        # nothing that would ever pick it back up (observed 2026-11-08: the
        # CodeForge build was dropped this way when the Helium review was
        # commissioned mid-attempt). A stack keeps every suspended goal owed
        # the pass back, in LIFO order, until it settles.
        self._suspended_stack: list[Focus] = []
        self.spent_discretionary_session = 0.0
        self.cycles = 0
        self._percept_queue: Any = None
        # Percepts taken off the queue by the inter-cycle wait to end its sleep
        # early. They are held here rather than kept in the queue because the
        # wait has to *read* one to know a message arrived, and a percept read
        # and then dropped is a message nobody ever sees. The next `perceive`
        # looks at these before it looks at the queue.
        self._woken: list[Any] = []
        # A bounded ring, not a list. `deque(maxlen=...)` drops the oldest entry
        # on append, so the buffer cannot outgrow its own window. The two readers
        # that used to be list slices go through `_tail` instead, because `deque`
        # has no slice syntax at all.
        self._thoughts: deque[str] = deque(maxlen=THOUGHT_BUFFER)
        # NA-P3: every ending of work is recorded here with its kind -- success,
        # stop, limit, error -- and the recent ones are rendered into the
        # workspace so the agent reports them in its own words. Persisted through
        # the continuity snapshot, because an ending a restart erases is an
        # ending nobody can be told about.
        self.stops = StopsLedger()
        self._consolidation_done_tonight: str | None = None
        # Consecutive passes an unanswered message has taken from a goal the
        # agent was holding. Counted rather than inferred, because "the
        # conversation took priority" is true on every one of those passes and
        # true forever; only the count says whether the goal is being starved.
        self._inbound_deferrals: int = 0
        self._last_park_sweep: float = 0.0
        self._last_zombie_sweep: float = 0.0
        # Clio Agent 3.3 project leadership: which project currently holds the
        # execution focus, and when that quantum began. Both are state, not
        # cache — the anti-thrashing guard and the switch protocol read them,
        # and a restart that lost them would re-open the lock and thrash.
        self.active_project_id: str | None = None
        self.project_focus_started_at: float | None = None
        # The governance notices `decide` chose to dispatch this pass; `act`
        # settles exactly these, so the decision and the delivery can never be
        # about two different sets of news.
        self._governance_batch: list[Any] = []

    @property
    def _suspended_focus(self) -> Focus | None:
        """The most recently suspended goal, or None.

        Kept as a property over `_suspended_stack` so the one-slot reads and
        writes that predate the stack keep working: reading gives the top of
        the stack, assigning replaces the whole stack (None empties it).
        """
        return self._suspended_stack[-1] if self._suspended_stack else None

    @_suspended_focus.setter
    def _suspended_focus(self, value: Focus | None) -> None:
        self._suspended_stack = [value] if value is not None else []

    def _push_suspended(self, focus: Focus | None) -> None:
        """Suspend a goal for an interruption, without discarding earlier ones."""
        if focus is None or focus.intention_id is None:
            return
        if any(
            item.intention_id == focus.intention_id for item in self._suspended_stack
        ):
            return
        self._suspended_stack.append(focus)

    def _pop_suspended(self) -> Focus | None:
        """The most recently suspended goal, spent exactly once."""
        return self._suspended_stack.pop() if self._suspended_stack else None

    # ------------------------------------------------------------- wake

    async def wake_up(self) -> None:

        report = await self.deps.continuity.wake_up()
        snapshot = report.get("snapshot") or {}
        await self._restore_focus_from_report(report)
        self.stops = StopsLedger.restore(snapshot.get("stops"))
        # Pick the thread back up where it was left, so waking is not the same
        # as starting over. The narrative survives in the snapshot; the raw turns
        # are the newest few, which is what resuming mid-thought needs.
        self.context.restore(snapshot.get("context"))
        project_state = snapshot.get("projects") or {}
        if isinstance(project_state, dict):
            self.active_project_id = project_state.get("active_project_id") or None
            started = project_state.get("project_focus_started_at")
            try:
                candidate = float(started) if started is not None else None
            except (TypeError, ValueError):
                candidate = None
            # `time.monotonic()` is process- and boot-relative, so a value
            # written before a restart is only meaningful inside a sane window:
            # a stamp from the future (a different boot) or one older than a
            # day is discarded, which re-opens the quantum rather than pinning
            # it open or shut for reasons nobody can see.
            if candidate is not None and 0.0 <= time.monotonic() - candidate <= 86400.0:
                self.project_focus_started_at = candidate
            else:
                self.project_focus_started_at = None
        self.deps.rhythm.mark_awake()
        self.states.transition(State.THINKING_REFLECTING, "wake")
        await self.deps.audit.append(
            actor="self", category="lifecycle", summary="woke up",
            details={"report": {k: v for k, v in report.items() if k != "snapshot"}},
        )
        bind_context(thread_id=self.deps.thread_id)
        await self._publish_presence()

    async def _restore_focus_from_report(self, report: dict[str, Any]) -> None:
        """Put the interrupted focus back, and say that it was picked up.

        Split out of `wake_up` so the resumption is testable on its own: the
        focus is revalidated against the store, the goal it serves is carried
        into the agent's own thread, and a focus whose work has vanished is
        recorded as a fact rather than dropped in silence (NA-P2.1, NA-P2.3).
        """
        import json as _json

        snapshot = report.get("snapshot") or {}
        focus_state = snapshot.get("focus")
        if isinstance(focus_state, str):
            try:
                focus_state = _json.loads(focus_state)
            except ValueError:
                focus_state = None
        if not focus_state:
            return
        try:
            restored = Focus.model_validate(focus_state)
            if restored.intention_id is None:
                return
            intention = await self.memory.get_intention(restored.intention_id)
            if intention is not None and intention.is_open():
                self.focus = restored
                logger.info("life.focus_restored", intention_id=str(restored.intention_id))
                # NA-P2.3: the resumption is said in the agent's own
                # thread, with the original goal carried beside it, so
                # an interruption is picked back up as a fact the agent
                # can see and report -- not as a focus that quietly
                # reappeared.
                await self._append_turn(
                    TurnKind.event.value,
                    f"resumed '{intention.title}' from before the "
                    "interruption (goal unchanged)",
                    detail={
                        "intention_id": str(intention.id),
                        "goal": {
                            "title": intention.title,
                            "desired_end_state": intention.desired_end_state,
                            "status": intention.status,
                        },
                    },
                    focus=intention.title,
                )
            elif intention is None:
                # The focus pointed at work that no longer exists. v3.0
                # dropped it silently, which is the drop NA-P2.1 names:
                # a task vanishing is a fact somebody may ask about, so
                # it is recorded where the agent can read it back.
                await self._append_turn(
                    TurnKind.event.value,
                    f"could not resume '{restored.title}': the work it "
                    "pointed at is gone",
                    focus=restored.title,
                )
        except Exception:
            logger.exception("life.focus_restore_failed")

    # -------------------------------------------------------- perceiving

    def attach_queue(self, queue: Any) -> None:
        self._percept_queue = queue

    async def perceive(self) -> list[Percept]:
        percepts: list[Percept] = []
        # A percept the inter-cycle wait pulled off the queue to end its sleep
        # early is looked at first. Reading one is how the wait learns a message
        # arrived, so it cannot put it back without racing the next writer;
        # holding it here and draining it first is what keeps the early wake
        # from costing the message that caused it.
        held, self._woken = self._woken, []
        for item in held:
            percept = self._percept_of(item)
            if percept is not None:
                percepts.append(percept)
        queue = self._percept_queue
        if queue is None:
            return percepts
        while not queue.empty():
            try:
                item = queue.get_nowait()
            except Exception:
                break
            percept = self._percept_of(item)
            if percept is not None:
                percepts.append(percept)
        return percepts

    def _percept_of(self, item: Any) -> Percept | None:
        """One queued item as a percept, or `None` for anything else.

        The queue carries the same two shapes it always did — a `Percept`, or an
        `Event` to convert — and the held list carries the very same objects, so
        both readers go through one conversion rather than two that could drift.
        """
        if isinstance(item, Percept):
            return item
        if isinstance(item, Event):
            return self._percept_from_event(item)
        return None

    async def _wait_until_next(self) -> None:
        """Wait out the tick, or end it the moment a percept arrives.

        The loop used to sleep the whole tick before looking at its queue, so a
        message sent a moment after a pass was answered on the tick's clock
        rather than the sender's — up to the longest tick (eight seconds while
        waiting, sixty while resting) of somebody on the other end watching for
        a sign the agent heard them. The tick is still the pace of an idle
        agent; it is no longer the pace of a channel.

        The wait is the tick raced against the next queue item. A tick that
        elapses first is the ordinary idle pass; an item that lands first ends
        the sleep so the next pass perceives it, and the item is held for that
        pass rather than dropped. Nothing is lost either way: the item read here
        is the one `perceive` drains first, and it is taken off the queue by a
        callback rather than after the race, so a stop landing at the same
        instant as the message cannot lose it.
        """
        ticking = asyncio.ensure_future(self.deps.rhythm.wait_until_next(self.states.state))
        queue = self._percept_queue
        if queue is None:
            await ticking
            return
        arriving = asyncio.ensure_future(queue.get())
        # The getter owns its item the moment it completes, not the moment the
        # race below returns: a done-callback fires on completion whether or not
        # the wait is still there to collect, which is what keeps an item from
        # being read and then dropped when a stop cancels the wait.
        arriving.add_done_callback(self._hold_arrival)
        # A cancelled child is not owed to anybody, but a raised one is, and a
        # future nobody retrieves is logged as an orphan. `ticking` is the one
        # abandoned without a reader when the getter wins.
        ticking.add_done_callback(_discard)
        try:
            await asyncio.wait({ticking, arriving}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Both are cancelled unconditionally and not awaited: the winner is
            # already done, and the loser is abandoned to the loop rather than
            # held open by a wait here — awaiting a cancelled getter is how a
            # cancellation meant for this pass would be swallowed instead.
            ticking.cancel()
            arriving.cancel()

    def _hold_arrival(self, task: asyncio.Future[Any]) -> None:
        """Keep the percept a queue read took, so ending the sleep cannot lose it."""
        if not task.cancelled() and task.exception() is None:
            self._woken.append(task.result())

    def _percept_from_event(self, event: Event) -> Percept:
        from clio.schemas.events import percept_from_event

        return percept_from_event(event)

    # ---------------------------------------------------------- bookmark

    async def bookmark(self, focus: Focus | None) -> None:
        """Write down where a piece of work stands, with the goal it serves.

        NA-P2.3: an interruption must not cost the task its aim. The bookmark
        carries the next step and the mental context as before, and now the
        goal itself -- title, desired end state, status -- read from the
        intention the focus points at. A resume that restores this bookmark
        restores the original goal with it, so an interrupted task continues
        toward what it was asked to do rather than toward whatever the last
        step said.
        """
        if focus is None or focus.intention_id is None:
            return
        from clio.schemas.gsl import Bookmark

        goal: dict[str, Any] = {"title": focus.title, "desired_end_state": focus.why}
        try:
            intention = await self.memory.get_intention(focus.intention_id)
            if intention is not None:
                goal = {
                    "title": intention.title,
                    "desired_end_state": intention.desired_end_state,
                    "status": intention.status,
                }
        except Exception:
            # A goal that cannot be read right now is still worth carrying in
            # the focus's own terms: the bookmark degrades rather than the
            # resumption losing the aim entirely.
            logger.warning("life.bookmark_goal_read_failed")
        await self.memory.set_bookmark(
            focus.intention_id,
            Bookmark(
                next_planned_step=focus.bookmark.get("next_planned_step"),
                mental_context=focus.bookmark.get("mental_context") or focus.why,
                goal=goal,
            ),
        )

    # ----------------------------------------------------- drive context

    async def _drive_inputs(self, percepts: list[Percept]) -> DriveInputs:
        # Deliberately not `list_intentions`. That asks for the top N by priority,
        # which is the wrong question here: a priority backlog can rank a real
        # commissioned deadline below the cut and hide it completely, and the
        # drives then weigh only the work they can see. See
        # `MemoryStore.list_attentionable_intentions`.
        intentions = list(await self.memory.list_attentionable_intentions(limit=50))
        # Evergreen projects whose cooldown has elapsed present themselves to
        # the drives as work: an `evolve_project` impulse resolves to the
        # project's root, and advancing that root proposes the next cycle.
        try:
            proposals = await self._evolution_candidates()
        except Exception:
            proposals = []
        intentions.extend(proposals)
        questions = await self.memory.open_questions(limit=50)
        attempts_failed = 0
        if self.focus is not None and self.focus.intention_id is not None:
            attempts = await self.memory.list_attempts(self.focus.intention_id, limit=5)
            attempts_failed = sum(1 for a in attempts if a.outcome == "failed")
        last_contact = await self.memory.db.fetchrow(
            "SELECT MAX(ts) AS last FROM messages "
            "WHERE direction IN ('inbound', 'outbound')"
        ) if getattr(self.memory, "db", None) is not None else None
        last_contact_age_h = None
        if last_contact is not None and last_contact["last"] is not None:
            last_contact_age_h = (datetime.now(UTC) - last_contact["last"]).total_seconds() / 3600.0
        try:
            telemetry = await self.memory.dag_telemetry()
        except Exception:
            telemetry = {}
        # Repeated leaf verification failures raise mastery; anomalous leaf
        # outcomes open questions that raise curiosity.
        leaf_failures = 0
        try:
            episodes = await self.memory.db.fetch(
                "SELECT COUNT(*) AS n FROM episodes WHERE kind = 'verification_failed' "
                "AND ts >= now() - interval '2 days'",
            ) if getattr(self.memory, "db", None) is not None else []
            if episodes:
                leaf_failures = int(episodes[0]["n"] or 0)
        except Exception:
            leaf_failures = attempts_failed
        return DriveInputs.from_intentions(
            intentions,
            open_questions=len(questions),
            failed_recent=attempts_failed,
            active_ventures=sum(1 for i in intentions if i.kind == "venture"),
            evolving_projects=len(proposals),
            unread_events=len(percepts),
            last_contact_age_h=last_contact_age_h,
            energy=self.deps.rhythm.energy(self.spent_discretionary_session),
            dag_blocked_edges=int(telemetry.get("blocked_edges") or 0),
            dag_ready_count=int(telemetry.get("ready_count") or 0),
            dag_budget_burn_usd=float(telemetry.get("budget_burn_usd") or 0.0),
            leaf_verification_failures=leaf_failures,
            anomalous_outcomes=leaf_failures,
        )

    # ------------------------------------------- project focus (3.3)
    #
    # One project holds the execution focus at a time, and holds it for a
    # minimum quantum. The lock is the anti-thrashing core: a second
    # conversation assigning a new project queues that project (with its own
    # plan and its own kickoff report) instead of silently yanking the agent
    # off the first one, and a switch that does happen checkpoints the old
    # project and says so.

    async def _project_lock_active(self, project: dict[str, Any] | None = None) -> bool:
        """Whether the active project is inside its minimum focus quantum."""
        if self.active_project_id is None or self.project_focus_started_at is None:
            return False
        project = project or await self._get_project(self.active_project_id)
        if project is None or str(project.get("status") or "") != "active":
            return False
        elapsed = time.monotonic() - float(self.project_focus_started_at)
        return elapsed < float(self.config.projects.project_min_focus_s)

    async def _get_project(self, project_id: str | None) -> dict[str, Any] | None:
        if not project_id or not hasattr(self.memory, "get_project"):
            return None
        try:
            return await self.memory.get_project(project_id)
        except Exception:
            return None

    async def _intention_project_id(self, intention_id: Any) -> str | None:
        if intention_id is None:
            return None
        try:
            intention = await self.memory.get_intention(intention_id)
        except Exception:
            return None
        if intention is None:
            return None
        return str(intention.project_id) if intention.project_id else None

    async def _choose_focus(self, percepts: list[Percept]) -> Focus | None:
        inputs = await self._drive_inputs(percepts)

        async def _create(**kwargs: Any) -> Any:
            return await self.memory.create_intention(**kwargs)

        focus, _pressures = await self.deps.motivation.choose_focus(
            inputs, self.deps.rhythm.energy(self.spent_discretionary_session),
            create_intention_fn=_create,
        )
        if focus is None or focus.intention_id is None:
            return focus
        # The preemption guard: inside the active project's quantum, an impulse
        # (or a drive-proposed commitment) belonging to a *different* project
        # does not take the pass. The agent keeps working the project that
        # holds focus — its own next ready node — and the challenger waits for
        # the rotation to hand it a turn.
        if await self._project_lock_active():
            candidate_project = await self._intention_project_id(focus.intention_id)
            override = bool((focus.bookmark or {}).get("priority_override", False))
            if candidate_project and candidate_project != self.active_project_id and not override:
                logger.info(
                    "life.project_quantum_held",
                    active_project=self.active_project_id,
                    candidate_project=candidate_project,
                    candidate_title=focus.title[:80],
                )
                return await self._focus_for_active_project()
        return focus

    async def _focus_for_active_project(self) -> Focus | None:
        """The active project's own next ready node, as the held focus."""
        try:
            ready = await self.memory.get_ready_intentions(limit=25)
        except Exception:
            return None
        for node in ready:
            if node.project_id and str(node.project_id) == str(self.active_project_id):
                return Focus(
                    intention_id=node.id,
                    title=node.title or "project work",
                    kind=node.kind or "task",
                    why="the project holding focus has ready work",
                )
        return None

    async def _switch_project_focus(
        self, old_project_id: str | None, new_project_id: str, reason: str,
    ) -> None:
        """The mandatory protocol for every change of project focus.

        Nothing assigns `self.active_project_id` directly: every change goes
        through here, because a switch without a checkpoint is a loss — the
        old project's standing (what was done, which node was live, which
        files moved) exists only in this moment and nowhere durable. Three
        things happen to the old project — a structured checkpoint into
        `projects.checkpoint_json`, a status of `preempted` (unless a human
        already paused it), and a governance notice to its owner saying what
        was completed, why the switch happened, and what resumes it — and two
        to the new one: `active` and a fresh quantum clock.
        """
        now = datetime.now(UTC)
        ledger = None
        if old_project_id and old_project_id != new_project_id:
            old_project = await self._get_project(old_project_id)
            if old_project is not None and str(old_project.get("status")) not in (
                "done", "evolving_cooldown",
            ):
                try:
                    ledgers = await self.memory.get_project_progress_ledger(
                        project_id=old_project_id,
                        current_focus_project_id=old_project_id,
                    )
                    ledger = ledgers[0] if ledgers else None
                except Exception:
                    ledger = None
                current_intention_id = None
                if self.focus is not None and self.focus.intention_id is not None:
                    current_intention_id = str(self.focus.intention_id)
                next_ready_title = ""
                try:
                    ready = await self.memory.get_ready_intentions(limit=25)
                    for node in ready:
                        if node.project_id and str(node.project_id) == old_project_id:
                            next_ready_title = node.title or ""
                            break
                except Exception:
                    next_ready_title = ""
                checkpoint = {
                    "switched_at": now.isoformat(),
                    "reason": reason[:300],
                    "completed_nodes": ledger.nodes_done if ledger else 0,
                    "total_nodes": ledger.nodes_total if ledger else 0,
                    "active_node_id": current_intention_id,
                    "modified_files": (
                        ledger.artifacts_touched[-10:] if ledger else []
                    ),
                    "recent_actions": (
                        ledger.recent_actions if ledger else []
                    ),
                    "next_planned_step": next_ready_title,
                }
                elapsed_s = 0.0
                if self.project_focus_started_at is not None:
                    elapsed_s = max(0.0, time.monotonic() - float(self.project_focus_started_at))
                accumulated = float(old_project.get("focus_seconds_accumulated") or 0.0)
                new_status = (
                    "paused_for_human"
                    if str(old_project.get("status")) == "paused_for_human"
                    else "preempted"
                )
                await self.memory.update_project(
                    old_project_id,
                    status=new_status,
                    checkpoint_json=checkpoint,
                    focus_seconds_accumulated=accumulated + elapsed_s,
                )
                checkpoint_summary = self.memory.format_checkpoint_summary(checkpoint)
                await self._emit_governance_notice(
                    "focus_switched",
                    project_id=old_project_id,
                    payload={
                        "project_title": old_project.get("title") or old_project_id,
                        "old_title": old_project.get("title") or old_project_id,
                        "new_title": (await self._get_project(new_project_id) or {}).get("title")
                        or new_project_id,
                        "reason": reason,
                        "checkpoint_summary": checkpoint_summary,
                        "person_id": old_project.get("owner_principal_id"),
                        "resume_condition": (
                            f"Project rotates back to “{old_project.get('title')}” when its "
                            "score wins the fair rotation, or when you ask for it by name."
                        ),
                    },
                    dedupe_key=f"focus_switched:{old_project_id}:{new_project_id}:{int(now.timestamp()) // 900}",
                )
                logger.info(
                    "life.project_checkpointed", project=old_project_id,
                    completed=checkpoint["completed_nodes"],
                    total=checkpoint["total_nodes"],
                )
        new_project = await self._get_project(new_project_id)
        if new_project is not None:
            await self.memory.update_project(
                new_project_id, status="active", last_active_at=now,
            )
        self.active_project_id = new_project_id
        self.project_focus_started_at = time.monotonic()

    async def _maybe_rotate_project_focus(self) -> None:
        """The fair multi-project rotation, run when a quantum or session ends.

        Score = (expected_value / max(estimated_cost_usd, 0.01))
                * max(1.0, idle_seconds / 600.0)

        Value per dollar times an idle multiplier: a cheap project that has
        been waiting all afternoon earns its turn against an expensive one
        that was worked a minute ago. The winner takes the focus through the
        switch protocol; the loser keeps its checkpoint.
        """
        if not self.active_project_id:
            return
        quantum_expired = (
            self.project_focus_started_at is not None
            and (time.monotonic() - float(self.project_focus_started_at))
            >= float(self.config.projects.project_min_focus_s)
        )
        session_expired = self.deps.rhythm.focus_session_expired()
        if not (quantum_expired or session_expired):
            return
        try:
            candidates = await self.memory.list_projects(
                statuses=["active", "preempted"],
            )
        except Exception:
            logger.warning("life.project_rotation_read_failed")
            return
        if not candidates:
            return
        scored: list[tuple[float, dict[str, Any]]] = []
        now = datetime.now(UTC)
        for project in candidates:
            stats = await self._project_value_estimate(str(project.get("project_id")))
            expected_value = stats.get("expected_value", 0.5)
            cost = max(float(stats.get("estimated_cost_usd", 0.05)), 0.01)
            last_active = project.get("last_active_at")
            idle_s = 0.0
            if last_active is not None:
                try:
                    idle_s = max(0.0, (now - last_active).total_seconds())
                except (TypeError, ValueError):
                    idle_s = 0.0
            score = (expected_value / cost) * max(1.0, idle_s / 600.0)
            scored.append((score, project))
        if not scored:
            return
        best_score, best = max(scored, key=lambda pair: pair[0])
        best_id = str(best.get("project_id"))
        if best_id == str(self.active_project_id):
            # The current focus won again: restart its quantum so the next
            # check is a full interval away, not one every cycle.
            self.project_focus_started_at = time.monotonic()
            return
        await self._switch_project_focus(
            self.active_project_id, best_id,
            reason=(
                f"fair rotation: “{best.get('title')}” scored highest "
                f"({best_score:.2f}) after the focus quantum expired"
            ),
        )
        # Follow the switch with the new project's own work, so the rotation
        # is visible in what the agent is doing on the very next pass.
        follow = await self._focus_for_active_project()
        if follow is not None:
            self.focus = follow

    async def _project_value_estimate(self, project_id: str) -> dict[str, float]:
        """A project's expected value and estimated remaining cost, from rows."""
        try:
            row = await self.memory.db.fetchrow(
                "SELECT COALESCE(SUM(COALESCE(expected_value, 0.5)), 0) AS ev, "
                "COALESCE(SUM(GREATEST(COALESCE(budget_allocated_usd, budget_usd, 0) "
                "- COALESCE(budget_spent_usd, 0), 0.05)), 0.05) AS cost "
                "FROM intentions WHERE project_id = $1::uuid "
                "AND status NOT IN ('done','cancelled')",
                project_id,
            )
            if row is None:
                return {"expected_value": 0.5, "estimated_cost_usd": 0.05}
            return {
                "expected_value": float(row["ev"] or 0.5),
                "estimated_cost_usd": max(float(row["cost"] or 0.05), 0.01),
            }
        except Exception:
            return {"expected_value": 0.5, "estimated_cost_usd": 0.05}

    async def _emit_governance_notice(
        self,
        event_type: str,
        project_id: str | None = None,
        payload: dict[str, Any] | None = None,
        dedupe_key: str = "",
    ) -> Any:
        """One proactive project report, into the shared outbox.

        Shared with the solver loop on purpose: `_switch_project_focus` emits
        `focus_switched` where the switch happens, and `decide` drains the same
        queue the solver writes its kickoffs, human asks, blockers and
        milestones into — one queue, one dedupe table, one delivery path.
        """
        payload = dict(payload or {})
        outbox = getattr(self.deps.gsl, "governance", None)
        if outbox is not None and hasattr(outbox, "emit"):
            return outbox.emit(
                event_type, payload,
                dedupe_key=dedupe_key or f"{event_type}:{project_id}",
                person_id=payload.get("person_id"), project_id=project_id,
            )
        return None

    async def _due_governance_notices(self, limit: int = 2) -> list[Any]:
        """The pending notices this pass may deliver, oldest first.

        Only notices with an address are returned: a governance report with
        nobody to deliver to would spend a pass discovering that. The rows
        stay in the outbox until `act` settles them, so a cycle that fails to
        compose them does not lose them.
        """
        outbox = getattr(self.deps.gsl, "governance", None)
        # A deployment (or a test double) whose solver loop has no outbox has
        # nothing to dispatch; the attribute is duck-checked rather than
        # assumed, because a missing governance layer must not cost a cycle.
        if outbox is None or not hasattr(outbox, "peek"):
            return []
        due: list[Any] = []
        for notice in outbox.peek(limit=limit + 5):
            person_id = notice.person_id or (notice.payload or {}).get("person_id")
            if not person_id:
                continue
            due.append(notice)
            if len(due) >= limit:
                break
        return due

    async def _deliver_governance_notice(self, notice: Any) -> bool:
        """Deliver one governance notice, model-composed when possible.

        The composition is a stage-notice write (the model choosing the
        words); a model that cannot answer falls back to the notice's own
        rendered facts, because a blocker or an ask that only exists as a
        queued object is the silent-lead failure this whole path exists to
        remove. Delivery goes through the outbound pipeline's governance
        category: immediate, deduped, and budgeted separately from casual
        conversation.
        """
        person_id = notice.person_id or (notice.payload or {}).get("person_id")
        if not person_id or self.deps.comms_out is None:
            return False
        text = None
        composer = getattr(self.deps.social, "compose_stage_notice", None)
        if composer is not None:
            try:
                text = await composer(SimpleNamespace(
                    stage=notice.event_type,
                    title=(notice.payload or {}).get("project_title")
                    or (notice.payload or {}).get("title") or "",
                    detail=notice.render_text(),
                    person_id=person_id,
                ), preview=self._preview())
            except Exception:
                logger.warning("life.governance_compose_failed", event_type=notice.event_type)
                text = None
        text = text or notice.render_text()
        if not text:
            return False
        channel = await self._channel_for(person_id)
        try:
            result = await self.deps.comms_out.send(
                person_id=person_id,
                channel=channel,
                text=text,
                urgency=(
                    "high" if notice.event_type == "critical_risk_or_blocker" else "normal"
                ),
                conversation_key=None,
                in_reply_to=None,
                intention_id=(notice.payload or {}).get("intention_id"),
                category="governance",
                event_type=notice.event_type,
                dedupe_key=notice.dedupe_key,
            )
        except TypeError:
            # A deployment whose pipeline predates the governance category
            # still gets the message, without the fast-path knobs.
            try:
                result = await self.deps.comms_out.send(
                    person_id=person_id, channel=channel, text=text,
                    urgency="normal", conversation_key=None, in_reply_to=None,
                )
            except Exception as exc:
                logger.warning("life.governance_send_failed", error=str(exc)[:200])
                return False
        except Exception as exc:
            logger.warning("life.governance_send_failed", error=str(exc)[:200])
            return False
        status = str((result or {}).get("status") or "")
        delivered = status in ("sent", "queued")
        if delivered:
            await self._record_outbound_notice_turn(
                {
                    "title": (notice.payload or {}).get("project_title")
                    or (notice.payload or {}).get("title") or "",
                    "person_id": person_id,
                    "stage": notice.event_type,
                    "channel": channel,
                    "intention_id": (notice.payload or {}).get("intention_id"),
                    "conversation_key": None,
                },
                text,
            )
        logger.info(
            "life.governance_dispatched", event_type=notice.event_type,
            person=person_id, status=status or "unknown",
        )
        return delivered

    async def _channel_for(self, person_id: str) -> str:
        """Where to reach a person, from their newest inbound row."""
        db = getattr(self.memory, "db", None)
        if db is not None:
            try:
                row = await db.fetchrow(
                    "SELECT channel FROM messages WHERE direction = 'inbound' "
                    "AND person_id = $1 ORDER BY ts DESC LIMIT 1",
                    person_id,
                )
                if row is not None and row["channel"]:
                    return str(row["channel"])
            except Exception:
                pass
        return "web"

    async def _deliberation_project_context(
        self, message: Any,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """The bound project and the full portfolio, for the reply prompt.

        The bound project is what this conversation assigned; the portfolio is
        every project's ledger. Both go into the deliberation so a question
        about progress is answered from the project's own numbers — never
        substituted with whichever project happens to hold the focus.
        """
        bound: dict[str, Any] | None = None
        ledgers: list[dict[str, Any]] = []
        conversation_key = str(getattr(message, "conversation_key", "") or "")
        if conversation_key and hasattr(self.memory, "get_conversation_project"):
            try:
                bound = await self.memory.get_conversation_project(conversation_key)
            except Exception:
                bound = None
        if hasattr(self.memory, "get_project_progress_ledger"):
            try:
                rows = await self.memory.get_project_progress_ledger(
                    current_focus_project_id=self.active_project_id,
                )
                ledgers = [ledger.as_dict() for ledger in rows]
            except Exception:
                ledgers = []
        return bound, ledgers

    async def _evolution_candidates(self) -> list[dict[str, Any]]:
        """Evergreen projects whose cooldown has elapsed, as drive-visible work.

        Each entry is shaped like an intention projection (`from_intentions`
        reads id/title/kind/priority/deadline) except its kind is `project` and
        its id is the project's root intention, so `resolve_impulse_target`
        for `evolve_project` returns it and the resulting focus points at
        something `_advance_goal` can evolve.
        """
        if not hasattr(self.memory, "list_projects"):
            return []
        projects = await self.memory.list_projects(statuses=["evolving_cooldown"])
        now = datetime.now(UTC)
        out: list[dict[str, Any]] = []
        for project in projects:
            due_at = project.get("next_evolution_at")
            if due_at is not None:
                if getattr(due_at, "tzinfo", None) is None:
                    due_at = due_at.replace(tzinfo=UTC)
                if due_at > now:
                    continue
            root = project.get("root_intention_id")
            if not root:
                continue
            out.append({
                "id": str(root),
                "title": f"Evolve: {project.get('title') or 'project'}",
                "kind": "project",
                "status": "active",
                "priority": 0.55,
                "origin": "evolution",
                "deadline": None,
                "created_at": None,
                "project_id": str(project.get("project_id")),
            })
        return out

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
        tools = self._catalog_or_empty()
        budget = "unknown"
        try:
            state = await self.deps.gateway.budget()
            daily_cap = state.get("daily_cap")
            cap_text = f"/${daily_cap:.2f}" if daily_cap is not None else " (no daily cap)"
            budget = (
                f"day ${state['day_total']:.2f}{cap_text}, "
                f"commitment ${state['day_commitment']:.2f}, discretionary ${state['day_discretionary']:.2f}"
            )
        except Exception:
            budget = f"session ${self.spent_discretionary_session:.2f}"
        # Clio 3.2 DAG telemetry for the two-tier commitments block.
        try:
            telemetry = await self.memory.dag_telemetry()
        except Exception:
            telemetry = {
                "completion_ratio": 0.0, "blocked_edges": 0,
                "budget_burn_usd": 0.0, "ready_count": 0,
            }
        commitments = await self.memory.list_intentions(limit=12)
        # The portfolio: every non-done project with its verified standing, so
        # the workspace — and therefore every reply written from it — can name
        # a project that lost the focus an hour ago with its real numbers.
        project_ledger: list[dict[str, Any]] = []
        if hasattr(self.memory, "get_project_progress_ledger"):
            try:
                ledgers = await self.memory.get_project_progress_ledger(
                    current_focus_project_id=self.active_project_id,
                )
                project_ledger = [ledger.as_dict() for ledger in ledgers]
            except Exception:
                project_ledger = []
        memories = []
        query = self.focus.title if self.focus else "recent context"
        try:
            memories = await self.memory.search(query, k=12)
        except Exception:
            memories = []
        thought_texts = _tail(self._thoughts, 10)
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
                    "telemetry": telemetry,
                    "spent_usd": c.spent_usd,
                    "budget_spent_usd": getattr(c, "budget_spent_usd", c.spent_usd),
                }
                for c in commitments
            ],
            memories=memories,
            thoughts=thought_texts,
            thread=thread,
            recent=recent,
            conversations=conversations,
            stops=self.stops.render(),
            project_ledger=project_ledger,
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
        # Inbound messages take this pass immediately. The salience gate that
        # used to defer them here is removed: a short check-in ("Are you OK?")
        # scored 0.38 against a 0.65 focused threshold and waited ~1h, which
        # reads as the agent ignoring its owner. Focus is preserved instead by
        # suspending it across the social turn and restoring it in `next_focus`
        # when the reply commissions no new work.
        inbound = await self._pending_inbound_messages()

        # Immediate is not unconditional. Removing the gate fixed "the agent
        # ignored its owner" and opened "the agent never touches its work": with
        # no bound at all, the head of an unanswered queue takes every pass, so
        # an agent whose inbox fills faster than it drains never reaches the
        # `advance_goal` branch below. Every cycle answers somebody, the task
        # stays exactly where it was, and nothing in the record says the goal
        # was ever skipped -- a queue is supposed to be unbounded, so the
        # starvation is invisible from the message side too.
        #
        # So the priority is counted, not assumed. After
        # `inbound_defers_goal_max` consecutive passes spent on a message while
        # holding a goal, the goal is worked and the message stays at the head
        # of the queue for the next pass. Both needs are still served; neither
        # can starve the other, which is what an uncounted priority always
        # becomes.
        if inbound and self._goal_is_owed():
            self._inbound_deferrals += 1
            limit = max(0, int(self.config.rhythm.focus.inbound_defers_goal_max))
            if self._inbound_deferrals > limit:
                logger.info(
                    "life.goal_preferred_to_message",
                    deferrals=self._inbound_deferrals, limit=limit,
                )
                inbound = []
        elif inbound:
            self._inbound_deferrals = 0

        if inbound:
            if (
                self.focus is not None
                and self.focus.kind != "social"
                and self.focus.intention_id is not None
            ):
                await self.bookmark(self.focus)
                self._push_suspended(self.focus)
            tier = "T3" if self._message_is_consequential(inbound[0]) else "T2"
            bound_project, all_ledgers = await self._deliberation_project_context(inbound[0])
            social_decision = await self.deps.social.deliberate(
                message=inbound[0],
                energy=self.deps.rhythm.energy(self.spent_discretionary_session),
                focus_title=self.focus.title if self.focus else None,
                tools=self._tool_names(),
                preview=self._preview(),
                bound_project=bound_project,
                project_ledgers=all_ledgers,
            )
            return Decision(
                kind="social", tier=tier, social=social_decision.model_dump(mode="json"),
                rationale=f"social deliberation for {inbound[0].person_id}: immediate (attention gate removed)",
            )

        # Priority 2: pending governance notices. A project lead reports
        # kickoffs, human asks, blockers, milestones and focus switches without
        # waiting to be asked — right after the person who just wrote, and
        # before the agent's own held goal, because a human ask that waits
        # behind an hour of solver work is a person idling for that hour.
        governance = await self._due_governance_notices(limit=2)
        if governance:
            self._governance_batch = list(governance)
            self.states.transition(State.NOTIFYING, "governance notice owed")
            return Decision(
                kind="governance", tier="T2",
                stage={"notices": len(governance)},
                rationale=(
                    "governance: "
                    + ", ".join(str(n.event_type) for n in governance[:3])
                ),
            )

        progress = await self._proactive_progress_due()
        if progress is not None:
            self.states.transition(State.NOTIFYING, "proactive progress worth sharing")
            return Decision(
                kind="progress", tier="T2", stage=progress,
                rationale=f"proactive progress for {progress.get('person_id')}: '{progress.get('title')}'",
            )

        # Reverie preempts held work when its clock is due, and it costs one
        # pass, not the focus. The branch used to sit below the focus handback,
        # which meant it was reachable only when the focus was empty -- and the
        # focus is essentially never empty: a goal with a pending step hands
        # itself back every cycle (`_settled_focus`), so over a three-day run
        # with 12,159 cycles the idle branch beneath it fired 25 times and the
        # one path that can start work nobody asked for -- reverie, the only
        # origin of ventures -- never committed anything. A due reverie is the
        # same shape of event as an inbound message: a reason this pass is not
        # about the held goal, taken for one cycle and returned after. The
        # focus is left exactly where it is -- no bookmark, no suspension --
        # because reverie writes a thought and possibly a venture intention;
        # it does not spend the focus, and the next cycle's commission bypass
        # and deadline check still outrank whatever it picked up.
        if (
            self.focus is not None
            and self.focus.intention_id is not None
            and self.focus.kind != "social"
            and self.reverie is not None
            and self.deps.rhythm.reverie_due()
        ):
            return Decision(
                kind="reverie", tier="T1",
                rationale="idle clock due: choosing what to be drawn to (focus held)",
            )

        if self.focus is not None and self.focus.intention_id is not None:
            # Impulse work is the drives' own choice; a commission is a person
            # waiting. `choose_focus` gives the commission the pass outright,
            # but it only runs when the focus is empty -- and an impulse focus
            # with a pending step never empties: `_settled_focus` hands it back
            # every cycle, so the bypass is unreachable and the person's work
            # sat open behind the impulse for as long as the impulse held.
            # Observed live on 2026-10-25: every cycle for hours advanced a
            # drive placeholder while the commissioned task waited one priority
            # band above it, and the replies said the commissioned task was
            # being picked up. The ask is one indexed query, and only on a
            # focus the drives invented; a commission or a deadline keeps the
            # pass it already had.
            if self.focus.kind == "impulse":
                commission = await self.memory.next_open_commission()
                if commission is not None and commission.id != self.focus.intention_id:
                    self.focus = Focus(
                        intention_id=commission.id,
                        title=commission.title or "commissioned work",
                        kind=commission.kind or "commitment",
                        why="commissioned work outranks idle impulses",
                    )
            # Worked. Whatever was waiting in the queue has not been lost, and
            # the next pass starts the count over -- which is the point: the
            # bound is on how long a goal may be *continuously* deferred, not on
            # how many messages may be answered while work is outstanding.
            self._inbound_deferrals = 0
            return Decision(kind="advance_goal", tier="T2", rationale="advance current intention")

        # A word somebody is owed, spent on the pass after the one that earned it.
        #
        # This branch did not exist, and the absence is the whole of the second
        # complaint. The loop had exactly two ways to speak: answer a message that
        # arrived, or be a solver step that happened to call `comm.send`. So a
        # person who said "tell me when it's done" got told when it was done only
        # if some step's model, unprompted, decided to mention it -- a coin the
        # person never sees and cannot predict, which is what "sometimes" is from
        # the outside. The intention carried the outcome and the episode log
        # carried the fact, and nothing anywhere joined them to the conversation.
        #
        # It sits below an inbound message and above working a goal, and both of
        # those placements are load-bearing. Below the message, because a person
        # who has just written is owed an answer more than the agent is owed its
        # own follow-up. Above the goal, because a goal holds focus until it
        # reaches a terminal state, so a branch below it is a branch this loop
        # never reaches: a task that runs for a day would keep the notice out of
        # every pass for a day. It is one pass and one model call per stage, so
        # what it costs a goal is bounded and what it costs a promise is not
        # keeping it.
        owed = await self._due_stage_notice()
        if owed is not None:
            self.states.transition(State.NOTIFYING, "a stage somebody is owed an answer on")
            return Decision(
                kind="notify", tier="T2", stage=_stage_as_dict(owed),
                rationale=f"tell {owed.person_id} that '{owed.title}' reached {owed.stage}",
            )

        # A social focus exists to answer one message. With no message pending
        # there is nothing to deliberate, so this used to return a decision that
        # matched no branch in `act` -- a cycle spent entirely on producing
        # nothing, with the drives and the reverie below it unreachable for as
        # long as the focus lasted. `next_focus` now drops the focus, so it
        # costs one cycle rather than thousands; falling through instead costs
        # none, and gives the idle branches below the pass they were being
        # starved of.
        if self.focus is not None and self.focus.kind == "social":
            # Released here rather than left for `next_focus` to drop, so the
            # idle branches below get this pass and the drives get the next one.
            # A social focus is spent the moment its message is handled; holding
            # a dead one is what parked the agent for 2235 cycles once already.
            logger.info("life.social_focus_without_message", focus=self.focus.title)
            self.focus = None

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

        if self.focus is not None and self.focus.kind == "social":
            return Decision(
                kind="idle_model", tier="T1",
                rationale="social focus without pending message; always call the model",
            )

        return Decision(
            kind="idle_model", tier="T1",
            rationale="nothing pending; always call the model",
        )

    def _goal_is_owed(self) -> bool:
        """Whether the focus is real work this agent is committed to finishing.

        The question behind the inbound-priority bound: deferring a conversation
        to work on a task is a trade, and declining to make it is a different
        answer from not noticing there was one. So the thing being deferred has
        to be work, judged by the same test `decide` acts on -- a focus that
        carries an intention id -- and not merely any focus at all.

        A social focus is excluded because a message is never deferred by a
        message: the agent answering somebody is not a lapse from the work.
        """
        focus = self.focus
        return bool(
            focus is not None
            and focus.kind != "social"
            and focus.intention_id is not None
        )

    def _message_is_consequential(self, message: Any) -> bool:
        # The same judgement `SocialCognition` makes, for the same reason: this
        # picks the tier the deliberation is about to ask for, and the two
        # answers have to be one answer. It used to be a second, narrower list
        # of trigger words held in this file, so the tier a message was
        # deliberated at and the tier it was labelled with could disagree.
        from clio.social.deliberation import is_consequential_text

        return is_consequential_text(message.text or "")

    def _tool_names(self) -> list[dict[str, Any]]:
        """The registered tools, for the deliberation that writes the reply.

        Asked of the toolhost rather than hardcoded, so the names the agent is
        told it has are the names it has. A list written into the prompt is a
        second thing that can be wrong, and the failure of getting it wrong is
        the agent telling its owner it cannot do something it can -- see
        `clio.prompts.deliberation.tool_names_line`.
        """
        return self._catalog_or_empty()

    def _catalog_or_empty(self) -> list[Any]:
        """The tool catalog, or nothing when it cannot be had.

        Asked of the toolhost rather than hardcoded, so the names the agent is
        told it has are the names it has. A list written into the prompt is a
        second thing that can be wrong, and the failure of getting it wrong is
        the agent telling its owner it cannot do something it can -- see
        `clio.prompts.deliberation.tool_names_line`.
        """
        toolhost = self.deps.toolhost
        if toolhost is None:
            return []
        try:
            return list(toolhost.catalog())
        except Exception:
            return []

    async def _pending_inbound_messages(self) -> list[Any]:
        """The unanswered queue, answered immediately.

        The attention gate that used to arbitrate here is removed, so the head
        of this queue always takes the pass. Focus is not lost because `decide`
        suspends it beforehand and `next_focus` restores it afterwards.
        """
        return await self.deps.social.pending_inbound(limit=3)

    # ---------------------------------------------------------- live steering

    async def _drain_steering(self) -> int:
        """Move the active project's fresh messages into the steering queue.

        One message shape gets this treatment: one bound (by
        `conversation_project_bindings`) to the project that currently holds
        the execution focus, while that project has a run under way. A quick
        status question typed during a long step is exactly the case: it is
        guidance for work in progress, so it goes to the solver loop's next
        step boundary and is acknowledged there, instead of taking a full
        deliberation turn that would also suspend the goal it is asking about.

        Everything else — a different conversation, a message with no bound
        project, a project not under way — stays an ordinary message. This
        never silently swallows mail; it re-routes mail that names work the
        loop is holding right now.
        """
        if self.deps.steering is None or self.active_project_id is None:
            return 0
        if not self._goal_is_owed():
            return 0
        # Which conversations belong to this project: read the bindings, then
        # keep the ones bound to the active project.
        try:
            keys = await self._conversation_keys_for_project(str(self.active_project_id))
        except Exception:
            logger.exception("life.steering_bindings_failed")
            return 0
        if not keys:
            return 0
        focus_intention = str(self.focus.intention_id) if (self.focus and self.focus.intention_id) else None
        try:
            drained = await self.deps.steering.drain_conversation_inbound(
                self.deps.social, str(self.active_project_id), keys,
                intention_id=focus_intention,
            )
        except Exception:
            logger.exception("life.steering_drain_failed")
            return 0
        if drained:
            logger.info("life.steering_drained", count=len(drained),
                        project_id=str(self.active_project_id))
        return len(drained)

    async def _conversation_keys_for_project(self, project_id: str) -> set[str]:
        """The conversation keys bound to a project, from the bindings table."""
        rows = await self.memory.db.fetch(
            "SELECT conversation_key FROM conversation_project_bindings "
            "WHERE active_project_id = $1::uuid",
            project_id,
        )
        return {str(r["conversation_key"]) for r in rows}

    async def _maybe_fast_lane(self) -> bool:
        """One cooperative fast-lane micro-slot, bounded by `fast_lane_budget_s`.

        At a clean boundary during a locked quantum, a quick message in a
        secondary conversation is answered here — read-only or conversational,
        tier capped at T2, `active_project_id` untouched, the primary
        project's sandbox state undisturbed. The budget bounds the slot, not a
        single model call: when the reply cost the slot its ninety seconds the
        loop closes the slot and goes back to work. A message that turns out
        to ask for real work is left exactly where it was — unhandled, at the
        head of the queue — so the ordinary social turn takes it with the
        focus suspension and commissioning machinery the fast lane deliberately
        does not run.
        """
        budget = float(self.config.projects.fast_lane_budget_s)
        if budget <= 0.0:
            return False
        if not await self._project_lock_active():
            return False
        if self.focus is None or self.focus.kind == "social":
            return False
        pending = await self.deps.social.pending_inbound(limit=1)
        if not pending:
            return False
        message = pending[0]
        keys = await self._conversation_keys_for_project(str(self.active_project_id or ""))
        if str(getattr(message, "conversation_key", "") or "") in keys:
            # The active project's own conversation: that is steering's
            # territory (drained above), not a fast-lane turn.
            return False
        started = time.monotonic()
        bound_project, ledgers = await self._deliberation_project_context(message)
        social_decision = await self.deps.social.deliberate(
            message=message,
            energy=self.deps.rhythm.energy(self.spent_discretionary_session),
            focus_title=self.focus.title,
            tools=self._dialogue_tool_names(),
            preview=self._preview(),
            bound_project=bound_project,
            project_ledgers=ledgers,
            fast_lane=True,
        )
        if social_decision.creates_intention:
            logger.info(
                "life.fast_lane_deferred_to_full_turn", person_id=str(message.person_id),
            )
            return False
        # The reply goes out through the ordinary turn machinery — the same
        # delivery, the same record of the decision — with commissioning
        # impossible by construction: the decision above was cleared of it.
        from clio.schemas.messages import SocialDecision

        decision = SocialDecision.model_validate(social_decision.model_dump(mode="json"))
        decided = decision.model_copy(
            update={"creates_intention": False, "intention_spec": None},
        )
        await self._act_social(decided.model_dump(mode="json"))
        elapsed = time.monotonic() - started
        self._fast_lane_spent_s = float(getattr(self, "_fast_lane_spent_s", 0.0)) + elapsed
        logger.info(
            "life.fast_lane_answered", person_id=str(message.person_id),
            elapsed_s=round(elapsed, 1), budget_s=budget,
        )
        return True

    def _dialogue_tool_names(self) -> list[dict[str, Any]]:
        """The catalog narrowed to the dialogue-only (read-only) set.

        The fast lane hands the deliberation this list instead of the full
        registry, so the tools the reply can even *name* are the ones its
        budget can afford. The gate still judges every call; this narrows the
        offer, which is what makes the slot read-only by construction rather
        than by promise.
        """
        from clio.guardian.safety import usable_tool_names

        names = [str(entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", ""))
                 for entry in self._tool_names()]
        kept = set(usable_tool_names([n for n in names if n], 1))
        return [entry for entry in self._tool_names()
                if str(entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")) in kept]

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
            self._record_solver_stops(result)
        elif decision.kind == "social" and decision.social is not None:
            commissioned = await self._act_social(decision.social)
            outcome.commission = commissioned
        elif decision.kind == "notify" and decision.stage is not None:
            outcome.ok = await self._notify_stage(decision.stage)
        elif decision.kind == "governance":
            batch = self._governance_batch
            self._governance_batch = []
            outbox = getattr(self.deps.gsl, "governance", None)
            delivered_any = False
            for notice in batch:
                delivered = await self._deliver_governance_notice(notice)
                if outbox is not None and hasattr(outbox, "remove"):
                    outbox.remove(notice)
                delivered_any = delivered_any or delivered
            outcome.ok = delivered_any if batch else True
            outcome.results.append({"kind": "governance", "notices": len(batch)})
        elif decision.kind == "progress" and decision.stage is not None:
            outcome.ok = await self._report_progress(decision.stage)
        elif decision.kind == "mind_wander":
            cost = await self._mind_wander()
            outcome.cost_usd += cost
            self.spent_discretionary_session += cost
        elif decision.kind == "idle_model":
            cost = await self._idle_model_call()
            outcome.cost_usd += cost
            self.spent_discretionary_session += cost
            outcome.results.append({"kind": "idle_model", "cost_usd": cost})
        elif decision.kind == "reverie":
            result = await self._reverie()
            outcome.ok = result.refused == "" and result.committed_id is not None
            outcome.results.append(result.model_dump(mode="json"))
            outcome.cost_usd += result.cost_usd
            self.spent_discretionary_session += result.cost_usd
        for thought in decision.thoughts:
            await self._record_thought(
                thought, importance=0.3, half_life_h=12,
                intention_id=self.focus.intention_id if self.focus else None,
                focus=self.focus.title if self.focus else None,
            )
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        return outcome

    async def _advance_goal(self) -> Any:
        assert self.focus is not None and self.focus.intention_id is not None
        intention = await self.memory.get_intention(self.focus.intention_id)
        if intention is None:
            from clio.schemas.gsl import SolverResult

            return SolverResult(outcome=SolverOutcome.goal_failed,
                                 thoughts=["intention vanished"])
        # An evergreen project resting in its cooldown, advanced because the
        # drives proposed it, does not run its (already done) root intention
        # again — it proposes the next evolution cycle.
        project = await self._get_project(
            str(intention.project_id) if intention.project_id else None
        )
        if project is not None and str(project.get("status") or "") == "evolving_cooldown":
            due_at = project.get("next_evolution_at")
            if due_at is None:
                due = True
            else:
                if getattr(due_at, "tzinfo", None) is None:
                    due_at = due_at.replace(tzinfo=UTC)
                due = due_at <= datetime.now(UTC)
            if due:
                return await self.deps.gsl.propose_next_evolution(project)
        if intention.status in ("done", "cancelled", "failed", "parked"):
            from clio.schemas.gsl import SolverResult

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

    def _record_solver_stops(self, result: Any) -> None:
        """Every ending this solver result carries, into the stops ledger.

        NA-P3.1/3.2: a task termination -- done, parked, failed -- and every
        model or tool call failure each get their own entry with a
        distinguishable kind, read off the structured `failures` list the loop
        now attaches and off the outcome itself. Nothing here composes a
        sentence for anybody: the ledger states the facts, the workspace block
        carries them, and the words are the agent's to choose downstream.
        """
        subject = self.focus.title if self.focus else ""
        intention_id = str(self.focus.intention_id) if self.focus and self.focus.intention_id else None
        for failure in result.failures or []:
            self.stops.record(
                kind=str(failure.get("kind") or StopKind.error.value),
                subject=subject,
                reason=str(failure.get("detail") or "")[:300],
                what=str(failure.get("what") or ""),
                intention_id=intention_id,
            )
        if result.outcome == SolverOutcome.goal_done.value:
            self.stops.record(
                kind=StopKind.completed,
                subject=subject,
                reason="the work reached its goal and was verified",
                intention_id=intention_id,
            )
        elif result.outcome == SolverOutcome.parked.value:
            reason = ""
            for thought in result.thoughts:
                if thought.startswith("parked"):
                    reason = thought
                    break
            self.stops.record(
                kind=StopKind.stopped,
                subject=subject,
                reason=reason[:300],
                intention_id=intention_id,
            )
        elif result.outcome == SolverOutcome.goal_failed.value:
            self.stops.record(
                kind=StopKind.error,
                subject=subject,
                reason="; ".join(result.thoughts)[:300],
                intention_id=intention_id,
            )

    def record_stop(
        self, kind: StopKind | str, subject: str = "", reason: str = "", what: str = "",
    ) -> None:
        """One stop, from anywhere in the loop: the one door to the ledger."""
        self.stops.record(kind=kind, subject=subject, reason=reason, what=what)

    async def _maybe_reopen_parked(self) -> None:
        """Parked work gets another chance, on a backoff, without a human.

        Parking was designed as "closed until reopened" with the reopening
        left to a person, which in the field is the difference between a
        pause and a loss: a goal that parked because a delegate thread
        crashed, or because the model produced bad plans three times in a
        row, stayed parked forever while the agent idled with owed work on
        a shelf. The sweep reopens aged parked intentions so the drives can
        pick them up again. Reopens are unbounded by default -- a goal that
        parks, reopens and parks again is a goal the agent has not given up
        on -- and a positive `park_reopen_max` bounds the sweep for
        deployments that want the shelf back.

        Intentions parked for budget are skipped: the budget comes back on
        its own schedule and the work resumes then, and reopening early
        would only re-park it. Everything here is best-effort -- a sweep
        that cannot read the store costs one log line and tries again on
        the next due pass.
        """
        gsl_cfg = self.config.gsl
        if gsl_cfg.park_reopen_max < 0:
            return
        if time.monotonic() - self._last_park_sweep < PARK_REOPEN_CHECK_S:
            return
        self._last_park_sweep = time.monotonic()
        try:
            stale = await self.memory.list_parked_intentions(gsl_cfg.park_reopen_backoff_s)
        except Exception:
            logger.warning("life.park_sweep_failed")
            return
        for intention in stale:
            resume = dict(intention.resume_state or {})
            reason = str(resume.get("parked_reason") or intention.closed_reason or "")
            if reason.startswith("budget exhausted"):
                continue
            if reason.startswith("zombie:"):
                # Parked by the zombie sweep for having no focus and no
                # activity: reopening it on the park backoff would recreate
                # the strand the sweep exists to end. It stays parked —
                # visible, noticed, and reopenable by a person — until then.
                continue
            # A placeholder named after a drive is not owed work, so it is not
            # owed a reopen either. The sweep exists to give real work another
            # chance; an "advance_commitment" row parked by its own fake loop
            # would be resurrected by this sweep every backoff window, and
            # nothing else in the system can tell it apart from work a person
            # is waiting on. With the drives no longer minting placeholders
            # (see `Motivation.choose_focus`) this only guards the ones that
            # already exist.
            if intention.origin == "impulse" and intention.title in IMPULSE_TARGETS:
                continue
            # An environment blocker is not the goal being impossible and not
            # the loop having failed: it is the world missing a thing only the
            # owner can supply -- a dead key (HTTP 402), no model for the tier,
            # an approval that never arrived. Reopening it on a backoff asks
            # the identical broken request again and parks it again; observed
            # live over 2026-11-08/09/10, the CodeForge commissions rode this
            # sweep for days. Such a goal stays parked -- visible in the
            # snapshot, escalated to the owner at park time (see `Gsl._park`)
            # -- until the owner changes the world, which is a person's
            # decision, not a sweep's.
            if resume.get("blocked_on_environment"):
                continue
            reopens = int(resume.get("park_reopens") or 0)
            if gsl_cfg.park_reopen_max > 0 and reopens >= gsl_cfg.park_reopen_max:
                continue
            resume["park_reopens"] = reopens + 1
            resume.pop("parked_reason", None)
            # A fresh degradation ladder for a goal that already climbed
            # one: the six attempts that earned the park are spent, and the
            # question this reopen asks is whether the world has changed,
            # which deserves the same six answers the first pass got.
            resume[PLAN_ATTEMPTS] = 0
            resume[PLAN_RETRY_AFTER] = None
            resume.pop(PLAN_DEGRADED, None)
            try:
                await self.memory.update_intention(
                    intention.id,
                    status=IntentionStatus.active.value,
                    resume_state=resume,
                )
            except Exception:
                logger.exception("life.park_reopen_failed", intention=str(intention.id))
                continue
            logger.info(
                "life.park_reopened", intention=str(intention.id),
                reopen=reopens + 1,
                of_max=(gsl_cfg.park_reopen_max if gsl_cfg.park_reopen_max > 0 else None),
                reason=reason[:200],
            )

    async def _maybe_park_zombies(self) -> None:
        """Park `in_progress` work nothing is working on, with a notice.

        A focus preemption used to strand a goal exactly here: `in_progress`
        in the store, no focus, no journal activity, no attempt closure —
        and nothing that would ever notice, because every query that reports
        on open work looks at the focused intention or the newest rows. The
        goal then sat open indefinitely while replies (reading a snapshot
        that could not see it) reported the *next* task as not started.

        The sweep is the other half of the suspended-focus stack: work the
        stack is holding is owed the pass back and is exempt; everything
        else that has been untouched — by focus or by journal — for
        `ZOMBIE_STALE_S` is parked, its dangling attempt closed, and a
        stage notice recorded so the person is told. Parked-for-zombie work
        is not reopened by the park sweep (see the `zombie:` check there):
        reopening it would recreate the strand.
        """
        if time.monotonic() - self._last_zombie_sweep < PARK_REOPEN_CHECK_S:
            return
        self._last_zombie_sweep = time.monotonic()
        try:
            rows = await self.memory.list_intentions(
                statuses=[IntentionStatus.in_progress.value], limit=50,
            )
        except Exception:
            logger.warning("life.zombie_sweep_failed")
            return
        if not rows:
            return
        held = {
            f.intention_id for f in self._suspended_stack if f.intention_id is not None
        }
        if self.focus is not None and self.focus.intention_id is not None:
            held.add(self.focus.intention_id)
        now = datetime.now(UTC)
        for intention in rows:
            if intention.id in held:
                continue
            updated = intention.updated_at
            if updated is None or (now - updated).total_seconds() < ZOMBIE_STALE_S:
                continue
            reason = (
                f"zombie: in_progress with no focus and no update for "
                f"{round((now - updated).total_seconds() / 3600.0, 1)} h; "
                f"parked so it is visible rather than stranded"
            )
            try:
                resume = dict(intention.resume_state or {})
                attempt_id = resume.pop("attempt_id", None)
                if attempt_id:
                    end_attempt = getattr(self.memory, "end_attempt", None)
                    if end_attempt is not None:
                        await end_attempt(
                            UUID(str(attempt_id)), "preempted",
                            {"intention_id": str(intention.id),
                             "title": (intention.title or "")[:200]},
                            ["focus was preempted and the work was never resumed"],
                            0.0,
                        )
                await self.memory.update_intention(
                    intention.id,
                    status=IntentionStatus.parked.value,
                    closed_reason=reason[:800],
                    resume_state=resume,
                )
                record_notice = getattr(self.memory, "record_stage_notice", None)
                if record_notice is not None:
                    await record_notice(intention, "parked", reason)
                logger.info(
                    "life.zombie_parked", intention=str(intention.id),
                    title=(intention.title or "")[:120],
                )
            except Exception:
                logger.exception(
                    "life.zombie_park_failed", intention=str(intention.id),
                )

    async def _act_social(self, social: dict[str, Any]) -> dict[str, Any] | None:
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
        from clio.schemas.messages import SocialDecision

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
                    category="casual",
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
        commission: dict[str, Any] | None = None
        if decision.creates_intention and decision.intention_spec:
            spec = decision.intention_spec
            title = str(spec.get("title") or "commission").strip() or "commission"
            adopted = await self._already_commissioned(title, message.person_id)
            if adopted is not None:
                # The work is already on the books and the person is asking again
                # because it has not visibly happened. Adopting the open row is
                # the whole fix; see `_already_commissioned` for what creating a
                # second one costs.
                commission = {"intention_id": str(adopted.id), "title": adopted.title}
            else:
                intention_id = await self.memory.create_intention(
                    title=title,
                    desired_end_state=spec.get("desired_end_state", ""),
                    kind="commitment",
                    origin="commission",
                    commissioned_by=message.person_id,
                    deadline=_deadline_from_spec(spec.get("deadline_iso")),
                )
                commission = {"intention_id": str(intention_id), "title": title}
                # A commission big enough to decompose is a project: it gets
                # its own container, its own isolated workspace lease, and its
                # own conversation binding. Two projects assigned from two
                # conversations therefore never share a file or a ledger.
                await self._maybe_create_project(
                    intention_id, title, spec, message,
                )
                # A follow-up that sequences the new work behind the work in
                # hand ("once that is finished, review X") is recorded as a
                # dependency, not just promised in prose: the edge is what
                # `get_ready_intentions` and the drives read, and without it
                # the ordering lived only in the reply that announced it —
                # which is how the prior work came to be discarded for the
                # work sequenced behind it (2026-11-08). `_settled_focus`
                # honors the flag by returning the suspended focus.
                prior = self._suspended_focus
                if prior is not None and prior.intention_id is not None:
                    sequenced = bool(
                        _SEQUENCED_RE.search(str(getattr(message, "text", "") or ""))
                    )
                    commission["sequenced"] = sequenced
                    if sequenced:
                        add_edge = getattr(self.memory, "add_intention_edge", None)
                        if add_edge is not None:
                            try:
                                await add_edge(
                                    prior.intention_id, intention_id,
                                    edge_type="depends_on",
                                )
                            except Exception:
                                logger.warning(
                                    "life.sequenced_edge_failed",
                                    prior=str(prior.intention_id),
                                    commission=str(intention_id),
                                )
        # A reply that answers an open human node closes it: the intention is
        # marked done with the person's evidence, and the nodes that depended
        # on it become ready — the project resumes on its own.
        await self._resolve_human_node(decision, message)
        # What this turn has to say, one message per text.
        #
        # A deliberation writes its reply as a list of short texts, and the list
        # used to be joined into one message because a question the agent had
        # answered once went out as two or three deliveries, and every text after
        # the first arrived as a message answering nothing — a surface's wait
        # matches the first outbound message after the question, and the rest
        # read from the other end as the same reply sent twice.
        #
        # The fix that holds is not the join but the reference: every text is sent
        # carrying `in_reply_to=message.id`, so each delivery names the question it
        # answers and no text is an answer to nothing. What that buys back is the
        # shape the agent chose — a thought and its aside arrive as the messages
        # they were written as, however many of those there are, the way a person
        # sends them — and what it keeps is the invariant the join existed for:
        # nothing is dropped, nothing is decided twice, and every message on the
        # surface answers the message it followed.
        # An entry with no words in it is not a message, so it is not sent as one.
        #
        # Each text can also name the breath before it: `reply_delays_s[i]` is how
        # long to wait before text i goes out, so a reply written as several
        # messages can arrive the way a person sends several — a gap between them
        # rather than one burst. The wait happens here, inside the turn, and not
        # on a timer elsewhere, because the turn is the only thing that knows the
        # reply is not finished: the message is marked handled only after the last
        # text is out, so a restart mid-reply re-deliberates rather than
        # forgetting the tail. Each gap is clamped to `reply_gap_max_s`, because
        # the number came out of a model and a misread unit must cost seconds,
        # not an afternoon of a held message.
        pairs: list[tuple[str, float]] = []
        for index, text in enumerate(decision.reply_texts):
            if not text or not text.strip():
                continue
            delays = decision.reply_delays_s
            delay = float(delays[index]) if index < len(delays) else 0.0
            pairs.append((text.strip(), max(0.0, delay)))
        texts = [text for text, _ in pairs]
        # Nothing to say is the same decision either way round: whether the action
        # was `act_silently` or a reply that turned out to be blank, the turn ends
        # in a declared silence and nothing goes out.
        if not texts:
            await self._record_silence_turn(message, decision)
            failed: dict[str, Any] | None = None
            if decision.decided:
                await self.deps.social.mark_handled(message, decided)
            else:
                # Nothing was decided, so nothing is finished with. Retiring the
                # row is what closed the question as though the agent had
                # answered it, and the agent answered nothing.
                failed = await self._hold_undecided(message, decision)
                if isinstance(failed, dict) and failed.get("reason"):
                    # The queue now knows more than the decision does: which
                    # attempt this was and, for a rate limit, how long the wait
                    # is. Carry that into the announcement so the screen names
                    # the pause ("waiting about 1h") instead of a bare failure.
                    decision.reason = str(failed["reason"])[:500]
            # On the last attempt `_hold_undecided` has already published the
            # ending -- "could not, after N attempts" -- and the decline
            # announcement here would say "retrying..." over the top of it,
            # which is the one status the message can never return to.
            if not (isinstance(failed, dict) and failed.get("_final")):
                await self._announce_decline(message, decision)
            return commission
        gap_ceiling = max(0.0, float(self.config.social.reply_gap_max_s))
        # `instruct_user` and `report_governance` are project-lead speech: they
        # go out through the governance fast-path (immediate, deduped, their
        # own daily cap) rather than the casual digest and etiquette queue.
        category = (
            "governance"
            if decision.action.value in ("instruct_user", "report_governance")
            else "casual"
        )
        for index, (text, delay) in enumerate(pairs):
            if index and delay > 0:
                waited = min(delay, gap_ceiling)
                logger.info("life.reply_paced", gap_s=round(waited, 2), next=text[:80])
                await asyncio.sleep(waited)
            await self.deps.comms_out.send(
                person_id=message.person_id,
                channel=message.channel,
                text=text,
                urgency=decision.urgency,
                conversation_key=message.conversation_key,
                in_reply_to=message.id,
                category=category,
            )
            await self._record_outbound_turn(message, text)
        await self.deps.social.mark_handled(message, decided)
        return commission

    async def _maybe_create_project(
        self,
        intention_id: Any,
        title: str,
        spec: dict[str, Any],
        message: Any,
    ) -> dict[str, Any] | None:
        """Give a big enough commission its own project container.

        Two conversations assigning two projects used to overwrite one focus
        slot with no record of which was which. A project fixes that from the
        bottom up: the root intention carries `project_id`, the container
        carries its own workspace lease (git worktree inside a repo, an
        isolated snapshot directory otherwise), and the conversation gets a
        binding — so Project 1 and Project 2 are separate by construction.

        A second project assigned while the first is inside its focus quantum
        is created `preempted`: it has a plan and a kickoff report, and it
        waits its turn rather than taking focus.
        """
        if not hasattr(self.memory, "create_project"):
            return None
        from clio.gsl.planner import classify_lifecycle_mode, needs_decomposition

        desired = str(spec.get("desired_end_state", "") or "")
        lifecycle = classify_lifecycle_mode(title, desired)
        placeholder = SimpleNamespace(
            title=title, desired_end_state=desired, kind="commitment",
            commissioned_by=message.person_id,
            success_criteria=list(spec.get("success_criteria") or []),
        )
        if not needs_decomposition(placeholder):
            return None
        lock_active = await self._project_lock_active()
        # Inside another project's locked quantum the new project is queued
        # (`preempted`); outside it, the new assignment may take the focus.
        status = "preempted" if (lock_active and self.active_project_id is not None) else "active"
        project = await self.memory.create_project(
            title=title,
            owner_principal_id=str(message.person_id or "owner"),
            root_intention_id=intention_id,
            lifecycle_mode=lifecycle,
            status=status,
            leases=getattr(self.deps, "leases", None),
        )
        conversation_key = str(getattr(message, "conversation_key", "") or "")
        if conversation_key:
            try:
                await self.memory.bind_conversation_project(
                    conversation_key, project["project_id"],
                )
            except Exception:
                logger.warning("life.project_bind_failed", project=project["project_id"])
        # Initial decomposition, now rather than at first advance: the new
        # project gets a concrete DAG immediately, its `human` nodes are
        # created as asks, and the kickoff report presents the plan in
        # conversation B — including the fact that this project is queued
        # behind the one currently executing, when it is.
        try:
            gsl = getattr(self.deps, "gsl", None)
            decomposer = getattr(gsl, "_maybe_decompose", None)
            root = await self.memory.get_intention(intention_id)
            if decomposer is not None and root is not None:
                # The container exists because this commission was judged
                # project-sized, so the decomposition is forced: difficulty is
                # the switch `needs_decomposition` reads for exactly this.
                resume = dict(root.resume_state or {})
                resume["difficulty"] = max(0.8, float(resume.get("difficulty") or 0.0))
                await decomposer(root, resume)
        except Exception:
            logger.exception("life.project_decompose_failed", project=project["project_id"])
        logger.info(
            "life.project_assigned", project=project["project_id"], title=title[:120],
            status=status, lease=project.get("workspace_lease_id"),
            conversation=conversation_key or None,
        )
        # A new project never silently takes the execution focus: if the lock
        # is held, the project is queued with its plan and its kickoff report
        # says so — the first project keeps its quantum. Outside the lock
        # (and for the first project of all), the switch is explicit and
        # checkpointed like every other one.
        if status == "active" and str(self.active_project_id or "") != project["project_id"]:
            await self._switch_project_focus(
                self.active_project_id, project["project_id"],
                reason=f"new project “{title}” assigned",
            )
        return project

    async def _resolve_human_node(self, decision: Any, message: Any) -> bool:
        """Close an `awaiting_human` node the person's reply just answered.

        The deliberation reports `resolves_human_node_id` with the evidence
        when the message fulfills an open human ask. Closing the node is the
        unlock: `get_ready_intentions` treats done predecessors as satisfied,
        so every node that depended on it becomes ready on the next cycle, and
        the project — paused only because nothing agent-executable remained —
        is set back to `active`.
        """
        node_id = getattr(decision, "resolves_human_node_id", None)
        if not node_id:
            return False
        try:
            node_uuid = UUID(str(node_id))
        except (TypeError, ValueError):
            logger.warning("life.human_node_unreadable", node=str(node_id)[:60])
            return False
        evidence = getattr(decision, "human_node_evidence", None) or {}
        try:
            node = await self.memory.get_intention(node_uuid)
            if node is None:
                return False
            if node.status == "done":
                return False
            await self.memory.update_intention(
                node_uuid,
                status="done",
                closed_reason="resolved by the owner's reply",
                instruction_spec={
                    **(node.instruction_spec or {}),
                    "evidence": evidence,
                    "resolved_by": str(getattr(message, "person_id", "") or ""),
                },
            )
            await self.memory.record_episode(
                kind="goal_done",
                summary=f"human step resolved: {node.title}",
                intention_id=node_uuid,
                importance=0.7,
            )
        except Exception:
            logger.exception("life.human_node_resolve_failed", node=str(node_uuid))
            return False
        if node.project_id is not None:
            try:
                await self.memory.update_project(
                    str(node.project_id), status="active", last_active_at=datetime.now(UTC),
                )
            except Exception:
                logger.warning("life.human_node_project_resume_failed", node=str(node_uuid))
        logger.info("life.human_node_resolved", node=str(node_uuid), evidence=bool(evidence))
        return True

    async def _already_commissioned(self, title: str, commissioned_by: str | None) -> Any | None:
        """The open row for this same work, when the owner is asking for it again.

        Asking twice is what a person does when the first ask did not visibly
        happen, and it used to be answered by writing the promise down twice. The
        cost was not the table. Progress through a goal lives in
        `intention.resume_state`, so a second intention is a second attempt at
        stage one: it understands again, plans again, and steps again from
        nothing — and `_commission_focus` hands the next cycle to it, abandoning
        whatever the first one had worked out.

        That is not a slow agent. It is an agent that cannot finish, because every
        time it tells the owner the work is under way it has also thrown away the
        part where it found out what the work is. One request to run a sub-agent
        produced three intentions with the same title, `understanding` written to
        each in turn, no plan ever made and no tool ever called — the work
        restarted at a rate faster than a single stage could be completed.

        So the promise is made once and resumed. The row that is already open
        keeps its `resume_state`, gets the focus, and the reply answers the person
        who asked rather than the bookkeeping behind it.

        Returns `None` — which is the ordinary case, and the one where work really
        is new — when nothing open matches. A store without the lookup, or one
        that could not answer, degrades to creating the row: the duplicate is a
        lesser failure than refusing work that was genuinely asked for, and this
        runs inside a cycle that has already answered somebody.
        """
        finder = getattr(self.memory, "find_open_intention", None)
        if finder is None:
            return None
        try:
            existing = await finder(title, commissioned_by=commissioned_by)
        except Exception:
            logger.warning("life.commission_lookup_failed", title=title[:120])
            return None
        if existing is None:
            return None
        resume = existing.resume_state or {}
        logger.info(
            "life.commission_resumed",
            intention=str(existing.id),
            title=existing.title,
            status=existing.status,
            understood=bool(resume.get("understanding")),
            planned=bool(resume.get("plan")),
        )
        return existing

    # ----------------------------------------------------- proactive progress

    async def _proactive_progress_due(self) -> dict[str, Any] | None:
        """An open commission whose real progress has not yet been shared.

        A liberty, not a duty, gated in both directions: the interval per
        person's verbosity level (quiet: only milestones, spaced out;
        detailed: every verified step) *and* fresh journaled actions since
        the last word to that person. A check that finds neither sends
        nothing, so the interval cannot spam.
        """
        if not bool(getattr(self.config.social, "proactive_enabled", True)):
            return None
        if not hasattr(self.deps.social, "compose_progress_note"):
            return None
        candidates: list[Any] = []
        if self.focus is not None and self.focus.intention_id is not None:
            try:
                focus_intention = await self.memory.get_intention(self.focus.intention_id)
            except Exception:
                focus_intention = None
            if focus_intention is not None and getattr(focus_intention, "commissioned_by", None):
                candidates.append(focus_intention)
        try:
            open_work = await self.memory.list_intentions(limit=12)
        except Exception:
            open_work = []
        for intention in open_work or []:
            commissioned_by = getattr(intention, "commissioned_by", None)
            if not commissioned_by or not getattr(intention, "is_open", None):
                continue
            status = str(getattr(intention, "status", "") or "")
            if status not in ("proposed", "active", "in_progress", "waiting", "awaiting_review"):
                continue
            if self.focus is not None and intention.id == self.focus.intention_id:
                continue
            candidates.append(intention)
        db = getattr(self.memory, "db", None)
        for intention in candidates[:4]:
            person_id = str(intention.commissioned_by or "")
            level = "balanced"
            getter = getattr(self.deps.social, "get_verbosity", None)
            if getter is not None:
                try:
                    level = normalise_verbosity(await getter(person_id))
                except Exception:
                    level = "balanced"
            interval = _verbosity_interval_min(level)
            last_outbound: datetime | None = None
            channel: str | None = None
            if db is not None:
                try:
                    row = await db.fetchrow(
                        "SELECT max(ts) AS last_out, min(channel) AS channel FROM messages "
                        "WHERE direction = 'outbound' AND person_id = $1 AND intention_id = $2",
                        person_id, intention.id,
                    )
                    last_outbound = row["last_out"] if row else None
                    channel = row["channel"] if row and row["channel"] else None
                except Exception:
                    last_outbound = None
                if channel is None:
                    try:
                        inbound_row = await db.fetchrow(
                            "SELECT channel FROM messages WHERE direction = 'inbound' "
                            "AND person_id = $1 ORDER BY ts DESC LIMIT 1",
                            person_id,
                        )
                        channel = inbound_row["channel"] if inbound_row else None
                    except Exception:
                        channel = None
            if last_outbound is not None:
                age_min = (datetime.now(UTC) - last_outbound).total_seconds() / 60.0
                if age_min < interval:
                    continue
            # Fresh evidence of movement since the last word. Quiet asks
            # for a real milestone (>=3 journal actions), not one action.
            need = 3 if level == "quiet" else 1
            fresh = 0
            if db is not None:
                try:
                    if last_outbound is None:
                        row = await db.fetchval(
                            "SELECT COUNT(*) FROM action_journal WHERE intention_id = $1",
                            intention.id,
                        )
                    else:
                        row = await db.fetchval(
                            "SELECT COUNT(*) FROM action_journal "
                            "WHERE intention_id = $1 AND ts_start > $2",
                            intention.id, last_outbound,
                        )
                    fresh = int(row or 0)
                except Exception:
                    fresh = need
            else:
                fresh = need
            if fresh < need:
                continue
            progress = self._progress_summary(intention, fresh)
            if not progress:
                continue
            return {
                "intention_id": str(intention.id),
                "title": str(intention.title or ""),
                "person_id": person_id,
                "channel": channel or "web",
                "progress": progress,
                "verbosity": level,
            }
        return None

    @staticmethod
    def _progress_summary(intention: Any, fresh_actions: int = 0) -> str:
        resume = getattr(intention, "resume_state", None) or {}
        if isinstance(resume, str):
            import json as _json

            try:
                resume = _json.loads(resume)
            except ValueError:
                resume = {}
        plan = resume.get("plan") or {}
        steps = plan.get("steps") or []
        done = sum(1 for step in steps if isinstance(step, dict) and step.get("status") == "done")
        artifacts = resume.get("artifacts") or []
        delivered = [
            str(action.get("args", {}).get("path") or "")
            for action in artifacts
            if isinstance(action, dict) and action.get("ok", True)
            and str((action.get("args") or {}).get("path") or "")
        ][-2:]
        current = ""
        for step in steps:
            if isinstance(step, dict) and step.get("status") != "done":
                current = str(step.get("description") or "")[:140]
                break
        parts: list[str] = []
        if steps:
            parts.append(f"{done} of {len(steps)} plan steps verified done")
        elif fresh_actions:
            parts.append(f"{fresh_actions} new actions journaled")
        if delivered:
            parts.append("files written: " + ", ".join(delivered))
        if current:
            parts.append(f"now: {current}")
        return "; ".join(parts)

    async def _report_progress(self, stage: dict[str, Any]) -> bool:
        """Share one proactive progress note, model-composed and etiquette-checked."""
        title = str(stage.get("title") or "")
        progress = str(stage.get("progress") or "")
        person_id = stage.get("person_id")
        text = await self.deps.social.compose_progress_note(
            title=title, progress=progress, person_id=person_id,
            preview=self._preview(),
        )
        if not text:
            return False
        try:
            await self.deps.comms_out.send(
                person_id=person_id,
                channel=stage.get("channel") or "web",
                text=text,
                urgency=Urgency.normal.value,
                conversation_key=stage.get("conversation_key"),
                in_reply_to=None,
                intention_id=stage.get("intention_id"),
            )
        except Exception as exc:
            logger.warning("life.progress_send_failed", error=str(exc)[:200])
            return False
        await self._record_outbound_notice_turn(
            {"title": title, "person_id": person_id, "stage": "progress",
             "channel": stage.get("channel"), "intention_id": stage.get("intention_id"),
             "conversation_key": stage.get("conversation_key")}, text,
        )
        return True

    # ----------------------------------------------------- stage notices

    async def _due_stage_notice(self) -> Any | None:
        """The oldest word this agent still owes somebody, or None.

        Asked of the store rather than tracked here, because it has to survive a
        restart: the debt is somebody's, not this process's, and an agent that
        forgets it on restart has kept the record of the work and lost the reason
        to speak. The store also bounds how often it is retried, so a channel that
        cannot deliver comes back on a curve rather than every tick.

        A notice with nobody to deliver to is skipped rather than attempted. The
        store already declines to write one for work nobody commissioned, so this
        is the belt to that braces: a row without an address cannot be sent on,
        and taking a pass to discover that would spend the pass and leave the debt.
        """
        getter = getattr(self.memory, "due_stage_notices", None)
        if getter is None or not hasattr(self.deps.social, "compose_stage_notice"):
            # A store without notices, or a social layer without the composer, is a
            # deployment that has neither. Taking the pass and failing in `act`
            # would spend a cycle to produce nothing and leave the debt owed, which
            # is the same silent drop the branch exists to remove — so the branch
            # does not open at all.
            return None
        try:
            due = await getter(limit=1)
        except Exception:
            logger.warning("life.stage_notice_read_failed")
            return None
        if not due:
            return None
        notice = due[0]
        return notice if getattr(notice, "person_id", None) else None

    async def _notify_stage(self, stage: dict[str, Any]) -> bool:
        """Say the one thing this stage notice is for, in the agent's own words.

        Four things happen here and each is a decision rather than plumbing.

        **The words are composed, not assembled.** `compose_stage_notice` asks the
        model, against a prompt written for opening a conversation rather than
        answering one. Nothing in this program sends a person a sentence nobody
        chose, and that is not a style preference: the transcript cannot tell a
        composed line from a real reply, so a canned one is indistinguishable from
        a lie told with a straight face.

        **The send names the intention.** That is what makes the promised-update
        rules apply downstream -- the digest window and the unanswered check are
        both skipped, because both ask whether the agent is *starting* a
        conversation, and this is not one. It is the answer to something it already
        said.

        **"Sent" is not the same as delivered, and only the first clears the
        debt.** A send that comes back `held`, `suppressed` or `failed` leaves the
        notice owed, with the reason recorded and a wait set — because a promise
        that failed once should be tried again and a promise that fails forever
        should stop being tried at the tick rate, and both need the row to say
        which happened.

        **The debt is eventually abandoned, explicitly.** Past the attempt cap the
        notice is marked delivered with the exhaustion recorded rather than left
        owed. That is the one outcome that is not "the person was told", and it is
        chosen over the alternative on purpose: an unbounded retry loop is an
        outage that looks like a notification, and a settled row is a thing an
        operator can see and act on, where a row stuck at `attempts: 400` is not.
        """
        notice_id = stage.get("notice_id")
        text = await self.deps.social.compose_stage_notice(stage, preview=self._preview())
        if not text:
            return await self._settle_stage_notice(
                notice_id, stage, delivered=False,
                detail="there was nothing worth saying",
            )
        person_id = stage.get("person_id")
        channel = stage.get("channel") or "web"
        try:
            result = await self.deps.comms_out.send(
                person_id=person_id,
                channel=channel,
                text=text,
                urgency=Urgency.normal.value,
                conversation_key=stage.get("conversation_key"),
                in_reply_to=None,
                intention_id=stage.get("intention_id"),
            )
        except Exception as exc:
            logger.warning("life.stage_notice_send_failed", error=str(exc)[:200])
            return await self._settle_stage_notice(
                notice_id, stage, delivered=False, detail=f"the send raised: {exc}"[:200],
            )
        status = str((result or {}).get("status") or "sent")
        if status in ("sent", "queued"):
            # `queued` clears the debt because the words are on their way and the
            # digest owns them from here; a queued entry that the digest later
            # refuses is a gap in the digest's own gates, not in the notice.
            await self._settle_stage_notice(
                notice_id, stage, delivered=True, detail=text,
                delivery={"status": status, "detail": (result or {}).get("detail")},
            )
            await self._record_outbound_notice_turn(stage, text)
            return True
        await self._settle_stage_notice(
            notice_id, stage, delivered=False, detail=f"{status}: {text}"[:300],
            delivery=result,
        )
        return False

    async def _settle_stage_notice(
        self, notice_id: Any, stage: dict[str, Any], *, delivered: bool,
        detail: str = "", delivery: dict[str, Any] | None = None,
    ) -> bool:
        """Settle one notice, with the wait or the exhaustion worked out here.

        The store counts the attempt and stamps the next one; the curve is decided
        in one place so every path that fails a notice waits the same amount. The
        attempt cap is the one thing here that is not about waiting: past it the
        notice is settled as owed-and-abandoned, which stops the retry and leaves
        the reason in the row.
        """
        setter = getattr(self.memory, "mark_stage_notice", None)
        if setter is None or notice_id is None:
            return delivered
        attempts = int(stage.get("attempts") or 0) + 1
        cfg = self.config.social
        payload = {"detail": detail, **(delivery or {})}
        if delivered:
            try:
                await setter(notice_id, delivered=True, delivery=payload)
            except Exception:
                logger.warning("life.stage_notice_settle_failed")
                return False
            return True
        if attempts >= int(cfg.stage_notice_max_attempts):
            payload["abandoned"] = (
                f"gave up after {attempts} attempts; the person was never told"
            )
            logger.warning(
                "life.stage_notice_abandoned", attempts=attempts,
                stage=str(stage.get("stage") or ""), detail=detail[:200],
            )
            try:
                await setter(notice_id, delivered=True, delivery=payload)
            except Exception:
                logger.warning("life.stage_notice_settle_failed")
            return False
        wait_s = backoff_seconds(
            attempts - 1,
            base_s=float(cfg.stage_notice_backoff_s),
            max_s=float(cfg.stage_notice_backoff_max_s),
            jitter=float(cfg.stage_notice_backoff_jitter),
        )
        try:
            await setter(
                notice_id, delivered=False, delivery=payload,
                next_attempt_at=datetime.now(UTC) + timedelta(seconds=wait_s),
            )
        except Exception:
            logger.warning("life.stage_notice_defer_failed")
        return False

    async def _record_outbound_notice_turn(self, stage: dict[str, Any], text: str) -> None:
        """Keep the message in the agent's own thread, as any other send is.

        Without this the agent's record of the conversation and the transcript
        disagree about whether it said the thing — which is the same class of gap
        as a sent message the agent cannot see, and produces the identical
        symptom: a reply asserting something was never done.
        """
        await self._append_turn(
            TurnKind.outbound.value,
            f"told {stage.get('person_id') or 'someone'} "
            f"'{str(stage.get('title') or '')[:80]}' reached {stage.get('stage')}",
            detail={"text": text, "person_id": stage.get("person_id"),
                    "channel": stage.get("channel"),
                    "intention_id": stage.get("intention_id")},
            conversation_key=stage.get("conversation_key"),
        )

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

    async def _hold_undecided(self, message: Any, decision: Any) -> dict[str, Any] | None:
        """Keeps a message nobody managed to decide about, for a later attempt.

        No model answered, so the person asked a question that was never heard
        and the turn was closed as though it had been answered with a considered
        silence. It was not: the agent did not choose this and it is not a
        choice it can be asked to defend. So the message is counted, written down
        with its reason, and left on the queue -- where a model that has recovered
        will pick it up on a later cycle and the person gets their answer without
        having asked twice.

        The asking is unbounded by default and spaced instead of stopped: the
        first few attempts are immediate, the rest move onto the escalation
        curve, and a question the world is not ready to answer is retried once a
        day for as long as the agent runs. A deployment that wants the old
        behaviour sets `social.max_deliberation_attempts` /
        `social.max_transient_attempts` to positive numbers, and then the
        person is told when the retries end -- the notice below -- rather than
        being left with a "retrying..." that never comes true.
        """
        reason = decision.reason if hasattr(decision, "reason") else str(getattr(decision, "reason", ""))
        user_action = "(action needed" in (reason or "")
        payload = await self.deps.social.record_failure(message, {
            "action": decision.action.value,
            "reason": decision.reason,
            "decided": False,
            "needs_user_action": user_action,
        })
        # The person is told when the retries end, not only while they run.
        # The last attempt used to publish the same "retrying..." line as
        # every earlier one, and then the row silently stopped matching the
        # queue's attempt cap -- a question that read as "still trying"
        # forever, to someone who was owed "I could not". The caps are the
        # same ones `pending_inbound` enforces, read through the same helper
        # so the two can never disagree about which attempt is the last.
        # With no cap configured (`NO_CAP`) there is no last attempt and no
        # notice: the queue keeps its promise instead of explaining why it
        # will not keep it.
        if isinstance(payload, dict) and self.bus is not None:
            attempts = int(payload.get("attempts") or 0)
            needs_action = bool(payload.get("needs_user_action"))
            ua_cap, tr_cap = effective_attempt_caps(self.config)
            cap = ua_cap if needs_action else tr_cap
            if cap < NO_CAP and attempts >= cap:
                payload["_final"] = True
                try:
                    await self.bus.publish(EventKind.MESSAGE_HANDLED, {
                        "message_id": str(message.id),
                        "person_id": message.person_id,
                        "channel": message.channel,
                        "action": decision.action.value,
                        "reason": reason[:200],
                        "decided": False,
                        "needs_user_action": needs_action,
                        "retrying": False,
                        "final": True,
                        "error_kind": "gave_up_after_retries",
                        "status_text": (
                            f"Could not answer after {attempts} attempts: {reason[:150]}. "
                            + ("This needs your action (API key or quota)."
                               if needs_action else
                               "Please resend when convenient and I will try again.")
                        ),
                    })
                except Exception:
                    logger.exception("life.final_attempt_publish_failed")
        return payload

    async def _record_thought(self, thought: str, *, importance: float, half_life_h: float,
                                thought_turn: bool = True, **turn_kwargs: Any) -> None:
        """A thought everywhere it is kept: the process list, the episode log, the turn history.

        Three paths think something worth keeping — acting, reverie, wandering —
        and all three keep it in the same three places. The weight is the
        caller's, because a considered-but-declined subject is worth less than
        one taken on; whether it also becomes a turn is too, because a reverie
        that committed nothing has nothing to continue.
        """
        self._thoughts.append(thought)
        await self.memory.record_episode(
            kind="thought", summary=thought, importance=importance, half_life_h=half_life_h,
        )
        if thought_turn:
            await self._append_turn(TurnKind.thought.value, thought, **turn_kwargs)

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

        `needs_user_action` tells the surface whether to show anything at all:
        transient outages (429/503/timeout) are retried quietly and stay a
        background `retrying` state; only key/limit failures surface prominently.

        `status_text`, `wait_s` and `error_kind` tell the surface *how* it is
        retrying, so a screen can name the pause: a 429 waits an extended
        period ("Rate limited (429) -- waiting about 90s before retrying..."),
        while any other transient failure retries immediately
        ("Retrying immediately..."). Without these a wait reads as silence,
        and silence reads as the agent having stopped midway.
        """
        if self.bus is None:
            return
        try:
            reason = (decision.ignore_reason or decision.reason or "")[:200]
            decided = getattr(decision, "decided", True)
            full_reason = str(getattr(decision, "reason", "") or "")
            user_action = "(action needed" in full_reason
            retrying = (not decided and not user_action)
            lowered = full_reason.lower()
            rate_limited = "429" in full_reason or "rate limit" in lowered or "rate-limit" in lowered
            wait_s = _retry_wait_s(full_reason)
            if not decided and user_action:
                status_text = f"Action needed: {reason}"
                error_kind = "needs_user_action"
            elif not decided and rate_limited:
                status_text = (
                    f"Rate limited (429) -- waiting {_format_retry_wait(wait_s)} "
                    f"before retrying... ({reason})"
                )
                error_kind = "rate_limited_waiting"
            elif not decided:
                status_text = f"Retrying immediately... ({reason})"
                error_kind = "retrying_immediately"
            else:
                status_text = reason
                error_kind = "declined" if decided else "unanswered"
            await self.bus.publish(EventKind.MESSAGE_HANDLED, {
                "message_id": str(message.id),
                "person_id": message.person_id,
                "channel": message.channel,
                "action": decision.action.value,
                "reason": reason,
                "decided": decided,
                "needs_user_action": user_action,
                "retrying": retrying,
                "rate_limited": rate_limited,
                "wait_s": wait_s,
                "status_text": status_text,
                "error_kind": error_kind,
                "waiting": retrying and rate_limited,
                "immediate": retrying and not rate_limited,
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
            recent_thoughts=_tail(self._thoughts, window),
            energy=self.deps.rhythm.energy(self.spent_discretionary_session),
        )
        if result.thought:
            # Kept as an episode whether or not anything was taken on: "I
            # considered this and did not want it" is a real observation about
            # what the agent is drawn to, and at a low half-life it costs
            # nothing to keep a running record of.
            await self._record_thought(
                result.thought,
                importance=0.3 if result.committed_id else 0.2,
                half_life_h=12 if result.committed_id else 6,
                thought_turn=result.committed_id is not None,
                focus=result.subject.subject,
            )
        if result.committed_id is None:
            return result

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

    async def _mind_wander(self) -> float:
        memories = await self.memory.random_memories(self.config.rhythm.mind_wandering.sample_memories)
        questions = await self.memory.open_questions(limit=3)
        fragments = [m.summary for m in memories[:self.config.rhythm.mind_wandering.sample_memories]]
        fragments += [f"open question: {q['text']}" for q in questions]
        glance = " ; ".join(fragments)[:500]
        return await self._idle_model_call(context=glance, purpose="life.mind_wander")

    async def _idle_model_call(self, context: str = "", purpose: str = "life.idle_model") -> float:
        """Always call the model on an idle cycle.

        Gated only by the gateway itself: budget and rate limits raise there,
        are recorded as ``limit_reached`` stops, and the cycle goes on. There
        is no energy, due-clock, or focus gate in front of this call.
        """
        gateway = self.deps.gateway
        if gateway is None and self.reverie is not None:
            gateway = getattr(self.reverie, "gateway", None)
        if gateway is None:
            logger.warning("life.idle_model_no_gateway")
            return 0.0
        recent = _tail(self._thoughts, 5)
        prompt_context = "\n".join(
            part for part in [
                f"focus: {self.focus.title}" if self.focus else "focus: none",
                context,
                "recent thoughts:\n" + "\n".join(recent) if recent else "",
            ] if part
        )[:2000]
        request = ModelRequest(
            tier=Tier.T1,
            purpose=purpose,
            budget_bucket="discretionary",
            consequence_class="routine",
            messages=[
                ModelMessage(role=Role.system, content=(
                    "You are idle with nothing owed to anybody. "
                    "Answer with one short thought, observation, or next step "
                    "in plain text. Do not call tools and do not commit to work."
                )),
                ModelMessage(role=Role.user, content=prompt_context or "Nothing pending."),
            ],
            max_tokens=self.config.rhythm.reverie.max_tokens,
        )
        try:
            response = await gateway.complete(request)
        except Exception as exc:
            from clio.engine.stops import disposition_from_exception

            kind = disposition_from_exception(exc)
            logger.warning("life.idle_model_failed", error=str(exc)[:200], kind=kind.value)
            self.stops.record(kind=kind, subject="idle_model", reason=str(exc)[:300], what="model_call")
            return 0.0
        cost = float(getattr(response, "cost_usd", 0.0) or 0.0)
        text = " ".join(str(getattr(response, "text", "") or "").split())[:500]
        await self._record_thought(
            f"idle-model: {text}" if text else "idle-model: (no text)",
            importance=0.2, half_life_h=6,
            focus=self.focus.title if self.focus else None,
        )
        return cost

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
            # Which project holds the execution focus, and when its quantum
            # began. Without these a restart re-opens the preemption lock and
            # the rotation treats two projects as equally idle, which is how
            # multi-project work thrashes.
            "projects": {
                "active_project_id": self.active_project_id,
                "project_focus_started_at": self.project_focus_started_at,
            },
            # Continuity is state, not transcript [P3]: the thread as written,
            # plus the newest raw turns so a restart resumes mid-thought rather
            # than mid-sentence.
            "context": self.context.snapshot(),
            # NA-P3: the stops ledger survives a restart, because an ending
            # whose record the restart erased is an ending nobody can be told
            # about afterwards.
            "stops": self.stops.snapshot(),
            "updated_at": datetime.now(UTC).isoformat(),
        })
        metrics.observe_cycle(self.deps.thread_id, self.states.state.value, duration_ms / 1000.0)
        await self._publish_presence()
        await self._publish_budget()

    async def _publish_presence(self) -> None:
        # The heartbeat file first, unconditionally: the supervisor reads it
        # (and nothing else of ours) to tell a wedged core from a busy one, and
        # a presence publish that could not reach the bus is exactly the moment
        # that distinction matters most. The name matches
        # `CORE_HEARTBEAT_FILE` in `clio.core.supervisor` — "heartbeat".
        try:
            heartbeat = self.config.paths.run_dir / "heartbeat"
            heartbeat.parent.mkdir(parents=True, exist_ok=True)
            heartbeat.touch()
        except OSError:
            pass
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
        settled = self._settled_focus(decision, outcome)
        if settled is None and self._suspended_focus is not None:
            # Work suspended for an interruption is owed the pass back,
            # whatever kind of cycle the interruption turned out to be. The
            # social branch used to be the only reader of the suspended slot,
            # so an interrupt whose cycle answered nobody -- a timer fired
            # into an empty queue, a percept that broke into work and then
            # dissolved -- ended its turn with `None`, and the owed work fell
            # out of focus with its promise still open. Every path that
            # settles on nothing now hands the pass back rather than dropping
            # it on the floor.
            settled = self._pop_suspended()
        return settled

    def _settled_focus(self, decision: Decision, outcome: Outcome) -> Focus | None:
        if decision.kind == "advance_goal" and outcome.results:
            result = outcome.results[0]
            result_outcome = result.get("outcome")
            if result_outcome in (SolverOutcome.goal_done.value, SolverOutcome.parked.value):
                return None
            return self.focus
        if decision.kind in ("none", "idle_model"):
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
            if focus is not None:
                # A new promise takes the pass. The work this turn suspended
                # for the message stays open and the drives can still find it,
                # but it does not resume ahead of the newer promise: the slot
                # is one deep on purpose, and a stale entry left here would
                # resurface after the new work's own social turns and steal
                # the pass from work that is still owed.
                #
                # Two corrections to that, both from the same live failure
                # (2026-11-08): a one-deep slot silently discarded the
                # suspended goal — `in_progress` in the store, no focus, no
                # attempt closure, nothing that would ever resume it — when a
                # follow-up message commissioned new work mid-attempt. And a
                # message that sequences the new work *after* the suspended
                # work ("once that is finished, review X") was treated as an
                # immediate preemption, discarding the very work it was
                # sequenced behind.
                #
                # So: the suspended stack is preserved either way (the newer
                # promise's own settlement hands the pass back down the
                # stack), and a sequenced commission does not take the pass
                # at all until the work it follows has settled — it is
                # recorded as a `depends_on` edge at commission time, and
                # this turn returns the suspended focus so the prior work
                # resumes on the following tick.
                commission = outcome.commission or {}
                if commission.get("sequenced") and self._suspended_focus is not None:
                    logger.info(
                        "life.sequenced_commission_queued",
                        commission=str(focus.intention_id),
                        after=str(self._suspended_focus.intention_id),
                    )
                    return self._pop_suspended()
                return focus
            # No new work was commissioned: the pass returns to what the social
            # turn interrupted, so immediate replies do not dissolve the
            # focus. The suspension was bookmarked when it was made -- in
            # `decide` for a message that arrived mid-work, in `_cycle` for an
            # attention interrupt -- so resuming here only continues it; a
            # vanished intention degrades in `_advance_goal`. The handback
            # itself is the net in `next_focus`, so an interrupt whose cycle
            # answers nobody reaches it too.
            return None
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
        done = self._consolidation_done_tonight
        if done is None:
            last = await self.memory.last_consolidation_at()
            if last is not None:
                done = self.deps.rhythm.local_time(last).date().isoformat()
        if not self.deps.rhythm.due_daily(
            self.config.memory.consolidation.run_at, done,
        ):
            self._consolidation_done_tonight = done
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
                # `wait_wake` rather than `wait_resume`, because a hold can also
                # be *raised* — a stop arriving while the agent is parked here —
                # and a waiter parked on the resume event alone would sit out its
                # whole heartbeat before noticing, which is the length of time
                # this whole method exists to prevent.
                await asyncio.wait_for(self.control.wait_wake(), timeout=PARKED_HEARTBEAT_S)
            except TimeoutError:
                pass
            # The flags are read again on both paths, not just the woken one: a
            # resume can come from a file rather than from a surface, and a file
            # announces itself by being absent. `continue`-ing on the timeout — as
            # this did — meant the absence was never looked at, so a hold released
            # by removing a file could only be released by a restart.
            await self.deps.control.refresh()

    # --------------------------------------------------------------- life

    async def _parked_stopped(self) -> None:
        """Waits out a stop without going deaf, and without ending.

        The same shape as `_parked`, and for the same reason: a loop that goes
        quiet for as long as the hold lasts is indistinguishable from a dead
        agent, so every pass still says the agent is here. The difference is what
        it waits for — a resume, rather than a resume *and* the absence of a stop,
        because a stop is now a hold the owner can take back from the same button
        that set it.

        Nothing is left undone while parked, which is the point: the cycle that
        was in flight when the stop arrived was cut short, so the pass the stop
        interrupted ends where it was interrupted rather than being repeated from
        the top.
        """
        while self.control.stopped and not self.control.ending:
            await self._publish_presence()
            try:
                await asyncio.wait_for(self.control.wait_wake(), timeout=PARKED_HEARTBEAT_S)
            except TimeoutError:
                pass
            await self.deps.control.refresh()

    async def _cycle_until_stopped(self) -> None:
        """One cycle, cut short by a stop that arrives while it is running.

        The flags were only ever read at the top of the loop, so a stop pressed
        while a cycle was in flight took effect whenever that cycle ended — and a
        cycle is a whole deliberation, with model calls that can each retry six
        times against a rate-limited provider. Thirty minutes of a Stop button
        doing nothing is not a slow agent, it is a switch that is only wired to
        the top of the loop; the cycle is a task here so the loop can stop waiting
        on it.

        Cancelling mid-call is what a Ctrl-C does to the same code, and the tools
        that could be interrupted journal themselves before they act, so a stop
        lands between steps rather than inside one that had already happened.
        """
        cycle = asyncio.ensure_future(self._cycle())
        asked = asyncio.ensure_future(self.control.wait_stop())
        try:
            done, _pending = await asyncio.wait(
                {cycle, asked}, return_when=asyncio.FIRST_COMPLETED,
            )
            if cycle in done:
                await cycle
            else:
                cycle.cancel()
                try:
                    await cycle
                except asyncio.CancelledError:
                    pass
                except Exception:
                    # The stop has already won the race, so the cycle's result
                    # is discarded either way — but *why* it failed is not. A
                    # stop that lands on a crashing cycle used to swallow the
                    # trace outright, which read as a clean stop in every log
                    # while the same exception returned on the next one.
                    logger.exception("life.cycle_interrupted_failed")
        finally:
            asked.cancel()

    async def life(self) -> None:
        # Waking is the one step the cycle handler cannot catch for us, and an
        # exception from it used to end the process outright — a database that
        # was still coming up took the core down before the loop it protects
        # had run even once. The supervisor would have restarted it; in here
        # it is cheaper and kinder: retry on the same backoff the rest of the
        # loop already trusts, and leave only by a signal.
        while True:
            try:
                await self.wake_up()
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("life.wake_failed")
                self.stops.record(
                    kind=StopKind.error,
                    subject="wake",
                    reason="waking the agent raised; retrying on a backoff",
                    what="wake",
                )
                if self.control.ending:
                    return
                await asyncio.sleep(60.0)
        while True:
            # The files before the flags: a stop written by the host has to be
            # what *this* pass reads, not what the next start reads. `load` does it
            # once, at startup, so a kill switch pressed after the process was
            # already running was only ever a note in a file nothing read again —
            # the one switch H1 calls always effective, effective only until
            # restart [H1].
            await self.deps.control.refresh()
            # Only a signal ends the loop. A stop from a surface parks the agent
            # instead, because the button that set it is the button that takes it
            # back: ending the process on every stop left the surface with a
            # Resume that could not resume (the process it resumed was gone, and
            # the supervisor's replacement had already cleared the flag) and a
            # Stop that undid itself a few seconds later.
            if self.control.ending:
                break
            if self.control.stopped:
                # NA-P3.1: a stop the owner asked for is a stop with a reason,
                # not an outage. Recorded once per episode, here where the
                # parking begins, so the ledger says the work was stopped by
                # the person rather than having failed.
                self.stops.record(
                    kind=StopKind.stopped,
                    subject=self.focus.title if self.focus else "",
                    reason="the owner stopped the agent",
                    what="control",
                )
                self.states.transition(State.PAUSED, "control stop")
                await self._parked_stopped()
                self.states.transition(State.RESUMING, "control resume")
                self.states.transition(State.THINKING_REFLECTING, "resumed")
                continue
            if self.control.paused:
                self.states.transition(State.PAUSED, "control pause")
                await self._parked()
                self.states.transition(State.RESUMING, "control resume")
                self.states.transition(State.THINKING_REFLECTING, "resumed")
                continue

            try:
                await self._cycle_until_stopped()
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
                # NA-P3.1: a cycle that died is recorded as one, with the error
                # named, so the ending is in the ledger the agent reads back --
                # a failure nobody wrote down is the silent drop NA-P2 exists
                # to remove.
                self.stops.record(
                    kind=StopKind.error,
                    subject=self.focus.title if self.focus else "cycle",
                    reason="the pass raised before it finished; see the log for the trace",
                    what="cycle",
                )
                await self._publish_presence()
                # The tick at the end of a good cycle is what keeps a failing
                # one from becoming a spin loop. A cycle that raises never
                # reaches it, so the pause is owed here instead.
                await self._wait_until_next()

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
            "projects": {
                "active_project_id": self.active_project_id,
                "project_focus_started_at": self.project_focus_started_at,
            },
            # The thread has to be here too. This snapshot replaces the last
            # per-cycle one, so leaving the context out makes shutting down the
            # one thing that reliably erases it -- the agent's final act on its
            # way out would be to forget everything it had been doing.
            "context": self.context.snapshot(),
            "stops": self.stops.snapshot(),
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
        await self._drain_steering()
        try:
            await self._maybe_fast_lane()
        except Exception:
            logger.exception("life.fast_lane_failed")
        await self._maybe_reopen_parked()
        await self._maybe_park_zombies()
        percepts = await self.perceive()
        appraisal = await self.deps.attention.appraise(
            percepts, self.focus,
            # No novelty centroid yet: nothing fills a recent-thought vector
            # list on this path, so the salience math runs on the focus
            # comparison alone until one does.
            recent_centroid=None,
            thresholds_by_state=self.config.rhythm.attention.interrupt_thresholds,
            state_name=self.states.state.value,
            active_project_id=self.active_project_id,
            project_lock_active=await self._project_lock_active(),
        )
        previous_focus = self.focus
        if appraisal.interrupt:
            # An interrupt takes this pass from whatever the agent was holding,
            # and the work it takes it from has to survive the taking the same
            # way it survives the social turn `decide` suspends it for: named in
            # `_suspended_focus`, so `next_focus` can hand the pass back once
            # the interruption has been dealt with.
            #
            # Without this, the interrupted focus was bookmarked and then
            # dropped. The bookmark kept the intention's own resume state, but
            # nothing anywhere was holding the agent to it: `decide` only
            # suspends a focus that is not already social, and the interrupt
            # has just made it social, so the one mechanism that carried owed
            # work across a turn never ran. Whether the promise was ever seen
            # again was left to the drives to notice an undated intention --
            # the exact lottery the commission focus exists to take the work
            # out of.
            interrupted = self.focus
            owed = self._goal_is_owed()
            await self.bookmark(interrupted)
            if owed:
                self._push_suspended(interrupted)
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
        # The fair rotation, checked at the seam where a focus quantum can end:
        # a project that has held the pass for its full interval (or a full
        # 90-minute session) may hand it to the best-scoring project waiting
        # behind it — through the switch protocol, never by silent reassignment.
        try:
            await self._maybe_rotate_project_focus()
        except Exception:
            logger.exception("life.project_rotation_failed")

        if self.focus is None and self.states.state != State.WAITING:
            self.states.transition(State.WAITING, "no focus")
        elif self.focus is not None and self.states.state == State.WAITING:
            self.states.transition(State.THINKING_REFLECTING, "focus acquired")

        await self.deps.rhythm.after_cycle(outcome.model_dump(mode="json"))
        await self._wait_until_next()

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


def _retry_wait_s(reason: str) -> float | None:
    """The wait named in a retry reason ("about 90s", "about 1h"), if it names one."""
    import re

    match = re.search(r"about\s+(\d+(?:\.\d+)?)\s*([smhd])", reason or "")
    if not match:
        return None
    try:
        value = float(match.group(1))
    except ValueError:
        return None
    unit = match.group(2)
    return value * {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[unit]


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
