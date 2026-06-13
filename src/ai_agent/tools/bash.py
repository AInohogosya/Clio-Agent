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
# SECURITY: Patterns use word-boundary anchors and cover multiple disk device
# naming schemes (sd, hd, vd, nvme, xvd) to prevent bypass via alternate names.
_BLOCKED_PATTERNS = [
    # rm -rf /, rm -rf //, rm -rf /*, rm -rf ~, rm -rf $HOME
    re.compile(r"\brm\s+-[rfRF]+(\s+-[rfRF]+)*\s+(/?/\s*|/?/\*|~|\$HOME|\$\{HOME\})"),
    re.compile(r"\brm\s+-[rfRF]+(\s+-[rfRF]+)*\s+/?/\s*$"),
    # dd destroy: dd if=/dev/(zero|random|urandom) of=/dev/(sd|hd|vd|nvme|xvd)
    re.compile(
        r"\bdd\s+.*if=/dev/(zero|random|urandom)\s+.*of=/dev/"
        r"(sd[a-z]+|hd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|xvd[a-z]+)"
    ),
    # Direct disk overwrite
    re.compile(r">\s*/dev/(sd[a-z]+|hd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|xvd[a-z]+)"),
    # Fork bomb variants
    re.compile(r"\(\s*\)\s*\{[^}]*\|[^}]*&[^}]*\}", re.IGNORECASE),
    # mkfs on block devices
    re.compile(
        r"\bmkfs\.[a-z]+\s+/dev/"
        r"(sd[a-z]+|hd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|xvd[a-z]+)"
    ),
    # Move root to /dev/null or /dev/zero
    re.compile(r"\bmv\s+(?:/?|\./|\.\.)\s*/(?:dev/(null|zero)|\.{0,2}$)"),
    # chmod 777 on root or critical system dirs
    re.compile(
        r"\bchmod\s+(-[R]+\s+)?777\s+"
        r"(?:/($|\s)|/(?:etc|usr|bin|sbin|lib|var)(?:/|$))"
    ),
    # shred on block devices
    re.compile(
        r"\bshred\s+(-[vnz]+\s+)*/dev/"
        r"(sd[a-z]+|hd[a-z]+|vd[a-z]+|nvme[0-9]+n[0-9]+|xvd[a-z]+)"
    ),
]


def _is_command_blocked(command: str) -> Optional[str]:
    """Return reason string if command matches a blocked pattern."""
    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command):
            return "Matched blocked pattern: %s" % pattern.pattern
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
                    message="Working directory does not exist: %s" % cwd,
                ),
                tool_name=self.name,
            )

        timeout = max(0.1, input.timeout)

        proc = None
        try:
            proc = subprocess.Popen(
                input.command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
            )
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if proc is not None:
                proc.kill()
                proc.wait()
            raise CommandTimeoutToolError(input.command, timeout)

        stdout = stdout_bytes.decode("utf-8", errors="replace") if isinstance(stdout_bytes, bytes) else (stdout_bytes or "")
        stderr = stderr_bytes.decode("utf-8", errors="replace") if isinstance(stderr_bytes, bytes) else (stderr_bytes or "")
        exit_code = proc.returncode

        output_parts = []
        if stdout:
            output_parts.append(stdout)
        if stderr:
            output_parts.append("\n[stderr]\n%s" % stderr)
        output_parts.append("\n[exit code: %d]" % exit_code)
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
