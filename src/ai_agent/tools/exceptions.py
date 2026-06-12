"""
Custom exceptions for the Tool System.
Each maps to a specific failure mode with structured context.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from enum import Enum

from ..utils.exceptions import AIAgentException, ErrorCategory, ErrorContext


class ToolErrorCode(str, Enum):
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_ACCESS_DENIED = "FILE_ACCESS_DENIED"
    FILE_WRITE_ERROR = "FILE_WRITE_ERROR"
    EDIT_MISMATCH = "EDIT_MISMATCH"
    COMMAND_BLOCKED = "COMMAND_BLOCKED"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    COMMAND_FAILED = "COMMAND_FAILED"
    INVALID_PATTERN = "INVALID_PATTERN"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    EXECUTION_ERROR = "EXECUTION_ERROR"


@dataclass
class ToolError:
    """Structured error returned inside ToolResult on failure."""
    code: ToolErrorCode
    message: str
    details: Dict[str, Any] = field(default_factory=dict)


# ── Exception hierarchy (raised internally, caught and wrapped) ──


class ToolSystemException(AIAgentException):
    """Base for all tool-related exceptions."""

    def __init__(self, message: str, code: ToolErrorCode, **kwargs):
        ctx = ErrorContext(
            category=ErrorCategory.EXTERNAL,
            retryable=False,
            max_retries=0,
            backoff_seconds=0.0,
            error_code=code.value,
        )
        super().__init__(message, context=ctx, **kwargs)
        self.tool_code = code


class FileNotFoundToolError(ToolSystemException):
    def __init__(self, path: str):
        super().__init__(
            f"File not found: {path}",
            ToolErrorCode.FILE_NOT_FOUND,
            path=path,
        )
        self.path = path


class FileAccessToolError(ToolSystemException):
    def __init__(self, path: str, reason: str):
        super().__init__(
            f"Cannot read file '{path}': {reason}",
            ToolErrorCode.FILE_ACCESS_DENIED,
            path=path,
            reason=reason,
        )
        self.path = path


class FileWriteToolError(ToolSystemException):
    def __init__(self, path: str, reason: str):
        super().__init__(
            f"Cannot write file '{path}': {reason}",
            ToolErrorCode.FILE_WRITE_ERROR,
            path=path,
            reason=reason,
        )
        self.path = path


class EditMismatchToolError(ToolSystemException):
    def __init__(self, path: str, old_string: str, occurrences: int = 0):
        if occurrences == 0:
            msg = f"String to replace not found in '{path}'"
        else:
            msg = (
                f"Found {occurrences} occurrences of the string to replace in "
                f"'{path}'. Use replace_all=True to replace all."
            )
        super().__init__(msg, ToolErrorCode.EDIT_MISMATCH, path=path)
        self.path = path
        self.occurrences = occurrences


class CommandBlockedToolError(ToolSystemException):
    def __init__(self, command: str, reason: str):
        super().__init__(
            f"Command blocked: {reason}",
            ToolErrorCode.COMMAND_BLOCKED,
            command=command,
            reason=reason,
        )
        self.command = command


class CommandTimeoutToolError(ToolSystemException):
    def __init__(self, command: str, timeout: float):
        super().__init__(
            f"Command timed out after {timeout}s",
            ToolErrorCode.COMMAND_TIMEOUT,
            command=command,
            timeout=timeout,
        )
        self.command = command
        self.timeout = timeout


class CommandFailedToolError(ToolSystemException):
    def __init__(self, command: str, exit_code: int, stderr: str = ""):
        super().__init__(
            f"Command failed with exit code {exit_code}",
            ToolErrorCode.COMMAND_FAILED,
            command=command,
            exit_code=exit_code,
            stderr=stderr,
        )
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr


class InvalidPatternToolError(ToolSystemException):
    def __init__(self, pattern: str, reason: str):
        super().__init__(
            f"Invalid pattern '{pattern}': {reason}",
            ToolErrorCode.INVALID_PATTERN,
            pattern=pattern,
            reason=reason,
        )
        self.pattern = pattern


class ToolPermissionError(ToolSystemException):
    def __init__(self, permission: str, tool_name: str):
        super().__init__(
            f"Tool '{tool_name}' requires '{permission}' permission",
            ToolErrorCode.PERMISSION_DENIED,
            permission=permission,
            tool_name=tool_name,
        )


class ToolExecutionError(ToolSystemException):
    def __init__(self, tool_name: str, reason: str):
        super().__init__(
            f"Tool '{tool_name}' execution failed: {reason}",
            ToolErrorCode.EXECUTION_ERROR,
            tool_name=tool_name,
            reason=reason,
        )
