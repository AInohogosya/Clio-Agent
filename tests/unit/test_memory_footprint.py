from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from ethos.bus.event_bus import SEEN_ID_WINDOW, EventBus
from ethos.comms.adapters.email import SEEN_UID_LIMIT, EmailAdapter
from ethos.config import load_config
from ethos.engine.life import THOUGHT_BUFFER, Life, LifeDeps, _tail
from ethos.gateway.embeddings import (
    HashingEmbedder,
    RemoteEmbedder,
    assert_model_dim,
    build_role_embedder,
)
from ethos.memory.retrieval import HybridRetriever, cosine, mmr_select
from ethos.schemas.memory import MemoryHit
from ethos.threads.manager import ThreadManager
from ethos.toolhost.browser import IDLE_TTL_S, MAX_SESSIONS, BrowserError, BrowserPool
from tests.conftest import make_config

"""
The memory ceiling is not one bug.

It is six ONNX sessions that should have been one, four collections that grew
without a bound, a pool of browsers with no idea when it was finished, and a
retrieval path that built a few hundred Python lists to compare numbers. Each is
separately small and separately invisible; together they are a supervisor that
does not fit in the machine it was given.

These tests are about the bounds, not about the code that used to lack them. The
assertion is always the same shape — a collection that used to be a function of
uptime is now a function of a configured ceiling — because that is the property
that has to survive the next person who finds a use for adding to one of them.
"""


def _config() -> Any:
    config = make_config(Path("/tmp/ethos-test-memory-footprint"))
    config.paths.ensure()
    return config


# ---------------------------------------------------------------------------
# Priority 0 — one embedding model, not six


def _production_config() -> Any:
    """The shipped configuration, not the test fixture's.

    Two things are undone here, both on purpose. The `config` fixture sets
    `provider="hashing"` so no test in this suite downloads a model, and
    `tests/conftest.py` exports `ETHOS_EMBEDDING=hashing` for the same reason —
    together they make `load_config` answer "hashing" even when the shipped YAML
    says otherwise. Which process loads a model is a question about the shipped
    deployment, so this asks about the shipped deployment.
    """
    config = load_config("config")
    assert config.gateway.embedding.provider == "hashing", \
        "the suite's ETHOS_EMBEDDING=hashing override is what this undoes"
    config.gateway.embedding.provider = "fastembed"
    return config


def test_toolhost_and_workers_borrow_the_gateway_model():
    """The two processes that were instantiating their own ~1.2 GB sessions.

    Neither needs a model. `toolhost` embeds one episode summary per
    `memory.record`; each worker embeds one summary per delegated thread's
    writes. Both ask a process that already has the answer, over a socket that
    was already open.
    """
    config = _production_config()
    assert config.gateway.embedding.provider == "fastembed", "or this asserts nothing"
    assert isinstance(build_role_embedder("toolhost", config), RemoteEmbedder)
    assert isinstance(build_role_embedder("worker", config), RemoteEmbedder)


def test_the_processes_that_actually_embed_keep_their_own_model(monkeypatch):
    """`gateway` serves the requests and `core` is on the goal loop.

    Neither goes over the wire to itself. `core` embeds a focus title and every
    percept summary on every cycle; a loopback round trip on the agent's critical
    path to save a session it would then mostly sit on is not a trade worth
    making, and the gateway cannot route its own embeddings to `core` without a
    dependency the other three do not have.

    The decision is asserted rather than the result: constructing the real thing
    would download 1.2 GB of ONNX weights into a unit test, and a test that needs
    a gigabyte downloaded to pass is a test that gets skipped on CI and run by
    accident on a laptop.
    """
    config = _production_config()
    asked: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "ethos.gateway.embeddings.build_embedder",
        lambda provider, model, dim, **kw: asked.append((provider, model, dim))
        or HashingEmbedder(dim),
    )

    for role in ("gateway", "core"):
        build_role_embedder(role, config)
    assert [a[1] for a in asked] == [config.gateway.embedding.model] * 2

    asked.clear()
    for role in ("toolhost", "worker"):
        embedder = build_role_embedder(role, config)
        assert isinstance(embedder, RemoteEmbedder), role
    assert asked == [], "borrowing a model is not the same as building one"


