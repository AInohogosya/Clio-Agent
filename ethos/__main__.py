from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from pathlib import Path
from typing import Any

import typer

from ethos import PRODUCT_NAME, RELEASE_DATE, RELEASE_TIMEZONE
from ethos.config import EthosConfig, load_config
from ethos.platform import IS_WINDOWS, SUPPORTS_UNIX_SOCKETS, hard_kill_pid, install_fast_loop

app = typer.Typer(
    name="ethos",
    help=f"{PRODUCT_NAME}: a continuously existing, general-purpose autonomous agent.",
)
db_app = typer.Typer(help="Database operations.")
app.add_typer(db_app, name="db")


def _release_line() -> str:
    """The one-line answer to "which build is this", shared by every spelling.

    The product name carries the version already, so it is printed rather than
    repeated next to it: `Clio Agent 3 Beta 1` is the version, and a second "3 Beta 1"
    beside it would be a second thing to keep in step. The date is stated rather than
    left to the file's mtime because a build copied between machines has the mtime of
    the copy, and a release date is a fact about the release.
    """
    return f"{PRODUCT_NAME} — released {RELEASE_DATE} ({RELEASE_TIMEZONE})"


def _version_option(value: bool) -> None:
    """`--version`, which every tool that installs a command is expected to answer.

    Separate from the `version` subcommand rather than replacing it: both spellings
    are in use, a script reads one and a person types the other, and there is no
    reason to make either of them an error.
    """
    if value:
        typer.echo(_release_line())
        raise typer.Exit(0)


