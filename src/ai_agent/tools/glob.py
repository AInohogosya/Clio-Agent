"""
GlobTool — search files using glob patterns (e.g. **/*.ts).
"""

import glob as globlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import Permission, ToolInput, ToolResult, ToolExecutor
from .exceptions import ToolError, ToolErrorCode


@dataclass
class GlobInput(ToolInput):
    pattern: str = ""
    path: str = "."


class GlobTool(ToolExecutor):
    name = "glob"
    description = "Search files using glob patterns (e.g. **/*.py)."
    required_permission = Permission.READ

    def _execute(self, input: GlobInput) -> ToolResult:
        if not input.pattern.strip():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.INVALID_PATTERN,
                    message="Empty glob pattern",
                ),
                tool_name=self.name,
            )

        base = Path(input.path).resolve()
        if not base.is_dir():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.INVALID_PATTERN,
                    message=f"Base directory does not exist: {base}",
                ),
                tool_name=self.name,
            )

        # Use Python's glob which supports ** for recursive search
        pattern = str(base / input.pattern)
        try:
            matches = sorted(globlib.glob(pattern, recursive=True))
        except Exception as exc:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.INVALID_PATTERN,
                    message=str(exc),
                ),
                tool_name=self.name,
            )

        if not matches:
            output = f"No files matched pattern '{input.pattern}' in {base}"
        else:
            matches_str = "\n".join(str(Path(m)) for m in matches)
            output = f"{len(matches)} file(s) matched:\n{matches_str}"

        return ToolResult.ok(
            output=output,
            tool_name=self.name,
            metadata={
                "pattern": input.pattern,
                "base": str(base),
                "match_count": len(matches),
                "matches": matches,
            },
        )
