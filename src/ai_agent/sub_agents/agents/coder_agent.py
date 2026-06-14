"""
CoderAgent — specialized sub-agent for coding tasks.

Implements code changes, bug fixes, feature additions, and test execution.
Uses the model runner for complex reasoning about code structure.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from ..base import SubAgentBase
from ..context import SubAgentContext
from ..registry import sub_agent
from ..prompts import CODER_SYSTEM_PROMPT, CODER_TASK_PROMPT
from ...utils.logger import get_logger

logger = get_logger("sub_agent.coder")


@sub_agent("coder", description="Implements code changes, fixes bugs, writes tests")
class CoderAgent(SubAgentBase):
    """
    Specialized sub-agent for coding tasks.

    Capabilities:
    - Read, write, and edit files
    - Run shell commands (tests, linters, build)
    - Search codebase with glob/grep
    - Multi-step implementation with verification
    """

    agent_type = "coder"

    def __init__(self, context: SubAgentContext) -> None:
        super().__init__(context)
        self._model_runner = context.model_runner
        self._cwd = context.working_directory

    def initialize(self) -> None:
        super().initialize()
        self._iteration = 0
        self._changes_made: List[Dict[str, str]] = []
        self._verification_results: List[str] = []

    def _run(self) -> str:
        """
        Execute the coding task using an autonomous loop.

        If a model runner is available, uses LLM reasoning.
        Otherwise, falls back to a structured task completion.
        """
        self._iteration = 0
        max_iter = self.context.max_iterations

        while self._iteration < max_iter:
            self._iteration += 1

            # Build the prompt
            prompt = self._build_task_prompt()
            system_prompt = CODER_SYSTEM_PROMPT

            # Call model if available
            if self._model_runner is not None:
                try:
                    from ...external_integration.model_runner import ModelRequest, TaskType
                    request = ModelRequest(
                        task_type=TaskType.AUTONOMOUS_LOOP,
                        prompt=prompt,
                        max_tokens=2048,
                        temperature=0.3,
                    )
                    response = self._model_runner.run_model(request)
                    if response.success and response.content:
                        # Parse commands from response and execute them
                        self._execute_model_output(response.content)
                    else:
                        error = response.error or "Model returned no content"
                        logger.warning(f"Model call failed: {error}")
                        self._verification_results.append(f"Model error: {error}")
                        break
                except Exception as e:
                    logger.error(f"Model execution error: {e}")
                    self._verification_results.append(f"Error: {e}")
                    break
            else:
                # No model runner — return task description for parent to handle
                return self._build_report(
                    output=f"Task received: {self.context.task}",
                    changes=[],
                    verification=["No model runner available — task queued for parent agent"],
                )

            # Check if task is complete (model emitted a report or specific marker)
            if self._is_complete():
                break

        return self._build_report(
            output=self._summarize_output(),
            changes=self._changes_made,
            verification=self._verification_results,
        )

    def _build_task_prompt(self) -> str:
        """Build the task prompt for the model."""
        ctx = self.context.build_prompt_context()
        history = self._format_execution_history()
        return (
            f"{CODER_TASK_PROMPT.format(**ctx)}\n\n"
            f"### Execution History (iteration {self._iteration})\n"
            f"{history}\n\n"
            f"Continue working on the task. When done, output a structured report."
        )

    def _format_execution_history(self) -> str:
        """Format recent execution history for the model prompt."""
        artifacts = self.context.artifacts
        history_lines = []

        if "commands" in artifacts:
            for cmd_info in artifacts["commands"][-10:]:
                history_lines.append(
                    f"  $ {cmd_info.get('command', '?')} → "
                    f"{'OK' if cmd_info.get('success') else 'FAIL'}"
                )

        if "files_modified" in artifacts:
            for f in artifacts.get("files_modified", [])[-10:]:
                history_lines.append(f"  [modified] {f}")

        return "\n".join(history_lines) if history_lines else "(no actions yet)"

    def _execute_model_output(self, output: str) -> None:
        """Parse and execute commands from model output."""
        import re
        import json

        # Extract commands from the output
        # Look for read/write/edit/glob/grep/bash commands
        lines = output.strip().splitlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("```"):
                continue

            # Direct tool calls
            if line.startswith("read(") or line.startswith("bash(") or \
               line.startswith("glob(") or line.startswith("grep("):
                self._execute_tool_call(line)
            elif line.startswith("write(") or line.startswith("edit("):
                self._execute_tool_call(line)

    def _execute_tool_call(self, command_line: str) -> None:
        """Execute a single tool call and record the result."""
        try:
            # Parse the tool call
            if command_line.startswith("read("):
                path = self._extract_arg(command_line, "path")
                if path:
                    full_path = os.path.join(self._cwd, path)
                    if os.path.exists(full_path):
                        with open(full_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        self.context.append_artifact_list("files_read", path)
                        self.context.artifacts[f"content_{path}"] = content
                        self.context.append_artifact_list("commands", {
                            "command": f"read({path})",
                            "success": True,
                        })
                    else:
                        self.context.append_artifact_list("commands", {
                            "command": f"read({path})",
                            "success": False,
                        })

            elif command_line.startswith("write("):
                path = self._extract_arg(command_line, "path")
                content = self._extract_arg(command_line, "content")
                if path:
                    full_path = os.path.join(self._cwd, path)
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(content or "")
                    self.context.append_artifact_list("files_modified", path)
                    self.context.append_artifact_list("commands", {
                        "command": f"write({path})",
                        "success": True,
                    })

            elif command_line.startswith("edit("):
                path = self._extract_arg(command_line, "path")
                old = self._extract_arg(command_line, "old_string") or self._extract_arg(command_line, "old")
                new = self._extract_arg(command_line, "new_string") or self._extract_arg(command_line, "new")
                if path and old and new:
                    full_path = os.path.join(self._cwd, path)
                    if os.path.exists(full_path):
                        with open(full_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        if old in content:
                            content = content.replace(old, new, 1)
                            with open(full_path, "w", encoding="utf-8") as f:
                                f.write(content)
                            self.context.append_artifact_list("files_modified", path)
                            self._changes_made.append({
                                "file": path,
                                "action": "edit",
                                "description": f"Replaced text in {path}",
                            })
                        self.context.append_artifact_list("commands", {
                            "command": f"edit({path})",
                            "success": True,
                        })

            elif command_line.startswith("bash("):
                cmd = self._extract_arg(command_line, "command")
                if cmd:
                    import subprocess
                    result = subprocess.run(
                        cmd, shell=True, capture_output=True, text=True,
                        timeout=60, cwd=self._cwd,
                    )
                    self.context.append_artifact_list("commands", {
                        "command": cmd,
                        "success": result.returncode == 0,
                    })
                    if result.stdout:
                        self.context.artifacts[f"bash_output_{cmd[:30]}"] = result.stdout[:500]

            elif command_line.startswith("glob("):
                pattern = self._extract_arg(command_line, "pattern")
                if pattern:
                    import glob as glob_mod
                    files = glob_mod.glob(pattern, root_dir=self._cwd, recursive=True)
                    self.context.artifacts[f"glob_{pattern}"] = files

            elif command_line.startswith("grep("):
                pattern = self._extract_arg(command_line, "pattern")
                path = self._extract_arg(command_line, "path") or "."
                if pattern:
                    import re as re_mod
                    matches = []
                    full_path = os.path.join(self._cwd, path)
                    if os.path.exists(full_path):
                        for root, _dirs, files in os.walk(full_path):
                            for fname in files:
                                fpath = os.path.join(root, fname)
                                try:
                                    with open(fpath, "r", encoding="utf-8") as f:
                                        for i, line in enumerate(f, 1):
                                            if re_mod.search(pattern, line):
                                                matches.append(f"{fpath}:{i}: {line.strip()}")
                                except (UnicodeDecodeError, PermissionError):
                                    pass
                    self.context.artifacts[f"grep_{pattern}"] = matches[:50]

        except Exception as e:
            logger.warning(f"Tool call execution error: {e}")
            self.context.append_artifact_list("commands", {
                "command": command_line[:50],
                "success": False,
            })

    @staticmethod
    def _extract_arg(command_line: str, arg_name: str) -> Optional[str]:
        """Extract a key=value argument from a command line."""
        import re
        # Match key="value" or key='value' or key=value
        pattern = rf'{arg_name}=["\']([^"\']*)["\']|{arg_name}=([^\s,)]+)'
        m = re.search(pattern, command_line)
        if m:
            return m.group(1) or m.group(2)
        return None

    def _is_complete(self) -> bool:
        """Check if the task appears complete based on artifacts."""
        # If we've made changes and run verification, we're done
        if self._changes_made and self._verification_results:
            return True
        # If we've exceeded half the iterations with changes, wrap up
        if self._changes_made and self._iteration >= self.context.max_iterations // 2:
            return True
        return False

    def _summarize_output(self) -> str:
        """Summarize the coding session."""
        if self._changes_made:
            return f"Completed {len(self._changes_made)} change(s) in {self._iteration} iteration(s)."
        return f"Analyzed task in {self._iteration} iteration(s). No changes made."

    def _build_report(self, output: str, changes: List, verification: List) -> str:
        """Build the structured report string."""
        lines = [
            "## Coder Agent Report",
            "",
            f"### Task",
            self.context.task,
            "",
            f"### Changes Made",
        ]
        if changes:
            for c in changes:
                lines.append(f"- {c.get('file', '?')}: {c.get('description', c.get('action', 'modified'))}")
        else:
            lines.append("- No changes made")

        lines.extend(["", "### Verification"])
        if verification:
            for v in verification:
                lines.append(f"- {v}")
        else:
            lines.append("- No verification performed")

        return "\n".join(lines)
