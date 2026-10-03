"""`ethos up`: the one command, and the only place the start-up sequence is written.

The sequence used to live in `./start.sh` — 330 lines of bash that created the
virtualenv, installed dependencies, brought up Postgres, migrated, seeded, found
Node, installed and built the interface, ran a health check, started the agent
detached, waited for it to answer, and opened a page. It worked, and it was the
wrong place for it. A shell script can only be run from the checkout that holds
it, so the command everybody was told to type did not exist for anybody who had
`pip install`ed the package — and the console script that `pip install` *does*
create, `ethos`, had no way to do any of it. One job, two implementations, and
the one in the package was the smaller one.

So the sequence is here now, in the module the commands it runs already live
next to, and `./start.sh` is a shim that hands over to it. The parts that cannot
move are still in bash: building a virtualenv needs `uv` before there is an
interpreter to ask, so the shim does that much and nothing else.

**What is delegated rather than reimplemented.** Every step that already exists as
a command — the migrations, the seed, the health check, the wait for a sign of
life, the status report — is run as a subprocess of `ethos` itself rather than
re-derived here. A second implementation of the seed is a second answer to "what
happens on a fresh machine", and the two would agree only until one of them was
edited. What is left in this module is the part that had no command: locating the
checkout, the local Postgres script, the interface toolchain, and starting the
supervisor detached from the terminal that asked for it.

**Where things are resolved from.** Every path here is derived from the installed
package (`ethos/__init__.py`'s own location), never from the working directory.
`ethos up` is documented as a command anybody can type from anywhere, and a
command that only works from the directory it was installed into is not that. The
checkout is found by looking for `config/ethos.yaml` beside the package, and its
absence is a sentence naming the fix rather than a `FileNotFoundError` from a
`npm install` four steps later.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from ethos.config import EthosConfig, load_config
from ethos.core.status import agent_liveness, interface_health, interface_port
from ethos.core.supervisor import node_tool
from ethos.platform import detached_popen_kwargs

# The database the local cluster is created at, and the one
# `scripts/ensure_postgres.sh` binds `127.0.0.1:5433` to. Set here rather than
# left to the shell script because this is the command a person now types, and a
# DSN that only exists inside a wrapper script is a DSN `ethos status` and
# `ethos doctor` cannot see — so the two halves of one install could disagree
# about which database the agent is on. An explicit `ETHOS_DSN` still wins.
DEFAULT_DSN = "postgresql://ethos:ethos@127.0.0.1:5433/ethos"

# How long to wait for the agent's first sign of life. Generous on purpose: it
# loads its embedder and reconciles its journals before its first cycle, and a
# start-up that gives up while the agent is still waking teaches the reader that
# this command cannot be trusted to start anything.
DEFAULT_WAIT_S = 180.0

SUPERVISOR_LOG_NAME = "supervisor.log"

# How many lines of each log `ethos logs` shows before it starts following. The
# last forty is what `tail -n 40 -f` showed, which is the window a person wants
# at the moment they ask: enough to see what just happened, not so much that the
# scrollback hides it.
LOG_TAIL_LINES = 40

EXIT_OK = 0
EXIT_FAILED = 1
# 128 + SIGINT, which is the exit code a shell reports for a Ctrl-C, and is what
# a caller distinguishing "the user stopped waiting" from "the start-up failed"
# will be looking for.
EXIT_INTERRUPTED = 130


class BootstrapError(RuntimeError):
    """A start-up that cannot go on, phrased as the one thing to fix."""


def say(message: str) -> None:
    """Progress, on stdout, in the shape the rest of the CLI prints things."""
    print(f"-- {message}", flush=True)


def warn(message: str) -> None:
    print(f"-- {message}", file=sys.stderr, flush=True)


def checkout_root() -> Path | None:
    """The checkout this package was installed from, or `None` outside one.

    Found by the shipped configuration sitting beside the package, not by walking
    up from the working directory: an editable install points at the checkout, so
    the package's own location *is* the answer, and asking a caller where they
    happened to be standing is how a command ends up operating on a different
    install than the one it was invoked from.
    """
    root = Path(__file__).resolve().parent.parent.parent
    return root if (root / "config" / "ethos.yaml").is_file() else None


def require_checkout_root() -> Path:
    """The checkout, or an error that says what to do about not having one.

    The agent is configured by files that ship with the source and are not
    importable data — `config/*.yaml`, the interface bridge in `web/`, and the
    database script in `scripts/` — so there is no install of this package that
    starts without them. Rather than let that surface as a missing `npm` or an
    unparseable DSN four steps into a start-up, it is refused at the door.

    `ETHOS_CONFIG_DIR` is deliberately not offered as the way out: it chooses
    *which* configuration a checkout reads, so pointing it somewhere else cannot
    supply the checkout that is missing. An error that suggests it would be
    advice about a dependency the machine does not lack, which is the class of
    wrong answer this repository spends a lot of its README warning about.
    """
    root = checkout_root()
    if root is None:
        raise BootstrapError(
            f"no ethos checkout found beside the installed package "
            f"({Path(__file__).resolve().parent}).\n"
            "   This agent is configured by files that ship with the source —\n"
            "   config/*.yaml, the interface bridge in web/, and the database\n"
            "   script in scripts/ — so it runs from a checkout, not from a wheel.\n"
            "   Install it editable from a clone, and run `ethos up` from there or\n"
            "   from anywhere:\n"
            "     pip install -e /path/to/Ethos"
        )
    return root


def _run(argv: list[str], *, root: Path | None = None, check: bool = True) -> int:
    """Run one of the agent's own commands, from the checkout, and report its code.

    `sys.executable -m ethos` rather than the `ethos` on `PATH`: the interpreter
    running this is the one the agent's dependencies are installed into, and a
    start-up that shells out to whatever `ethos` resolves to on a machine with
    two installs is a start-up that migrates one database and starts another.
    """
    completed = subprocess.Popen(
        [sys.executable, "-m", "ethos", *argv],
        cwd=str(root) if root else None,
    )
    try:
        code = completed.wait()
    except KeyboardInterrupt:
        # The child is a step of this start-up, not a detached agent: leaving it
        # running would mean a `ethos await-agent` still polling after the person
        # who asked it to stop had gone, and a Ctrl-C that appears to work while
        # a subprocess quietly carries on is worse than one that visibly does not.
        completed.terminate()
        try:
            completed.wait(timeout=5)
        except subprocess.TimeoutExpired:
            completed.kill()
        raise
    if check and code != 0:
        raise BootstrapError(f"`ethos {' '.join(argv)}` failed with exit code {code}")
    return code


def _running_pid(root: Path) -> int | None:
    """The supervisor's pid, if one is running right now.

    Asked as a question rather than read out of the pid file, because
    `ethos status --pid` is the one place that checks the recorded pid is still a
    live process: a file left behind by a `kill -9` reads as a supervisor for
    ever, and a launcher that trusted it would refuse to start an agent that
    would have started fine. Its output is captured rather than inherited, since
    a bare pid in the middle of a start-up report reads as a line of the report.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "ethos", "status", "--pid"],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


