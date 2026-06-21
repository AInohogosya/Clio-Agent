"""Core agent state machine implementing the Plan -> Execute -> Observe -> Reflect loop."""

from __future__ import annotations

import hashlib
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


def _hash_arguments(arguments: dict[str, Any]) -> str:
    """Create a stable hash of tool call arguments for deduplication."""
    canonical = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


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
        """Create an initial plan from a task description.

        The plan is task-aware: it includes exploration, targeted reading,
        implementation, and verification steps — each with concrete tool calls.
        """
        steps = [
            PlanStep(
                step_id=0,
                description="Analyze the task and record it in working memory",
                tool_calls=[
                    ToolCall(tool=ToolName.SCRATCHPAD_WRITE, arguments={"content": task}),
                ],
            ),
            PlanStep(
                step_id=1,
                description="Explore the codebase structure via AST overview",
                tool_calls=[
                    ToolCall(tool=ToolName.AST_SEARCH, arguments={"query": "*"}),
                ],
            ),
            PlanStep(
                step_id=2,
                description="Read relevant source files identified from exploration",
                tool_calls=[
                    ToolCall(
                        tool=ToolName.CONTEXT_SEARCH,
                        arguments={"query": task},
                    ),
                ],
            ),
            PlanStep(
                step_id=3,
                description="Implement the required changes",
                tool_calls=[
                    ToolCall(
                        tool=ToolName.FILE_EDIT,
                        arguments={"description": "Apply the changes needed for: " + task},
                    ),
                ],
            ),
            PlanStep(
                step_id=4,
                description="Verify correctness with lint and tests",
                tool_calls=[
                    ToolCall(tool=ToolName.LINT_CHECK, arguments={}),
                    ToolCall(tool=ToolName.TEST_RUN, arguments={}),
                ],
            ),
        ]
        return steps

    def refine_plan(self, state: AgentState, observation: str) -> list[PlanStep]:
        """Refine the plan based on observations and reflections.

        Produces a new set of remaining steps that address the current
        situation — errors, stagnation, or incomplete work — instead of
        blindly repeating the same steps.
        """
        current = state.scratchpad.current_plan_step()
        if current is not None:
            current.result_summary = observation

        # Build a new plan based on what's happening
        refined: list[PlanStep] = []
        next_id = state.scratchpad.current_step + 1

        if state.error_count > 0:
            # Error recovery: lint check + targeted fix
            refined.append(
                PlanStep(
                    step_id=next_id,
                    description=f"Diagnose and fix errors from: {observation[:80]}",
                    tool_calls=[
                        ToolCall(tool=ToolName.LINT_CHECK, arguments={}),
                        ToolCall(
                            tool=ToolName.CONTEXT_SEARCH,
                            arguments={"query": observation[:100]},
                        ),
                    ],
                )
            )
            next_id += 1
            refined.append(
                PlanStep(
                    step_id=next_id,
                    description="Apply fix based on error diagnosis",
                    tool_calls=[
                        ToolCall(
                            tool=ToolName.FILE_EDIT,
                            arguments={"description": f"Fix: {observation[:100]}"},
                        ),
                    ],
                )
            )
            next_id += 1
        elif state.stagnation_count > 0:
            # Stagnation recovery: try a different approach
            # Check if we've been reading files without acting
            if state.scratchpad.files_read:
                last_file = state.scratchpad.files_read[-1].file_path
                last_line = state.scratchpad.get_last_read_line(last_file)
                if last_line > 0:
                    # Continue reading from where we left off instead of re-reading
                    refined.append(
                        PlanStep(
                            step_id=next_id,
                            description=f"Continue reading {last_file} from line {last_line + 1}",
                            tool_calls=[
                                ToolCall(
                                    tool=ToolName.FILE_READ,
                                    arguments={
                                        "file_path": last_file,
                                        "start_line": last_line + 1,
                                        "end_line": last_line + 200,
                                    },
                                ),
                            ],
                        )
                    )
                    next_id += 1

            # Add a step to act on what we've already read
            refined.append(
                PlanStep(
                    step_id=next_id,
                    description="Act on gathered information — implement or verify",
                    tool_calls=[
                        ToolCall(
                            tool=ToolName.FILE_EDIT,
                            arguments={
                                "description": f"Based on analysis: {state.scratchpad.task}"
                            },
                        ),
                    ],
                )
            )
            next_id += 1
        else:
            # Default: keep remaining steps but skip duplicates
            remaining = list(state.scratchpad.plan[state.scratchpad.current_step + 1:])
            for step in remaining:
                # Skip steps whose tool calls have all been executed
                all_done = all(
                    state.scratchpad.was_action_executed(
                        tc.tool.value, _hash_arguments(tc.arguments)
                    )
                    for tc in step.tool_calls
                )
                if not all_done:
                    step.step_id = next_id
                    refined.append(step)
                    next_id += 1

        # Always end with verification
        refined.append(
            PlanStep(
                step_id=next_id,
                description="Verify changes with lint and tests",
                tool_calls=[
                    ToolCall(tool=ToolName.LINT_CHECK, arguments={}),
                    ToolCall(tool=ToolName.TEST_RUN, arguments={}),
                ],
            )
        )

        return refined


