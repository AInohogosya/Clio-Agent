"""Shell execution with pexpect persistent sessions and output truncation."""

from __future__ import annotations

import asyncio
import re
import shlex
import time
from typing import Any

import pexpect
import structlog

from neuro_scaffold.config.settings import Settings

logger = structlog.get_logger(__name__)


class OutputTruncator:
    """Truncates large outputs, preserving head and tail."""

    def __init__(self, head_lines: int = 50, tail_lines: int = 50) -> None:
        self._head = head_lines
        self._tail = tail_lines

    def truncate(self, output: str) -> tuple[str, bool]:
        """Truncate output if it exceeds head + tail lines.

        Returns (possibly_truncated_output, was_truncated).
        """
        lines = output.splitlines()
        max_lines = self._head + self._tail
        if len(lines) <= max_lines:
            return output, False

        head = lines[:self._head]
        tail = lines[-self._tail:]
        omitted = len(lines) - self._head - self._tail
        marker = f"\n... [{omitted} lines omitted] ...\n"
        return "\n".join(head) + marker + "\n".join(tail), True

    def truncate_bytes(self, data: bytes, encoding: str = "utf-8") -> tuple[str, bool]:
        try:
            text = data.decode(encoding, errors="replace")
        except (UnicodeDecodeError, LookupError):
            text = data.decode("ascii", errors="replace")
        return self.truncate(text)


class ShellExecutor:
    """Execute shell commands with timeout and output truncation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._truncator = OutputTruncator(
            head_lines=settings.shell_truncate_head,
            tail_lines=settings.shell_truncate_tail,
        )

    def _is_dangerous(self, command: str) -> tuple[bool, str]:
        """Check if a command is potentially destructive."""
        dangerous_patterns = [
            r"\brm\s+(-[rfRF]+\s+)?/",
            r"\brm\s+-rf\s+/",
            r"\bdd\s+if=",
            r":\(\)\s*{\s*:\|:&\s*};\s*:",
            r"\bmkfs\b",
            r"\bfdisk\b",
            r"\bshred\b",
            r"\bchmod\s+-R\s+777\s+/",
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, command):
                return True, f"Dangerous pattern detected: {pattern}"
        return False, ""

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Execute a shell command asynchronously."""
        timeout = timeout or float(self._settings.shell_timeout_seconds)

        if dry_run:
            is_dangerous, reason = self._is_dangerous(command)
            warning = ""
            if is_dangerous:
                warning = f" [WARNING: {reason}]"
            return {
                "success": True,
                "output": f"[DRY RUN] {command}{warning}",
                "error": None,
                "exit_code": 0,
                "truncated": False,
                "duration_ms": 0,
            }

        is_dangerous, reason = self._is_dangerous(command)
        if is_dangerous:
            logger.warning("Blocked dangerous command", command=command[:100], reason=reason)
            return {
                "success": False,
                "output": "",
                "error": f"Command blocked for safety: {reason}",
                "exit_code": -1,
                "truncated": False,
                "duration_ms": 0,
            }

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                limit=1024 * 1024,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            duration_ms = (time.monotonic() - start) * 1000

            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")

            combined = stdout_text
            if stderr_text:
                combined += f"\n[STDERR]\n{stderr_text}"

            truncated_output, was_truncated = self._truncator.truncate(combined)

            return {
                "success": proc.returncode == 0,
                "output": truncated_output,
                "error": None if proc.returncode == 0 else f"Exit code: {proc.returncode}",
                "exit_code": proc.returncode,
                "truncated": was_truncated,
                "duration_ms": duration_ms,
            }
        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start) * 1000
            logger.warning("Command timed out", command=command[:100], timeout=timeout)
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            return {
                "success": False,
                "output": "",
                "error": f"Command timed out after {timeout}s",
                "exit_code": -1,
                "truncated": False,
                "duration_ms": duration_ms,
            }
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception("Command execution failed", command=command[:100])
            return {
                "success": False,
                "output": "",
                "error": str(exc),
                "exit_code": -1,
                "truncated": False,
                "duration_ms": duration_ms,
            }


