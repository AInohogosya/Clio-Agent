"""
Terminal History System for CLI AI Agent System
Replaces the Save command with terminal log display and history preservation
OS-independent implementation with comprehensive error handling
"""

import json
import time
import os
import subprocess
import threading
import shlex
import platform
import stat
import signal
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass, field, asdict
from pathlib import Path, PurePath
from enum import Enum
from contextlib import contextmanager

from ..utils.logger import get_logger
from ..utils.exceptions import ExecutionError, PlatformError, ValidationError


class TerminalEntryType(Enum):
    """Types of terminal entries"""
    COMMAND = "command"
    OUTPUT = "output"
    ERROR = "error"


@dataclass
class TerminalEntry:
    """Individual terminal entry with command execution information"""
    timestamp: float
    entry_type: TerminalEntryType
    content: str
    command: Optional[str] = None
    working_directory: Optional[str] = None
    return_code: Optional[int] = None
    duration: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with proper serialization"""
        data = asdict(self)
        data['entry_type'] = self.entry_type.value
        data['timestamp'] = str(self.timestamp)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TerminalEntry':
        """Create from dictionary with proper deserialization"""
        data['entry_type'] = TerminalEntryType(data['entry_type'])
        data['timestamp'] = float(data['timestamp'])
        return cls(**data)


@dataclass
class TerminalSession:
    """Complete terminal session for a work session"""
    session_id: str
    start_time: float
    entries: List[TerminalEntry] = field(default_factory=list)
    end_time: Optional[float] = None
    current_working_directory: str = field(default_factory=lambda: str(Path("/")))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with proper serialization"""
        data = asdict(self)
        data['start_time'] = str(self.start_time)
        if self.end_time is not None:
            data['end_time'] = str(self.end_time)
        data['entries'] = [entry.to_dict() for entry in self.entries]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TerminalSession':
        """Create from dictionary with proper deserialization"""
        data['start_time'] = float(data['start_time'])
        if data.get('end_time') is not None:
            data['end_time'] = float(data['end_time'])
        data['entries'] = [TerminalEntry.from_dict(entry) for entry in data.get('entries', [])]
        return cls(**data)


