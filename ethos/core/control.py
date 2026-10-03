from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

from ethos.db import Database
from ethos.observability.logger import get_logger

logger = get_logger("ethos.core.control")

CONTROL_KEY = "lifecycle"
CONTROL_KIND = "control"


class ControlPlane:
    """Oversight is shared infrastructure [D-09, H1].

    Pause and kill are always effective: flags live in the control table the
    web dashboard and OOB guardian write to, plus hard files in the run dir
    that the agent never owns. The toolhost gate refuses actions while
    pause_actions is set, so a paused agent cannot act even mid-cycle.

    `preview` is a mode rather than an oversight hold, and it is kept off to one
    side for that reason. It gates the same way — the gate refuses every tool —
    but it answers a different question: not "somebody stopped this" but
    "somebody is watching it and does not want it touching anything yet". So the
    lifecycle verbs leave it alone in both directions. `resume` does not clear
    it, because an owner un-pausing an agent they had also put in preview is
    asking for the agent to think again, not for it to start writing files; and
    none of the pause verbs set it, because an agent somebody has just hit the
    emergency stop on is not thereby being previewed.

    No hard file, unlike the three above, and that is the reason rather than an
    oversight. Those exist because the agent must be stoppable by something that
    is neither the agent nor the database, at the moment somebody decides it has
    to be [H1]. Preview mode has no such reader to serve — the row and the bus
    already reach every gate that cares, in this process and in the toolhost's —
    and a file whose removal meant something different from the row that also
    holds the flag would be a second source of truth with its own rules, which is
    exactly how "I removed it and it was still previewing" happens.
    """

    def __init__(self, db: Database | None, run_dir: str, bus: Any = None, owns_row: bool = True):
        self.db = db
        self.bus = bus
        self.run_dir = os.path.expanduser(run_dir)
        # Whether this process is the one whose reading of the row is the
        # authoritative one — see `load`, where it is the difference between
        # correcting a stale flag and overwriting somebody else's.
        self.owns_row = owns_row
        os.makedirs(self.run_dir, exist_ok=True)
        self.stopped = False
        self.paused = False
        self.pause_actions = False
        self.emergency = False
        self.preview = False
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        self._loaded = False

    @property
    def stop_file(self) -> str:
        return os.path.join(self.run_dir, "stop")

    @property
    def pause_file(self) -> str:
        return os.path.join(self.run_dir, "paused")

    @property
    def emergency_file(self) -> str:
        return os.path.join(self.run_dir, "emergency")

    async def load(self) -> None:
        self._loaded = True
        if self.db is not None:
            row = await self.db.fetchrow(
                "SELECT attrs::text AS attrs FROM env_entities WHERE kind = $1 AND key = $2",
                CONTROL_KIND, CONTROL_KEY,
            )
            if row is not None:
                attrs = json.loads(row["attrs"]) if isinstance(row["attrs"], str) else dict(row["attrs"])
                self.paused = bool(attrs.get("paused", False))
                self.pause_actions = bool(attrs.get("pause_actions", False))
                self.emergency = bool(attrs.get("emergency", False))
                # `preview` *is* inherited, unlike `stopped`: it is not a record of
                # how the last run ended, it is a mode the owner put the agent in
                # and has not taken it out of. A restart that quietly restored the
                # agent's ability to act while the row every surface reads still
                # said "preview" would be the more dangerous of the two errors —
                # the checkbox would be showing the safe thing and the machine
                # would be doing the other one.
                self.preview = bool(attrs.get("preview", False))
                # `stopped` is a record of how the last run ended, not an order
                # to the next one, so a fresh start does not inherit it: the
                # process being started *is* the owner's restart, and treating
                # the row as a standing command meant one Ctrl-C — a closed
                # terminal, a supervisor cycling its children — left an agent
                # that woke, restored its focus, and exited within a second,
                # on every start, forever. Nothing could clear the flag: resume
                # is for a paused agent by design, and the stop was never meant
                # to outlive the process it stopped. The durable kill is the
                # stop file below, which is re-read every cycle [H1] and is only
                # undone by removing it — plus the host's own switches above
                # the agent (the OOB file, VM suspend, kill -9).
                self.stopped = False
        self._apply_files()
        # The row now says what *this* process is doing rather than how the last
        # one ended, and that is the only reading every reader can use.
        #
        # The interface reads this same row to decide whether the agent can take a
        # turn, so a `stopped` row left behind by an ordinary Ctrl-C — which is
        # what a signal handler records, because the process really is ending —
        # outlived the process that wrote it and had every surface reporting a
        # working agent as stopped, refusing to send to it. The flag was already
        # not inherited here; saying so in the row is the rest of it.
        #
        # Only when this process owns the row, though. A second process that
        # merely obeys the lifecycle — the toolhost's gate does, and must — would
        # write its own `stopped: false` over a stop the agent was really in, and
        # do it on every restart of *itself*, which the supervisor does without
        # anybody asking. Correcting a stale flag is right for the process whose
        # own ending was recorded; it is sabotage for every other one.
        if self.db is not None and self.owns_row:
            await self._persist()

    def _apply_files(self) -> None:
        if os.path.exists(self.stop_file):
            self.stopped = True
        if os.path.exists(self.pause_file):
            self.paused = True
            self.pause_actions = True
        if os.path.exists(self.emergency_file):
            self.emergency = True
            self.paused = True
            self.pause_actions = True

    async def _persist(self) -> None:
        if self.db is None:
            return
        attrs = {
            "paused": self.paused,
            "pause_actions": self.pause_actions,
            "stopped": self.stopped,
            "emergency": self.emergency,
            "preview": self.preview,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        await self.db.execute(
            "INSERT INTO env_entities (id, kind, key, attrs, owner, last_verified_at, health) "
            "VALUES (gen_random_uuid(), $1, $2, $3::jsonb, 'control', now(), 'ok') "
            "ON CONFLICT (key) DO UPDATE SET attrs = $3::jsonb, last_verified_at = now()",
            CONTROL_KIND, CONTROL_KEY, json.dumps(attrs),
        )

    async def apply_command(self, action: str, by: str = "system") -> dict[str, Any]:
        if action == "pause_all":
            self.paused = True
            self.pause_actions = True
            self._resume_event.clear()
        elif action == "pause_actions":
            self.pause_actions = True
        elif action == "resume":
            self.paused = False
            self.pause_actions = False
            self.emergency = False
            self._resume_event.set()
        elif action == "stop":
            self.stopped = True
        elif action == "emergency_stop":
            self.emergency = True
            self.paused = True
            self.pause_actions = True
            self.stopped = True
            self._resume_event.clear()
        elif action == "preview_on":
            self.preview = True
        elif action == "preview_off":
            self.preview = False
        else:
            return {"ok": False, "error": f"unknown action {action}"}
        await self._persist()
        logger.info("control.command", action=action, by=by)
        return {"ok": True, "action": action, "by": by}

    async def refresh(self) -> None:
        """Called at the top of every cycle: files + bus state are truth [H1]."""
        if not self._loaded:
            await self.load()
        else:
            self._apply_files()

    async def wait_resume(self) -> None:
        await self._resume_event.wait()

    def snapshot(self) -> dict[str, Any]:
        return {
            "paused": self.paused,
            "pause_actions": self.pause_actions,
            "stopped": self.stopped,
            "emergency": self.emergency,
            "preview": self.preview,
        }

    def install_signal_handlers(self) -> None:
        import signal
        import sys

        if sys.platform == "win32":
            return
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGUSR1, lambda: _spawn(self.apply_command("pause_all", "signal:SIGUSR1")))
        loop.add_signal_handler(signal.SIGUSR2, lambda: _spawn(self.apply_command("resume", "signal:SIGUSR2")))


def _spawn(coro: Any) -> None:
    asyncio.get_running_loop().create_task(coro)
