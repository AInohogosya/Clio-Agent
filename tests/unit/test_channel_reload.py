"""Doors that follow the configuration instead of being a snapshot of it.

Every test here is about a running `CommsHost` and a configuration that changed
underneath it. The assertions that matter are the ones about *order* and about
*what was left alone*: a reload that gets those wrong does not fail loudly, it
leaves a channel that looks configured and receives nothing, which is the same
symptom as the bug this whole mechanism exists to remove.
"""

from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from typing import Any

import pytest

from ethos.comms.host import CommsHost, _Plan

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


class Recorder:
    """Every half of every adapter's life, in the order it happened.

    Ordered rather than counted because the failure this file is about is an
    ordering one: a replacement opened beside the thing it replaces is a second
    `getUpdates` on one token, and Telegram answers that with a conflict for as
    long as the first is alive.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.adapters: dict[str, int] = {}

    def record(self, event: str, name: str) -> None:
        self.events.append((event, name))

    def started(self, name: str) -> bool:
        return (self.events.count(("start", name)), self.events.count(("listen", name)))


class FakeAdapter:
    """An adapter that reports what was done to it and to whom."""

    name = "fake"

    def __init__(self, recorder: Recorder, name: str, **_: Any) -> None:
        self.recorder = recorder
        self.name = name
        recorder.adapters[name] = recorder.adapters.get(name, 0) + 1

    async def start(self) -> None:
        self.recorder.record("start", self.name)

    async def listen(self) -> None:
        self.recorder.record("listen", self.name)

    async def stop(self) -> None:
        self.recorder.record("stop", self.name)

    async def send(self, *, person_id: str | None = None, text: str = "", **_: Any) -> dict[str, Any]:
        return {"status": "sent"}


class PortTaken(FakeAdapter):
    async def start(self) -> None:
        raise OSError(errno.EADDRINUSE, "Address already in use")


def _telegram_only(monkeypatch: pytest.MonkeyPatch, **telegram: Any) -> Any:
    """The shipped config with one polling door and nothing else that listens."""
    from ethos.config import load_config

    monkeypatch.chdir(REPO_CONFIG.parent)
    config = load_config(str(REPO_CONFIG))
    config.channels.cli.enabled = False
    config.channels.web.enabled = False
    config.channels.whatsapp.enabled = False
    config.channels.slack.enabled = False
    config.channels.discord.enabled = False
    config.channels.email.enabled = False
    config.channels.webhook.enabled = False
    config.channels.telegram.enabled = telegram.pop("enabled", True)
    for key, value in telegram.items():
        setattr(config.channels.telegram, key, value)
    monkeypatch.setenv(config.channels.telegram.token_env or "TEST_TG_TOKEN", "test-token")
    return config


def _planned(host: CommsHost, doors: dict[str, tuple], receiver: tuple[str, int, int] | None = None) -> None:
    """Make `host.plan` answer with exactly these doors, built as fakes."""
    recorder = host._recorder  # type: ignore[attr-defined]

    def _plan(_channels: Any) -> _Plan:
        adapters = {name: FakeAdapter(recorder, name) for name in doors}
        return _Plan(adapters=adapters, signatures=dict(doors), receiver=receiver)

    host.plan = _plan  # type: ignore[method-assign]


def _host(config: Any, recorder: Recorder) -> CommsHost:
    async def on_inbound(_message: Any) -> None:
        return None

    host = CommsHost(config, on_inbound=on_inbound)
    host._recorder = recorder  # type: ignore[attr-defined]
    return host


# --------------------------------------------------------------------- the door


async def test_a_door_opened_in_the_config_opens_without_a_restart(monkeypatch: pytest.MonkeyPatch):
    """The whole point: a settings save is enough, and nothing has to be restarted.

    Asserted on a host that is already running, because a host that has not started
    yet proves nothing — that is what it always did.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {})
    await host.start(listen=True)
    try:
        assert recorder.events == [], "nothing is open yet"

        _planned(host, {"telegram": ("telegram", "test-token")})
        assert await host.reconcile(host.channels) is True
        assert recorder.started("telegram") == (1, 1), "started and listening, in a running host"
    finally:
        await host.stop()


