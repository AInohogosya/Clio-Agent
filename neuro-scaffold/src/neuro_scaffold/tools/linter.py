"""Background linter and syntax checker."""

from __future__ import annotations

import ast as python_ast
import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import structlog

from neuro_scaffold.agent.models import LintIssue, LintResult, Severity

logger = structlog.get_logger(__name__)


class LinterChecker:
    """Runs linting and syntax checks on code files."""

    def __init__(self, default_timeout: float = 30.0) -> None:
        self._timeout = default_timeout

    async def check_file(self, file_path: str) -> LintResult:
        """Check a single file for syntax and lint issues."""
        path = Path(file_path)
        if not path.exists():
            return LintResult(
                issues=[
                    LintIssue(
                        file=file_path,
                        line=1,
                        column=0,
                        severity=Severity.ERROR,
                        message="File not found",
                    )
                ]
            )

        suffix = path.suffix.lower()
        start = time.monotonic()

        if suffix == ".py":
            result = await self._check_python(path)
        elif suffix in (".js", ".mjs", ".cjs"):
            result = await self._check_with_subprocess(["node", "--check", str(path)])
        elif suffix in (".ts", ".tsx"):
            result = await self._check_with_subprocess(["tsc", "--noEmit", str(path)])
        elif suffix == ".json":
            result = await self._check_json(path)
        else:
            result = LintResult(files_checked=1)

        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    async def check_directory(
        self,
        dir_path: str,
        extensions: list[str] | None = None,
    ) -> LintResult:
        """Check all files in a directory."""
        path = Path(dir_path)
        if not path.is_dir():
            return LintResult(
                issues=[
                    LintIssue(
                        file=dir_path,
                        line=1,
                        column=0,
                        severity=Severity.ERROR,
                        message="Not a directory",
                    )
                ]
            )

        extensions = extensions or [".py", ".js", ".ts", ".json"]
        all_issues: list[LintIssue] = []
        files_checked = 0
        start = time.monotonic()

        for ext in extensions:
            for file_path in path.rglob(f"*{ext}"):
                if ".git" in file_path.parts or "node_modules" in file_path.parts or "__pycache__" in file_path.parts:
                    continue
                file_result = await self.check_file(str(file_path))
                all_issues.extend(file_result.issues)
                files_checked += 1

        return LintResult(
            issues=all_issues,
            files_checked=files_checked,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def _check_python(self, path: Path) -> LintResult:
        """Check a Python file for syntax errors using the ast module."""
        content = path.read_text(encoding="utf-8")
        issues: list[LintIssue] = []

        try:
            python_ast.parse(content)
        except SyntaxError as exc:
            issues.append(
                LintIssue(
                    file=str(path),
                    line=exc.lineno or 1,
                    column=exc.offset or 0,
                    severity=Severity.ERROR,
                    message=str(exc),
                    rule_id="E0001",
                    source="python_ast",
                )
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-m", "py_compile", str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            if stderr:
                issues.append(
                    LintIssue(
                        file=str(path),
                        line=1,
                        column=0,
                        severity=Severity.WARNING,
                        message=stderr.decode("utf-8", errors="replace").strip(),
                        source="py_compile",
                    )
                )
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            pass

        return LintResult(issues=issues, files_checked=1)

    async def _check_json(self, path: Path) -> LintResult:
        """Check a JSON file for syntax errors."""
        issues: list[LintIssue] = []
        try:
            content = path.read_text(encoding="utf-8")
            json.loads(content)
        except json.JSONDecodeError as exc:
            issues.append(
                LintIssue(
                    file=str(path),
                    line=exc.lineno,
                    column=exc.colno,
                    severity=Severity.ERROR,
                    message=str(exc),
                    rule_id="JSON001",
                    source="json",
                )
            )
        return LintResult(issues=issues, files_checked=1)

    async def _check_with_subprocess(self, cmd: list[str]) -> LintResult:
        """Run an external linter/syntax checker."""
        issues: list[LintIssue] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            if proc.returncode != 0 and stderr:
                text = stderr.decode("utf-8", errors="replace").strip()
                issues.append(
                    LintIssue(
                        file=cmd[-1] if cmd else "",
                        line=1,
                        column=0,
                        severity=Severity.ERROR,
                        message=text[:500],
                        source=cmd[0] if cmd else "unknown",
                    )
                )
        except (asyncio.TimeoutError, FileNotFoundError, OSError) as exc:
            issues.append(
                LintIssue(
                    file=cmd[-1] if cmd else "",
                    line=1,
                    column=0,
                    severity=Severity.WARNING,
                    message=f"Linter not available: {exc}",
                    source=cmd[0] if cmd else "unknown",
                )
            )
        return LintResult(issues=issues, files_checked=1)