def _default_dsn() -> str:
    return os.environ.get("ETHOS_DSN") or DEFAULT_DSN


def _ensure_postgres(root: Path) -> None:
    """The one hard dependency, brought up rather than assumed.

    The script is idempotent: an already-answering database at the DSN is left
    alone, and only a missing one is installed and started — via docker when
    docker is here, otherwise a local PostgreSQL 16 + pgvector in `data/pgdata`.
    """
    script = root / "scripts" / "ensure_postgres.sh"
    if not script.is_file():
        raise BootstrapError(
            f"{script} is missing, so the database cannot be prepared.\n"
            "   Run this from a full checkout, or point ETHOS_DSN at a PostgreSQL 16\n"
            "   with pgvector and pg_trgm that is already running."
        )
    if not os.access(script, os.X_OK):
        script.chmod(script.stat().st_mode | 0o111)
    completed = subprocess.run([str(script), "up"], cwd=str(root), check=False)
    if completed.returncode != 0:
        raise BootstrapError(
            f"PostgreSQL could not be brought up at {_default_dsn()} "
            f"(scripts/ensure_postgres.sh exited {completed.returncode})"
        )


def _prepare_interface(root: Path, log_dir: Path) -> bool:
    """Install and build the interface, and report whether the page will exist.

    Node is the interface's toolchain and not the agent's, so a machine without
    it still gets a working agent: the bridge serves the whole API without the
    page. What is not acceptable is finding that out halfway through a start-up,
    so the toolchain is looked for once, here, and everything after this point
    knows which of the two worlds it is in.

    `node` and `npm` are looked for separately because they are not always in the
    same place — an `nvm install` can leave a version with a runtime and no
    package manager beside it — and because the bridge is started with `node`
    directly: `npm run start` would need `node` on `PATH` a second time, inside a
    child nobody is watching.
    """
    npm = node_tool("npm")
    if npm is None:
        warn("no Node.js found: the agent will run, the page will not be served")
        warn(f"  install Node, or run the bridge yourself once you have: node {root / 'web' / 'server' / 'index.mjs'}")
        return False

    log_dir.mkdir(parents=True, exist_ok=True)
    web = root / "web"
    interface = root / "interface"

    if not (web / "node_modules").is_dir():
        say("installing interface bridge dependencies")
        bridge_log = log_dir / "bridge-install.log"
        if _npm_install(npm, web, bridge_log) != 0:
            warn(f"npm install failed — see {bridge_log}")

    if interface.is_dir():
        if not (interface / "node_modules").is_dir():
            say("installing interface dependencies")
            interface_log = log_dir / "interface-install.log"
            if _npm_install(npm, interface, interface_log) != 0:
                warn(f"npm install failed — see {interface_log}")
        if not (interface / "packages" / "web" / "dist" / "index.html").is_file():
            say("building the interface")
            build_log = log_dir / "interface-build.log"
            with build_log.open("ab") as fh:
                built = subprocess.run(
                    [npm, "run", "build", "--workspace", "@project-phone/core", "--prefix", str(interface)],
                    check=False, stdout=fh, stderr=subprocess.STDOUT,
                ).returncode == 0 and subprocess.run(
                    [npm, "run", "build", "--workspace", "@project-phone/web", "--prefix", str(interface)],
                    check=False, stdout=fh, stderr=subprocess.STDOUT,
                ).returncode == 0
            if not built:
                warn(f"the interface did not build — see {build_log}")
                return False

    # The supervisor starts the bridge itself, with this exact interpreter, so
    # the child cannot end up on a different Node than the one its dependencies
    # were installed with.
    node = node_tool("node")
    if node:
        os.environ["ETHOS_NODE"] = node
    # Whether the page will exist, asked of the build rather than assumed from the
    # fact that the steps ran: a checkout with no `interface/` in it, or one whose
    # build was already there from an earlier version, both finish this function
    # without an index.html. The caller uses this answer to decide whether to
    # print "open <url>", and a start-up that opens a browser onto a 503 is the
    # same class of wrong answer as one that reports an agent that is not there.
    return (interface / "packages" / "web" / "dist" / "index.html").is_file()


