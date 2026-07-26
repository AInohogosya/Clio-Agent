"""
Cross-platform compatibility utilities for Clio Agent.
Provides consistent platform detection and operations across Windows, macOS, and Linux.
"""

import os
import sys
import platform
import subprocess
import signal
import shutil
import time
from pathlib import Path
from typing import Optional, List, Tuple, Union


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
    """Check if running on a Unix-like system (Linux, macOS, BSD, etc.)."""
    return not is_windows()


def get_platform() -> str:
    """Get platform identifier string."""
    if is_windows():
        return "windows"
    elif is_macos():
        return "macos"
    elif is_linux():
        return "linux"
    else:
        return "unknown"


def get_home_dir() -> Path:
    """Get the user's home directory in a cross-platform way."""
    return Path.home()


def ping_host(host: str, timeout: float = 3.0) -> bool:
    """Ping a host to check network connectivity.
    
    Args:
        host: Host to ping (IP or hostname)
        timeout: Timeout in seconds
        
    Returns:
        True if ping succeeds, False otherwise
    """
    try:
        if is_windows():
            # Windows ping: -n count, -w timeout in milliseconds
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
        else:
            # Unix ping: -c count, -W timeout in seconds
            cmd = ["ping", "-c", "1", "-W", str(int(timeout)), host]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 2
        )
        return result.returncode == 0
    except Exception:
        return False


def get_disk_root() -> Path:
    """Get the root disk path for disk usage checks.
    
    Returns:
        Path to the root filesystem (C:\\ on Windows, / on Unix)
    """
    if is_windows():
        return Path("C:\\")
    else:
        return Path("/")


def get_cleanup_targets() -> List[Path]:
    """Get list of directories that can be safely cleaned up for disk space.
    
    Returns:
        List of Path objects for cleanup targets
    """
    targets = []
    
    if is_windows():
        # Windows temp directories
        temp_dir = os.environ.get("TEMP") or os.environ.get("TMP")
        if temp_dir:
            targets.append(Path(temp_dir))
        # Windows user cache
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            targets.append(Path(local_app_data) / "Temp")
            targets.append(Path(local_app_data) / "Microsoft" / "Windows" / "INetCache")
    else:
        # Unix temp directories
        targets.append(Path("/tmp"))
        targets.append(Path("/var/tmp"))
        
        # User cache directories
        home = get_home_dir()
        targets.append(home / ".cache")
        
        # XDG cache
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            targets.append(Path(xdg_cache))
    
    # Common cache directories
    home = get_home_dir()
    targets.append(home / ".cache" / "pip")
    targets.append(home / ".cache" / "npm")
    targets.append(home / ".cache" / "yarn")
    
    # Filter to only existing directories
    return [t for t in targets if t.exists() and t.is_dir()]


def is_process_alive(pid: int) -> bool:
    """Check if a process is alive.
    
    Args:
        pid: Process ID to check
        
    Returns:
        True if process exists and is running, False otherwise
    """
    try:
        if is_windows():
            # On Windows, use tasklist or OpenProcess
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return str(pid) in result.stdout
        else:
            # On Unix, send signal 0 to check existence
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return False


def kill_process(pid: int, force: bool = False) -> bool:
    """Kill a process.
    
    Args:
        pid: Process ID to kill
        force: If True, use SIGKILL (Unix) or /F (Windows)
        
    Returns:
        True if process was killed, False otherwise
    """
    try:
        if is_windows():
            if force:
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            else:
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
            return result.returncode == 0
        else:
            if force:
                os.kill(pid, signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGTERM)
            return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return False


def spawn_detached(cmd: Union[str, List[str]], cwd: Optional[Union[str, Path]] = None) -> int:
    """Spawn a detached process that runs independently of the parent.
    
    Args:
        cmd: Command to run (string or list of arguments)
        cwd: Working directory for the process
        
    Returns:
        PID of the spawned process
    """
    if is_windows():
        # On Windows, use CREATE_NEW_PROCESS_GROUP and DETACHED_PROCESS
        import subprocess as sp
        creationflags = sp.CREATE_NEW_PROCESS_GROUP | sp.DETACHED_PROCESS
        if isinstance(cmd, str):
            proc = sp.Popen(
                cmd,
                shell=True,
                cwd=cwd,
                creationflags=creationflags,
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
                stdin=sp.DEVNULL,
            )
        else:
            proc = sp.Popen(
                cmd,
                cwd=cwd,
                creationflags=creationflags,
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
                stdin=sp.DEVNULL,
            )
        return proc.pid
    else:
        # On Unix, use double-fork or start_new_session
        import subprocess as sp
        if isinstance(cmd, str):
            proc = sp.Popen(
                cmd,
                shell=True,
                cwd=cwd,
                start_new_session=True,
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
                stdin=sp.DEVNULL,
            )
        else:
            proc = sp.Popen(
                cmd,
                cwd=cwd,
                start_new_session=True,
                stdout=sp.DEVNULL,
                stderr=sp.DEVNULL,
                stdin=sp.DEVNULL,
            )
        return proc.pid


def get_free_disk_space(path: Union[str, Path]) -> int:
    """Get free disk space in bytes for a path.
    
    Args:
        path: Path to check
        
    Returns:
        Free space in bytes, or 0 on error
    """
    try:
        usage = shutil.disk_usage(str(path))
        return usage.free
    except Exception:
        return 0


def get_total_disk_space(path: Union[str, Path]) -> int:
    """Get total disk space in bytes for a path.
    
    Args:
        path: Path to check
        
    Returns:
        Total space in bytes, or 0 on error
    """
    try:
        usage = shutil.disk_usage(str(path))
        return usage.total
    except Exception:
        return 0


def is_admin() -> bool:
    """Check if running with admin/root privileges."""
    try:
        if is_windows():
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin()
        else:
            return os.geteuid() == 0
    except Exception:
        return False


def get_shell() -> str:
    """Get the user's default shell."""
    if is_windows():
        return os.environ.get("COMSPEC", "cmd.exe")
    else:
        return os.environ.get("SHELL", "/bin/bash")