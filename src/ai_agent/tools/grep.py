"""
GrepTool — search file contents (ripgrep equivalent).
Uses Python's re module for regex matching across files.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .base import Permission, ToolInput, ToolResult, ToolExecutor
from .exceptions import ToolError, ToolErrorCode

# Reasonable cap to prevent runaway searches
_MAX_MATCHES = 500
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per file


@dataclass
class GrepMatch:
    file_path: str
    line_number: int
    line_text: str


@dataclass
class GrepInput(ToolInput):
    pattern: str = ""
    path: str = "."
    file_extensions: Optional[List[str]] = None
    max_matches: int = 100
    ignore_case: bool = False
    context_lines: int = 0


class GrepTool(ToolExecutor):
    name = "grep"
    description = "Search file contents using regex (ripgrep equivalent)."
    required_permission = Permission.READ

    def _execute(self, input: GrepInput) -> ToolResult:
        if not input.pattern.strip():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.INVALID_PATTERN,
                    message="Empty search pattern",
                ),
                tool_name=self.name,
            )

        base = Path(input.path).resolve()
        if not base.exists():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.INVALID_PATTERN,
                    message=f"Path does not exist: {base}",
                ),
                tool_name=self.name,
            )

        flags = re.IGNORECASE if input.ignore_case else 0
        try:
            regex = re.compile(input.pattern, flags)
        except re.error as exc:
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.INVALID_PATTERN,
                    message=f"Invalid regex: {exc}",
                ),
                tool_name=self.name,
            )

        max_matches = min(input.max_matches, _MAX_MATCHES)
        extensions = set(input.file_extensions) if input.file_extensions else None
        context_lines = max(0, input.context_lines)

        # Cache file lines for context rendering
        file_lines_cache: Dict[Path, List[str]] = {}
        matches: List[Tuple[Path, int, str]] = []

        if base.is_file():
            files = [base]
        else:
            files = [f for f in base.rglob("*") if f.is_file()]

        for file_path in files:
            if len(matches) >= max_matches:
                break

            if extensions and file_path.suffix.lstrip(".") not in extensions:
                continue

            try:
                if file_path.stat().st_size > _MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue

            file_lines = text.splitlines()
            file_lines_cache[file_path] = file_lines

            for lineno, line in enumerate(file_lines, 1):
                if regex.search(line):
                    matches.append((file_path, lineno, line.rstrip()))
                    if len(matches) >= max_matches:
                        break

        if not matches:
            ext_info = (
                f" (extensions: {', '.join(extensions)})" if extensions else ""
            )
            output = f"No matches for '{input.pattern}' in {base}{ext_info}"
        else:
            # Group matches by file
            grouped: Dict[Path, List[Tuple[int, str]]] = {}
            for fp, lineno, text in matches:
                grouped.setdefault(fp, []).append((lineno, text))

            out_lines: List[str] = []
            for fp, file_matches in grouped.items():
                out_lines.append(f"\
{fp}:")
                all_lines = file_lines_cache[fp]
                total = len(all_lines)

                match_line_nums: Set[int] = {ln for ln, _ in file_matches}

                # Build set of lines to display (matches + context)
                show: Set[int] = set()
                for ln in match_line_nums:
                    show.add(ln)
                    for ctx in range(ln - context_lines, ln + context_lines + 1):
                        if 1 <= ctx <= total:
                            show.add(ctx)

                sorted_show = sorted(show)
                for i, ln in enumerate(sorted_show):
                    if i > 0 and ln > sorted_show[i - 1] + 1:
                        out_lines.append("  ---")
                    text = all_lines[ln - 1].rstrip()
                    marker = ">>" if ln in match_line_nums else " "
                    out_lines.append(f"{marker} {ln}: {text}")

            output = f"{len(matches)} match(es) for '{input.pattern}':" + "\
" + "\
".join(out_lines)

        return ToolResult.ok(
            output=output,
            tool_name=self.name,
            metadata={
                "pattern": input.pattern,
                "base": str(base),
                "match_count": len(matches),
                "truncated": len(matches) >= max_matches,
            },
        )

