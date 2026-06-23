"""
Configuration management for AI Agent System
Zero-defect policy: comprehensive configuration with validation
"""

import os
import yaml
import json
import threading
import tempfile
import shutil
from typing import Dict, Any, Optional, Union
from pathlib import Path
from dataclasses import dataclass, field, fields, asdict
from .exceptions import ConfigurationError, ValidationError

# ---------------------------------------------------------------------------
# Cross-platform config path resolution
# ---------------------------------------------------------------------------

def _resolve_config_path(config_path: Optional[Union[str, Path]] = None) -> Path:
    """Resolve config.yaml path to a single canonical location. All modules
    must use this to ensure they read/write the same file.
    Priority: explicit path > env CLIO_CONFIG > project root.
    """
    if config_path is not None:
        p = Path(config_path)
        if p.is_dir():
            p = p / "config.yaml"
        return p.resolve()
    env_path = os.environ.get("CLIO_CONFIG")
    if env_path:
        return Path(env_path).resolve()
    return (Path(__file__).resolve().parents[3] / "config.yaml")


# ---------------------------------------------------------------------------
# Atomic writes with threading lock
# ---------------------------------------------------------------------------

_config_file_lock = threading.Lock()


def _atomic_write_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Write YAML atomically via temp file + rename, with threading lock.
    """
    _config_file_lock.acquire()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            suffix=".yaml", prefix=".config_", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                yaml.dump(data, tmp_f, default_flow_style=False,
                          sort_keys=False, allow_unicode=True)
            shutil.move(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    finally:
        _config_file_lock.release()



def _get_ollama_model_from_settings() -> str:
    """Get the Ollama model from settings manager, with fallback to default"""
    try:
        from .settings_manager import get_settings_manager
        settings = get_settings_manager()
        model = settings.get_ollama_model()
        return model if model else "qwen3.5:2b"
    except Exception:
        return "qwen3.5:2b"


@dataclass
class LoggingConfig:
    """Logging configuration"""
    level: str = "INFO"
    file: Optional[str] = None
    json_format: bool = False
    console: bool = True
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


@dataclass
class APIConfig:
    """API configuration"""
    # Local Ollama configuration
    local_endpoint: str = "http://localhost:11434"
    local_model: str = "llama3.2:latest"
    
    # OpenRouter configuration
    openrouter_api_key: str = ""
    
    # API keys for multiple providers
    api_keys: Dict[str, str] = field(default_factory=dict)
    
    # Model configurations for multiple providers
    models: Dict[str, str] = field(default_factory=dict)
    
    # General settings
    timeout: int = 30
    max_retries: int = 3
    retry_delay: float = 1.0
    preferred_provider: str = ""  # Must be explicitly set by user


@dataclass
class SecurityConfig:
    """Security configuration"""
    allowed_commands: list = field(default_factory=lambda: [
        "cli_command", "end", "regenerate_step"
    ])
    sanitize_text_input: bool = True
    validate_file_paths: bool = True
    max_text_length: int = 1000
    command_timeout: int = 600
    
    # Command blocking settings (default: disabled for user freedom)
    enable_command_blocking: bool = False      # Block dangerous commands like 'rm -rf /'
    enable_confirmation_prompts: bool = False  # Require confirmation for risky commands
    enable_sudo_warning: bool = False          # Show warning for sudo commands
    enable_shell_pipe_warning: bool = False    # Show warning for 'curl ... | bash'
    enable_sandbox: bool = True                # Use sandbox tools (firejail, etc.) when available


@dataclass
class PerformanceConfig:
    """Performance configuration"""
    max_concurrent_tasks: int = 1
    task_timeout: int = 7200
    command_timeout: int = 600
    api_timeout: int = 30
    memory_limit_mb: int = 1024


@dataclass
class EngineConfig:
    """Five-phase engine configuration"""
    click_delay: float = 0.1
    typing_delay: float = 0.05
    scroll_duration: float = 0.5
    drag_duration: float = 0.3
    screenshot_quality: int = 95
    screenshot_format: str = "PNG"
    max_task_retries: int = 3
    max_command_retries: int = 3
    command_timeout: int = 600
    task_timeout: int = 7200
    max_rebuilds_per_session: int = 3


@dataclass
class TelegramConfig:
    """Telegram bot configuration"""
    enabled: bool = False
    bot_token: str = ""
    bot_username: str = ""
    bot_name: str = ""
    api_id: int = 0
    api_hash: str = ""
    session_name: str = ""
    contacts: list = field(default_factory=list)
    authorized_users: list = field(default_factory=list)
    output_recipients: list = field(default_factory=list)
    enable_input_listener: bool = False
    send_phase2_end_updates: bool = False
    allowed_user_ids: list = field(default_factory=list)
    max_history_length: int = 50


@dataclass
class ExecutionConfig:
    """Execution mode configuration"""
    mode: str = "auto"  # "auto", "normal", or "telegram"
    safety_mode: bool = True
    dry_run: bool = False
    verify_commands: bool = True
    command_timeout: int = 600
    task_timeout: int = 7200
    max_iterations: int = 500
    auto_recovery: bool = True
    enable_phase2_summarization: bool = True
    show_thought_log: bool = True  # Print agent's thought/activity log to terminal


@dataclass
class CacheConfig:
    """Cache configuration"""
    enabled: bool = False
    max_size: int = 1000
    ttl: int = 3600
    persist_to_disk: bool = False


@dataclass
class CostConfig:
    """Cost management configuration"""
    daily_budget: Optional[float] = None
    monthly_budget: Optional[float] = None
    per_request_budget: Optional[float] = None
    warning_threshold: float = 0.8
    critical_threshold: float = 0.95


@dataclass
class UserConfig:
    """User preferences configuration"""
    name: str = ""
    preferred_style: str = "detailed"
    auto_confirm: bool = False
    show_progress: bool = True
    attributes: Dict[str, str] = field(default_factory=dict)
    """Free-form personal attributes (5-100 items).
    
    Examples: device, location, skills, hobbies, language, occupation, etc.
    Users define their own keys and values during onboarding.
    """


@dataclass
class Config:
    """Main configuration class"""
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    api: APIConfig = field(default_factory=APIConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    performance: PerformanceConfig = field(default_factory=PerformanceConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    user: UserConfig = field(default_factory=UserConfig)
    
    # Platform-specific settings
    platform: Dict[str, Any] = field(default_factory=dict)
    
    # Custom settings
    custom: Dict[str, Any] = field(default_factory=dict)
    
    # Custom System Prompts (Amore configuration)
    custom_system_prompt: str = ""  # Custom prompt to inject into Phase 1 system prompt
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by dot notation key"""
        keys = key.split('.')
        value = self
        
        try:
            for k in keys:
                if hasattr(value, k):
                    value = getattr(value, k)
                elif isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            return value
        except (AttributeError, KeyError):
            return default


