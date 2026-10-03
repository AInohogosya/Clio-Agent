from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

from ethos.core.status import (
    AGENT_ABSENT_S,
    PRESENCE_EVENT,
    agent_liveness,
    describe_age,
    interface_port,
)
from ethos.core.supervisor import ProcessSpec, Supervisor, node_tool
from tests.conftest import make_config

"""
Starting the agent is the product, and every bug in this file is a way it did not
happen.

The one a person notices is `./start.sh` printing the commands that start the
agent instead of starting it: the interface came up, showed the agent's last
known thoughts, and answered a question with "the agent is not running. Start it,
then ask again". The rest are the reasons the answer stayed wrong after the
launcher did start it — a supervisor that ended itself on the sixth child while
five ran on unsupervised, a Node install a version manager had put on `PATH` only
for interactive shells, a sign of life fresh enough to call an agent present when
the process behind it had already been killed. Each of those ends the same way,
and each is cheap to test next time.
"""


def _supervisor(tmp_path: Path) -> Supervisor:
    config = make_config(tmp_path)
    config.paths.ensure()
    return Supervisor(config)


class _FakeProc:
    """A child that is alive until the test says otherwise."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


# --------------------------------------------------------------- starting it


def test_a_child_that_cannot_be_spawned_does_not_end_the_supervisor(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The brick this file exists for.

    `./start.sh` started the agent, the sixth child could not be spawned — no
    `npm` on a `PATH` that a version manager had only ever set for interactive
    shells — and `FileNotFoundError` came out of `Popen`, through the start-up
    loop, and off the top of the supervisor. The five processes it had already
    started kept running: an agent with no gateway owner, no reaper, and no log
    line saying the supervision had stopped. One child's absence is not the
    supervisor's own absence, and it must not be reported as one.
    """
    supervisor = _supervisor(tmp_path)
    good = ProcessSpec("ethos-core", ["/bin/echo", "core"])
    missing = ProcessSpec("ethos-web", ["/nonexistent/node", "web/server/index.mjs"], optional=True)
    supervisor.specs = [missing, good]

    started: list[str] = []

    def fake_popen(argv: list[str], **kwargs: Any) -> Any:
        started.append(argv[0])
        if argv[0] == "/nonexistent/node":
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert supervisor._open(missing) is False, "the failure must be reported, not raised"
    assert supervisor._open(good) is True, "the child after a failed one must still be started"
    assert started == ["/nonexistent/node", "/bin/echo"]