class TerminalHistory:
    """
    Terminal History System that replaces Save command functionality
    Preserves terminal history and displays command outputs instead of Save content
    OS-independent implementation with comprehensive error handling
    """
    
    DEFAULT_TIMEOUT = 30
    DEFAULT_HISTORY_DIR = "./peripherals/terminal_history"
    
    def __init__(self, session_id: Optional[str] = None, history_dir: Optional[Union[str, Path]] = None):
        """
        Initialize terminal history system
        
        Args:
            session_id: Optional session identifier
            history_dir: Directory for history storage
            
        Raises:
            PlatformError: If initialization fails due to platform-specific issues
            ValidationError: If parameters are invalid
        """
        try:
            # Validate and set session ID
            self.session_id = session_id or f"session_{int(time.time())}"
            if not self.session_id or not isinstance(self.session_id, str):
                raise ValidationError("Session ID must be a non-empty string")
            
            # Initialize history directory with OS-independent path handling
            self.history_dir = Path(history_dir or self.DEFAULT_HISTORY_DIR)
            self._ensure_history_directory()
            
            # Initialize logger
            self.logger = get_logger("terminal_history")
            self._execution_lock = threading.Lock()
            self._current_process = None
            self._current_process_lock = threading.Lock()
            
            # Initialize terminal session
            # SECURITY FIX: Start from user's home directory instead of root (/)
            # to prevent unrestricted filesystem access
            self._current_directory = Path.home()
            self._previous_directory = None  # For cd - command support
            self.terminal_session = TerminalSession(
                session_id=self.session_id,
                start_time=time.time(),
                current_working_directory=str(self._current_directory)
            )
            
            # Platform-specific settings
            self._platform = platform.system().lower()
            self._shell = self._detect_shell()
            
            self.logger.info(
                f"Terminal history system initialized",
                session_id=self.session_id,
                platform=self._platform,
                shell=self._shell,
                history_dir=str(self.history_dir),
                working_directory=str(self._current_directory)
            )
            
        except Exception as e:
            if isinstance(e, (PlatformError, ValidationError)):
                raise
            raise PlatformError(f"Failed to initialize terminal history: {str(e)}") from e
    
    def _detect_shell(self) -> str:
        """Detect the current shell for command execution"""
        try:
            if self._platform == "windows":
                return os.environ.get("COMSPEC", "cmd.exe")
            else:
                return os.environ.get("SHELL", "/bin/bash")
        except Exception as e:
            self.logger.warning(f"Failed to detect shell: {e}")
            return "bash" if self._platform != "windows" else "cmd.exe"
    
    def _ensure_history_directory(self) -> None:
        """Ensure history directory exists with proper permissions"""
        try:
            # Create directory with parents if needed
            self.history_dir.mkdir(parents=True, exist_ok=True)
            
            # Check directory is writable
            if not os.access(self.history_dir, os.W_OK):
                raise PermissionError(f"History directory is not writable: {self.history_dir}")
                
        except PermissionError as e:
            raise PlatformError(f"Permission denied creating history directory: {e}") from e
        except OSError as e:
            raise PlatformError(f"Failed to create history directory: {e}") from e
    
    def execute_command(self, command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Execute a CLI command and preserve its output in history
        
        Args:
            command: The CLI command to execute
            timeout: Command timeout in seconds (overrides default)
            
        Returns:
            Dict with execution results including output, error, return code
            
        Raises:
            ValidationError: If command is invalid
            PlatformError: If command execution fails due to platform issues
        """
        if not command or not isinstance(command, str):
            raise ValidationError("Command must be a non-empty string")
        
        if not command.strip():
            raise ValidationError("Command cannot be empty or whitespace only")
        
        # Validate timeout
        if timeout is not None:
            if not isinstance(timeout, (int, float)):
                raise ValidationError("Timeout must be a number")
            if timeout <= 0:
                raise ValidationError("Timeout must be positive")
        else:
            timeout = self.DEFAULT_TIMEOUT
        start_time = time.time()
        
        try:
            # Log command entry
            command_entry = TerminalEntry(
                timestamp=start_time,
                entry_type=TerminalEntryType.COMMAND,
                content=command,
                command=command,
                working_directory=str(self._current_directory)
            )
            self.terminal_session.entries.append(command_entry)
            
            self.logger.info(f"Executing command", command=command, working_directory=str(self._current_directory))
            
            # Handle special commands
            if command.strip().startswith('cd '):
                result = self._handle_cd_command(command, start_time)
            elif command.strip() == 'cd -':
                result = self._handle_cd_dash_command(command, start_time)
            elif command.strip() == 'cd':
                result = self._handle_cd_home_command(command, start_time)
            else:
                result = self._execute_subprocess_command(command, timeout)
            
            duration = time.time() - start_time
            
            # Update command entry with execution results
            command_entry.return_code = result.returncode
            command_entry.duration = duration
            
            self.logger.info(
                f"Command completed",
                command=command,
                return_code=result.returncode,
                duration=duration,
                success=result.returncode == 0
            )
            
            # Log output entry
            if result.stdout and result.stdout.strip():
                output_entry = TerminalEntry(
                    timestamp=start_time + duration,
                    entry_type=TerminalEntryType.OUTPUT,
                    content=result.stdout,
                    command=command,
                    working_directory=str(self._current_directory),
                    return_code=result.returncode,
                    duration=duration
                )
                self.terminal_session.entries.append(output_entry)
            
            # Log error entry if there's stderr
            if result.stderr and result.stderr.strip():
                error_entry = TerminalEntry(
                    timestamp=start_time + duration,
                    entry_type=TerminalEntryType.ERROR,
                    content=result.stderr,
                    command=command,
                    working_directory=str(self._current_directory),
                    return_code=result.returncode,
                    duration=duration
                )
                self.terminal_session.entries.append(error_entry)
            
            # Persist history
            self._persist_history()
            
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
                "duration": duration,
                "working_directory": str(self._current_directory)
            }
            
        except subprocess.TimeoutExpired as e:
            error_msg = f"Command timed out after {timeout} seconds: {command}"
            self.logger.error(error_msg)
            
            # Log timeout error
            error_entry = TerminalEntry(
                timestamp=time.time(),
                entry_type=TerminalEntryType.ERROR,
                content=error_msg,
                command=command,
                working_directory=str(self._current_directory),
                return_code=-1
            )
            self.terminal_session.entries.append(error_entry)
            self._persist_history()
            
            return {
                "success": False,
                "stdout": "",
                "stderr": error_msg,
                "return_code": -1,
                "duration": timeout,
                "working_directory": str(self._current_directory)
            }
            
        except Exception as e:
            error_msg = f"Command execution failed: {str(e)}"
            self.logger.error(error_msg, command=command, error_type=type(e).__name__)
            
            # Log execution error
            error_entry = TerminalEntry(
                timestamp=time.time(),
                entry_type=TerminalEntryType.ERROR,
                content=error_msg,
                command=command,
                working_directory=str(self._current_directory),
                return_code=-1
            )
            self.terminal_session.entries.append(error_entry)
            self._persist_history()
            
            return {
                "success": False,
                "stdout": "",
                "stderr": error_msg,
                "return_code": -1,
                "duration": time.time() - start_time,
                "working_directory": str(self._current_directory)
            }
    
    def get_recent_output(self, count: int = 10) -> List[TerminalEntry]:
        """
        Get recent terminal entries (commands, outputs, and errors) for context
        
        Args:
            count: Number of recent entries to retrieve
            
        Returns:
            List of recent terminal entries
        """
        if count <= 0:
            return []
        
        return self.terminal_session.entries[-count:] if self.terminal_session.entries else []
    
    def get_command_history(self, count: int = 10) -> List[TerminalEntry]:
        """
        Get recent command entries
        
        Args:
            count: Number of recent commands to retrieve
            
        Returns:
            List of recent command entries
        """
        if count <= 0:
            return []
        
        command_entries = [entry for entry in self.terminal_session.entries 
                         if entry.entry_type == TerminalEntryType.COMMAND]
        return command_entries[-count:] if command_entries else []
    
    def get_current_working_directory(self) -> Path:
        """Get current working directory as Path object"""
        return self._current_directory
    
    def _needs_shell(self, command: str) -> bool:
        """Return True when a command requires a shell interpreter.

        Simple commands like ``ls -la /tmp`` can be run via ``shlex.split()``
        without a shell, but any use of shell metacharacters (pipes,
        redirections, variable expansion, tilde expansion, etc.) requires a
        shell.  Detecting these upfront avoids mysterious failures where the
        command is perfectly valid but the argument is not a real executable.
        """
        import re
        # Characters / patterns that require a shell:
        shell_metacharacters = re.compile(
            r'[|&;<>$~*?{}()`\n]|&&|\|\||\$\('
        )
        return bool(shell_metacharacters.search(command))

    def _execute_subprocess_command(self, command: str, timeout: int):
        """Execute command using subprocess with platform-specific handling.

        When the command contains no shell metacharacters it is run directly
        via ``shlex.split()`` (fast, no shell overhead).  When shell features
        are detected (pipes, redirections, ``~``, ``$VAR``, ...) the command
        is delegated to ``bash -c`` (or ``cmd.exe /c`` on Windows).

        Dangerous patterns (``rm -rf /``, fork bombs, etc.) are already
        blocked by the ``BashTool`` in the agent pipeline, so running through
        a shell here is safe.
        """
        try:
            if self._platform == "windows":
                # On Windows, always use cmd.exe /c to get full shell features
                result = subprocess.run(
                    ['cmd.exe', '/c', command],
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(self._current_directory),
                    encoding='utf-8',
                    errors='replace'
                )
            else:
                if self._needs_shell(command):
                    # Delegate to bash so that ~, $VAR, |, >, &&, || etc. work
                    result = subprocess.run(
                        ['bash', '-c', command],
                        shell=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self._current_directory),
                        encoding='utf-8',
                        errors='replace'
                    )
                else:
                    # Simple command — run without shell for efficiency
                    cmd_parts = shlex.split(command)
                    result = subprocess.run(
                        cmd_parts,
                        shell=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self._current_directory),
                        encoding='utf-8',
                        errors='replace'
                    )

            return result

        except FileNotFoundError as e:
            # Command not found - return a mock result with error info
            from types import SimpleNamespace
            self.logger.warning(f"Command not found: {e}")
            return SimpleNamespace(returncode=-1, stdout='', stderr=str(e))
        except subprocess.CalledProcessError as e:
            # This shouldn't happen with capture_output=True, but just in case
            self.logger.warning(f"CalledProcessError in command execution: {e}")
            return e
        except UnicodeDecodeError as e:
            self.logger.error(f"Unicode decode error in command output: {e}")
            # Retry via shell with encoding fallback
            try:
                if self._platform == "windows":
                    result = subprocess.run(
                        ['cmd.exe', '/c', command],
                        shell=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self._current_directory),
                        encoding='utf-8',
                        errors='replace'
                    )
                else:
                    result = subprocess.run(
                        ['bash', '-c', command],
                        shell=False,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=str(self._current_directory),
                        encoding='utf-8',
                        errors='replace'
                    )
                return result
            except Exception as retry_e:
                raise PlatformError(f"Failed to execute command with encoding fallback: {retry_e}") from retry_e

    def _handle_cd_command(self, command: str, start_time: float):
        """Handle cd commands without using subprocess to maintain proper directory state"""
        from types import SimpleNamespace
        
        # Parse the target directory
        command_parts = command.strip().split(None, 1)
        if len(command_parts) < 2:
            return self._handle_cd_home_command(command, start_time)
        
        target_dir = command_parts[1].strip()
        
        # Create a mock result object
        result = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=""
        )
        
        try:
            # Handle special cases and path expansion
            if target_dir == '~' or target_dir == '$HOME':
                target_path = Path.home()
            elif target_dir.startswith('~'):
                # Handle ~user paths
                try:
                    target_path = Path(target_dir).expanduser()
                except Exception as e:
                    result.stderr = f"cd: {target_dir}: Invalid path expansion"
                    result.returncode = 1
                    self.logger.warning(f"Failed to expand path {target_dir}: {e}")
                    return result
            else:
                # Handle relative and absolute paths
                target_path = Path(target_dir)
                if not target_path.is_absolute():
                    target_path = self._current_directory / target_path
            
            # Resolve the path (handles .., ., etc.)
            target_path = target_path.resolve()
            
            # SECURITY: Block cd into truly dangerous virtual/kernel filesystems
            # and root-owned sensitive config directories.  Normal directories
            # such as /usr, /opt, /home, /tmp, /root are allowed so the agent
            # can freely explore the filesystem.
            dangerous_paths = [
                Path('/proc'),   # kernel/process pseudo-fs
                Path('/sys'),    # kernel/driver pseudo-fs
                Path('/dev'),    # raw device files
            ]

            for dangerous in dangerous_paths:
                try:
                    target_path.relative_to(dangerous)
                    result.stderr = f"cd: access denied: {target_dir} (restricted system directory)"
                    result.returncode = 1
                    self.logger.warning(f"Blocked access to dangerous directory: {target_path}")
                    return result
                except ValueError:
                    continue
            
            # Validate and update directory
            if target_path.exists() and target_path.is_dir():
                # Check if directory is accessible
                if not os.access(target_path, os.R_OK | os.X_OK):
                    # Format error message to match actual shell behavior
                    if self._platform == "darwin":
                        # macOS zsh format: "cd: permission denied: target_dir"
                        result.stderr = f"cd: permission denied: {target_dir}"
                    elif self._shell.endswith("bash"):
                        # bash format: "bash: cd: target_dir: Permission denied"
                        result.stderr = f"bash: cd: {target_dir}: Permission denied"
                    else:
                        # Generic format
                        result.stderr = f"cd: {target_dir}: Permission denied"
                    result.returncode = 1
                    self.logger.warning(f"Permission denied for directory: {target_path}")
                else:
                    # Store previous directory for cd -
                    self._previous_directory = self._current_directory
                    self._current_directory = target_path
                    self.terminal_session.current_working_directory = str(self._current_directory)
                    self.logger.info(f"Changed directory to: {self._current_directory}")
            else:
                # Format error message to match actual shell behavior
                if self._platform == "darwin":
                    # macOS zsh format: "cd: no such file or directory: target_dir"
                    result.stderr = f"cd: no such file or directory: {target_dir}"
                elif self._shell.endswith("bash"):
                    # bash format: "bash: cd: target_dir: No such file or directory"
                    result.stderr = f"bash: cd: {target_dir}: No such file or directory"
                else:
                    # Generic format that works across shells
                    result.stderr = f"cd: {target_dir}: No such file or directory"
                result.returncode = 1
                self.logger.warning(f"Directory does not exist: {target_path}")
                
        except Exception as e:
            result.stderr = f"cd: {str(e)}"
            result.returncode = 1
            self.logger.error(f"Failed to update working directory: {e}")
        
        return result
    
    def _handle_cd_dash_command(self, command: str, start_time: float):
        """Handle cd - command (go to previous directory)"""
        from types import SimpleNamespace
        
        result = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=""
        )
        
        try:
            if self._previous_directory is None:
                # Format error message to match actual shell behavior
                if self._platform == "darwin":
                    # macOS zsh format: "cd: no such file or directory: -"
                    result.stderr = "cd: no such file or directory: -"
                elif self._shell.endswith("bash"):
                    # bash format: "bash: cd -: OLDPWD not set"
                    result.stderr = "bash: cd -: OLDPWD not set"
                else:
                    # Generic format
                    result.stderr = "cd: -: OLDPWD not set"
                result.returncode = 1
                self.logger.info("cd - command detected, but no previous directory available")
            else:
                # Swap current and previous directories
                current_dir = self._current_directory
                self._current_directory = self._previous_directory
                self._previous_directory = current_dir
                self.terminal_session.current_working_directory = str(self._current_directory)
                self.logger.info(f"Changed to previous directory: {self._current_directory}")
                
        except Exception as e:
            result.stderr = f"cd: {str(e)}"
            result.returncode = 1
            self.logger.error(f"Failed to change to previous directory: {e}")
        
        return result
    
    def _handle_cd_home_command(self, command: str, start_time: float):
        """Handle cd command with no arguments (go to home directory)"""
        from types import SimpleNamespace
        
        result = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=""
        )
        
        try:
            home_dir = Path.home()
            if home_dir.exists() and home_dir.is_dir():
                if os.access(home_dir, os.R_OK | os.X_OK):
                    # Store previous directory
                    self._previous_directory = self._current_directory
                    self._current_directory = home_dir
                    self.terminal_session.current_working_directory = str(self._current_directory)
                    self.logger.info(f"Changed to home directory: {self._current_directory}")
                else:
                    # Format error message to match actual shell behavior
                    if self._platform == "darwin":
                        # macOS zsh format: "cd: permission denied: path"
                        result.stderr = f"cd: permission denied: {home_dir}"
                    elif self._shell.endswith("bash"):
                        # bash format: "bash: cd: path: Permission denied"
                        result.stderr = f"bash: cd: {home_dir}: Permission denied"
                    else:
                        # Generic format
                        result.stderr = f"cd: {home_dir}: Permission denied"
                    result.returncode = 1
                    self.logger.warning(f"Permission denied for home directory: {home_dir}")
            else:
                # Format error message to match actual shell behavior
                if self._platform == "darwin":
                    # macOS zsh format: "cd: no such file or directory: path"
                    result.stderr = f"cd: no such file or directory: {home_dir}"
                elif self._shell.endswith("bash"):
                    # bash format: "bash: cd: path: No such file or directory"
                    result.stderr = f"bash: cd: {home_dir}: No such file or directory"
                else:
                    # Generic format
                    result.stderr = f"cd: {home_dir}: No such file or directory"
                result.returncode = 1
                self.logger.warning(f"Home directory does not exist: {home_dir}")
                
        except Exception as e:
            result.stderr = f"cd: {str(e)}"
            result.returncode = 1
            self.logger.error(f"Failed to change to home directory: {e}")
        
        return result
    
    # ── ANSI colour palette (class-level, no external dependency) ──────────
    _RESET  = "\033[0m"
    _BOLD   = "\033[1m"
    _DIM    = "\033[2m"
    _CYAN   = "\033[36m"
    _GREEN  = "\033[32m"
    _YELLOW = "\033[33m"
    _RED    = "\033[31m"
    _PURPLE = "\033[35m"
    _GRAY   = "\033[90m"
    _WHITE  = "\033[97m"
    _BG_GREEN  = "\033[42m"
    _BG_RED    = "\033[41m"
    _BG_GRAY   = "\033[100m"

    def _color(self, text: str, *codes: str, use_color: bool = True) -> str:
        """Wrap text with ANSI codes when colour is enabled."""
        if not use_color:
            return text
        return f"{''.join(codes)}{text}{self._RESET}"

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape sequences for plain-text output."""
        import re
        return re.sub(r'\033\[[0-9;]*m', '', text)

    def _format_duration(self, duration: Optional[float], use_color: bool = True) -> str:
        """Format duration as a human-readable badge."""
        if duration is None:
            return ""
        if duration < 1:
            label = f"{duration * 1000:.0f}ms"
        elif duration < 60:
            label = f"{duration:.1f}s"
        else:
            minutes = int(duration // 60)
            seconds = duration % 60
            label = f"{minutes}m {seconds:.0f}s"
        return self._color(f" {label} ", self._DIM, self._GRAY, use_color=use_color)

    def _status_badge(self, return_code: Optional[int], use_color: bool = True) -> str:
        """Return a coloured status badge for a command's return code."""
        if return_code is None:
            return self._color(" \u22ef ", self._DIM, self._GRAY, use_color=use_color)
        if return_code == 0:
            return self._color(" \u2713 ", self._BOLD, self._GREEN, use_color=use_color)
        return self._color(f" \u2717 {return_code} ", self._BOLD, self._RED, use_color=use_color)

    def display_terminal_log(self, max_entries: int = 20, use_color: bool = True) -> str:
        """
        Generate a polished, product-quality terminal log display.

        Each command is shown with its timestamp, duration, return code,
        and any output / error streams in a clean, scannable layout.

        Args:
            max_entries: Maximum number of entries to display (0 = all).
            use_color:  When True (default) ANSI colour codes are embedded.
                        Set to False for plain-text output (e.g. Telegram).

        Returns:
            Formatted terminal log string.
        """
        try:
            if not isinstance(max_entries, int) or max_entries < 0:
                return "Invalid max_entries value. Must be a non-negative integer."

            if max_entries == 0:
                recent_entries = self.terminal_session.entries[:]
            else:
                recent_entries = self.terminal_session.entries[-max_entries:]

            if not recent_entries:
                return self._color("  No terminal history available.", self._DIM, self._GRAY, use_color=use_color)

            W = 62  # content width for the header separator

            output_lines = []

            # Header
            session_short = self.session_id
            if len(session_short) > 24:
                session_short = session_short[:21] + "\u2026"

            header_title = " TERMINAL LOG "
            header_left = f" Session: {session_short} "
            header_right = f" {self._platform} / {self._shell} "

            output_lines.append("")
            output_lines.append(
                self._color(f"\u250c{'\u2500' * W}\u2510", self._DIM, self._GRAY, use_color=use_color)
            )
            output_lines.append(
                self._color("\u2502", self._DIM, self._GRAY, use_color=use_color)
                + self._color(f"{header_title:^{W}}", self._BOLD, self._WHITE, use_color=use_color)
                + self._color("\u2502", self._DIM, self._GRAY, use_color=use_color)
            )
            output_lines.append(
                self._color(f"\u251c{'\u2500' * W}\u2524", self._DIM, self._GRAY, use_color=use_color)
            )
            output_lines.append(
                self._color("\u2502", self._DIM, self._GRAY, use_color=use_color)
                + self._color(f"{header_left:<{W}}", self._CYAN, use_color=use_color)
                + self._color("\u2502", self._DIM, self._GRAY, use_color=use_color)
            )
            output_lines.append(
                self._color("\u2502", self._DIM, self._GRAY, use_color=use_color)
                + self._color(f"{header_right:>{W}}", self._GRAY, use_color=use_color)
                + self._color("\u2502", self._DIM, self._GRAY, use_color=use_color)
            )
            output_lines.append(
                self._color(f"\u2514{'\u2500' * W}\u2518", self._DIM, self._GRAY, use_color=use_color)
            )

            # Working directory
            cwd = str(self._current_directory)
            if len(cwd) > W - 2:
                cwd = "\u2026" + cwd[-(W - 3):]
            output_lines.append(
                self._color("  📁 ", self._YELLOW, use_color=use_color)
                + self._color(cwd, self._DIM, self._GRAY, use_color=use_color)
            )
            output_lines.append("")

            # Entries
            command_count = 0
            i = 0
            while i < len(recent_entries):
                entry = recent_entries[i]

                if entry.entry_type == TerminalEntryType.COMMAND:
                    command_count += 1
                    timestamp_str = time.strftime('%H:%M:%S', time.localtime(entry.timestamp))

                    dur_badge = self._format_duration(entry.duration, use_color=use_color)
                    status = self._status_badge(entry.return_code, use_color=use_color)

                    cmd_display = entry.content
                    max_cmd = W - len(timestamp_str) - len(self._strip_ansi(dur_badge)) - len(self._strip_ansi(status)) - 6
                    if max_cmd < 20:
                        max_cmd = 20
                    if len(cmd_display) > max_cmd:
                        cmd_display = cmd_display[:max_cmd - 1] + "\u2026"

                    output_lines.append(
                        self._color(f"  {timestamp_str} ", self._DIM, self._GRAY, use_color=use_color)
                        + self._color("\u276f ", self._YELLOW, use_color=use_color)
                        + self._color(cmd_display, self._BOLD, self._WHITE, use_color=use_color)
                        + dur_badge
                        + status
                    )

                    j = i + 1
                    has_output = False
                    while j < len(recent_entries) and recent_entries[j].entry_type != TerminalEntryType.COMMAND:
                        output_entry = recent_entries[j]

                        if output_entry.entry_type == TerminalEntryType.OUTPUT and output_entry.content.strip():
                            has_output = True
                            for line in output_entry.content.split('\n'):
                                if line.strip():
                                    truncated = line if len(line) <= W + 4 else line[:W] + "\u2026"
                                    output_lines.append(
                                        self._color("    \u2502 ", self._DIM, self._GRAY, use_color=use_color)
                                        + self._color(truncated, self._GRAY, use_color=use_color)
                                    )

                        elif output_entry.entry_type == TerminalEntryType.ERROR and output_entry.content.strip():
                            has_output = True
                            for line in output_entry.content.split('\n'):
                                if line.strip():
                                    truncated = line if len(line) <= W + 4 else line[:W] + "\u2026"
                                    output_lines.append(
                                        self._color("    \u2502 ", self._DIM, self._RED, use_color=use_color)
                                        + self._color(truncated, self._RED, use_color=use_color)
                                    )

                        j += 1

                    if has_output:
                        output_lines.append(
                            self._color("    \u2514" + "\u2500" * (W - 2), self._DIM, self._GRAY, use_color=use_color)
                        )

                    output_lines.append("")

                    i = j
                else:
                    i += 1

            # Footer
            total = len([e for e in recent_entries if e.entry_type == TerminalEntryType.COMMAND])
            shown = command_count
            if shown != total:
                footer_text = f" showing {shown} of {total} commands "
            else:
                footer_text = f" {shown} command{'s' if shown != 1 else ''} "

            output_lines.append(
                self._color(f"  {footer_text}", self._DIM, self._GRAY, use_color=use_color)
            )
            output_lines.append("")

            return "\n".join(output_lines)

        except Exception as e:
            self.logger.error(f"Error generating terminal log: {e}")
            return f"Error generating terminal log: {str(e)}"

    def get_last_command_output(self) -> str:
        """Get the output of the most recent command"""
        try:
            # Find the most recent command entry
            last_command_entry = None
            for entry in reversed(self.terminal_session.entries):
                if entry.entry_type == TerminalEntryType.COMMAND:
                    last_command_entry = entry
                    break
            
            if not last_command_entry:
                return "No command history available."
            
            # Get output/error entries that came after this command
            command_outputs = []
            for entry in self.terminal_session.entries:
                if (entry.timestamp > last_command_entry.timestamp and 
                    entry.entry_type in [TerminalEntryType.OUTPUT, TerminalEntryType.ERROR]):
                    command_outputs.append(entry.content)
            
            if command_outputs:
                return "\n".join(command_outputs)
            else:
                # If no output entries, check if command executed but had no output
                if last_command_entry.return_code is not None:
                    if last_command_entry.return_code == 0:
                        return f"Command '{last_command_entry.content}' executed successfully with no output."
                    else:
                        return f"Command '{last_command_entry.content}' failed with return code {last_command_entry.return_code}."
                else:
                    return f"Command '{last_command_entry.content}' was logged but execution status unknown."
                
        except Exception as e:
            self.logger.error(f"Failed to get last command output: {e}")
            return f"Error retrieving command output: {str(e)}"
    
    def _persist_history(self) -> None:
        """Persist terminal history to disk with comprehensive error handling"""
        try:
            history_file = self.history_dir / f"{self.session_id}.json"
            
            # Create backup of existing file if it exists
            if history_file.exists():
                backup_file = history_file.with_suffix('.json.bak')
                try:
                    import shutil
                    shutil.copy2(history_file, backup_file)
                except Exception as e:
                    self.logger.warning(f"Failed to create backup of history file: {e}")
            
            # Convert to serializable format
            history_data = self.terminal_session.to_dict()
            
            # Write to file with atomic operation
            temp_file = history_file.with_suffix('.json.tmp')
            try:
                with open(temp_file, 'w', encoding='utf-8', newline='') as f:
                    json.dump(history_data, f, indent=2, ensure_ascii=False)
                
                # Atomic move
                temp_file.replace(history_file)
                
            except Exception as e:
                # Clean up temp file if it exists
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
                raise e
                
        except PermissionError as e:
            self.logger.error(f"Permission denied persisting terminal history: {e}")
        except OSError as e:
            self.logger.error(f"OS error persisting terminal history: {e}")
        except Exception as e:
            self.logger.error(f"Failed to persist terminal history: {e}")
    
    def end_session(self) -> None:
        """End the current session and finalize history"""
        try:
            self.terminal_session.end_time = time.time()
            self._persist_history()
            self.logger.info(f"Terminal session ended: {self.session_id}")
        except Exception as e:
            self.logger.error(f"Error ending session: {e}")
    
    def clear_session(self) -> None:
        """Clear the current terminal session (reset terminal logs)"""
        try:
            # Clear all entries from the current session
            self.terminal_session.entries = []
            
            # Reset session start time
            self.terminal_session.start_time = time.time()
            
            # Reset working directory to home
            self._current_directory = Path.home()
            self.terminal_session.current_working_directory = str(self._current_directory)
            
            # Persist the cleared session
            self._persist_history()
            
            self.logger.info(f"Terminal session cleared: {self.session_id}")
        except Exception as e:
            self.logger.error(f"Error clearing terminal session: {e}")
    
    def load_session(self, session_id: str) -> bool:
        """
        Load a previous session for context
        
        Args:
            session_id: Session ID to load
            
        Returns:
            True if session loaded successfully, False otherwise
        """
        try:
            if not session_id or not isinstance(session_id, str):
                raise ValidationError("Session ID must be a non-empty string")
            
            history_file = self.history_dir / f"{session_id}.json"
            if not history_file.exists():
                self.logger.warning(f"Session file does not exist: {history_file}")
                return False
            
            if not os.access(history_file, os.R_OK):
                self.logger.error(f"Permission denied reading session file: {history_file}")
                return False
            
            with open(history_file, 'r', encoding='utf-8') as f:
                history_data = json.load(f)
            
            # Validate loaded data
            if not isinstance(history_data, dict):
                raise ValidationError("Invalid session data format")
            
            # Reconstruct terminal session
            self.terminal_session = TerminalSession.from_dict(history_data)
            
            # Update current directory
            self._current_directory = Path(self.terminal_session.current_working_directory)
            self.session_id = session_id
            
            self.logger.info(f"Terminal session loaded: {session_id}")
            return True
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in session file {session_id}: {e}")
            return False
        except ValidationError as e:
            self.logger.error(f"Invalid session data for {session_id}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to load terminal session {session_id}: {e}")
            return False
    
    def list_sessions(self) -> List[str]:
        """
        List all available session IDs
        
        Returns:
            List of session IDs
        """
        try:
            sessions = []
            for file_path in self.history_dir.glob("*.json"):
                if file_path.is_file() and not file_path.name.endswith('.bak'):
                    session_id = file_path.stem
                    sessions.append(session_id)
            
            return sorted(sessions)
            
        except Exception as e:
            self.logger.error(f"Failed to list sessions: {e}")
            return []
    
    def cleanup_old_sessions(self, max_sessions: int = 100) -> int:
        """
        Clean up old session files, keeping only the most recent ones
        
        Args:
            max_sessions: Maximum number of sessions to keep
            
        Returns:
            Number of sessions removed
        """
        try:
            # Validate max_sessions
            if not isinstance(max_sessions, int) or max_sessions <= 0:
                self.logger.warning(f"Invalid max_sessions value: {max_sessions}. Must be positive integer.")
                return 0
            
            sessions = self.list_sessions()
            if len(sessions) <= max_sessions:
                return 0
            
            # Sort by modification time and remove oldest
            session_files = []
            for session_id in sessions:
                file_path = self.history_dir / f"{session_id}.json"
                try:
                    mtime = file_path.stat().st_mtime
                    session_files.append((mtime, file_path))
                except Exception as e:
                    self.logger.warning(f"Failed to get modification time for {session_id}: {e}")
            
            # Sort by modification time (oldest first)
            session_files.sort()
            
            # Remove oldest sessions
            removed_count = 0
            for mtime, file_path in session_files[:-max_sessions]:
                try:
                    file_path.unlink()
                    removed_count += 1
                    self.logger.info(f"Removed old session file: {file_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to remove session file {file_path}: {e}")
            
            return removed_count
            
        except Exception as e:
            self.logger.error(f"Failed to cleanup old sessions: {e}")
            return 0
    
    @contextmanager
    def temporary_directory(self, target_dir: Optional[Union[str, Path]] = None):
        """
        Context manager for temporarily changing directory
        
        Args:
            target_dir: Directory to change to (defaults to current directory)
        """
        original_dir = self._current_directory
        try:
            if target_dir:
                target_path = Path(target_dir).resolve()
                if target_path.exists() and target_path.is_dir():
                    self._current_directory = target_path
                    self.terminal_session.current_working_directory = str(self._current_directory)
                else:
                    raise ValueError(f"Target directory does not exist: {target_path}")
            yield
        finally:
            # Always restore original directory
            self._current_directory = original_dir
            self.terminal_session.current_working_directory = str(self._current_directory)

    def cancel_current_command(self) -> None:
        """Terminate the currently running foreground command batch, if any."""
        with self._current_process_lock:
            process = self._current_process
        if process and process.poll() is None:
            self.logger.info("Cancelling active command batch")
            self._terminate_process_tree(process)

    def execute_commands_batch(self, commands: List[str], timeout: Optional[int] = None, cancel_event=None) -> Dict[str, Any]:
        """
        Execute multiple commands in a single batch using the same terminal session.
        All commands are pasted and executed at once, maintaining the same session.
        
        Args:
            commands: List of commands to execute
            timeout: Command timeout in seconds (overrides default)
            
        Returns:
            Dict with execution results including output, error, return code
        """
        if not commands:
            raise ValidationError("Commands list cannot be empty")
        
        # Validate timeout
        if timeout is not None:
            if not isinstance(timeout, (int, float)):
                raise ValidationError("Timeout must be a number")
            if timeout <= 0:
                raise ValidationError("Timeout must be positive")
        else:
            timeout = self.DEFAULT_TIMEOUT
        
        start_time = time.time()
        
        # Combine all commands with newlines for batch execution
        batch_command = "\n".join(commands)
        
        try:
            # Log the batch command entry
            command_entry = TerminalEntry(
                timestamp=start_time,
                entry_type=TerminalEntryType.COMMAND,
                content=batch_command,
                command=batch_command,
                working_directory=str(self._current_directory)
            )
            self.terminal_session.entries.append(command_entry)
            
            self.logger.info(f"Executing batch of {len(commands)} commands", 
                           command_count=len(commands),
                           working_directory=str(self._current_directory))
            
            # Execute all commands in a single shell session. The lock keeps
            # overlapping user requests from running two foreground command
            # batches against the same terminal history at the same time.
            with self._execution_lock:
                result = self._execute_batch_subprocess(commands, timeout, cancel_event=cancel_event)
            
            duration = time.time() - start_time
            
            # Update command entry with execution results
            command_entry.return_code = result.returncode
            command_entry.duration = duration
            
            self.logger.info(
                f"Batch command completed",
                return_code=result.returncode,
                duration=duration,
                success=result.returncode == 0
            )
            
            # Log output entry
            if result.stdout and result.stdout.strip():
                output_entry = TerminalEntry(
                    timestamp=start_time + duration,
                    entry_type=TerminalEntryType.OUTPUT,
                    content=result.stdout,
                    command=batch_command,
                    working_directory=str(self._current_directory),
                    return_code=result.returncode,
                    duration=duration
                )
                self.terminal_session.entries.append(output_entry)
            
            # Log error entry if there's stderr
            if result.stderr and result.stderr.strip():
                error_entry = TerminalEntry(
                    timestamp=start_time + duration,
                    entry_type=TerminalEntryType.ERROR,
                    content=result.stderr,
                    command=batch_command,
                    working_directory=str(self._current_directory),
                    return_code=result.returncode,
                    duration=duration
                )
                self.terminal_session.entries.append(error_entry)
            
            # Persist history
            self._persist_history()
            
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "return_code": result.returncode,
                "duration": duration,
                "working_directory": str(self._current_directory)
            }
            
        except subprocess.TimeoutExpired as e:
            error_msg = f"Batch command timed out after {timeout} seconds"
            self.logger.error(error_msg)
            
            # Log timeout error
            error_entry = TerminalEntry(
                timestamp=time.time(),
                entry_type=TerminalEntryType.ERROR,
                content=error_msg,
                command=batch_command,
                working_directory=str(self._current_directory),
                return_code=-1
            )
            self.terminal_session.entries.append(error_entry)
            self._persist_history()
            
            return {
                "success": False,
                "stdout": "",
                "stderr": error_msg,
                "return_code": -1,
                "duration": timeout,
                "working_directory": str(self._current_directory)
            }
            
        except Exception as e:
            error_msg = f"Batch command execution failed: {str(e)}"
            self.logger.error(error_msg, error_type=type(e).__name__)
            
            # Log execution error
            error_entry = TerminalEntry(
                timestamp=time.time(),
                entry_type=TerminalEntryType.ERROR,
                content=error_msg,
                command=batch_command,
                working_directory=str(self._current_directory),
                return_code=-1
            )
            self.terminal_session.entries.append(error_entry)
            self._persist_history()
            
            return {
                "success": False,
                "stdout": "",
                "stderr": error_msg,
                "return_code": -1,
                "duration": time.time() - start_time,
                "working_directory": str(self._current_directory)
            }

    def _execute_batch_subprocess(self, commands: List[str], timeout: int, cancel_event=None):
        """Execute multiple commands in a single subprocess batch with inactivity-based timeout.
        
        Uses two timeout mechanisms:
        - Overall timeout: the configured command timeout.
        - Inactivity timeout: at least the default timeout, extended to match
          larger configured command timeouts.
        
        Args:
            commands: List of commands to execute
            timeout: Maximum overall timeout in seconds
            
        Returns:
            SimpleNamespace with returncode, stdout, stderr
        """
        import threading
        from types import SimpleNamespace
        
        overall_timeout = timeout
        inactivity_timeout = max(self.DEFAULT_TIMEOUT, overall_timeout * 0.5)
        
        # Join commands with newlines to execute sequentially in same shell.
        # Commands that explicitly end with "&" are treated as persistent
        # background tasks and detached from the temporary shell so they are not
        # killed when the batch shell exits or when Telegram returns a response.
        processed_commands = []
        for cmd in commands:
            cmd_stripped = cmd.strip()
            if cmd_stripped.startswith('cd '):
                parts = cmd_stripped.split(None, 1)
                if len(parts) > 1:
                    target_dir = parts[1].strip()
                    processed_commands.append(f"cd {target_dir} || exit 1")
                else:
                    processed_commands.append("cd ~ || exit 1")
            elif self._is_background_command(cmd_stripped):
                processed_commands.append(self._detach_background_command(cmd_stripped))
            else:
                processed_commands.append(cmd_stripped)
        
        batch_script = "\n".join(processed_commands)
        
        stdout_lines = []
        stderr_lines = []
        last_output_time = [time.time()]
        process_returncode = [None]
        is_timed_out = [False]
        timeout_reason = [""]
        
        def read_output(pipe, lines_list, is_stderr=False):
            """Read output from pipe continuously"""
            try:
                for line in iter(pipe.readline, b''):
                    if is_timed_out[0]:
                        break
                    decoded_line = line.decode('utf-8', errors='replace')
                    lines_list.append(decoded_line)
                    last_output_time[0] = time.time()
            except Exception:
                pass
            finally:
                pipe.close()
        
        import tempfile
        
        # Write batch script to temp file to avoid shell=True
        script_suffix = '.bat' if self._platform == "windows" else '.sh'
        script_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix=script_suffix, delete=False, 
                                              dir=None) as tmp_script:
                if self._platform == "windows":
                    tmp_script.write(f"@echo off\n{batch_script}\n")
                else:
                    tmp_script.write(f"#!/bin/bash\n{batch_script}\n")
                script_path = tmp_script.name
            
            # Make script executable on Unix
            if self._platform != "windows":
                os.chmod(script_path, 0o755)
            
            process = None
            # Start process with Popen for streaming output (shell=False for security)
            if self._platform == "windows":
                process = subprocess.Popen(
                    ['cmd.exe', '/c', script_path],
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    cwd=str(self._current_directory)
                )
            else:
                process = subprocess.Popen(
                    ['bash', script_path],
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    cwd=str(self._current_directory),
                    start_new_session=True
                )
            
            with self._current_process_lock:
                self._current_process = process
            # Start threads to read output
            stdout_thread = threading.Thread(target=read_output, args=(process.stdout, stdout_lines, False))
            stderr_thread = threading.Thread(target=read_output, args=(process.stderr, stderr_lines, True))
            stdout_thread.daemon = True
            stderr_thread.daemon = True
            stdout_thread.start()
            stderr_thread.start()
            
            start_time = time.time()
            
            # Monitor process with dual timeout
            while process.poll() is None:
                current_time = time.time()
                elapsed = current_time - start_time
                since_last_output = current_time - last_output_time[0]
                
                if elapsed > overall_timeout:
                    is_timed_out[0] = True
                    timeout_reason[0] = f"Overall timeout exceeded ({overall_timeout}s)"
                    self._terminate_process_tree(process)
                    break
                
                if since_last_output >= inactivity_timeout:
                    is_timed_out[0] = True
                    timeout_reason[0] = f"No output for {inactivity_timeout:.0f}s - process appears stuck"
                    self._terminate_process_tree(process)
                    break
                
                if cancel_event and cancel_event.is_set():
                    is_timed_out[0] = True
                    timeout_reason[0] = "Cancelled by newer user request"
                    self._terminate_process_tree(process)
                    break

                time.sleep(0.1)
            
            # Wait for threads to finish
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            
            # Get final return code
            if is_timed_out[0]:
                process_returncode[0] = -1
            else:
                process_returncode[0] = process.poll() or 0
            
            stdout = "".join(stdout_lines)
            stderr = "".join(stderr_lines)
            
            if is_timed_out[0]:
                stderr += f"\n[TIMEOUT: {timeout_reason[0]}]"
                self.logger.warning(f"Batch command timed out: {timeout_reason[0]}")
            
            result = SimpleNamespace(
                returncode=process_returncode[0],
                stdout=stdout,
                stderr=stderr
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"Batch execution failed: {e}")
            return SimpleNamespace(
                returncode=-1,
                stdout="",
                stderr=f"Execution failed: {str(e)}"
            )
        finally:
            with self._current_process_lock:
                if process is not None and self._current_process is process:
                    self._current_process = None
            # Clean up temp script file
            try:
                if script_path is not None and os.path.exists(script_path):
                    os.unlink(script_path)
            except Exception:
                pass

    def _is_background_command(self, command: str) -> bool:
        """Return True when a command explicitly requests background execution."""
        return command.rstrip().endswith('&') and not command.rstrip().endswith('&&')

    def _detach_background_command(self, command: str) -> str:
        """Detach an explicit background command so it survives batch shell exit.

        The autonomous loop runs commands from a temporary shell. Plain
        ``cmd &`` jobs can keep inherited pipes open or receive SIGHUP when that
        shell exits, which makes long-running services appear to terminate
        unpredictably, especially in Telegram mode. This wrapper redirects
        output to a per-session log, starts the process with nohup, and prints
        the PID for later inspection.
        """
        inner_command = command.rstrip()[:-1].strip()
        if not inner_command:
            return command

        if self._platform == "windows":
            return f'start /b cmd /c "{inner_command}"'

        background_dir = self.history_dir / "background_jobs"
        background_dir.mkdir(parents=True, exist_ok=True)
        log_path = background_dir / f"{self.session_id}_{int(time.time() * 1000)}.log"
        quoted_inner = shlex.quote(inner_command)
        quoted_log = shlex.quote(str(log_path))
        return (
            f"nohup bash -lc {quoted_inner} > {quoted_log} 2>&1 < /dev/null & "
            f"echo 'Started background process PID:' $! '(log: {log_path})'"
        )

    def _terminate_process_tree(self, process) -> None:
        """Terminate the batch process and its foreground children."""
        try:
            if self._platform != "windows":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            time.sleep(1)
            if process.poll() is None:
                if self._platform != "windows":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
        except ProcessLookupError:
            pass
        except Exception as e:
            self.logger.warning(f"Failed to terminate process tree cleanly: {e}")
            try:
                process.kill()
            except Exception:
                pass


# Global terminal history instance for easy access
_global_terminal_history: Optional[TerminalHistory] = None


def get_terminal_history() -> TerminalHistory:
    """Get global terminal history instance"""
    global _global_terminal_history
    if _global_terminal_history is None:
        _global_terminal_history = TerminalHistory()
    return _global_terminal_history


def execute_command(command: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    """
    Global function to execute command and preserve history
    
    Args:
        command: Command to execute
        timeout: Optional timeout in seconds
        
    Returns:
        Dict with execution results
        
    Example:
        result = execute_command("ls -la")
        if result["success"]:
            print(result["stdout"])
    """
    return get_terminal_history().execute_command(command, timeout)


def display_terminal_log(max_entries: int = 20) -> str:
    """
    Global function to display terminal log
    
    Args:
        max_entries: Maximum number of entries to display
        
    Returns:
        Formatted terminal log string
        
    Example:
        print(display_terminal_log())
    """
    return get_terminal_history().display_terminal_log(max_entries)


def get_last_command_output() -> str:
    """
    Global function to get last command output
    
    Returns:
        Output of the most recent command
        
    Example:
        print(get_last_command_output())
    """
    return get_terminal_history().get_last_command_output()
