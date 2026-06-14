"""
Built-in sub-agent implementations.

Each agent is registered with the global registry via the @sub_agent decorator.
"""

from .coder_agent import CoderAgent
from .research_agent import ResearchAgent
from .review_agent import ReviewAgent
from .architect_agent import ArchitectAgent

__all__ = ["CoderAgent", "ResearchAgent", "ReviewAgent", "ArchitectAgent"]