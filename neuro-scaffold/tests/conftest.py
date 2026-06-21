"""Shared test fixtures."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from neuro_scaffold.config.settings import Settings
from neuro_scaffold.agent.models import AgentState, Scratchpad
from neuro_scaffold.agent.state_machine import AgentStateMachine, ToolRegistry


@pytest.fixture
def test_settings() -> Settings:
    """Create test settings with temporary paths."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Settings(
            scratchpad_path=Path(tmpdir) / "scratchpad.json",
            max_iterations=5,
            max_tool_calls_per_iteration=5,
            shell_timeout_seconds=10,
            gateway_port=9090,
        )


@pytest.fixture
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def agent_state() -> AgentState:
    return AgentState(
        max_iterations=5,
        max_tool_calls_per_iteration=5,
    )


@pytest.fixture
def scratchpad() -> Scratchpad:
    return Scratchpad(task="Test task")


@pytest.fixture
def sample_python_file(tmp_path: Path) -> Path:
    """Create a sample Python file for testing."""
    file_path = tmp_path / "sample.py"
    file_path.write_text(
        "def hello(name: str) -> str:\n"
        '    """Say hello."""\n'
        '    return f"Hello, {name}!"\n'
        "\n"
        "class Greeter:\n"
        '    """A greeter class."""\n'
        "    def __init__(self, greeting: str = 'Hello') -> None:\n"
        "        self.greeting = greeting\n"
        "\n"
        "    def greet(self, name: str) -> str:\n"
        '        return f"{self.greeting}, {name}!"\n',
        encoding="utf-8",
    )
    return file_path


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a sample multi-file project for testing."""
    (tmp_path / "main.py").write_text(
        "from utils import helper\n"
        "\n"
        "def main():\n"
        '    print(helper("world"))\n'
        "\n"
        'if __name__ == "__main__":\n'
        "    main()\n",
    )
    (tmp_path / "utils.py").write_text(
        "def helper(name: str) -> str:\n"
        '    return f"Hello, {name}!"\n',
    )
    (tmp_path / "config.json").write_text(
        '{"debug": true, "version": "1.0.0"}\n',
    )
    return tmp_path
