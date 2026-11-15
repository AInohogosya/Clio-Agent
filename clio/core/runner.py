from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
from datetime import UTC, datetime
from typing import Any

from clio.bus import EventBus
from clio.comms.host import CommsHost, inbound_to_bus
from clio.config import ClioConfig, load_config
from clio.core.control import ControlPlane
from clio.core.scheduler import Scheduler
from clio.core.seed import seed
from clio.db import Database
from clio.engine.attention import Attention
from clio.engine.drives import Motivation
from clio.engine.life import Life, LifeDeps
from clio.engine.reverie import Reverie
from clio.engine.rhythm import RhythmController
from clio.env import env
from clio.gateway.client import DirectGateway
from clio.gateway.embeddings import build_role_embedder
from clio.gateway.secrets import SecretVault
from clio.gateway.service import GatewayService
from clio.observability import metrics
from clio.observability.audit import AuditLog, DbAuditSink
from clio.observability.logger import configure_logging, get_logger
from clio.observability.tracing import setup_tracing
from clio.schemas.events import EventKind, Percept
from clio.schemas.models import Tier
from clio.social.deliberation import SocialCognition
from clio.social.etiquette import OutboundPipeline
from clio.social.relationships import RelationshipStore
from clio.threads.manager import ThreadManager
from clio.toolhost.server import Toolhost

logger = get_logger("clio.core.runner")

BUS_PERCEPT_KINDS = {
    EventKind.INBOUND_MSG, EventKind.TIMER_FIRED, EventKind.THREAD_RESULT,
    EventKind.GUARDIAN_UNDO, EventKind.CONTROL_UPDATE, EventKind.PERCEPT,
}

# Out-of-band kinds: handled by a dedicated subscriber at dispatch time and
# never turned into percepts. The three lanes the architecture names move
# outside the normal flow — a steering message that sat in the percept queue
# until the run finished would be guidance arriving after the work it was for.
OUT_OF_BAND_KINDS = {EventKind.STEERING_INJECT}


async def connect_database(config: ClioConfig) -> Database:
    db = await Database.connect(
        config.database.dsn,
        min_pool=config.database.min_pool,
        max_pool=config.database.max_pool,
        command_timeout_s=config.database.command_timeout_s,
        connect_timeout_s=config.database.connect_timeout_s,
    )
    logger.info("core.db_connected")
    return db


