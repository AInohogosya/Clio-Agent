from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from datetime import UTC, datetime
from typing import Any

from ethos.bus import EventBus
from ethos.comms.host import CommsHost, inbound_to_bus
from ethos.config import EthosConfig, load_config
from ethos.core.control import ControlPlane
from ethos.core.scheduler import Scheduler
from ethos.core.seed import seed
from ethos.db import Database
from ethos.engine.attention import Attention
from ethos.engine.drives import Motivation
from ethos.engine.life import Life, LifeDeps
from ethos.engine.reverie import Reverie
from ethos.engine.rhythm import RhythmController
from ethos.gateway.client import DirectGateway
from ethos.gateway.embeddings import build_role_embedder
from ethos.gateway.secrets import SecretVault
from ethos.gateway.service import GatewayService
from ethos.observability import metrics
from ethos.observability.audit import AuditLog, DbAuditSink
from ethos.observability.logger import configure_logging, get_logger
from ethos.observability.tracing import setup_tracing
from ethos.schemas.events import EventKind, Percept
from ethos.schemas.models import Tier
from ethos.social.deliberation import SocialCognition
from ethos.social.etiquette import OutboundPipeline
from ethos.social.relationships import RelationshipStore
from ethos.threads.manager import ThreadManager
from ethos.toolhost.server import Toolhost

logger = get_logger("ethos.core.runner")

BUS_PERCEPT_KINDS = {
    EventKind.INBOUND_MSG, EventKind.TIMER_FIRED, EventKind.THREAD_RESULT,
    EventKind.GUARDIAN_UNDO, EventKind.CONTROL_UPDATE, EventKind.PERCEPT,
}


async def connect_database(config: EthosConfig) -> Database:
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
    def __init__(self, config: EthosConfig, db: Database, *, listen_channels: bool = False):
        self.config = config
        # Whether this process is the one that holds the channel doors open.
        #
        # False in the seven-process model, because `ethos-comms` is, and true in
        # the single-process development run, where there is no `ethos-comms` at
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
        from ethos.memory.store import MemoryStore

        self.memory = MemoryStore(db, self.embedder, config, bus=self.bus)
        from ethos.memory.self_model import SelfModel

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

        from ethos.guardian.gate import GuardianGate, ModelDueProcessVerifier
        from ethos.guardian.journal import ActionJournal
        from ethos.guardian.registry import ArtifactRegistry
        from ethos.guardian.snapshots import SnapshotManager
        from ethos.guardian.trash import TrashStore

        self.registry = ArtifactRegistry(db)
        self.trash = TrashStore(
            config.paths.trash, db=db,
            fast_retention_days=config.permissions.guardian.fast_path_retention_days,
            due_retention_days=config.permissions.guardian.due_process_retention_days,
        )
        self.snapshots = SnapshotManager(config, db=db)
        self.journal = ActionJournal(db=db)
        self.verifier = ModelDueProcessVerifier(self.gateway, config.self_name)
        self.guardian = GuardianGate(
            config, registry=self.registry, trash=self.trash, snapshots=self.snapshots,
            audit=self.audit, verifier=self.verifier, control=self.control,
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
        )
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
            },
        )
        self.attention = Attention(config, embedder=self.embedder, gateway=self.gateway,
                                   self_model=self.self_model)
        self.motivation = Motivation(config)
        self.rhythm = RhythmController(config)
        self.reverie = Reverie(
            config, gateway=self.gateway, memory=self.memory,
            self_model=self.self_model, audit=self.audit,
        )

        from ethos.memory.context import ContextLayer

        self.context = ContextLayer(
            config, memory=self.memory, gateway=self.gateway, thread_id="self",
        )
        from ethos.gsl.verification import VerificationLadder

        self.verification = VerificationLadder(
            config, self.gateway, self.threads, comms_out=self.comms_out, audit=self.audit,
        )
        from ethos.gsl.loop import GeneralSolverLoop

        self.gsl = GeneralSolverLoop(
            config, self.memory, self.gateway, self.toolhost, self.verification,
            audit=self.audit, context=self.context, thread_id="self",
            # The planner asks the rhythm controller how long to wait between
            # failed attempts rather than keeping its own idea of pacing, so the
            # backoff that stops a frozen goal is decided in the same place as
            # every other decision about when this agent does something.
            rhythm=self.rhythm,
        )
        from ethos.memory.continuity import ContinuityManager

        self.continuity = ContinuityManager(config, db=db, bus=self.bus)
        from ethos.memory.consolidate import ConsolidationDAG

        self.consolidation = ConsolidationDAG(self.memory, self.self_model, self.gateway, config, bus=self.bus)

        self.percept_queue: asyncio.Queue[Percept] = asyncio.Queue()
        self.deps = LifeDeps(
            config=config, control=self.control, bus=self.bus, memory=self.memory,
            self_model=self.self_model, attention=self.attention, motivation=self.motivation,
            rhythm=self.rhythm, gateway=self.gateway, gsl=self.gsl, social=self.social,
            comms_out=self.comms_out, toolhost=self.toolhost, audit=self.audit,
            continuity=self.continuity, consolidation=self.consolidation,
            context=self.context, embedder=self.embedder, thread_id="self",
            reverie=self.reverie,
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
            if event.kind in BUS_PERCEPT_KINDS:
                from ethos.schemas.events import percept_from_event

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
        await self.bus.start()
        await self.control.load()
        self._wire_bus()
        self.control.install_signal_handlers()
        await self.scheduler.start()
        # Send-only in the seven-process model, where `ethos-comms` is the process
        # that holds the doors open. The core's own copy of each adapter exists so a
        # reply can leave on the channel its question arrived on — including a
        # channel this process never listens to.
        #
        # The one-process development run has no `ethos-comms` to do it, so it is
        # told to. See `CoreRunner.listen_channels` for why that is a flag and not a
        # guess: the failure of guessing wrong in either direction is a channel
        # that silently receives nothing, and the two directions do not fail alike.
        await self.comms.start(listen=self.listen_channels)
        # The core's copy of each door follows the configuration too, and for a
        # different reason than `ethos-comms` does. This process never listens, so
        # there is no poller to reopen here; what it needs is the ability to *send*
        # on a door that was opened since it started. Without this, a person who
        # pasted a token and pressed Save would get their message received by
        # `ethos-comms` and then find the agent unable to answer on the channel it
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


async def run_core(config: EthosConfig | None = None, *, listen_channels: bool = False) -> None:
    config = config or load_config()
    configure_logging(level=os.environ.get("ETHOS_LOG_LEVEL", "INFO"))
    setup_tracing("ethos-core")
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
    runtime.control.stopped = True
    with contextlib.suppress(Exception):
        await runtime.control.apply_command("stop", f"signal:{sig.name}")
    # The ending is recorded here, where the signal is, rather than left to the
    # life loop's exit. The loop notices a stop at the top of its next pass, which
    # may be a whole cycle away — mid tool call, mid model call — and whatever
    # supervises this process does not wait that long: it asks its children to
    # stop, gives them a grace period and then kills them. So the one moment the
    # agent knows for certain that it is ending was the moment its record of
    # having ended went unwritten, and for the length of the presence window
    # after that, every surface and every `ethos status` read a machine with
    # nobody on it as an agent that was merely quiet.
    with contextlib.suppress(Exception):
        await runtime.continuity.save({
            "thread_id": "self",
            "state": "stopped",
            "stopped_at": datetime.now(UTC).isoformat(),
        })
