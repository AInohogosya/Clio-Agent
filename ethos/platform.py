"""What this host can actually do, asked in one place so nothing has to guess.

Ethos is written in a style that reads as platform-neutral, and for a long time
that was true only in appearance: a `pty` import at the top of a module, an
`os.kill(pid, 0)`, a `start_new_session=True` that CPython accepts on Windows and
then ignores. Each of those is a line that means something on macOS and Linux and
means nothing — or means the opposite of what it says — on Windows, and the whole
set shares one property: **none of them raises.** A POSIX-only import fails loudly
and is fixed in a minute. A POSIX-only *convention* fails silently, and the agent
carries on believing it asked a process to stop when it killed it, or that a
destructive command was harmless because its spelling was `Remove-Item`.

So the split this module draws is not "Windows code" and "POSIX code". It is
between the capabilities that are **absent** — no pseudo-terminals, no unix
sockets, no X11 — which are asked about and reported, and the conventions that are
**silently different** — process liveness, process termination, file permissions —
which are answered here rather than at each call site, because a call site that
has to remember is a call site that will forget.

Every predicate below is decided by *asking the platform*, not by hardcoding a
name. `SUPPORTS_UNIX_SOCKETS` is `os.name == "posix"` rather than
`sys.platform != "win32"` because the question is whether the syscall family
exists, and the two spellings of the question would eventually disagree on
Cygwin, on a POSIX subsystem for Windows, and on anything else that reports
`sys.platform` as `win32` while providing the calls. uvloop is the exception, and
is asked as `sys.platform != "win32"`, because that is how `pyproject.toml`
already decides whether to install it: the dependency marker and the guard have to
be the same question or the CLI asks for a package the installer was told to skip.

**The rule this module exists to enforce: nothing is silently no-op'd.** Each
capability that is missing has a name and a reason, and `ethos doctor` prints both.
A capability that is skipped is reported as skipped, never as `ok` — the failure
mode this codebase is most careful about is a health check that reports success for
something it did not do.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from typing import Any

__all__ = [
    "IS_POSIX",
    "IS_WINDOWS",
    "SUPPORTS_UNIX_SOCKETS",
    "CapabilityError",
    "available_capabilities",
    "current_user_sid",
    "detached_popen_kwargs",
    "hard_kill_pid",
    "install_fast_loop",
    "posix_only",
    "process_is_alive",
    "read_acl",
    "restrict_to_user",
    "uvicorn_loop",
]

IS_WINDOWS = sys.platform == "win32"
"""`sys.platform` rather than `os.name`, to match the `uvloop` dependency marker.

`pyproject.toml` installs uvloop under `sys_platform != 'win32'`. A guard spelled
any other way would ask a different question from the one the installer's answer
was based on, and the failure would be an `ImportError` at the top of the CLI
rather than a missing optional speedup.
"""

IS_POSIX = os.name == "posix"

SUPPORTS_UNIX_SOCKETS = IS_POSIX
"""Whether `AF_UNIX` and `asyncio.start_unix_server` exist here.

The single idiom for this, because the fallback is not a fallback in the sense of
"worse": a loopback TCP socket carries the same JSON over the same machine, and
the whole internal protocol is one HTTP request. What changes is the *address*,
and therefore the length of the string a client has to agree on, and nothing
else. httpx's `uds=` transport is POSIX-only for the same reason the syscall is.
"""


class CapabilityError(RuntimeError):
    """A capability this host does not have, asked for by name.

    Distinct from `NotImplementedError` so a caller can tell "this platform cannot"
    from "this code is unfinished", which is the difference between a feature that
    degrades and a bug.
    """


# --------------------------------------------------------------------------- pty

# `pty`, `termios` and `tty` are POSIX-only in CPython: not merely unused on
# Windows, absent. `find_spec` rather than a try/except import because the answer
# is wanted at import time on every platform — including in `ethos doctor`, which
# has to be able to say *why* a feature is missing without importing the thing that
# is missing. `os.fork`, which `PtySession` also needs, is likewise absent.
PTY_SUPPORTED = IS_POSIX and importlib.util.find_spec("pty") is not None
"""Whether a persistent pseudo-terminal shell session can exist here.

