"""Integration tests for the full agent loop."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from neuro_scaffold.agent.models import AgentPhase, AgentState, ToolName, ToolResult
from neuro_scaffold.agent.state_machine import AgentStateMachine, ToolRegistry, _hash_arguments
from neuro_scaffold.config.settings import Settings


class TestAgentLoop:
    @pytest.fixture
    def settings(self) -> Settings:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Settings(
                scratchpad_path=Path(tmpdir) / "scratchpad.json",
                max_iterations=5,
                max_tool_calls_per_iteration=5,
                shell_timeout_seconds=10,
            )

    @pytest.mark.asyncio
    async def test_initialize_creates_plan(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        state = await agent.initialize("Write a hello world function")
        assert state.phase == AgentPhase.PLANNING
        assert len(state.scratchpad.plan) > 0
        assert state.scratchpad.task == "Write a hello world function"

    @pytest.mark.asyncio
    async def test_run_iteration_advances(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        await agent.initialize("Test task")
        state, done = await agent.run_iteration()
        assert state.iteration == 1
        assert state.scratchpad.current_step >= 0

    @pytest.mark.asyncio
    async def test_full_run_completes(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        final_state = await agent.run("Simple test task")
        assert final_state.phase in (AgentPhase.COMPLETED, AgentPhase.FAILED)
        assert final_state.iteration >= 1

    @pytest.mark.asyncio
    async def test_iteration_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                scratchpad_path=Path(tmpdir) / "scratchpad.json",
                max_iterations=2,
                max_tool_calls_per_iteration=1,
            )
            agent = AgentStateMachine(settings)
            final_state = await agent.run("Test with limited iterations")
            assert final_state.phase == AgentPhase.FAILED
            assert final_state.iteration == 2

    @pytest.mark.asyncio
    async def test_error_tracking(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        await agent.initialize("Test task")
        agent.state.record_error()
        assert agent.state.error_count == 1
        agent.state.reset_error_count()
        assert agent.state.error_count == 0

    @pytest.mark.asyncio
    async def test_error_limit_halts(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        await agent.initialize("Test task")
        agent.state.record_error()
        agent.state.record_error()
        agent.state.record_error()
        assert agent.state.is_error_limit_reached() is True

    @pytest.mark.asyncio
    async def test_tool_call_limit(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        await agent.initialize("Test task")
        for _ in range(settings.max_tool_calls_per_iteration):
            agent.state.record_tool_call()
        assert agent.state.is_tool_call_limit_reached() is True

    @pytest.mark.asyncio
    async def test_scratchpad_persistence(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        await agent.initialize("Test persistence")
        assert settings.scratchpad_path.exists()

    @pytest.mark.asyncio
    async def test_tool_registry(self, settings: Settings) -> None:
        registry = ToolRegistry()

        async def mock_handler(args: dict[str, Any], timeout: float) -> ToolResult:
            return ToolResult(
                call_id="test",
                tool=ToolName.SHELL_EXEC,
                success=True,
                output="mock output",
            )

        registry.register(ToolName.SHELL_EXEC, mock_handler)
        assert ToolName.SHELL_EXEC in registry.available_tools
        assert registry.get_handler(ToolName.SHELL_EXEC) is not None
        assert registry.get_handler(ToolName.FILE_READ) is None

    @pytest.mark.asyncio
    async def test_state_transitions(self, settings: Settings) -> None:
        agent = AgentStateMachine(settings)
        assert agent.state.phase == AgentPhase.IDLE
        await agent.initialize("Test")
        assert agent.state.phase == AgentPhase.PLANNING
        state, done = await agent.run_iteration()
        assert state.phase in (
            AgentPhase.PLANNING,
            AgentPhase.EXECUTING,
            AgentPhase.OBSERVING,
            AgentPhase.REFLECTING,
            AgentPhase.COMPLETED,
            AgentPhase.FAILED,
        )


class TestLoopPrevention:
    """Tests for the loop-prevention fixes."""

    @pytest.fixture
    def settings(self) -> Settings:
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Settings(
                scratchpad_path=Path(tmpdir) / "scratchpad.json",
                max_iterations=10,
                max_tool_calls_per_iteration=5,
                max_stagnation_count=3,
                shell_timeout_seconds=10,
            )

    @pytest.mark.asyncio
    async def test_plan_has_real_tool_calls_for_implement_step(self, settings: Settings) -> None:
        """Fix 1: The 'Implement' step should have non-empty tool_calls."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Add a new function")
        implement_step = agent.state.scratchpad.plan[3]
        assert implement_step.description == "Implement the required changes"
        assert len(implement_step.tool_calls) > 0, (
            "Implement step must have tool calls, not an empty list"
        )

    @pytest.mark.asyncio
    async def test_plan_has_context_search_step(self, settings: Settings) -> None:
        """Fix 1: Plan should include a context search step for reading files."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Refactor the helper function")
        step_descriptions = [s.description for s in agent.state.scratchpad.plan]
        has_read_step = any(
            "read" in d.lower() or "context" in d.lower() or "relevant" in d.lower()
            for d in step_descriptions
        )
        assert has_read_step, (
            f"Plan should include a step for reading relevant files. Got: {step_descriptions}"
        )

    @pytest.mark.asyncio
    async def test_initial_plan_has_5_steps_all_with_tool_calls(self, settings: Settings) -> None:
        """Fix 1: The new plan should have 5 meaningful steps, all with tool calls."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Do something useful")
        assert len(agent.state.scratchpad.plan) == 5
        for step in agent.state.scratchpad.plan:
            assert len(step.tool_calls) > 0, (
                f"Step '{step.description}' should have tool calls"
            )

    @pytest.mark.asyncio
    async def test_duplicate_tool_call_is_skipped(self, settings: Settings) -> None:
        """Fix 2: Duplicate tool calls should be skipped, not re-executed."""
        registry = ToolRegistry()
        call_count = 0

        async def counting_handler(args: dict[str, Any], timeout: float) -> ToolResult:
            nonlocal call_count
            call_count += 1
            return ToolResult(
                call_id="test",
                tool=ToolName.SHELL_EXEC,
                success=True,
                output=f"call #{call_count}",
            )

        registry.register(ToolName.SHELL_EXEC, counting_handler)
        agent = AgentStateMachine(settings, tool_registry=registry)
        await agent.initialize("Test dedup")

        args = {"command": "echo hello"}
        args_hash = _hash_arguments(args)
        agent.state.scratchpad.record_action(
            ToolName.SHELL_EXEC.value, args_hash, iteration=0
        )

        from neuro_scaffold.agent.models import ToolCall
        result = await agent._executor.execute(
            ToolCall(tool=ToolName.SHELL_EXEC, arguments=args),
            agent.state,
        )
        assert result.success is True
        assert result.metadata.get("skipped") is True
        assert call_count == 0, "Handler should not have been called for duplicate"

    @pytest.mark.asyncio
    async def test_stagnation_detection_via_repeated_observations(self, settings: Settings) -> None:
        """Fix 3: Agent should detect stagnation from repeated identical observations."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Test stagnation")

        for _ in range(6):
            agent.state.scratchpad.add_observation("[shell_exec] Success: same output")

        assert agent.state.detect_stagnation() is True

    @pytest.mark.asyncio
    async def test_stagnation_detection_via_repeated_file_reads(self, settings: Settings) -> None:
        """Fix 3: Agent should detect stagnation from reading the same file repeatedly."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Test file read stagnation")

        for i in range(4):
            agent.state.scratchpad.record_file_read(
                "src/main.py", 1, 100, iteration=i + 1
            )

        assert agent.state.detect_stagnation() is True

    @pytest.mark.asyncio
    async def test_stagnation_limit_halts_agent(self) -> None:
        """Fix 6: Agent should halt when stagnation limit is reached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                scratchpad_path=Path(tmpdir) / "scratchpad.json",
                max_iterations=50,
                max_stagnation_count=2,
                max_tool_calls_per_iteration=5,
            )
            agent = AgentStateMachine(settings)
            await agent.initialize("Test stagnation halt")

            agent.state.stagnation_count = 2

            state, done = await agent.run_iteration()
            assert done is True
            assert state.phase == AgentPhase.FAILED

    @pytest.mark.asyncio
    async def test_refine_plan_called_on_stagnation(self) -> None:
        """Fix 5: Plan refinement should be triggered when stagnation is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            s = Settings(
                scratchpad_path=Path(tmpdir) / "scratchpad.json",
                max_iterations=10, max_stagnation_count=5, max_tool_calls_per_iteration=5,
            )
            agent = AgentStateMachine(s)
            await agent.initialize("Test refinement")
            agent.state.stagnation_count = 1
            agent.state.scratchpad.add_observation("[shell_exec] Success: some output")
            state, done = await agent.run_iteration()
            assert len(agent.state.scratchpad.plan) > 0

    @pytest.mark.asyncio
    async def test_refine_plan_error_recovery_includes_fix(self, settings: Settings) -> None:
        """Fix 7: Error recovery should include both lint and fix steps."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Test error recovery")
        agent.state.record_error()
        agent.state.record_error()
        refined = agent._planner.refine_plan(agent.state, "SyntaxError in main.py")
        assert len(refined) >= 2
        assert "verify" in refined[-1].description.lower() or "lint" in refined[-1].description.lower()

    @pytest.mark.asyncio
    async def test_refine_plan_stagnation_continues_reading(self, settings: Settings) -> None:
        """Fix 4: When stagnated, refinement should continue from last read line."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Test truncation recovery")
        agent.state.scratchpad.record_file_read("src/main.py", 1, 100, iteration=1)
        agent.state.stagnation_count = 1
        refined = agent._planner.refine_plan(agent.state, "[file_read] Success: ... [TRUNCATED]")
        has_continue = any("continue reading" in s.description.lower() for s in refined)
        assert has_continue, f"Should continue reading. Got: {[s.description for s in refined]}"

    @pytest.mark.asyncio
    async def test_scratchpad_tracks_executed_actions(self, settings: Settings) -> None:
        """Fix 3: Scratchpad should track executed actions for deduplication."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Test action tracking")
        agent.state.scratchpad.record_action("shell_exec", "abc123", iteration=1)
        assert agent.state.scratchpad.was_action_executed("shell_exec", "abc123") is True
        assert agent.state.scratchpad.was_action_executed("shell_exec", "other") is False
        assert agent.state.scratchpad.was_action_executed("file_read", "abc123") is False

    @pytest.mark.asyncio
    async def test_scratchpad_tracks_file_reads(self, settings: Settings) -> None:
        """Fix 3: Scratchpad should track file reads with line ranges."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Test file tracking")
        agent.state.scratchpad.record_file_read("src/main.py", 1, 50, iteration=1)
        assert agent.state.scratchpad.was_file_read("src/main.py") is True
        assert agent.state.scratchpad.get_last_read_line("src/main.py") == 50
        agent.state.scratchpad.record_file_read("src/main.py", 51, 100, iteration=2)
        assert agent.state.scratchpad.get_last_read_line("src/main.py") == 100

    @pytest.mark.asyncio
    async def test_observations_stagnation_detection(self, settings: Settings) -> None:
        """Fix 3: observations_are_stagnant should detect repeating patterns."""
        agent = AgentStateMachine(settings)
        await agent.initialize("Test stagnation")
        for _ in range(3):
            agent.state.scratchpad.add_observation("output A")
        assert agent.state.scratchpad.observations_are_stagnant(window=3) is False
        for _ in range(3):
            agent.state.scratchpad.add_observation("output A")
        assert agent.state.scratchpad.observations_are_stagnant(window=3) is True

    @pytest.mark.asyncio
    async def test_reflector_detects_stagnation(self, settings: Settings) -> None:
        """Fix 2: Reflector should produce stagnation-specific reflection."""
        from neuro_scaffold.agent.state_machine import Reflector
        agent = AgentStateMachine(settings)
        await agent.initialize("Test reflector")
        for _ in range(6):
            agent.state.scratchpad.add_observation("[shell_exec] Success: same")
        reflector = Reflector()
        reflection = reflector.reflect(agent.state, "[shell_exec] Success: same")
        assert "Stagnation detected" in reflection
        assert agent.state.stagnation_count == 1

    @pytest.mark.asyncio
    async def test_reflector_detects_repeated_file_reads(self, settings: Settings) -> None:
        """Fix 2: Reflector should detect repeated file reads."""
        from neuro_scaffold.agent.state_machine import Reflector
        agent = AgentStateMachine(settings)
        await agent.initialize("Test reflector file reads")
        agent.state.scratchpad.record_file_read("main.py", 1, 50, iteration=1)
        agent.state.scratchpad.record_file_read("main.py", 1, 50, iteration=2)
        reflector = Reflector()
        reflection = reflector.reflect(agent.state, "[file_read] Success: content")
        assert "repeated reads" in reflection.lower() or "re-reading" in reflection.lower()

    @pytest.mark.asyncio
    async def test_reflector_resets_stagnation_on_progress(self, settings: Settings) -> None:
        """Fix 2: Reflector should reset stagnation on meaningful progress."""
        from neuro_scaffold.agent.state_machine import Reflector
        agent = AgentStateMachine(settings)
        await agent.initialize("Test progress reset")
        agent.state.stagnation_count = 2
        reflector = Reflector()
        reflector.reflect(agent.state, "[file_edit] Success: applied changes")
        assert agent.state.stagnation_count == 0

    @pytest.mark.asyncio
    async def test_reflector_detects_truncated_output(self, settings: Settings) -> None:
        """Fix 4: Reflector should produce specific reflection for truncated output."""
        from neuro_scaffold.agent.state_machine import Reflector
        agent = AgentStateMachine(settings)
        await agent.initialize("Test truncation")
        reflector = Reflector()
        reflection = reflector.reflect(
            agent.state, "[file_read] Success: partial content [TRUNCATED]"
        )
        assert "truncated" in reflection.lower()
