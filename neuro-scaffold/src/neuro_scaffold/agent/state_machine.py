"""Core agent state machine implementing the Plan -> Execute -> Observe -> Reflect loop."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

import aiofiles
import structlog

from neuro_scaffold.agent.models import (
    AgentPhase,
    AgentState,
    PlanStep,
    Scratchpad,
    ToolCall,
    ToolName,
    ToolResult,
)
from neuro_scaffold.config.settings import Settings

logger = structlog.get_logger(__name__)

ToolHandler = Callable[[dict[str, Any], float], Awaitable[ToolResult]]


class ScratchpadManager:
    """Manages persistent scratchpad storage."""

    def __init__(self, scratchpad_path: Path) -> None:
        self._path = scratchpad_path

    async def load(self) -> Scratchpad | None:
        if not self._path.exists():
            return None
        try:
            async with aiofiles.open(self._path, "r") as f:
                raw = await f.read()
            data = json.loads(raw)
            return Scratchpad(**data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load scratchpad", path=str(self._path), error=str(exc))
            return None

    async def save(self, scratchpad: Scratchpad) -> None:
        scratchpad.updated_at = datetime.now(timezone.utc)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._path, "w") as f:
                await f.write(scratchpad.model_dump_json(indent=2))
        except OSError as exc:
            logger.error("Failed to save scratchpad", path=str(self._path), error=str(exc))

    async def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


class Planner:
    """Generates and manages the agent's plan."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create_plan(self, task: str) -> list[PlanStep]:
        """Create an initial plan from a task description."""
        steps = [
            PlanStep(
                step_id=0,
                description="Analyze the task and understand requirements",
                tool_calls=[
                    ToolCall(tool=ToolName.SCRATCHPAD_WRITE, arguments={"content": task}),
                ],
            ),
            PlanStep(
                step_id=1,
                description="Explore the codebase structure",
                tool_calls=[
                    ToolCall(tool=ToolName.AST_SEARCH, arguments={"query": "*"}),
                ],
            ),
            PlanStep(
                step_id=2,
                description="Implement the required changes",
                tool_calls=[],
            ),
            PlanStep(
                step_id=3,
                description="Run tests and verify correctness",
                tool_calls=[
                    ToolCall(tool=ToolName.LINT_CHECK, arguments={}),
                    ToolCall(tool=ToolName.TEST_RUN, arguments={}),
                ],
            ),
        ]
        return steps

    def refine_plan(self, state: AgentState, observation: str) -> list[PlanStep]:
        """Refine the plan based on observations and reflections."""
        current = state.scratchpad.current_plan_step()
        if current is not None:
            current.result_summary = observation

        remaining_steps = list(state.scratchpad.plan[state.scratchpad.current_step + 1:])

        if state.error_count > 0:
            recovery_step = PlanStep(
                step_id=len(state.scratchpad.plan),
                description=f"Fix errors: {observation[:100]}",
                tool_calls=[
                    ToolCall(tool=ToolName.LINT_CHECK, arguments={}),
                ],
            )
            remaining_steps.insert(0, recovery_step)

        return remaining_steps


class Executor:
    """Executes tool calls and returns results."""

    def __init__(self, settings: Settings, tool_registry: ToolRegistry) -> None:
        self._settings = settings
        self._tool_registry = tool_registry

    async def execute(self, call: ToolCall, state: AgentState) -> ToolResult:
        """Execute a single tool call."""
        if state.is_tool_call_limit_reached():
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                success=False,
                error="Tool call limit for this iteration reached",
            )

        handler = self._tool_registry.get_handler(call.tool)
        if handler is None:
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                success=False,
                error=f"Unknown tool: {call.tool}",
            )

        if call.dry_run:
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                success=True,
                output=f"[DRY RUN] Would execute {call.tool} with {call.arguments}",
            )

        state.record_tool_call()
        start = time.monotonic()
        try:
            timeout = call.timeout_seconds or self._settings.shell_timeout_seconds
            result = await handler(call.arguments, timeout=timeout)
            result.duration_ms = (time.monotonic() - start) * 1000
            return result
        except TimeoutError:
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                success=False,
                error=f"Tool call timed out after {call.timeout_seconds or self._settings.shell_timeout_seconds}s",
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            logger.exception("Tool execution failed", tool=call.tool, call_id=call.call_id)
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                success=False,
                error=str(exc),
                duration_ms=(time.monotonic() - start) * 1000,
            )


class Observer:
    """Observes and interprets tool results."""

    def observe(self, result: ToolResult, state: AgentState) -> str:
        """Produce an observation string from a tool result."""
        if result.success:
            observation = f"[{result.tool}] Success: {result.output[:500]}"
            if result.truncated:
                observation += " [TRUNCATED]"
        else:
            observation = f"[{result.tool}] Failed: {result.error}"

        state.scratchpad.add_observation(observation)
        state.last_tool_result = result

        if not result.success:
            state.record_error()
        else:
            state.reset_error_count()

        return observation


