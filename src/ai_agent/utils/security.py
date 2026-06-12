"""
Security Utilities for VEXIS-CLI
Enhanced security features including sandboxing and sensitive data masking
Configuration via config.yaml or environment variables
"""

import re
import os
import subprocess
import tempfile
import shutil
from typing import List, Dict, Set, Optional, Pattern
from dataclasses import dataclass
from pathlib import Path

from .logger import get_logger
from .config import SecurityConfig as ConfigSecurityConfig, load_config


# Re-export SecurityConfig from config for backward compatibility
SecurityConfig = ConfigSecurityConfig


# Sensitive patterns for masking in logs
SENSITIVE_PATTERNS: Dict[str, Pattern] = {
    "api_key": re.compile(r'(api[_-]?key\s*[:=]\s*)["\']?[^"\'\s]+["\']?', re.IGNORECASE),
    "password": re.compile(r'(password\s*[:=]\s*)["\']?[^"\'\s]+["\']?', re.IGNORECASE),
    "token": re.compile(r'(token\s*[:=]\s*)["\']?[^"\'\s]+["\']?', re.IGNORECASE),
    "secret": re.compile(r'(secret\s*[:=]\s*)["\']?[^"\'\s]+["\']?', re.IGNORECASE),
    "private_key": re.compile(r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----', re.IGNORECASE),
    "bearer_token": re.compile(r'Bearer\s+[a-zA-Z0-9_-]+', re.IGNORECASE),
    "aws_key": re.compile(r'AKIA[0-9A-Z]{16}'),
    "github_token": re.compile(r'gh[pousr]_[A-Za-z0-9_]{36,}'),
    "slack_token": re.compile(r'xox[baprs]-[0-9a-zA-Z-]+'),
}

# Security configuration settings
# Note: Command blocking is DISABLED by default for user freedom
# Enable via: config.yaml, environment variables, or CLI arguments

# Dangerous commands that should be blocked (when blocking is enabled)
DANGEROUS_COMMANDS: Set[str] = {
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "dd if=/dev/zero of=/dev/sd",
    "mkfs.ext4 /dev/sd",
    ":(){ :|:& };:",  # Fork bomb
    "> /dev/sda",
    "mv / /dev/null",
}

# Commands requiring confirmation (when confirmation is enabled)
CONFIRMATION_COMMANDS: Set[str] = {
    "rm -rf",
    "rm -r",
    "rmdir",
    "drop",
    "delete",
    "truncate",
    "chmod 777",
    "chown -R",
    "kill -9",
}


@dataclass
class SecurityCheckResult:
    """Result of security check"""
    is_safe: bool
    requires_confirmation: bool
    blocked_commands: List[str]
    warning_commands: List[str]
    masked_output: str
    reason: Optional[str] = None


class SensitiveDataMasker:
    """Masks sensitive data in logs and output"""
    
    MASK_STRING = "[REDACTED]"
    
    def __init__(self):
        self.logger = get_logger("security.masker")
        self.patterns = SENSITIVE_PATTERNS
    
    def mask(self, text: str) -> str:
        """Mask sensitive data in text"""
        if not text:
            return text
        
        masked = text
        mask_count = 0
        
        for pattern_name, pattern in self.patterns.items():
            matches = list(pattern.finditer(masked))
            # Replace from end to avoid index shifting
            for match in reversed(matches):
                start, end = match.span()
                # Only mask the sensitive part, keep the label
                if pattern_name in ["api_key", "password", "token", "secret"]:
                    # Find where the value starts (after the separator)
                    label_end = start
                    for i in range(start, end):
                        if masked[i] in [':', '=']:
                            label_end = i + 1
                            break
                    # Mask only the value part
                    masked = masked[:label_end] + self.MASK_STRING + masked[end:]
                else:
                    masked = masked[:start] + self.MASK_STRING + masked[end:]
                mask_count += 1
        
        if mask_count > 0:
            self.logger.debug(f"Masked {mask_count} sensitive values")
        
        return masked
    
    def mask_dict(self, data: Dict) -> Dict:
        """Recursively mask sensitive data in dictionary"""
        result = {}
        for key, value in data.items():
            # Check if key suggests sensitive data
            if any(sensitive in key.lower() for sensitive in ['key', 'password', 'token', 'secret', 'credential']):
                result[key] = self.MASK_STRING
            elif isinstance(value, str):
                result[key] = self.mask(value)
            elif isinstance(value, dict):
                result[key] = self.mask_dict(value)
            elif isinstance(value, list):
                result[key] = [self.mask(str(item)) if isinstance(item, str) else item for item in value]
            else:
                result[key] = value
        return result


class CommandSecurityChecker:
    """Security checker for terminal commands (configurable)"""
    
    def __init__(self, config: Optional[SecurityConfig] = None):
        self.logger = get_logger("security.checker")
        self.config = config or SecurityConfig()  # Default: all disabled
        self.dangerous = DANGEROUS_COMMANDS
        self.confirmation_required = CONFIRMATION_COMMANDS
        self.masker = SensitiveDataMasker()
    
    def check_command(self, command: str) -> SecurityCheckResult:
        """
        Check if a command is safe to execute based on configuration
        
        Returns:
            SecurityCheckResult with safety status based on config
        """
        if not command or not command.strip():
            return SecurityCheckResult(
                is_safe=False,
                requires_confirmation=False,
                blocked_commands=[],
                warning_commands=[],
                masked_output="",
                reason="Empty command"
            )
        
        command_lower = command.lower().strip()
        blocked = []
        warnings = []
        
        # Check for dangerous commands (only if blocking is enabled)
        if self.config.enable_command_blocking:
            for dangerous in self.dangerous:
                if dangerous in command_lower:
                    blocked.append(dangerous)
                    self.logger.warning(f"Blocked dangerous command: {dangerous}")
        
        # Check for commands requiring confirmation (only if confirmation is enabled)
        if self.config.enable_confirmation_prompts:
            for confirm_cmd in self.confirmation_required:
                if confirm_cmd in command_lower:
                    warnings.append(confirm_cmd)
        
        # Check for sudo (only if warning is enabled)
        if self.config.enable_sudo_warning:
            if command_lower.startswith("sudo "):
                warnings.append("sudo")
        
        # Check for pipe to shell (only if warning is enabled)
        if self.config.enable_shell_pipe_warning:
            if "| bash" in command_lower or "| sh" in command_lower:
                warnings.append("pipe_to_shell")
        
        # Mask any sensitive data in the command for logging
        masked_command = self.masker.mask(command)
        
        is_safe = len(blocked) == 0
        requires_confirmation = len(warnings) > 0 and len(blocked) == 0
        
        reason = None
        if blocked:
            reason = f"Blocked dangerous command(s): {', '.join(blocked)}"
        elif warnings:
            reason = f"Command requires confirmation due to: {', '.join(warnings)}"
        
        return SecurityCheckResult(
            is_safe=is_safe,
            requires_confirmation=requires_confirmation,
            blocked_commands=blocked,
            warning_commands=warnings,
            masked_output=masked_command,
            reason=reason
        )
    
    def check_commands(self, commands: List[str]) -> List[SecurityCheckResult]:
        """Check multiple commands"""
        return [self.check_command(cmd) for cmd in commands]


class SandboxManager:
    """
    Manages sandboxed execution environment
    
    Note: Full sandboxing requires external tools like firejail, nsjail, or chroot.
    This class provides a best-effort implementation using available tools.
    """
    
    def __init__(self):
        self.logger = get_logger("security.sandbox")
        self.temp_dir: Optional[Path] = None
        self.available_sandbox = self._detect_sandbox_tool()
    
    def _detect_sandbox_tool(self) -> Optional[str]:
        """Detect available sandboxing tool"""
        tools = ["firejail", "nsjail", "bubblewrap", "chroot"]
        
        for tool in tools:
            if shutil.which(tool):
                self.logger.info(f"Detected sandbox tool: {tool}")
                return tool
        
        self.logger.warning("No sandboxing tool detected. Running commands in unrestricted environment.")
        return None
    
    def create_temp_workspace(self) -> Path:
        """Create temporary workspace for sandboxed execution"""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="vexis_sandbox_"))
        self.logger.info(f"Created sandbox workspace: {self.temp_dir}")
        return self.temp_dir
    
    def wrap_command(self, command: str, workspace: Optional[Path] = None) -> str:
        """
        Wrap command with sandbox restrictions
        
        Args:
            command: Original command
            workspace: Working directory to restrict to
            
        Returns:
            Sandboxed command or original if no sandbox available
        """
        if not self.available_sandbox:
            self.logger.warning("No sandbox tool available, running command without restrictions")
            return command
        
        work_dir = workspace or self.temp_dir or Path.cwd()
        
        if self.available_sandbox == "firejail":
            # Firejail: restrict filesystem access
            return f'firejail --noprofile --whitelist={work_dir} --private={work_dir} --quiet {command}'
        
        elif self.available_sandbox == "bubblewrap":
            # Bubblewrap: create minimal sandbox
            return (
                f'bwrap --unshare-all --die-with-parent '
                f'--ro-bind / / --bind {work_dir} {work_dir} '
                f'--chdir {work_dir} {command}'
            )
        
        elif self.available_sandbox == "nsjail":
            # Nsjail: more comprehensive sandboxing
            return f'nsjail -Mo --chroot {work_dir} --disable_proc --quiet -- {command}'
        
        else:
            return command
    
    def cleanup(self):
        """Clean up temporary workspace"""
        if self.temp_dir and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
                self.logger.info(f"Cleaned up sandbox workspace: {self.temp_dir}")
            except Exception as e:
                self.logger.error(f"Failed to cleanup sandbox: {e}")
            finally:
                self.temp_dir = None


