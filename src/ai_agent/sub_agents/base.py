"""
Base class for the Sub-Agent system.

Every sub-agent inherits from SubAgentBase and implements the `_run` method.
The lifecycle is: spawn -> run -> report -> terminate.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger


class SubAgentStatus(Enum):
    """Status of a sub-agent."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    KILLED = "killed"


class SubAgentState(Enum):
    """Lifecycle state of a sub-agent."""
    SPAWNED = "spawned"
    INITIALIZING = "initializing"
    EXECUTING = "executing"
    REPORTING = "reporting"
    TERMINATED = "terminated"


@dataclass
class SubAgentResult:
    """Result returned by a sub-agent after execution."""
    agent_id: str
    agent_type: str
    success: bool
    output: str
    error: Optional[str] = None
    duration_ms: float = 0.0
    artifacts: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "artifacts": self.artifacts,
            "metadata": self.metadata,
        }


class SubAgentBase(ABC):
    """
    Abstract base class for all sub-agents.

    Sub-agents receive a context, execute a specialized task, and return
    a result. They run in isolation from the main agent's execution loop
    but share the same model runner for LLM calls.

    Lifecycle:
        1. __init__ — construct with context
        2. initialize — set up resources (called by manager)
        3. _run — execute the task (implemented by subclass)
        4. report — build SubAgentResult (called by manager)
        5. cleanup — release resources (called by manager)
    """

    agent_type: str = "base"

    def __init__(self, context: "SubAgentContext") -> None:
        self.context = context
        self.agent_id = context.agent_id or uuid.uuid4().hex[:12]
        self.status = SubAgentStatus.PENDING
        self.state = SubAgentState.SPAWNED
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        self._logger = get_logger(f"sub_agent.{self.agent_type}.{self.agent_id}")

    def initialize(self) -> None:
        """Set up resources before execution. Override for custom setup."""
        self.state = SubAgentState.INITIALIZING
        self._logger.info(f"Initializing {self.agent_type} sub-agent {self.agent_id}")

    @abstractmethod
    def _run(self) -> str:
        """
        Execute the sub-agent's task.

        Returns:
            Output string summarizing the result.

        Raises:
            Exception: On failure (caught by manager, sets FAILED status).
        """
        ...

    def run(self) -> SubAgentResult:
        """
        Execute the full sub-agent lifecycle: initialize -> _run -> report.

        Called by SubAgentManager. Do not override — implement `_run` instead.
        """
        self._start_time = time.monotonic()
        self.status = SubAgentStatus.RUNNING
        self.state = SubAgentState.EXECUTING

        try:
            self.initialize()
            output = self._run()
            self.status = SubAgentStatus.COMPLETED
            result = self._build_result(output, success=True)
        except Exception as exc:
            self._logger.error(f"Sub-agent {self.agent_id} failed: {exc}")
            self.status = SubAgentStatus.FAILED
            result = self._build_result(str(exc), success=False, error=str(exc))
        finally:
            self._end_time = time.monotonic()
            self.state = SubAgentState.REPORTING
            self.cleanup()
            self.state = SubAgentState.TERMINATED
            result.duration_ms = (self._end_time - self._start_time) * 1000

        return result

    def _build_result(self, output: str, success: bool, error: Optional[str] = None) -> SubAgentResult:
        """Build the result dataclass from execution output."""
        return SubAgentResult(
            agent_id=self.agent_id,
            agent_type=self.agent_type,
            success=success,
            output=output,
            error=error,
            artifacts=self.context.artifacts,
            metadata={
                "state": self.state.value,
                "duration_ms": (self._end_time - self._start_time) * 1000
                if self._end_time else 0.0,
            },
        )

    def cleanup(self) -> None:
        """Release resources after execution. Override for custom teardown."""
        self._logger.info(f"Cleaning up {self.agent_type} sub-agent {self.agent_id}")

    def kill(self) -> None:
        """Request graceful termination of the sub-agent."""
        self.status = SubAgentStatus.KILLED
        self._logger.warning(f"Kill requested for sub-agent {self.agent_id}")
