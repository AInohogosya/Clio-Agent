from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from typing import Any

from clio.config import ClioConfig
from clio.observability import metrics
from clio.observability.logger import get_logger
from clio.schemas.events import EventKind

logger = get_logger("clio.threads.manager")

# Result envelopes that say the work did not finish, whatever the worker's
# return value. A worker that *raises* is already handled below; the missing
# half was the worker that catches its own failure and returns a dict — the
# plain reading of "the worker returned" is "the work is done", and a pursuit
# that ended in `{"status": "failed"}` was closed as `done, "thread completed"`
# in the intention store, with the error still sitting in the result nobody
# reconciled. These are the statuses the worker's own vocabulary uses.
# `unknown` belongs here even though it is not a clean failure: a sub-agent
# that stopped without being able to verify what it did may or may not have
# changed the machine, and closing that intention as `done` is exactly the
# optimistic ending the status vocabulary exists to prevent.
_FAILED_RESULT_STATUSES = frozenset({
    "failed", "unavailable", "blocked", "unknown", "timeout",
    "no-gateway", "missing-intention",
})


def ev_weight(expected_value: float | None, estimated_cost_usd: float | None) -> float:
    """One child's claim on a shared budget: value per dollar."""
    ev = 0.5 if expected_value is None else max(0.0, float(expected_value))
    cost = 0.05 if estimated_cost_usd is None else max(1e-6, float(estimated_cost_usd))
    return max(1e-9, ev / cost)


def allocate_child_budget(
    parent_budget_usd: float | None,
    parent_spent_usd: float = 0.0,
    *,
    expected_value: float | None = None,
    estimated_cost_usd: float | None = None,
    peers: list[dict[str, Any]] | None = None,
    h2_cap_usd: float | None = None,
) -> float | None:
    """This child intention's budget, carved EV-proportionally.

    When `peers` are given the whole pool is split by value-per-dollar;
    otherwise this child gets its weight against the field cost. Capped at
    the H2 ceiling when one is supplied. Pure function for testability.
    """
    if parent_budget_usd is None:
        return None
    pool = max(0.0, float(parent_budget_usd) - max(0.0, float(parent_spent_usd)))
    if h2_cap_usd is not None:
        pool = min(pool, max(0.0, float(h2_cap_usd)))
    if not peers:
        return round(pool, 4)
    from clio.memory.store import allocate_ev_budgets

    children = [
        {"key": "self", "expected_value": expected_value, "estimated_cost_usd": estimated_cost_usd},
        *peers,
    ]
    return allocate_ev_budgets(pool, children, h2_cap=None)["self"]


def is_failed_result(result: Any) -> bool:
    """Whether a worker's returned envelope says the work did not finish.

    Shared instead of duplicated: the in-process manager and the deployed
    `clio worker` process both receive this envelope, and both used to read
    "the worker returned" as "the work is done".
    """
    return (
        isinstance(result, dict)
        and str(result.get("status", "")) in _FAILED_RESULT_STATUSES
    )


def failure_reason(result: dict[str, Any]) -> str:
    for key in ("error", "summary", "status"):
        value = str(result.get(key) or "").strip()
        if value:
            return value[:300]
    return "worker reported failure"


class ThreadHandle:
    def __init__(self, thread_id: str, spec: dict[str, Any], task: asyncio.Task | None = None):
        self.thread_id = thread_id
        self.spec = spec
        self.task = task
        self.status = "running"
        self.result: Any = None
        self.error: str | None = None
        self.done_event = asyncio.Event()
        self.started_at = time.monotonic()
        """For the orphan reaper: when this handle began, so a worker that
        will never report back can be told from one that is merely slow."""

    def finish(self, result: Any = None, error: str | None = None) -> None:
        self.result = result
        self.error = error
        self.status = "failed" if error else "done"
        self.done_event.set()


