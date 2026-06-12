"""
FileReadTool — read file content (text, code, images referenced by path).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import Permission, ToolInput, ToolResult, ToolExecutor
from .exceptions import (
    FileNotFoundToolError,
    FileAccessToolError,
    ToolError,
    ToolErrorCode,
)


@dataclass
class FileReadInput(ToolInput):
    file_path: str = ""
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class FileReadTool(ToolExecutor):
    name = "file_read"
    description = "Read file content. Supports text, code, and binary files."
    required_permission = Permission.READ

    def _execute(self, input: FileReadInput) -> ToolResult:
        path = Path(input.file_path)

        if not path.exists():
            raise FileNotFoundToolError(str(path))

        if not path.is_file():
            raise FileAccessToolError(str(path), "Not a regular file")

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            raise FileAccessToolError(str(path), "Permission denied")
        except OSError as exc:
            raise FileAccessToolError(str(path), str(exc))

        total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

        # Apply optional line range
        if input.start_line is not None or input.end_line is not None:
            lines = content.splitlines(keepends=True)
            start = (input.start_line or 1) - 1  # 1-indexed → 0-indexed
            end = input.end_line if input.end_line is not None else len(lines)
            start = max(0, start)
            end = min(len(lines), end)
            content = "".join(lines[start:end])

        return ToolResult.ok(
            output=content,
            tool_name=self.name,
            metadata={
                "path": str(path),
                "total_lines": total_lines,
                "size_bytes": path.stat().st_size,
            },
        )
