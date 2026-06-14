"""
Sub-Agent Context — isolated context state per sub-agent.

Each sub-agent receives its own context object containing:
- Task-specific instructions and parameters
- Access to the shared configuration
- An artifacts dict for sharing structured results
- A reference to the model runner for LLM calls
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("sub_agent.context")


@dataclass
class SubAgentContext:
    """
    Isolated context state for a sub-agent execution.

    The context provides everything a sub-agent needs to execute its task
    without accessing the main agent's execution state directly.

    Attributes:
        task: The task description / instruction for the sub-agent.
        agent_id: Unique identifier (auto-generated if not provided).
        config: Shared application configuration dict.
        model_runner: Reference to the ModelRunner for LLM calls.
        working_directory: CWD for file operations (defaults to project root).
        parent_context: Optional metadata inherited from the spawning agent.
        artifacts: Mutable dict for sub-agent to store structured results.
        max_iterations: Maximum execution iterations before timeout.
        timeout_seconds: Wall-clock timeout for the sub-agent.
        extra: Arbitrary key-value pairs for agent-specific parameters.
    """

    task: str
    agent_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    config: Dict[str, Any] = field(default_factory=dict)
    model_runner: Any = None
    working_directory: str = field(default_factory=lambda: os.getcwd())
    parent_context: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 50
    timeout_seconds: int = 600
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.task:
            raise ValueError("SubAgentContext.task cannot be empty")

    @property
    def project_root(self) -> Path:
        """Derive project root from working directory (up to 4 levels)."""
        cwd = Path(self.working_directory)
        # Walk up to find the directory containing config.yaml
        for _ in range(4):
            if (cwd / "config.yaml").exists():
                return cwd
            if cwd.parent == cwd:
                break
            cwd = cwd.parent
        return Path(self.working_directory)

    def set_artifact(self, key: str, value: Any) -> None:
        """Store a structured result artifact."""
        self.artifacts[key] = value

    def get_artifact(self, key: str, default: Any = None) -> Any:
        """Retrieve a structured result artifact."""
        return self.artifacts.get(key, default)

    def append_artifact_list(self, key: str, item: Any) -> None:
        """Append an item to a list artifact (creates list if not exists)."""
        if key not in self.artifacts:
            self.artifacts[key] = []
        self.artifacts[key].append(item)

    def build_prompt_context(self) -> Dict[str, Any]:
        """Build a context dict for prompt template formatting."""
        return {
            "task": self.task,
            "agent_id": self.agent_id,
            "working_directory": self.working_directory,
            "max_iterations": self.max_iterations,
            "timeout_seconds": self.timeout_seconds,
            **self.parent_context,
            **self.extra,
        }

    def clone_with_task(self, new_task: str, **overrides: Any) -> SubAgentContext:
        """Create a copy of this context with a different task.

        Useful for spawning child sub-agents with modified instructions.
        """
        import copy
        data = {
            "task": new_task,
            "agent_id": overrides.get("agent_id", uuid.uuid4().hex[:12]),
            "config": self.config,
            "model_runner": self.model_runner,
            "working_directory": self.working_directory,
            "parent_context": {**self.parent_context, "parent_agent_id": self.agent_id},
            "artifacts": {},
            "max_iterations": overrides.get("max_iterations", self.max_iterations),
            "timeout_seconds": overrides.get("timeout_seconds", self.timeout_seconds),
            "extra": {**self.extra, **overrides.get("extra", {})},
        }
        data.update(overrides)
        ctx = SubAgentContext(**data)
        return ctx