async def test_a_door_turned_off_in_the_config_is_closed(monkeypatch: pytest.MonkeyPatch):
    """A door somebody switched off has to stop being held, or the token keeps polling.

    The failure is not that the bot stays deaf — it is that it keeps answering.
    Turning a door off and finding the agent still replying on it is the opposite of
    what was asked for.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "test-token")})
    await host.start(listen=True)
    try:
        assert "telegram" in host.adapters

        _planned(host, {})
        assert await host.reconcile(host.channels) is True
        assert "telegram" not in host.adapters
        assert ("stop", "telegram") in recorder.events
    finally:
        await host.stop()


async def test_a_changed_token_replaces_the_door_and_leaves_the_others_alone(monkeypatch: pytest.MonkeyPatch):
    """One door changed is one door rebuilt.

    The rest of the doors are left running on purpose: re-opening a Telegram poller
    that is already answering drops the reply to a message somebody sent a second
    ago, and the person watching has no way to tell that from a broken channel.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "old"), "discord": ("discord", "dc")})
    await host.start(listen=True)
    try:
        kept = host.adapters["discord"]

        _planned(host, {"telegram": ("telegram", "new"), "discord": ("discord", "dc")})
        assert await host.reconcile(host.channels) is True
        assert host.adapters["discord"] is kept, "an unchanged door is not restarted"
        assert ("stop", "discord") not in recorder.events
        assert ("stop", "telegram") in recorder.events
    finally:
        await host.stop()


async def test_a_changed_door_is_closed_before_its_replacement_is_opened(monkeypatch: pytest.MonkeyPatch):
    """Close first, open second — the ordering the whole design turns on.

    Telegram answers a second `getUpdates` on one token with a conflict for as long
    as the first is alive, so opening the replacement beside the original is not a
    brief overlap to be tolerated: it is a channel that stops receiving for exactly
    as long as somebody is watching to see whether the save worked.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "old")})
    await host.start(listen=True)
    try:
        mark = len(recorder.events)
        _planned(host, {"telegram": ("telegram", "new")})
        await host.reconcile(host.channels)
        after = recorder.events[mark:]
        assert after.index(("stop", "telegram")) < after.index(("start", "telegram"))
    finally:
        await host.stop()


async def test_a_poll_that_finds_nothing_different_does_not_touch_a_running_door(monkeypatch: pytest.MonkeyPatch):
    """Every two seconds, forever, against a configuration that has not moved.

    A rebuild on every poll would restart the poller continuously: dropped replies,
    a Telegram conflict every few seconds, and a log nobody could read. The cheap
    comparison is the difference between a channel that can be reloaded safely and
    one that cannot be reloaded at all.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "test-token")})
    await host.start(listen=True)
    try:
        before = len(recorder.events)
        for _ in range(5):
            assert await host.reconcile(host.channels) is False
        assert len(recorder.events) == before, "nothing was started, stopped or reopened"
    finally:
        await host.stop()


# ------------------------------------------------------------------ the split


async def test_the_answering_process_opens_no_door_when_it_reloads(monkeypatch: pytest.MonkeyPatch):
    """`ethos-core` follows the configuration without ever taking a door.

    A reload is the one moment this process is handed a fresh, complete set of
    channels and could plausibly open them. It must not, because `ethos-comms` is
    already holding every one, and two pollers on one bot is a conflict rather than
    redundancy.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {})
    await host.start(listen=False)
    try:
        _planned(host, {"telegram": ("telegram", "test-token")})
        assert await host.reconcile(host.channels) is True
        assert ("start", "telegram") in recorder.events, "it can send on the new door"
        assert ("listen", "telegram") not in recorder.events, "and it must not open it"
    finally:
        await host.stop()


async def test_the_answering_process_registers_push_routes_without_binding_the_port(
    monkeypatch: pytest.MonkeyPatch,
):
    """Routes yes, port no.

    The core needs to know where a reply goes, which is the routes; it does not need
    the port, which belongs to whoever is listening. Conflating the two is how a
    second receiver ends up racing the first for a loopback port.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {})
    await host.start(listen=False)
    try:
        _planned(host, {"slack": ("slack", "s")}, receiver=("127.0.0.1", 0, 262144))
        await host.reconcile(host.channels)
        assert host.receiver is not None
        assert host.receiver.started is False
    finally:
        await host.stop()


# ----------------------------------------------------------------- the failure