def _npm_install(npm: str, prefix: Path, log: Path) -> int:
    with log.open("ab") as fh:
        return subprocess.run(
            [npm, "install", "--prefix", str(prefix)],
            check=False, stdout=fh, stderr=subprocess.STDOUT,
        ).returncode


def _start_supervisor(root: Path, log_dir: Path) -> tuple[int, Path]:
    """Start the agent, detached from the terminal that asked for it.

    `detached_popen_kwargs()` is the whole of it, and on POSIX it is
    `start_new_session=True`: the Python spelling of the `nohup … & disown` this
    used to do, which gives the agent its own session so closing the terminal that
    started it — or logging out — sends it no hangup. An agent that dies with the
    terminal that started it is the same failure the one-command start-up exists to
    remove, and it is why a page opened from one terminal used to go dead when that
    terminal was closed.

    Windows needs a different spelling and was getting this one, which is why the
    kwargs are asked for rather than written out: CPython's Windows branch of
    `subprocess.Popen` names the parameter `unused_start_new_session` and its
    docstring says "start_new_session (POSIX only)". A keyword accepted and ignored
    is worse than one rejected, because the process *starts* — detached from
    nothing, inheriting a console, and killed by the window close that ends the
    terminal, with a start-up report that said it was running.

    The supervisor writes its own pid file and reaps its children, so `ethos stop`
    is the whole of the shutdown story.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / SUPERVISOR_LOG_NAME
    with log_path.open("ab") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "ethos", "supervisor"],
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=fh,
            cwd=str(root),
            env={**os.environ, "ETHOS_DSN": _default_dsn()},
            **detached_popen_kwargs(),
        )
    # A supervisor that died inside its first second must not be reported as a
    # start. One second is not a guess at how long start-up takes; it is the
    # window in which a missing interpreter, an unimportable dependency or a bad
    # config surfaces, and all three surface before it.
    time.sleep(1.0)
    if proc.poll() is not None:
        tail = _tail(log_path, 20)
        raise BootstrapError(
            f"the agent did not start (supervisor exited {proc.returncode}). "
            f"Its supervisor said:\n{tail}"
        )
    return proc.pid, log_path


def _tail(path: Path, lines: int) -> str:
    """The last few lines of a file, or a sentence saying there are none."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"(could not read {path}: {exc})"
    return "\n".join(content[-lines:]) or "(nothing logged yet)"


