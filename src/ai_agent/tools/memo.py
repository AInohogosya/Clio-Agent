"""
Memo Tool for AI Agent
Provides a simple memo system with save, list, and delete operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .base import Permission, ToolExecutor, ToolInput, ToolResult, ToolError, ToolErrorCode


# Module-level storage for memos (persists across tool invocations)
_memo_items: List[str] = []


@dataclass
class MemoInput(ToolInput):
    """Input for the Memo tool."""
    action: str = "list"  # "list", "save", "delete"
    content: Optional[str] = None  # The memo content (for save)
    index: Optional[int] = None  # The 1-based index (for delete)


class MemoTool(ToolExecutor):
    """Tool for managing memos: list all, save new, or delete by index."""

    name = "memo"
    description = "Manage memos: list all memos, save new memos, or delete memos by index"
    required_permission = Permission.WRITE

    def _execute(self, input: MemoInput) -> ToolResult:
        global _memo_items

        action = input.action.lower()

        if action == "list":
            return self._list_items()
        elif action == "save":
            return self._save_item(input.content)
        elif action == "delete":
            return self._delete_item(input.index)
        else:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Unknown action: {action}. Use 'list', 'save', or 'delete'.",
                ),
                tool_name=self.name,
            )

    def _list_items(self) -> ToolResult:
        """List all memo items with numbers."""
        if not _memo_items:
            return ToolResult.ok("Memo list is empty.", tool_name=self.name)

        lines = ["Memo List:"]
        for i, item in enumerate(_memo_items, 1):
            lines.append(f"  {i}. {item}")
        return ToolResult.ok("\n".join(lines), tool_name=self.name)

    def _save_item(self, content: Optional[str]) -> ToolResult:
        """Save a new memo."""
        if not content or not content.strip():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="Cannot save empty memo. Provide a non-empty string.",
                ),
                tool_name=self.name,
            )

        _memo_items.append(content.strip())
        return ToolResult.ok(
            f"Saved memo #{len(_memo_items)}: {content.strip()}",
            tool_name=self.name,
        )

    def _delete_item(self, index: Optional[int]) -> ToolResult:
        """Delete a memo by 1-based index."""
        if index is None:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message="Index is required for delete action.",
                ),
                tool_name=self.name,
            )

        if index < 1 or index > len(_memo_items):
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.EXECUTION_ERROR,
                    message=f"Invalid index: {index}. Valid range: 1-{len(_memo_items)}.",
                ),
                tool_name=self.name,
            )

        deleted_item = _memo_items.pop(index - 1)
        return ToolResult.ok(
            f"Deleted memo #{index}: {deleted_item}",
            tool_name=self.name,
        )


def clear_memo_list() -> None:
    """Clear all memos (useful for testing)."""
    global _memo_items
    _memo_items = []


def get_memo_items() -> List[str]:
    """Get a copy of the current memo items."""
    return list(_memo_items)