"""
Platform compatibility utilities for Clio Agent.
Provides cross-platform abstractions for process management, filesystem, and network operations.
"""

import os
import sys
import signal
import subprocess
import shutil
from pathlib import Path
from typing import List, Optional, Tuple


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """Check if running on macOS."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Check if running on Linux."""
    return sys.platform.startswith("linux")


def is_unix() -> bool:
    """Check if running on a Unix-like system (Linux, macOS, BSD)."""
    return not is_windows()


def get_platform() -> str:
    """Get a string identifier for the current platform."""
    if is_windows():
        return "windows"
    elif is_macos():
        return "macos"
    elif is_linux():
        return "linux"
    else:
        return "unknown"


def is_process_alive(pid: int) -> bool:
    """
    Check if a process with the given PID is alive.

    Args:
        pid: Process ID to check

    Returns:
        True if process exists and is running, False otherwise
    """
    if pid <= 0:
        return False
    try:
        if is_windows():
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x001F0FFF, False, pid)  # PROCESS_ALL_ACCESS
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            # On Unix, send signal 0 to check existence
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def kill_process(pid: int, force: bool = False) -> bool:
    """
    Kill a process by PID.

    Args:
        pid: Process ID to kill
        force: If True, use SIGKILL (SIGTERM otherwise on Unix)

    Returns:
        True if process was killed or didn't exist, False on error
    """
    if pid <= 0:
        return False
    try:
        if is_windows():
            # On Windows, use taskkill
            result = subprocess.run(
                ["taskkill", "/F" if force else "", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        else:
            # On Unix, send SIGTERM then SIGKILL if force
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, sig)
            return True
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        return False


def spawn_detached(command: List[str], cwd: Optional[Path] = None) -> Optional[int]:
    """
    Spawn a detached child process.

    Args:
        command: Command and arguments as a list
        cwd: Working directory for the child process

    Returns:
        PID of the child process, or None on failure
    """
    try:
        if is_windows():
            # On Windows, use CREATE_NEW_PROCESS_GROUP and DETACHED_PROCESS
            creation_flags = (
                0x00000200 | 0x00000008
            )  # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                creationflags=creation_flags,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # On Unix, use start_new_session to detach
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return proc.pid
    except Exception:
        return None


def ping_host(host: str, timeout: float = 3.0) -> bool:
    """
    Ping a host to check network connectivity.

    Args:
        host: Host to ping (IP or hostname)
        timeout: Timeout in seconds

    Returns:
        True if ping succeeded, False otherwise
    """
    try:
        if is_windows():
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]

        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 1)
        return result.returncode == 0
    except Exception:
        return False


def get_disk_root() -> Path:
    """
    Get the root disk path for the current platform.

    Returns:
        Path to the root filesystem
    """
    if is_windows():
        return Path("C:\\")
    else:
        return Path("/")


def get_cleanup_targets() -> List[Path]:
    """
    Get a list of paths that can be safely cleaned up to free disk space.

    Returns:
        List of Path objects that can be cleaned
    """
    targets = []

    # Common cache directories
    if is_windows():
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            targets.append(Path(local_app_data) / "Temp")
            targets.append(Path(local_app_data) / "pip" / "cache")
    else:
        # Unix-like systems
        targets.append(Path("/tmp"))
        targets.append(Path("/var/tmp"))

        # User cache directories
        home = Path.home()
        targets.append(home / ".cache" / "pip")
        targets.append(home / ".cache" / "huggingface")
        targets.append(home / ".cache" / "torch")

        # XDG cache
        xdg_cache = os.environ.get("XDG_CACHE_HOME", home / ".cache")
        targets.append(Path(xdg_cache) / "pip")

    # Python cache directories
    targets.append(Path.cwd() / "__pycache__")

    # Filter to only existing directories
    return [p for p in targets if p.exists() and p.is_dir()]
