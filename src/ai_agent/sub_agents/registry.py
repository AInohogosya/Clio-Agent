"""
Sub-Agent Registry — decorator-based registration system.

Mirrors the plugin pattern: sub-agents are registered via a decorator
and discovered at runtime. The registry maps agent type names to their
classes and provides factory methods for instantiation.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Dict, List, Optional, Type

from .base import SubAgentBase
from ..utils.logger import get_logger

logger = get_logger("sub_agent.registry")


class SubAgentRegistry:
    """
    Central registry for all available sub-agent types.

    Usage:
        registry = SubAgentRegistry()

        @registry.register("coder")
        class CoderAgent(SubAgentBase):
            ...

        agent = registry.create("coder", context)
    """

    def __init__(self) -> None:
        self._agents: Dict[str, Type[SubAgentBase]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}

    def register(self, agent_type: str, *, description: str = "") -> callable:
        """
        Decorator to register a sub-agent class under a given type name.

        Args:
            agent_type: Unique string identifier (e.g. "coder", "research").
            description: Human-readable description of the agent.

        Usage:
            @registry.register("coder", description="Implements code tasks")
            class CoderAgent(SubAgentBase):
                agent_type = "coder"
                ...
        """
        def decorator(cls: Type[SubAgentBase]) -> Type[SubAgentBase]:
            if agent_type in self._agents:
                logger.warning(
                    f"Overwriting existing sub-agent registration: {agent_type}"
                )
            self._agents[agent_type] = cls
            self._metadata[agent_type] = {
                "description": description,
                "class_name": cls.__name__,
                "module": cls.__module__,
            }
            logger.info(f"Registered sub-agent: {agent_type} -> {cls.__name__}")
            return cls
        return decorator

    def create(self, agent_type: str, context: "SubAgentContext") -> SubAgentBase:
        """
        Factory method: instantiate a sub-agent of the given type.

        Args:
            agent_type: Registered type name.
            context: Context to pass to the sub-agent.

        Returns:
            An initialized (but not yet run) sub-agent instance.

        Raises:
            KeyError: If agent_type is not registered.
        """
        cls = self._agents.get(agent_type)
        if cls is None:
            available = ", ".join(sorted(self._agents.keys()))
            raise KeyError(
                f"Unknown sub-agent type: '{agent_type}'. "
                f"Available types: {available}"
            )
        return cls(context)

    def list_types(self) -> List[str]:
        """Return all registered agent type names."""
        return sorted(self._agents.keys())

    def get_metadata(self, agent_type: str) -> Optional[Dict[str, Any]]:
        """Return metadata for a registered agent type."""
        return self._metadata.get(agent_type)

    def get_class(self, agent_type: str) -> Optional[Type[SubAgentBase]]:
        """Return the class for a registered agent type."""
        return self._agents.get(agent_type)

    def is_registered(self, agent_type: str) -> bool:
        """Check if an agent type is registered."""
        return agent_type in self._agents

    def discover(self, package_name: str = "src.ai_agent.sub_agents.agents") -> int:
        """
        Auto-discover and register sub-agents from a package.

        Imports all modules in the package so that @register decorators
        execute. Returns the number of new agents discovered.
        """
        count_before = len(self._agents)
        try:
            package = importlib.import_module(package_name)
            for _importer, modname, _ispkg in pkgutil.iter_modules(package.__path__):
                full_name = f"{package_name}.{modname}"
                try:
                    importlib.import_module(full_name)
                    logger.debug(f"Discovered sub-agent module: {full_name}")
                except Exception as e:
                    logger.warning(f"Failed to import {full_name}: {e}")
        except ImportError as e:
            logger.warning(f"Could not import package {package_name}: {e}")
        count_after = len(self._agents)
        new_count = count_after - count_before
        if new_count > 0:
            logger.info(f"Discovered {new_count} new sub-agent(s) from {package_name}")
        return new_count


# Global registry singleton
_global_registry: Optional[SubAgentRegistry] = None


def get_global_registry() -> SubAgentRegistry:
    """Get or create the global sub-agent registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = SubAgentRegistry()
    return _global_registry


def sub_agent(agent_type: str, *, description: str = "") -> callable:
    """
    Convenience decorator that registers with the global registry.

    Usage:
        @sub_agent("coder", description="Implements code tasks")
        class CoderAgent(SubAgentBase):
            ...
    """
    return get_global_registry().register(agent_type, description=description)
