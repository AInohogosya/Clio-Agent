"""
Sub-Agent System for Clio Agent 1

Provides a managed, parallel execution system for specialized sub-agents:
- CodingAgent: ALL coding tasks - writing, editing, debugging, refactoring, testing (PRIMARY coding entity)
- ResearchAgent: Investigation and analysis tasks
- ReviewAgent: Code review and quality analysis tasks
- ArchitectAgent: Architectural design, ADR generation, and trade-off analysis

Sub-agents run in isolated contexts with their own state, report results
back to the main agent, and can be spawned/killed dynamically.
"""

from .base import (
    SubAgentBase,
    SubAgentResult,
    SubAgentState,
    SubAgentStatus,
)
from .context import SubAgentContext
from .manager import SubAgentManager
from .registry import SubAgentRegistry, get_global_registry, sub_agent

__all__ = [
    # Base
    "SubAgentBase",
    "SubAgentResult",
    "SubAgentState",
    "SubAgentStatus",
    # Context
    "SubAgentContext",
    # Manager
    "SubAgentManager",
    # Registry
    "SubAgentRegistry",
    "get_global_registry",
    "sub_agent",
]