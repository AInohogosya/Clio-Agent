"""
Built-in sub-agent implementations.

Each agent is registered with the global registry via the @sub_agent decorator.
"""

from .research_agent import ResearchAgent
from .review_agent import ReviewAgent
from .architect_agent import ArchitectAgent
from .coding_agent import CodingAgent

__all__ = ["ResearchAgent", "ReviewAgent", "ArchitectAgent", "CodingAgent"]