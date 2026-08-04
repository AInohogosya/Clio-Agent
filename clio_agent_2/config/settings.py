"""
Configuration module for Clio-Agent-2.
Handles loading environment variables and application settings.
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    import os as _os

    def load_dotenv(dotenv_path=None, override=False, **_kwargs):
        """Load a .env file into os.environ using only the standard library."""
        candidates = [dotenv_path] if dotenv_path else [".env", "config/.env"]
        for _path in candidates:
            if not _path or not _os.path.isfile(_path):
                continue
            try:
                with open(_path, encoding="utf-8") as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if not _line or _line.startswith("#") or "=" not in _line:
                            continue
                        _k, _, _v = _line.partition("=")
                        _k, _v = _k.strip(), _v.strip().strip('"').strip("'} ")
                        if _k and (override or _k not in _os.environ):
                            _os.environ[_k] = _v
            except OSError:
                pass
            return True
        return False


def _dump_yaml_value(value, indent: int = 0) -> str:
    """Serialize a single Python value to a YAML representation (no external deps).

    Handles the simple types produced by Config.to_dict(): dict, list, str,
    int, float, bool and None. Scalars are quoted only when required to keep
    the output readable and unambiguous.
    """
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(_dump_yaml_value(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_dump_yaml_scalar(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{pad}-")
                lines.append(_dump_yaml_value(item, indent + 1))
            else:
                lines.append(f"{pad}- {_dump_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{pad}{_dump_yaml_scalar(value)}"


def _dump_yaml_scalar(value) -> str:
    """Format a scalar value for YAML output."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # Strings: quote when empty or when they could be misread as another type.
    s = str(value)
    needs_quote = (
        s == ""
        or s.lower() in ("true", "false", "null", "yes", "no", "on", "off")
        or s[0] in ("!", "&", "*", "?", "|", ">", "%", "@", "`", "#", "-", "{", "[")
        or s[0] in ("'", '"')
        or ": " in s or "#" in s
        or " #" in s
    )
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