class SecurityManager:
    """Main security manager combining all security features"""
    
    def __init__(
        self,
        config: Optional[SecurityConfig] = None,
        enable_sandbox: bool = True,
        auto_mask_logs: bool = True
    ):
        self.logger = get_logger("security.manager")
        self.config = config or SecurityConfig()
        self.masker = SensitiveDataMasker()
        self.checker = CommandSecurityChecker(config=self.config)
        self.sandbox = SandboxManager() if (enable_sandbox and self.config.enable_sandbox) else None
        self.auto_mask = auto_mask_logs
        
        self.logger.info("Security manager initialized",
                        command_blocking=self.config.enable_command_blocking,
                        confirmation_prompts=self.config.enable_confirmation_prompts,
                        sandbox_enabled=(self.sandbox is not None),
                        auto_mask=auto_mask_logs)
    
    def validate_and_prepare(self, commands: List[str]) -> tuple[List[str], List[str]]:
        """
        Validate commands and prepare them for execution
        
        Returns:
            Tuple of (safe_commands, blocked_commands)
        """
        safe_commands = []
        blocked = []
        
        for cmd in commands:
            result = self.checker.check_command(cmd)
            
            if not result.is_safe:
                blocked.append(result.masked_output)
                self.logger.error(f"Command blocked: {result.reason}")
                continue
            
            if result.requires_confirmation:
                self.logger.warning(f"Command requires confirmation: {result.masked_output}")
                # In non-interactive mode, we might want to skip or flag these
            
            # Apply sandbox if enabled
            if self.sandbox:
                cmd = self.sandbox.wrap_command(cmd)
            
            safe_commands.append(cmd)
        
        return safe_commands, blocked
    
    def mask_for_logging(self, text: str) -> str:
        """Mask sensitive data before logging"""
        if not self.auto_mask:
            return text
        return self.masker.mask(text)
    
    def cleanup(self):
        """Cleanup security resources"""
        if self.sandbox:
            self.sandbox.cleanup()


