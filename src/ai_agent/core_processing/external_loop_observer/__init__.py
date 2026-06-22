"""
External Loop Observer Package for Clio-Agent-1 AI Agent System.

Provides an out-of-band loop detection and breaking system that operates
independently from the agent's own LLM, resolving the self-referential
paradox where an LLM cannot reliably detect its own loops.

Architecture:
  - ActionNormalizer:  Normalizes raw action tuples into comparable signatures
  - PatternAnalyzer:   Detects exact, cyclic, and semantic repetition patterns
  - ExternalObserver:  Orchestrates observation, intervention, and persistence
  - ObserverLLMClient: Separate LLM client for semantic loop analysis
"""

from .observer import ExternalObserver, ObserverConfig, ObserverVerdict
from .action_normalizer import ActionNormalizer, NormalizedAction
from .pattern_analyzer import PatternAnalyzer, PatternMatch, PatternType
from .intervention import Intervention, InterventionLevel

__all__ = [
    "ExternalObserver",
    "ObserverConfig",
    "ObserverVerdict",
    "ActionNormalizer",
    "NormalizedAction",
    "PatternAnalyzer",
    "PatternMatch",
    "PatternType",
    "Intervention",
    "InterventionLevel",
]