def test_hashing_needs_no_gateway_hop(config):
    """A zero-footprint mode must not acquire a runtime dependency.

    `ETHOS_EMBEDDING=hashing` exists to be run on a machine that cannot spare a
    model at all. Making the toolhost wait on a loopback request to be told how to
    hash a word would be a way to make the cheapest mode the least available one.
    """
    config.gateway.embedding.provider = "hashing"
    for role in ("toolhost", "worker", "core", "gateway"):
        assert isinstance(build_role_embedder(role, config), HashingEmbedder), role

def test_a_model_the_schema_cannot_hold_is_refused_at_construction():
    """The cheap model, and what it costs.

    `BAAI/bge-small-en-v1.5` is 384-dimensional and 0.067 GB. The columns are
    `vector(1024)`. Setting it is the cheapest memory win on the table and it is
    also a database that will not take a write, so the check happens once, before
    anything is stored, and says what the fix is.
    """
    assert_model_dim("BAAI/bge-small-en-v1.5", 384)  # the matching pair: silent
    with pytest.raises(ValueError, match="vector"):
        assert_model_dim("BAAI/bge-small-en-v1.5", 1024)


def test_a_model_fastembed_cannot_describe_is_not_a_dimension_error():
    """A local directory or a private mirror has to be allowed through.

    `build_embedder` falls back to hashed vectors when a model will not load, and
    that fallback is precisely what must not fire for a width mismatch — so the
    "is this model narrow?" question has to be able to answer "I don't know".
    """
    assert_model_dim("some/local/model", 1024)  # no exception