class Executor:
    """Executes tool calls and returns results."""

    def __init__(self, settings: Settings, tool_registry: ToolRegistry) -> None:
        self._settings = settings
        self._tool_registry = tool_registry

    async def execute(self, call: ToolCall, state: AgentState) -> ToolResult:
        """Execute a single tool call with deduplication and file-read tracking."""
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

        # Deduplication: skip exact duplicate tool calls
        args_hash = _hash_arguments(call.arguments)
        if state.scratchpad.was_action_executed(call.tool.value, args_hash):
            logger.info(
                "Skipping duplicate tool call",
                tool=call.tool.value,
                iteration=state.iteration,
            )
            return ToolResult(
                call_id=call.call_id,
                tool=call.tool,
                success=True,
                output=f"[SKIPPED] Duplicate of already-executed {call.tool.value} call.",
                metadata={"skipped": True, "reason": "duplicate"},
            )

        state.record_tool_call()
        state.scratchpad.record_action(call.tool.value, args_hash, state.iteration)
        start = time.monotonic()
        try:
            timeout = call.timeout_seconds or self._settings.shell_timeout_seconds
            result = await handler(call.arguments, timeout=timeout)
            result.duration_ms = (time.monotonic() - start) * 1000

            # Track file reads for loop prevention
            self._track_file_read(call, result, state)

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

    def _track_file_read(self, call: ToolCall, result: ToolResult, state: AgentState) -> None:
        """Track file read operations to enable loop detection."""
        if call.tool == ToolName.FILE_READ and result.success:
            file_path = call.arguments.get("file_path", call.arguments.get("path", ""))
            if file_path:
                start_line = call.arguments.get("start_line", 1)
                end_line = call.arguments.get("end_line", 0)
                state.scratchpad.record_file_read(
                    file_path=str(file_path),
                    start_line=int(start_line) if start_line else 1,
                    end_line=int(end_line) if end_line else 0,
                    iteration=state.iteration,
                )
        elif call.tool == ToolName.CONTEXT_SEARCH and result.success:
            # Track context search results — extract file paths from metadata
            files = result.metadata.get("files", [])
            for f in files:
                state.scratchpad.record_file_read(
                    file_path=str(f),
                    iteration=state.iteration,
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
    """Reflects on observations to update the plan with real reasoning."""

    def reflect(self, state: AgentState, observation: str) -> str:
        """Produce a reflection and decide next action.

        Detects stagnation, duplicate file reads, and repeated failures
        to produce meaningful reflections that guide plan refinement.
        """
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

        # Check for stagnation
        if state.detect_stagnation():
            state.record_stagnation()
            reflection = (
                f"Stagnation detected (count: {state.stagnation_count}/"
                f"{state.max_stagnation_count}). "
            )
            if state.is_stagnation_limit_reached():
                reflection += "Stagnation limit reached. Halting to prevent infinite loop."
            else:
                reflection += "Plan needs refinement. Will attempt a different approach."
            state.scratchpad.add_reflection(reflection)
            return reflection

        # Check for repeated file reads (same file read multiple times)
        if self._is_repeated_file_read(state):
            state.record_stagnation()
            reflection = (
                "Detected repeated reads of the same file without progress. "
                "Need to take action on already-read content instead of re-reading."
            )
            state.scratchpad.add_reflection(reflection)
            return reflection

        # Check if the tool call was skipped as duplicate
        if "[SKIPPED]" in observation:
            state.record_stagnation()
            reflection = (
                "Tool call was skipped (duplicate). "
                "No new information gained this iteration. Need different approach."
            )
            state.scratchpad.add_reflection(reflection)
            return reflection

        current = state.scratchpad.current_plan_step()
        if current is not None:
            current.completed = True

        if "Failed" in observation:
            reflection = f"Encountered failure: {observation[:200]}. Will attempt recovery."
        elif "[TRUNCATED]" in observation:
            reflection = (
                "Output was truncated. Need to read more of the source "
                "before proceeding with changes."
            )
        elif current is not None:
            reflection = (
                f"Step '{current.description}' completed. "
                f"Proceeding to next step."
            )
            # Reset stagnation on meaningful progress
            state.reset_stagnation()
        else:
            reflection = "No more steps. Task may be complete."
            state.reset_stagnation()

        state.scratchpad.add_reflection(reflection)
        return reflection

    def _is_repeated_file_read(self, state: AgentState) -> bool:
        """Check if the same file has been read multiple times without other actions."""
        files_read = state.scratchpad.files_read
        if len(files_read) < 2:
            return False
        # Check if the last 2+ reads are the same file with no other actions between
        recent = files_read[-2:]
        if recent[0].file_path == recent[1].file_path:
            return True
        return False


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
            max_stagnation_count=settings.max_stagnation_count,
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
            max_stagnation_count=self._settings.max_stagnation_count,
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

        # Check stagnation limit before doing work
        if self._state.is_stagnation_limit_reached():
            self._state.transition_to(AgentPhase.FAILED)
            await self._scratchpad_mgr.save(self._state.scratchpad)
            logger.warning("Stagnation limit reached, halting", iteration=self._state.iteration)
            return self._state, True

        # PLAN / REFINE: check if we need to refine the plan
        self._state.transition_to(AgentPhase.PLANNING)
        if self._state.stagnation_count > 0 and len(self._state.scratchpad.observations) > 0:
            last_observation = self._state.scratchpad.observations[-1]
            refined = self._planner.refine_plan(self._state, last_observation)
            if refined:
                current_idx = self._state.scratchpad.current_step
                self._state.scratchpad.plan = (
                    self._state.scratchpad.plan[: current_idx + 1] + refined
                )
                for i, step in enumerate(self._state.scratchpad.plan):
                    step.step_id = i
                logger.info(
                    "Plan refined",
                    new_total_steps=len(self._state.scratchpad.plan),
                    stagnation_count=self._state.stagnation_count,
                )

        current_step = self._state.scratchpad.current_plan_step()
        if current_step is None:
            self._state.transition_to(AgentPhase.COMPLETED)
            return self._state, True

        # EXECUTE
        self._state.transition_to(AgentPhase.EXECUTING)
        any_success = False
        for tool_call in current_step.tool_calls:
            result = await self._executor.execute(tool_call, self._state)

            # OBSERVE
            self._state.transition_to(AgentPhase.OBSERVING)
            observation = self._observer.observe(result, self._state)

            if result.success and not result.metadata.get("skipped"):
                any_success = True

            # REFLECT
            self._state.transition_to(AgentPhase.REFLECTING)
            self._reflector.reflect(self._state, observation)

            if self._state.is_error_limit_reached():
                self._state.transition_to(AgentPhase.FAILED)
                await self._scratchpad_mgr.save(self._state.scratchpad)
                return self._state, True

            if self._state.is_stagnation_limit_reached():
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
