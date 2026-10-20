"""Persistent pseudo-terminal shell sessions, where the platform has them.

A PTY is what makes `shell.job` and interactive workflows usable: a session keeps
environment, cwd and background jobs alive across tool calls, which a
`subprocess.run` cannot do. Getting one needs `pty.openpty`, `os.fork`,
`os.setsid`, `os.execve` and `termios` — five names CPython provides on POSIX and
**none** of which exist on Windows. Windows has ConPTY, which is a different thing
behind a different API, and is not reachable from this module.

So the module imports everywhere and the *capability* is what is conditional. The
distinction matters more than it looks: a top-level `import pty` makes
`ethos.toolhost` — and therefore the toolhost process, and therefore the whole
agent — fail to start on Windows, when the only thing actually needed is a shell
that will refuse one particular tool. Importing lazily and asking
`ethos.platform.PTY_SUPPORTED` turns a dead agent into a degraded one, which is
the difference between a platform gap and an outage.

The POSIX imports are therefore inside `PtySession.open`, `resize` and `close` —
the methods that need them — and `is_pty_supported()` is the question callers ask
before trying. `PtyManager.open_session` raises `CapabilityError` naming the
reason rather than returning a dead session, so a tool call comes back as a
sentence the agent can read instead of an `ImportError` from four frames down.

One line in the original was a bug on its own terms and is worth naming:
`def open(self, shell: str = "/bin/zsh" if os.uname().sysname == "Darwin" else
"/bin/bash")` evaluates `os.uname()` **as a default argument value**, which is at
class-definition time, at import. So a module that merely mentioned a default
shell could not be imported on a platform without `uname`. The default is now
computed when a session is actually opened, and by a function that does not
assume which POSIX it is on.
"""

from __future__ import annotations

import asyncio
import os
import struct
from collections import deque
from typing import Any

from ethos.platform import PTY_SUPPORTED, PTY_UNAVAILABLE_REASON, CapabilityError


def is_pty_supported() -> bool:
    """Whether a PTY session can be opened on this host.

    Asked as a question rather than assumed from the import succeeding, because a
    module that imports everywhere does not mean the capability is there — that is
    the whole reason this module exists in this shape.
    """
    return PTY_SUPPORTED


def unavailable_reason() -> str:
    """Why not, in a sentence a tool result or a log line can carry."""
    return PTY_UNAVAILABLE_REASON


def default_shell() -> str:
    """The shell a session starts, chosen from what is actually installed.

    The original hardcoded `/bin/zsh` on Darwin and `/bin/bash` everywhere else,
    which is a `/bin/bash` that does not exist on a machine that has not installed
    it — and the failure arrived as `execve: No such file or directory` after the
    fork, so the session reported itself open and produced nothing. `shutil.which`
    is the same question the desktop controller already asks of `xdotool`.
    """
    import shutil

    for candidate in ("/bin/bash", "/bin/zsh", "/bin/sh"):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("bash") or shutil.which("sh") or "/bin/sh"