class PersistentShell:
    """Persistent shell session using pexpect for stateful command execution."""

    def __init__(
        self,
        session_id: str,
        shell: str = "/bin/sh",
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
        prompt: str = "NEURO_SHELL_PROMPT>",
        ttl_seconds: int = 1800,
    ) -> None:
        self._session_id = session_id
        self._shell = shell
        self._cwd = cwd
        self._env = env or {}
        self._prompt = prompt
        self._ttl = ttl_seconds
        self._child: pexpect.spawn | None = None
        self._created_at = time.monotonic()
        self._last_used = time.monotonic()
        self._command_count = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def is_alive(self) -> bool:
        return self._child is not None and self._child.isalive()

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self._last_used) > self._ttl

    @property
    def command_count(self) -> int:
        return self._command_count

    def start(self) -> None:
        """Start the persistent shell session."""
        if self.is_alive:
            return
        env = {**self._env, "PS1": self._prompt, "PROMPT_COMMAND": f"echo {self._prompt}"}
        self._child = pexpect.spawn(
            self._shell,
            cwd=self._cwd,
            env=env,
            encoding="utf-8",
            timeout=30,
            maxread=1024 * 1024,
        )
        self._child.expect([self._prompt, pexpect.TIMEOUT], timeout=5)
        self._last_used = time.monotonic()
        logger.info("Persistent shell started", session_id=self._session_id)

    def execute(self, command: str, timeout: float = 60) -> dict[str, Any]:
        """Execute a command in the persistent shell."""
        if not self.is_alive:
            self.start()
        if not self.is_alive:
            return {
                "success": False,
                "output": "",
                "error": "Shell session is not alive",
                "exit_code": -1,
            }

        start = time.monotonic()
        self._child.sendline(command)
        try:
            self._child.expect(self._prompt, timeout=timeout)
            output = self._child.before or ""
            output = output.replace("\r\n", "\n").strip()
            duration_ms = (time.monotonic() - start) * 1000
            self._command_count += 1
            self._last_used = time.monotonic()

            return {
                "success": True,
                "output": output,
                "error": None,
                "exit_code": 0,
            }
        except pexpect.TIMEOUT:
            duration_ms = (time.monotonic() - start) * 1000
            return {
                "success": False,
                "output": self._child.before or "",
                "error": f"Command timed out after {timeout}s",
                "exit_code": -1,
            }
        except pexpect.EOF:
            return {
                "success": False,
                "output": "",
                "error": "Shell session ended unexpectedly",
                "exit_code": -1,
            }

    def stop(self) -> None:
        """Stop the persistent shell session."""
        if self._child and self._child.isalive():
            self._child.sendline("exit")
            try:
                self._child.expect(pexpect.EOF, timeout=5)
            except pexpect.TIMEOUT:
                self._child.terminate(force=True)
            logger.info(
                "Persistent shell stopped",
                session_id=self._session_id,
                commands_executed=self._command_count,
            )
        self._child = None


class PersistentShellPool:
    """Manages a pool of persistent shell sessions."""

    def __init__(self, max_sessions: int = 10, ttl_seconds: int = 1800) -> None:
        self._max_sessions = max_sessions
        self._ttl = ttl_seconds
        self._sessions: dict[str, PersistentShell] = {}

    def create(
        self,
        session_id: str,
        cwd: str = "/workspace",
        env: dict[str, str] | None = None,
    ) -> PersistentShell:
        if len(self._sessions) >= self._max_sessions:
            self._evict_expired()
        if len(self._sessions) >= self._max_sessions:
            oldest_key = min(self._sessions, key=lambda k: self._sessions[k]._last_used)
            self._sessions[oldest_key].stop()
            del self._sessions[oldest_key]

        shell = PersistentShell(
            session_id=session_id,
            cwd=cwd,
            env=env,
            ttl_seconds=self._ttl,
        )
        shell.start()
        self._sessions[session_id] = shell
        return shell

    def get(self, session_id: str) -> PersistentShell | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired or not session.is_alive:
            session.stop()
            del self._sessions[session_id]
            return None
        return session

    def destroy(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session:
            session.stop()
            return True
        return False

    def _evict_expired(self) -> int:
        expired = [sid for sid, s in self._sessions.items() if s.is_expired or not s.is_alive]
        for sid in expired:
            self._sessions[sid].stop()
            del self._sessions[sid]
        return len(expired)

    def cleanup(self) -> int:
        return self._evict_expired()

    @property
    def active_count(self) -> int:
        return len(self._sessions)