def test_the_embedding_env_knobs_are_read(monkeypatch):
    """The four things an operator reaches for when the agent will not fit."""
    monkeypatch.setenv("ETHOS_EMBEDDING_THREADS", "2")
    monkeypatch.setenv("ETHOS_EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.setenv("ETHOS_EMBEDDING_DIM", "768")
    monkeypatch.setenv("ETHOS_EMBEDDING_CACHE", "/var/cache/ethos/onnx")
    cfg = load_config("config")
    assert cfg.gateway.embedding.threads == 2
    assert cfg.gateway.embedding.model == "BAAI/bge-base-en-v1.5"
    assert cfg.gateway.embedding.dim == 768
    assert cfg.gateway.embedding.cache_dir == "/var/cache/ethos/onnx"
    assert cfg.gateway.embedding.resolve_cache_dir(cfg) == "/var/cache/ethos/onnx"


def test_the_weights_cache_is_the_agents_own_and_not_fastembeds_default():
    """fastembed's default is not a promise about where anything lands.

    A per-user cache that a fresh container, a different service account or a
    swept temp directory silently empties turns every restart into a 1.2 GB
    download. Empty `cache_dir` means a directory under the agent's data
    directory, which is the volume a deployment actually persists.
    """
    cfg = load_config("config")
    resolved = cfg.gateway.embedding.resolve_cache_dir(cfg)
    assert resolved is not None
    assert resolved.startswith(str(cfg.paths.data_dir))
    assert "fastembed" in resolved


def test_the_default_session_asks_for_one_thread():
    """`None` sizes the ONNX arena to the core count.

    On a ten-core machine that is ten worker threads and ten per-thread arenas
    inside a session that embeds one sentence at a time.
    """
    assert load_config("config").gateway.embedding.threads == 1


def test_a_remote_embedder_raises_rather_than_substituting_hashed_vectors():
    """The one failure the local builder refuses to commit, tested on the remote one.

    `MemoryStore` writes whatever vector it is handed into the same pgvector
    columns, in the same space, with no record of which embedder produced it. If
    the gateway is unreachable and this process quietly hashes instead, the store
    ends up holding two spaces and every later search is silently wrong. A tool
    call that fails is visible; that is not.
    """
    embedder = RemoteEmbedder("/nonexistent/gateway.sock", dim=1024, retries=0)
    with pytest.raises(RuntimeError, match="gateway embedding unavailable"):
        embedder.embed(["a sentence"])
    embedder.close()


# ---------------------------------------------------------------------------
# Priority 1 — collections bounded by configuration, not by uptime


class _ThreadMemory:
    def __init__(self) -> None:
        self.closed: list[tuple[Any, str]] = []

    async def create_intention(self, title: str, desired_end_state: str = "", **_: Any):
        return uuid4()

    async def get_intention(self, intention_id: Any) -> None:
        return None

    async def close_intention(self, intention_id: Any, status: str, reason: str) -> None:
        self.closed.append((intention_id, status))


def _manager(config: Any, worker: Any = None, **kwargs: Any) -> ThreadManager:
    return ThreadManager(
        config, _ThreadMemory(), worker_factory=worker or (lambda spec: _echo()),
        **kwargs,
    )


async def _echo() -> str:
    return "the answer"


def _thread_spec(goal: str = "a goal") -> dict[str, Any]:
    return {"goal": goal, "role": "delegate"}


async def test_a_finished_thread_leaves_the_in_flight_table(config):
    """`active` is a fact about work in progress, and should be readable as one.

    The count was already right — `_running_count` filters on `status` — so this
    is not a concurrency fix. It is about the table being the thing it says it
    is: a manager holding 5,000 handles under a name that means "running" is a
    number nobody can trust, and it is 5,000 handles.
    """
    manager = _manager(config)
    handle = await manager.spawn_spec(_thread_spec())
    assert handle.thread_id in manager.active
    await manager.wait(handle)

    assert handle.thread_id not in manager.active
    assert manager._running_count() == 0
    assert handle.thread_id in manager.finished, "and the recent past still has it"


async def test_a_manager_that_has_run_for_days_still_holds_a_bounded_number(config):
    """The leak, stated as the assertion that would have caught it.

    500 delegations is a week of an active agent. Before the bound, this test's
    manager held 500 handles — each pinning its spec and its result — for the
    life of the process, and the only release was exiting.
    """
    config.threads.retention = 8
    manager = _manager(config)
    for i in range(500):
        handle = await manager.spawn_spec(_thread_spec(f"goal {i}"))
        await manager.wait(handle)

    assert manager.active == {}, "in-flight table holds only in-flight threads"
    assert len(manager.finished) == 8
    assert manager.finished_retention == 8


async def test_the_recent_past_evicts_the_oldest_first(config):
    """Which end is forgotten is the whole content of an LRU.

    A manager that dropped the newest handle would be useless for the question it
    exists to answer — what just happened.
    """
    config.threads.retention = 3
    manager = _manager(config)
    ids = []
    for i in range(6):
        handle = await manager.spawn_spec(_thread_spec(f"goal {i}"))
        await manager.wait(handle)
        ids.append(handle.thread_id)

    assert list(manager.finished) == ids[-3:]


async def test_a_finished_handle_still_answers_its_waiter(config):
    """Retiring from the table must not retire the caller's handle.

    `wait` is called on the object the caller was handed, so the eviction is
    invisible to it. This is the assertion that the two mechanisms are separate.
    """
    manager = _manager(config)
    handle = await manager.spawn_spec(_thread_spec())
    result = await manager.wait(handle)

    assert result == "the answer"
    assert handle.status == "done"
    assert manager.finished[handle.thread_id] is handle


async def test_a_failing_thread_is_retired_too(config):
    """`failed` is terminal like `done`, and leaks the same way if not."""

    async def _boom() -> str:
        raise RuntimeError("the delegate gave up")

    config.threads.retention = 4
    manager = _manager(config, worker=_boom)
    handle = await manager.spawn_spec(_thread_spec())
    await manager.wait(handle)

    assert handle.status == "failed"
    assert handle.thread_id not in manager.active
    assert handle.thread_id in manager.finished


async def test_a_manager_can_be_told_to_keep_nothing(config):
    """`retention: 0` is the honest setting for a process that reads the database.

    The cache has a floor of zero rather than a minimum, because a deployment
    that wants no resident handles should be able to say so rather than be given
    a floor nobody asked for.
    """
    config.threads.retention = 0
    manager = _manager(config)
    handle = await manager.spawn_spec(_thread_spec())
    await manager.wait(handle)

    assert manager.active == {}
    assert manager.finished == {}


async def test_a_worker_result_arriving_over_the_bus_also_retires(config):
    """The other completion path.

    In `process` mode nothing runs `_run`, so the `thread.result` event is the
    only place a handle reaches a terminal state. If retirement only lived in
    `_run`'s `finally`, every delegated thread in the deployed configuration
    would still be resident — the fix would pass every in-process test and do
    nothing in production.
    """
    from ethos.schemas.events import EventKind

    config.threads.mode = "process"
    published: list[tuple[str, dict[str, Any]]] = []

    class _Bus:
        def subscribe(self, kind: str, handler: Any) -> None:
            self.handler = handler

        async def publish(self, kind: Any, payload: dict[str, Any]) -> int:
            published.append((str(kind), payload))
            return 1

    bus = _Bus()
    manager = _manager(config, bus=bus)
    handle = await manager.spawn_spec(_thread_spec())

    assert handle.thread_id in manager.active
    await bus.handler(
        SimpleNamespace(kind=EventKind.THREAD_RESULT,
                        payload={"thread_id": handle.thread_id, "status": "done",
                                 "result": "from the worker"})
    )

    assert handle.result == "from the worker"
    assert handle.thread_id not in manager.active
    assert handle.thread_id in manager.finished
    assert manager._running_count() == 0


# ---------------------------------------------------------------------------


def _life(config: Any) -> Life:
    """A Life with no collaborators, which is all the thought buffer needs."""

    class _Nothing:
        def __getattr__(self, name: str) -> Any:
            async def _noop(*_: Any, **__: Any) -> Any:
                return None

            return _noop

    deps = LifeDeps(
        config=config, control=_Nothing(), bus=_Nothing(), memory=_Nothing(),
        self_model=_Nothing(), attention=_Nothing(), motivation=_Nothing(),
        rhythm=_Nothing(), gateway=_Nothing(), gsl=_Nothing(), social=_Nothing(),
        comms_out=_Nothing(), toolhost=_Nothing(), audit=_Nothing(),
        continuity=_Nothing(), consolidation=_Nothing(), context=None,
        embedder=None, thread_id="self", reverie=_Nothing(),
    )
    return Life(deps)


def test_the_thought_buffer_is_a_ring_not_a_list(config):
    """Both readers want 8 and 10; the buffer is what it holds, not what it has seen."""
    life = _life(config)
    for i in range(THOUGHT_BUFFER * 3):
        life._thoughts.append(f"thought {i}")

    assert len(life._thoughts) == THOUGHT_BUFFER
    assert life._thoughts[-1] == f"thought {THOUGHT_BUFFER * 3 - 1}"
    assert life._thoughts.maxlen == THOUGHT_BUFFER


def test_the_oldest_thoughts_are_the_ones_forgotten(config):
    """A window is only useful if it is the recent one.

    `deque(maxlen=...)` drops from the left, which is the end nothing reads.
    """
    life = _life(config)
    for i in range(THOUGHT_BUFFER + 50):
        life._thoughts.append(f"thought {i}")

    assert "thought 0" not in list(life._thoughts)
    assert life._thoughts[0] == "thought 50"
    assert len(life._thoughts) == THOUGHT_BUFFER


def test_extending_the_buffer_drops_oldest_too(config):
    """`act` extends with a whole decision's thoughts at once, not one at a time."""
    life = _life(config)
    life._thoughts.extend(f"batch {i}" for i in range(THOUGHT_BUFFER + 10))
    assert len(life._thoughts) == THOUGHT_BUFFER
    assert life._thoughts[0] == "batch 10"


@pytest.mark.parametrize("n,expected", [
    (0, []),
    (1, ["c"]),
    (2, ["b", "c"]),
    (3, ["a", "b", "c"]),
    (99, ["a", "b", "c"]),
])
def test_the_tail_readers_get_what_they_asked_for(n, expected):
    """`deque` has no slice syntax, so both reads go through `_tail`.

    `_tail` is what `deque[-10:]` cannot be, and getting it wrong would either
    raise inside the goal loop or quietly report an empty mind.
    """
    from collections import deque

    assert _tail(deque(["a", "b", "c"]), n) == expected


def test_the_tail_of_an_empty_buffer_is_empty(config):
    """A fresh process has thought of nothing, and that is not an error."""
    life = _life(config)
    assert _tail(life._thoughts, 10) == []


# ---------------------------------------------------------------------------


def _email() -> EmailAdapter:
    return EmailAdapter(
        address="a@example.com", password="x", imap_host="imap.example.com",
        smtp_host="smtp.example.com", poll_interval_s=30.0, on_inbound=lambda m: None,
    )


def test_a_polled_mailbox_does_not_grow_a_set_without_limit():
    """The only adapter whose dedupe set had no ceiling.

    IMAP hands out increasing UIDs, so a mailbox polled every thirty seconds is
    a stream with no natural end and nothing in this class used to shorten it.
    Its siblings cap at 2048 and 4096; this one was the exception nobody copied.
    """
    adapter = _email()
    for uid in range(1, 20_000):
        adapter.mark_seen_uid(uid)

    assert len(adapter._seen_uids) <= SEEN_UID_LIMIT


def test_a_redelivered_message_is_only_delivered_once():
    """The reason the set exists at all: IMAP re-offers, and a second inbound row
    is the agent answering the same email twice."""
    adapter = _email()
    assert adapter.mark_seen_uid(42) is True
    assert adapter.mark_seen_uid(42) is False


def test_the_window_forgets_the_oldest_uids_first():
    """UIDs ascend, so the lowest is the oldest.

    Anything above the window has been delivered and will not be offered again,
    which is what makes this safe to drop at all.
    """
    adapter = _email()
    for uid in range(SEEN_UID_LIMIT + 1):
        adapter.mark_seen_uid(uid)

    assert min(adapter._seen_uids) > 1, "the first UIDs are what went"
    assert SEEN_UID_LIMIT in adapter._seen_uids, "the newest is never the one dropped"


def test_recently_seen_uids_are_still_deduplicated_after_an_eviction():
    """Eviction must not make the newest message deliverable a second time."""
    adapter = _email()
    recent = [adapter.mark_seen_uid(uid) for uid in range(SEEN_UID_LIMIT + 500)]
    assert all(recent), "each UID is new exactly once"
    assert adapter.mark_seen_uid(SEEN_UID_LIMIT + 499) is False


# ---------------------------------------------------------------------------


def _bus() -> EventBus:
    return EventBus(SimpleNamespace(new_listener_connection=None), source="test")


def test_the_dedupe_window_is_a_window_not_a_growing_set():
    """The old shape was `set.clear()` at 100,000.

    Two things were wrong with the clearing and both are the clearing: it
    discarded the dedupe window the instant it filled, so at id 100,001 every id
    the bus had already delivered was deliverable again; and it did it in one
    statement, on the process that was about to answer a phone.
    """
    bus = _bus()
    for event_id in range(1, SEEN_ID_WINDOW * 3):
        bus._seen_add(event_id)

    assert len(bus._seen_ids) == SEEN_ID_WINDOW
    assert len(bus._seen_order) == SEEN_ID_WINDOW


def test_the_dedupe_window_forgets_one_id_at_a_time():
    """A window has to be a window: the oldest leaves, the rest stay."""
    bus = _bus()
    for event_id in range(1, 101):
        bus._seen_add(event_id)
    for event_id in range(101, SEEN_ID_WINDOW + 2):
        bus._seen_add(event_id)

    assert 1 not in bus._seen_ids
    assert 2 in bus._seen_ids, "one eviction, not a wipe"
    assert SEEN_ID_WINDOW + 1 in bus._seen_ids


def test_the_dedupe_window_still_remembers_a_replayed_notification():
    """The property the set exists for, at steady state rather than at the cliff."""
    bus = _bus()
    for event_id in range(1, 5_000):
        bus._seen_add(event_id)
    bus._seen_add(4_999)

    assert 4_999 in bus._seen_ids


def test_the_same_id_is_never_recorded_twice():
    """Re-recording would grow the ordering without growing the set, and the
    eviction below would then discard an id that is still in the set."""
    bus = _bus()
    for _ in range(50):
        bus._seen_add(7)

    assert list(bus._seen_order) == [7]
    assert bus._seen_ids == {7}


# ---------------------------------------------------------------------------


class _FakeBrowser:
    """A session that costs nothing and records whether it was closed."""

    def __init__(self, session_id: str, headless: bool = True) -> None:
        self.id = session_id
        self.headless = headless
        self.closed = False
        self.refuse_close = False

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        if self.refuse_close:
            raise BrowserError("the driver is already gone")
        self.closed = True


@pytest.fixture()
def pool(monkeypatch) -> BrowserPool:
    """A pool whose sessions are fake, so nothing launches Chromium here."""
    monkeypatch.setattr("ethos.toolhost.browser.BrowserSession", _FakeBrowser)
    return BrowserPool(max_sessions=3, idle_ttl_s=60.0)


async def test_a_session_the_pool_has_stopped_using_is_closed(pool):
    """The reaper, at the only moment the pool is touched.

    150–400 MB per browser, held by Chromium, which gives none of it back when
    the agent looks away. Only `close_all` used to end a session's life, and that
    runs at process exit.
    """
    now = [0.0]
    pool._clock = lambda: now[0]

    await pool.get_or_create("work")
    assert len(pool.sessions) == 1

    now[0] = pool.idle_ttl_s + 1
    reaped = await pool.reap_idle()

    assert reaped == ["work"]
    assert pool.sessions == {}


async def test_a_session_still_in_use_survives_the_clock(pool):
    """Idle is measured from the last fetch, not from creation.

    A page the agent is working through — a slow form, a login to come back to —
    is untouched for minutes at a time and must not be reaped out from under it.
    """
    now = [0.0]
    pool._clock = lambda: now[0]
    await pool.get_or_create("work")

    now[0] = pool.idle_ttl_s + 30
    assert await pool.get_or_create("work") is not None, "the fetch refreshes it"
    assert "work" in pool.sessions

    now[0] = pool.idle_ttl_s + 31
    await pool.reap_idle()
    assert "work" in pool.sessions, "and it is only reaped once the clock passes it"


async def test_the_pool_will_not_hold_more_than_its_cap(pool):
    """Four browsers is already more than the tool surface implies; a fifth has
    to cost something."""
    for i in range(6):
        await pool.get_or_create(f"s{i}")

    assert len(pool.sessions) <= 3


async def test_hitting_the_cap_costs_the_least_recently_used_browser(pool):
    """The cap evicts; it does not refuse.

    A pool that answered the fifth request with an error teaches the agent that
    browsing is broken, and the fifth browser is cheaper than that lesson. The
    victim is the one nobody has looked at longest.
    """
    await pool.get_or_create("a")
    await pool.get_or_create("b")
    await pool.get_or_create("c")
    await pool.get_or_create("d")  # over the cap: "a" goes

    assert set(pool.sessions) == {"b", "c", "d"}
    assert pool._last_used.keys() == {"b", "c", "d"}


async def test_a_freshly_used_session_is_not_the_one_evicted(pool):
    """LRU, not insertion order: touching a session protects it."""
    await pool.get_or_create("a")
    await pool.get_or_create("b")
    await pool.get_or_create("c")
    await pool.get_or_create("a")  # "a" is now the most recently used
    await pool.get_or_create("d")

    assert set(pool.sessions) == {"a", "c", "d"}


async def test_abandoned_sessions_are_reaped_before_a_live_one_is(pool):
    """Idleness first, then the ceiling.

    A pool holding three dead sessions should not evict a live fourth one when
    the ceiling is reached twenty minutes later.
    """
    now = [0.0]
    pool._clock = lambda: now[0]
    for name in ("a", "b", "c"):
        await pool.get_or_create(name)

    now[0] = pool.idle_ttl_s + 5
    await pool.get_or_create("d")

    assert set(pool.sessions) == {"d"}


async def test_a_browser_that_will_not_close_does_not_stop_the_reaper(pool):
    """Closing is best-effort by necessity.

    A driver that has already died raises on `close`, and the next session still
    has to be reaped — a pool that gave up there would leak everything after it.
    """
    now = [0.0]
    pool._clock = lambda: now[0]
    stubborn = await pool.get_or_create("stubborn")
    stubborn.refuse_close = True  # type: ignore[attr-defined]
    await pool.get_or_create("polite")

    now[0] = pool.idle_ttl_s + 1
    reaped = await pool.reap_idle()

    assert set(reaped) == {"stubborn", "polite"}
    assert pool.sessions == {}


async def test_the_default_ceiling_and_timeout_are_finite():
    """Both defaults are numbers, and both are small on purpose."""
    defaults = BrowserPool()
    assert defaults.max_sessions == MAX_SESSIONS == 4
    assert defaults.idle_ttl_s == IDLE_TTL_S
    assert 0 < IDLE_TTL_S <= 600, (
        "long enough for a page the agent is still working through, short enough "
        "that an abandoned browser is gone before the next piece of work starts"
    )


async def test_a_zero_timeout_disables_reaping_rather_than_reaping_everything():
    """`0` has to mean "off".

    `>= 0` against a just-created session would close every browser the instant
    it was opened, which is a very different thing from never expiring one.
    """
    pool = BrowserPool(max_sessions=2, idle_ttl_s=0.0)
    await pool.get_or_create("a")

    assert await pool.reap_idle() == []
    assert "a" in pool.sessions


# ---------------------------------------------------------------------------
# Priority 3 — the retrieval path's transient allocations


def _hit(id_: str, vec: list[float] | None, score: float = 0.5) -> MemoryHit:
    return MemoryHit(id=id_, kind="episode", summary=f"summary {id_}",
                     embedding=vec, score=score)


async def test_a_narrow_kind_asks_only_the_table_that_can_answer():
    """`search_facts` used to ask four tables for 48 rows each and keep the facts.

    Three quarters of that transfer, and of the vector parse, thrown away on every
    call — for a method that can only ever return one kind. The result list is
    unchanged; only the tables behind it are.
    """
    queried: list[str] = []

    class _DB:
        async def fetch(self, sql: str, *_: Any) -> list[Any]:
            queried.append(sql)
            return []

    cfg = load_config("config").memory
    retriever = HybridRetriever(_DB(), HashingEmbedder(1024), cfg)

    await retriever._candidates([0.1] * 1024, 48, kinds={"fact"})

    assert len(queried) == 1, "one table can answer a fact-only search"
    assert "FROM facts" in queried[0]


async def test_an_unfiltered_search_still_asks_all_four():
    """The ranking is over the merged candidate set, so narrowing would change it."""
    queried: list[str] = []

    class _DB:
        async def fetch(self, sql: str, *_: Any) -> list[Any]:
            queried.append(sql)
            return []

    cfg = load_config("config").memory
    retriever = HybridRetriever(_DB(), HashingEmbedder(1024), cfg)

    await retriever._candidates([0.1] * 1024, 48)

    assert len(queried) == 4
    for table in ("episodes", "facts", "knowhow", "questions"):
        assert any(f"FROM {table}" in sql for sql in queried), table


async def test_the_vectorized_cosine_is_the_cosine():
    """The number that replaced the list comprehension is the same quotient."""
    from ethos.memory.retrieval import HybridRetriever as HR

    texts = ["[" + ",".join(repr(v) for v in vec) + "]" for vec in
             ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [0.0, 0.0, 0.0])]
    hits = [HR._row_to_hit(
        {"id": str(i), "kind": "episode", "summary": "s", "body": None, "ts": None,
         "importance": 0.5, "strength": 0.5, "emb": t, "source": "episode"},
        with_embedding=False,
    ) for i, t in enumerate(texts)]
    packed = HR._pack(hits, texts, [1.0, 0.0, 0.0])

    expected = [cosine([1.0, 0.0, 0.0], [float(x) for x in t.strip("[]").split(",")])
                for t in texts]
    assert list(packed.cosines) == pytest.approx(expected, abs=1e-12)
    assert packed.cosines[-1] == 0.0, "a zero row scores zero, as cosine() says"