# Global security manager instance
_global_security_manager: Optional[SecurityManager] = None


def get_security_config_from_env() -> SecurityConfig:
    """
    Load security configuration from environment variables
    
    Environment variables:
    - VEXIS_ENABLE_COMMAND_BLOCKING: true/false
    - VEXIS_ENABLE_CONFIRMATION_PROMPTS: true/false
    - VEXIS_ENABLE_SUDO_WARNING: true/false
    - VEXIS_ENABLE_SHELL_PIPE_WARNING: true/false
    - VEXIS_ENABLE_SANDBOX: true/false
    """
    def env_bool(name: str, default: bool) -> bool:
        value = os.getenv(name, "").lower()
        return value in ("true", "1", "yes", "on") if value else default
    
    return SecurityConfig(
        enable_command_blocking=env_bool("VEXIS_ENABLE_COMMAND_BLOCKING", False),
        enable_confirmation_prompts=env_bool("VEXIS_ENABLE_CONFIRMATION_PROMPTS", False),
        enable_sudo_warning=env_bool("VEXIS_ENABLE_SUDO_WARNING", False),
        enable_shell_pipe_warning=env_bool("VEXIS_ENABLE_SHELL_PIPE_WARNING", False),
        enable_sandbox=env_bool("VEXIS_ENABLE_SANDBOX", True)
    )


