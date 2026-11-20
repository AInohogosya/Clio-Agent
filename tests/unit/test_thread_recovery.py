"""A delegated task that hits an issue must try to recover before it ends.

Two failures composed into the reported symptom — "the agent ends a task as-is
when something goes wrong":

  1. `_pursue` wrapped its whole pursuit in one `except`, so a single rate
     limit, dropped connection or prose answer ended the task on the spot,
     with no retry, no backoff and no repair turn.
  2. `ThreadManager._run` read "the worker returned" as "the work is done",
     so a pursuit that ended in `{"status": "failed"}` was closed in the
     intention store as `done, "thread completed"` — the failure swallowed.

The tests here pin the recovery (bounded retries, one repair turn for an
unusable answer) and the honest recording when the recovery is exhausted.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from clio.threads import worker as worker_module
from clio.threads.manager import ThreadManager
from clio.threads.worker import _pursue, run_thread


class _RateLimited(Exception):
    status = 429


class _Memory:
    """Just enough memory for the paths under test."""

    def __init__(self) -> None:
        self.closed: list[tuple[Any, str, str]] = []
        self.episodes: list[dict[str, Any]] = []

    async def create_intention(self, *a: Any, **k: Any) -> uuid.UUID:
        return uuid.uuid4()

    async def get_intention(self, intention_id: Any) -> None:
        return None

    async def close_intention(self, intention_id: Any, status: str, reason: str) -> None:
        self.closed.append((intention_id, status, reason))

    async def record_episode(self, **k: Any) -> None:
        self.episodes.append(k)


def _response(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text, cost_usd=0.01)


class _ScriptedGateway:
    """Answers per `purpose`, from a script; records every request it saw."""

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.purposes: list[str] = []
        self.cost_usd = 0.0

    async def complete(self, request: Any) -> Any:
        purpose = request.purpose
        # A repair re-ask keeps the purpose of the call it repairs.
        base = purpose
        self.purposes.append(base)
        queue = self.script.get(base)
        assert queue, f"unexpected purpose {base}"
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _spec(goal: str = "a delegated goal") -> dict[str, Any]:
    return {"id": "thread-test", "goal": goal}


def _intention() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), title="a delegated goal")


def _ok_devise() -> _response:
    return _response(
        '{"approaches": [{"id": "a1", "summary": "write it directly", '
        '"expected_value": 0.8, "estimated_cost_usd": 0.05, "reversible": true}]}'
    )


def _ok_plan(steps: int = 1) -> _response:
    entries = ", ".join(
        f'{{"id": "s{n+1}", "description": "step {n+1}"}}' for n in range(steps)
    )
    return _response(f'{{"steps": [{entries}]}}')


def _ok_step() -> _response:
    return _response('{"thought": "did it", "actions": [{"tool": "fs.write", "args": {}}], "done": true}')


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The waits are real seconds in production; the tests only need order."""
    monkeypatch.setattr(worker_module, "RATE_LIMIT_BASE_S", 0.001)
    monkeypatch.setattr(worker_module, "RATE_LIMIT_MAX_S", 0.002)
    monkeypatch.setattr(worker_module, "TRANSIENT_BASE_S", 0.001)
    monkeypatch.setattr(worker_module, "TRANSIENT_MAX_S", 0.002)
    monkeypatch.setattr(worker_module, "backoff_seconds", lambda *a, **k: 0.0)


async def test_a_rate_limited_call_is_waited_out_not_fatal(config):
    """A 429 is time, not a verdict on the task: two limits and then an
    answer is a completed delegation, where before it was `failed`."""
    gateway = _ScriptedGateway({
        "thread.devise": [_RateLimited("slow down"), _RateLimited("slow down"), _ok_devise()],
        "thread.plan": [_ok_plan()],
        "thread.step": [_ok_step()],
    })

    result = await _pursue(
        "a delegated goal", _intention(), [], _spec(), config,
        memory=_Memory(), gateway=gateway, workdir=".",
    )

    assert result["status"] == "done", result
    assert gateway.purposes.count("thread.devise") == 3


async def test_a_prose_answer_gets_one_repair_turn(config):
    """A formatting slip is re-asked with the answer quoted back, not read as
    an empty plan."""
    gateway = _ScriptedGateway({
        "thread.devise": [_response("I would write the file directly, it is simple."),
                          _ok_devise()],
        "thread.plan": [_ok_plan()],
        "thread.step": [_ok_step()],
    })

    result = await _pursue(
        "a delegated goal", _intention(), [], _spec(), config,
        memory=_Memory(), gateway=gateway, workdir=".",
    )

    assert result["status"] == "done", result
    assert gateway.purposes.count("thread.devise") == 2