def log_paths(config: EthosConfig, root: Path | None = None) -> list[Path]:
    """Every log a running agent writes, the supervisor's first.

    One directory — the agent's own — rather than a second one in the checkout,
    because a log whose location depends on where the command was typed from is a
    log that cannot be found. A `supervisor.log` in the checkout's `data/logs` is
    still followed when one is there: that is where the retired `./start.sh`
    wrote it, and somebody upgrading mid-run should not lose the thread.
    """
    log_dir = Path(config.paths.data_dir) / "logs"
    found: list[Path] = []
    supervisor = log_dir / SUPERVISOR_LOG_NAME
    if supervisor.is_file():
        found.append(supervisor)
    found += sorted(p for p in log_dir.glob("ethos-*.log") if p.is_file())
    if root is not None:
        legacy = root / "data" / "logs" / SUPERVISOR_LOG_NAME
        if legacy.is_file() and legacy not in found:
            found.append(legacy)
    return found


def _await_ready(
    config: EthosConfig,
    root: Path,
    wait_s: float,
    *,
    started_here: bool,
) -> bool:
    """Whether the agent is there — asked the right way for who started it.

    `ethos await-agent` asks the sharper question a launcher can ask: *has it said
    anything since this command began*. Freshness is what proves a start worked,
    because presence inside the window only proves the agent was there at some
    point in the last quarter of an hour.

    So it is the right question for an agent this command started, and the wrong
    one for an agent that was already running: that one published its last sign of
    life up to a whole presence window ago and will not publish again until its
    next cycle, so waiting for a fresh one reports a healthy agent as one that
    failed to start — and a start-up that says that about a working agent is a
    start-up nobody believes afterwards. An agent that was already there is asked
    whether it is there, and only falls through to the wait when the answer is no.
    """
    if not started_here and agent_liveness(config)[0]:
        return True
    return _run(["await-agent", f"{wait_s:g}"], root=root, check=False) == 0


def _raise_on_signal(*signals: str) -> None:
    """Make Ctrl-C interrupt this process, however it was started.

    A process backgrounded from a non-interactive shell — a `cron` entry, a
    service manager, another program's `subprocess` — inherits `SIGINT` set to
    `SIG_IGN`, and CPython leaves an ignored signal ignored rather than
    installing its own handler. So the default is a process that ignores Ctrl-C
    in exactly the places nobody is sitting at a keyboard to send it some other
    way, which is the opposite of what a start-up should be. Asking for the
    handler explicitly is what makes the behaviour the same in a terminal and
    everywhere else.
    """
    def handler(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    for name in signals:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, handler)
        except (OSError, ValueError):
            # Not the main thread, or a platform without it. Nothing to arrange
            # then, and nothing to report: the default disposition still stands.
            pass


