"""
Core Processing Layer for AI Agent System
Autonomous Loop Architecture.
"""

from .autonomous_loop_engine import AutonomousLoopEngine
from .context_manager import (
    load_context_state,
    context_files_exist,
    get_context_summary,
    get_context_for_prompt,
    display_context_in_terminal,
    clear_context_state,
)

__all__ = [
    "AutonomousLoopEngine",
    "load_context_state",
    "context_files_exist",
    "get_context_summary",
    "get_context_for_prompt",
    "display_context_in_terminal",
    "clear_context_state",
]