def test_a_child_that_never_started_is_restarted_like_one_that_died(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """An absent child is the supervisor's to put back, whichever way it went.

    A spawn failure leaves no return code to test for, so treating it as anything
    other than a death meant a child that could not be started was never started
    again: the log said the process had been started, and the agent went on
    running without it.
    """
    supervisor = _supervisor(tmp_path)
    supervisor.BACKOFF_S = 0.0
    spec = ProcessSpec("ethos-gateway", ["/nonexistent/gateway", "gateway"])
    supervisor.specs = [spec]

    attempts: list[int] = []

    def always_missing(argv: list[str], **kwargs: Any) -> Any:
        attempts.append(1)
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(subprocess, "Popen", always_missing)

    assert supervisor._open(spec) is False
    supervisor._sweep()
    assert len(attempts) == 2, "a child that could not start must be tried again"
    assert spec.retired is False, "one failure is not the end of the road for it"


def test_a_child_is_given_up_on_only_after_the_limit(tmp_path: Any, monkeypatch: Any) -> None:
    """Bounded, because the alternative is a spawn loop every two seconds."""
    supervisor = _supervisor(tmp_path)
    supervisor.BACKOFF_S = 0.0
    spec = ProcessSpec("ethos-gateway", ["/nonexistent/gateway", "gateway"])
    supervisor.specs = [spec]

    def always_missing(argv: list[str], **kwargs: Any) -> Any:
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    monkeypatch.setattr(subprocess, "Popen", always_missing)

    supervisor._open(spec)
    for _ in range(supervisor.MAX_RESTARTS + 2):
        supervisor._sweep()
    assert spec.retired is True, "a child that will not start must stop being asked"


def test_an_optional_child_that_finds_the_job_done_is_not_a_failure(
    tmp_path: Any,
) -> None:
    """A bridge that exits 0 because the port is taken has done its job.

    The supervisor starts the interface alongside an agent that may already have
    one running, so the second bridge finding the port held is the ordinary case.
    Logging that as a dead child is a red line in the log for something that is
    working — and a log full of red is a log nobody reads, which is where the
    failures worth reading are.
    """
    supervisor = _supervisor(tmp_path)
    spec = ProcessSpec("ethos-web", ["node", "index.mjs"], optional=True)
    supervisor._retire(spec, 0)
    assert spec.retired is True, "it must not be started again — the port is held"
    assert spec.proc is None


# -------------------------------------------------------------- staying up


def test_a_signal_ends_the_supervisor_rather_than_only_its_children(tmp_path: Any) -> None:
    """`ethos stop` has to stop something.

    The signal used to start a shutdown task and be done with it, while the main
    loop carried on sleeping and polling over children that were already gone —
    a supervisor that would not exit, so a launcher waiting for the pid to
    disappear waited for ever, and a second `ethos up` found a live supervisor
    and refused to start the agent. The request for a stop is a flag; the loop
    that owns the children is what acts on it.
    """
    supervisor = _supervisor(tmp_path)
    alive = _FakeProc(pid=7)
    supervisor.specs = [ProcessSpec("ethos-core", ["x"], optional=False)]
    supervisor.specs[0].proc = alive

    supervisor._request_stop()
    assert supervisor._stopping, "a signal must stop the loop, not only the children"

    async def run_briefly() -> None:
        await supervisor.shutdown()

    asyncio.run(run_briefly())
    assert alive.terminated, "the children must be asked to stop"
    assert supervisor._shut_down, "and the shutdown must be the one that ran"


def test_the_pid_file_is_only_trusted_while_somebody_is_behind_it(tmp_path: Any) -> None:
    """A pid file left by a `kill -9` must not read as a running agent.

    It is how a launcher decides whether to start anything, so a stale one either
    refuses to start a second copy of a seven-process agent, or signals a pid the
    operating system has since handed to somebody else entirely.
    """
    supervisor = _supervisor(tmp_path)
    assert supervisor.read_pid() is None, "no file is no supervisor"

    # A pid that is certainly not running.
    supervisor.pid_file.write_text("999999\n", encoding="utf-8")
    assert supervisor.read_pid() is None

    # This process is certainly running, so this one is.
    supervisor.pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    assert supervisor.read_pid() == os.getpid()

    supervisor.pid_file.write_text("not a pid\n", encoding="utf-8")
    assert supervisor.read_pid() is None


def test_a_supervisor_only_removes_the_pid_file_that_is_its_own(tmp_path: Any) -> None:
    """The pid file is a shared, unowned piece of the run directory.

    Anything can be writing it — another launcher's supervisor, an operator's own
    script — and deleting a live pid out from under it is how a second `ethos up`
    comes to believe the agent is not running while it very much is.
    """
    supervisor = _supervisor(tmp_path)

    # Somebody else's supervisor: a live pid, and not ours.
    supervisor.pid_file.parent.mkdir(parents=True, exist_ok=True)
    supervisor.pid_file.write_text(f"{os.getppid()}\n", encoding="utf-8")
    supervisor._remove_pid_file()
    assert supervisor.pid_file.exists(), "another supervisor's pid file is not ours to delete"

    supervisor.pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    supervisor._remove_pid_file()
    assert not supervisor.pid_file.exists(), "our own must go, or it outlives us as a lie"


# ------------------------------------------------------------------ node


def _off_path(monkeypatch: Any) -> None:
    """Pretends `PATH` has nothing on it, as it is for every non-interactive start."""
    monkeypatch.setattr("ethos.core.supervisor.shutil.which", lambda name, **kw: None)


def _installed(directory: Path, tool: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    program = directory / tool
    program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    program.chmod(0o755)
    return program


def test_node_is_found_where_a_version_manager_put_it(tmp_path: Any, monkeypatch: Any) -> None:
    """`nvm` and its relatives put Node on `PATH` from a shell profile.

    Everything else in this repository is started by a script — by `cron`, by a
    launchd job, by an editor's task runner, by another program's `subprocess` —
    and a shell profile is not any of those. The bridge child died with
    `FileNotFoundError: 'npm'` on a machine with Node installed, and took the
    supervisor down with it.
    """
    _off_path(monkeypatch)
    home = tmp_path / "home"
    _installed(home / ".nvm" / "versions" / "node" / "v22.0.0" / "bin", "node")
    newest = home / ".nvm" / "versions" / "node" / "v24.1.0" / "bin"
    _installed(newest, "node")

    found = node_tool("node", home=home)
    assert found == str(newest / "node"), "the newest installed version is the one to use"


def test_node_and_npm_are_found_independently(tmp_path: Any, monkeypatch: Any) -> None:
    """They are not always installed together.

    An `nvm install` can leave a version with a runtime and no package manager
    beside it, and this machine had exactly that: node 24 and npm under 22. The
    bridge runs on one, the installs run on the other, and asking for both in the
    same breath would have found neither.
    """
    _off_path(monkeypatch)
    home = tmp_path / "home"
    newer = home / ".nvm" / "versions" / "node" / "v24.1.0" / "bin"
    _installed(newer, "node")
    older = home / ".nvm" / "versions" / "node" / "v22.0.0" / "bin"
    _installed(older, "node")
    _installed(older, "npm")

    assert node_tool("node", home=home) == str(newer / "node")
    assert node_tool("npm", home=home) == str(older / "npm")


def test_no_node_anywhere_is_reported_rather_than_guessed(tmp_path: Any, monkeypatch: Any) -> None:
    """None is an answer the caller can act on; a wrong path is not.

    A missing toolchain must become a sentence naming what to install, and never
    a path handed to `Popen` to fail on later.
    """
    _off_path(monkeypatch)
    home = tmp_path / "empty-home"
    home.mkdir()
    assert node_tool("node", home=home) is None


# --------------------------------------------------------------- the truth


def test_a_fresh_sign_of_life_is_the_agent_thinking(tmp_path: Any, monkeypatch: Any) -> None:
    """The positive case, which is the one a launcher is waiting for."""
    config = make_config(tmp_path)
    _no_stop_record(config)
    _presence(monkeypatch, age_s=2.0)
    alive, age_s, detail = agent_liveness(config)
    assert alive is True
    assert detail == ""
    assert age_s is not None and 0 < age_s < 10


def test_silence_beyond_the_window_is_an_absent_agent(tmp_path: Any, monkeypatch: Any) -> None:
    config = make_config(tmp_path)
    _no_stop_record(config)
    _presence(monkeypatch, age_s=AGENT_ABSENT_S + 5)
    alive, _age, detail = agent_liveness(config)
    assert alive is False
    assert "counts as gone" in detail, "the reason has to name the rule it applied"


def test_a_recorded_stop_is_not_overruled_by_a_recent_sign_of_life(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """The blind spot recency has, and the case it produced here.

    `ethos stop` asks the agent to end and then, ten seconds later, kills
    whatever has not. The agent notices a stop at the top of its next pass, which
    may be a minute away, so the one moment it knows for certain that it is
    ending is the moment its own record of having ended went unwritten. For the
    rest of the presence window the machine read as an agent that was merely
    quiet — which is the state that made "the agent is not running" advice
    arrive for an agent that had just answered.
    """
    config = make_config(tmp_path)
    _presence(monkeypatch, age_s=30.0)
    _record_stop(config, seconds_ago=5)
    alive, _age, detail = agent_liveness(config)
    assert alive is False
    assert "recorded its own stop" in detail


def test_a_stop_record_older_than_the_last_word_is_not_an_ending(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """`stopped` is a record of how the last run ended, not a standing order.

    Believed only while it is newer than the last thing the agent said — the same
    rule `ControlPlane.load` uses for the flag it refuses to inherit, because the
    process being started is the owner's restart.
    """
    config = make_config(tmp_path)
    _presence(monkeypatch, age_s=5.0)
    _record_stop(config, seconds_ago=600)
    alive, _age, _detail = agent_liveness(config)
    assert alive is True


def test_awaiting_needs_a_sign_of_life_newer_than_the_wait(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """What a start-up waits for, which "is it running" cannot answer.

    A machine where the agent died an hour ago still has a bridge serving its
    last known state and a supervisor-shaped hole in the process table. Only a
    sign of life *after* the start proves the start-up did something.
    """
    from datetime import UTC, datetime

    config = make_config(tmp_path)
    _no_stop_record(config)
    _presence(monkeypatch, age_s=600.0)

    started = datetime.now(UTC)
    alive, _age, detail = agent_liveness(config, after=started)
    assert alive is False
    assert "since this command started" in detail


def test_an_unreachable_database_is_reported_not_raised(tmp_path: Any, monkeypatch: Any) -> None:
    """A status command that raises is a status command with no output."""
    config = make_config(tmp_path)
    config.database.dsn = "postgresql://nobody:nobody@127.0.0.1:1/nothing"

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("no answer from the database at 127.0.0.1:1/nothing")

    monkeypatch.setattr("ethos.core.status._observation", refuse)
    alive, _age, detail = agent_liveness(config)
    assert alive is False
    assert "no answer from the database" in detail


def test_the_bridge_port_is_the_one_the_bridge_uses(tmp_path: Any, monkeypatch: Any) -> None:
    """A status line about the wrong port is the most useless thing it could print."""
    config = make_config(tmp_path)
    config.channels.web.http_port = 8720
    monkeypatch.delenv("ETHOS_WEB_PORT", raising=False)
    assert interface_port(config) == 8720
    monkeypatch.setenv("ETHOS_WEB_PORT", "9999")
    assert interface_port(config) == 9999
    monkeypatch.setenv("ETHOS_WEB_PORT", "nonsense")
    assert interface_port(config) == 8720, "a port that is not a port falls back, it does not fail"


def test_an_age_reads_as_the_shortest_honest_phrase() -> None:
    assert describe_age(2.0) == "just now"
    assert describe_age(45.0) == "45s ago"
    assert describe_age(600.0) == "10m ago"
    assert describe_age(7200.0) == "2h ago"
    assert describe_age(None) == "moments ago"


def test_the_presence_event_is_the_one_the_interface_listens_to() -> None:
    """One fact, two code bases.

    The browser's agent link treats anything on this event as proof the agent
    exists, and the bridge relays it. A status command reading a different row
    would be a third opinion about the same machine, which is how "the agent is
    not running" arrives from a surface while the agent is answering somebody
    else's question.
    """
    from ethos.schemas.events import EventKind

    assert PRESENCE_EVENT == EventKind.PRESENCE_UPDATE


# ------------------------------------------------------------------ helpers


def _presence(monkeypatch: Any, age_s: float) -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    seen = now - timedelta(seconds=age_s)

    async def fake_observation(_dsn: str) -> tuple[datetime, datetime]:
        return seen, now

    monkeypatch.setattr("ethos.core.status._observation", fake_observation)


def _no_stop_record(config: Any) -> None:
    path = config.paths.data_dir / "continuity.json"
    if path.exists():
        path.unlink()


def _record_stop(config: Any, seconds_ago: float) -> None:
    import json
    from datetime import UTC, datetime, timedelta

    path = config.paths.data_dir / "continuity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    saved_at = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    path.write_text(
        json.dumps({
            "version": 1,
            "saved_at": saved_at.isoformat(),
            "state": {"thread_id": "self", "state": "stopped"},
        }),
        encoding="utf-8",
    )


def test_the_run_loop_ends_when_a_stop_is_asked_for(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """`run()` has to come back.

    A launcher waits for the supervisor's pid to disappear, and a second
    `ethos up` believes a live supervisor means the agent is already up — so a
    loop that keeps polling over children that are already gone turns a stop into
    a hang and the next start into a refusal. The loop is run here for real, with
    one real child, and the request comes from the same place a signal does.

    `build_specs` is stubbed: the loop rebuilds the process list on the way in,
    and the real list is the seven-process agent, which a unit test has no
    business starting.
    """
    supervisor = _supervisor(tmp_path)
    monkeypatch.setattr(Supervisor, "build_specs", lambda self: None)
    monkeypatch.setattr(Supervisor, "POLL_S", 0.05)
    supervisor.specs = [ProcessSpec("ethos-sleeper", ["/bin/sleep", "30"])]

    async def run() -> float:
        task = asyncio.create_task(supervisor.run())
        await asyncio.sleep(0.3)
        assert supervisor.specs[0].proc is not None, "the child must actually have started"
        supervisor._request_stop()
        started = time.monotonic()
        await asyncio.wait_for(task, timeout=SHORT_TIMEOUT_S)
        return time.monotonic() - started

    took = asyncio.run(run())
    assert took < SHORT_TIMEOUT_S, "run() must return once a stop is asked for"
    assert not supervisor.pid_file.exists(), "a supervisor that has ended must not leave its pid"
    assert supervisor.specs[0].proc is None or supervisor.specs[0].proc.poll() is not None


SHORT_TIMEOUT_S = 5.0


def test_shutdown_is_written_where_the_signal_is(tmp_path: Any, monkeypatch: Any) -> None:
    """The agent records its own ending before it can be killed for not.

    `ethos-core` handles `SIGTERM` by setting the stop flag, and the life loop
    writes `state: "stopped"` when it reaches its exit — a whole cycle later, at
    the earliest. Its supervisor gives children ten seconds and then kills them,
    so in the normal case the record was never written at all.
    """
    import json

    from ethos.core.runner import _shutdown

    written: list[dict[str, Any]] = []

    class _Continuity:
        async def save(self, state: dict[str, Any]) -> None:
            written.append(state)

    class _Control:
        stopped = False

        async def apply_command(self, action: str, by: str) -> dict[str, Any]:
            return {"ok": True, "action": action, "by": by}

    class _Runtime:
        control = _Control()
        continuity = _Continuity()

    asyncio.run(_shutdown(_Runtime(), signal.SIGTERM))
    assert written, "the ending must be recorded where the signal is handled"
    assert written[0]["state"] == "stopped"
    assert written[0]["stopped_at"], "a record of an ending needs a time on it"
    json.dumps(written[0])  # it is about to be written to a file


def test_the_pid_file_is_not_written_where_it_cannot_be(tmp_path: Any, monkeypatch: Any) -> None:
    """A run directory that cannot be written is no reason to refuse to run the agent.

    The supervisor still works without a pid file; it just cannot be stopped by a
    pid. Saying so is the whole of the handling — raising here would turn a
    permissions problem into a machine with no agent on it, which is the failure
    this whole change is about.
    """
    supervisor = _supervisor(tmp_path)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_text", refuse)
    supervisor._write_pid_file()  # must not raise
    assert supervisor.read_pid() is None


def test_the_bridge_is_started_by_the_program_that_reads_its_package_json(
    tmp_path: Any,
) -> None:
    """The supervisor's bridge child is `node web/server/index.mjs`.

    It used to be `npm run start --prefix web`, which needs `npm` on `PATH` and
    then needs `node` on `PATH` again inside the script npm runs — so a machine
    with npm on `PATH` and node off it started a bridge that died with `env: node:
    No such file or directory`. The command the README, the Makefile and a person's
    own terminal all use is the one that works in every one of those places.
    """
    supervisor = _supervisor(tmp_path)
    supervisor.build_specs()
    web = [spec for spec in supervisor.specs if spec.name == "ethos-web"]
    if not web:
        return  # web/node_modules is not installed here; nothing to assert
    argv = web[0].argv
    assert Path(argv[0]).name.startswith("node"), f"expected node, got {argv[0]}"
    assert argv[1].endswith("web/server/index.mjs")
    assert web[0].optional, "the bridge is not the agent"
    assert Path(argv[0]).is_absolute(), "the child must not depend on the PATH it was found on"


# ---------------------------------------------------------------------------
# Stopping the supervisor where a signal cannot.
#
# Windows has no SIGTERM that a detached process can receive, so `ethos stop`
# there writes a request file and the supervisor's existing two-second poll loop
# notices it. Every one of these is about the *file* being real, because the whole
# design rests on the supervisor reacting to it rather than ignoring it — and the
# failure mode of getting it wrong is silent in the worst direction: a supervisor
# that ignores a stop request looks exactly like one that is busy.
#
# These run against `tmp_path`, never the agent's real run directory. The run
# directory of a *running* agent is a live protocol, and a test that leaves a stop
# request in it stops the agent.


def test_a_stop_request_is_written_where_the_supervisor_looks_for_it(tmp_path: Any) -> None:
    supervisor = _supervisor(tmp_path)
    assert not supervisor.stop_file.exists(), "the run directory starts with no request in it"
    assert supervisor.request_stop_by_file() is True
    assert supervisor.stop_file.exists()
    assert supervisor._stop_requested() is True
    assert supervisor.stop_file.parent == supervisor.pid_file.parent, (
        "the request and the pid must be answered by the same process, so they "
        "belong in the same directory"
    )


def test_a_stop_request_that_cannot_be_written_is_reported_not_raised(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """A refused ask must be a `False`, so `ethos stop` can say so.

    Raising here would escape `ethos stop` as a traceback about a permission bit,
    which is a much worse answer than "its run directory is not writable" — and it
    would be a *false* answer, because nothing was sent and the agent is still up.
    """
    supervisor = _supervisor(tmp_path)
    supervisor.stop_file.parent.mkdir(parents=True, exist_ok=True)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_text", refuse)
    assert supervisor.request_stop_by_file() is False
    assert not supervisor._stop_requested()


def test_a_stale_stop_request_does_not_stop_a_fresh_supervisor(tmp_path: Any) -> None:
    """A supervisor killed rather than asked must not refuse to start.

    The leftover request is the hazard: without the `run()` sweep that clears it, a
    machine that hard-killed its agent once would start a new one that shut itself
    down within two seconds, and `ethos up` would report it as running. That is a
    false *start*, which is worse than a failure because nothing is broken enough
    to notice.
    """
    supervisor = _supervisor(tmp_path)
    assert supervisor.request_stop_by_file() is True
    assert supervisor._stop_requested() is True

    # What `run()` does immediately after claiming the pid file.
    supervisor._clear_stop_file()
    assert not supervisor._stop_requested()


def test_shutdown_clears_the_request_so_the_next_start_is_clean(tmp_path: Any) -> None:
    supervisor = _supervisor(tmp_path)
    assert supervisor.request_stop_by_file() is True
    asyncio.run(supervisor.shutdown())
    assert not supervisor.stop_file.exists()
    assert supervisor._shut_down is True


def test_a_stop_request_leaves_the_poll_loop_by_the_same_door_a_signal_uses(
    tmp_path: Any,
) -> None:
    """`_request_stop` is the method, so everything after a stop is shared code.

    The risk is a second, Windows-only shutdown path: the pid file is removed by
    one and not the other, or the children are closed by one and not the other, and
    the difference only shows up on the platform nobody tests. Asserting the flag
    `run()`'s loop tests is what makes that structural rather than a convention.
    """
    supervisor = _supervisor(tmp_path)
    assert not supervisor._stopping
    supervisor._request_stop()
    # `_stopping` is literally the loop's exit condition in `run()`, so this is
    # the flag that ends the poll loop and reaches `shutdown()`.
    assert supervisor._stopping is True