def get_security_manager(
    config: Optional[SecurityConfig] = None,
    enable_sandbox: bool = True,
    auto_mask_logs: bool = True
) -> SecurityManager:
    """
    Get global security manager instance
    
    Config priority:
    1. Explicit config parameter
    2. Environment variables (VEXIS_*)
    3. config.yaml (via load_config())
    4. Default values (all disabled)
    """
    global _global_security_manager
    
    if _global_security_manager is None:
        # Priority: explicit > env > config.yaml > default
        if config is None:
            # Try environment variables first
            env_config = get_security_config_from_env()
            
            # Check if any env vars were set
            if any([
                os.getenv("VEXIS_ENABLE_COMMAND_BLOCKING"),
                os.getenv("VEXIS_ENABLE_CONFIRMATION_PROMPTS"),
                os.getenv("VEXIS_ENABLE_SUDO_WARNING"),
                os.getenv("VEXIS_ENABLE_SHELL_PIPE_WARNING"),
            ]):
                config = env_config
            else:
                # Fall back to config.yaml
                try:
                    main_config = load_config()
                    config = main_config.security
                except Exception:
                    config = SecurityConfig()  # Default (all disabled)
        
        _global_security_manager = SecurityManager(config, enable_sandbox, auto_mask_logs)
    
    return _global_security_manager


def mask_sensitive_data(text: str) -> str:
    """Convenience function to mask sensitive data"""
    masker = SensitiveDataMasker()
    return masker.mask(text)


def check_command_safety(
    command: str,
    config: Optional[SecurityConfig] = None
) -> SecurityCheckResult:
    """
    Check command safety with optional config
    If no config provided, uses settings from environment or config.yaml
    """
    if config is None:
        # Try environment first, then config.yaml
        env_config = get_security_config_from_env()
        if any([
            os.getenv("VEXIS_ENABLE_COMMAND_BLOCKING"),
            os.getenv("VEXIS_ENABLE_CONFIRMATION_PROMPTS"),
        ]):
            config = env_config
        else:
            try:
                main_config = load_config()
                config = main_config.security
            except Exception:
                config = SecurityConfig()
    
    checker = CommandSecurityChecker(config=config)
    return checker.check_command(command)


def create_secure_config(
    enable_blocking: bool = False,
    enable_confirmation: bool = False,
    enable_sudo_warning: bool = False,
    enable_shell_pipe_warning: bool = False,
    enable_sandbox: bool = True
) -> SecurityConfig:
    """
    Create a security configuration programmatically
    
    For user-friendly configuration, prefer:
    1. config.yaml -> security section
    2. Environment variables (VEXIS_*)
    
    Example:
        # Strict mode (blocks dangerous commands)
        config = create_secure_config(
            enable_blocking=True,
            enable_confirmation=True
        )
        
        # Permissive mode (default - allows all commands)
        config = create_secure_config()
    """
    return SecurityConfig(
        enable_command_blocking=enable_blocking,
        enable_confirmation_prompts=enable_confirmation,
        enable_sudo_warning=enable_sudo_warning,
        enable_shell_pipe_warning=enable_shell_pipe_warning,
        enable_sandbox=enable_sandbox
    )