class CoreRuntime:
    def __init__(self, config: ClioConfig, db: Database, *, listen_channels: bool = False):
        self.config = config
        # Whether this process is the one that holds the channel doors open.
        #
        # False in the seven-process model, because `clio-comms` is, and true in
        # the single-process development run, where there is no `clio-comms` at
        # all. The default is False because the mistake it prevents is silent and
        # the other way round: a core that listened while a comms process was
        # already listening would put two `getUpdates` long-polls on one bot, and
        # Telegram answers the second with a conflict for as long as the first is
        # alive — a channel that looks configured and receives nothing. The
        # reverse mistake is loud and obvious instead: a single-process run that
        # nobody asked to listen hears nothing, and the person who wanted to try
        # Telegram from their phone says so.
        self.listen_channels = listen_channels
        self.db = db
        self.bus = EventBus(db, source="core")
        self.audit = AuditLog(DbAuditSink(db))
        # Asked of one function which processes load a model and which borrow the
        # gateway's. `core` loads its own: it embeds a focus title and a percept
        # summary on every cycle, and a loopback round trip for that is a cost
        # paid on the agent's critical path to save a session it then mostly sits
        # on. Everything that only needs a vector occasionally does not.
        self.embedder = build_role_embedder("core", config)
        from clio.memory.store import MemoryStore

        self.memory = MemoryStore(db, self.embedder, config, bus=self.bus)
        from clio.memory.self_model import SelfModel

        self.self_model = SelfModel(db, bus=self.bus)
        self.gateway_service = GatewayService(config, db=db, embedder=self.embedder)
        self.gateway = DirectGateway(self.gateway_service)
        self.vault = SecretVault(
            keyfile=config.paths.data_dir / "keys" / "age.key",
            store_path=config.paths.data_dir / "secrets.age",
            allowed_domains=config.permissions.secrets.allowed_domains,
        )
        self.control = ControlPlane(db, config.paths.run_dir, bus=self.bus)
        self.scheduler = Scheduler(config, db, bus=self.bus)

        from clio.guardian.gate import GuardianGate, ModelDueProcessVerifier
        from clio.guardian.journal import ActionJournal
        from clio.guardian.registry import ArtifactRegistry
        from clio.guardian.snapshots import SnapshotManager
        from clio.guardian.trash import TrashStore

        self.registry = ArtifactRegistry(db)
        self.trash = TrashStore(
            config.paths.trash, db=db,
            fast_retention_days=config.permissions.guardian.fast_path_retention_days,
            due_retention_days=config.permissions.guardian.due_process_retention_days,
        )
        self.snapshots = SnapshotManager(config, db=db)
        self.journal = ActionJournal(db=db)
        self.verifier = ModelDueProcessVerifier(self.gateway, config.self_name)
        from clio.guardian.approval import ApprovalManager

        self.approvals = ApprovalManager(
            default_timeout_s=float(getattr(config.permissions, "approval_timeout_s", 300.0) or 300.0)
        )
        # One lease manager for the whole core process: project containers take
        # their isolated workspaces from it (`create_project`), the guardian
        # enforces each call against the project's lease, and evergreen
        # milestones commit through it.
        # The containment: every project's lease root is provisioned inside a
        # sandbox (`clio/sandbox/provider.py`), one sandbox per project,
        # mounted at /workspace. The provider is selected by capability — the
        # strongest runtime this machine actually has, ending at the
        # filesystem baseline — and a deployment that names a backend it does
        # not have is refused loudly rather than silently degraded.
        from clio.sandbox.provider import SandboxManager, select_provider
        from clio.threads.context import WorkspaceLeaseManager

        self.sandbox_provider = None
        self.sandbox_manager = None
        try:
            self.sandbox_provider = select_provider(config)
            self.sandbox_manager = SandboxManager(db, self.sandbox_provider)
        except Exception:
            logger.exception("core.sandbox_provider_unavailable")
        self.leases = WorkspaceLeaseManager(
            config, snapshots=self.snapshots, trash=self.trash,
            sandbox_manager=self.sandbox_manager, db=db,
        )
        # Live mid-turn steering (clio/gsl/steering.py): the queue the solver
        # loop claims at step boundaries, and the out-of-band lane inbound
        # messages ride when they belong to the active project's conversation.
        from clio.gsl.steering import SteeringQueue

        self.steering = SteeringQueue(db, bus=self.bus)
        from clio.guardian.policy import PolicyEngine

        self.policy_engine = PolicyEngine(db, listener=db)
        self.guardian = GuardianGate(
            config, registry=self.registry, trash=self.trash, snapshots=self.snapshots,
            audit=self.audit, verifier=self.verifier, control=self.control,
            approvals=self.approvals, leases=self.leases, policy_engine=self.policy_engine,
        )

        self.relationships = RelationshipStore(config, db=db)
        self.social = SocialCognition(
            config, gateway=self.gateway, relationships=self.relationships,
            audit=self.audit, self_model=self.self_model, db=db,
        )
        self.comms = CommsHost(config, on_inbound=inbound_to_bus(self.bus, self.social))
        self.comms_out = OutboundPipeline(
            config, db=db, bus=self.bus, audit=self.audit,
            relationships=self.relationships, sender=self.comms.send,
        )
        self.threads = ThreadManager(
            config, self.memory, bus=self.bus, audit=self.audit, gateway=self.gateway,
            self_model=self.self_model,
        )
        from clio.subagents import SubagentRegistry

        self.subagents = SubagentRegistry(config, gateway=self.gateway)
        self.toolhost = Toolhost(
            config, gate=self.guardian, journal=self.journal, bus=self.bus,
            services={
                "memory": self.memory,
                "vault": self.vault,
                "registry": self.registry,
                "trash": self.trash,
                "threads": self.threads,
                "scheduler": self.scheduler,
                "comms_send": self.comms_out.send,
                "subagents": self.subagents,
                "gateway": self.gateway,
            },
        )
        # Wired after construction: the toolhost needs the thread manager as a
        # service, and the thread worker needs the toolhost to execute steps.
        self.threads.toolhost = self.toolhost
        self.attention = Attention(config, embedder=self.embedder, gateway=self.gateway,
                                   self_model=self.self_model)
        self.motivation = Motivation(config)
        self.rhythm = RhythmController(config)
        self.reverie = Reverie(
            config, gateway=self.gateway, memory=self.memory,
            self_model=self.self_model, audit=self.audit,
        )

        from clio.memory.context import ContextLayer

        self.context = ContextLayer(
            config, memory=self.memory, gateway=self.gateway, thread_id="self",
        )
        from clio.gsl.verification import VerificationLadder

        self.verification = VerificationLadder(
            config, self.gateway, self.threads, comms_out=self.comms_out, audit=self.audit,
        )
        from clio.gsl.loop import GeneralSolverLoop

        self.gsl = GeneralSolverLoop(
            config, self.memory, self.gateway, self.toolhost, self.verification,
            audit=self.audit, context=self.context, thread_id="self",
            # The planner asks the rhythm controller how long to wait between
            # failed attempts rather than keeping its own idea of pacing, so the
            # backoff that stops a frozen goal is decided in the same place as
            # every other decision about when this agent does something.
            rhythm=self.rhythm,
            # Live mid-turn steering: claimed at every step boundary (below).
            steering=self.steering,
            # Evergreen milestones commit the project's workspace lease through
            # this manager; without it a milestone would ship without the work.
            leases=self.leases,
        )
        from clio.memory.continuity import ContinuityManager

        self.continuity = ContinuityManager(config, db=db, bus=self.bus)
        from clio.memory.consolidate import ConsolidationDAG

        self.consolidation = ConsolidationDAG(self.memory, self.self_model, self.gateway, config, bus=self.bus)

        self.percept_queue: asyncio.Queue[Percept] = asyncio.Queue()
        self.deps = LifeDeps(
            config=config, control=self.control, bus=self.bus, memory=self.memory,
            self_model=self.self_model, attention=self.attention, motivation=self.motivation,
            rhythm=self.rhythm, gateway=self.gateway, gsl=self.gsl, social=self.social,
            comms_out=self.comms_out, toolhost=self.toolhost, audit=self.audit,
            continuity=self.continuity, consolidation=self.consolidation,
            context=self.context, embedder=self.embedder, thread_id="self",
            reverie=self.reverie, leases=self.leases,
        )
        self.life = Life(self.deps)
        self.life.attach_queue(self.percept_queue)
        self._audit_anchor_task: asyncio.Task | None = None
        self._digest_task: asyncio.Task | None = None
        self._channels_task: asyncio.Task | None = None

    def _wire_bus(self) -> None:
        async def on_event(event: Any) -> None:
            # Two questions about one event, asked separately, never as a chain.
            #
            # A lifecycle verb is both something the control plane must obey and
            # something the agent notices: attention counts `control.update` as
            # interruptible, so it belongs in both sets. Chained as `if/elif`, the
            # percept branch took it and the second was unreachable — so a `stop`,
            # `pause` or `emergency_stop` pressed on any surface was written to the
            # control table and then dropped on the floor, the control plane never
            # heard of it, and the agent carried on exactly as before. Oversight
            # that reads as obeyed and is not [H1]. The same shadowing swallowed
            # `guardian.undo`, so an undo asked for on the interface never ran.
            if event.kind == EventKind.CONTROL_UPDATE:
                await self.control.apply_command(
                    event.payload.get("action", ""), event.payload.get("by", "bus"),
                )
            elif event.kind == EventKind.GUARDIAN_UNDO:
                await self._handle_undo(event.payload)
            elif event.kind == EventKind.STEERING_INJECT:
                await self._handle_steering(event)
            if event.kind in BUS_PERCEPT_KINDS and event.kind not in OUT_OF_BAND_KINDS:
                from clio.schemas.events import percept_from_event

                await self.percept_queue.put(percept_from_event(event))

        self.bus.subscribe(None, on_event)

    async def _handle_undo(self, payload: dict[str, Any]) -> None:

        action_id = payload.get("action_id") or payload.get("undo_ref")
        requested_by = payload.get("requested_by", "dashboard")
        if not action_id:
            return
        try:
            entry = await self.trash.get(str(action_id))
            if entry is not None:
                restored = await self.trash.restore(str(action_id))
                await self.audit.append(
                    actor=requested_by, category="guardian",
                    summary=f"restored {entry.origin} from trash",
                    details={"entry": action_id, "restored_to": str(restored)},
                )
                await self.bus.publish(EventKind.GUARDIAN_UNDO, {
                    "done": True, "restored": str(restored),
                })
                return
            record = await self.journal.get(str(action_id))
            if record is not None:
                undo_ref = record.get("undo_ref")
                if undo_ref:
                    entry = await self.trash.get(str(undo_ref))
                    if entry is not None:
                        restored = await self.trash.restore(str(undo_ref))
                        await self.audit.append(
                            actor=requested_by, category="guardian",
                            summary=f"undo of action {action_id}",
                            details={"restored_to": str(restored)},
                        )
                        return
                await self.audit.append(
                    actor=requested_by, category="guardian",
                    summary=f"undo requested for action {action_id}: no restorable reference",
                    details={"action_id": action_id},
                )
        except Exception:
            logger.exception("core.undo_failed")

    async def _handle_steering(self, event: Any) -> None:
        """A `steering.inject` event that did not already become a row.

        The queue's own `enqueue` publishes the event *after* inserting the
        row, so an event whose payload already names its `steering_id` is an
        echo of work done and needs nothing. An event without one came from a
        surface that published directly to the bus — the bridge, a channel
        adapter, the API — and the row it describes does not exist until this
        writes it. Either way the message reaches the step boundary; only the
        row's author differs.
        """
        from clio.gsl.steering import steering_from_event

        payload = steering_from_event(event)
        if payload is None or payload.get("steering_id"):
            return
        try:
            await self.steering.enqueue(
                str(payload["project_id"]),
                str(payload.get("conversation_key") or "direct"),
                str(payload.get("person_id") or "unknown"),
                str(payload["text"]),
                intention_id=payload.get("intention_id"),
                thread_id=payload.get("thread_id"),
                priority=str(payload.get("priority") or "normal"),
            )
        except Exception:
            logger.exception("core.steering_enqueuer_failed")

    async def _audit_anchor_loop(self) -> None:
        import json

        while True:
            await asyncio.sleep(600)
            with contextlib.suppress(Exception):
                state = await self.audit.anchor_state()
                await self.bus.publish("audit.anchor", state)
                anchor_path = self.config.paths.data_dir / "anchor.json"
                tmp = anchor_path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(state, fh, sort_keys=True)
                    fh.flush()
                tmp.replace(anchor_path)
                logger.info("core.audit_anchored", seq=state.get("seq"))

    async def _digest_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            with contextlib.suppress(Exception):
                await self.comms_out.flush_due_digests()

    async def start(self) -> None:
        config = self.config
        config.paths.ensure()
        # Before anything that thinks: a catalogue entry whose loopback endpoint
        # is not there would otherwise sit in every failover ladder as a
        # candidate that can only answer 404, and a 404 is the one failure the
        # ladder is not allowed to route around.
        await self.gateway_service.probe_local_endpoints()
        await self.bus.start()
        await self.control.load()
        # Layer 1 policies: loaded before the first tool call can arrive, and
        # reloaded live over LISTEN/NOTIFY when an operator edits the table.
        with contextlib.suppress(Exception):
            await self.guardian.policy_engine.start() if self.guardian.policy_engine else None
        self._wire_bus()
        self.control.install_signal_handlers()
        await self.scheduler.start()
        # Send-only in the seven-process model, where `clio-comms` is the process
        # that holds the doors open. The core's own copy of each adapter exists so a
        # reply can leave on the channel its question arrived on — including a
        # channel this process never listens to.
        #
        # The one-process development run has no `clio-comms` to do it, so it is
        # told to. See `CoreRunner.listen_channels` for why that is a flag and not a
        # guess: the failure of guessing wrong in either direction is a channel
        # that silently receives nothing, and the two directions do not fail alike.
        await self.comms.start(listen=self.listen_channels)
        # The core's copy of each door follows the configuration too, and for a
        # different reason than `clio-comms` does. This process never listens, so
        # there is no poller to reopen here; what it needs is the ability to *send*
        # on a door that was opened since it started. Without this, a person who
        # pasted a token and pressed Save would get their message received by
        # `clio-comms` and then find the agent unable to answer on the channel it
        # arrived on — a reply that fails only once the conversation is already
        # under way, which is the worst moment for it to fail.
        self._channels_task = asyncio.create_task(self.comms.watch_channels())
        self._audit_anchor_task = asyncio.create_task(self._audit_anchor_loop())
        self._digest_task = asyncio.create_task(self._digest_flush_loop())
        metrics.start_metrics_server(9710)
        logger.info("core.started", tier_floor=Tier.T3.value)

    async def stop(self) -> None:
        for task in (self._audit_anchor_task, self._digest_task, self._channels_task):
            if task is not None:
                task.cancel()
        await self.scheduler.stop()
        await self.comms.stop()
        await self.toolhost.aclose()
        await self.bus.stop()
        await self.db.close()

    async def run(self) -> None:
        await self.start()
        try:
            await self.life.run()
        finally:
            await self.stop()


