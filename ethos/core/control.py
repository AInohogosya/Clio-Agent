from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

from ethos.db import Database, decode_env_attrs
from ethos.observability import exhaustive
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
        # This process is *ending*, as opposed to the agent being stopped. Only a
        # signal sets it, and it is the one thing that ends the life loop: a stop
        # from a surface parks the agent (see `Life.life`) so the Resume button
        # beside it has something to resume, whereas a Ctrl-C has to actually end
        # the process. Before this was split, `stop` could only mean "exit", which
        # is why resume could not be a restart: the process a stop ended was the
        # process the surface was talking to, and the supervisor brought a new one
        # up behind it with the flag already cleared — so the Stop button did not
        # stick and the Resume button did nothing, and both read as dead.
        self.ending = False
        self._resume_event = asyncio.Event()
        self._resume_event.set()
        # Set while the agent is stopped, so a cycle already in flight can be cut
        # short instead of running to its end before anybody noticed.
        self._stop_event = asyncio.Event()
        # Which flags are standing because a file in the run dir says so, rather
        # than because a surface asked. See `_apply_files`.
        self._stop_from_file = False
        self._hold_from_file = False
        # Set by anything that changes a flag, and cleared by whoever waits on it.
        # One event rather than one per flag because the loop never cares *which*
        # flag changed, only that its cached reading is stale.
        self._wake_event = asyncio.Event()
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
                attrs = decode_env_attrs(row)
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
        # The row's flags are new to this process and every waiter needs them,
        # so the events are brought in step before anything can wait on them.
        self._after_change()
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
        before = self.snapshot()
        # Each file is a two-way switch, and each remembers whether it is the one
        # that set the flag. That is what makes removing the file release the hold
        # without also releasing a hold a surface pressed: a stop a person asked
        # for has no file behind it and is taken back with `resume`, while a stop
        # the host wrote is the host's to lift, and the only way it ever could be.
        # It mattered more once a stop stopped meaning "the process ends" — a hold
        # nothing could take back was a hold only a restart could take back, which
        # is the same dead button from the other side.
        if os.path.exists(self.stop_file):
            self._stop_from_file = True
            self.stopped = True
        elif self._stop_from_file:
            self._stop_from_file = False
            self.stopped = False
        held = os.path.exists(self.pause_file) or os.path.exists(self.emergency_file)
        if held:
            self._hold_from_file = True
            self.paused = True
            self.pause_actions = True
            if os.path.exists(self.emergency_file):
                self.emergency = True
        elif self._hold_from_file:
            self._hold_from_file = False
            self.paused = False
            self.pause_actions = False
            self.emergency = False
        self._after_change(before)

    def _after_change(self, before: dict[str, Any] | None = None) -> None:
        """Keeps the waiters in step with the flags they are waiting on.

        Every gate that blocks — a parked loop, a cycle waiting to be interrupted
        — is an `asyncio.Event` next to the flag it is blocked on, and an event
        that outlives the flag is how "I pressed stop and the agent carried on"
        happens. So the events are set from the flags here rather than remembered
        at each assignment: one place, and no verb added later can forget.
        """
        after = self.snapshot()
        if before == after:
            return
        if self.stopped:
            self._stop_event.set()
        else:
            self._stop_event.clear()
        if self.paused or self.stopped:
            self._resume_event.clear()
        else:
            self._resume_event.set()
        self._wake_event.set()

    async def _persist(self, changed: set[str] | None = None) -> None:
        """Writes this process's reading of the lifecycle row.

        `changed` names the flags this write is about, and then only those keys
        are merged into the row. The full replace it used to do was a lost update
        waiting for a late event: this row has more than one writer (the bridge
        writes the verb a surface pressed, this process writes its own reading of
        it), and a process that answers an *earlier* verb persisted its whole
        snapshot — including a `stopped: false` it had read before the stop was
        pressed — straight over the stop that had just been written. A stop that
        silently reverts reads exactly like a Stop button that does not work.

        The one full write is `load`, and it is deliberate: correcting a stale
        flag is right for the process whose own ending was recorded, and wrong for
        every other process (see the note there).
        """
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
        if changed is None:
            await self.db.upsert_env(CONTROL_KIND, CONTROL_KEY, attrs, owner="control")
            return
        merge = {key: attrs[key] for key in changed if key in attrs}
        merge["updated_at"] = attrs["updated_at"]
        if not merge:
            return
        # The same key-by-key merge the interface's bridge uses, so a flag written
        # by one writer and a flag written by the other can both be standing.
        await self.db.execute(
            "INSERT INTO env_entities (id, kind, key, attrs, owner, last_verified_at, health) "
            "VALUES (gen_random_uuid(), $1, $2, $3::jsonb, $4, now(), 'ok') "
            "ON CONFLICT (key) DO UPDATE SET attrs = ("
            "  SELECT jsonb_object_agg(key, value) FROM ("
            "    SELECT key, value FROM jsonb_each(COALESCE(env_entities.attrs, '{}'::jsonb))"
            "    WHERE key NOT IN (SELECT key FROM jsonb_each($3::jsonb))"
            "    UNION ALL"
            "    SELECT key, value FROM jsonb_each($3::jsonb)"
            "  ) merged"
            "), last_verified_at = now()",
            CONTROL_KIND, CONTROL_KEY, json.dumps(merge), "control",
        )

    async def apply_command(self, action: str, by: str = "system") -> dict[str, Any]:
        before = self.snapshot()
        changed: set[str] = set()
        if action == "pause_all":
            self.paused = True
            self.pause_actions = True
            changed = {"paused", "pause_actions"}
        elif action == "pause_actions":
            self.pause_actions = True
            changed = {"pause_actions"}
        elif action == "resume":
            # `stopped` included, deliberately and against the older rule that
            # resume was not a restart. The rule was right while a stop could only
            # mean "end the process": there was nothing left for a resume to
            # restart, so clearing the flag would have let a resume quietly revive
            # an agent somebody had stopped for good. A stop now parks the agent
            # in this process — it is still here, still answering, and its Resume
            # button is the way back — so the case the old rule was protecting
            # against no longer exists, and a Resume that left the flag standing
            # was a button that could only ever report that it had done nothing.
            self.paused = False
            self.pause_actions = False
            self.emergency = False
            self.stopped = False
            changed = {"paused", "pause_actions", "emergency", "stopped"}
        elif action == "stop":
            self.stopped = True
            changed = {"stopped"}
        elif action == "emergency_stop":
            self.emergency = True
            self.paused = True
            self.pause_actions = True
            self.stopped = True
            changed = {"emergency", "paused", "pause_actions", "stopped"}
        elif action == "preview_on":
            self.preview = True
            changed = {"preview"}
        elif action == "preview_off":
            self.preview = False
            changed = {"preview"}
        else:
            return {"ok": False, "error": f"unknown action {action}"}
        self._after_change(before)
        await self._persist(changed)
        logger.info("control.command", action=action, by=by)
        exhaustive.log_settings_change(
            actor=by, what=f"lifecycle.{action}",
            before=before, after=self.snapshot(),
        )
        return {"ok": True, "action": action, "by": by}

    def end(self, by: str = "signal") -> None:
        """This process is ending, rather than the agent being stopped.

        A signal is the one reader that must end the loop, so it is kept separate
        from `stopped` and is not a row anybody can set: the row is what a surface
        reads, and a surface that could set "this process is ending" would be a
        surface that can make the agent unrecoverable with a button.
        """
        self.ending = True
        self.stopped = True
        self._after_change()
        logger.info("control.ending", by=by)

    async def refresh(self) -> None:
        """Called at the top of every cycle: files + bus state are truth [H1]."""
        if not self._loaded:
            await self.load()
        else:
            self._apply_files()

    async def wait_resume(self) -> None:
        await self._resume_event.wait()

    async def wait_stop(self) -> None:
        """Waits until the agent is asked to stop, so a cycle can be cut short."""
        await self._stop_event.wait()

    async def wait_wake(self) -> None:
        """Waits until something changes the flags, then re-reads them.

        Bounded by the caller's timeout: a parked agent that nobody resumes still
        has to say it is there on the presence heartbeat.
        """
        await self._wake_event.wait()
        self._wake_event.clear()

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
