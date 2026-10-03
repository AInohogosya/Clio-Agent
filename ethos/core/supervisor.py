from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from ethos.config import EthosConfig, load_config
from ethos.observability.logger import configure_logging, get_logger
from ethos.platform import IS_WINDOWS, process_is_alive

logger = get_logger("ethos.core.supervisor")

PYTHON = sys.executable

CORE_PROCESSES: list[tuple[str, list[str]]] = []

SUPERVISOR_PID_FILE = "supervisor.pid"

STOP_REQUEST_FILE = "supervisor.stop"
"""The file `ethos stop` writes to ask for a shutdown where no signal can.

Windows has no way to say "please stop" to a process that was started detached,
and this is the reason that is a mechanism rather than a workaround:

- `GenerateConsoleCtrlEvent` — the API behind `CTRL_BREAK_EVENT` — only reaches
  processes that share a console *and* sit in the caller's process group. A
  supervisor started by `ethos up` is deliberately given neither, which is what
  makes it survive its terminal.
- `TerminateProcess`, which is what `os.kill` reaches for any signal other than
  the two console events, has no grace period at all. The supervisor's own
  shutdown — the pid file removal, the final log lines — would not run.

So on Windows the *ask* is a file in the run directory, and the supervisor's
existing two-second poll loop is the thing that notices. The poll loop was
already there watching for dead children, so this costs no new machinery and no
new thread; `_request_stop`, the method a POSIX signal calls, is the method this
calls, and everything downstream of a stop is the same code either way.

Deliberately *not* used on POSIX, where `SIGTERM` says this better and the
supervisor handles it without polling a file. The file is only written by the
Windows branch of `ethos stop`, and `run()` removes a stale one at start-up so a
supervisor that was killed rather than asked cannot refuse to start.
"""

SHUTDOWN_GRACE_S = 10.0


class ProcessSpec:
    def __init__(self, name: str, argv: list[str], optional: bool = False):
        self.name = name
        self.argv = argv
        self.optional = optional
        self.proc: subprocess.Popen | None = None
        self.last_start: float = 0.0
        self.restart_count: int = 0
        """Set once this child has used up its restarts, so the sweep stops asking."""
        self.retired: bool = False
        """The earliest moment the next attempt is allowed to happen."""
        self.next_attempt: float = 0.0


