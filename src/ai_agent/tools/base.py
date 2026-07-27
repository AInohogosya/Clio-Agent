"""
Tool Base Classes

Base classes for tool system with permissions and execution.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from pathlib import Path

logger = logging.getLogger(__name__)


class Permission(Enum):
    """Tool permission levels."""
    READ = "read"           # Read-only operations
    WRITE = "write"         # Write/modify operations
    EXECUTE = "execute"     # Execute commands
    NETWORK = "network"     # Network access
    SYSTEM = "system"       # System-level operations
    ADMIN = "admin"         # Administrative operations


@dataclass
class ToolInput:
    """Base input for tools."""
    pass


@dataclass
class ToolResult:
    """Result of tool execution."""
    success: bool
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    @classmethod
    def ok(cls, output: Any = None, metadata: Dict[str, Any] = None) -> "ToolResult":
        return cls(success=True, output=output, metadata=metadata or {})
    
    @classmethod
    def fail(cls, error: str, metadata: Dict[str, Any] = None) -> "ToolResult":
        return cls(success=False, error=error, metadata=metadata or {})


class ToolError(Exception):
    """Tool execution error."""
    
    def __init__(self, code: str, message: str, details: Dict[str, Any] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"{code}: {message}")


class ToolErrorCode(Enum):
    """Tool error codes."""
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    INVALID_INPUT = "INVALID_INPUT"
    TIMEOUT = "TIMEOUT"
    RESOURCE_ERROR = "RESOURCE_ERROR"


@dataclass
class ParallelTask:
    """Task for parallel execution."""
    name: str
    tool: str
    input: ToolInput
    permission: Permission = Permission.READ


@dataclass
class ParallelResult:
    """Result of parallel execution."""
    task_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None


class ToolExecutor(ABC):
    """Abstract base class for tool executors."""
    
    name: str = ""
    description: str = ""
    required_permission: Permission = Permission.READ
    guideline: str = ""
    
    def __init__(self, permissions: Optional[Set[Permission]] = None):
        self._permissions = permissions or {Permission.READ}
    
    @property
    def permissions(self) -> Set[Permission]:
        return self._permissions
    
    def has_permission(self, permission: Permission) -> bool:
        return permission in self._permissions
    
    @abstractmethod
    async def execute(self, input: ToolInput) -> ToolResult:
        """Execute the tool."""
        pass
    
    def validate_input(self, input: ToolInput) -> Optional[str]:
        """Validate input. Returns error message or None."""
        return None


class ToolRegistry:
    """Registry for tools."""
    
    def __init__(self):
        self._tools: Dict[str, ToolExecutor] = {}
        self._categories: Dict[str, List[str]] = {}
    
    def register(self, tool: ToolExecutor, category: str = "general") -> None:
        """Register a tool."""
        if tool.name in self._tools:
            logger.warning(f"Overwriting tool: {tool.name}")
        
        self._tools[tool.name] = tool
        
        if category not in self._categories:
            self._categories[category] = []
        if tool.name not in self._categories[category]:
            self._categories[category].append(tool.name)
        
        logger.debug(f"Registered tool: {tool.name} in category: {category}")
    
    def unregister(self, name: str) -> bool:
        """Unregister a tool."""
        if name in self._tools:
            del self._tools[name]
            # Remove from categories
            for cat, tools in self._categories.items():
                if name in tools:
                    tools.remove(name)
            return True
        return False
    
    def get(self, name: str) -> Optional[ToolExecutor]:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def list_tools(self, category: str = None) -> List[str]:
        """List all tools or tools in a category."""
        if category:
            return self._categories.get(category, [])
        return list(self._tools.keys())
    
    def get_categories(self) -> List[str]:
        """Get all categories."""
        return list(self._categories.keys())
    
    def get_tool_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get tool information."""
        tool = self._tools.get(name)
        if not tool:
            return None
        
        return {
            "name": tool.name,
            "description": tool.description,
            "required_permission": tool.required_permission.value,
            "guideline": tool.guideline,
            "permissions": [p.value for p in tool.permissions]
        }
    
    def get_all_info(self) -> List[Dict[str, Any]]:
        """Get info for all tools."""
        return [self.get_tool_info(name) for name in self._tools]


# Global default registry
_default_registry: Optional[ToolRegistry] = None


def get_default_registry() -> ToolRegistry:
    """Get or create default registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry


def register_tool(tool: ToolExecutor, category: str = "general") -> None:
    """Register tool in default registry."""
    get_default_registry().register(tool, category)