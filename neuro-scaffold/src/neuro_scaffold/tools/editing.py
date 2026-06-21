"""Smart editing tools with strict diff validation."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class DiffHunk:
    """A single hunk in a unified diff."""
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[str] = field(default_factory=list)


@dataclass
class DiffPatch:
    """A complete diff patch for a single file."""
    old_path: str
    new_path: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deletion: bool = False


class DiffValidationError(Exception):
    """Raised when a diff fails validation."""
    pass


class DiffValidator:
    """Validates unified diff format and content."""

    UNIFIED_DIFF_HEADER = re.compile(r"^---\s+(.+)$")
    UNIFIED_DIFF_NEW = re.compile(r"^\+\+\+\s+(.+)$")
    HUNK_HEADER = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")

    def validate_patch(self, patch_text: str) -> list[DiffPatch]:
        """Validate a unified diff and return parsed patches."""
        if not patch_text.strip():
            raise DiffValidationError("Empty patch")

        patches: list[DiffPatch] = []
        current_patch: DiffPatch | None = None
        current_hunk: DiffHunk | None = None
        lines = patch_text.splitlines()

        for i, line in enumerate(lines):
            header_match = self.UNIFIED_DIFF_HEADER.match(line)
            if header_match:
                current_patch = DiffPatch(
                    old_path=header_match.group(1).strip(),
                    new_path="",
                )
                if header_match.group(1) == "/dev/null":
                    current_patch.is_new_file = True
                patches.append(current_patch)
                current_hunk = None
                continue

            new_match = self.UNIFIED_DIFF_NEW.match(line)
            if new_match and current_patch is not None:
                current_patch.new_path = new_match.group(1).strip()
                if new_match.group(1) == "/dev/null":
                    current_patch.is_deletion = True
                continue

            hunk_match = self.HUNK_HEADER.match(line)
            if hunk_match and current_patch is not None:
                current_hunk = DiffHunk(
                    old_start=int(hunk_match.group(1)),
                    old_lines=int(hunk_match.group(2) or 1),
                    new_start=int(hunk_match.group(3)),
                    new_lines=int(hunk_match.group(4) or 1),
                )
                current_patch.hunks.append(current_hunk)
                continue

            if current_hunk is not None:
                if line.startswith(("+", "-", " ")):
                    current_hunk.lines.append(line)
                elif line.strip() == "":
                    current_hunk.lines.append(" " + line)
                else:
                    raise DiffValidationError(
                        f"Invalid diff line {i + 1}: {line[:50]}"
                    )

        if not patches:
            raise DiffValidationError("No valid patches found in diff")

        for patch in patches:
            self._validate_patch_counts(patch)

        return patches

    def _validate_patch_counts(self, patch: DiffPatch) -> None:
        for hunk in patch.hunks:
            removed = sum(1 for l in hunk.lines if l.startswith("-"))
            added = sum(1 for l in hunk.lines if l.startswith("+"))
            context = sum(1 for l in hunk.lines if l.startswith(" "))
            if removed + context != hunk.old_lines and not patch.is_new_file:
                raise DiffValidationError(
                    f"Hunk line count mismatch: expected {hunk.old_lines} old lines, got {removed + context}"
                )
            if added + context != hunk.new_lines and not patch.is_deletion:
                raise DiffValidationError(
                    f"Hunk line count mismatch: expected {hunk.new_lines} new lines, got {added + context}"
                )

    def generate_unified_diff(
        self,
        old_content: str,
        new_content: str,
        old_path: str = "a/file",
        new_path: str = "b/file",
        context_lines: int = 3,
    ) -> str:
        """Generate a unified diff between two strings."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=old_path,
            tofile=new_path,
            n=context_lines,
        )
        return "".join(diff)


