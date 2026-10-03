from __future__ import annotations

import asyncio
import uuid
from typing import Any

from ethos.config import EthosConfig
from ethos.observability import metrics
from ethos.observability.logger import get_logger
from ethos.schemas.events import EventKind

logger = get_logger("ethos.threads.manager")


class ThreadHandle:
    def __init__(self, thread_id: str, spec: dict[str, Any], task: asyncio.Task | None = None):
        self.thread_id = thread_id
        self.spec = spec
        self.task = task
        self.status = "running"
        self.result: Any = None
        self.error: str | None = None
        self.done_event = asyncio.Event()

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
        self.active: dict[str, ThreadHandle] = {}
        self.worker_factory = worker_factory
        self.mode = config.threads.mode
        if self.bus is not None and self.mode == "process":
            self.bus.subscribe(EventKind.THREAD_RESULT, self._on_process_result)

    def _running_count(self) -> int:
        return sum(1 for h in self.active.values() if h.status == "running")

    async def spawn_spec(self, spec: dict[str, Any]) -> ThreadHandle:
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

    async def _run(self, handle: ThreadHandle, worker: Any) -> None:
        try:
            result = await worker(handle.spec)
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

                    await self.memory.close_intention(
                        _uuid(handle.spec["intention_id"]),
                        IntentionStatus.failed.value, f"thread failed: {exc}",
                    )
                except Exception:
                    pass
        finally:
            metrics.THREADS_ACTIVE.set(self._running_count())
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
