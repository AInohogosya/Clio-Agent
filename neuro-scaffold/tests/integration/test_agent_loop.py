"""Integration tests for the full agent loop."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from neuro_scaffold.agent.models import AgentPhase, AgentState, ToolName, ToolResult
from neuro_scaffold.agent.state_machine import AgentStateMachine, ToolRegistry
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