async def test_exhausted_rate_limits_end_the_task_honestly(config):
    """Bounded does not mean unbounded: after the retry budget the task ends,
    and the envelope says so with the stage and the error. The field default
    is no ceiling at all; this test restores the bounded mode it is about."""
    config.threads.call_attempts = 3
    gateway = _ScriptedGateway({
        "thread.devise": [_RateLimited("slow down")] * 9,
    })

    result = await _pursue(
        "a delegated goal", _intention(), [], _spec(), config,
        memory=_Memory(), gateway=gateway, workdir=".",
    )

    assert result["status"] == "failed"
    assert result["stage"] == "devise"
    assert "slow down" in result["error"]


async def test_a_step_with_no_usable_answer_twice_fails_the_task(config):
    """The step gets its repair turn; failing that, the task ends saying
    which step could not be answered — never a `done` over silence."""
    gateway = _ScriptedGateway({
        "thread.devise": [_ok_devise()],
        "thread.plan": [_ok_plan()],
        "thread.step": [_response("it went fine"), _response("really, it went fine")],
    })

    result = await _pursue(
        "a delegated goal", _intention(), [], _spec(), config,
        memory=_Memory(), gateway=gateway, workdir=".",
    )

    assert result["status"] == "failed"
    assert result["stage"] == "step s1"
    assert gateway.purposes.count("thread.step") == 2


async def test_a_failed_envelope_is_not_closed_as_done(config):
    """The manager half of the symptom: a worker that returns `{"status":
    "failed"}` was recorded as `done, "thread completed"`."""
    memory = _Memory()

    async def _failing_worker(spec: dict[str, Any]) -> dict[str, Any]:
        return {"status": "failed", "goal": spec.get("goal"), "error": "the provider went away"}

    manager = ThreadManager(config, memory, worker_factory=_failing_worker)
    handle = await manager.spawn_spec({"goal": "a delegated goal", "role": "delegate"})
    await manager.wait(handle)

    assert handle.status == "failed"
    assert handle.error == "the provider went away"
    assert len(memory.closed) == 1
    _, status, reason = memory.closed[0]
    assert status == "parked"
    assert "the provider went away" in reason


async def test_an_unknown_subagent_outcome_is_not_closed_as_done(config):
    """`unknown` is a sub-agent's honest verdict that it may or may not have
    changed the machine. Treating the returned envelope as a success closed
    the intention `done` over work nobody can verify -- the same optimistic
    ending `blocked` and `failed` are already excluded from."""
    memory = _Memory()

    async def _unknown_worker(spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "agent": "apex", "status": "unknown", "goal": spec.get("goal"),
            "summary": "the click may or may not have landed",
        }

    manager = ThreadManager(config, memory, worker_factory=_unknown_worker)
    handle = await manager.spawn_spec({"goal": "a delegated goal", "role": "apex"})
    await manager.wait(handle)

    assert handle.status == "failed"
    assert "may or may not" in (handle.error or "")
    assert len(memory.closed) == 1
    _, status, _reason = memory.closed[0]
    assert status == "parked"


