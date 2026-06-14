"""
Sub-Agent Manager — orchestrates spawn, monitor, kill, collect.

Manages a pool of sub-agents executing in parallel via ThreadPoolExecutor.
Handles lifecycle, timeouts, result collection, and graceful termination.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .base import SubAgentBase, SubAgentResult, SubAgentStatus
from .context import SubAgentContext
from .registry import SubAgentRegistry, get_global_registry
from ..utils.logger import get_logger

logger = get_logger("sub_agent.manager")


@dataclass
class SubAgentHandle:
    """Handle for tracking a spawned sub-agent."""
    agent_id: str
    agent_type: str
    future: Future[SubAgentResult]
    context: SubAgentContext
    spawned_at: float = field(default_factory=time.monotonic)
    result: Optional[SubAgentResult] = None


class SubAgentManager:
    """
    Manages parallel execution of sub-agents.

    Features:
    - Spawn sub-agents with isolated contexts
    - Parallel execution via ThreadPoolExecutor
    - Timeout enforcement per sub-agent
    - Kill individual or all sub-agents
    - Collect results as they complete
    - Configurable max parallelism

    Usage:
        manager = SubAgentManager(config=config)

        ctx = SubAgentContext(task="Implement feature X", config=config)
        handle = manager.spawn("coder", ctx)

        # Wait for results
        results = manager.wait_all()
        # Or collect as they complete
        for result in manager.collect_completed():
            print(result.output)

        manager.shutdown()
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        registry: Optional[SubAgentRegistry] = None,
        max_workers: int = 0,
    ) -> None:
        """
        Args:
            config: Application configuration dict (reads sub_agents section).
            registry: Sub-agent registry (uses global if not provided).
            max_workers: Max concurrent sub-agents (0 = from config, default 4).
        """
        self.config = config or {}
        sa_config = self.config.get("sub_agents", {})
        self._registry = registry or get_global_registry()
        self._max_workers = max_workers or sa_config.get("max_parallel", 4)
        self._default_timeout = sa_config.get("timeout", 600)
        self._enabled = sa_config.get("enabled", True)

        # Internal state
        self._handles: Dict[str, SubAgentHandle] = {}
        self._lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._results: List[SubAgentResult] = []
        self._shutdown = False

        logger.info(
            f"SubAgentManager initialized (max_workers={self._max_workers}, "
            f"timeout={self._default_timeout}s)"
        )

    @property
    def is_enabled(self) -> bool:
        """Whether the sub-agent system is enabled."""
        return self._enabled

    def spawn(self, agent_type: str, context: SubAgentContext) -> SubAgentHandle:
        """
        Spawn a new sub-agent of the given type.

        Args:
            agent_type: Registered type name (e.g. "coder").
            context: Context for the sub-agent.

        Returns:
            SubAgentHandle for tracking and result collection.

        Raises:
            RuntimeError: If manager is shutdown or sub-agents disabled.
            KeyError: If agent_type is not registered.
        """
        if self._shutdown:
            raise RuntimeError("SubAgentManager is shutdown — cannot spawn new agents")
        if not self._enabled:
            raise RuntimeError("Sub-agent system is disabled in config")

        # Resolve model_runner from config context
        if context.model_runner is None:
            context.model_runner = self._get_model_runner_from_config()

        # Create agent instance from registry
        agent = self._registry.create(agent_type, context)

        # Create handle
        handle = SubAgentHandle(
            agent_id=agent.agent_id,
            agent_type=agent_type,
            future=None,  # set after submission
            context=context,
        )

        # Submit to thread pool
        executor = self._get_executor()
        future = executor.submit(self._execute_agent, agent, handle)

        with self._lock:
            handle.future = future
            self._handles[handle.agent_id] = handle

        logger.info(
            f"Spawned sub-agent: type={agent_type}, id={handle.agent_id}"
        )
        return handle

    def _execute_agent(
        self, agent: SubAgentBase, handle: SubAgentHandle
    ) -> SubAgentResult:
        """Run a sub-agent and handle timeout."""
        timeout = handle.context.timeout_seconds or self._default_timeout

        # Wrap with timeout handling
        result_container: List[SubAgentResult] = []
        error_container: List[Exception] = []

        def _run():
            try:
                result_container.append(agent.run())
            except Exception as e:
                error_container.append(e)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            agent.kill()
            result = SubAgentResult(
                agent_id=agent.agent_id,
                agent_type=agent.agent_type,
                success=False,
                output="",
                error=f"Sub-agent timed out after {timeout}s",
                duration_ms=timeout * 1000,
            )
            logger.warning(f"Sub-agent {agent.agent_id} timed out")
            return result

        if error_container:
            raise error_container[0]

        return result_container[0]

    def wait_all(self, timeout: Optional[float] = None) -> List[SubAgentResult]:
        """
        Wait for all spawned sub-agents to complete.

        Args:
            timeout: Optional wall-clock timeout in seconds.

        Returns:
            List of SubAgentResults in completion order.
        """
        with self._lock:
            futures = {
                h.agent_id: h.future for h, h.future in (
                    (h, h.future) for h in self._handles.values()
                )
                if h.future is not None
            }

        if not futures:
            return []

        results: List[SubAgentResult] = []
        start = time.monotonic()

        for future in as_completed(futures.values(), timeout=timeout):
            try:
                result = future.result()
                results.append(result)
                self._results.append(result)
            except Exception as e:
                agent_id = [
                    aid for aid, f in futures.items() if f == future
                ]
                aid = agent_id[0] if agent_id else "unknown"
                results.append(SubAgentResult(
                    agent_id=aid,
                    agent_type="unknown",
                    success=False,
                    output="",
                    error=f"Sub-agent execution failed: {e}",
                ))

        elapsed = time.monotonic() - start
        logger.info(f"All sub-agents completed in {elapsed:.1f}s ({len(results)} results)")
        return results

    def collect_completed(
        self, timeout: float = 0.1
    ):
        """
        Generator that yields results as they complete.

        Args:
            timeout: Poll interval in seconds.

        Yields:
            SubAgentResult objects.
        """
        pending: Dict[str, Future] = {}
        with self._lock:
            for h in self._handles.values():
                if h.future is not None and h.agent_id not in pending:
                    pending[h.agent_id] = h.future

        while pending:
            done_ids = []
            for agent_id, future in pending.items():
                if future.done():
                    try:
                        result = future.result()
                        yield result
                        self._results.append(result)
                    except Exception as e:
                        yield SubAgentResult(
                            agent_id=agent_id,
                            agent_type="unknown",
                            success=False,
                            output="",
                            error=str(e),
                        )
                    done_ids.append(agent_id)

            for aid in done_ids:
                del pending[aid]

            if pending:
                time.sleep(timeout)

    def kill(self, agent_id: str) -> bool:
        """
        Kill a specific sub-agent by ID.

        Returns True if found and killed, False if not found or already done.
        """
        with self._lock:
            handle = self._handles.get(agent_id)
            if handle is None:
                return False
            if handle.future is None or handle.future.done():
                return False

        # Cancel the future if not yet started
        cancelled = handle.future.cancel()
        if cancelled:
            logger.info(f"Cancelled sub-agent {agent_id} before execution")
            return True

        # If running, mark for cleanup (thread-based execution)
        # The agent itself needs to check for a kill signal periodically
        logger.warning(f"Cannot cancel running sub-agent {agent_id} — marking for cleanup")
        return False

    def kill_all(self) -> int:
        """
        Kill all active sub-agents.

        Returns the number of agents killed.
        """
        count = 0
        with self._lock:
            agent_ids = list(self._handles.keys())

        for agent_id in agent_ids:
            if self.kill(agent_id):
                count += 1

        logger.info(f"Killed {count} sub-agent(s)")
        return count

    def get_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a sub-agent."""
        with self._lock:
            handle = self._handles.get(agent_id)
            if handle is None:
                return None

            future = handle.future
            if future is None:
                return {"agent_id": agent_id, "status": "pending"}

            if future.done():
                try:
                    result = future.result()
                    return {
                        "agent_id": agent_id,
                        "agent_type": handle.agent_type,
                        "status": "completed" if result.success else "failed",
                        "duration_ms": result.duration_ms,
                    }
                except Exception as e:
                    return {
                        "agent_id": agent_id,
                        "agent_type": handle.agent_type,
                        "status": "error",
                        "error": str(e),
                    }

            return {
                "agent_id": agent_id,
                "agent_type": handle.agent_type,
                "status": "running",
                "elapsed_s": time.monotonic() - handle.spawned_at,
            }

    def list_active(self) -> List[Dict[str, Any]]:
        """List all active (not yet completed) sub-agents."""
        with self._lock:
            return [
                {
                    "agent_id": h.agent_id,
                    "agent_type": h.agent_type,
                    "elapsed_s": time.monotonic() - h.spawned_at,
                }
                for h in self._handles.values()
                if h.future is not None and not h.future.done()
            ]

    def shutdown(self, wait: bool = True, cancel_pending: bool = False) -> None:
        """
        Shutdown the manager and release resources.

        Args:
            wait: If True, wait for running agents to complete.
            cancel_pending: If True, cancel pending futures before shutdown.
        """
        self._shutdown = True

        if cancel_pending:
            with self._lock:
                for h in self._handles.values():
                    if h.future and not h.future.done():
                        h.future.cancel()

        if self._executor is not None:
            self._executor.shutdown(wait=wait)
            self._executor = None

        logger.info("SubAgentManager shutdown complete")

    def collect_results(self) -> List[SubAgentResult]:
        """Return all collected results."""
        return list(self._results)

    def _get_executor(self) -> ThreadPoolExecutor:
        """Get or create the thread pool executor."""
        if self._executor is None or self._executor._shutdown:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="sub_agent",
            )
        return self._executor

    def _get_model_runner_from_config(self):
        """Create a ModelRunner from config if available."""
        try:
            from ..external_integration.model_runner import ModelRunner
            from ..utils.config import load_config
            cfg = load_config()
            api_cfg = getattr(cfg, "api", None)
            provider = getattr(api_cfg, "preferred_provider", None) if api_cfg else None
            model = None
            if api_cfg and hasattr(api_cfg, "models"):
                models = getattr(api_cfg, "models", {})
                if hasattr(models, "get"):
                    model = models.get(provider) if provider else None
            return ModelRunner(
                provider=provider,
                model=model,
                config=self.config.get("api", {}),
            )
        except Exception as e:
            logger.warning(f"Could not create ModelRunner from config: {e}")
            return None

    def __enter__(self) -> SubAgentManager:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()