async def test_a_row_too_short_to_match_the_query_is_not_scored_against_it():
    """A dimension mismatch is zero similarity, not a broadcast exception.

    pgvector refuses to store a wrong-width vector, but the column can still hold
    a null and the `text` cast is a parse away from a malformed literal.
    """
    from ethos.memory.retrieval import HybridRetriever as HR

    hits = [HR._row_to_hit(
        {"id": "1", "kind": "episode", "summary": "s", "body": None, "ts": None,
         "importance": 0.5, "strength": 0.5, "emb": "[0.1,0.2]", "source": "episode"},
        with_embedding=False,
    )]
    packed = HR._pack(hits, ["[0.1,0.2]"], [1.0] * 1024)

    assert packed.cosines[0] == 0.0


async def test_the_candidate_ceiling_bounds_the_widening_but_not_the_request():
    """`k * 2` was the only bound, so a large `k` pulled 4x that from four tables.

    The effective limit is `max(ceiling, k)`: it caps how much extra is fetched to
    choose from, and never answers a caller with fewer than they asked for.
    """
    cfg = load_config("config").memory
    assert cfg.retrieval.max_candidate_k == 256

    def candidate_k(k: int) -> int:
        return min(max(k * 2, cfg.retrieval.candidate_k),
                   max(cfg.retrieval.max_candidate_k, k))

    assert candidate_k(24) == 48, "the default widening is unchanged"
    assert candidate_k(500) == 500, "never fewer than the k asked for"
    assert candidate_k(4) == 48, "and never fewer than candidate_k"


