"""
Tool System for AI Agent
Implements FileRead, FileWrite, FileEdit, Bash, Glob, and Grep tools
with unified interface, permission management, and execution logging.
"""

from .base import (
    ParallelResult,
    ParallelTask,
    Permission,
    ToolError,
    ToolErrorCode,
    ToolExecutor,
    ToolInput,
    ToolRegistry,
    ToolResult,
    get_tool_registry,
)
from .exceptions import (
    CommandBlockedToolError,
    CommandFailedToolError,
    CommandTimeoutToolError,
    EditMismatchToolError,
    FileAccessToolError,
    FileNotFoundToolError,
    FileWriteToolError,
    InvalidPatternToolError,
    ToolExecutionError,
    ToolPermissionError,
)
from .file_read import FileReadTool, FileReadInput
from .file_write import FileWriteTool, FileWriteInput
from .file_edit import FileEditTool, FileEditInput
from .bash import BashTool, BashInput
from .glob import GlobTool, GlobInput
from .grep import GrepTool, GrepInput
from .todo_list import ToDoListTool, ToDoListInput, clear_todo_list, get_todo_items
from .memo import MemoTool, MemoInput, clear_memo_list, get_memo_items


def initialize_tool_registry(registry=None):
    """Initialize the tool registry with all available tools."""
    from .base import get_tool_registry, PermissionSet
    from .file_read import FileReadTool
    from .file_write import FileWriteTool
    from .file_edit import FileEditTool
    from .bash import BashTool
    from .glob import GlobTool
    from .grep import GrepTool
    from .memo import MemoTool

    if registry is None:
        registry = get_tool_registry()

    permissions = PermissionSet()
    registry.register(FileReadTool(permissions))
    registry.register(FileWriteTool(permissions))
    registry.register(FileEditTool(permissions))
    registry.register(BashTool(permissions))
    registry.register(GlobTool(permissions))
    registry.register(GrepTool(permissions))
    registry.register(ToDoListTool(permissions))
    registry.register(MemoTool(permissions))

    return registry


__all__ = [
    # Base
    "Permission",
    "ToolError",
    "ToolErrorCode",
    "ToolExecutor",
    "ToolInput",
    "ToolRegistry",
    "ToolResult",
    "get_tool_registry",
    # Exceptions
    "FileNotFoundToolError",
    "FileAccessToolError",
    "FileWriteToolError",
    "EditMismatchToolError",
    "CommandBlockedToolError",
    "CommandTimeoutToolError",
    "CommandFailedToolError",
    "InvalidPatternToolError",
    "ToolPermissionError",
    "ToolExecutionError",
    # Tools
    "FileReadTool",
    "FileReadInput",
    "FileWriteTool",
    "FileWriteInput",
    "FileEditTool",
    "FileEditInput",
    "BashTool",
    "BashInput",
    "GlobTool",
    "GlobInput",
    "GrepTool",
    "GrepInput",
    "ToDoListTool",
    "ToDoListInput",
    "clear_todo_list",
    "get_todo_items",
    "MemoTool",
    "MemoInput",
    "clear_memo_list",
    "get_memo_items",
    "initialize_tool_registry",
]
