"""
ResearchAgent — specialized sub-agent for investigation tasks.

Explores codebase, analyzes architecture, traces dependencies,
and produces structured findings. Read-only by default.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from ..base import SubAgentBase
from ..context import SubAgentContext
from ..registry import sub_agent
from ..prompts import RESEARCH_SYSTEM_PROMPT, RESEARCH_TASK_PROMPT
from ...utils.logger import get_logger

logger = get_logger("sub_agent.research")


@sub_agent("research", description="Investigates codebase, analyzes architecture, traces dependencies")
class ResearchAgent(SubAgentBase):
    """
    Specialized sub-agent for research and investigation tasks.

    Capabilities:
    - Read and analyze files
    - Search patterns with grep/glob
    - Trace dependencies across modules
    - Run analysis commands (git, find, etc.)
    - Produce structured findings

    Operates in read-only mode — does not write or edit files.
    """

    agent_type = "research"

    def __init__(self, context: SubAgentContext) -> None:
        super().__init__(context)
        self._model_runner = context.model_runner
        self._cwd = context.working_directory
        self._findings: List[Dict[str, str]] = []
        self._files_examined: List[str] = []

    def initialize(self) -> None:
        super().initialize()
        self._iteration = 0
        self._findings = []
        self._files_examined = []

    def _run(self) -> str:
        """Execute the research task."""
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
                        self._execute_research_output(response.content)
                    else:
                        error = response.error or "Model returned no content"
                        logger.warning(f"Model call failed: {error}")
                        break
                except Exception as e:
                    logger.error(f"Model execution error: {e}")
                    break
            else:
                # Fallback: do independent research
                return self._fallback_research()

            # Check if we have enough findings
            if len(self._findings) >= 3 and self._iteration >= 5:
                break

        return self._build_report()

    def _build_task_prompt(self) -> str:
        """Build the task prompt for the model."""
        ctx = self.context.build_prompt_context()
        history = self._format_progress()
        return (
            f"{RESEARCH_TASK_PROMPT.format(**ctx)}\n\n"
            f"### Progress (iteration {self._iteration})\n"
            f"{history}\n\n"
            f"Continue investigating. Output findings as you discover them."
        )

    def _format_progress(self) -> str:
        """Format research progress for the model."""
        lines = []
        if self._findings:
            lines.append("Findings so far:")
            for f in self._findings[-5:]:
                lines.append(f"  - {f['title']}: {f.get('detail', '')[:100]}")
        if self._files_examined:
            lines.append(f"Files examined: {len(self._files_examined)}")
        return "\n".join(lines) if lines else "(starting research)"

    def _execute_research_output(self, output: str) -> None:
        """Parse and execute research commands from model output."""
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
        """Execute a read command."""
        path = self._extract_arg(command_line, "path")
        if not path:
            return
        full_path = os.path.join(self._cwd, path)
        if os.path.exists(full_path):
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._files_examined.append(path)
                self.context.artifacts[f"content_{path}"] = content[:3000]
                # Auto-extract key findings from the file
                self._auto_extract_findings(path, content)
            except Exception as e:
                logger.warning(f"Could not read {path}: {e}")

    def _do_grep(self, command_line: str) -> None:
        """Execute a grep command."""
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

        artifact_key = f"grep_{pattern}_{path}"
        self.context.artifacts[artifact_key] = matches[:50]
        if matches:
            self._findings.append({
                "title": f"Grep: {pattern}",
                "detail": f"Found {len(matches)} matches in {path}",
                "evidence": matches[:5],
            })

    def _do_glob(self, command_line: str) -> None:
        """Execute a glob command."""
        pattern = self._extract_arg(command_line, "pattern")
        if not pattern:
            return
        import glob as glob_mod
        files = glob_mod.glob(pattern, root_dir=self._cwd, recursive=True)
        self.context.artifacts[f"glob_{pattern}"] = files
        if files:
            self._findings.append({
                "title": f"Glob: {pattern}",
                "detail": f"Found {len(files)} files matching {pattern}",
                "evidence": files[:10],
            })

    def _do_bash(self, command_line: str) -> None:
        """Execute a bash command for analysis."""
        cmd = self._extract_arg(command_line, "command")
        if not cmd:
            return
        import subprocess
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=self._cwd,
            )
            artifact_key = f"bash_{cmd[:30]}"
            self.context.artifacts[artifact_key] = result.stdout[:1000]
            if result.stdout.strip():
                self._findings.append({
                    "title": f"Command: {cmd}",
                    "detail": result.stdout[:500],
                })
        except subprocess.TimeoutExpired:
            logger.warning(f"Bash command timed out: {cmd}")
        except Exception as e:
            logger.warning(f"Bash command failed: {e}")

    def _auto_extract_findings(self, path: str, content: str) -> None:
        """Auto-extract key findings from file content."""
        # Detect class definitions
        for m in re.finditer(r'^class\s+(\w+)', content, re.MULTILINE):
            self._findings.append({
                "title": f"Class: {m.group(1)}",
                "detail": f"Found in {path}:{content[:m.start()].count(chr(10)) + 1}",
            })

        # Detect function definitions
        for m in re.finditer(r'^(?:    )?def\s+(\w+)', content, re.MULTILINE):
            if len(self._findings) < 20:  # Cap auto-extracted findings
                self._findings.append({
                    "title": f"Function: {m.group(1)}",
                    "detail": f"Found in {path}:{content[:m.start()].count(chr(10)) + 1}",
                })

        # Detect TODO/FIXME markers
        for m in re.finditer(r'(?:TODO|FIXME|HACK|XXX)[:\s]*(.+?)$', content, re.MULTILINE):
            self._findings.append({
                "title": "TODO/FIXME found",
                "detail": f"{path}: {m.group(1).strip()}",
            })

    def _fallback_research(self) -> str:
        """Fallback research when no model runner is available."""
        # Explore the working directory structure
        for root, dirs, files in os.walk(self._cwd):
            # Skip common non-relevant directories
            dirs[:] = [
                d for d in dirs
                if d not in {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist'}
            ]
            for fname in files:
                rel_path = os.path.relpath(os.path.join(root, fname), self._cwd)
                self._files_examined.append(rel_path)

        self._findings.append({
            "title": "Directory Structure",
            "detail": f"Found {len(self._files_examined)} files in project",
            "evidence": self._files_examined[:20],
        })

        return self._build_report()

    def _build_report(self) -> str:
        """Build the structured research report."""
        lines = [
            "## Research Agent Report",
            "",
            f"### Question",
            self.context.task,
            "",
            f"### Findings ({len(self._findings)})",
        ]

        for i, finding in enumerate(self._findings, 1):
            lines.append(f"\n{i}. **{finding['title']}**")
            lines.append(f"   - Detail: {finding.get('detail', 'N/A')}")
            if "evidence" in finding:
                for ev in finding["evidence"][:3]:
                    lines.append(f"   - `{ev}`")

        lines.extend([
            "",
            "### Summary",
            f"Examined {len(self._files_examined)} files in "
            f"{self._iteration} iteration(s). "
            f"Found {len(self._findings)} relevant finding(s).",
            "",
            "### Open Questions",
            "None" if len(self._findings) >= 3 else "Investigation may need more iterations.",
        ])

        return "\n".join(lines)

    @staticmethod
    def _extract_arg(command_line: str, arg_name: str) -> Optional[str]:
        """Extract a key=value argument from a command line."""
        pattern = rf'{arg_name}=["\']([^"\']*)["\']|{arg_name}=([^\s,)]+)'
        m = re.search(pattern, command_line)
        if m:
            return m.group(1) or m.group(2)
        return None
