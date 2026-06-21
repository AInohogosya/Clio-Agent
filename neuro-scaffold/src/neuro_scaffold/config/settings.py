"""Application settings with validation via pydantic-settings."""

from __future__ import annotations

import secrets
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AgentMode(str, Enum):
    STANDALONE = "standalone"
    SUBAGENT = "subagent"
    GATEWAY = "gateway"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEURO_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Identity ──────────────────────────────────────────────────────
    agent_id: str = Field(default="neuro-scaffold-0", description="Unique agent instance ID")
    agent_name: str = Field(default="Neuro-Scaffold", description="Human-readable agent name")
    mode: AgentMode = Field(default=AgentMode.SUBAGENT)

    # ── Security ──────────────────────────────────────────────────────
    clio_api_key: SecretStr = Field(
        default=SecretStr(secrets.token_urlsafe(64)),
        description="API key for Clio Agent authentication",
    )
    jwt_secret: SecretStr = Field(
        default=SecretStr(secrets.token_urlsafe(64)),
        description="JWT signing secret",
    )
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiry_seconds: int = Field(default=3600, ge=60, le=86400)
    allowed_origins: list[str] = Field(default=["http://localhost:8080"])
    max_auth_attempts: int = Field(default=5, ge=1, le=20)
    lockout_seconds: int = Field(default=300, ge=30, le=3600)

    # ── Gateway / Server ──────────────────────────────────────────────
    gateway_host: str = Field(default="0.0.0.0")
    gateway_port: int = Field(default=9090, ge=1024, le=65535)
    gateway_workers: int = Field(default=1, ge=1, le=8)

    # ── Agent Loop ────────────────────────────────────────────────────
    max_iterations: int = Field(default=50, ge=1, le=200)
    max_tool_calls_per_iteration: int = Field(default=10, ge=1, le=50)
    iteration_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    max_stagnation_count: int = Field(default=3, ge=1, le=20)
    scratchpad_path: Path = Field(default=Path("/tmp/neuro_scratchpad.json"))

    # ── Shell / Execution ─────────────────────────────────────────────
    shell_timeout_seconds: int = Field(default=120, ge=5, le=3600)
    shell_max_output_lines: int = Field(default=2000, ge=100, le=50000)
    shell_truncate_head: int = Field(default=50, ge=10, le=500)
    shell_truncate_tail: int = Field(default=50, ge=10, le=500)
    persistent_shell_ttl_seconds: int = Field(default=1800, ge=60, le=7200)

    # ── Context / AST ─────────────────────────────────────────────────
    ast_cache_max_entries: int = Field(default=5000, ge=100, le=50000)
    context_max_tokens: int = Field(default=8000, ge=1000, le=128000)
    context_chunk_overlap: int = Field(default=200, ge=0, le=2000)

    # ── Logging ───────────────────────────────────────────────────────
    log_level: LogLevel = Field(default=LogLevel.INFO)
    log_format: str = Field(default="json")
    log_file: Path | None = Field(default=None)

    # ── Docker / Containment ──────────────────────────────────────────
    container_workspace: str = Field(default="/workspace")
    container_user: str = Field(default="neuro")
    container_uid: int = Field(default=10000, ge=10000, le=60000)
    container_gid: int = Field(default=10000, ge=10000, le=60000)
    container_memory_limit: str = Field(default="512m")
    container_cpu_limit: float = Field(default=1.0, ge=0.1, le=8.0)
    container_network_enabled: bool = Field(default=False)
    container_readonly_rootfs: bool = Field(default=True)
    container_capabilities_drop: list[str] = Field(default=["ALL"])
    container_seccomp_profile: str | None = Field(default=None)
    container_apparmor_profile: str | None = Field(default="neuro-scaffold")

    # ── LLM ──────────────────────────────────────────────────────────
    llm_provider: str = Field(default="openai")
    llm_model: str = Field(default="gpt-4o")
    llm_api_key: SecretStr | None = Field(default=None)
    llm_base_url: str | None = Field(default=None)
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=4096, ge=256, le=32768)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("container_capabilities_drop", mode="before")
    @classmethod
    def parse_capabilities(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [cap.strip() for cap in v.split(",") if cap.strip()]
        return v

    @property
    def clio_api_key_plain(self) -> str:
        return self.clio_api_key.get_secret_value()

    @property
    def jwt_secret_plain(self) -> str:
        return self.jwt_secret.get_secret_value()

    @property
    def llm_api_key_plain(self) -> str | None:
        if self.llm_api_key is not None:
            return self.llm_api_key.get_secret_value()
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