class ConfigManager:
    """Configuration manager with validation and environment support"""
    
    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        self.config_path = Path(config_path) if config_path else None
        self._config: Optional[Config] = None
        self._raw_config: Dict[str, Any] = {}
    
    def load_config(self) -> Config:
        """Load configuration from file and environment"""
        if self._config is None:
            self._load_raw_config()
            self._config = self._create_config_from_raw()
            self._validate_config()
        return self._config
    
    def _get_default_raw_config(self) -> Dict[str, Any]:
        """Build a complete default config dict from all dataclass field defaults.

        This ensures save_config() never writes an incomplete file.
        """
        import dataclasses as _dc
        def _dc_defaults(cls):
            result = {}
            for fld in fields(cls):
                if fld.default is not _dc.MISSING:
                    result[fld.name] = fld.default
                elif fld.default_factory is not _dc.MISSING:
                    result[fld.name] = fld.default_factory()
            return result
        return {
            "logging": _dc_defaults(LoggingConfig),
            "api": _dc_defaults(APIConfig),
            "security": _dc_defaults(SecurityConfig),
            "performance": _dc_defaults(PerformanceConfig),
            "engine": _dc_defaults(EngineConfig),
            "telegram": _dc_defaults(TelegramConfig),
            "execution": _dc_defaults(ExecutionConfig),
            "cache": _dc_defaults(CacheConfig),
            "cost": _dc_defaults(CostConfig),
            "user": _dc_defaults(UserConfig),
            "custom_system_prompt": "",
        }

    def _load_raw_config(self):
        """Load raw configuration from file"""
        # Start with complete defaults derived from dataclass field defaults
        self._raw_config = self._get_default_raw_config()

        # Load from file if exists
        if self.config_path and self.config_path.exists():
            try:
                if self.config_path.suffix.lower() in ['.yaml', '.yml']:
                    with open(self.config_path, 'r') as f:
                        file_config = yaml.safe_load(f)
                elif self.config_path.suffix.lower() == '.json':
                    with open(self.config_path, 'r') as f:
                        file_config = json.load(f)
                else:
                    raise ConfigurationError(
                        f"Unsupported config file format: {self.config_path.suffix}",
                        config_file=str(self.config_path)
                    )

                # Handle empty/whitespace-only YAML files (safe_load returns None)
                if file_config is None:
                    file_config = {}
                if not isinstance(file_config, dict):
                    raise ConfigurationError(
                        f"Config file must contain a YAML mapping, got {type(file_config).__name__}",
                        config_file=str(self.config_path)
                    )

                # Replace None values for section keys with empty dicts so
                # downstream .get() calls on sections don't crash.
                for _sk, _sv in list(file_config.items()):
                    if _sv is None:
                        file_config[_sk] = {}

                # Merge with default config
                self._merge_config(self._raw_config, file_config)

            except ConfigurationError:
                raise
            except Exception as e:
                raise ConfigurationError(
                    f"Failed to load config file: {e}",
                    config_file=str(self.config_path)
                )

        # Override with environment variables
        self._load_from_environment()
    
    def _merge_config(self, base: Dict[str, Any], override: Dict[str, Any]):
        """Recursively merge configuration dictionaries"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_config(base[key], value)
            else:
                base[key] = value
    
    def _load_from_environment(self):
        """Load configuration from environment variables"""
        env_mappings = {
            "AI_AGENT_LOG_LEVEL": ("logging", "level"),
            "AI_AGENT_LOG_FILE": ("logging", "file"),
            "AI_AGENT_LOG_JSON": ("logging", "json_format"),
            "AI_AGENT_LOCAL_ENDPOINT": ("api", "local_endpoint"),
            "AI_AGENT_LOCAL_MODEL": ("api", "local_model"),
            "AI_AGENT_PREFERRED_PROVIDER": ("api", "preferred_provider"),
            "AI_AGENT_API_TIMEOUT": ("api", "timeout"),
            "AI_AGENT_API_MAX_RETRIES": ("api", "max_retries"),
            "AI_AGENT_COMMAND_TIMEOUT": ("security", "command_timeout"),
            "AI_AGENT_MAX_CONCURRENT_TASKS": ("performance", "max_concurrent_tasks"),
            "AI_AGENT_TASK_TIMEOUT": ("performance", "task_timeout"),
        }
        
        for env_var, (section, key) in env_mappings.items():
            value = os.getenv(env_var)
            if value is not None:
                # Type conversion
                if key in ["timeout", "max_retries", "command_timeout", "max_concurrent_tasks", "task_timeout"]:
                    try:
                        if '.' in value:
                            value = float(value)
                        else:
                            value = int(value)
                    except ValueError:
                        continue
                elif key in ["json_format", "console", "enabled"]:
                    value = value.lower() in ['true', '1', 'yes', 'on']
                
                # Set in config
                if section not in self._raw_config:
                    self._raw_config[section] = {}
                self._raw_config[section][key] = value
    
    def _create_config_from_raw(self) -> Config:
        """Create Config object from raw configuration"""
        try:
            # Get API config dict
            api_config_dict = self._raw_config.get("api", {})
            
            def _filter_dc_dict(dc_cls, raw):
                import dataclasses
                valid = {f.name for f in dataclasses.fields(dc_cls)}
                return {k: v for k, v in raw.items() if k in valid}

            return Config(
                logging=LoggingConfig(**_filter_dc_dict(LoggingConfig, self._raw_config.get("logging", {}))),
                api=APIConfig(**_filter_dc_dict(APIConfig, api_config_dict)),
                security=SecurityConfig(**_filter_dc_dict(SecurityConfig, self._raw_config.get("security", {}))),
                performance=PerformanceConfig(**_filter_dc_dict(PerformanceConfig, self._raw_config.get("performance", {}))),
                engine=EngineConfig(**_filter_dc_dict(EngineConfig, self._raw_config.get("engine", {}))),
                telegram=TelegramConfig(**_filter_dc_dict(TelegramConfig, self._raw_config.get("telegram", {}))),
                execution=ExecutionConfig(**_filter_dc_dict(ExecutionConfig, self._raw_config.get("execution", {}))),
                cache=CacheConfig(**_filter_dc_dict(CacheConfig, self._raw_config.get("cache", {}))),
                cost=CostConfig(**_filter_dc_dict(CostConfig, self._raw_config.get("cost", {}))),
                user=UserConfig(**_filter_dc_dict(UserConfig, self._raw_config.get("user", {}))),
                platform=self._raw_config.get("platform", {}),
                custom=self._raw_config.get("custom", {}),
                custom_system_prompt=self._raw_config.get("custom_system_prompt", ""),
            )
        except Exception as e:
            raise ConfigurationError(
                f"Failed to create config object: {e}",
                config_key="config_creation"
            )
    
    def _validate_config(self):
        """Validate configuration"""
        # Basic validation - no complex schema validation needed
        if not isinstance(self._raw_config, dict):
            raise ConfigurationError("Configuration must be a dictionary")
    
    def save_config(self, config_path: Optional[Union[str, Path]] = None):
        """Save current configuration state to config.yaml.

        FIX #20: Previously a no-op, this now actually persists the
        in-memory configuration (including any changes made via set()).
        Uses atomic write to prevent corruption on concurrent access.
        """
        _path = Path(config_path) if config_path else self._config_path
        if _path is None:
            raise ConfigurationError(
                "Cannot save config: no config_path specified and no default set",
                config_key="save_config"
            )
        try:
            _atomic_write_yaml(_path, self._raw_config)
        except ConfigurationError:
            raise
        except Exception as e:
            raise ConfigurationError(
                f"Failed to save config to {_path}: {e}",
                config_key="save_config"
            )
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by dot notation key"""
        if not self._config:
            self.load_config()
        
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                if hasattr(value, k):
                    value = getattr(value, k)
                elif isinstance(value, dict) and k in value:
                    value = value[k]
                else:
                    return default
            return value
        except (AttributeError, KeyError):
            return default
    
    def set(self, key: str, value: Any):
        """Set configuration value by dot notation key"""
        if not self._config:
            self.load_config()
        
        keys = key.split('.')
        config_obj = self._config
        
        # Navigate to parent
        for k in keys[:-1]:
            if hasattr(config_obj, k):
                config_obj = getattr(config_obj, k)
            elif isinstance(config_obj, dict):
                if k not in config_obj:
                    config_obj[k] = {}
                config_obj = config_obj[k]
        
        # Set value
        final_key = keys[-1]
        if hasattr(config_obj, final_key):
            setattr(config_obj, final_key, value)
        elif isinstance(config_obj, dict):
            config_obj[final_key] = value


# Global config manager instance
_config_manager: Optional[ConfigManager] = None


def load_config(config_path: Optional[Union[str, Path]] = None, force_reload: bool = False) -> Config:
    """Load configuration (singleton pattern)

    Args:
        config_path: Path to config file. If None, uses _resolve_config_path().
        force_reload: If True, reload config even if already loaded.
    """
    global _config_manager

    if config_path is None:
        config_path = _resolve_config_path()

    if _config_manager is None or force_reload:
        _config_manager = ConfigManager(config_path)

    if force_reload:
        _config_manager._config = None
        _config_manager._raw_config = {}

    return _config_manager.load_config()


def get_config_manager() -> ConfigManager:
    """Get global config manager instance"""
    global _config_manager

    if _config_manager is None:
        _config_manager = ConfigManager(_resolve_config_path())

    return _config_manager


def save_config(config_path: Optional[Union[str, Path]] = None):
    """Save current configuration state to config.yaml.

    FIX #20: Delegates to ConfigManager.save_config() which now
    actually persists the configuration.
    """
    _mgr = get_config_manager()
    _path = Path(config_path) if config_path else _resolve_config_path()
    _mgr.save_config(_path)