class SmartEditor:
    """Smart file editor with search/replace and diff-based editing."""

    def __init__(self, validator: DiffValidator | None = None) -> None:
        self._validator = validator or DiffValidator()

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        """Read a file, optionally with line range."""
        try:
            file_path = Path(path)
            if not file_path.exists():
                return {"success": False, "content": "", "error": f"File not found: {path}"}
            if not file_path.is_file():
                return {"success": False, "content": "", "error": f"Not a file: {path}"}

            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines()

            if start_line is not None or end_line is not None:
                s = (start_line or 1) - 1
                e = end_line if end_line is not None else len(lines)
                lines = lines[s:e]
                content = "\n".join(lines)

            return {
                "success": True,
                "content": content,
                "total_lines": len(content.splitlines()),
                "error": None,
            }
        except UnicodeDecodeError:
            return {"success": False, "content": "", "error": f"Cannot decode file as UTF-8: {path}"}
        except PermissionError:
            return {"success": False, "content": "", "error": f"Permission denied: {path}"}
        except OSError as exc:
            return {"success": False, "content": "", "error": str(exc)}

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Write content to a file, creating it if it doesn't exist."""
        try:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return {
                "success": True,
                "bytes_written": len(content.encode("utf-8")),
                "error": None,
            }
        except PermissionError:
            return {"success": False, "bytes_written": 0, "error": f"Permission denied: {path}"}
        except OSError as exc:
            return {"success": False, "bytes_written": 0, "error": str(exc)}

    def search_replace(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict[str, Any]:
        """Perform search/replace in a file."""
        read_result = self.read_file(path)
        if not read_result["success"]:
            return read_result

        content = read_result["content"]
        count = content.count(old_string)

        if count == 0:
            return {
                "success": False,
                "replacements": 0,
                "error": "Search string not found in file",
            }

        if count > 1 and not replace_all:
            return {
                "success": False,
                "replacements": 0,
                "error": f"Found {count} occurrences. Use replace_all=True to replace all.",
            }

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        write_result = self.write_file(path, new_content)
        if not write_result["success"]:
            return write_result

        diff = self._validator.generate_unified_diff(content, new_content, path, path)

        return {
            "success": True,
            "replacements": count if replace_all else 1,
            "diff": diff,
            "error": None,
        }

    def apply_patch(self, patch_text: str) -> dict[str, Any]:
        """Apply a unified diff patch to files."""
        try:
            patches = self._validator.validate_patch(patch_text)
        except DiffValidationError as exc:
            return {"success": False, "files_modified": 0, "error": str(exc)}

        modified = 0
        errors: list[str] = []

        for patch in patches:
            if patch.is_new_file:
                result = self.write_file(patch.new_path, "")
                if result["success"]:
                    modified += 1
                else:
                    errors.append(result["error"] or "Unknown error")
                continue

            if patch.is_deletion:
                try:
                    Path(patch.old_path).unlink()
                    modified += 1
                except OSError as exc:
                    errors.append(str(exc))
                continue

            read_result = self.read_file(patch.old_path)
            if not read_result["success"]:
                errors.append(read_result["error"] or f"Cannot read {patch.old_path}")
                continue

            old_lines = read_result["content"].splitlines()
            new_lines = self._apply_hunks(old_lines, patch.hunks)
            new_content = "\n".join(new_lines)
            if read_result["content"].endswith("\n"):
                new_content += "\n"

            write_result = self.write_file(patch.new_path, new_content)
            if write_result["success"]:
                modified += 1
            else:
                errors.append(write_result["error"] or "Unknown error")

        return {
            "success": len(errors) == 0,
            "files_modified": modified,
            "errors": errors,
            "error": "; ".join(errors) if errors else None,
        }

    def _apply_hunks(self, lines: list[str], hunks: list[DiffHunk]) -> list[str]:
        """Apply diff hunks to a list of lines."""
        result: list[str] = []
        old_idx = 0

        for hunk in hunks:
            result.extend(lines[old_idx:hunk.old_start - 1])
            for line in hunk.lines:
                if line.startswith("+"):
                    result.append(line[1:])
                elif line.startswith(" "):
                    result.append(line[1:])
                elif line.startswith("-"):
                    pass
            old_idx = hunk.old_start - 1 + hunk.old_lines

        result.extend(lines[old_idx:])
        return result
