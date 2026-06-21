"""
Sub-Agent Tool — allows the main agent to spawn and manage sub-agents.

This tool integrates the sub-agent system into the main agent's tool
registry, enabling dynamic spawning of specialized agents during execution.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .base import (
    ParallelResult,
    ParallelTask,
    Permission,
    ToolError,
    ToolErrorCode,
    ToolExecutor,
    ToolInput,
    ToolResult,
)
from ..sub_agents.context import SubAgentContext
from ..sub_agents.registry import get_global_registry
from ..sub_agents.manager import SubAgentManager
from ..utils.logger import get_logger

logger = get_logger("tools.sub_agent")


@dataclass
class SubAgentInput(ToolInput):
    """Input for the SubAgentTool."""
    action: str = "spawn"  # spawn, list, status, kill, list_types
    agent_type: str = ""
    task: str = ""
    agent_id: str = ""
    config: Dict[str, Any] = None
    max_iterations: int = 50
    timeout_seconds: int = 600


class SubAgentTool(ToolExecutor):
    """
    Tool for spawning and managing sub-agents.

    Actions:
    - spawn: Create and run a sub-agent (returns when complete)
    - list: List active sub-agents
    - status: Get status of a specific sub-agent
    - kill: Kill a specific sub-agent
    - list_types: List available sub-agent types
    """

    name = "sub_agent"
    description = (
        "Spawn and manage sub-agents for parallel task execution. "
        "Supports actions: spawn, list, status, kill, list_types."
    )
    required_permission = Permission.EXECUTE
    guideline = (
        "Use sub-agents for complex, parallelizable tasks. "
        "Spawn a researcher for investigation, "
        "reviewer for code review. Wait for results before proceeding."
    )

    def __init__(self, permissions=None, config: Optional[Dict[str, Any]] = None):
        super().__init__(permissions)
        self._config = config or {}
        self._manager: Optional[SubAgentManager] = None

    def _get_manager(self) -> SubAgentManager:
        """Get or create the sub-agent manager."""
        if self._manager is None:
            self._manager = SubAgentManager(
                config=self._config,
                max_workers=self._config.get("sub_agents", {}).get("max_parallel", 4),
            )
        return self._manager

    def _execute(self, input: SubAgentInput) -> ToolResult:
        """Execute the sub-agent action."""
        action = input.action

        try:
            if action == "spawn":
                return self._action_spawn(input)
            elif action == "list":
                return self._action_list()
            elif action == "status":
                return self._action_status(input)
            elif action == "kill":
                return self._action_kill(input)
            elif action == "list_types":
                return self._action_list_types()
            else:
                return ToolResult.fail(
                    error=ToolError(
                        code=ToolErrorCode.EXECUTION_ERROR,
                        message=f"Unknown sub-agent action: {action}. "
                                f"Valid: spawn, list, status, kill, list_types",
                    ),
                    tool_name=self.name,
                )
        except Exception as e:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Sub-agent tool error: {e}",
                ),
                tool_name=self.name,
            )

    def _action_spawn(self, input: SubAgentInput) -> ToolResult:
        """Spawn a sub-agent and wait for its result."""
        if not input.agent_type:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="agent_type is required for spawn action",
                ),
                tool_name=self.name,
            )
        if not input.task:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="task is required for spawn action",
                ),
                tool_name=self.name,
            )

        registry = get_global_registry()
        if not registry.is_registered(input.agent_type):
            available = ", ".join(registry.list_types())
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Unknown agent type: {input.agent_type}. "
                            f"Available: {available}",
                ),
                tool_name=self.name,
            )

        manager = self._get_manager()

        context = SubAgentContext(
            task=input.task,
            config=self._config,
            working_directory=os.getcwd(),
            max_iterations=input.max_iterations,
            timeout_seconds=input.timeout_seconds,
            parent_context={"spawned_by": "main_agent_tool"},
        )

        handle = manager.spawn(input.agent_type, context)

        # Wait for the result (blocking)
        results = manager.wait_all(timeout=input.timeout_seconds + 10)

        if not results:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="Sub-agent produced no results",
                ),
                tool_name=self.name,
            )

        result = results[0]
        output = json.dumps(result.to_dict(), indent=2) if not result.success else result.output

        return ToolResult.ok(
            output=output,
            tool_name=self.name,
            metadata={
                "agent_id": result.agent_id,
                "agent_type": result.agent_type,
                "success": result.success,
                "duration_ms": result.duration_ms,
            },
        )

    def _action_list(self) -> ToolResult:
        """List active sub-agents."""
        manager = self._get_manager()
        active = manager.list_active()
        if not active:
            return ToolResult.ok(
                output="No active sub-agents",
                tool_name=self.name,
            )
        return ToolResult.ok(
            output=json.dumps(active, indent=2),
            tool_name=self.name,
        )

    def _action_status(self, input: SubAgentInput) -> ToolResult:
        """Get status of a specific sub-agent."""
        if not input.agent_id:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="agent_id is required for status action",
                ),
                tool_name=self.name,
            )
        manager = self._get_manager()
        status = manager.get_status(input.agent_id)
        if status is None:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"No sub-agent found with id: {input.agent_id}",
                ),
                tool_name=self.name,
            )
        return ToolResult.ok(
            output=json.dumps(status, indent=2),
            tool_name=self.name,
        )

    def _action_kill(self, input: SubAgentInput) -> ToolResult:
        """Kill a specific sub-agent."""
        if not input.agent_id:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="agent_id is required for kill action",
                ),
                tool_name=self.name,
            )
        manager = self._get_manager()
        killed = manager.kill(input.agent_id)
        return ToolResult.ok(
            output=json.dumps({
                "agent_id": input.agent_id,
                "killed": killed,
            }),
            tool_name=self.name,
        )

    def _action_list_types(self) -> ToolResult:
        """List available sub-agent types."""
        registry = get_global_registry()
        types = registry.list_types()
        metadata = {}
        for t in types:
            meta = registry.get_metadata(t)
            if meta:
                metadata[t] = meta
        return ToolResult.ok(
            output=json.dumps({
                "types": types,
                "metadata": metadata,
            }, indent=2),
            tool_name=self.name,
        )
