from __future__ import annotations

import json
from typing import Any

from ethos.core.control import ControlPlane
from ethos.core.runner import CoreRuntime
from tests.conftest import make_config


class _FakeDB:
    """Records control writes and answers load() with the last of them."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        attrs = self.rows.get("lifecycle")
        return {"attrs": attrs} if attrs is not None else None

    async def upsert_env(self, kind: str, key: str, attrs: dict[str, Any], **_: Any) -> None:
        self.rows[key] = dict(attrs)

    async def execute(self, stmt: str, *args: Any) -> str:
        """The key-by-key merge `ControlPlane._persist` writes for one verb."""
        attrs = json.loads(args[2])
        self.rows.setdefault(args[1], {})
        self.rows[args[1]].update(attrs)
        return "MERGE 1"


def _plane(db: _FakeDB, tmp_path: Any) -> ControlPlane:
    config = make_config(tmp_path)
    return ControlPlane(db, str(config.paths.run_dir))


"""
The verbs of the lifecycle, and the two things that are not verbs.

`preview` is a mode rather than a hold, and `ending` is this process rather than
the agent; both are kept apart from the verbs on purpose.

`stopped` used to be a record of how the last run ended rather than an order,
because a stop ended the process: nothing was left for a resume to restart, and
a flag every start inherited made the agent wake and exit within a second, for
good. A stop parks the agent now — the same process, still answering, waiting to
be resumed — so `resume` clears `stopped` again, and starting the process is no
longer the only way back. What a *signal* does is still to end the process, and
that is `ending`, which no surface can set.
"""


def test_a_stop_binds_the_running_agent_until_it_is_resumed(tmp_path: Any) -> None:
    """The Stop/Resume pair, which is a pair only if the second half works.

    A stop used to end the process, which left the surface holding a Resume
    button with nothing behind it: the process it would have resumed was gone, and
    the supervisor's replacement had already loaded the flag as `false`. So the
    agent came back on its own a few seconds after every Stop — the Stop button
    did not stick — and pressing Resume did nothing at all, because resume was
    forbidden from clearing the very flag it was supposed to undo. A stop parks
    the agent now, so resume is the way back and clearing the flag is what it
    means.
    """
    import asyncio

    db = _FakeDB()
    plane = _plane(db, tmp_path)
    result = asyncio.run(plane.apply_command("stop", "web"))
    assert result["ok"]
    assert plane.stopped, "a stop must bind the agent it stops"
    assert db.rows["lifecycle"]["stopped"] is True

    asyncio.run(plane.apply_command("resume", "web"))
    assert not plane.stopped, "resume is the other half of the stop button"
    assert db.rows["lifecycle"]["stopped"] is False, \
        "and the row every surface reads has to say the agent is back"


def test_a_resume_releases_a_stop_that_came_with_an_emergency(tmp_path: Any) -> None:
    """Emergency stop is a hold too, not a latch somebody is locked out of.

    It sets `stopped` as well as the pauses, so a resume that cleared only the
    pauses left an agent the owner had emergency-stopped unable to be started
    again from the surface that stopped it.
    """
    import asyncio

    db = _FakeDB()
    plane = _plane(db, tmp_path)
    asyncio.run(plane.apply_command("emergency_stop", "web"))
    assert plane.stopped and plane.emergency and plane.paused

    asyncio.run(plane.apply_command("resume", "web"))
    assert not (plane.stopped or plane.emergency or plane.paused or plane.pause_actions)


def test_a_verb_writes_only_the_flags_it_means(tmp_path: Any) -> None:
    """One writer's verb must not undo another writer's flag [the lost update].

    This row has more than one writer: the bridge writes the verb a surface
    pressed, this process writes its own reading of it. The write used to replace
    every flag with this process's snapshot, so answering an *earlier* verb
    persisted a `stopped: false` that had been read before the stop was pressed,
    straight over the stop that had just been written — a stop that silently
    reverts, which reads exactly like a Stop button that does not work.
    """
    import asyncio

    db = _FakeDB()
    bridge = _plane(db, tmp_path)
    core = ControlPlane(db, str(make_config(tmp_path).paths.run_dir), owns_row=False)
    asyncio.run(core.load())

    # The surface stops the agent, and this process is still holding the reading
    # it had before that arrived.
    asyncio.run(bridge.apply_command("stop", "web"))
    assert db.rows["lifecycle"]["stopped"] is True

    # A verb that knows nothing about stops must not mention `stopped` at all.
    asyncio.run(core.apply_command("preview_on", "web"))
    assert db.rows["lifecycle"]["stopped"] is True, \
        "a preview must not resurrect a stopped agent"
    assert db.rows["lifecycle"]["preview"] is True


def test_starting_the_process_is_the_restart_of_a_stopped_agent(tmp_path: Any) -> None:
    """The brick this file exists for.

    One Ctrl-C — or a closed terminal, or a supervisor cycling its children —
    persisted `stopped: true` exactly like an oversight stop. The next start
    loaded it and the agent exited within a second of waking, on every start,
    forever, while the interface stayed up and read the corpse as an agent
    choosing to ignore its owner. The flag is a record of an ending, and a new
    process is a beginning; these tests keep them apart.
    """
    import asyncio

    db = _FakeDB()
    stopped_run = _plane(db, tmp_path)
    asyncio.run(stopped_run.apply_command("stop", "signal:SIGTERM"))

    fresh = _plane(db, tmp_path)
    asyncio.run(fresh.load())
    assert not fresh.stopped, "a fresh start must not inherit the last run's ending"


def test_a_pause_does_survive_a_restart(tmp_path: Any) -> None:
    """Not inheriting the stop must not lose the pauses.

    A paused or emergency-stopped agent that is started again has to come back
    held: paused, and with its actions gated. What the owner resumes is a
    choice; what the process inherits is the safety posture it was left in.
    """
    import asyncio

    db = _FakeDB()
    stopped_run = _plane(db, tmp_path)
    asyncio.run(stopped_run.apply_command("emergency_stop", "web"))

    fresh = _plane(db, tmp_path)
    asyncio.run(fresh.load())
    assert not fresh.stopped, "the ending is not an order"
    assert fresh.emergency and fresh.paused and fresh.pause_actions, \
        "the posture it was left in is"


def test_the_stop_file_is_the_durable_kill(tmp_path: Any) -> None:
    """H1's kill switch is a file, and the file is re-read, not just loaded.

    A stop file present at start stops the start; a stop file dropped while
    the agent runs stops the run — files are truth, re-checked every cycle,
    and undone only by removing them. That is the durable kill the DB row is
    not, and it is why not inheriting the row costs H1 nothing.
    """
    import asyncio

    db = _FakeDB()
    config = make_config(tmp_path)
    config.paths.ensure()

    (config.paths.run_dir / "stop").write_text("")
    plane = ControlPlane(db, str(config.paths.run_dir))
    asyncio.run(plane.load())
    assert plane.stopped, "a stop file at start must stop the start"
    (config.paths.run_dir / "stop").unlink()

    running = ControlPlane(db, str(config.paths.run_dir))
    asyncio.run(running.load())
    assert not running.stopped
    (config.paths.run_dir / "stop").write_text("")
    asyncio.run(running.refresh())
    assert running.stopped, "a stop file dropped mid-run must stop the run"


# Preview mode: a mode the owner chose, kept apart from the lifecycle verbs.


def test_preview_survives_a_restart_because_it_is_a_mode_not_an_ending(tmp_path: Any) -> None:
    """The opposite rule from `stopped`, and it has to be the opposite rule.

    `stopped` is a record of how the last run ended, so a new process ignores it.
    `preview` is not that: it is a mode the agent is in, and a restart that
    quietly dropped it would hand the agent back its tools while every surface
    still showed the box ticked. The gap between what the checkbox says and what
    the machine does is the whole failure this feature exists to prevent, so it is
    closed in the direction that errs toward holding.
    """
    import asyncio

    db = _FakeDB()
    running = _plane(db, tmp_path)
    asyncio.run(running.apply_command("preview_on", "web"))

    fresh = _plane(db, tmp_path)
    asyncio.run(fresh.load())
    assert fresh.preview, "preview mode must outlive the process that was in it"
    assert db.rows["lifecycle"]["preview"] is True


def test_resume_does_not_hand_an_agent_its_tools_back(tmp_path: Any) -> None:
    """Un-pausing an agent is asking for it to think, not for it to start writing.

    These are two different requests and folding them together means one of them
    silently does the wrong thing. Somebody who put an agent in preview mode to
    watch it, then hit pause by accident and resumed it, has not asked for its
    file writes back — and an owner who cannot get an agent out of preview mode
    from the pause button would have no way to undo their own caution.
    """
    import asyncio

    db = _FakeDB()
    plane = _plane(db, tmp_path)
    asyncio.run(plane.apply_command("preview_on", "web"))
    asyncio.run(plane.apply_command("pause_all", "web"))
    asyncio.run(plane.apply_command("resume", "web"))
    assert not plane.paused and not plane.pause_actions
    assert plane.preview, "resume is for a paused agent, not for a previewing one"


def test_no_lifecycle_verb_puts_an_agent_into_preview(tmp_path: Any) -> None:
    """And the other direction, for the same reason.

    An agent somebody has just emergency-stopped is not thereby being previewed.
    If `emergency_stop` set the flag, the refusal it produces would be journalled
    as a mode somebody chose to look at it in, which is not what happened and not
    what anybody reading the trail afterwards would think happened.
    """
    import asyncio

    db = _FakeDB()
    plane = _plane(db, tmp_path)
    for action in ("pause_actions", "pause_all", "stop", "emergency_stop"):
        asyncio.run(plane.apply_command(action, "web"))
        assert not plane.preview, action


def test_preview_off_is_the_only_thing_that_clears_it(tmp_path: Any) -> None:
    import asyncio

    db = _FakeDB()
    plane = _plane(db, tmp_path)
    asyncio.run(plane.apply_command("preview_on", "web"))
    assert plane.preview
    result = asyncio.run(plane.apply_command("preview_off", "web"))
    assert result["ok"]
    assert not plane.preview
    assert db.rows["lifecycle"]["preview"] is False, \
        "the row every surface reads has to say the same thing"


def test_the_row_every_surface_reads_carries_the_flag(tmp_path: Any) -> None:
    """The interface reads the lifecycle row, so the row is the whole contract.

    A flag held only in this process's memory would be a checkbox that survives
    until the next restart and reads as though it had been saved — the one
    failure mode a durable safety setting must not have.
    """
    import asyncio

    db = _FakeDB()
    plane = _plane(db, tmp_path)
    asyncio.run(plane.apply_command("preview_on", "web"))
    assert db.rows["lifecycle"]["preview"] is True
    assert plane.snapshot()["preview"] is True


def test_there_is_no_preview_file(tmp_path: Any) -> None:
    """No hard file for preview mode, and the reason it is not a gap.

    `stopped` and `paused` have files because the agent has to be stoppable by
    something that is neither the agent nor the database [H1]. Preview mode has no
    such reader to serve, and a file would not be a safety net anyway — it would
    be a second source of truth whose removal meant something different from the
    row that also holds the flag, which is how "I removed it and it was still
    previewing" happens. A test rather than a comment, because the file is
    tempting to add and this says what it would cost.
    """
    config = make_config(tmp_path)
    plane = _plane(_FakeDB(), tmp_path)
    assert not hasattr(plane, "preview_file")
    assert not (config.paths.run_dir / "preview").exists()


class _StubBus:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def subscribe(self, kind: str | None, handler: Any) -> None:
        self.handlers.append(handler)


class _StubControl:
    def __init__(self) -> None:
        self.applied: list[tuple[str, str]] = []

    async def apply_command(self, action: str, by: str = "system") -> dict[str, Any]:
        self.applied.append((action, by))
        return {"ok": True}


def _runtime_stub() -> Any:
    import asyncio
    from types import SimpleNamespace

    bus = _StubBus()
    control = _StubControl()
    undone: list[dict[str, Any]] = []

    async def _handle_undo(payload: dict[str, Any]) -> None:
        undone.append(payload)

    runtime = SimpleNamespace(
        bus=bus, control=control, percept_queue=asyncio.Queue(), _handle_undo=_handle_undo,
    )
    CoreRuntime._wire_bus(runtime)  # type: ignore[arg-type]
    return runtime, control, undone, bus.handlers[0]


def test_a_verb_a_surface_sends_reaches_the_control_plane(tmp_path: Any) -> None:
    """The oversight that reads as obeyed and is not [H1].

    Every surface — the browser, the terminal, `curl` — sends a lifecycle verb the
    same way: a `control.update` row on the bus. The core subscribed one handler
    for the whole bus and dispatched it as a chain, and `control.update` was also
    listed among the kinds the agent *perceives*, because attention counts it as
    interruptible. A kind in both sets reached the first branch only: the verb was
    dropped, the control table row said otherwise, and the agent carried on doing
    exactly what it had been told to stop. So a `stop` or an `emergency_stop`
    pressed on any surface did nothing at all, and the reader had no way to tell.
    """
    import asyncio

    from ethos.schemas.events import Event

    _, control, _, handler = _runtime_stub()
    asyncio.run(handler(Event(kind="control.update", payload={"action": "pause_all", "by": "phone"})))
    assert control.applied == [("pause_all", "phone")], "the verb must be obeyed, not perceived"


def test_a_verb_is_still_something_the_agent_notices(tmp_path: Any) -> None:
    """Perceived *and* obeyed: two questions about one event, asked separately.

    The two are not alternatives. Attention counts `control.update` as
    interruptible precisely so the agent knows it was paused — dropping the
    percept to get the command through would buy oversight by blinding the mind
    to it.
    """
    import asyncio

    from ethos.schemas.events import Event

    runtime, _, _, handler = _runtime_stub()
    asyncio.run(handler(Event(kind="control.update", payload={"action": "stop", "by": "phone"})))
    queued = runtime.percept_queue
    assert queued.qsize() == 1
    assert asyncio.run(queued.get()).kind == "control.update", \
        "the agent must still see what was done to it"


def test_an_undo_asked_for_on_a_surface_is_carried_out(tmp_path: Any) -> None:
    """Same shadowing, same fix: `guardian.undo` was swallowed the same way.

    The bridge publishes it and the core owns carrying it out, so a queued undo
    that reached nothing left the reader looking at a button that answered
    immediately and undid nothing.
    """
    import asyncio

    from ethos.schemas.events import Event

    _, _, undone, handler = _runtime_stub()
    asyncio.run(handler(Event(kind="guardian.undo", payload={"action_id": "a-1"})))
    assert undone == [{"action_id": "a-1"}]


def test_a_process_that_only_obeys_the_lifecycle_does_not_re_write_it(tmp_path: Any) -> None:
    """`owns_row=False` is the difference between correcting a flag and clobbering one.

    The core corrects a stale `stopped` on startup, and it is right to: the row
    records how *that* process ended. The toolhost's gate obeys the same lifecycle
    and is not that process — so with the correction left on, every restart of the
    toolhost (which the supervisor does on its own, with backoff, for a crash the
    owner never asked about) would write its own `stopped: false` over a stop the
    agent was really in. Every surface would then read a stopped agent as running,
    and a restart would bring it back. A reader must not be a writer.
    """
    import asyncio

    db = _FakeDB()
    core = _plane(db, tmp_path)
    asyncio.run(core.apply_command("stop", "web"))
    assert db.rows["lifecycle"]["stopped"] is True

    # A second process starts, reads the row, and inherits the posture.
    reader = ControlPlane(db, str(make_config(tmp_path).paths.run_dir), owns_row=False)
    asyncio.run(reader.load())
    assert not reader.stopped, "it is not the process that was stopped"
    assert db.rows["lifecycle"]["stopped"] is True, \
        "and the row other readers trust must still say the agent is stopped"


def test_a_reader_obeys_a_preview_it_reads(tmp_path: Any) -> None:
    """It does not write the row on the way in, but it does honour what is there.

    The two halves are separate on purpose. Not writing is about not clobbering
    somebody else's state; obeying is the entire point of the plane existing in
    this process at all — a gate that read the flag and ignored it would be the
    same no-gate as having none.
    """
    import asyncio

    db = _FakeDB()
    core = _plane(db, tmp_path)
    asyncio.run(core.apply_command("preview_on", "web"))

    reader = ControlPlane(db, str(make_config(tmp_path).paths.run_dir), owns_row=False)
    asyncio.run(reader.load())
    assert reader.preview, "a gate in another process must see the flag too"


def test_starting_the_agent_re_writes_the_row_it_reads(tmp_path: Any) -> None:
    """The row has to mean what this process is doing, not how the last one ended.

    A signal handler persists `stopped: true` when the process is told to stop,
    which is a true record of that process ending — and every surface reads the
    same row to decide whether the agent can take a turn. So an ordinary Ctrl-C,
    the way an operator stops this thing locally, left a row that made a
    *running* agent report itself stopped and refused messages. Not inheriting
    the flag on load is only half of it; the row has to be corrected too, because
    the flag is read from outside this process.
    """
    import asyncio

    db = _FakeDB()
    ended = _plane(db, tmp_path)
    asyncio.run(ended.apply_command("stop", "signal:SIGTERM"))

    started = _plane(db, tmp_path)
    asyncio.run(started.load())
    assert not started.stopped, "the new process is not stopped"
    assert db.rows["lifecycle"]["stopped"] is False, \
        "and the row every reader trusts now says the same thing"


def test_only_a_signal_ends_the_loop(tmp_path: Any) -> None:
    """`ending` is a fact about this process, and no verb a surface sends sets it.

    It has to be separate from `stopped`, because those two now mean different
    things: a stop is a hold somebody can take back with the button beside it, and
    an ending is not something a row in a database gets to decide.
    """
    import asyncio

    plane = _plane(_FakeDB(), tmp_path)
    asyncio.run(plane.apply_command("stop", "web"))
    assert plane.stopped and not plane.ending, "a surface stop is a hold, not an ending"

    plane.end("signal:SIGTERM")
    assert plane.ending and plane.stopped, "a signal ends the process, and stops it too"
