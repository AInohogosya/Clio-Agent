"""
FileWriteTool — create a new file or overwrite an existing file.
"""

from dataclasses import dataclass
from pathlib import Path

from .base import Permission, ToolInput, ToolResult, ToolExecutor
from .exceptions import FileWriteToolError, ToolError, ToolErrorCode


@dataclass
class FileWriteInput(ToolInput):
    file_path: str = ""
    content: str = ""


class FileWriteTool(ToolExecutor):
    name = "file_write"
    description = "Create a new file or overwrite an existing file."
    required_permission = Permission.WRITE

    def _execute(self, input: FileWriteInput) -> ToolResult:
        path = Path(input.file_path)

        if input.file_path.endswith("/") or (path.exists() and path.is_dir()):
            raise FileWriteToolError(str(path), "Path is a directory")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(input.content, encoding="utf-8")
        except PermissionError:
            raise FileWriteToolError(str(path), "Permission denied")
        except OSError as exc:
            raise FileWriteToolError(str(path), str(exc))

        lines = input.content.count("\n") + (1 if input.content and not input.content.endswith("\n") else 0)

        return ToolResult.ok(
            output=f"File written: {path} ({lines} lines, {len(input.content)} bytes)",
            tool_name=self.name,
            metadata={
                "path": str(path),
                "lines": lines,
                "size_bytes": len(input.content),
            },
        )