@app.callback()
def cli(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="print the version and exit",
        is_eager=True,
        callback=_version_option,
    ),
    config_dir: str | None = typer.Option(
        None,
        "--config-dir",
        "-c",
        metavar="DIR",
        help="where to read config/*.yaml from (default: $ETHOS_CONFIG_DIR, then the checkout's config/)",
    ),
    home: str | None = typer.Option(
        None,
        "--home",
        metavar="DIR",
        help="the agent's own home, for memory, journals and sockets (default: $ETHOS_HOME, then ~/.ethos)",
    ),
) -> None:
    """Clio Agent 3 Beta 1: a continuously existing, general-purpose autonomous agent.

    The one command that brings the agent up is `ethos up`; `ethos status` asks
    whether it is there, `ethos stop` ends it, `ethos logs` follows it, and
    `ethos doctor` says what is configured and what is missing.
    """
    # Written back into the environment rather than threaded through every
    # command's signature: `load_config()` already resolves both of these from the
    # environment, and a second route to the same setting is a second answer to
    # "which directory is this reading", which is the question that has to have
    # one.
    if config_dir is not None:
        # A named directory that is not there is a typo, and `load_config` reads a
        # missing section file as "no overrides" — so without this the agent would
        # start on defaults, against a database and a channel configuration
        # nobody chose, and every later answer would be confidently wrong. Said
        # only for the flag, which somebody typed: a deployment's `ETHOS_CONFIG_DIR`
        # is a default, and a default that is wrong is the deployment's business.
        if not Path(config_dir).expanduser().is_dir():
            typer.secho(
                f"ethos: --config-dir {config_dir} is not a directory.\n"
                "       The agent's configuration ships with the source; point this at\n"
                "       a checkout's config/ directory, or drop the flag to use it.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)
        os.environ["ETHOS_CONFIG_DIR"] = config_dir
    if home is not None:
        os.environ["ETHOS_HOME"] = home


@app.command()
def version() -> None:
    typer.echo(_release_line())


@app.command()
def up(
    wait: float = typer.Option(
        180.0,
        "--wait",
        "-w",
        metavar="SECONDS",
        envvar="ETHOS_AGENT_WAIT_S",
        help="give up waiting for the agent after this long (default: 180)",
    ),
    open_page: bool = typer.Option(
        True,
        "--open/--no-open",
        envvar="ETHOS_NO_OPEN",
        help="open the interface in a browser once the agent is up",
    ),
) -> None:
    """Prepare the machine, start the agent, wait for it, open the page.

    The recommended way to run the agent, and the one this package installs: one
    command, from any directory, in a checkout. It brings up Postgres if nothing
    is answering at `$ETHOS_DSN`, applies migrations, seeds, installs and builds
    the interface, runs a health check, starts the seven supervised processes
    detached from this terminal, and waits until the agent has actually published
    a sign of life — because a process that started is not an agent that came up,
    and a start-up that reports the first as the second teaches the reader that
    this command cannot be trusted.

    Idempotent: run it on a running agent and it says so and leaves it alone.
    Exits 0 when the agent is up, 1 when it is not, 130 on Ctrl-C — in which case
    the agent it started is still running, because it was started in its own
    session on purpose.

    `./start.sh` still works and is deprecated: it creates the virtualenv (which
    needs `uv`, and so cannot be done from inside an installed package) and then
    runs this.
    """
    from ethos.core.bootstrap import EXIT_FAILED, BootstrapError, bring_up

    try:
        code = bring_up(wait_s=wait, open_browser=open_page)
    except BootstrapError as exc:
        typer.secho(f"ethos: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(EXIT_FAILED) from None
    raise typer.Exit(code)


@app.command()
def logs(
    from_start: bool = typer.Option(
        False,
        "--from-start",
        help="print each log from its first line rather than from its tail",
    ),
) -> None:
    """Follow the supervisor's log and every process's own, like `tail -F`.

    Both in one stream, with each line prefixed by the process that wrote it: the
    supervisor's account of what it started and lost, and the children's account
    of what they thought about it. A question about a symptom needs both, and
    two terminals is one too many for a person holding a phone.
    """
    from ethos.core.bootstrap import follow_logs

    raise typer.Exit(follow_logs(from_start=from_start))


@app.command()
def core(
    listen_channels: bool = typer.Option(
        False,
        "--listen-channels",
        help="also hold the channel doors open (Telegram, Discord, email, the webhook port)",
    ),
) -> None:
    """Run ethos-core: the Self, the life loop, and embedded services.

    `--listen-channels` is for the single-process development run, where there is
    no `ethos-comms` beside it to do the listening. Under the supervisor the flag
    must stay off: `ethos-comms` is already holding every door, and a core that
    opened them too would put a second long-poll on one Telegram bot — which
    Telegram answers with a conflict for as long as the first is alive, leaving a
    channel that looks configured and receives nothing. So the supervisor starts
    the core without it and this process is the only one that ever listens.
    """
    from ethos.core.runner import run_core

    install_fast_loop()
    asyncio.run(run_core(listen_channels=listen_channels))


@app.command()
def gateway() -> None:
    """Run ethos-gateway: model routing service on its unix socket."""
    from ethos.config import load_config
    from ethos.core.runner import connect_database
    from ethos.db import Database
    from ethos.gateway.service import serve

    install_fast_loop()
    config = load_config()
    config.paths.ensure()
    db: Database | None = None
    if os.environ.get("ETHOS_NO_DB") != "1":
        db = asyncio.run(connect_database(config))
    asyncio.run(serve(config, db))


@app.command()
def toolhost() -> None:
    """Run ethos-toolhost: tool execution service under the guardian gate."""
    from ethos.config import load_config

    install_fast_loop()
    config = load_config()
    config.paths.ensure()
    # One `asyncio.run`, and the database is connected inside it. This used to open
    # a loop to build the pool, close it, and open a second one to serve — which
    # leaves every pooled connection bound to a loop that no longer exists. It went
    # unnoticed for as long as nothing queried the pool during start-up, and then
    # stopped the process dead the moment something did: asyncpg refuses a query on
    # a connection another loop is driving, so this raised on the first read rather
    # than on the first write. The connection belongs to the loop that will use it.
    asyncio.run(_serve_toolhost(config))


async def _serve_toolhost(config) -> None:
    import uvicorn

    from ethos.bus.event_bus import EventBus
    from ethos.core.control import ControlPlane
    from ethos.core.runner import connect_database
    from ethos.guardian.gate import GuardianGate, ModelDueProcessVerifier
    from ethos.guardian.journal import ActionJournal
    from ethos.guardian.registry import ArtifactRegistry
    from ethos.guardian.snapshots import SnapshotManager
    from ethos.guardian.trash import TrashStore
    from ethos.memory.store import MemoryStore
    from ethos.observability.audit import AuditLog, DbAuditSink
    from ethos.schemas.events import EventKind
    from ethos.toolhost.server import Toolhost, create_toolhost_app

    db = await connect_database(config)

    # This gate used to be built with no control plane at all, which is defensible
    # only for the process it was written for: it was assumed that the core holds
    # the gate that matters and this one is a service for other callers. But the
    # supervisor starts this as one of seven processes, so every tool that arrives
    # over the socket was being executed by a gate that could not see whether the
    # agent had been stopped — or put in preview mode. A gate that gates nothing is
    # worse than no gate, because it is the one place a refusal could still have
    # been made. So it reads the same lifecycle row the core does, and listens on
    # the same bus, which is what makes the flag current rather than true only
    # until the next restart.
    #
    # `owns_row=False` because this process obeys the lifecycle rather than being
    # the one that defines it. Without it, every restart of *this* process would
    # rewrite the row with its own `stopped: false` — over a stop the agent was
    # really in, on a restart the supervisor performs without anybody asking.
    control = ControlPlane(db, str(config.paths.run_dir), owns_row=False)
    await control.load()
    bus = EventBus(db, source="toolhost")

    async def _on_control_update(event: Any) -> None:
        if event.kind != EventKind.CONTROL_UPDATE:
            return
        await control.apply_command(
            event.payload.get("action", ""), event.payload.get("by", "bus"),
        )

    bus.subscribe(EventKind.CONTROL_UPDATE, _on_control_update)
    await bus.start()

    embedder = __import__("ethos.gateway.embeddings", fromlist=["build_embedder"]).build_embedder(
        config.gateway.embedding.provider, config.gateway.embedding.model, config.gateway.embedding.dim,
    )
    memory = MemoryStore(db, embedder, config)
    audit = AuditLog(DbAuditSink(db))
    registry = ArtifactRegistry(db)
    trash = TrashStore(
        config.paths.trash, db=db,
        fast_retention_days=config.permissions.guardian.fast_path_retention_days,
        due_retention_days=config.permissions.guardian.due_process_retention_days,
    )
    snapshots = SnapshotManager(config, db=db)
    journal = ActionJournal(db=db)
    verifier = ModelDueProcessVerifier(None, config.self_name)
    gate = GuardianGate(config, registry=registry, trash=trash, snapshots=snapshots,
                         audit=audit, verifier=verifier, control=control)
    host = Toolhost(config, gate=gate, journal=journal, services={
        "memory": memory, "registry": registry, "trash": trash,
    })
    application = create_toolhost_app(host)
    # From `run_dir`, not from `config.gateway.socket_path`'s parent. The socket
    # the gateway binds and the socket the toolhost binds are two answers to one
    # question, and deriving the second from the first is how it ended up
    # hardcoded to `/run/ethos` in one place and `~/.ethos/run` in another.
    socket_path = str(config.paths.run_dir / "toolhost.sock")
    # `SUPPORTS_UNIX_SOCKETS` and not `os.access(dir, W_OK)` alone. The original
    # asked only whether the directory was writable, and on Windows it is: a
    # writable `run` directory is not the question, the missing `AF_UNIX` is. So
    # both branches below built a `uvicorn.Config(uds=...)`, uvicorn was asked for
    # an address family the platform does not have, and the toolhost — the process
    # that executes every tool call in the agent — died at start-up with a socket
    # error on a machine whose only problem was its address family.
    #
    # The TCP branch is the same shape the gateway already used for this, and the
    # reason it is a branch rather than a failure: the protocol between these two
    # processes is one JSON request over loopback, and the address is the only
    # thing that changes.
    if SUPPORTS_UNIX_SOCKETS and os.access(os.path.dirname(socket_path) or "/", os.W_OK):
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        config_server = uvicorn.Config(application, uds=socket_path, log_level="warning")
    else:
        config_server = uvicorn.Config(
            application, host="127.0.0.1", port=config.gateway.toolhost_port,
            log_level="warning",
        )
    server = uvicorn.Server(config_server)
    try:
        await server.serve()
    finally:
        # The listener connection is a real one and this process is long-lived, so
        # the shutdown that used to be implicit in interpreter exit is now spelled
        # out: a toolhost restarted under the supervisor closes the old socket and
        # the old LISTEN rather than leaving both for the next one to trip over.
        await bus.stop()


@app.command()
def comms() -> None:
    """Run ethos-comms: channel adapters (cli, web ws, telegram, whatsapp, email).

    Also the process that listens for the channels which cannot be polled — the
    webhook receiver, on one loopback port, which a tunnel forwards to.
    """
    from ethos.bus import EventBus
    from ethos.comms.host import CommsHost, inbound_to_bus
    from ethos.config import load_config
    from ethos.core.runner import connect_database
    from ethos.db import Database
    from ethos.observability.logger import configure_logging
    from ethos.social.deliberation import SocialCognition

    install_fast_loop()

    async def _run() -> None:
        config = load_config()
        configure_logging()
        config.paths.ensure()
        db: Database = await connect_database(config)
        bus = EventBus(db, source="comms")
        await bus.start()
        from ethos.memory.self_model import SelfModel

        social = SocialCognition(config, relationships=None, db=db,
                                self_model=SelfModel(db))
        host = CommsHost(config, on_inbound=inbound_to_bus(bus, social))
        await host.start()
        # The doors follow the configuration rather than being a snapshot of it, so a
        # token pasted into a settings screen is a token this process is holding a
        # poller for within a couple of seconds — and the operator watching a phone
        # is not being asked to restart the agent to go and look.
        watch = asyncio.create_task(host.watch_channels())
        try:
            await asyncio.Event().wait()
        finally:
            watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watch
            await host.stop()
            await bus.stop()
            await db.close()

    asyncio.run(_run())


@app.command()
def worker() -> None:
    """Run an ethos-workers process: claims delegated thread requests from the bus."""
    from ethos.bus import EventBus
    from ethos.config import load_config
    from ethos.core.runner import connect_database
    from ethos.observability.logger import configure_logging

    install_fast_loop()

    async def _run() -> None:
        config = load_config()
        configure_logging()
        db = await connect_database(config)
        bus = EventBus(db, source="worker")
        await bus.start()

        from uuid import UUID

        from ethos.gateway.client import DirectGateway
        from ethos.gateway.embeddings import build_embedder
        from ethos.gateway.service import GatewayService
        from ethos.memory.store import MemoryStore
        from ethos.schemas.gsl import IntentionStatus
        from ethos.threads.worker import run_thread

        embedder = build_embedder(config.gateway.embedding.provider,
                                  config.gateway.embedding.model,
                                  config.gateway.embedding.dim)
        memory = MemoryStore(db, embedder, config)
        gateway_service = GatewayService(config, db=db, embedder=embedder)
        gateway = DirectGateway(gateway_service)

        async def handle(event) -> None:
            if event.kind != "thread.request":
                return
            spec = dict(event.payload)
            thread_id = spec.get("id", "unknown")
            try:
                result = await run_thread(spec, config, memory, gateway)
                status, error = "done", None
            except Exception as exc:
                result, status, error = None, "failed", str(exc)
            intention_id = spec.get("intention_id")
            if intention_id:
                try:
                    await memory.close_intention(
                        UUID(str(intention_id)),
                        IntentionStatus.done.value if status == "done" else IntentionStatus.failed.value,
                        "worker completed" if status == "done" else f"worker failed: {error}",
                    )
                except Exception:
                    pass
            await bus.publish("thread.result", {
                "thread_id": thread_id,
                "goal": spec.get("goal"),
                "status": status,
                "result": result,
                "error": error,
                "intention_id": intention_id,
            })

        bus.subscribe("thread.request", handle)
        try:
            await asyncio.Event().wait()
        finally:
            await bus.stop()
            await gateway_service.aclose()
            await db.close()

    asyncio.run(_run())


@app.command()
def supervisor() -> None:
    """Run the process supervisor for the full seven-process model."""
    from ethos.core.supervisor import run_supervisor

    install_fast_loop()
    asyncio.run(run_supervisor())


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply all Alembic migrations."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    root = Path(__file__).resolve().parent.parent
    alembic_cfg = AlembicConfig(str(root / "migrations" / "alembic.ini"))
    dsn = os.environ.get("ETHOS_DSN")
    if dsn:
        alembic_cfg.set_main_option("sqlalchemy.url", _asyncpg_dsn_to_sqlalchemy(dsn))
    command.upgrade(alembic_cfg, "head")
    typer.echo("migrations applied")


@db_app.command("seed")
def db_seed() -> None:
    """Seed self-model defaults, people, audit genesis, first intentions."""
    from ethos.config import load_config
    from ethos.core.seed import seed

    config = load_config()
    dsn = os.environ.get("ETHOS_DSN") or config.database.dsn
    os.environ["ETHOS_DSN"] = dsn

    async def _run() -> None:
        from ethos.db import Database

        db = await Database.connect(dsn)
        try:
            report = await seed(config, db)
            typer.echo(f"seeded: {report}")
        finally:
            await db.close()

    asyncio.run(_run())


@app.command()
def seed() -> None:
    """Seed self-model defaults, people, audit genesis, first intentions."""
    db_seed()


@app.command()
def status(
    pid_only: bool = typer.Option(
        False, "--pid", help="print only the supervisor's pid, if there is one"
    ),
) -> None:
    """Report whether the agent is running. Exits non-zero when it is not.

    Asked with one question, because that is the question a launcher, a script and
    a person all actually have: is the agent there? Everything else here is
    supporting detail — who supervises it, and whether the interface bridge is up.

    "Running" is decided by the agent's own sign of life rather than by a pid: a
    supervisor whose children have all died is still a live process, and a bridge
    with the agent down serves the agent's last known state perfectly — so both
    of them will happily say yes to a machine where nobody is home. The window is
    the same one the interface's own link uses before it tells somebody the agent
    is not running (`AGENT_ABSENT_MS` in Phone's agent client), so this command
    and the browser cannot disagree about the same machine at the same moment.

    `--pid` is for the other half of the question — whether something is *already*
    running — which a launcher has to ask before it starts anything and cannot
    answer from this report without reading prose.
    """
    from ethos.core.status import (
        agent_liveness,
        agent_log_dir,
        describe_age,
        interface_health,
    )
    from ethos.core.supervisor import Supervisor

    config = load_config()
    pid = Supervisor(config).read_pid()
    if pid_only:
        if pid:
            typer.echo(pid)
        raise typer.Exit(0 if pid else 1)

    typer.echo(f"{PRODUCT_NAME} status")
    typer.echo(f"  supervisor: {f'running (pid {pid})' if pid else 'not running'}")

    alive, age_s, detail = agent_liveness(config)
    if alive:
        typer.echo(f"  agent:      running (last sign of life {describe_age(age_s)})")
    else:
        typer.echo(f"  agent:      not running ({detail}) — start it with `ethos up`")
        typer.echo(f"               its own logs are in {agent_log_dir(config)}")

    url, up, interface_detail = interface_health(config)
    if up:
        typer.echo(f"  interface:  up ({url}, {interface_detail})")
    else:
        typer.echo(f"  interface:  down ({interface_detail})")

    raise typer.Exit(0 if alive else 1)


@app.command("await-agent")
def await_agent(seconds: float = typer.Argument(120.0, help="how long to wait, in seconds")) -> None:
    """Wait until the agent has published a sign of life since this command began.

    The question a start-up has to answer, which "is it running" cannot: a machine
    where the agent died an hour ago still has a bridge serving its last known
    state and a supervisor-shaped hole in the process table, so both can report a
    healthy system for a machine with nobody home. What a start-up can wait for is
    the agent doing something *after* it was asked to.

    Exits non-zero on the timeout, and says where the logs are when it does — a
    wait that fails with nothing else is the same dead end as the message this
    whole change exists to remove.
    """
    import time

    from ethos.core.status import (
        agent_liveness,
        agent_log_dir,
        database_now,
        describe_age,
    )

    config = load_config()
    started = database_now(config)
    deadline = time.monotonic() + max(seconds, 0.0)
    while True:
        alive, age_s, detail = agent_liveness(config, after=started)
        if alive:
            typer.echo(f"the agent is running (last sign of life {describe_age(age_s)})")
            raise typer.Exit(0)
        if time.monotonic() >= deadline:
            typer.echo(f"the agent did not come up within {seconds:g}s ({detail})")
            typer.echo(f"its own logs are in {agent_log_dir(config)}")
            raise typer.Exit(1)
        time.sleep(2.0)


@app.command()
def stop() -> None:
    """Ask the running supervisor to shut the agent down, and wait for it to go.

    A signal to the supervisor rather than to each child, because the supervisor
    is what knows the children, and because a supervisor that is asked politely
    takes its own process down with them. A no-op when nothing is running, so
    stopping something twice is not an error.

    The signal is the POSIX half and `_stop_windows` is the other: `SIGTERM` has
    no Windows spelling, and the call that looks equivalent is not. See that
    function for why the fallback is a file rather than a different signal.
    """
    import time

    from ethos.core.supervisor import Supervisor

    config = load_config()
    config.paths.ensure()
    supervisor = Supervisor(config)
    pid = supervisor.read_pid()
    if pid is None:
        typer.echo("no supervisor is running")
        raise typer.Exit(0)

    if IS_WINDOWS:
        _stop_windows(supervisor, pid, time)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # It ended between the pid being read and the signal being sent, which is
        # the outcome that was asked for. A traceback here would report a stop
        # that worked as a failure.
        typer.echo(f"the supervisor (pid {pid}) had already stopped")
        raise typer.Exit(0) from None
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if supervisor.read_pid() != pid:
            typer.echo(f"stopped the supervisor (pid {pid})")
            raise typer.Exit(0)
        time.sleep(0.2)
    typer.echo(
        f"the supervisor (pid {pid}) did not stop within 20s — its children may still be running"
    )
    raise typer.Exit(1)


def _stop_windows(supervisor, pid: int, time) -> None:
    """Stop a supervisor on Windows, where "please stop" is not a signal.

    `os.kill(pid, signal.SIGTERM)` is the whole of the POSIX version and it is
    wrong here in a way that cannot be fixed by choosing a different signal:
    CPython's Windows `os.kill` passes any value other than `CTRL_C_EVENT` and
    `CTRL_BREAK_EVENT` to `TerminateProcess`. So the one call that looks like a
    polite request is an immediate, unhandleable kill — the supervisor's pid file
    is never removed, `ethos stop` then reports a failure, and the next `ethos up`
    finds a pid file naming a process that is gone.

    The two console events that *are* requests cannot be used either: a detached
    supervisor has no console to be interrupted on and is not in this process's
    group, so `GenerateConsoleCtrlEvent` would fail.

    So the ask is the supervisor's own poll loop, which costs it nothing — it was
    already waking every two seconds to check for dead children — and the wait, the
    twenty-second deadline and the exit codes are all the POSIX version's. If the
    ask is not honoured within it, the process tree is taken down forcibly and the
    command says so rather than reporting a clean stop.
    """
    if not supervisor.request_stop_by_file():
        typer.echo(
            f"could not ask the supervisor (pid {pid}) to stop — its run directory "
            f"({supervisor.stop_file.parent}) is not writable"
        )
        raise typer.Exit(1)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if supervisor.read_pid() != pid:
            typer.echo(f"stopped the supervisor (pid {pid})")
            raise typer.Exit(0)
        time.sleep(0.2)
    # Escalate, and say what happened. `taskkill /T /F` rather than
    # `TerminateProcess` on the supervisor alone, so the children go with it: they
    # are its descendants, and a supervisor that is gone leaves them running
    # unsupervised, which is the whole thing the supervisor is for.
    typer.echo(
        f"the supervisor (pid {pid}) did not stop within 20s — stopping it and its "
        f"children forcibly, so nothing gets the chance to close cleanly"
    )
    hard_kill_pid(pid)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if supervisor.read_pid() != pid:
            typer.echo(f"the supervisor (pid {pid}) is gone; it did not shut down cleanly")
            raise typer.Exit(1)
        time.sleep(0.2)
    typer.echo(f"the supervisor (pid {pid}) could not be stopped at all")
    raise typer.Exit(1)


@app.command()
def doctor(
    probe_models: bool = typer.Option(
        True,
        "--probe-models/--no-probe-models",
        help=(
            "send one token-sized request per configured model to find out whether "
            "it can actually answer. On by default, and costs nothing but the "
            "request; --no-probe-models answers from configuration alone, which "
            "cannot tell a working key from a dead one."
        ),
    ),
) -> None:
    """Health check: config, database, pgvector, migrations, dirs, keys, models."""
    config = load_config()
    ok = True
    typer.echo(f"{PRODUCT_NAME} doctor")
    config.paths.ensure()
    typer.echo("  config dir: OK")
    dsn = os.environ.get("ETHOS_DSN") or config.database.dsn

    # The migration head this checkout carries, read with Alembic's own
    # directory walker so the comparison below is like for like.
    head: str | None = None
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parent.parent
        head = ScriptDirectory.from_config(
            AlembicConfig(str(root / "migrations" / "alembic.ini")),
        ).get_current_head()
    except Exception:
        head = None

    try:

        async def _check() -> tuple[bool, str | None]:
            from ethos.db import Database

            db = await Database.connect(dsn, min_pool=1, max_pool=2, connect_timeout_s=3)
            try:
                await db.fetchval("SELECT 1")
                # pgvector is a hard dependency of the memory layer, and its
                # absence only surfaces deep inside a running agent — the one
                # place a health check exists to keep it out of.
                vector = await db.fetchrow("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                try:
                    version = await db.fetchval("SELECT version_num FROM alembic_version")
                except Exception:
                    version = None
                return vector is not None, version
            finally:
                await db.close()

        vector_ok, version = asyncio.run(_check())
        typer.echo(f"  database: OK ({dsn.split('@')[-1]})")
        typer.echo("  pgvector: OK" if vector_ok else "  pgvector: FAIL (the database needs CREATE EXTENSION vector)")
        if not vector_ok:
            ok = False
        if head:
            if version is None:
                typer.echo("  migrations: FAIL (schema not applied — run `python -m ethos db upgrade`)")
                ok = False
            elif version != head:
                typer.echo(f"  migrations: {version} (head is {head} — run `python -m ethos db upgrade`)")
                ok = False
            else:
                typer.echo(f"  migrations: OK ({head})")
    except Exception as exc:
        ok = False
        typer.echo(f"  database: FAIL ({exc}) — is it up? Try `scripts/ensure_postgres.sh up`.")

    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        present = bool(os.environ.get(var))
        typer.echo(f"  {var}: {'present' if present else 'absent (optional provider)'}")

    for line, failed in _model_health_lines(config, probe=probe_models):
        typer.echo(f"  {line}")
        ok = ok and not failed

    # The doors, one line each, because a channel that is configured in a file
    # nobody has opened and is not working is the failure `doctor` exists to name.
    # It also names the file an interface writes, which is the one thing somebody
    # who has just been told "telegram: on, but missing telegram.token" needs to
    # know next and cannot get from a log of a process they did not start.
    for line in config.channels.door_report(config.paths.home):
        typer.echo(f"  {line}")

    for line in _filesystem_reach_lines(config):
        typer.echo(f"  {line}")

    for sock, label in ((config.gateway.socket_path, "gateway socket"),):
        exists = Path(sock).exists()
        typer.echo(f"  {label}: {'up' if exists else 'down'}")
    raise typer.Exit(0 if ok else 1)


def _model_health_lines(config: EthosConfig, *, probe: bool) -> list[tuple[str, bool]]:
    """
    Whether the agent can think, which is not the same question as whether it is configured.

    The line above this one checks that `ANTHROPIC_API_KEY` is set. That is a fact
    about an environment variable, and it stays true for a key that was revoked
    yesterday, a quota that ran out this morning, or a model the account was never
    entitled to -- which is the whole shape of the outage this section was added
    for. Everything was configured; nothing could answer; and a health check built
    from configuration said the installation was fine.

    So the two are reported separately and never collapsed: `key present` is what
    the file says, `usable` is what the provider says about it, and a model with
    a key that cannot answer is reported as exactly that. The failure is reported
    against the exit code as well as the text, because a script that runs `doctor`
    in a pipeline needs it to fail rather than merely to look concerned.

    `--no-probe-models` is the escape hatch for an offline machine. It degrades
    honestly rather than quietly: the lines say the probe was not run, so nobody
    reads "everything configured" as "everything works".
    """
    from ethos.gateway.service import GatewayService

    try:
        service = GatewayService(config, db=None)
    except Exception as exc:
        return [(f"models: FAIL (could not build the gateway: {exc})", True)]

    if not probe:
        configured = len(service.config.models.models)
        return [
            (f"models: {configured} configured, usability not probed "
             f"(--no-probe-models: a present key is not a working one)", False),
        ]

    try:
        reports = asyncio.run(service.probe_models())
    except Exception as exc:
        return [(f"models: FAIL (probe could not run: {exc})", True)]

    lines: list[tuple[str, bool]] = []
    usable = [r for r in reports if r["usable"]]
    # Only the base model decides the exit code. The catalogue is a set of
    # fallbacks, and a deployment with none of them answering is not broken if the
    # one it was configured to use is -- but it is broken if *nothing* is, which is
    # the state where the agent cannot produce a single token. So the per-model
    # lines below flag the base model and the summary line covers the rest.
    if usable:
        lines.append((
            f"models: {len(usable)} of {len(reports)} usable — "
            f"{', '.join(r['model'] for r in usable[:4])}",
            False,
        ))
    else:
        lines.append(("models: FAIL — no configured model can answer", True))
    for report in reports:
        if report["usable"]:
            continue
        detail = report["reason"] or "no reason given"
        lines.append((
            f"model {report['provider']}/{report['model']}: "
            f"{'key present' if report['key_present'] else 'no key'}, "
            f"unusable ({report['status']}) — {detail}",
            report.get("is_base") is True,
        ))
    return lines


def _filesystem_reach_lines(config: EthosConfig) -> list[str]:
    """
    What the agent can actually reach, measured, and what to do about the gaps.

    The agent's filesystem permissions are the running user's, so the honest
    first line is which user that is — a deployment running the systemd units
    answers `ethos`, not the person at the keyboard, and that difference is the
    whole answer to "why can't it see my files" on a server.

    Everything below that is a measurement rather than a claim. `os.access` is
    not used and no permission bit is consulted: the probe writes and deletes a
    real file, because the macOS privacy layer refuses on a directory the mode
    bits say are wide open, and a report built from the bits would say `ok` for
    exactly the directories that are refused. Those refusals are the one limit
    in this program that cannot be lifted from inside it — Full Disk Access is a
    consent given in System Settings by the person at the keyboard — so the job
    here is to name the directory and the remedy, not to work around it.
    """
    from ethos.paths import agent_home, is_platform_protected, probe_reach, user_home

    lines = [
        f"filesystem: running as {_current_user_label()} "
        f"(the agent's reach is exactly this user's)",
        f"filesystem: relative paths resolve to {user_home()}",
        f"filesystem: the agent's own directory is {agent_home(config)} "
        f"(memory and journal — not a boundary)",
    ]
    if is_platform_protected():
        lines.append(
            "filesystem: macOS privacy protection applies; denied locations below "
            "need Full Disk Access for whatever starts the agent"
        )

    # Probed once, not once for the list and again for the count: each probe
    # writes and deletes a real file, and `doctor` is the command somebody runs
    # when they are least expecting their filesystem to be touched.
    entries = probe_reach()
    for entry in entries:
        if entry.exists and not entry.reachable:
            lines.append(f"filesystem: DENIED {entry.path} — {entry.denied_by}")

    denied = [e for e in entries if e.exists and not e.reachable]
    if denied:
        lines.append(
            f"filesystem: {len(denied)} location(s) unreachable. This is the "
            "operating system, not the agent: grant access and restart it."
        )
    return lines


def _current_user_label() -> str:
    """The account the agent runs as, named the way a person would recognise.

    The POSIX answer is a uid and the name it resolves to, because a uid is what
    the agent's own permission checks are done against and a name is what a person
    reading the report recognises.

    Windows has no `pwd` and no `os.getuid()`, so the fallback does not reach for
    either: the original `except` branch called `os.getuid()` a second time, which
    meant the branch that was supposed to survive a missing `pwd` raised the very
    `AttributeError` it was there to catch, and `ethos doctor` — the one command
    whose whole job is to answer questions about the host instead of raising them
    — died printing the account it runs as. `USERNAME` is the environment
    variable Windows itself sets for the interactive account, and `Path.home()`
    covers the case where even that has been cleared.

    The three tiers are deliberately different rather than three attempts at one
    answer. A process started by a service manager has no `USERNAME` even though
    it has a perfectly good home directory, and a report that says "uid 0" on
    Windows would be a *different wrong answer*, not a weaker right one.
    """
    if os.name == "posix":
        try:
            import pwd

            return f"{pwd.getpwuid(os.getuid()).pw_name} (uid {os.getuid()})"
        except Exception:
            return f"uid {os.getuid()}"
    # `USERDOMAIN\USERNAME` is the form Windows itself displays, and it is the one
    # that disambiguates two accounts of the same name on a domain-joined machine.
    user = os.environ.get("USERNAME")
    if user:
        return f"{os.environ.get('USERDOMAIN', '.')}\\{user}" if os.environ.get("USERDOMAIN") else user
    return f"the account owning {Path.home()}"


def _asyncpg_dsn_to_sqlalchemy(dsn: str) -> str:
    if dsn.startswith("postgres://"):
        return "postgresql+asyncpg://" + dsn[len("postgres://"):]
    if dsn.startswith("postgresql://"):
        return "postgresql+asyncpg://" + dsn[len("postgresql://"):]
    return dsn


def main() -> None:
    """The entry point, shared by the `ethos` console script and `python -m ethos`.

    Both spellings land here rather than one of them landing on the Typer app
    directly, so there is exactly one path into the CLI and exactly one place
    that reads a `.env` before the first option is parsed.
    """
    _load_env_files()
    app()


def _load_env_files() -> None:
    """A `.env` file, if one is there. Never a requirement — always the first answer.

    The first one found wins: `$ETHOS_ENV_FILE` when it is set, else `./.env` when
    the command was typed in a directory that has one, else the checkout's. The
    real environment beats both, because a value exported for one command
    (`ETHOS_DSN=… ethos up`) is a deliberate override and a file that won over it
    would make that command do something other than what it says.

    Written to stderr, and once per process tree: `ethos status --pid` prints a
    bare pid for a launcher to read, and `ethos up` starts six more processes that
    would each print the same line again.
    """
    if os.environ.get("ETHOS_ENV_LOADED") == "1":
        return
    named = os.environ.get("ETHOS_ENV_FILE")
    if named:
        candidates = [Path(named).expanduser()]
    else:
        candidates = [Path.cwd() / ".env"]
        from ethos.core.bootstrap import checkout_root

        root = checkout_root()
        if root is not None:
            candidates.append(root / ".env")
    for candidate in candidates:
        if not candidate.is_file():
            if named:
                typer.secho(
                    f"ethos: {candidate} does not exist, and ETHOS_ENV_FILE names it",
                    fg=typer.colors.RED,
                    err=True,
                )
            continue
        loaded = _read_env_file(candidate)
        os.environ["ETHOS_ENV_LOADED"] = "1"
        typer.echo(
            f"-- environment: {candidate} ({len(loaded)} setting(s) not already in the environment)",
            err=True,
        )
        return


def _read_env_file(path: Path) -> list[str]:
    """The names this file contributed, having set them in the environment.

    Twenty lines rather than a dependency: the format is `KEY=value`, one per
    line, `#` for a comment — and the whole of what a `.env` has to be. A parser
    that grows a syntax nobody uses is a parser that has to be trusted.
    """
    loaded: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.secho(f"ethos: could not read {path}: {exc}", fg=typer.colors.RED, err=True)
        return loaded
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


if __name__ == "__main__":
    main()
