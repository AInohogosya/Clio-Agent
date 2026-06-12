"""
ToDo List Tool for AI Agent
Provides a simple todo list with add, list, and delete operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .base import Permission, ToolExecutor, ToolInput, ToolResult, ToolError, ToolErrorCode


# Module-level storage for the todo list (persists across tool invocations)
_todo_items: List[str] = []


@dataclass
class ToDoListInput(ToolInput):
    """Input for the ToDo list tool."""
    action: str = "list"  # "list", "add", "delete"
    item: Optional[str] = None  # The todo item text (for add)
    index: Optional[int] = None  # The 1-based index (for delete)


class ToDoListTool(ToolExecutor):
    """Tool for managing a simple todo list."""

    name = "todo_list"
    description = "Manage a todo list: list all items, add new items, or delete items by index"
    required_permission = Permission.WRITE

    def _execute(self, input: ToDoListInput) -> ToolResult:
        global _todo_items

        action = input.action.lower()

        if action == "list":
            return self._list_items()
        elif action == "add":
            return self._add_item(input.item)
        elif action == "delete":
            return self._delete_item(input.index)
        else:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Unknown action: {action}. Use 'list', 'add', or 'delete'.",
                ),
                tool_name=self.name,
            )

    def _list_items(self) -> ToolResult:
        """List all todo items with numbers."""
        if not _todo_items:
            return ToolResult.ok("ToDo list is empty.", tool_name=self.name)

        lines = ["ToDo List:"]
        for i, item in enumerate(_todo_items, 1):
            lines.append(f"  {i}. {item}")
        return ToolResult.ok("\n".join(lines), tool_name=self.name)

    def _add_item(self, item: Optional[str]) -> ToolResult:
        """Add a new item to the todo list."""
        if not item or not item.strip():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="Cannot add empty item. Provide a non-empty string.",
                ),
                tool_name=self.name,
            )

        _todo_items.append(item.strip())
        return ToolResult.ok(
            f"Added item #{len(_todo_items)}: {item.strip()}",
            tool_name=self.name,
        )

    def _delete_item(self, index: Optional[int]) -> ToolResult:
        """Delete an item by 1-based index."""
        if index is None:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="Index is required for delete action.",
                ),
                tool_name=self.name,
            )

        if index < 1 or index > len(_todo_items):
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Invalid index: {index}. Valid range: 1-{len(_todo_items)}.",
                ),
                tool_name=self.name,
            )

        deleted_item = _todo_items.pop(index - 1)
        return ToolResult.ok(
            f"Deleted item #{index}: {deleted_item}",
            tool_name=self.name,
        )


def clear_todo_list() -> None:
    """Clear all todo items (useful for testing)."""
    global _todo_items
    _todo_items = []


def get_todo_items() -> List[str]:
    """Get a copy of the current todo items."""
    return list(_todo_items)