async def run_core(config: ClioConfig | None = None, *, listen_channels: bool = False) -> None:
    config = config or load_config()
    # DEBUG by default: the structured stream should carry every event the
    # program emits, not just the ones worth a console line. The exhaustive
    # sink (observability.exhaustive) is independent of this level either way.
    configure_logging(level=env("LOG_LEVEL", "DEBUG"))
    setup_tracing("clio-core")
    config.paths.ensure()
    db = await connect_database(config)
    try:
        row = await db.fetchval("SELECT COUNT(*) FROM self_model")
        if int(row or 0) == 0:
            await seed(config, db)
        runtime = CoreRuntime(config, db, listen_channels=listen_channels)
        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig, lambda s=sig: asyncio.create_task(_shutdown(runtime, s)),
                )
        await runtime.run()
    finally:
        with contextlib.suppress(Exception):
            await db.close()


async def _shutdown(runtime: CoreRuntime, sig: signal.Signals) -> None:
    logger.info("core.signal_received", signal=sig.name)
    # `ending`, not just `stopped`: a signal ends the process, where a stop from a
    # surface parks the agent and waits to be resumed. `end` also raises the same
    # events `stop` does, so a cycle in flight is cut short here as well.
    runtime.control.end(f"signal:{sig.name}")
    with contextlib.suppress(Exception):
        await runtime.control.apply_command("stop", f"signal:{sig.name}")
    # The ending is recorded here, where the signal is, rather than left to the
    # life loop's exit. The loop notices a stop at the top of its next pass, which
    # may be a whole cycle away — mid tool call, mid model call — and whatever
    # supervises this process does not wait that long: it asks its children to
    # stop, gives them a grace period and then kills them. So the one moment the
    # agent knows for certain that it is ending was the moment its record of
    # having ended went unwritten, and for the length of the presence window
    # after that, every surface and every `clio status` read a machine with
    # nobody on it as an agent that was merely quiet.
    with contextlib.suppress(Exception):
        await runtime.continuity.save({
            "thread_id": "self",
            "state": "stopped",
            "stopped_at": datetime.now(UTC).isoformat(),
        })
