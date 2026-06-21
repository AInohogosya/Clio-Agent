"""
Delegate Sub-Agent Tool — allows the Main Agent to spawn sub-agents.

This tool integrates the SubAgentManager into the Main Agent's tool
system. The Main Agent can use it to spawn, list, or retrieve results
from sub-agents (Research, Review, Architect, etc.).

Tool commands:
  delegate(agent_type="architect", task="Design a caching layer")
    Spawns a sub-agent of the given type with the specified task.

  subagent_list()
    Lists all active sub-agents.

  subagent_result(agent_id="abc123")
    Gets the result of a completed sub-agent.

  subagent_kill(agent_id="abc123")
    Kills a specific sub-agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import (
    Permission,
    PermissionSet,
    ToolError,
    ToolErrorCode,
    ToolExecutor,
    ToolInput,
    ToolResult,
)
from ..utils.logger import get_logger

logger = get_logger("tools.delegate_sub_agent")


@dataclass
class DelegateSubAgentInput(ToolInput):
    """Input for spawning a sub-agent."""
    agent_type: str = ""
    task: str = ""
    timeout_seconds: int = 600
    max_iterations: int = 50
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SubAgentResultInput(ToolInput):
    """Input for retrieving a sub-agent result."""
    agent_id: str = ""


class DelegateSubAgentTool(ToolExecutor):
    """Tool that allows the Main Agent to delegate tasks to sub-agents.

    Integrates with SubAgentManager for lifecycle management.

    Usage in Main Agent prompts:
        subagent(agent_type="architect", task="Design a new caching layer")

    The tool spawns the sub-agent asynchronously and returns a handle ID
    that can be used to check status and retrieve results.
    """

    name = "subagent"
    description = "Delegate a task to a specialized sub-agent (research, review, architect)"
    required_permission = Permission.EXECUTE

    def __init__(self, permissions: Optional[PermissionSet] = None) -> None:
        super().__init__(permissions)
        self._manager = None
        self._config: Dict[str, Any] = {}

    def set_manager(self, manager, config: Optional[Dict[str, Any]] = None) -> None:
        """Inject the SubAgentManager instance.

        Must be called after tool creation but before first use.
        """
        self._manager = manager
        if config:
            self._config = config

    def _ensure_manager(self):
        """Lazily create a SubAgentManager if none is set."""
        if self._manager is not None:
            return
        try:
            from ..sub_agents.manager import SubAgentManager
            self._manager = SubAgentManager(config=self._config)
        except Exception as e:
            logger.warning(f"Could not create SubAgentManager: {e}")

    def _execute(self, input: ToolInput) -> ToolResult:
        """Execute the sub-agent delegation."""
        if isinstance(input, DelegateSubAgentInput):
            return self._delegate(input)
        elif isinstance(input, SubAgentResultInput):
            return self._get_result(input)
        else:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Unknown input type: {type(input).__name__}",
                ),
                tool_name=self.name,
            )

    def _delegate(self, input: DelegateSubAgentInput) -> ToolResult:
        """Spawn a sub-agent for the given task."""
        if not input.agent_type:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="agent_type is required for subagent delegation",
                ),
                tool_name=self.name,
            )
        if not input.task:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="task is required for subagent delegation",
                ),
                tool_name=self.name,
            )

        self._ensure_manager()
        if self._manager is None:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="SubAgentManager is not available",
                ),
                tool_name=self.name,
            )

        try:
            from ..sub_agents.context import SubAgentContext

            # Check if agent type is registered
            from ..sub_agents.registry import get_global_registry
            registry = get_global_registry()
            if not registry.is_registered(input.agent_type):
                available = ", ".join(registry.list_types())
                return ToolResult.fail(
                    error=ToolError(
                        code=ToolErrorCode.EXECUTION_ERROR,
                        message=(
                            f"Unknown sub-agent type: '{input.agent_type}'. "
                            f"Available: {available}"
                        ),
                    ),
                    tool_name=self.name,
                )

            # Create context
            ctx = SubAgentContext(
                task=input.task,
                timeout_seconds=input.timeout_seconds,
                max_iterations=input.max_iterations,
                extra=input.extra,
                config=self._config,
            )

            # Spawn the sub-agent
            handle = self._manager.spawn(input.agent_type, ctx)

            output = (
                f"Sub-agent spawned successfully.\n"
                f"  Agent ID:   {handle.agent_id}\n"
                f"  Agent Type: {handle.agent_type}\n"
                f"  Task:       {input.task[:200]}\n\n"
                f"Use subagent_result(agent_id=\"{handle.agent_id}\") "
                f"to check progress and retrieve results."
            )

            return ToolResult.ok(
                output=output,
                tool_name=self.name,
                metadata={
                    "agent_id": handle.agent_id,
                    "agent_type": handle.agent_type,
                },
            )

        except Exception as e:
            logger.error(f"Sub-agent delegation failed: {e}")
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Failed to spawn sub-agent: {e}",
                ),
                tool_name=self.name,
            )

    def _get_result(self, input: SubAgentResultInput) -> ToolResult:
        """Get the result of a sub-agent by ID."""
        if not input.agent_id:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="agent_id is required",
                ),
                tool_name=self.name,
            )

        self._ensure_manager()
        if self._manager is None:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="SubAgentManager is not available",
                ),
                tool_name=self.name,
            )

        try:
            status = self._manager.get_status(input.agent_id)
            if status is None:
                return ToolResult.fail(
                    error=ToolError(
                        code=ToolErrorCode.EXECUTION_ERROR,
                        message=f"No sub-agent found with ID: {input.agent_id}",
                    ),
                    tool_name=self.name,
                )

            sa_status = status.get("status", "unknown")

            if sa_status == "running":
                elapsed = status.get("elapsed_s", 0)
                return ToolResult.ok(
                    output=(
                        f"Sub-agent {input.agent_id} is still running "
                        f"({elapsed:.1f}s elapsed). Check again later."
                    ),
                    tool_name=self.name,
                    metadata=status,
                )

            # Try to collect results
            results = self._manager.collect_results()
            for result in results:
                if result.agent_id == input.agent_id:
                    status_icon = "✅" if result.success else "❌"
                    lines = [
                        f"{status_icon} Sub-agent {result.agent_id} "
                        f"({result.agent_type}) completed in "
                        f"{result.duration_ms:.0f}ms",
                        f"",
                        f"--- Output ---",
                        result.output[:4000] if result.output else "(no output)",
                    ]
                    if result.error:
                        lines.extend(["", f"--- Error ---", result.error[:1000]])
                    return ToolResult.ok(
                        output="\n".join(lines),
                        tool_name=self.name,
                        metadata={
                            "agent_id": result.agent_id,
                            "agent_type": result.agent_type,
                            "success": result.success,
                            "duration_ms": result.duration_ms,
                        },
                    )

            return ToolResult.ok(
                output=(
                    f"Sub-agent {input.agent_id} status: {sa_status}. "
                    f"Results not yet collected."
                ),
                tool_name=self.name,
                metadata=status,
            )

        except Exception as e:
            logger.error(f"Failed to get sub-agent result: {e}")
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Failed to get sub-agent result: {e}",
                ),
                tool_name=self.name,
            )


class ListSubAgentsTool(ToolExecutor):
    """Tool for listing all active sub-agents."""

    name = "subagent_list"
    description = "List all active sub-agents and their status"
    required_permission = Permission.READ

    def __init__(self, permissions: Optional[PermissionSet] = None) -> None:
        super().__init__(permissions)
        self._manager = None

    def set_manager(self, manager) -> None:
        self._manager = manager

    def _execute(self, input: ToolInput) -> ToolResult:
        if self._manager is None:
            return ToolResult.ok(
                output="No active sub-agents (manager not initialized).",
                tool_name=self.name,
            )

        try:
            active = self._manager.list_active()
            if not active:
                return ToolResult.ok(
                    output="No active sub-agents.",
                    tool_name=self.name,
                )

            lines = [f"Active sub-agents ({len(active)}):"]
            for sa in active:
                lines.append(
                    f"  - {sa['agent_id']} ({sa['agent_type']}) "
                    f"— {sa.get('elapsed_s', 0):.1f}s elapsed"
                )

            # Also show completed results
            completed = self._manager.collect_results()
            if completed:
                lines.append(f"\nCompleted sub-agents ({len(completed)}):")
                for r in completed:
                    icon = "✅" if r.success else "❌"
                    lines.append(
                        f"  {icon} {r.agent_id} ({r.agent_type}) "
                        f"— {r.duration_ms:.0f}ms"
                    )

            return ToolResult.ok(
                output="\n".join(lines),
                tool_name=self.name,
            )

        except Exception as e:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Failed to list sub-agents: {e}",
                ),
                tool_name=self.name,
            )


class KillSubAgentTool(ToolExecutor):
    """Tool for killing a specific sub-agent."""

    name = "subagent_kill"
    description = "Kill a specific sub-agent by ID"
    required_permission = Permission.EXECUTE

    def __init__(self, permissions: Optional[PermissionSet] = None) -> None:
        super().__init__(permissions)
        self._manager = None

    def set_manager(self, manager) -> None:
        self._manager = manager

    def _execute(self, input: ToolInput) -> ToolResult:
        if self._manager is None:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="SubAgentManager not available",
                ),
                tool_name=self.name,
            )

        if isinstance(input, SubAgentResultInput):
            agent_id = input.agent_id
        else:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Unknown input type: {type(input).__name__}",
                ),
                tool_name=self.name,
            )

        if not agent_id:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="agent_id is required",
                ),
                tool_name=self.name,
            )

        try:
            killed = self._manager.kill(agent_id)
            if killed:
                return ToolResult.ok(
                    output=f"Sub-agent {agent_id} killed.",
                    tool_name=self.name,
                )
            else:
                return ToolResult.ok(
                    output=f"Sub-agent {agent_id} not found or already completed.",
                    tool_name=self.name,
                )
        except Exception as e:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Failed to kill sub-agent: {e}",
                ),
                tool_name=self.name,
            )


# ════════════════════════════════════════════════════════════════
# Convenience functions for the Main Agent's loop engine
# ════════════════════════════════════════════════════════════════

def parse_subagent_command(command_str: str) -> Optional[ToolInput]:
    """Parse a subagent(...) command from the Main Agent's output.

    Expected format:
        subagent(agent_type="architect", task="Design a caching layer")
        subagent_result(agent_id="abc123")
        subagent_list()
        subagent_kill(agent_id="abc123")
    """
    import re

    # Match subagent(type="...", task="...")
    delegate_match = re.match(
        r'subagent\(\s*agent_type\s*=\s*"([^"]*)"\s*,\s*task\s*=\s*"([^"]*)"(?:\s*,\s*(.*?))?\s*\)',
        command_str,
    )
    if delegate_match:
        agent_type = delegate_match.group(1)
        task = delegate_match.group(2)
        extra_str = delegate_match.group(3)

        kwargs: Dict[str, Any] = {}
        if extra_str:
            for m in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', extra_str):
                kwargs[m.group(1)] = m.group(2)
            for m in re.finditer(r'(\w+)\s*=\s*(\d+)', extra_str):
                kwargs[m.group(1)] = int(m.group(2))

        return DelegateSubAgentInput(
            agent_type=agent_type,
            task=task,
            timeout_seconds=kwargs.get("timeout_seconds", 600),
            max_iterations=kwargs.get("max_iterations", 50),
            extra=kwargs.get("extra", {}),
        )

    # Match subagent_result(agent_id="...")
    result_match = re.match(
        r'subagent_result\(\s*agent_id\s*=\s*"([^"]*)"\s*\)',
        command_str,
    )
    if result_match:
        return SubAgentResultInput(agent_id=result_match.group(1))

    # Match subagent_list()
    if re.match(r'subagent_list\(\s*\)', command_str):
        return ToolInput()

    # Match subagent_kill(agent_id="...")
    kill_match = re.match(
        r'subagent_kill\(\s*agent_id\s*=\s*"([^"]*)"\s*\)',
        command_str,
    )
    if kill_match:
        return SubAgentResultInput(agent_id=kill_match.group(1))

    return None


def get_subagent_tools_config() -> Dict[str, Any]:
    """Get sub-agent tool configuration for the loop engine."""
    return {
        "commands": [
            {
                "pattern": r'subagent\(\s*agent_type\s*=\s*"',
                "description": "delegate task to specialized sub-agent",
                "examples": [
                    'subagent(agent_type="architect", task="Design a caching layer")',
                    'subagent(agent_type="research", task="Find all usages of deprecated API")',
                    'subagent(agent_type="review", task="Review security in authentication module")',
                ],
            },
            {
                "pattern": r'subagent_result\(\s*agent_id\s*=\s*"',
                "description": "get result from a completed sub-agent",
                "examples": [
                    'subagent_result(agent_id="abc123def456")',
                ],
            },
            {
                "pattern": r'subagent_list\(\s*\)',
                "description": "list all active sub-agents",
                "examples": ["subagent_list()"],
            },
            {
                "pattern": r'subagent_kill\(\s*agent_id\s*=\s*"',
                "description": "kill a specific sub-agent",
                "examples": ['subagent_kill(agent_id="abc123def456")'],
            },
        ],
        "instructions": """
## SUB-AGENT DELEGATION

You can delegate complex tasks to specialized sub-agents using the
`subagent(...)` command. Sub-agents run in parallel and report back
their results.

### Available Sub-Agent Types
- **research** — Investigates codebase, finds patterns, traces dependencies
- **review** — Performs code review, quality analysis, security audit
- **architect** — Produces architectural designs, ADRs, trade-off analysis

### Usage
1. Spawn: `subagent(agent_type="architect", task="Design a caching layer")`
2. Check: `subagent_result(agent_id="<id from spawn output>")`
3. List:  `subagent_list()`

Sub-agents produce structured reports that you can use to inform your
decisions. The architect agent is especially useful for complex design
questions — it performs a full 6-phase analysis (Discovery → Analysis
→ Design → Critique → Refinement → Synthesis) and produces professional
Architectural Decision Records.
""",
    }