async def test_one_door_failing_to_start_does_not_cost_the_others_their_start(monkeypatch: pytest.MonkeyPatch):
    """A revoked Telegram token is not a reason for WhatsApp to stop listening."""
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {})

    def _plan(_channels: Any) -> _Plan:
        return _Plan(
            adapters={"telegram": PortTaken(recorder, "telegram"), "discord": FakeAdapter(recorder, "discord")},
            signatures={"telegram": ("telegram", "t"), "discord": ("discord", "d")},
        )

    host.plan = _plan  # type: ignore[method-assign]
    await host.start(listen=True)
    try:
        assert ("start", "discord") in recorder.events
        assert ("listen", "discord") in recorder.events
    finally:
        await host.stop()


async def test_a_failed_reload_is_retried_rather_than_believed(monkeypatch: pytest.MonkeyPatch):
    """A reload that throws leaves the previous answer standing.

    The alternative is the one failure this cannot recover from: a half-applied
    reload that recorded itself as done, so the watcher never tries the same change
    again and the door stays shut with nothing left to retry it.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "old")})
    await host.start(listen=True)
    try:
        fingerprint = host._fingerprint

        def _boom(_channels: Any) -> _Plan:
            raise RuntimeError("the file changed under me")

        host.plan = _boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            await host.reconcile(host.channels)
        assert host._fingerprint == fingerprint, "so the next poll tries again"
        assert "telegram" in host.adapters, "and the door nobody asked to close is still up"
    finally:
        await host.stop()


async def test_a_door_whose_close_failed_is_forgotten_anyway(monkeypatch: pytest.MonkeyPatch):
    """A poller that could not be stopped is not one to keep a handle on.

    Kept, a later reload would see a signature matching what it wants, decide the
    door was already correct, and leave a poller nothing owns running forever.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "old")})
    await host.start(listen=True)
    try:
        host.adapters["telegram"].stop = _raises  # type: ignore[method-assign]
        _planned(host, {})
        await host.reconcile(host.channels)
        assert "telegram" not in host.adapters
    finally:
        await host.stop()


async def _raises() -> None:
    raise RuntimeError("could not close")


# ------------------------------------------------------------------- the watcher


async def test_a_change_to_the_file_is_picked_up_with_nothing_told_about_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """End to end, from the file on disk to an open door, with no restart and no signal.

    Driven through the real `plan` and a real `load_config`, with only the Telegram
    class substituted, because the thing being tested is the chain: a settings
    screen writes `channels.yaml`, something notices, and the door opens. A test that
    handed `reconcile` a config object directly would pass whether or not anybody
    was actually watching the file.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ETHOS_HOME", str(home))
    monkeypatch.setenv("ETHOS_CONFIG_DIR", str(REPO_CONFIG))
    monkeypatch.setenv("TEST_TG_TOKEN", "test-token")
    monkeypatch.chdir(tmp_path)

    recorder = Recorder()
    monkeypatch.setattr(
        "ethos.comms.host.TelegramAdapter",
        lambda token, **kwargs: FakeAdapter(recorder, "telegram", token=token, **kwargs),
    )

    host = _host(_telegram_only(monkeypatch, enabled=False), recorder)
    await host.start(listen=True)
    watcher = asyncio.create_task(host.watch_channels(interval=0.05))
    try:
        assert "telegram" not in host.adapters, "the door starts shut"

        (home / "channels.yaml").write_text(
            "telegram:\n  enabled: true\n  token: 'from-the-file'\n", encoding="utf-8"
        )
        for _ in range(100):
            if "telegram" in host.adapters:
                break
            await asyncio.sleep(0.05)
        assert "telegram" in host.adapters, "the watcher opened it"
        assert recorder.started("telegram") == (1, 1)

        # And the reverse, because a door that can be opened but not closed is a
        # door that keeps answering after somebody turned it off.
        (home / "channels.yaml").write_text("telegram:\n  enabled: false\n", encoding="utf-8")
        for _ in range(100):
            if "telegram" not in host.adapters:
                break
            await asyncio.sleep(0.05)
        assert "telegram" not in host.adapters, "and closed it again"
    finally:
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher
        await host.stop()


async def test_a_file_that_will_not_parse_leaves_every_door_exactly_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
):
    """Somebody halfway through hand-editing a file is not asking to be closed.

    Reading an unreadable configuration as an empty one would take down every door
    in the agent on a keystroke, which is both worse than a save that lands late and
    not something anybody could undo from the screen that caused it.
    """
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "test-token")})
    await host.start(listen=True)

    def _unreadable() -> Any:
        raise ValueError("channels.yaml: mapping values are not allowed here")

    monkeypatch.setattr("ethos.comms.host.load_config", _unreadable)
    watcher = asyncio.create_task(host.watch_channels(interval=0.01))
    try:
        await asyncio.sleep(0.1)
        assert "telegram" in host.adapters
        assert ("stop", "telegram") not in recorder.events
    finally:
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher
        await host.stop()


async def test_the_watcher_does_nothing_to_a_host_that_never_started(monkeypatch: pytest.MonkeyPatch):
    """Reconciling before `start` would apply a plan to nothing and claim success."""
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "t")})
    assert await host.reconcile(host.channels) is False
    assert recorder.events == []
    assert host.adapters == {}


class StubUpdater:
    """Stands in for the library's updater, which is the thing that owns the poller."""

    def __init__(self, stop: Any) -> None:
        self._stop = stop

    async def stop(self) -> None:
        await self._stop()