def test_the_vectorized_re_ranker_agrees_with_the_list_one():
    """MMR is the one place a candidate's vector is read after the candidates move.

    The retriever ranks by blended score and then re-ranks for diversity, so the
    candidate order and the vector rows arrive in different orders. Comparing row
    *n* against whatever candidate sits at position *n* produces a plausible,
    quietly wrong ranking — so the two are compared here, over inputs chosen to
    separate them: a zero row (which scores 0.0 against everything), equal-scoring
    pairs, and both a lambda that rewards relevance and one that rewards novelty.
    """
    rng = random.Random(11)
    for _ in range(60):
        dim = rng.choice([3, 8, 32])
        n = rng.randint(2, 25)
        hits: list[MemoryHit] = []
        vectors = np.zeros((n, dim))
        for i in range(n):
            row = [rng.gauss(0, 1) for _ in range(dim)]
            if rng.random() < 0.15:
                row = [0.0] * dim
            vectors[i] = row
            hits.append(_hit(str(i), list(row), score=round(rng.random(), 3)))
        k = rng.randint(1, 8)
        lam = rng.choice([0.7, 1.0, 0.3])

        expected = [h.id for h in _reference_mmr(hits, k, lam)]

        # Rows in the same order as the candidates.
        assert [h.id for h in mmr_select(hits, k, lam, vectors=vectors)] == expected

        # Rows in the order the retriever actually has them: ranked candidates,
        # unranked matrix. This is the case that has to be right.
        order = list(range(n))
        rng.shuffle(order)
        ranked = [hits[i] for i in order]
        got = [h.id for h in mmr_select(ranked, k, lam, vectors=vectors, rows=order)]
        assert got == expected