def node_tool(name: str, home: Path | None = None) -> str | None:
    """An absolute path to a Node tool, or `None` when it cannot be found.

    `PATH` first, because that is where it is when it is there. Then the places
    Node actually lives on a machine that installed it the ordinary way: nvm, fnm,
    volta, asdf, and the two Homebrew prefixes.

    The fallback is the point. `nvm` and its relatives put `node` on `PATH` from a
    shell profile, so a script run from a terminal has it and a script run from
    anywhere else — a `cron` entry, a launchd job, an editor's task runner,
    another program's `subprocess` — does not. That difference is how the
    interface bridge came to be spawned by a supervisor that raised
    `FileNotFoundError: 'npm'` on its *sixth* child and exited with the first five
    still running and unsupervised: half an agent, nothing watching it, and no log
    line saying the supervision was over. A path is returned rather than a
    boolean, so the child is then started by absolute path and cannot lose its
    interpreter between the check and the spawn either.
    """
    found = shutil.which(name)
    if found:
        return found
    root = home or Path.home()
    candidates: list[Path] = []
    nvm = root / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        # Newest first, because the first match is the one that is used — and
        # `nvm install` leaves every version it ever installed behind, so the
        # wrong order hands the interface an old interpreter nobody chose.
        candidates += sorted((p / "bin" for p in nvm.iterdir() if p.is_dir()), reverse=True)
    fnm = root / ".fnm" / "node-versions"
    if fnm.is_dir():
        candidates += sorted((p / "installation" / "bin" for p in fnm.iterdir() if p.is_dir()), reverse=True)
    candidates += [
        root / ".volta" / "bin",
        root / ".asdf" / "shims",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    for directory in candidates:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class Supervisor:
    """Seven-process model [D-11]: starts gateway, toolhost, comms, workers,
    core and (optionally) web; restarts crashed children with backoff."""

    MAX_RESTARTS = 5
    BACKOFF_S = 3.0
    POLL_S = 2.0

    def __init__(self, config: EthosConfig):
        self.config = config
        self.log_dir = config.paths.data_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.specs: list[ProcessSpec] = []
        self._stopping = False
        self._shut_down = False

    @property
    def pid_file(self) -> Path:
        return self.config.paths.run_dir / SUPERVISOR_PID_FILE

    @property
    def stop_file(self) -> Path:
        return self.config.paths.run_dir / STOP_REQUEST_FILE

    def request_stop_by_file(self) -> bool:
        """Ask this supervisor to stop, the way Windows has to ask.

        Writes the request and reports whether it landed. Returns `False` rather
        than raising when the run directory cannot be written, so the caller can
        say "I could not ask" instead of the operator being told the agent stopped
        when nothing was sent.
        """
        try:
            self.stop_file.parent.mkdir(parents=True, exist_ok=True)
            self.stop_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
        except OSError as exc:
            logger.warning("supervisor.stop_request_failed", path=str(self.stop_file), reason=str(exc))
            return False
        logger.info("supervisor.stop_requested", path=str(self.stop_file))
        return True

    def _clear_stop_file(self) -> None:
        try:
            self.stop_file.unlink()
        except OSError:
            pass

    def _stop_requested(self) -> bool:
        return self.stop_file.exists()

    def build_specs(self) -> None:
        entry = [PYTHON, "-m", "ethos"]
        self.specs = [
            ProcessSpec("ethos-gateway", entry + ["gateway"]),
            ProcessSpec("ethos-toolhost", entry + ["toolhost"]),
            ProcessSpec("ethos-comms", entry + ["comms"]),
        ]
        for i in range(self.config.workers.count):
            self.specs.append(ProcessSpec(f"ethos-workers-{i+1}", entry + ["worker"]))
        # No `--listen-channels` here, and its absence is the point. `ethos-comms`
        # is in the list above and is the process that holds every channel door
        # open; a core that opened them too would put a second `getUpdates`
        # long-poll on one Telegram bot, which Telegram answers with a conflict
        # for as long as the first is alive — a channel that looks configured and
        # receives nothing. The flag exists for the single-process development
        # run, where this supervisor is not there to hold the doors instead.
        self.specs.append(ProcessSpec("ethos-core", entry + ["core"]))
        web_dir = Path(__file__).resolve().parent.parent.parent / "web"
        web_entry = web_dir / "server" / "index.mjs"
        if (web_dir / "node_modules").exists():
            # Node directly, and by absolute path, rather than through
            # `npm run start --prefix web`. That indirection costs a whole class
            # of failure for nothing: npm is only on PATH in a shell that loaded a
            # version manager, and the script npm runs then has to find `node` on
            # PATH a second time — so a supervisor with npm on PATH and node off it
            # started a bridge that died with `env: node: No such file or
            # directory`, which is the same hole this function exists to dig around.
            # `node web/server/index.mjs` is also the command the README, the
            # Makefile and a person's own terminal all use, so the bridge is now
            # started here exactly the way it is started everywhere else.
            node = os.environ.get("ETHOS_NODE") or node_tool("node")
            if node is None:
                # The bridge is optional for one reason only: it is not the agent.
                # It is also the one child that needs a toolchain the agent itself
                # does not, which is why a missing Node is a sentence naming what
                # to do about it rather than an exception out of a `Popen` that
                # ends the supervisor and orphans everything it had already begun.
                logger.warning(
                    "supervisor.web_skipped",
                    reason="node_not_found",
                    hint="install Node, or run the bridge yourself: node web/server/index.mjs",
                )
            else:
                self.specs.append(
                    ProcessSpec("ethos-web", [node, str(web_entry)], optional=True),
                )

    def _open(self, spec: ProcessSpec) -> bool:
        """Starts one child. False when it could not be started at all.

        A failure to spawn is a fact about *this* child — an interpreter that has
        moved, a Node install that is not on `PATH`, a resource limit — and not
        about the supervisor. Raising out of here used to end the supervisor from
        inside its own start-up loop, which left every already-started process
        running with nothing to restart them. So the failure is logged with the
        child's name and handed back, and the caller counts it like any other
        death: the same backoff, the same limit, the same "we have given up".
        """
        log_path = self.log_dir / f"{spec.name}.log"
        try:
            fh = open(log_path, "ab")
        except OSError as exc:
            logger.error("supervisor.start_failed", process=spec.name, reason=str(exc))
            return False
        try:
            spec.proc = subprocess.Popen(
                spec.argv, stdout=fh, stderr=fh, cwd=Path.cwd(),
            )
        except (OSError, ValueError) as exc:
            logger.error(
                "supervisor.start_failed",
                process=spec.name,
                program=spec.argv[0],
                reason=str(exc),
                hint="that program is not on this machine, or is not executable",
            )
            spec.proc = None
            return False
        finally:
            # The child holds its own copy of the descriptor now. Keeping this one
            # open leaks a descriptor per restart, and a supervisor restarts.
            fh.close()
        spec.last_start = time.monotonic()
        logger.info("supervisor.started", process=spec.name, pid=spec.proc.pid)
        return True

    def _should_restart(self, spec: ProcessSpec) -> bool:
        if self._stopping or spec.optional:
            return False
        if spec.restart_count >= self.MAX_RESTARTS:
            logger.error("supervisor.giving_up", process=spec.name)
            return False
        if time.monotonic() - spec.last_start < 10.0 and spec.restart_count >= 2:
            return False
        return True

    def _retire(self, spec: ProcessSpec, rc: int | None) -> None:
        spec.retired = True
        spec.proc = None
        # A child that leaves quietly did its job or found it already done — the
        # bridge exiting 0 because the port is taken, say — which is not a failure
        # and must not be logged as one.
        if spec.optional and rc == 0:
            logger.info("supervisor.child_done", process=spec.name)
            return
        # Said once, not on every poll: a child that will not be restarted stays
        # dead, and re-announcing it every two seconds is a log nobody can read.
        # An optional child's death is a warning — the rest of the agent is still
        # up, and the one thing missing is named.
        log = logger.warning if spec.optional else logger.error
        log("supervisor.child_dead", process=spec.name, rc=rc)

    def _sweep(self) -> None:
        """One pass over every child: what died, what never started, what to retry."""
        now = time.monotonic()
        for spec in self.specs:
            if self._stopping or spec.retired:
                continue
            if spec.proc is not None and spec.proc.poll() is None:
                continue
            rc = spec.proc.returncode if spec.proc is not None else None
            if spec.proc is not None:
                spec.proc = None
            elif now < spec.next_attempt:
                continue
            # A child that never started is on the same footing as one that died:
            # both are absent, and both are the supervisor's to put back.
            if not self._should_restart(spec):
                self._retire(spec, rc)
                continue
            spec.restart_count += 1
            delay = self.BACKOFF_S * (2 ** min(spec.restart_count, 4))
            logger.warning(
                "supervisor.restarting", process=spec.name, rc=rc, delay=round(delay, 1),
            )
            spec.next_attempt = now + delay
            spec.last_start = now
            self._open(spec)

    def _write_pid_file(self) -> None:
        try:
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
        except OSError as exc:
            # Not fatal: the supervisor still runs, it just cannot be stopped by
            # a pid. Said rather than raised, because a read-only run directory is
            # no reason to refuse to run the agent.
            logger.warning("supervisor.pid_file_failed", path=str(self.pid_file), reason=str(exc))

    def _remove_pid_file(self) -> None:
        """Removes the pid file, but only while it is still ours.

        A supervisor that was killed rather than asked to stop cannot remove its
        own, so the next one finds a pid that may be anybody's. It can tell: a pid
        that is not running is not a supervisor, and a pid that belongs to some
        other program is not ours to delete.
        """
        try:
            recorded = int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        if recorded != os.getpid():
            return
        try:
            self.pid_file.unlink()
        except OSError:
            pass

    def read_pid(self) -> int | None:
        """The pid of a supervisor that is running right now, or `None`.

        The liveness check is what makes the pid file usable from another process.
        A file left behind by a `kill -9` reads as a supervisor for ever, and a
        launcher that trusts it either refuses to start a second copy or signals a
        pid the operating system has since handed to somebody else.

        Delegated to `ethos.platform.process_is_alive` because the POSIX
        expression does not survive the trip. `os.kill(pid, 0)` is a probe only on
        POSIX; on Windows CPython hands any signal value other than
        `CTRL_C_EVENT` / `CTRL_BREAK_EVENT` straight to `TerminateProcess`, so the
        same line that asked whether the supervisor was alive would have ended it —
        and `ethos status` would report a running agent as gone while killing it,
        and `ethos up` would then start a second supervisor to fight the first.
        """
        try:
            recorded = int(self.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        if recorded <= 0:
            return None
        return recorded if process_is_alive(recorded) else None

    def _close_children(self) -> None:
        """Stop every child, giving each the grace period it can actually get.

        `terminate()` → wait up to `SHUTDOWN_GRACE_S` → `kill()`, unchanged in
        shape, and the two phases mean the same thing on both platforms because
        `Popen.poll()` calls `GetExitCodeProcess` on Windows: the wait is real, and
        a child that finished on its own is noticed rather than killed.

        What is *not* the same is the first phase. `Popen.terminate()` is
        `TerminateProcess` on Windows — an immediate hard kill, with no handler
        and no chance to close a connection pool — and on POSIX it is `SIGTERM`,
        which a child may handle. So on Windows the children do not get a graceful
        shutdown, and that is said in the log rather than left to look like one:
        the supervisor's own shutdown still runs, and the children's journals live
        in PostgreSQL, which reclaims an abandoned connection without help.

        Fixing this properly means the children each watching for a stop request of
        their own, which is a change to six commands' start-up and not a portability
        fix. It is named in the log instead, and flagged in the handover.
        """
        if IS_WINDOWS:
            logger.warning(
                "supervisor.children_hard_stopped",
                detail=(
                    "Windows has no SIGTERM; each child is TerminateProcess'd, so it "
                    "does not close its database connections on the way out. The "
                    "supervisor's own shutdown below still completes."
                ),
            )
        for spec in reversed(self.specs):
            if spec.proc is not None and spec.proc.poll() is None:
                spec.proc.terminate()
        deadline = time.monotonic() + SHUTDOWN_GRACE_S
        for spec in self.specs:
            if spec.proc is None:
                continue
            while spec.proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if spec.proc.poll() is None:
                spec.proc.kill()

    def _request_stop(self) -> None:
        """What a signal does: notes the request, nothing else.

        The shutdown itself belongs to `run`, which is the only thing that knows
        when its children were started. Run as a task of its own it could race
        the loop's exit, and the loser is the child that was mid-`terminate`.
        """
        self._stopping = True

    async def run(self) -> None:
        self.build_specs()
        loop = asyncio.get_running_loop()
        if os.name == "posix":
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._request_stop)
        self._write_pid_file()
        # A stop request left behind by a supervisor that was killed rather than
        # asked is not a request for *this* one, and acting on it would make a
        # freshly started agent shut itself down within two seconds of `ethos up`
        # reporting it as running. Removed once, here, after this process has
        # claimed the pid file and before any child exists — so there is no window
        # in which a real `ethos stop` from the previous run is dropped, and none
        # in which this run's own request is deleted either.
        self._clear_stop_file()
        logger.info("supervisor.running", processes=[spec.name for spec in self.specs])
        for spec in self.specs:
            self._open(spec)
            await asyncio.sleep(0.3)
        try:
            while not self._stopping:
                await asyncio.sleep(self.POLL_S)
                if self._stop_requested():
                    # The same method a POSIX signal calls, so everything after a
                    # stop is the same code whichever platform asked for it. Only
                    # reachable where a signal cannot be, and the loop was already
                    # running: this costs no new thread and no new timer.
                    self._request_stop()
                    continue
                self._sweep()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if self._shut_down:
            return
        self._stopping = True
        self._shut_down = True
        logger.info("supervisor.shutting_down")
        # The children are waited for with blocking calls, so they go to a thread:
        # on the event loop the ten-second grace period would be ten seconds in
        # which a second Ctrl-C does nothing, which is the least useful moment to
        # find out about it.
        await asyncio.get_running_loop().run_in_executor(None, self._close_children)
        self._remove_pid_file()
        # A request that was honoured is not left lying about. A request that was
        # *not* honoured is also cleared, because this supervisor is gone either
        # way and the next one starting is not a stop request.
        self._clear_stop_file()
        logger.info("supervisor.stopped")


async def run_supervisor(config: EthosConfig | None = None) -> None:
    config = config or load_config()
    configure_logging(level=os.environ.get("ETHOS_LOG_LEVEL", "INFO"))
    config.paths.ensure()
    supervisor = Supervisor(config)
    await supervisor.run()