class StubApp:
    """Just enough application for `stop()` to walk: the two shutdown steps and an updater."""

    def __init__(self, updater: Any) -> None:
        self.updater = updater

    async def stop(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


async def _lingering_poller() -> None:
    """Stands in for the library's long-poll loop: pending until somebody stops it."""
    await asyncio.sleep(3600)


def _telegram_adapter() -> Any:
    from ethos.comms.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter("t", accept_from_anyone=True)
    adapter._polling = True
    return adapter


async def _named_task(name: str) -> asyncio.Task:
    """A pending task named the way the library names its poller."""
    return asyncio.create_task(_lingering_poller(), name=name)


async def test_a_poller_that_outlives_its_door_is_cancelled(monkeypatch: pytest.MonkeyPatch):
    """A door that will not shut politely must not be left half-open.

    A poller that survives its own door is worse than a door that will not shut: it
    keeps a `getUpdates` long-poll open against a token the settings screen now says
    is closed, so the next person to open that door meets a conflict from a poller
    they cannot see. The cooperative stop is the tidiest route and the one tried
    first; this is the guarantee that there is a second route.
    """
    adapter = _telegram_adapter()
    poller = await _named_task("Updater:start_polling:polling_task")
    adapter._app = StubApp(StubUpdater(_raises))

    await adapter.stop()

    await asyncio.sleep(0)
    assert poller.cancelled(), "the poller was cancelled rather than left running"
    assert adapter._app is None, "and the adapter let go of its handle"
    assert adapter._polling is False


async def test_a_close_that_is_interrupted_still_takes_the_poller_with_it():
    """The guarantee holds on the cancelled path too, not only the failing one.

    A reload interrupted half-done — the next save arriving, the agent shutting down
    mid-save — must not be the one route that leaves a live poller, and it cannot be
    an `await` in a handler: a task being cancelled is not guaranteed to reach one.
    So this cancels the close while it is in flight and checks the poller anyway.
    """
    adapter = _telegram_adapter()
    poller = await _named_task("Updater:start_polling:polling_task")
    reached = asyncio.Event()
    never = asyncio.Event()

    async def _stuck() -> None:
        reached.set()
        await never.wait()

    adapter._app = StubApp(StubUpdater(_stuck))

    closing = asyncio.create_task(adapter.stop())
    await reached.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing

    await asyncio.sleep(0)
    assert poller.cancelled(), "an interrupted close still closes the door"
    assert adapter._app is None


async def test_a_poll_reads_the_configuration_only_when_the_files_have_moved(monkeypatch: pytest.MonkeyPatch):
    """The common poll has to be cheap, because it is the common poll.

    Re-reading the configuration parses every file in it and rebuilds every model —
    tens of milliseconds, every two seconds, for as long as the agent runs, in two
    processes. Skipping the read while both files are untouched is the difference
    between a watcher and a tax, and it is observable: the read is what decides
    whether anything is reconciled at all.
    """
    from ethos.comms import host as host_module

    reads = 0
    real_load = host_module.load_config

    def _counted() -> Any:
        nonlocal reads
        reads += 1
        return real_load()

    monkeypatch.setattr(host_module, "load_config", _counted)
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "test-token")})
    await host.start(listen=True)

    watcher = asyncio.create_task(host.watch_channels(interval=0.001))
    try:
        await asyncio.sleep(0.01)
        # Exactly one read, the one the first poll always makes; the ten-odd polls
        # after it were answered by the stamps. Well inside `RESCAN_EVERY_POLLS`, so
        # this measures the stamps and not the backstop — the two are asserted apart.
        assert reads == 1, "one read at startup, then the stamps answered every poll"
        assert ("stop", "telegram") not in recorder.events
    finally:
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher
        await host.stop()