class Config:
    """Centralized configuration management for Clio-Agent-2."""

    def __init__(self, env_path: Optional[str] = None):
        """
        Initialize configuration by loading environment variables.
        
        Args:
            env_path: Path to .env file. Defaults to config/.env in project root.
        """
        if env_path is None:
            project_root = Path(__file__).parent.parent
            env_path = project_root / "config" / ".env"

        # Always store a Path so file operations (.exists(), .parent) work
        # regardless of whether the caller passed a string or a Path.
        self._env_path = Path(env_path)
        # Mirror of the persisted settings, written next to .env as a
        # human-friendly YAML snapshot (config.yaml). Kept in sync whenever
        # settings are saved via save_to_env()/save_settings().
        self._yaml_path = self._env_path.parent / "config.yaml"
        self._load_config()

    def _load_config(self):
        """Load configuration from .env file."""
        load_dotenv(self._env_path)

        # LLM API Keys
        self.openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
        self.google_api_key: Optional[str] = os.getenv("GOOGLE_API_KEY")
        self.anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
        self.openrouter_api_key: Optional[str] = os.getenv("OPENROUTER_API_KEY")
        self.grok_api_key: Optional[str] = os.getenv("GROK_API_KEY")
        self.deepseek_api_key: Optional[str] = os.getenv("DEEPSEEK_API_KEY")
        # Additional OpenAI-compatible built-in provider keys
        self.mistral_api_key: Optional[str] = os.getenv("MISTRAL_API_KEY")
        self.groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
        self.perplexity_api_key: Optional[str] = os.getenv("PERPLEXITY_API_KEY")
        self.together_api_key: Optional[str] = os.getenv("TOGETHER_API_KEY")
        self.fireworks_api_key: Optional[str] = os.getenv("FIREWORKS_API_KEY")
        self.nvidia_api_key: Optional[str] = os.getenv("NVIDIA_API_KEY")
        self.nim_api_key: Optional[str] = os.getenv("NIM_API_KEY")
        self.qwen_api_key: Optional[str] = os.getenv("QWEN_API_KEY")
        self.huggingface_api_key: Optional[str] = os.getenv("HUGGINGFACE_API_KEY")
        self.deepinfra_api_key: Optional[str] = os.getenv("DEEPINFRA_API_KEY")
        self.ollama_api_key: Optional[str] = os.getenv("OLLAMA_API_KEY")
        self.ollama_base_url: Optional[str] = os.getenv("OLLAMA_BASE_URL")
        # User-supplied "Other" providers (loaded from CUSTOM_PROVIDERS + friends)
        self.custom_providers: List[Dict[str, Any]] = self.load_custom_providers()

        # OpenRouter Settings
        self.openrouter_http_referer: Optional[str] = os.getenv("OPENROUTER_HTTP_REFERER")
        self.openrouter_app_name: Optional[str] = os.getenv("OPENROUTER_APP_NAME")

        # Search API Key
        self.search_api_key: Optional[str] = os.getenv("SEARCH_API_KEY")

        # Bot Tokens
        self.telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
        self.discord_bot_token: Optional[str] = os.getenv("DISCORD_BOT_TOKEN", "").strip() or None

        # Bot Interface Access Control
        self.allowed_users: Optional[str] = os.getenv("ALLOWED_USERS", "").strip() or None
        self.allowed_telegram_chat_ids: Optional[str] = os.getenv("ALLOWED_TELEGRAM_CHAT_IDS", "").strip() or None
        self.allowed_discord_user_ids: Optional[str] = os.getenv("ALLOWED_DISCORD_USER_IDS", "").strip() or None
        self.allowed_whatsapp_ids: Optional[str] = os.getenv("ALLOWED_WHATSAPP_IDS", "").strip() or None

        # WhatsApp Business API Configuration
        self.whatsapp_phone_number_id: Optional[str] = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip() or None
        self.whatsapp_access_token: Optional[str] = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip() or None
        self.whatsapp_app_secret: Optional[str] = os.getenv("WHATSAPP_APP_SECRET", "").strip() or None
        self.whatsapp_webhook_verify_token: Optional[str] = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "").strip() or None
        self.whatsapp_webhook_url: Optional[str] = os.getenv("WHATSAPP_WEBHOOK_URL", "").strip() or None
        self.whatsapp_webhook_port: Optional[int] = int(os.getenv("WHATSAPP_WEBHOOK_PORT", "8080"))

        # LLM Settings
        # NOTE: There is intentionally NO built-in default model. The model must
        # be set explicitly (e.g. via /llm_default or /config model). A previously
        # configured model is persisted in config/.env under DEFAULT_MODEL and is
        # reloaded here, so the setting survives a program restart.
        self.default_llm_provider: str = os.getenv("DEFAULT_LLM_PROVIDER", "openai")
        self.current_model: str = os.getenv("DEFAULT_MODEL", "")
        # Guardrail: keep the underlying LLM provider/model from changing
        # unexpectedly. The .env value is optional and defaults to "true",
        # so the safe (locked) posture is the default.
        self.llm_settings_locked: bool = os.getenv("LLM_SETTINGS_LOCKED", "true").lower() == "true"
        self.context_log_max_lines: int = int(os.getenv("CONTEXT_LOG_MAX_LINES", "1000"))

        # Agent Settings
        self.agent_name: str = os.getenv("AGENT_NAME", "Clio-Agent-2")
        self.autonomous_mode: bool = os.getenv("AUTONOMOUS_MODE", "true").lower() == "true"
        self.thinking_interval: float = float(os.getenv("THINKING_INTERVAL", "5.0"))

    def save_to_env(self, key: str, value: str) -> bool:
        """
        Save a configuration value to the .env file.
        
        Args:
            key: Environment variable name (e.g., "TELEGRAM_BOT_TOKEN")
            value: Value to set
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Read current content
            if not self._env_path.exists():
                # Create new file
                self._env_path.parent.mkdir(parents=True, exist_ok=True)
                content = ""
            else:
                with open(self._env_path, encoding='utf-8') as f:
                    content = f.read()

            # Check if key exists in file
            pattern = rf'^{re.escape(key)}=.*$'
            if re.search(pattern, content, re.MULTILINE):
                # Replace existing value
                content = re.sub(pattern, f'{key}={value}', content, flags=re.MULTILINE)
            else:
                # Add new line
                if content and not content.endswith('\n'):
                    content += '\n'
                content += f'{key}={value}\n'

            # Write back
            with open(self._env_path, 'w', encoding='utf-8') as f:
                f.write(content)

            # Update the process environment as well. load_dotenv() does NOT
            # override variables that already exist in os.environ, so without
            # this the subsequent reload would keep reading the stale value and
            # the persisted change would be lost on the next restart.
            os.environ[key] = value

            # Reload environment variables
            self._load_config()

            # Keep the human-friendly YAML mirror in sync as well.
            self.save_to_yaml()

            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False


    def save_settings(self, settings: Dict[str, str]) -> bool:
        """
        Save multiple configuration values to the .env file at once.

        Args:
            settings: Mapping of environment variable name to value
                      (e.g. {"DEFAULT_MODEL": "gpt-4o"}).

        Returns:
            True if successful, False otherwise.
        """
        try:
            if not self._env_path.exists():
                self._env_path.parent.mkdir(parents=True, exist_ok=True)
                content = ""
            else:
                with open(self._env_path, encoding='utf-8') as f:
                    content = f.read()

            for key, value in settings.items():
                value = str(value)
                pattern = rf'^{re.escape(key)}=.*$'
                if re.search(pattern, content, re.MULTILINE):
                    # Replace existing value
                    content = re.sub(pattern, f'{key}={value}', content, flags=re.MULTILINE)
                else:
                    # Add new line
                    if content and not content.endswith('\n'):
                        content += '\n'
                    content += f'{key}={value}\n'
                # Keep the process environment in sync (see save_to_env).
                os.environ[key] = value

            with open(self._env_path, 'w', encoding='utf-8') as f:
                f.write(content)

            # Reload environment variables once for all changes
            self._load_config()

            # Keep the human-friendly YAML mirror in sync as well.
            self.save_to_yaml()

            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False

    def save_to_yaml(self, path: Optional[str] = None) -> bool:
        """
        Write the current (non-sensitive) configuration to a YAML file so the
        settings configured via /reconfigure, /config, etc. are also captured
        in a human-readable config.yaml next to .env.

        Args:
            path: Optional explicit path. Defaults to config/config.yaml next
                  to the .env file.

        Returns:
            True if successful, False otherwise.
        """
        try:
            target = Path(path) if path else self._yaml_path
            target.parent.mkdir(parents=True, exist_ok=True)
            data = self.to_dict()
            header = (
                "# Clio-Agent-2 configuration snapshot\n"
                "# Generated automatically by /reconfigure, /config and related\n"
                "# commands. This mirrors config/.env (without secrets) for easy\n"
                "# reading. Edit config/.env to change actual values.\n"
            )
            content = header + _dump_yaml_value(data) + "\n"
            with open(target, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving config.yaml: {e}")
            return False

    def get_yaml_path(self) -> Path:
        """Get the path to the config.yaml snapshot file."""
        return self._yaml_path

    def reload(self):
        """Reload configuration from .env file."""
        self._load_config()

    def get_api_key(self, provider: str) -> Optional[str]:
        """Get API key for specified provider."""
        provider_map = {
            "openai": self.openai_api_key,
            "google": self.google_api_key,
            "anthropic": self.anthropic_api_key,
            "openrouter": self.openrouter_api_key,
            "grok": self.grok_api_key,
            "deepseek": self.deepseek_api_key,
            "mistral": self.mistral_api_key,
            "groq": self.groq_api_key,
            "perplexity": self.perplexity_api_key,
            "together": self.together_api_key,
            "fireworks": self.fireworks_api_key,
            "nvidia": self.nvidia_api_key,
            "nim": self.nim_api_key,
            "qwen": self.qwen_api_key,
            "huggingface": self.huggingface_api_key,
            "deepinfra": self.deepinfra_api_key,
            "ollama": self.ollama_api_key,
        }
        key = provider_map.get(provider.lower())
        if key is None:
            # Fall back to user-supplied "Other" providers.
            for cp in getattr(self, "custom_providers", []) or []:
                if cp.get("id") == provider.lower():
                    return cp.get("api_key") or None
        return key

    # ---------------------------------------------------------------------------
    # "Other" (custom) providers — see add_custom_provider() for the .env layout.
    # ---------------------------------------------------------------------------

    @staticmethod
    def _custom_suffix(provider_id: str) -> str:
        """Map a provider id to its upper-case, safe env-var suffix."""
        return re.sub(r"[^A-Za-z0-9]", "_", provider_id).upper()

    def _remove_env_value(self, key: str) -> None:
        """Delete a key entirely from .env (not just blank it)."""
        try:
            if not self._env_path.exists():
                return
            with open(self._env_path, encoding="utf-8") as f:
                content = f.read()
            content = re.sub(
                rf"^{re.escape(key)}=.*$(\n?)", "", content, flags=re.MULTILINE
            )
            with open(self._env_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.environ.pop(key, None)
        except Exception as e:
            print(f"Error removing env value {key}: {e}")

    def load_custom_providers(self) -> List[Dict[str, Any]]:
        """Return the list of user-supplied "Other" providers from .env."""
        raw = os.getenv("CUSTOM_PROVIDERS", "") or ""
        ids = [i.strip() for i in raw.split(",") if i.strip()]
        providers: List[Dict[str, Any]] = []
        for pid in ids:
            suffix = self._custom_suffix(pid)
            base_url = os.getenv(f"CUSTOM_{suffix}_BASE_URL", "") or ""
            if not base_url:
                continue
            providers.append({
                "id": pid,
                "base_url": base_url,
                "api_key": os.getenv(f"CUSTOM_{suffix}_API_KEY", "") or "",
                "label": os.getenv(f"CUSTOM_{suffix}_LABEL", pid) or pid,
                "auth_header": os.getenv(f"CUSTOM_{suffix}_AUTH_HEADER", "Authorization") or "Authorization",
                "auth_prefix": os.getenv(f"CUSTOM_{suffix}_AUTH_PREFIX", "Bearer") or "Bearer",
                "models_path": os.getenv(f"CUSTOM_{suffix}_MODELS_PATH", "/models") or "/models",
                "default_model": os.getenv(f"CUSTOM_{suffix}_DEFAULT_MODEL", "") or "",
            })
        return providers

    def add_custom_provider(
        self,
        provider_id: str,
        base_url: str,
        api_key: str = "",
        label: str = "",
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer",
        models_path: str = "/models",
        default_model: str = "",
    ) -> bool:
        """Persist a new (or updated) custom "Other" provider to .env."""
        pid = (provider_id or "").strip().lower()
        if not pid:
            raise ValueError("A provider ID is required.")
        if not re.match(r"^[a-z0-9][a-z0-9_\-]*$", pid):
            raise ValueError(
                "Provider ID must start with a letter/digit and contain only "
                "lowercase letters, digits, '-' or '_'."
            )
        if not base_url or not base_url.strip():
            raise ValueError("A base URL is required.")
        base_url = base_url.strip()
        label = label or pid
        auth_header = auth_header or "Authorization"
        auth_prefix = auth_prefix if auth_prefix is not None else "Bearer"
        models_path = models_path or "/models"

        # Add the id to the index (if not already present), then write each field.
        ids = [p["id"] for p in self.load_custom_providers()]
        if pid not in ids:
            ids.append(pid)
            self.save_to_env("CUSTOM_PROVIDERS", ",".join(ids))
        suffix = self._custom_suffix(pid)
        self.save_to_env(f"CUSTOM_{suffix}_BASE_URL", base_url)
        self.save_to_env(f"CUSTOM_{suffix}_API_KEY", api_key or "")
        self.save_to_env(f"CUSTOM_{suffix}_LABEL", label)
        self.save_to_env(f"CUSTOM_{suffix}_AUTH_HEADER", auth_header)
        self.save_to_env(f"CUSTOM_{suffix}_AUTH_PREFIX", auth_prefix)
        self.save_to_env(f"CUSTOM_{suffix}_MODELS_PATH", models_path)
        self.save_to_env(f"CUSTOM_{suffix}_DEFAULT_MODEL", default_model or "")
        # save_to_env() already reloaded config + synced config.yaml, so
        # self.custom_providers is now up to date.
        return True

    def remove_custom_provider(self, provider_id: str) -> bool:
        """Remove a custom "Other" provider from .env. Returns True if removed."""
        pid = (provider_id or "").strip().lower()
        ids = [p["id"] for p in self.load_custom_providers()]
        if pid not in ids:
            return False
        suffix = self._custom_suffix(pid)
        new_ids = [i for i in ids if i != pid]
        self.save_to_env("CUSTOM_PROVIDERS", ",".join(new_ids))
        for var in (
            f"CUSTOM_{suffix}_BASE_URL",
            f"CUSTOM_{suffix}_API_KEY",
            f"CUSTOM_{suffix}_LABEL",
            f"CUSTOM_{suffix}_AUTH_HEADER",
            f"CUSTOM_{suffix}_AUTH_PREFIX",
            f"CUSTOM_{suffix}_MODELS_PATH",
            f"CUSTOM_{suffix}_DEFAULT_MODEL",
        ):
            self._remove_env_value(var)
        # Ensure in-memory state + yaml mirror reflect the removal.
        self.reload()
        return True

    @staticmethod
    def _is_real_secret(value: Optional[str]) -> bool:
        """Return True only if value is a real secret, not a placeholder."""
        if not value:
            return False
        s = str(value).strip()
        if not s:
            return False
        lowered = s.lower()
        if (
            "your_" in lowered
            or lowered.startswith("your-")
            or "sk-your" in lowered
            or "placeholder" in lowered
            or "example" in lowered
            or "xxxx" in lowered
            or "<" in s
            or ">" in s
        ):
            return False
        return True

    def validate_api_keys(self) -> Dict[str, bool]:
        """Check which API keys are configured."""
        status = {
            "openai": self._is_real_secret(self.openai_api_key),
            "google": self._is_real_secret(self.google_api_key),
            "anthropic": self._is_real_secret(self.anthropic_api_key),
            "openrouter": self._is_real_secret(self.openrouter_api_key),
            "grok": self._is_real_secret(self.grok_api_key),
            "deepseek": self._is_real_secret(self.deepseek_api_key),
            "mistral": self._is_real_secret(self.mistral_api_key),
            "groq": self._is_real_secret(self.groq_api_key),
            "perplexity": self._is_real_secret(self.perplexity_api_key),
            "together": self._is_real_secret(self.together_api_key),
            "fireworks": self._is_real_secret(self.fireworks_api_key),
            "nvidia": self._is_real_secret(self.nvidia_api_key),
            "nim": self._is_real_secret(self.nim_api_key),
            "qwen": self._is_real_secret(self.qwen_api_key),
            "huggingface": self._is_real_secret(self.huggingface_api_key),
            "deepinfra": self._is_real_secret(self.deepinfra_api_key),
            # Ollama is keyless by default, so a configured base URL counts.
            "ollama": bool(
                (self.ollama_base_url and self.ollama_base_url.strip())
                or (self.ollama_api_key and self.ollama_api_key.strip())
            ),
            "telegram": self._is_real_secret(self.telegram_bot_token),
            "discord": self._is_real_secret(self.discord_bot_token),
            "whatsapp": bool(
                self._is_real_secret(self.whatsapp_phone_number_id)
                and self._is_real_secret(self.whatsapp_access_token)
            ),
        }
        # User-supplied "Other" providers count as configured when they have a
        # base URL.
        for cp in getattr(self, "custom_providers", []) or []:
            pid = cp.get("id")
            if pid:
                status[pid] = bool(cp.get("base_url"))
        return status

    def to_dict(self) -> Dict[str, Any]:
        """Export configuration as dictionary (excluding sensitive keys)."""
        # Custom providers are surfaced without their secret API key.
        custom = [
            {k: v for k, v in cp.items() if k != "api_key"}
            for cp in (getattr(self, "custom_providers", []) or [])
        ]
        return {
            "default_llm_provider": self.default_llm_provider,
            "current_model": self.current_model,
            "llm_settings_locked": self.llm_settings_locked,
            "context_log_max_lines": self.context_log_max_lines,
            "agent_name": self.agent_name,
            "autonomous_mode": self.autonomous_mode,
            "thinking_interval": self.thinking_interval,
            "allowed_users": self.allowed_users,
            "allowed_telegram_chat_ids": self.allowed_telegram_chat_ids,
            "allowed_discord_user_ids": self.allowed_discord_user_ids,
            "allowed_whatsapp_ids": self.allowed_whatsapp_ids,
            "api_keys_configured": self.validate_api_keys(),
            "custom_providers": custom,
        }

    def get_env_path(self) -> Path:
        """Get the path to the .env file."""
        return self._env_path


# Global configuration instance
config = Config()
