"""
Tool Calling Framework

Provides a unified system for AI to call tools via JSON commands.
Supports both chat and voice conversation modes.
"""

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union
from functools import wraps
import inspect

# Import from base for shared types
from .base import Permission, ToolExecutor, ToolInput, ToolResult, ToolError, ToolErrorCode, ToolRegistry

logger = logging.getLogger(__name__)


class ToolPermission(Enum):
    """Tool permission levels."""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    ADMIN = "admin"


# Alias for compatibility
Permission = ToolPermission


@dataclass
class ToolParameter:
    """Tool parameter definition."""
    name: str
    type: str  # string, number, boolean, array, object
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[Any]] = None


@dataclass
class ToolDefinition:
    """Tool definition for LLM function calling."""
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)
    permission: ToolPermission = ToolPermission.READ
    returns: Optional[str] = None
    examples: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_openai_function(self) -> Dict[str, Any]:
        """Convert to OpenAI function format."""
        properties = {}
        required = []
        
        for param in self.parameters:
            prop = {
                "type": param.type,
                "description": param.description
            }
            if param.enum:
                prop["enum"] = param.enum
            if param.default is not None:
                prop["default"] = param.default
            
            properties[param.name] = prop
            
            if param.required:
                required.append(param.name)
        
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
    
    def to_anthropic_tool(self) -> Dict[str, Any]:
        """Convert to Anthropic tool format."""
        return self.to_openai_function()


@dataclass
class ToolCall:
    """A tool call from the LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]
    
    @classmethod
    def from_openai(cls, call) -> "ToolCall":
        """Create from OpenAI tool call."""
        return cls(
            id=call.id,
            name=call.function.name,
            arguments=json.loads(call.function.arguments)
        )
    
    @classmethod
    def from_anthropic(cls, call) -> "ToolCall":
        """Create from Anthropic tool call."""
        return cls(
            id=call.id,
            name=call.name,
            arguments=call.input
        )


@dataclass
class ToolResult:
    """Result of a tool execution."""
    call_id: str
    name: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """Base class for all tools."""
    
    def __init__(self):
        self._definition = self._create_definition()
    
    @abstractmethod
    def _create_definition(self) -> ToolDefinition:
        """Create tool definition."""
        pass
    
    @property
    def definition(self) -> ToolDefinition:
        return self._definition
    
    @property
    def name(self) -> str:
        return self._definition.name
    
    @abstractmethod
    async def execute(self, arguments: Dict[str, Any]) -> Any:
        """Execute the tool."""
        pass
    
    def validate_arguments(self, arguments: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate arguments against definition."""
        for param in self._definition.parameters:
            if param.required and param.name not in arguments:
                return False, f"Missing required parameter: {param.name}"
            
            if param.name in arguments:
                value = arguments[param.name]
                if not self._validate_type(value, param.type):
                    return False, f"Parameter {param.name} must be {param.type}"
                
                if param.enum and value not in param.enum:
                    return False, f"Parameter {param.name} must be one of {param.enum}"
        
        return True, None
    
    def _validate_type(self, value: Any, expected_type: str) -> bool:
        """Validate value type."""
        type_map = {
            "string": str,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict
        }
        
        expected = type_map.get(expected_type)
        if expected:
            return isinstance(value, expected)
        return True


class ToolRegistry:
    """Registry for managing tools."""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._categories: Dict[str, List[str]] = {}
    
    def register(self, tool: BaseTool, category: str = "general") -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        if category not in self._categories:
            self._categories[category] = []
        self._categories[category].append(tool.name)
        logger.debug(f"Registered tool: {tool.name} (category: {category})")
    
    def unregister(self, name: str) -> bool:
        """Unregister a tool."""
        if name in self._tools:
            tool = self._tools.pop(name)
            for cat, tools in self._categories.items():
                if name in tools:
                    tools.remove(name)
            return True
        return False
    
    def get(self, name: str) -> Optional[BaseTool]:
        """Get tool by name."""
        return self._tools.get(name)
    
    def list_tools(self, category: str = None) -> List[BaseTool]:
        """List all tools, optionally filtered by category."""
        if category:
            names = self._categories.get(category, [])
            return [self._tools[n] for n in names if n in self._tools]
        return list(self._tools.values())
    
    def get_definitions(self, category: str = None) -> List[ToolDefinition]:
        """Get tool definitions for LLM."""
        tools = self.list_tools(category)
        return [t.definition for t in tools]
    
    def get_openai_functions(self, category: str = None) -> List[Dict[str, Any]]:
        """Get OpenAI function format."""
        return [t.to_openai_function() for t in self.list_tools(category)]
    
    def get_anthropic_tools(self, category: str = None) -> List[Dict[str, Any]]:
        """Get Anthropic tool format."""
        return [t.to_anthropic_tool() for t in self.list_tools(category)]