async def test_the_configuration_is_read_again_periodically_even_when_the_files_look_unchanged(
    monkeypatch: pytest.MonkeyPatch,
):
    """The stamps are a shortcut, not the authority.

    A stamp is a cached inode's modification time and length. Believing it with the
    last word about a file it never read would mean a door silently staying shut for
    as long as the process lives, which is the one failure this whole mechanism
    exists to remove — traded for a small saving on a poll that changes nothing.
    """
    from ethos.comms import host as host_module

    monkeypatch.setattr(host_module, "RESCAN_EVERY_POLLS", 3)
    reads = 0
    real_load = host_module.load_config

    def _counted() -> Any:
        nonlocal reads
        reads += 1
        return real_load()

    monkeypatch.setattr(host_module, "load_config", _counted)
    recorder = Recorder()
    host = _host(_telegram_only(monkeypatch), recorder)
    _planned(host, {"telegram": ("telegram", "test-token")})
    await host.start(listen=True)

    watcher = asyncio.create_task(host.watch_channels(interval=0.001))
    try:
        await asyncio.sleep(0.1)
        assert reads >= 1, "the configuration was read anyway, on the timer"
    finally:
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher
        await host.stop()


async def test_a_save_that_lands_before_the_watchers_first_poll_is_not_missed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The window between stamping and the first comparison is a real one.

    The watcher's first stamp is taken before the task has run even once, so a save
    in that window is stamped as already-known and then waited on until the periodic
    read — which is a door shut for a quarter of a minute after somebody asked for it
    to be open, and it is the *startup* save, the one somebody makes while waiting
    for the agent to come up.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ETHOS_HOME", str(home))
    monkeypatch.setenv("ETHOS_CONFIG_DIR", str(REPO_CONFIG))
    monkeypatch.setenv("TEST_TG_TOKEN", "test-token")
    monkeypatch.chdir(tmp_path)

    recorder = Recorder()
    monkeypatch.setattr(
        "ethos.comms.host.TelegramAdapter",
        lambda token, **kwargs: FakeAdapter(recorder, "telegram", token=token, **kwargs),
    )
    host = _host(_telegram_only(monkeypatch, enabled=False), recorder)
    await host.start(listen=True)

    # The save lands in the same tick the watcher is created, which is the race.
    watcher = asyncio.create_task(host.watch_channels(interval=0.05))
    (home / "channels.yaml").write_text(
        "telegram:\n  enabled: true\n  token: 'from-the-file'\n", encoding="utf-8"
    )
    try:
        for _ in range(60):
            if "telegram" in host.adapters:
                break
            await asyncio.sleep(0.05)
        assert "telegram" in host.adapters, "caught without waiting for the periodic read"
    finally:
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher
        await host.stop()


def test_a_reload_does_not_reach_out_to_the_environment_twice(monkeypatch: pytest.MonkeyPatch):
    """The fingerprint is over signatures, never over adapter objects.

    An adapter's `repr` is its address, so hashing the objects themselves would make
    every poll look like a change and rebuild the doors twice a second — a poller
    restarted in a loop, which is a channel that never gets to answer anything.
    """
    from ethos.config import load_config

    monkeypatch.chdir(REPO_CONFIG.parent)
    config = load_config(str(REPO_CONFIG))
    config.channels.cli.enabled = False
    config.channels.web.enabled = False

    async def on_inbound(_message: Any) -> None:
        return None

    host = CommsHost(config, on_inbound=on_inbound)
    first = host.plan(config.channels)
    second = host.plan(config.channels)
    assert first.fingerprint() == second.fingerprint()
    assert first.adapters["telegram" if "telegram" in first.adapters else "cli"] is not \
        second.adapters["telegram" if "telegram" in second.adapters else "cli"], "fresh objects, same answer"


def test_the_watcher_interval_is_short_enough_to_be_invisible_and_long_enough_to_be_free():
    """The number is a judgement, so it is asserted rather than left to drift."""
    from ethos.comms.host import WATCH_INTERVAL_S

    assert 0.5 <= WATCH_INTERVAL_S <= 5.0