class ThreadManager:
    """Threads are parallel instances of the SAME framework sharing one identity
    kernel and one long-term memory [D-01, D-29].

    Constraints: max 4 concurrent delegates, max depth 2, budget carved from
    the parent intention. Only the Self speaks externally; threads report
    through the bus.
    """

    def __init__(
        self,
        config: ClioConfig,
        memory: Any,
        bus: Any = None,
        audit: Any = None,
        worker_factory: Any = None,
        gateway: Any = None,
        toolhost: Any = None,
        self_model: Any = None,
    ):
        self.config = config
        self.memory = memory
        self.bus = bus
        self.audit = audit
        self.gateway = gateway
        self.toolhost = toolhost
        self.self_model = self_model
        """Threads that have not finished yet, keyed by thread id.

        Inserted when the handle is created and removed the moment it reaches a
        terminal state, so its size is a fact about work in progress. A caller
        that wants a handle after the fact already holds the object, so losing it
        from this table costs nothing — and leaving it here means the table's
        length, which is the one number somebody would measure, says something
        true.
        """
        self.active: dict[str, ThreadHandle] = {}
        """The recent past, newest last, capped at `finished_retention`.

        Bounded rather than unbounded because the alternative is the leak this
        replaces: a manager that ran for a week held every thread it had ever
        run, results and specs both, and none of it was reachable.
        """
        self.finished: OrderedDict[str, ThreadHandle] = OrderedDict()
        self.finished_retention = max(0, config.threads.retention)
        self.worker_factory = worker_factory
        self.mode = config.threads.mode
        if self.bus is not None and self.mode == "process":
            self.bus.subscribe(EventKind.THREAD_RESULT, self._on_process_result)

    def _running_count(self) -> int:
        return sum(1 for h in self.active.values() if h.status == "running")

    def _retire(self, thread_id: str) -> None:
        """Move a finished handle out of `active` and into the bounded LRU.

        `retention: 0` means the handle is simply dropped. The caller's reference
        still works; only the post-mortem window closes, which is the point of
        asking for zero.
        """
        handle = self.active.pop(thread_id, None)
        if handle is None or self.finished_retention == 0:
            return
        self.finished[thread_id] = handle
        self.finished.move_to_end(thread_id)
        while len(self.finished) > self.finished_retention:
            self.finished.popitem(last=False)
        metrics.THREADS_FINISHED_KEPT.set(len(self.finished))

    async def _reap_stale(self) -> None:
        """Reclaim running handles whose worker will never report back.

        The failure this exists for has no corpse: a worker that dies
        before publishing its result leaves the handle saying "running"
        forever, and every one of those permanently holds one of the
        `max_concurrent` slots. Four orphans and the agent can no longer
        delegate anything -- the "agent stopped" failure with nothing dead
        to show for it. A stale handle is marked failed, its intention is
        parked (where the reopen sweep can give the work another chance),
        and its slot is given back.

        Called when the slots are full and whenever a `wait` times out, so
        it costs nothing while threads are healthy.
        """
        stale_after = float(getattr(self.config.threads, "stale_after_s", 7200.0))
        now = time.monotonic()
        for thread_id, handle in list(self.active.items()):
            if handle.status != "running" or now - handle.started_at < stale_after:
                continue
            logger.warning(
                "threads.reaping_stale", thread_id=thread_id,
                age_s=round(now - handle.started_at), stale_after_s=stale_after,
            )
            handle.finish(
                error=f"thread presumed dead: no result within {round(stale_after)}s "
                      "(orphan reaper)",
            )
            if handle.spec.get("intention_id"):
                try:
                    from clio.schemas.gsl import IntentionStatus

                    # Parked rather than failed, for the same reason a crashed
                    # thread is: the worker is gone, not the goal, and a
                    # parked row is one the reopen sweep can pick back up.
                    await self.memory.close_intention(
                        _uuid(handle.spec["intention_id"]),
                        IntentionStatus.parked.value,
                        "thread presumed dead: no result before the staleness window",
                    )
                except Exception:
                    logger.exception("threads.close_intention_failed")
            if handle.task is not None and not handle.task.done():
                # The `_run` coroutine's finally-block retires and publishes
                # once the cancellation lands, so the reaper only cancels and
                # lets that path do its own bookkeeping.
                handle.task.cancel()
            else:
                # Process mode, or a task that already ended without a
                # result: nothing is left to run the finally-block, so the
                # retirement and the bus event are the reaper's to send.
                self._retire(thread_id)
                if self.bus is not None:
                    try:
                        await self.bus.publish(
                            EventKind.THREAD_RESULT,
                            {
                                "thread_id": handle.thread_id,
                                "goal": handle.spec.get("goal"),
                                "status": handle.status,
                                "result": handle.result,
                                "error": handle.error,
                                "intention_id": handle.spec.get("intention_id"),
                            },
                        )
                    except Exception:
                        logger.exception("threads.result_publish_failed")
        metrics.THREADS_ACTIVE.set(self._running_count())

    async def spawn_spec(self, spec: dict[str, Any]) -> ThreadHandle:
        if self._running_count() >= self.config.threads.max_concurrent:
            # Full is often not full: the oldest "running" handles may be
            # orphans of workers that died unpublished, and refusing a new
            # delegate because of them is the agent being stopped by its
            # own accounting. Reclaim first, refuse only if real work
            # still holds every slot.
            await self._reap_stale()
        if self._running_count() >= self.config.threads.max_concurrent:
            raise RuntimeError(
                f"max {self.config.threads.max_concurrent} concurrent delegates reached"
            )
        depth = int(spec.get("depth", 1))
        if depth > self.config.threads.max_depth:
            raise RuntimeError(f"delegation depth {depth} exceeds max {self.config.threads.max_depth}")

        thread_id = f"thread-{uuid.uuid4().hex[:12]}"
        parent_intention_id = spec.get("parent_intention_id")
        budget_usd = spec.get("budget_usd")
        if budget_usd is None and parent_intention_id:
            try:
                from uuid import UUID

                parent = await self.memory.get_intention(UUID(str(parent_intention_id)))
                if parent is not None and parent.budget_usd is not None:
                    h2_cap = float(
                        getattr(self.config.permissions.hard_limits, "H2_capital_at_risk_usd", 0) or 0
                    ) or None
                    peers = spec.get("peers") or spec.get("siblings")
                    budget_usd = allocate_child_budget(
                        parent.budget_usd, parent.spent_usd,
                        expected_value=spec.get("expected_value"),
                        estimated_cost_usd=spec.get("estimated_cost_usd"),
                        peers=peers if isinstance(peers, list) else None,
                        h2_cap_usd=h2_cap,
                    )
            except Exception:
                logger.exception("threads.budget_carve_failed")

        intention_id = None
        if spec.get("role") != "verifier":
            intention_id = await self.memory.create_intention(
                title=str(spec.get("goal", "delegated goal"))[:200],
                desired_end_state=str(spec.get("goal", "")),
                kind="task",
                parent_id=_uuid(parent_intention_id),
                origin="thread",
                priority=0.5,
                budget_usd=budget_usd,
            )

        full_spec = {
            **spec,
            "id": thread_id,
            "intention_id": str(intention_id) if intention_id else None,
            "budget_usd": budget_usd,
        }
        handle = ThreadHandle(thread_id, full_spec)
        self.active[thread_id] = handle
        metrics.THREADS_ACTIVE.set(self._running_count())

        if self.mode == "process" and self.bus is not None:
            await self.bus.publish(EventKind.THREAD_REQUEST, full_spec)
            logger.info("threads.requested", thread_id=thread_id, role=spec.get("role", "delegate"))
            return handle

        worker = self.worker_factory or self._default_worker
        handle.task = asyncio.create_task(self._run(handle, worker))
        logger.info("threads.spawned", thread_id=thread_id, role=spec.get("role", "delegate"))
        return handle

    async def _on_process_result(self, event: Any) -> None:
        thread_id = event.payload.get("thread_id")
        handle = self.active.get(thread_id)
        if handle is None or handle.status != "running":
            return
        if event.payload.get("status") == "failed":
            handle.finish(error=str(event.payload.get("error", "worker failed")))
        else:
            handle.finish(result=event.payload.get("result"))
        self._retire(thread_id)
        metrics.THREADS_ACTIVE.set(self._running_count())

    async def _run(self, handle: ThreadHandle, worker: Any) -> None:
        try:
            result = await worker(handle.spec)
            if is_failed_result(result):
                # The worker ended its own task on an issue and said so in the
                # envelope. Recorded the way the solver loop records a goal it
                # cannot finish: parked, with the reason, so the ledger and the
                # stage notices carry the ending instead of the intention
                # silently reading `done, "thread completed"`.
                reason = failure_reason(result)
                handle.finish(error=reason)
                if handle.spec.get("intention_id"):
                    try:
                        from clio.schemas.gsl import IntentionStatus

                        await self.memory.close_intention(
                            _uuid(handle.spec["intention_id"]),
                            IntentionStatus.parked.value, f"thread failed: {reason}",
                        )
                    except Exception:
                        logger.exception("threads.close_intention_failed")
            else:
                handle.finish(result=result)
                if handle.spec.get("intention_id"):
                    try:
                        from clio.schemas.gsl import IntentionStatus

                        await self.memory.close_intention(
                            _uuid(handle.spec["intention_id"]),
                            IntentionStatus.done.value, "thread completed",
                        )
                    except Exception:
                        logger.exception("threads.close_intention_failed")
        except Exception as exc:
            logger.exception("threads.worker_failed", thread_id=handle.thread_id)
            handle.finish(error=str(exc))
            if handle.spec.get("intention_id"):
                try:
                    from clio.schemas.gsl import IntentionStatus

                    # Parked rather than failed, for the same reason the solver
                    # loop parks: the reason is written and notifiable, and a
                    # crashed thread is a goal that could not be finished, not
                    # a verdict on the goal itself.
                    await self.memory.close_intention(
                        _uuid(handle.spec["intention_id"]),
                        IntentionStatus.parked.value, f"thread crashed: {exc}",
                    )
                except Exception:
                    logger.exception("threads.close_intention_failed")
        finally:
            metrics.THREADS_ACTIVE.set(self._running_count())
            # Retire after the result has been published, so a subscriber woken by
            # the bus event can still find the handle in `active` while it reads
            # it — the ordering that keeps `_on_process_result`'s own retirement
            # from racing this one.
            self._retire(handle.thread_id)
            if self.bus is not None:
                try:
                    await self.bus.publish(
                        EventKind.THREAD_RESULT,
                        {
                            "thread_id": handle.thread_id,
                            "goal": handle.spec.get("goal"),
                            "status": handle.status,
                            "result": handle.result,
                            "error": handle.error,
                            "intention_id": handle.spec.get("intention_id"),
                        },
                    )
                except Exception:
                    logger.exception("threads.result_publish_failed")

    async def wait(self, handle: ThreadHandle, timeout_s: float | None = None) -> Any:
        try:
            if timeout_s is None:
                await handle.done_event.wait()
            else:
                await asyncio.wait_for(handle.done_event.wait(), timeout=timeout_s)
        except TimeoutError:
            # A wait that ran out is also the cheapest moment to check
            # whether the thing being waited on is ever going to answer:
            # the caller has already decided to stop waiting, and a
            # handle that is stale by now is an orphan whose slot is owed
            # back either way.
            await self._reap_stale()
            return None
        return handle.result

    def _default_worker(self, spec: dict[str, Any]) -> Any:
        from clio.threads.worker import run_thread

        return run_thread(spec, self.config, self.memory, self.gateway, toolhost=self.toolhost, self_model=self.self_model)

    async def spawn_ready_branches(
        self,
        parent_intention_id: Any,
        *,
        extra_spec: dict[str, Any] | None = None,
    ) -> list[ThreadHandle]:
        """Dispatch ready DAG branches in parallel across ThreadManager.

        Reads the parent's ready children (depends_on predecessors done) and
        spawns one thread per branch, each budget-carved EV-proportionally.
        Returns the handles; failures are logged, not raised, so one bad
        branch never blocks its siblings.
        """
        from uuid import UUID

        handles: list[ThreadHandle] = []
        try:
            ready = await self.memory.get_ready_intentions()
        except Exception:
            logger.exception("threads.ready_lookup_failed")
            return handles
        parent_uuid = None
        try:
            parent_uuid = UUID(str(parent_intention_id))
        except (ValueError, TypeError):
            parent_uuid = None
        candidates: list[Any] = []
        for intention in ready:
            if parent_uuid is not None and getattr(intention, "parent_id", None) != parent_uuid:
                continue
            if parent_uuid is None and str(getattr(intention, "parent_id", "") or "") != str(parent_intention_id):
                continue
            candidates.append(intention)
        if not candidates and parent_uuid is not None:
            # Fall back to the parent's direct children when the global ready
            # list is filtered differently.
            try:
                subgraph = await self.memory.get_intention_subgraph(parent_uuid)
                candidates = [
                    n for n in subgraph["intentions"]
                    if n.id != parent_uuid and n.status in ("active", "proposed", "in_progress", "waiting")
                ]
            except Exception:
                candidates = []
        peers = [
            {
                "key": str(c.id),
                "expected_value": getattr(c, "expected_value", None),
                "estimated_cost_usd": (c.resume_state or {}).get("estimated_cost_usd")
                if isinstance(getattr(c, "resume_state", None), dict) else None,
            }
            for c in candidates
        ]
        for intention in candidates[: max(0, self.config.threads.max_concurrent)]:
            try:
                spec: dict[str, Any] = {
                    "goal": intention.title,
                    "parent_intention_id": str(parent_intention_id),
                    "peers": peers,
                    "expected_value": getattr(intention, "expected_value", None),
                    **(extra_spec or {}),
                }
                handle = await self.spawn_spec(spec)
                handles.append(handle)
            except Exception:
                logger.exception("threads.spawn_branch_failed", intention=str(intention.id))
        return handles


def _uuid(value: str | None):
    if value is None:
        return None
    from uuid import UUID

    return UUID(str(value))
