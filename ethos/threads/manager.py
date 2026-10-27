from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from typing import Any

from ethos.config import EthosConfig
from ethos.observability import metrics
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind

logger = get_logger("ethos.threads.manager")

# Result envelopes that say the work did not finish, whatever the worker's
# return value. A worker that *raises* is already handled below; the missing
# half was the worker that catches its own failure and returns a dict — the
# plain reading of "the worker returned" is "the work is done", and a pursuit
# that ended in `{"status": "failed"}` was closed as `done, "thread completed"`
# in the intention store, with the error still sitting in the result nobody
# reconciled. These are the statuses the worker's own vocabulary uses.
_FAILED_RESULT_STATUSES = frozenset({
    "failed", "unavailable", "blocked", "timeout", "no-gateway", "missing-intention",
})


def is_failed_result(result: Any) -> bool:
    """Whether a worker's returned envelope says the work did not finish.

    Shared instead of duplicated: the in-process manager and the deployed
    `ethos worker` process both receive this envelope, and both used to read
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
        config: EthosConfig,
        memory: Any,
        bus: Any = None,
        audit: Any = None,
        worker_factory: Any = None,
        gateway: Any = None,
    ):
        self.config = config
        self.memory = memory
        self.bus = bus
        self.audit = audit
        self.gateway = gateway
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
                    from ethos.schemas.gsl import IntentionStatus

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
                    remaining = max(0.0, parent.budget_usd - parent.spent_usd)
                    budget_usd = round(remaining / 2, 4)
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
                        from ethos.schemas.gsl import IntentionStatus

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
                        from ethos.schemas.gsl import IntentionStatus

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
                    from ethos.schemas.gsl import IntentionStatus

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
        from ethos.threads.worker import run_thread

        return run_thread(spec, self.config, self.memory, self.gateway)


def _uuid(value: str | None):
    if value is None:
        return None
    from uuid import UUID

    return UUID(str(value))