class ToolExecutor:
    """Executes tool calls."""
    
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self._permission_callback: Optional[Callable[[str, ToolPermission], bool]] = None
    
    def set_permission_check(self, callback: Callable[[str, ToolPermission], bool]):
        """Set permission check callback."""
        self._permission_callback = callback
    
    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute a tool call."""
        tool = self.registry.get(call.name)
        
        if not tool:
            return ToolResult(
                call_id=call.id,
                name=call.name,
                success=False,
                error=f"Tool not found: {call.name}"
            )
        
        # Validate arguments
        valid, error = tool.validate_arguments(call.arguments)
        if not valid:
            return ToolResult(
                call_id=call.id,
                name=call.name,
                success=False,
                error=error
            )
        
        # Check permissions
        if self._permission_callback:
            allowed = self._permission_callback(call.name, tool.definition.permission)
            if not allowed:
                return ToolResult(
                    call_id=call.id,
                    name=call.name,
                    success=False,
                    error=f"Permission denied for tool: {call.name}"
                )
        
        # Execute
        try:
            result = await tool.execute(call.arguments)
            return ToolResult(
                call_id=call.id,
                name=call.name,
                success=True,
                result=result
            )
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return ToolResult(
                call_id=call.id,
                name=call.name,
                success=False,
                error=str(e)
            )
    
    async def execute_batch(self, calls: List[ToolCall]) -> List[ToolResult]:
        """Execute multiple tool calls in parallel."""
        tasks = [self.execute(call) for call in calls]
        return await asyncio.gather(*tasks)


class JSONCommandParser:
    """Parses JSON commands from LLM output."""
    
    # Pattern to match JSON commands at the start of response
    COMMAND_PATTERN = re.compile(
        r'^\s*(\{.*?\})\s*(.*)$',
        re.DOTALL
    )
    
    # Pattern for tool call format
    TOOL_CALL_PATTERN = re.compile(
        r'\{\s*"tool_call"\s*:\s*\{[^}]+\}\s*\}',
        re.DOTALL
    )
    
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
    
    def parse(self, text: str) -> tuple[Optional[List[ToolCall]], str]:
        """
        Parse text for tool calls.
        Returns (tool_calls, remaining_text).
        """
        text = text.strip()
        
        # Try to find JSON at the beginning
        match = self.COMMAND_PATTERN.match(text)
        if not match:
            return None, text
        
        json_str = match.group(1)
        remaining = match.group(2).strip()
        
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return None, text
        
        # Check for tool_call format
        if "tool_call" in data:
            tool_data = data["tool_call"]
            call = ToolCall(
                id=tool_data.get("id", "call_1"),
                name=tool_data.get("name", ""),
                arguments=tool_data.get("arguments", {})
            )
            return [call], remaining
        
        # Check for direct function call format
        if "name" in data and "arguments" in data:
            call = ToolCall(
                id=data.get("id", "call_1"),
                name=data["name"],
                arguments=data["arguments"]
            )
            return [call], remaining
        
        # Check for multiple tool calls
        if "tool_calls" in data:
            calls = []
            for tc in data["tool_calls"]:
                call = ToolCall(
                    id=tc.get("id", "call_1"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", {})
                )
                calls.append(call)
            return calls, remaining
        
        return None, text
    
    def extract_all_commands(self, text: str) -> List[ToolCall]:
        """Extract all tool calls from text."""
        calls = []
        
        # Find all JSON-like structures
        for match in self.TOOL_CALL_PATTERN.finditer(text):
            try:
                data = json.loads(match.group())
                if "tool_call" in data:
                    tc = data["tool_call"]
                    calls.append(ToolCall(
                        id=tc.get("id", f"call_{len(calls)}"),
                        name=tc.get("name", ""),
                        arguments=tc.get("arguments", {})
                    ))
            except json.JSONDecodeError:
                continue
        
        return calls


def tool(
    name: str = None,
    description: str = "",
    parameters: List[ToolParameter] = None,
    permission: ToolPermission = ToolPermission.READ,
    returns: str = None
):
    """Decorator to create a tool from a function."""
    
    def decorator(func: Callable) -> BaseTool:
        # Get function signature
        sig = inspect.signature(func)
        
        # Build parameters
        tool_params = []
        if parameters:
            tool_params = parameters
        else:
            for param_name, param in sig.parameters.items():
                if param_name in ('self', 'cls'):
                    continue
                
                # Determine type
                param_type = "string"
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation == int:
                        param_type = "number"
                    elif param.annotation == float:
                        param_type = "number"
                    elif param.annotation == bool:
                        param_type = "boolean"
                    elif param.annotation == list:
                        param_type = "array"
                    elif param.annotation == dict:
                        param_type = "object"
                
                required = param.default == inspect.Parameter.empty
                default = param.default if not required else None
                
                tool_params.append(ToolParameter(
                    name=param_name,
                    type=param_type,
                    description=f"Parameter {param_name}",
                    required=required,
                    default=default
                ))
        
        tool_name = name or func.__name__
        tool_desc = description or func.__doc__ or f"Tool: {tool_name}"
        
        class FunctionTool(BaseTool):
            def _create_definition(self) -> ToolDefinition:
                return ToolDefinition(
                    name=tool_name,
                    description=tool_desc,
                    parameters=tool_params,
                    permission=permission,
                    returns=returns
                )
            
            async def execute(self, arguments: Dict[str, Any]) -> Any:
                return await func(**arguments)
        
        return FunctionTool()
    
    return decorator


# Built-in tools

@tool(
    name="end_voice_conversation",
    description="End the current voice conversation and return to text chat",
    permission=ToolPermission.EXECUTE
)
async def end_voice_conversation() -> str:
    """End voice conversation."""
    return "Voice conversation ended. Returning to text chat."


@tool(
    name="spawn_agent",
    description="Spawn a background AI agent for long-running tasks",
    parameters=[
        ToolParameter("agent_type", "string", "Type of agent: coding, research, review, architect", True, enum=["coding", "research", "review", "architect"]),
        ToolParameter("task", "string", "Task description for the agent", True),
        ToolParameter("timeout", "number", "Timeout in seconds", False, 300)
    ],
    permission=ToolPermission.EXECUTE
)
async def spawn_agent(agent_type: str, task: str, timeout: int = 300) -> Dict[str, Any]:
    """Spawn a background agent."""
    from ..agents import create_agent_manager
    
    manager = create_agent_manager()
    await manager.start()
    
    task_obj = AgentTask(
        name=f"{agent_type}_{task[:30]}",
        description=task,
        agent_type=agent_type
    )
    
    result = await manager.spawn_and_wait(agent_type, task_obj, timeout=timeout)
    await manager.stop()
    
    return {
        "task_id": result.id,
        "status": result.status.value,
        "result": result.result,
        "error": result.error
    }


@tool(
    name="get_agent_status",
    description="Get status of a running agent task",
    parameters=[
        ToolParameter("task_id", "string", "Task ID to check", True)
    ],
    permission=ToolPermission.READ
)
async def get_agent_status(task_id: str) -> Dict[str, Any]:
    """Get agent task status."""
    # This would integrate with the agent manager
    return {
        "task_id": task_id,
        "status": "completed",
        "progress": 1.0
    }


@tool(
    name="take_photo",
    description="Take a photo with the camera",
    permission=ToolPermission.EXECUTE
)
async def take_photo() -> Dict[str, Any]:
    """Take a photo with camera."""
    # This would integrate with the camera module
    return {
        "success": True,
        "image_base64": "...",
        "timestamp": "2024-01-01T00:00:00Z"
    }


@tool(
    name="read_file",
    description="Read a file from the filesystem",
    parameters=[
        ToolParameter("path", "string", "File path to read", True)
    ],
    permission=ToolPermission.READ
)
async def read_file(path: str) -> str:
    """Read file contents."""
    try:
        with open(path, 'r') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


@tool(
    name="write_file",
    description="Write content to a file",
    parameters=[
        ToolParameter("path", "string", "File path to write", True),
        ToolParameter("content", "string", "Content to write", True)
    ],
    permission=ToolPermission.WRITE
)
async def write_file(path: str, content: str) -> str:
    """Write file contents."""
    try:
        with open(path, 'w') as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"


@tool(
    name="run_command",
    description="Run a shell command",
    parameters=[
        ToolParameter("command", "string", "Command to execute", True),
        ToolParameter("cwd", "string", "Working directory", False),
        ToolParameter("timeout", "number", "Timeout in seconds", False, 30)
    ],
    permission=ToolPermission.EXECUTE
)
async def run_command(command: str, cwd: str = None, timeout: int = 30) -> Dict[str, Any]:
    """Run shell command."""
    import subprocess
    import os
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


# Tool registry instance
_default_registry: Optional[ToolRegistry] = None


def get_default_registry() -> ToolRegistry:
    """Get or create default tool registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
        # Register built-in tools
        _default_registry.register(end_voice_conversation, "voice")
        _default_registry.register(spawn_agent, "agent")
        _default_registry.register(get_agent_status, "agent")
        _default_registry.register(take_photo, "voice")
        _default_registry.register(read_file, "file")
        _default_registry.register(write_file, "file")
        _default_registry.register(run_command, "system")
    return _default_registry


def create_tool_executor(registry: ToolRegistry = None) -> ToolExecutor:
    """Create tool executor with registry."""
    return ToolExecutor(registry or get_default_registry())


def create_command_parser(registry: ToolRegistry = None) -> JSONCommandParser:
    """Create JSON command parser."""
    return JSONCommandParser(registry or get_default_registry())