def _reference_mmr(candidates: list[MemoryHit], k: int, lambda_: float = 0.7) -> list[MemoryHit]:
    """The original MMR, kept here as the thing the fast path has to match."""
    selected: list[MemoryHit] = []
    pool = list(candidates)
    while pool and len(selected) < k:
        best, best_value = None, float("-inf")
        for hit in pool:
            diversity = 0.0 if not selected else max(
                (cosine(hit.embedding or [], s.embedding or [])
                 if hit.embedding and s.embedding else 0.0)
                for s in selected
            )
            value = lambda_ * hit.score - (1 - lambda_) * diversity
            if value > best_value:
                best_value, best = value, hit
        if best is None:
            break
        selected.append(best)
        pool.remove(best)
    return selected


def test_two_identical_memories_are_still_two_candidates():
    """MMR used to remove by value, so an equal pair could remove the wrong one.

    `list.remove` finds the first element that *compares equal*. Two memories with
    the same summary, body and vector compare equal, and removing the first of
    them when the second was chosen drops a candidate the caller never saw.
    Selection now runs on positions, so the two cannot be confused.
    """
    vec = [1.0, 0.0]
    twin = MemoryHit(id="twin", kind="episode", summary="same", embedding=list(vec), score=0.5)
    other = _hit("other", [0.0, 1.0], score=0.4)
    selected = mmr_select([twin, twin, other], k=2, lambda_=0.7)
    assert len(selected) == 2


def test_the_candidate_matrix_is_one_allocation_not_a_few_hundred_lists():
    """At 1024 dimensions a `list[float]` is pointers *and* boxed floats.

    The buffer holds the same numbers in a fraction of the memory, and it is the
    array the dot products run against.
    """
    from ethos.memory.retrieval import HybridRetriever as HR

    dim = 1024
    texts = ["[" + ",".join(repr(float(i)) for i in range(dim)) + "]"
             for _ in range(192)]
    hits = [HR._row_to_hit(
        {"id": str(i), "kind": "episode", "summary": "s", "body": None, "ts": None,
         "importance": 0.5, "strength": 0.5, "emb": t, "source": "episode"},
        with_embedding=False,
    ) for i, t in enumerate(texts)]
    packed = HR._pack(hits, texts, [1.0] * dim)

    assert packed.vectors.shape == (192, dim)
    assert packed.vectors.dtype == np.float64
    assert np.shares_memory(packed.vectors, packed.vectors)
    assert all(h.embedding is None for h in packed.hits), \
        "the list is not materialized for rows that will be dropped"
    assert packed.vectors.nbytes == 192 * dim * 8