class Reflector:
    """Reflects on observations to update the plan."""

    def reflect(self, state: AgentState, observation: str) -> str:
        """Produce a reflection and decide next action."""
        if state.is_error_limit_reached():
            reflection = (
                f"Error limit reached ({state.error_count}/{state.max_consecutive_errors}). "
                "Halting and requesting human intervention."
            )
            state.scratchpad.add_reflection(reflection)
            return reflection

        if state.is_iteration_limit_reached():
            reflection = (
                f"Iteration limit reached ({state.iteration}/{state.max_iterations}). "
                "Wrapping up."
            )
            state.scratchpad.add_reflection(reflection)
            return reflection

        current = state.scratchpad.current_plan_step()
        if current is not None:
            current.completed = True

        if "Failed" in observation:
            reflection = f"Encountered failure: {observation[:200]}. Will attempt recovery."
        elif current is not None:
            reflection = (
                f"Step '{current.description}' completed. "
                f"Proceeding to next step."
            )
        else:
            reflection = "No more steps. Task may be complete."

        state.scratchpad.add_reflection(reflection)
        return reflection


class ToolRegistry:
    """Registry of all available tool handlers."""

    def __init__(self) -> None:
        self._handlers: dict[ToolName, ToolHandler] = {}

    def register(self, tool: ToolName, handler: ToolHandler) -> None:
        self._handlers[tool] = handler

    def get_handler(self, tool: ToolName) -> ToolHandler | None:
        return self._handlers.get(tool)

    @property
    def available_tools(self) -> list[ToolName]:
        return list(self._handlers.keys())


class AgentStateMachine:
    """Main agent state machine orchestrating the Plan -> Execute -> Observe -> Reflect loop."""

    def __init__(
        self,
        settings: Settings,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._scratchpad_mgr = ScratchpadManager(settings.scratchpad_path)
        self._planner = Planner(settings)
        self._registry = tool_registry or ToolRegistry()
        self._executor = Executor(settings, self._registry)
        self._observer = Observer()
        self._reflector = Reflector()
        self._state = AgentState(
            max_iterations=settings.max_iterations,
            max_tool_calls_per_iteration=settings.max_tool_calls_per_iteration,
        )

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._registry

    async def initialize(self, task: str) -> AgentState:
        """Initialize the agent with a task."""
        self._state = AgentState(
            max_iterations=self._settings.max_iterations,
            max_tool_calls_per_iteration=self._settings.max_tool_calls_per_iteration,
        )
        self._state.scratchpad = Scratchpad(task=task)
        self._state.scratchpad.plan = self._planner.create_plan(task)
        self._state.transition_to(AgentPhase.PLANNING)
        await self._scratchpad_mgr.save(self._state.scratchpad)
        logger.info("Agent initialized", task=task, steps=len(self._state.scratchpad.plan))
        return self._state

    async def run_iteration(self) -> tuple[AgentState, bool]:
        """Run a single iteration of the agent loop.

        Returns the updated state and a boolean indicating whether the agent is done.
        """
        self._state.start_new_iteration()
        logger.info(
            "Starting iteration",
            iteration=self._state.iteration,
            phase=self._state.phase.value,
        )

        # PLAN
        self._state.transition_to(AgentPhase.PLANNING)
        current_step = self._state.scratchpad.current_plan_step()
        if current_step is None:
            self._state.transition_to(AgentPhase.COMPLETED)
            return self._state, True

        # EXECUTE
        self._state.transition_to(AgentPhase.EXECUTING)
        for tool_call in current_step.tool_calls:
            result = await self._executor.execute(tool_call, self._state)

            # OBSERVE
            self._state.transition_to(AgentPhase.OBSERVING)
            observation = self._observer.observe(result, self._state)

            # REFLECT
            self._state.transition_to(AgentPhase.REFLECTING)
            self._reflector.reflect(self._state, observation)

            if self._state.is_error_limit_reached():
                self._state.transition_to(AgentPhase.FAILED)
                await self._scratchpad_mgr.save(self._state.scratchpad)
                return self._state, True

        # Mark step complete and advance
        current_step.completed = True
        self._state.scratchpad.advance()

        if self._state.scratchpad.is_complete():
            self._state.transition_to(AgentPhase.COMPLETED)
            await self._scratchpad_mgr.save(self._state.scratchpad)
            return self._state, True

        if self._state.is_iteration_limit_reached():
            self._state.transition_to(AgentPhase.FAILED)
            await self._scratchpad_mgr.save(self._state.scratchpad)
            return self._state, True

        await self._scratchpad_mgr.save(self._state.scratchpad)
        return self._state, False

    async def run(self, task: str) -> AgentState:
        """Run the full agent loop until completion."""
        await self.initialize(task)
        done = False
        while not done:
            self._state, done = await self.run_iteration()
        logger.info(
            "Agent loop finished",
            phase=self._state.phase.value,
            iterations=self._state.iteration,
        )
        return self._state