Probed with `find_spec` so that the answer costs no import and so that the module
which *would* import `pty` is not dragged in by the module reporting its absence.
"""

PTY_UNAVAILABLE_REASON = (
    "persistent PTY sessions need pty.openpty, os.fork and os.execve, and CPython "
    "provides none of them on Windows; the ConPTY path is not a POSIX pty"
)


def posix_only(feature: str, reason: str) -> None:
    """Refuse a feature on a host that cannot have it, saying which one and why.

    Used at the boundary where a POSIX capability is reached, so that a tool call
    the agent was told it had comes back as a sentence naming the reason rather
    than as an `AttributeError` from whichever line happened to touch `pty` first.
    """
    raise CapabilityError(f"{feature} is not available on {sys.platform}: {reason}")


# -------------------------------------------------------------------- event loop


def install_fast_loop() -> None:
    """Install uvloop, where uvloop is installed.

    A no-op on Windows, and deliberately silent about it: uvloop is an *optimisation*
    (it is worth a double-digit percentage on a busy event loop) and the absence of
    an optimisation is not a failure to report. The one thing that must not happen is
    the import — `uvloop` is not a Windows wheel, and importing it unconditionally
    takes down the entire CLI including `ethos --version` and `ethos doctor`, which
    are the two commands somebody would reach for when something else has failed.
    """
    if IS_WINDOWS:
        return
    import uvloop

    uvloop.install()


def uvicorn_loop() -> str:
    """The event loop policy name to hand uvicorn.

    uvicorn resolves this string with `importlib`, so an unavailable name fails at
    `Server.serve()` — late, inside a process that has already bound its socket and
    written its "serving" log line. `"asyncio"` is uvicorn's own default and exists
    on every platform uvicorn supports, including the Windows selector loop, so the
    fallback is a supported configuration rather than a spelling of "none".
    """
    return "asyncio" if IS_WINDOWS else "uvloop"


# ------------------------------------------------------------------- processes


def detached_popen_kwargs() -> dict[str, Any]:
    """The `Popen` kwargs that make a child survive the terminal that started it.

    On POSIX this is `start_new_session=True`, the Python spelling of
    `nohup … & disown`: the child gets a new session, so closing that terminal —
    or logging out — sends it no hangup. CPython accepts the same keyword on
    Windows and *ignores* it: the parameter is literally named
    `unused_start_new_session` in the Windows branch of `subprocess.Popen`, and its
    docstring reads "start_new_session (POSIX only)". An agent started that way dies
    with the window, silently, which is the exact failure the detached start exists
    to remove.

    The Windows spelling is `creationflags`. `DETACHED_PROCESS` (0x8) detaches the
    child from the console of the process that created it: it does not inherit a
    console, does not receive the console's Ctrl-C or Ctrl-Close, and is not killed
    when that console closes. `CREATE_NEW_PROCESS_GROUP` (0x200) additionally makes
    the child the root of its own process group, which is what lets a group-wide
    send-to-all-targets reach it and its descendants later. Both constants are
    defined by CPython's `subprocess` module *only* under its `_mswindows` block,
    which is why they are named here rather than at module scope: referencing
    `subprocess.DETACHED_PROCESS` on Linux is an `AttributeError` at import.
    """
    if not IS_WINDOWS:
        return {"start_new_session": True}
    return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}


def _open_process(pid: int) -> int | None:
    """A Windows process handle for `pid`, or `None` when it is not openable.

    `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` is the least privilege that
    still answers the question being asked; it is what the `GetExitCodeProcess`
    documentation calls "the minimum access required", and unlike
    `PROCESS_ALL_ACCESS` it is granted to a process the caller does not own.

    `ctypes.WinDLL` does not exist on POSIX, so it is reached through a `getattr`
    that fails loudly here rather than at import: a wrong answer in this function
    would be a live process reported dead, which is the failure mode of the POSIX
    probe this replaces.
    """
    import ctypes

    windll = getattr(ctypes, "WinDLL", None)
    if windll is None:
        raise CapabilityError("Windows process handles are not available on this platform")
    process_query_limited_information = 0x1000
    kernel32 = windll("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong,
    ]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(
        process_query_limited_information, 0, ctypes.c_ulong(pid)
    )
    return None if not handle else int(handle)


def process_is_alive(pid: int) -> bool:
    """Whether a pid is a live process right now.

    The POSIX spelling is `os.kill(pid, 0)`: signal 0 is not delivered, it only
    asks whether the process could be signalled. **On Windows that is not a probe,
    it is a kill.** CPython's `os.kill` accepts any signal value there, and
    anything other than `CTRL_C_EVENT` / `CTRL_BREAK_EVENT` is passed straight to
    `TerminateProcess`. So the same expression that asks "is the supervisor still
    there?" would answer "no", having just ended it — and `ethos status` would
    refuse to start an agent, and `ethos up` would launch a second supervisor to
    fight a first that no longer exists. This is the reason liveness lives in one
    function rather than at each call site.

    The Windows question is asked with `OpenProcess` and `GetExitCodeProcess`. A
    handle that will not open means the process is gone, and `GetExitCodeProcess`
    returning anything other than `STILL_ACTIVE` (259) means it has finished. An
    `Access denied` opening is reported as *not* ours and therefore not counted,
    which is the same answer the POSIX branch gives for `PermissionError` — a
    process the agent cannot signal is not a supervisor it is entitled to act on.
    """
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return False
        return True

    import ctypes

    still_active = 259
    handle = _open_process(pid)
    if handle is None:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == still_active
    finally:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))


def hard_kill_pid(pid: int) -> None:
    """End a process now, with no chance to clean up. The *escalation*, not the ask.

    Reaching this means the polite route has been tried and timed out. On POSIX
    that route is `SIGTERM`. **On Windows there is no such route**, and working out
    why is a finding rather than a gap in this function:

    - `SIGINT` and `SIGBREAK` become console control events, and
      `GenerateConsoleCtrlEvent` only reaches processes that share a console *and*
      sit in the caller's process group. A detached supervisor, by construction,
      does neither, so `os.kill(pid, signal.CTRL_BREAK_EVENT)` raises `OSError` —
      and raises it after the operator has already decided the agent is going.
    - `os.kill(pid, anything_else)` is not a request either. CPython hands any
      value other than the two control events to `TerminateProcess`, so even
      `SIGTERM` arrives here as a hard kill. The same call in the same shape is a
      polite request on one platform and a murder on the other.

    Which is why the cooperative path on Windows is a file the supervisor's own
    poll loop watches (`ethos.core.supervisor.STOP_REQUEST_FILE`), and this is the
    step after it.

    `taskkill /T` so the whole process tree goes: the supervisor's children are its
    descendants, and a supervisor that is gone leaves them running unsupervised —
    the failure the supervisor exists to prevent. `taskkill` lives in
    `%SystemRoot%\\System32` and is on `PATH` in any interactive session; it is
    called by name so that a stripped `PATH` shows up as a failure to escalate
    rather than being quietly worked around.
    """
    if not IS_WINDOWS:
        os.kill(pid, 9)
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False, capture_output=True, text=True,
    )


# ------------------------------------------------------------------- file ACLs


def current_user_sid() -> str | None:
    """This process's user SID as `S-1-5-21-…`, or `None` when it cannot be had.

    The SID rather than a name, because a name is not a stable key for an access
    control entry: a bare account is `alice`, a domain user's name is
    `CORP\\alice`, and a local account and a domain account of the same name
    are different principals. `icacls` accepts the `*SID` form directly, so this
    also avoids quoting a name that may contain a backslash.

    `ConvertSidToStringSidW` allocates, so the result is freed with `LocalFree` —
    leaking it is harmless in a one-shot CLI but not in a long-lived toolhost, and
    the free is two lines.
    """
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    token_query = 0x0008
    token_user = 1
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    handle = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), token_query, ctypes.byref(handle)
    ):
        return None
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(
            handle, token_user, None, 0, ctypes.byref(size)
        )
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            handle, token_user, buffer, size, ctypes.byref(size)
        ):
            return None
        # TOKEN_USER is a SID_AND_ATTRIBUTES: the SID pointer is the first field.
        sid = ctypes.cast(
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.c_void_p
        )
        text = ctypes.c_wchar_p()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return None
        try:
            return text.value
        finally:
            kernel32.LocalFree(text)
    finally:
        kernel32.CloseHandle(handle)


def read_acl(path: str | os.PathLike[str]) -> str:
    """The access control list of a path, as `icacls` prints it.

    An empty string when it cannot be read, which callers must not mistake for
    "no entries": the difference is the whole point of checking.
    """
    if not IS_WINDOWS:
        return ""
    result = subprocess.run(
        ["icacls", str(path)], check=False, capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _acl_is_private(acl: str, sid: str) -> bool:
    """Whether an `icacls` listing names nobody but the owner.

    Fail-closed on anything unrecognised. The principals NTFS grants by default to
    a file in a user profile are the owner, `SYSTEM`, `Administrators` and
    sometimes `Users`; the last of those is the one that lets a second account on
    the machine read an API key, so its presence is a failure rather than a detail.
    The two that remain are not a weakening — `SYSTEM` and `Administrators` can
    already take the file — and refusing them would make the check unsatisfiable.
    """
    if not acl.strip():
        return False
    lowered = acl.lower()
    # `(I)` marks an entry inherited from the parent. An inherited `Users` grant is
    # exactly the thing `/inheritance:r` exists to strip, so its presence fails.
    if "(i)" in lowered:
        return False
    for line in acl.splitlines()[1:]:
        principal = line.split("(")[0].strip().strip("*").lower()
        if not principal:
            continue
        if principal in {"everyone", "users", "authenticated users", "guests", "nt authority\\users"}:
            return False
        if sid and sid.lstrip("*").lower() not in principal and principal not in {
            "nt authority\\system", "builtin\\administrators", "system", "administrators",
        }:
            return False
    return True


def restrict_to_user(path: str | os.PathLike[str]) -> str:
    """Make a path readable and writable by its owner and nobody else.

    Returns a short description of what was actually done, for `ethos doctor` to
    print. Raises `CapabilityError` when it could not be done, because the caller
    is a credential store and a store that quietly kept its file world-readable is
    the thing this exists to prevent.

    The POSIX answer is `chmod 0600`, and the Windows answer is an ACL, because
    **the POSIX answer does nothing on Windows.** NTFS has no mode bits; `os.chmod`
    there reaches `SetFileAttributes` and can only clear the read-only flag, so
    `chmod(path, 0o600)` returns success having changed nothing a reader would
    notice. Any check of the form `(mode & 0o077) == 0` is therefore *vacuously
    true* on Windows, which is the worst kind of wrong in a security check: it
    passes, and it is followed.

    The Windows call is `icacls <path> /inheritance:r /grant:r *<SID>:(F)`.
    `/inheritance:r` removes every entry inherited from the parent directory — which
    is where a second local account's read access would come from — and
    `/grant:r` replaces this principal's grant instead of adding to it, so running
    this twice is the same as running it once. `(F)` is full control, matching what
    `0600` grants the owner on POSIX.

    It is then **read back and checked**, because an `icacls` that exited 0 is not
    a guarantee and a security check that cannot fail is not a check. See
    `_acl_is_private` for what counts as private and why the reading is
    fail-closed.
    """
    if not IS_WINDOWS:
        os.chmod(path, 0o600)
        return "posix 0600"

    sid = current_user_sid()
    if not sid:
        raise CapabilityError(
            "this file holds a credential and its permissions could not be restricted: "
            "the current user's SID could not be read from the process token"
        )
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
        check=False, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise CapabilityError(
            f"this file holds a credential and icacls could not restrict it: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    if not _acl_is_private(read_acl(path), sid):
        raise CapabilityError(
            f"this file holds a credential and its access control list is still "
            f"readable by more than its owner, so it was not accepted: {read_acl(path).strip()}"
        )
    return f"windows acl ({sid})"


# ------------------------------------------------------------------ reporting


def available_capabilities() -> list[tuple[str, bool, str]]:
    """Every capability this host either has or provably lacks, for `ethos doctor`.

    A list of `(name, supported, reason)` and nothing else. The `reason` is
    mandatory and is filled in for the supported case too, because a line that says
    only "pty sessions: ok" tells a reader nothing they did not already assume, and
    the whole value of this function is the difference between a host where a
    feature works and a host where it is absent by design.

    Reported, never returned as `ok`. A capability that was skipped is not a
    capability that was checked, and `doctor` is the one surface in this program
    that claims to be honest about what it measured.
    """
    return [
        (
            "unix domain sockets",
            SUPPORTS_UNIX_SOCKETS,
            "AF_UNIX is present; the gateway and toolhost bind a unix socket"
            if SUPPORTS_UNIX_SOCKETS
            else "Windows has no AF_UNIX; the gateway and toolhost use loopback TCP",
        ),
        (
            "persistent PTY sessions",
            PTY_SUPPORTED,
            "pty.openpty, os.fork and os.execve are all present"
            if PTY_SUPPORTED
            else PTY_UNAVAILABLE_REASON,
        ),
        (
            "POSIX shell semantics",
            IS_POSIX,
            "/bin/sh, process groups and `&` backgrounding behave as written"
            if IS_POSIX
            else "shell.run uses cmd.exe, so POSIX quoting, globs and pipelines differ",
        ),
        (
            "X11 desktop control",
            _desktop_possible(),
            "xdotool is on PATH and a DISPLAY can be set"
            if _desktop_possible()
            else "needs an X11 DISPLAY and xdotool, which is the agent VM's setup",
        ),
        (
            "file permissions by mode bits",
            IS_POSIX,
            "chmod 0600 is enforced by the kernel"
            if IS_POSIX
            else "NTFS has no mode bits; credentials are protected by an ACL instead",
        ),
    ]


def _desktop_possible() -> bool:
    """Whether the X11 desktop controller could run here at all.

    Asked with `shutil.which` rather than by `os.name`: the controller drives
    `xdotool` against a DISPLAY, and a Windows machine that has XQuartz or WSLg on
    its PATH is still a machine where `DISPLAY=:1` names nothing. So the tool has to
    be there *and* the platform has to be one where the convention is meaningful.
    """
    if not IS_POSIX:
        return False
    import shutil

    return shutil.which("xdotool") is not None
