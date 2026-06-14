"""
ReviewAgent — specialized sub-agent for code review.

Analyzes code quality, style, security, and best practices.
Produces structured review reports with severity-rated findings.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from ..base import SubAgentBase
from ..context import SubAgentContext
from ..registry import sub_agent
from ..prompts import REVIEW_SYSTEM_PROMPT, REVIEW_TASK_PROMPT
from ...utils.logger import get_logger

logger = get_logger("sub_agent.review")


@sub_agent("review", description="Performs code review, quality analysis, security audit")
class ReviewAgent(SubAgentBase):
    """
    Specialized sub-agent for code review tasks.

    Capabilities:
    - Read and analyze target files
    - Check for common bugs, security issues, style violations
    - Verify consistency with codebase patterns
    - Produce severity-rated findings (Critical/Warning/Info)

    Operates in READ-ONLY mode — never modifies files.
    """

    agent_type = "review"

    def __init__(self, context: SubAgentContext) -> None:
        super().__init__(context)
        self._model_runner = context.model_runner
        self._cwd = context.working_directory
        self._findings: List[Dict[str, str]] = []
        self._files_reviewed: List[str] = []
        self._critical_count = 0
        self._warning_count = 0
        self._info_count = 0

    def initialize(self) -> None:
        super().initialize()
        self._iteration = 0
        self._findings = []
        self._files_reviewed = []
        self._critical_count = 0
        self._warning_count = 0
        self._info_count = 0

    def _run(self) -> str:
        """Execute the review task."""
        self._iteration = 0
        max_iter = self.context.max_iterations

        while self._iteration < max_iter:
            self._iteration += 1

            prompt = self._build_task_prompt()

            if self._model_runner is not None:
                try:
                    from ...external_integration.model_runner import ModelRequest, TaskType
                    request = ModelRequest(
                        task_type=TaskType.AUTONOMOUS_LOOP,
                        prompt=prompt,
                        max_tokens=2048,
                        temperature=0.2,
                    )
                    response = self._model_runner.run_model(request)
                    if response.success and response.content:
                        self._execute_review_output(response.content)
                    else:
                        error = response.error or "Model returned no content"
                        logger.warning(f"Model call failed: {error}")
                        break
                except Exception as e:
                    logger.error(f"Model execution error: {e}")
                    break
            else:
                # Fallback: independent code analysis
                return self._fallback_review()

            # If we've reviewed enough files and have findings, stop
            if len(self._files_reviewed) >= 5 and len(self._findings) >= 3:
                break
            if self._iteration >= max_iter:
                break

        return self._build_report()

    def _build_task_prompt(self) -> str:
        """Build the task prompt for the model."""
        ctx = self.context.build_prompt_context()
        progress = self._format_progress()
        return (
            f"{REVIEW_TASK_PROMPT.format(**ctx)}\n\n"
            f"### Progress (iteration {self._iteration})\n"
            f"{progress}\n\n"
            f"Continue the review. When done, output a structured report."
        )

    def _format_progress(self) -> str:
        """Format review progress for the model."""
        lines = []
        if self._files_reviewed:
            lines.append(f"Files reviewed: {len(self._files_reviewed)}")
            for f in self._files_reviewed[-5:]:
                lines.append(f"  - {f}")
        if self._findings:
            lines.append(f"Total findings: {len(self._findings)} "
                         f"(Critical: {self._critical_count}, "
                         f"Warning: {self._warning_count}, "
                         f"Info: {self._info_count})")
        return "\n".join(lines) if lines else "(starting review)"

    def _execute_review_output(self, output: str) -> None:
        """Parse and execute review commands from model output."""
        lines = output.strip().splitlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("```"):
                continue

            if line.startswith("read("):
                self._do_read(line)
            elif line.startswith("grep("):
                self._do_grep(line)
            elif line.startswith("glob("):
                self._do_glob(line)
            elif line.startswith("bash("):
                self._do_bash(line)

    def _do_read(self, command_line: str) -> None:
        """Execute a read command for review."""
        path = self._extract_arg(command_line, "path")
        if not path:
            return
        full_path = os.path.join(self._cwd, path)
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._files_reviewed.append(path)
                self.context.artifacts[f"content_{path}"] = content[:5000]
                self._analyze_code(path, content)
            except Exception as e:
                logger.warning(f"Could not read {path}: {e}")

    def _do_grep(self, command_line: str) -> None:
        """Execute a grep command for pattern detection."""
        pattern = self._extract_arg(command_line, "pattern")
        path = self._extract_arg(command_line, "path") or "."
        if not pattern:
            return

        full_path = os.path.join(self._cwd, path)
        matches = []
        if os.path.exists(full_path):
            for root, _dirs, files in os.walk(full_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            for i, line in enumerate(f, 1):
                                if re.search(pattern, line):
                                    rel = os.path.relpath(fpath, self._cwd)
                                    matches.append(f"{rel}:{i}: {line.strip()}")
                    except (UnicodeDecodeError, PermissionError):
                        pass

        if matches:
            self.context.artifacts[f"grep_{pattern}"] = matches[:30]
            severity = "warning" if len(matches) > 5 else "info"
            self._add_finding(
                title=f"Pattern: {pattern}",
                detail=f"Found {len(matches)} occurrences",
                severity=severity,
                evidence=matches[:3],
            )

    def _do_glob(self, command_line: str) -> None:
        """Execute a glob command to find review targets."""
        pattern = self._extract_arg(command_line, "pattern")
        if not pattern:
            return
        import glob as glob_mod
        files = glob_mod.glob(pattern, root_dir=self._cwd, recursive=True)
        self.context.artifacts[f"glob_{pattern}"] = files
        # Auto-review found files
        for filepath in files[:10]:
            full_path = os.path.join(self._cwd, filepath)
            if os.path.exists(full_path) and filepath not in self._files_reviewed:
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    self._files_reviewed.append(filepath)
                    self._analyze_code(filepath, content)
                except Exception:
                    pass

    def _do_bash(self, command_line: str) -> None:
        """Execute analysis commands (linters, test runners, etc.)."""
        cmd = self._extract_arg(command_line, "command")
        if not cmd:
            return
        import subprocess
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=self._cwd,
            )
            self.context.artifacts[f"bash_{cmd[:30]}"] = result.stdout[:500]
            if result.stdout.strip():
                self._add_finding(
                    title=f"Command output: {cmd}",
                    detail=result.stdout[:300],
                    severity="info",
                )
        except subprocess.TimeoutExpired:
            logger.warning(f"Bash command timed out: {cmd}")
        except Exception as e:
            logger.warning(f"Bash command failed: {e}")

    def _analyze_code(self, path: str, content: str) -> None:
        """Static analysis pass on file content."""
        lines = content.splitlines()

        # Check for security issues
        dangerous_patterns = [
            (r'eval\s*\(', "Use of eval() — security risk", "critical"),
            (r'exec\s*\(', "Use of exec() — security risk", "critical"),
            (r'subprocess\..*shell\s*=\s*True', "subprocess with shell=True — injection risk", "warning"),
            (r'os\.system\s*\(', "Use of os.system() — prefer subprocess", "warning"),
            (r'input\s*\(', "Use of input() — not recommended in production", "info"),
            (r'password\s*=', "Hardcoded password detected", "critical"),
            (r'secret\s*=', "Possible hardcoded secret", "warning"),
        ]

        for pattern, desc, severity in dangerous_patterns:
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line, re.IGNORECASE):
                    self._add_finding(
                        title=desc,
                        detail=f"{path}:{i}",
                        severity=severity,
                        evidence=[f"{path}:{i}: {line.strip()}"],
                    )

        # Check for code quality issues
        quality_patterns = [
            (r'except\s*:', "Bare except clause — catches all exceptions", "warning"),
            (r'TODO|FIXME|HACK', "TODO/FIXME comment found", "info"),
            (r'print\s*\(', "Debug print statement", "info"),
            (r'import\s+\*', "Wildcard import — avoid", "warning"),
        ]

        for pattern, desc, severity in quality_patterns:
            matches = []
            for i, line in enumerate(lines, 1):
                if re.search(pattern, line):
                    matches.append(f"{path}:{i}: {line.strip()}")
            if matches:
                self._add_finding(
                    title=desc,
                    detail=f"Found {len(matches)} occurrence(s)",
                    severity=severity,
                    evidence=matches[:3],
                )

        # Check for long functions (>50 lines)
        func_start = None
        func_name = None
        for i, line in enumerate(lines, 1):
            m = re.match(r'^(    )?def\s+(\w+)', line)
            if m:
                if func_start is not None and (i - func_start) > 50:
                    self._add_finding(
                        title=f"Long function: {function_name}",
                        detail=f"{path}:{func_start}-{i} ({i - func_start} lines)",
                        severity="warning",
                    )
                func_start = i
                func_name = m.group(2)

    def _add_finding(
        self,
        title: str,
        detail: str,
        severity: str,
        evidence: Optional[List[str]] = None,
    ) -> None:
        """Add a finding and update counters."""
        finding = {
            "title": title,
            "detail": detail,
            "severity": severity,
            "evidence": evidence or [],
        }
        self._findings.append(finding)

        if severity == "critical":
            self._critical_count += 1
        elif severity == "warning":
            self._warning_count += 1
        else:
            self._info_count += 1

    def _fallback_review(self) -> str:
        """Fallback review when no model runner is available."""
        # Do basic static analysis on common file types
        review_targets = []
        for root, _dirs, files in os.walk(self._cwd):
            _dirs[:] = [
                d for d in _dirs
                if d not in {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist'}
            ]
            for fname in files:
                if fname.endswith(('.py', '.js', '.ts')):
                    review_targets.append(os.path.join(root, fname))

        for filepath in review_targets[:20]:
            rel = os.path.relpath(filepath, self._cwd)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                self._files_reviewed.append(rel)
                self._analyze_code(rel, content)
            except Exception:
                pass

        return self._build_report()

    def _build_report(self) -> str:
        """Build the structured review report."""
        lines = [
            "## Code Review Report",
            "",
            f"### Scope",
        ]
        if self._files_reviewed:
            for f in self._files_reviewed:
                lines.append(f"- {f}")
        else:
            lines.append("- No files reviewed")

        lines.extend([
            "",
            "### Summary",
            f"Reviewed {len(self._files_reviewed)} file(s) in "
            f"{self._iteration} iteration(s). "
            f"Found {len(self._findings)} finding(s): "
            f"{self._critical_count} critical, "
            f"{self._warning_count} warning, "
            f"{self._info_count} info.",
        ])

        if self._findings:
            # Group by severity
            for severity_label, severity_key in [
                ("Critical", "critical"),
                ("Warning", "warning"),
                ("Info", "info"),
            ]:
                group = [f for f in self._findings if f["severity"] == severity_key]
                if group:
                    icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity_key, "⚪")
                    lines.extend(["", f"### {icon} {severity_label}"])
                    for i, finding in enumerate(group, 1):
                        lines.append(f"\n{i}. **{finding['title']}**")
                        lines.append(f"   - Detail: {finding['detail']}")
                        for ev in finding.get("evidence", [])[:2]:
                            lines.append(f"   - `{ev}`")

        # Verdict
        lines.extend(["", "### Verdict"])
        if self._critical_count > 0:
            lines.append("REJECT — Critical issues must be fixed")
        elif self._warning_count > 3:
            lines.append("REQUEST_CHANGES — Multiple warnings should be addressed")
        else:
            lines.append("APPROVE — Code meets quality standards")

        return "\n".join(lines)

    @staticmethod
    def _extract_arg(command_line: str, arg_name: str) -> Optional[str]:
        """Extract a key=value argument from a command line."""
        pattern = rf'{arg_name}=["\']([^"\']*)["\']|{arg_name}=([^\s,)]+)'
        m = re.search(pattern, command_line)
        if m:
            return m.group(1) or m.group(2)
        return None
