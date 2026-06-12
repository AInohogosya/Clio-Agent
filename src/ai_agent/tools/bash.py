"""
BashTool — execute shell commands with timeout, working directory,
and basic dangerous-command blocking.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .base import Permission, ToolInput, ToolResult, ToolExecutor
from .exceptions import (
    ToolError,
    ToolErrorCode,
    CommandBlockedToolError,
    CommandTimeoutToolError,
    CommandFailedToolError,
)


@dataclass
class BashInput(ToolInput):
    command: str = ""
    cwd: Optional[str] = None
    timeout: float = 60.0


# Dangerous patterns that are always blocked (not configurable — these are
# destructive operations that no agent should perform without explicit user
# consent at the OS level).
_BLOCKED_PATTERNS = [
    re.compile(r"\brm\s+(-[rfRF]+\s+)+/?(\s|$)"),            # rm -rf /
    re.compile(r"\brm\s+(-[rfRF]+\s+)+\*"),                    # rm -rf /*
    re.compile(r"\brm\s+(-[rfRF]+\s+)+~"),                     # rm -rf ~
    re.compile(r"\bdd\s+if=/dev/(zero|random)\s+of=/dev/"),   # dd destroy
    re.compile(r">\s*/dev/sd"),                                 # overwrite disk
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*"),  # fork bomb
    re.compile(r"\bmkfs\.[a-z]+\s+/dev/"),                    # format disk device
    re.compile(r"\bmv\s+/\s+/dev/null"),                        # mv / /dev/null
]


def _is_command_blocked(command: str) -> Optional[str]:
    """Return reason string if command matches a blocked pattern."""
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command):
            return f"Matched blocked pattern: {pattern.pattern}"
    return None


class BashTool(ToolExecutor):
    name = "bash"
    description = "Execute shell commands with timeout and safety checks."
    required_permission = Permission.EXECUTE

    def _execute(self, input: BashInput) -> ToolResult:
        if not input.command.strip():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.COMMAND_BLOCKED,
                    message="Empty command",
                ),
                tool_name=self.name,
            )

        block_reason = _is_command_blocked(input.command)
        if block_reason:
            raise CommandBlockedToolError(input.command, block_reason)

        cwd = Path(input.cwd).resolve() if input.cwd else None
        if cwd and not cwd.is_dir():
            return ToolResult.fail(
                error=ToolError(
                    code=ToolErrorCode.COMMAND_FAILED,
                    message=f"Working directory does not exist: {cwd}",
                ),
                tool_name=self.name,
            )

        timeout = max(0.1, input.timeout)

        try:
            proc = subprocess.run(
                input.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
            )
        except subprocess.TimeoutExpired:
            raise CommandTimeoutToolError(input.command, timeout)

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode

        output_parts = []
        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append(f"\n[stderr]\n{stderr}")
        output_parts.append(f"\n[exit code: {exit_code}]")
        output = "".join(output_parts).strip()

        if exit_code != 0:
            raise CommandFailedToolError(input.command, exit_code, stderr)

        return ToolResult.ok(
            output=output,
            tool_name=self.name,
            metadata={
                "command": input.command,
                "exit_code": exit_code,
                "cwd": str(cwd) if cwd else ".",
            },
        )
