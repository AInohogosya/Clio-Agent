"""
CodingAgent - specialized sub-agent for all software development tasks.

The most capable coding entity in the Clio Agent system. Handles:
- Writing new code (features, modules, scripts, tests)
- Editing and modifying existing code
- Debugging and fixing bugs
- Refactoring and code improvement
- Writing and running tests
- Build verification and error resolution

This agent has full read/write/edit/bash access and operates iteratively:
explore -> plan -> implement -> test -> verify -> report.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, List, Optional

from ..base import SubAgentBase
from ..context import SubAgentContext
from ..registry import sub_agent
from ..prompts import CODING_SYSTEM_PROMPT, CODING_TASK_PROMPT
from ...utils.logger import get_logger

logger = get_logger("sub_agent.coding")

@sub_agent("coding", description="Writes, edits, debugs, and refactors code. Handles all software development tasks including new features, bug fixes, testing, and refactoring. Full read/write/edit access.")
class CodingAgent(SubAgentBase):
    """
    Specialized sub-agent for all coding and software development tasks.

    Capabilities:
    - Read, write, and edit files
    - Search and explore codebase (grep, glob, bash)
    - Write new features, modules, and scripts
    - Fix bugs and debug issues
    - Refactor existing code
    - Write and run tests
    - Verify builds and resolve compilation errors
    - Multi-file coordinated changes

    This agent is the PRIMARY coding entity - it surpasses the main agent
    in all software development tasks due to its focused expertise and
    full filesystem access.
    """

    agent_type = "coding"

    def __init__(self, context: SubAgentContext) -> None:
        super().__init__(context)
        self._model_runner = context.model_runner
        self._cwd = context.working_directory
        self._files_changed: List[Dict[str, str]] = []
        self._test_results: List[Dict[str, str]] = []
        self._errors_encountered: List[str] = []
        self._build_commands: List[str] = []

    def initialize(self) -> None:
        super().initialize()
        self._iteration = 0
        self._files_changed = []
        self._test_results = []
        self._errors_encountered = []
        self._build_commands = []
        self._determine_build_commands()

    def _determine_build_commands(self) -> None:
        """Detect the project's build/test commands from the working directory."""
        if os.path.isfile(os.path.join(self._cwd, "pyproject.toml")):
            self._build_commands = ["python -m pytest --tb=short -q"]
        elif os.path.isfile(os.path.join(self._cwd, "setup.py")):
            self._build_commands = ["python -m pytest --tb=short -q"]
        elif os.path.isfile(os.path.join(self._cwd, "requirements.txt")):
            self._build_commands = ["python -m pytest --tb=short -q"]
        elif os.path.isfile(os.path.join(self._cwd, "package.json")):
            self._build_commands = ["npm test", "npx tsc --noEmit"]
        elif os.path.isfile(os.path.join(self._cwd, "Cargo.toml")):
            self._build_commands = ["cargo test", "cargo check"]
        elif os.path.isfile(os.path.join(self._cwd, "go.mod")):
            self._build_commands = ["go test ./...", "go build ./..."]
        elif os.path.isfile(os.path.join(self._cwd, "pom.xml")):
            self._build_commands = ["mvn test -q"]
        elif os.path.isfile(os.path.join(self._cwd, "build.gradle")):
            self._build_commands = ["gradle test -q"]
        else:
            self._build_commands = []

    def _run(self) -> str:
        """Execute the coding task."""
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
                        max_tokens=4096,
                        temperature=0.3,
                    )
                    response = self._model_runner.run_model(request)
                    if response.success and response.content:
                        self._execute_coding_output(response.content)
                    else:
                        error = response.error or "Model returned no content"
                        logger.warning(f"Model call failed: {error}")
                        self._errors_encountered.append(f"Model error: {error}")
                        break
                except Exception as e:
                    logger.error(f"Model execution error: {e}")
                    self._errors_encountered.append(f"Exception: {e}")
                    break
            else:
                return self._fallback_coding()

            if self._iteration >= 3 and self._files_changed:
                self._verify_build()
                if not self._errors_encountered:
                    break

        return self._build_report()

    def _build_task_prompt(self) -> str:
        """Build the task prompt for the model."""
        ctx = self.context.build_prompt_context()
        history = self._format_progress()
        files_changed_str = self._format_files_changed()
        errors_str = "\n".join(f"- {e}" for e in self._errors_encountered[-5:]) or "(none)"

        return (
            f"{CODING_TASK_PROMPT.format(**ctx)}\n\n"
            f"### Progress (iteration {self._iteration})\n"
            f"{history}\n\n"
            f"### Files Changed So Far\n"
            f"{files_changed_str}\n\n"
            f"### Errors Encountered\n"
            f"{errors_str}\n\n"
            f"Continue working on the task. If the task is complete and all "
            f"tests pass, output a final summary and stop."
        )

    def _format_progress(self) -> str:
        """Format recent execution progress."""
        if not self._files_changed and not self._errors_encountered:
            return "(just started)"
        parts = []
        if self._files_changed:
            parts.append(f"Changed {len(self._files_changed)} file(s)")
        if self._errors_encountered:
            parts.append(f"Encountered {len(self._errors_encountered)} error(s)")
        if self._test_results:
            passed = sum(1 for t in self._test_results if t.get("status") == "passed")
            parts.append(f"Tests: {passed}/{len(self._test_results)} passed")
        return ", ".join(parts)

    def _format_files_changed(self) -> str:
        """Format the list of files changed."""
        if not self._files_changed:
            return "(no files changed yet)"
        return "\n".join(
            f"- `{f['path']}` - {f['action']}"
            for f in self._files_changed
        )

    def _execute_coding_output(self, output: str) -> None:
        """Parse and execute the LLM's coding output."""
        for m in re.finditer(r'read\(path="([^"]+)"\)', output):
            self._safe_read(m.group(1))
        for m in re.finditer(r'write\(path="([^"]+)",\s*content="([\s\S]*?)"\)', output):
            self._safe_write(m.group(1), m.group(2))
        for m in re.finditer(r'edit\(path="([^"]+)",\s*old_string="([\s\S]*?)",\s*new_string="([\s\S]*?)"\)', output):
            self._safe_edit(m.group(1), m.group(2), m.group(3))
        for m in re.finditer(r'bash\(command="([^"]+)"\)', output):
            self._safe_bash(m.group(1))

    def _safe_read(self, path: str) -> Optional[str]:
        """Safely read a file."""
        try:
            if not os.path.isabs(path):
                path = os.path.join(self._cwd, path)
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"Read failed for {path}: {e}")
            return None

    def _safe_write(self, path: str, content: str) -> bool:
        """Safely write a file, tracking the change."""
        try:
            if not os.path.isabs(path):
                path = os.path.join(self._cwd, path)
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            action = "modified" if os.path.exists(path) else "created"
            rel_path = os.path.relpath(path, self._cwd)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._files_changed.append({"path": rel_path, "action": action})
            logger.info(f"File {action}: {rel_path}")
            return True
        except Exception as e:
            logger.error(f"Write failed for {path}: {e}")
            self._errors_encountered.append(f"Write failed for {path}: {e}")
            return False

    def _safe_edit(self, path: str, old_string: str, new_string: str) -> bool:
        """Safely edit a file with find-and-replace."""
        try:
            if not os.path.isabs(path):
                path = os.path.join(self._cwd, path)
            rel_path = os.path.relpath(path, self._cwd)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if old_string not in content:
                logger.warning(f"old_string not found in {rel_path}")
                self._errors_encountered.append(
                    f"Edit failed: old_string not found in {rel_path}"
                )
                return False
            new_content = content.replace(old_string, new_string, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            self._files_changed.append({"path": rel_path, "action": "edited"})
            logger.info(f"File edited: {rel_path}")
            return True
        except Exception as e:
            logger.error(f"Edit failed for {path}: {e}")
            self._errors_encountered.append(f"Edit failed for {path}: {e}")
            return False

    def _safe_bash(self, command: str) -> Optional[str]:
        """Safely execute a bash command."""
        try:
            result = subprocess.run(
                command, shell=True, cwd=self._cwd,
                capture_output=True, text=True, timeout=120,
            )
            output = result.stdout + result.stderr
            if result.returncode != 0:
                self._errors_encountered.append(
                    f"Command failed (exit {result.returncode}): {command}\n{output[:500]}"
                )
            logger.info(f"Command executed: {command} (exit {result.returncode})")
            return output
        except subprocess.TimeoutExpired:
            self._errors_encountered.append(f"Command timed out: {command}")
            return None
        except Exception as e:
            self._errors_encountered.append(f"Command error: {command} - {e}")
            return None

    def _verify_build(self) -> None:
        """Run build/test commands to verify the code works."""
        for cmd in self._build_commands:
            logger.info(f"Running verification: {cmd}")
            output = self._safe_bash(cmd)
            if output is not None:
                has_failure = "failed" in output.lower() or "error" in output.lower()
                passed = not has_failure
                self._test_results.append({
                    "command": cmd,
                    "status": "passed" if passed else "failed",
                    "output": output[:1000],
                })
                if not passed:
                    self._errors_encountered.append(
                        f"Verification failed: {cmd}\n{output[:500]}"
                    )

    def _fallback_coding(self) -> str:
        """Fallback coding when no model runner is available."""
        return (
            "## Coding Agent Report\n\n"
            "### Status\n"
            "FAILED - No model runner available for the Coding Agent.\n\n"
            "### Task\n"
            f"{self.context.task}\n\n"
            "### Recommendation\n"
            "Ensure the model runner is properly configured in the sub-agent context.\n"
        )

    def _build_report(self) -> str:
        """Build the structured coding report."""
        if self._errors_encountered:
            status = "FAILED" if not self._files_changed else "PARTIAL"
        elif self._files_changed:
            status = "SUCCESS"
        else:
            status = "NO CHANGES"

        lines = [
            "## Coding Agent Report",
            "",
            "### Task",
            self.context.task,
            "",
            "### Status",
            f"**{status}**",
            "",
            f"### Files Changed ({len(self._files_changed)})",
        ]

        if self._files_changed:
            for f in self._files_changed:
                lines.append(f"- `{f['path']}` - {f['action']}")
        else:
            lines.append("- No files were changed")

        lines.extend(["", f"### Test Results ({len(self._test_results)})"])
        if self._test_results:
            for t in self._test_results:
                icon = "PASS" if t["status"] == "passed" else "FAIL"
                lines.append(f"- [{icon}] `{t['command']}` - {t['status']}")
        else:
            lines.append("- No test commands were run")

        lines.extend(["", f"### Errors ({len(self._errors_encountered)})"])
        if self._errors_encountered:
            for e in self._errors_encountered:
                lines.append(f"- {e}")
        else:
            lines.append("- No errors encountered")

        lines.extend([
            "",
            "### Summary",
            f"Completed in {self._iteration} iteration(s). "
            f"Changed {len(self._files_changed)} file(s). "
            f"Encountered {len(self._errors_encountered)} error(s).",
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
