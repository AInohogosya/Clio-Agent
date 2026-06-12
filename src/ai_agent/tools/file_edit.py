"""
FileEditTool — partial edit of an existing file (search & replace).
Supports single replacement and replace-all mode.
"""

from dataclasses import dataclass
from pathlib import Path

from .base import Permission, ToolInput, ToolResult, ToolExecutor
from .exceptions import (
    FileNotFoundToolError,
    FileAccessToolError,
    EditMismatchToolError,
    FileWriteToolError,
)


@dataclass
class FileEditInput(ToolInput):
    file_path: str = ""
    old_string: str = ""
    new_string: str = ""
    replace_all: bool = False


class FileEditTool(ToolExecutor):
    name = "file_edit"
    description = "Partial edit of an existing file (search & replace)."
    required_permission = Permission.WRITE

    def _execute(self, input: FileEditInput) -> ToolResult:
        path = Path(input.file_path)

        if not path.exists():
            raise FileNotFoundToolError(str(path))

        try:
            content = path.read_text(encoding="utf-8")
        except PermissionError:
            raise FileAccessToolError(str(path), "Permission denied")

        occurrences = content.count(input.old_string)

        if occurrences == 0:
            raise EditMismatchToolError(str(path), input.old_string, occurrences=0)

        if not input.replace_all and occurrences > 1:
            raise EditMismatchToolError(str(path), input.old_string, occurrences=occurrences)

        if input.replace_all:
            new_content = content.replace(input.old_string, input.new_string)
        else:
            idx = content.index(input.old_string)
            new_content = content[:idx] + input.new_string + content[idx + len(input.old_string):]

        try:
            path.write_text(new_content, encoding="utf-8")
        except PermissionError:
            raise FileWriteToolError(str(path), "Permission denied")
        except OSError as exc:
            raise FileWriteToolError(str(path), str(exc))

        return ToolResult.ok(
            output=f"Edited {path}: replaced {occurrences} occurrence(s)",
            tool_name=self.name,
            metadata={
                "path": str(path),
                "occurrences": occurrences,
                "replace_all": input.replace_all,
            },
        )