async def test_a_crashing_worker_parks_with_a_reason(config):
    """A worker that raises used to close its intention as `failed` — the one
    status the loop never writes, and the one stage notices cannot carry."""
    memory = _Memory()

    async def _crashing_worker(spec: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("the worktree vanished")

    manager = ThreadManager(config, memory, worker_factory=_crashing_worker)
    handle = await manager.spawn_spec({"goal": "a delegated goal", "role": "delegate"})
    await manager.wait(handle)

    assert handle.status == "failed"
    assert len(memory.closed) == 1
    _, status, reason = memory.closed[0]
    assert status == "parked"
    assert "the worktree vanished" in reason


async def test_a_successful_thread_is_still_closed_done(config):
    """The honest-recording fix must not turn real successes into failures."""
    memory = _Memory()

    async def _good_worker(spec: dict[str, Any]) -> str:
        return "the answer"

    manager = ThreadManager(config, memory, worker_factory=_good_worker)
    handle = await manager.spawn_spec({"goal": "a delegated goal", "role": "delegate"})
    result = await manager.wait(handle)

    assert result == "the answer"
    assert handle.status == "done"
    assert memory.closed[0][1] == "done"


def test_the_failure_envelope_vocabulary_is_shared():
    """The in-process manager and the deployed `clio worker` process both read
    this envelope; the helpers are one definition so the two cannot drift."""
    from clio.threads.manager import failure_reason, is_failed_result

    for status in (
        "failed", "unavailable", "blocked", "unknown", "timeout",
        "no-gateway", "missing-intention",
    ):
        assert is_failed_result({"status": status}), status
    assert not is_failed_result({"status": "done"})
    assert not is_failed_result({"verdict": "needs_human"})
    assert not is_failed_result("the answer")
    assert not is_failed_result(None)
    assert failure_reason({"status": "failed", "error": "boom"}) == "boom"
    assert failure_reason({"status": "blocked", "summary": "waiting on a person"}) == "waiting on a person"
    assert failure_reason({"status": "failed"}) == "failed"


async def test_orphaned_handles_release_their_slots(config):
    """A worker that dies unpublished must not hold a delegate slot forever.

    In process mode a worker publishes its result over the bus, and one that
    dies before publishing leaves a handle saying "running" for the life of
    the manager. Four of those and `spawn_spec` refused every new delegate --
    the agent stopped delegating with nothing dead to show for it. The
    reaper reclaims stale handles: marked failed, intention parked (where
    the reopen sweep can pick the work back up), slot returned.
    """
    import asyncio

    memory = _Memory()
    # Zero means "stale immediately", which is what an orphan is from the
    # moment its worker dies; real workers get the generous default.
    config.threads.stale_after_s = 0.0

    async def _hanging_worker(spec: dict[str, Any]) -> Any:
        await asyncio.sleep(3600)

    manager = ThreadManager(config, memory, worker_factory=_hanging_worker)
    held = [
        await manager.spawn_spec({"goal": "stuck goal", "role": "delegate"})
        for _ in range(config.threads.max_concurrent)
    ]
    assert manager._running_count() == config.threads.max_concurrent

    # The spawn that used to raise forever, because four handles that would
    # never answer were counted as four live delegates.
    after = await manager.spawn_spec({"goal": "the next thing", "role": "delegate"})
    assert after.status == "running"
    for handle in held:
        assert handle.status == "failed"
        assert "presumed dead" in handle.error
    assert all(status == "parked" for _, status, _ in memory.closed), \
        "the orphaned work is parked, which is where the reopen sweep finds it"
    # Let the cancelled tasks run their finally-blocks so the test ends clean.
    for task in [h.task for h in held if h.task is not None]:
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_crashing_subagent_is_retried(config, monkeypatch: pytest.MonkeyPatch):
    """A sub-agent that crashes once gets a second attempt before the thread
    reports the crash."""
    config.threads.subagent_attempts = 2
    from clio.subagents.base import SubAgentResult

    runs = {"count": 0}

    class _FlakyAgent:
        async def run(self, goal: str, workdir: str) -> SubAgentResult:
            runs["count"] += 1
            if runs["count"] == 1:
                raise RuntimeError("port not open yet")
            return SubAgentResult(agent="foundry", status="done", summary="3/3 merged")

    class _FakeRegistry:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get(self, role: str) -> _FlakyAgent:
            return _FlakyAgent()

        def describe(self) -> list[str]:
            return []

    monkeypatch.setattr("clio.subagents.SubagentRegistry", _FakeRegistry)

    result = await run_thread(
        {"id": "thread-sub", "goal": "build it", "role": "foundry",
         "intention_id": str(uuid.uuid4())},
        config, memory=_Memory(), gateway=object(),
    )

    assert runs["count"] == 2
    assert result["status"] == "done"


async def test_a_subagent_that_always_crashes_reports_the_crash(config, monkeypatch: pytest.MonkeyPatch):
    config.threads.subagent_attempts = 2
    runs = {"count": 0}

    class _DeadAgent:
        async def run(self, goal: str, workdir: str) -> Any:
            runs["count"] += 1
            raise RuntimeError("deterministic crash")

    class _FakeRegistry:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def get(self, role: str) -> _DeadAgent:
            return _DeadAgent()

        def describe(self) -> list[str]:
            return []

    monkeypatch.setattr("clio.subagents.SubagentRegistry", _FakeRegistry)

    result = await run_thread(
        {"id": "thread-sub", "goal": "build it", "role": "foundry",
         "intention_id": str(uuid.uuid4())},
        config, memory=_Memory(), gateway=object(),
    )

    assert runs["count"] == 2
    assert result["status"] == "failed"
    assert "deterministic crash" in result["error"]