class PtySession:
    """A persistent pseudo-terminal shell session under user `ethos`.

    Sessions keep environment, cwd and background jobs alive across tool
    calls, which is what makes `shell.job` and interactive workflows usable.
    """

    def __init__(self, session_id: str, cwd: str | None = None, env: dict[str, str] | None = None):
        self.id = session_id
        self.env = env or {}
        self.cwd = cwd
        self.scrollback: deque[str] = deque(maxlen=400)
        self.alive = False
        self._master: int | None = None
        self._slave: int | None = None
        self._pid: int | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self.last_activity = 0.0
        self._select: Any = None
        """`select.select`, bound by `open`. A field rather than an import in
        `_drain_sync` so the reader thread — which cannot import usefully on its
        own — is handed the one callable it needs."""

    def open(self, shell: str | None = None) -> None:
        """Fork a shell onto a new pseudo-terminal and start draining it.

        Refuses up front on a host without one, by name, so the refusal is a
        sentence about the platform rather than an `ImportError` raised from inside
        the `pty` import three lines down.
        """
        if not PTY_SUPPORTED:
            raise CapabilityError(f"a PTY session cannot be opened here: {PTY_UNAVAILABLE_REASON}")

        # Imported here, not at module scope: these five names do not exist on
        # Windows, and a module-level import of any of them takes down every
        # process that imports this one — including the toolhost, which has plenty
        # of work that is nothing to do with terminals.
        import pty
        import select
        import tty

        self._select = select.select
        self._master, self._slave = pty.openpty()
        tty.setraw(self._master)
        pid = os.fork()
        if pid == 0:
            os.setsid()
            os.dup2(self._slave, 0)
            os.dup2(self._slave, 1)
            os.dup2(self._slave, 2)
            if self._slave > 2:
                os.close(self._slave)
            os.close(self._master)
            env_full = dict(os.environ)
            env_full.update(self.env)
            if self.cwd:
                try:
                    os.chdir(self.cwd)
                except OSError:
                    pass
            try:
                os.execve(shell or default_shell(), [shell or default_shell(), "-i"], env_full)
            except OSError:
                os._exit(127)
        os.close(self._slave)
        self._slave = None
        self._pid = pid
        self.alive = True
        self.last_activity = asyncio.get_event_loop().time()
        self._reader_task = asyncio.get_running_loop().run_in_executor(None, self._drain_sync)

    def _drain_sync(self) -> None:
        while self.alive and self._master is not None:
            try:
                ready, _, _ = self._select([self._master], [], [], 0.2)
            except (OSError, ValueError):
                break
            if ready:
                try:
                    data = os.read(self._master, 4096)
                except OSError:
                    break
                if not data:
                    break
                self.scrollback.append(data.decode("utf-8", errors="replace"))


    def write(self, data: str) -> None:
        if self._master is None or not self.alive:
            raise RuntimeError("pty session is not open")
        os.write(self._master, data.encode("utf-8"))
        self.last_activity = asyncio.get_event_loop().time()

    def read(self, wait_s: float = 0.3) -> str:
        import time as _time

        deadline = _time.monotonic() + wait_s
        while _time.monotonic() < deadline:
            if self.scrollback:
                return self.scrollback.popleft()
            _time.sleep(0.02)
        if self.scrollback:
            return self.scrollback.popleft()
        return ""

    def dump(self) -> str:
        out = "".join(self.scrollback)
        self.scrollback.clear()
        return out

    def resize(self, rows: int = 24, cols: int = 80) -> None:
        if self._master is None:
            return
        try:
            # `fcntl` and `termios` are POSIX-only, so they are imported here
            # rather than at the top of the module — and this used to reach past
            # even that with a bare `__import__("fcntl")` inside a `try/except
            # Exception: pass`, which made a missing platform module and a failed
            # ioctl indistinguishable and reported neither. A resize that does not
            # happen is not worth a message, but a resize that cannot be attempted
            # is worth not pretending about.
            import fcntl
            import termios

            fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except (OSError, ImportError, AttributeError):
            pass

    def close(self) -> None:
        self.alive = False
        if self._reader_task is not None:
            try:
                self._reader_task.cancel()
            except Exception:
                pass
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass
            self._master = None
        if self._pid is not None:
            try:
                # `os.kill` is a signal on POSIX and a `TerminateProcess` on
                # Windows, but a session is only ever open where `os.fork` existed,
                # so this is the POSIX branch by construction rather than by a
                # platform test.
                os.kill(self._pid, 15)
            except (ProcessLookupError, OSError):
                pass
            self._pid = None

    @property
    def pid(self) -> int | None:
        return self._pid


class PtyManager:
    def __init__(self, idle_ttl_s: float = 1800.0):
        self.sessions: dict[str, PtySession] = {}
        self.idle_ttl_s = idle_ttl_s

    def open_session(self, session_id: str | None = None, cwd: str | None = None,
                     env: dict[str, str] | None = None) -> PtySession:
        """Open a session, or hand back the live one under that id.

        `CapabilityError` before anything is created on a host without PTYs, rather
        than a session object that is constructed, registered and can never open —
        the caller would then get a "session opened" result containing no shell.
        """
        if not PTY_SUPPORTED:
            raise CapabilityError(f"shell sessions are not available here: {PTY_UNAVAILABLE_REASON}")
        session_id = session_id or f"pty-{id(self)}-{len(self.sessions)}"
        if session_id in self.sessions and self.sessions[session_id].alive:
            return self.sessions[session_id]
        session = PtySession(session_id, cwd=cwd, env=env)
        session.open()
        self.sessions[session_id] = session
        return session

    def get(self, session_id: str) -> PtySession | None:
        session = self.sessions.get(session_id)
        if session is not None and not session.alive:
            self.close_session(session_id)
            return None
        return session

    def close_session(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            session.close()

    def reap_idle(self, now: float) -> int:
        idle = [sid for sid, s in self.sessions.items() if now - s.last_activity > self.idle_ttl_s]
        for sid in idle:
            self.close_session(sid)
        return len(idle)

    def list(self) -> list[dict[str, Any]]:
        return [
            {"id": s.id, "alive": s.alive, "pid": s.pid}
            for s in self.sessions.values()
        ]
