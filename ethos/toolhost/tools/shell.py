from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

from ethos.paths import user_home
from ethos.platform import detached_popen_kwargs
from ethos.schemas.tools import ToolErrorCode, ToolResult
from ethos.toolhost.server import Tool, ToolContext, Toolhost

MAX_OUTPUT = 200_000


async def _run_subprocess(
    command: str,
    cwd: str | None,
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=full_env,
        # See `ethos.platform.detached_popen_kwargs`, which is what this is asking
        # for. `start_new_session=True` here is not cosmetic: it puts the shell in
        # its own session, so a Ctrl-C at the terminal the agent was started from
        # reaches the person and not the shell that is running their command, and
        # so the `killpg` below has a process group to kill that contains the
        # command *and everything it started*. Windows has no process group a
        # signal can be sent to and no `killpg` to send it with, so the timed-out
        # command is terminated directly and its children are not — stated here
        # because the returned `stderr` is the only place a caller would ever
        # learn it.
        **detached_popen_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_s)
    except TimeoutError:
        await _terminate_tree(process)
        return {
            "command": command,
            "returncode": -1,
            "stdout": "",
            "stderr": f"command timed out after {timeout_s}s and was terminated",
            "timed_out": True,
        }
    return {
        "command": command,
        "returncode": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT],
        "stderr": stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT],
        "timed_out": False,
    }


async def _terminate_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a timed-out command, escalating the way POSIX expects.

    `SIGTERM` to the whole process group, five seconds, then `SIGKILL` to the same
    group. The group is the point: `sh -c "npm install &"` leaves a build running
    that a plain `terminate()` on the shell would orphan, and an orphaned build
    holds the file handles a later run needs.

    The original reached for `os.killpg` inside `try: … except OSError:` and
    believed it was guarded. It was not: `os.killpg` does not *exist* on Windows, so
    the `AttributeError` is not an `OSError` and went straight through. The agent's
    own shell tool then raised out of `execute` on the first command that overran
    its timeout — a normal thing for a command to do, and otherwise handled by
    returning a result.

    Windows has no process group a signal can be sent to and no `killpg` to send it
    with, so the question is asked rather than assumed. There the shell is
    terminated directly and the tree under it is not swept: `taskkill /T` is the
    only handle on it, it is a synchronous subprocess inside an async tool, and the
    cost of being wrong in that direction — one orphaned `npm install` — is much
    lower than the cost of blocking the event loop while a process tree is walked.
    Noted in the result rather than left to be discovered later.
    """
    if not hasattr(os, "killpg"):
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            process.kill()


class ShellRunTool(Tool):
    name = "shell.run"
    description = (
        "Run a shell command and return stdout/stderr. Dangerous commands pass "
        "through the guardian gate (fast-path trash, snapshots, audit). Runs with "
        "the same filesystem permissions as the user who started the agent, and "
        "relative paths are relative to their home directory."
    )
    timeout_s = 300.0
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "shell command line"},
            "cwd": {"type": "string", "description": "working directory; any directory the user can reach"},
            "timeout_s": {"type": "number", "description": "optional timeout in seconds"},
            "env": {"type": "object", "description": "extra environment variables"},
        },
        "required": ["command"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        command = str(args.get("command", ""))
        if not command.strip():
            raise ValueError("command must not be empty")
        cwd = args.get("cwd") or ctx.cwd or str(user_home())
        Path(cwd).mkdir(parents=True, exist_ok=True)
        result = await _run_subprocess(
            command, cwd, float(args.get("timeout_s", 120.0)), env=args.get("env"),
        )
        return result


class ShellJobTool(Tool):
    name = "shell.job"
    description = "Start a background shell job; poll its output or kill it with shell.job actions."
    timeout_s = 15.0
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "action": {"type": "string", "enum": ["start", "status", "output", "kill"]},
            "job_id": {"type": "string"},
            "cwd": {"type": "string", "description": "working directory; defaults to the user's home"},
        },
        "required": ["command", "action"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        action = args.get("action", "start")
        if action == "start":
            command = str(args.get("command", ""))
            if not command.strip():
                raise ValueError("command must not be empty")
            job_id = f"job-{len(host.background_jobs) + 1}-{os.getpid()}"
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                # Same default as `shell.run`. It used to be left unset, which
                # meant the job started wherever the toolhost process happened to
                # be launched from -- so a relative path in a background command
                # meant one thing here and another in the foreground, and both
                # depended on how the deployment was started.
                cwd=args.get("cwd") or ctx.cwd or str(user_home()),
            )
            host.background_jobs[job_id] = {"process": process, "command": command, "output": []}
            asyncio.get_running_loop().create_task(self._pump(host, job_id))
            return {"job_id": job_id, "command": command, "status": "running"}
        job_id = str(args.get("job_id", ""))
        job = host.background_jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown job: {job_id}")
        process: asyncio.subprocess.Process = job["process"]
        if action == "status":
            return {"job_id": job_id, "status": "running" if process.returncode is None else "done",
                    "returncode": process.returncode, "command": job["command"]}
        if action == "output":
            return {"job_id": job_id, "output": "".join(job["output"])[-MAX_OUTPUT:],
                    "status": "running" if process.returncode is None else "done"}
        if action == "kill":
            if process.returncode is None:
                process.kill()
            return {"job_id": job_id, "killed": True}
        raise ValueError(f"unknown action: {action}")

    async def _pump(self, host: Toolhost, job_id: str) -> None:
        job = host.background_jobs[job_id]
        process = job["process"]
        assert process.stdout is not None
        async for line in process.stdout:
            job["output"].append(line.decode("utf-8", errors="replace"))
            if len(job["output"]) > 4000:
                job["output"] = job["output"][-2000:]
        await process.wait()


class ShellSessionOpenTool(Tool):
    name = "shell.session_open"
    description = "Open a persistent PTY shell session (keeps cwd/env/state across writes)."
    timeout_s = 20.0
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "cwd": {"type": "string", "description": "working directory; defaults to the user's home"},
            "env": {"type": "object", "description": "extra environment variables"},
        },
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = host.pty_manager.open_session(
            session_id=args.get("session_id"),
            # The user's home, like every other shell entry point. Left unset,
            # `PtySession` skipped its `chdir` and the session inherited whatever
            # directory the toolhost was started in.
            cwd=args.get("cwd") or ctx.cwd or str(user_home()),
            env=args.get("env"),
        )
        await asyncio.sleep(0.5)
        return {"session_id": session.id, "alive": session.alive, "banner": session.dump()[:4000]}


class ShellSessionWriteTool(Tool):
    name = "shell.session_write"
    description = "Send a command to an open PTY session."
    timeout_s = 30.0
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "command": {"type": "string"},
            "wait_s": {"type": "number"},
        },
        "required": ["session_id", "command"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = host.pty_manager.get(str(args.get("session_id", "")))
        if session is None:
            raise KeyError("session not found or dead")
        session.write(str(args.get("command", "")) + "\n")
        await asyncio.sleep(float(args.get("wait_s", 1.0)))
        return {"session_id": session.id, "output": session.dump()[:MAX_OUTPUT]}


class ShellSessionReadTool(Tool):
    name = "shell.session_read"
    description = "Drain new output from an open PTY session."
    timeout_s = 15.0
    input_schema = {
        "type": "object",
        "properties": {"session_id": {"type": "string"}, "wait_s": {"type": "number"}},
        "required": ["session_id"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        session = host.pty_manager.get(str(args.get("session_id", "")))
        if session is None:
            raise KeyError("session not found or dead")
        await asyncio.sleep(float(args.get("wait_s", 0.3)))
        return {"session_id": session.id, "output": session.dump()[:MAX_OUTPUT]}


class ShellSessionCloseTool(Tool):
    name = "shell.session_close"
    description = "Close a PTY session."
    timeout_s = 10.0
    input_schema = {
        "type": "object",
        "properties": {"session_id": {"type": "string"}},
        "required": ["session_id"],
    }

    async def run(self, args: dict[str, Any], ctx: ToolContext, host: Toolhost) -> Any:
        host.pty_manager.close_session(str(args.get("session_id", "")))
        return {"closed": str(args.get("session_id", ""))}


__all__ = [
    "ShellRunTool", "ShellJobTool", "ShellSessionOpenTool", "ShellSessionWriteTool",
    "ShellSessionReadTool", "ShellSessionCloseTool", "ToolResult", "ToolErrorCode",
]