def bring_up(
    *,
    wait_s: float = DEFAULT_WAIT_S,
    open_browser: bool = True,
    config: EthosConfig | None = None,
) -> int:
    """Prepare the machine, start the agent, and wait until it answers.

    Returns a process exit code rather than raising for the ordinary failures, so
    the caller is a command with one job: report what happened and exit with the
    number a script or a supervisor will read. `BootstrapError` is for the
    failures that are not ordinary — a missing checkout, a database that will not
    come up — and carries the sentence that fixes them.
    """
    root = require_checkout_root()
    _raise_on_signal("SIGINT", "SIGTERM")
    # Before `load_config`, because the DSN is read into the configuration and
    # every later step — the migrations, the seed, the wait, the status line —
    # has to be asking about the same database the local cluster was just made
    # for.
    os.environ["ETHOS_DSN"] = _default_dsn()
    config = config or load_config()
    log_dir = Path(config.paths.data_dir) / "logs"

    say(f"agent home: {config.paths.home}")
    _ensure_postgres(root)

    say("applying migrations")
    _run(["db", "upgrade"], root=root)

    say("seeding")
    # A seed that fails is survivable — the agent seeds itself on an empty
    # `self_model` — so this is reported and stepped over rather than taken as a
    # reason to refuse to start.
    _run(["db", "seed"], root=root, check=False)

    interface_built = _prepare_interface(root, log_dir)

    say("doctor")
    _run(["doctor"], root=root, check=False)

    # Already up? Say so and go straight to the report, rather than starting a
    # second seven-process agent that would then fight the first one for a port,
    # a socket and a gateway.
    existing_pid = _running_pid(root)
    supervisor_log = log_dir / SUPERVISOR_LOG_NAME
    if existing_pid is not None:
        say(f"the agent is already running (supervisor pid {existing_pid})")
    else:
        say("starting the agent")
        supervisor_log = _start_supervisor(root, log_dir)[1]

    say(f"waiting for the agent to answer (up to {wait_s:g}s)")
    try:
        ready = _await_ready(config, root, wait_s, started_here=existing_pid is None)
    except KeyboardInterrupt:
        _interrupted()
        return EXIT_INTERRUPTED

    print("", flush=True)
    _run(["status"], root=root, check=False)
    print("", flush=True)

    if not ready:
        where = []
        if supervisor_log.is_file():
            where.append(f"  {supervisor_log}          what the supervisor started, and what it lost")
        where.append(f"  {log_dir}/   what each process said — `ethos logs` follows both")
        print(
            "\n".join(["The agent did not come up. These are the two logs that say why:", *where]),
            file=sys.stderr,
            flush=True,
        )
        return EXIT_FAILED

    say("the agent is running")
    url = f"http://127.0.0.1:{interface_port(config)}"
    _, interface_up, _ = interface_health(config)
    if interface_built and interface_up:
        print(f"  open {url}                the agent, in a browser", flush=True)
        if open_browser and not os.environ.get("ETHOS_NO_OPEN"):
            _open(url)
    else:
        print(f"  the browser page is not being served; the bridge answers at {url} when it is")
    # The interface's own commands are only worth suggesting where the interface
    # is: a deployment that never cloned it should not be told about a terminal
    # client it cannot run.
    if (root / "interface" / "package.json").is_file():
        print("  npm run tui --workspace @project-phone/cli --prefix interface   the agent, in a terminal")
        print("  phone channels                        which doors are open, and what each is missing")
    # Said here because this is the screen a person reads after `ethos up` and
    # before they go looking for Telegram, and because the answer is a place
    # rather than a sentence: the doors are configured in the settings screen,
    # in `phone channels set`, and in the agent's own home. The `doctor` run
    # above already said which of them are open and what each is missing.
    print("  settings → Messaging                 open a door (Telegram, WhatsApp, Slack, Discord, email)")
    print("  ethos stop                            stop the agent")
    print("  ethos logs                            follow its logs")
    print("", flush=True)
    return EXIT_OK


def _open(url: str) -> None:
    """The page, if a browser can be asked for. Never a reason to fail a start-up."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _interrupted() -> None:
    print(
        "\ninterrupted. The agent you started keeps running — `ethos status` to ask "
        "whether it came up, `ethos stop` to end it.",
        file=sys.stderr,
        flush=True,
    )


def follow_logs(*, config: EthosConfig | None = None, from_start: bool = False) -> int:
    """Follow the supervisor's log and every process's own, the way `tail -F` does.

    A follower rather than a subprocess because there is no portable `tail -F`:
    this has to work on the machine the agent runs on, and shelling out to a flag
    that does not exist on BSD would make the one diagnostic command the least
    portable thing in the repository.
    """
    config = config or load_config()
    _raise_on_signal("SIGINT", "SIGTERM")
    paths = log_paths(config, checkout_root())
    if not paths:
        print(
            f"no logs yet — {Path(config.paths.data_dir) / 'logs'} is written when the "
            "agent starts (`ethos up`)",
            file=sys.stderr,
        )
        return EXIT_FAILED

    handles: dict[Path, object] = {}
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            warn(f"could not follow {path}: {exc}")
            continue
        if from_start:
            for line in handle:
                sys.stdout.write(f"{path.name}: {line}")
        else:
            handle.seek(0, 2)
        handles[path] = handle

    if not handles:
        print("no logs could be opened", file=sys.stderr)
        return EXIT_FAILED

    try:
        while True:
            for path, handle in list(handles.items()):
                line = handle.readline()
                if line:
                    sys.stdout.write(f"{path.name}: {line}")
                    continue
                # An empty read is either "nothing new" or "the file was rotated
                # out from under this handle", and the two are told apart by the
                # file being shorter than the position this handle is parked at.
                try:
                    if path.stat().st_size < handle.tell():
                        handle.seek(0)
                except OSError:
                    handles.pop(path, None)
                    handle.close()
                    continue
            sys.stdout.flush()
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("", flush=True)
        return EXIT_OK
