"""
Base types and interfaces for the Tool System.

Every tool follows the unified protocol:
    execute(input: ToolInput) -> ToolResult
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from ..utils.logger import get_logger
from .exceptions import (
    ToolError,
    ToolErrorCode,
    ToolExecutionError,
    ToolPermissionError,
    ToolSystemException,
)


# ── Permission model ──


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"


@dataclass
class PermissionSet:
    """Set of permissions granted to the agent runtime."""
    allowed: set = field(default_factory=lambda: {
        Permission.READ, Permission.WRITE, Permission.EXECUTE,
    })

    def has(self, permission: Permission) -> bool:
        return permission in self.allowed


# ── Input / Output ──


@dataclass
class ToolInput:
    """Base class for all tool inputs. Subclass per tool."""
    pass


@dataclass
class ToolResult:
    """Unified result returned by every tool."""
    success: bool
    output: str
    tool_name: str = ""
    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    duration_ms: float = 0.0
    error: Optional[ToolError] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, output: str, tool_name: str = "", **kw: Any) -> ToolResult:
        return cls(success=True, output=output, tool_name=tool_name, **kw)

    @classmethod
    def fail(
        cls,
        error: ToolError,
        tool_name: str = "",
        **kw: Any,
    ) -> ToolResult:
        return cls(success=False, output="", tool_name=tool_name, error=error, **kw)


# ── Parallel execution ──


@dataclass
class ParallelTask:
    """A single tool invocation inside a parallel batch."""
    tool_name: str
    input: ToolInput
    label: str = ""          # optional human-readable label for log output


@dataclass
class ParallelResult:
    """Aggregated result of a parallel batch execution."""
    results: List[Tuple[ParallelTask, ToolResult]]
    total_duration_ms: float
    success_count: int
    fail_count: int
    parallel: bool = True

    @property
    def all_succeeded(self) -> bool:
        return self.fail_count == 0

    def formatted_output(self) -> str:
        lines = [f"[parallel batch] {self.success_count} ok, {self.fail_count} failed, {self.total_duration_ms:.0f}ms"]
        for task, result in self.results:
            tag = task.label or task.tool_name
            status = "ok" if result.success else "FAIL"
            lines.append(f"  [{status}] {tag}: {result.output[:300]}")
        return "\n".join(lines)


# ── Logging ──


@dataclass
class ToolExecutionLog:
    """Record of a single tool invocation."""
    execution_id: str
    tool_name: str
    input_summary: Dict[str, Any]
    result_summary: Dict[str, Any]
    timestamp: float
    duration_ms: float
    success: bool


class ToolLogger:
    """Buffered logger for tool execution records."""

    def __init__(self) -> None:
        self._logger = get_logger("tools")
        self._records: List[ToolExecutionLog] = []

    def log(self, record: ToolExecutionLog) -> None:
        self._records.append(record)
        status = "OK" if record.success else "FAIL"
        self._logger.info(
            f"[{status}] {record.tool_name} ({record.duration_ms:.1f}ms) "
            f"id={record.execution_id}",
            execution_id=record.execution_id,
            tool_name=record.tool_name,
            duration_ms=record.duration_ms,
            success=record.success,
        )

    @property
    def records(self) -> List[ToolExecutionLog]:
        return list(self._records)


# Global tool logger instance
_tool_logger = ToolLogger()


def get_tool_logger() -> ToolLogger:
    return _tool_logger


# ── Abstract tool ──


class ToolExecutor(ABC):
    """Every tool must subclass this."""

    name: str = ""
    description: str = ""
    required_permission: Permission = Permission.READ
    guideline: str = "Understand the essence of the task before acting. Avoid unnecessary actions. Prefer direct, minimal steps over exploratory or redundant operations."

    def __init__(self, permissions: Optional[PermissionSet] = None) -> None:
        self.permissions = permissions or PermissionSet()
        self._instance_logger = get_logger(f"tools.{self.name}")

    # ── lifecycle hooks ──

    def check_permission(self) -> None:
        if not self.permissions.has(self.required_permission):
            raise ToolPermissionError(self.required_permission.value, self.name)

    # ── main entry ──

    def execute(self, input: ToolInput) -> ToolResult:
        start = time.monotonic()
        try:
            self.check_permission()
            result = self._execute(input)
        except ToolSystemException as exc:
            result = ToolResult.fail(
                error=ToolError(
                    code=exc.tool_code,
                    message=str(exc),
                ),
                tool_name=self.name,
            )
        except Exception as exc:
            result = ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=str(exc),
                ),
                tool_name=self.name,
            )
        result.duration_ms = (time.monotonic() - start) * 1000
        result.tool_name = self.name
        get_tool_logger().log(
            ToolExecutionLog(
                execution_id=result.execution_id,
                tool_name=self.name,
                input_summary=self._summarize_input(input),
                result_summary={"success": result.success},
                timestamp=time.time(),
                duration_ms=result.duration_ms,
                success=result.success,
            )
        )
        return result

    @abstractmethod
    def _execute(self, input: ToolInput) -> ToolResult:
        ...

    # ── helpers ──

    def _summarize_input(self, input: ToolInput) -> Dict[str, Any]:
        """Extract a log-safe summary from the tool input."""
        summary: Dict[str, Any] = {}
        for k, v in input.__dict__.items():
            if isinstance(v, str) and len(v) > 200:
                summary[k] = v[:200] + "…"
            else:
                summary[k] = v
        return summary


# ── Registry ──


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolExecutor] = {}
        self._logger = get_logger("tools.registry")

    def register(self, tool: ToolExecutor) -> None:
        self._tools[tool.name] = tool
        self._logger.info(f"Registered tool: {tool.name}")

    def get(self, name: str) -> Optional[ToolExecutor]:
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def execute(self, name: str, input: ToolInput) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Unknown tool: {name}",
                ),
                tool_name=name,
            )
        return tool.execute(input)

    def execute_parallel(
        self,
        tasks: List[ParallelTask],
        max_workers: int = 0,
    ) -> ParallelResult:
        """
        Execute multiple tool calls concurrently via ThreadPoolExecutor.

        Args:
            tasks:       List of ParallelTask describing each tool invocation.
            max_workers: Max concurrency.  0 (default) → min(len(tasks), 8).

        Returns:
            ParallelResult with individual results and aggregate stats.
        """
        if not tasks:
            return ParallelResult(
                results=[],
                total_duration_ms=0,
                success_count=0,
                fail_count=0,
            )

        if max_workers <= 0:
            max_workers = min(len(tasks), 8)

        start = time.monotonic()
        results: List[Tuple[ParallelTask, ToolResult]] = []
        ok_count = 0
        fail_count = 0

        self._logger.info(
            f"Parallel batch start: {len(tasks)} tasks, {max_workers} workers"
        )

        def _run(task: ParallelTask) -> Tuple[ParallelTask, ToolResult]:
            return (task, self.execute(task.tool_name, task.input))

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {pool.submit(_run, t): t for t in tasks}
            for future in as_completed(future_map):
                task, result = future.result()
                results.append((task, result))
                if result.success:
                    ok_count += 1
                else:
                    fail_count += 1

        elapsed_ms = (time.monotonic() - start) * 1000
        self._logger.info(
            f"Parallel batch done: {ok_count} ok, {fail_count} failed, {elapsed_ms:.0f}ms"
        )

        return ParallelResult(
            results=results,
            total_duration_ms=elapsed_ms,
            success_count=ok_count,
            fail_count=fail_count,
        )


# Global registry singleton
_global_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry
