"""Typed application configuration loaded from the environment.

Configuration follows The Twelve-Factor App: settings come from the
process environment (``LITEMCP_*`` variables, optionally from a local
``.env`` file for development) and are validated eagerly at import time,
so a misconfigured process fails fast at startup instead of at first use.

Security contract (see docs/architecture/08-implementation-plan.md, M0 exit
standard): a ``prod`` environment must refuse to start when debug mode is
enabled, a wide-open Origin allowlist is configured, or sample/short
encryption keys are supplied.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# A Fernet key is 32 random bytes, urlsafe base64-encoded (44 chars, no
# padding) as produced by ``cryptography.fernet.Fernet.generate_key()``.
_FERNET_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}=$|^[A-Za-z0-9_-]{44}$")
_SAMPLE_KEY_MARKERS = (
    "example",
    "changeme",
    "replace-me",
    "change-me",
    "sample",
    "your-",
)


class Environment(StrEnum):
    """Deployment environment tiers understood by the application."""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Typed application settings, populated from ``LITEMCP_*`` environment variables.

    Required values (missing configuration fails fast with a
    ``ValidationError`` at construction time):

    - ``LITEMCP_DATABASE_URL``: SQLAlchemy async database URL.
    - ``LITEMCP_REDIS_URL``: Redis connection URL.
    - ``LITEMCP_ENCRYPTION_KEYS``: one or more Fernet keys (see
      ``M1-SEC-001`` for the versioned-encryption semantics; the first
      key is the active one, the rest are rotation history).
    """

    model_config = SettingsConfigDict(
        env_prefix="LITEMCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- application ---
    app_name: str = "litemcp"
    environment: Environment = Environment.DEV
    debug: bool = False
    log_level: LogLevel = LogLevel.INFO

    # --- infrastructure (required, fail fast when missing) ---
    database_url: SecretStr
    redis_url: SecretStr
    encryption_keys: Annotated[list[str], NoDecode, Field(min_length=1)]

    # --- gateway (feature flags default to safe/closed, see plan §5) ---
    gateway_enabled: bool = False

    # --- HTTP security ---
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- storage ---
    storage_backend: Literal["local"] = "local"
    storage_path: str = Field(default="./data/storage", min_length=1)

    # --- password hashing (Argon2id, see M2-AUTH-002) ---
    argon2_memory_cost_kib: int = Field(default=19456, ge=1)
    argon2_time_cost: int = Field(default=2, ge=1)
    argon2_parallelism: int = Field(default=1, ge=1)

    @field_validator("encryption_keys", mode="before")
    @classmethod
    def _split_encryption_keys(cls, value: object) -> object:
        """Accept comma-separated keys from the environment."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",")]
        return value

    @field_validator("allowed_origins", "trusted_proxies", mode="before")
    @classmethod
    def _split_csv_lists(cls, value: object) -> object:
        """Accept comma-separated values from the environment."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",")]
        return value

    @field_validator("encryption_keys", mode="after")
    @classmethod
    def _encryption_keys_not_blank(cls, value: list[str]) -> list[str]:
        for key in value:
            if not key or not key.strip():
                raise ValueError("encryption_keys must not contain blank entries")
        return value

    @field_validator("allowed_origins", mode="after")
    @classmethod
    def _no_blank_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if not origin or not origin.strip():
                raise ValueError("allowed_origins must not contain blank entries")
        return value

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> Settings:
        """M0 exit standard: production must refuse unsafe configuration."""
        if self.environment != Environment.PROD:
            return self
        if self.debug:
            raise ValueError(
                "debug mode is forbidden when environment is prod; set LITEMCP_DEBUG=false"
            )
        if "*" in self.allowed_origins or "" in self.allowed_origins:
            raise ValueError(
                "wildcard or blank allowed_origins are forbidden in prod; "
                "enumerate explicit origins"
            )
        for key in self.encryption_keys:
            if not _FERNET_KEY_RE.match(key) or any(
                marker in key.lower() for marker in _SAMPLE_KEY_MARKERS
            ):
                raise ValueError(
                    "production requires real Fernet keys (32-byte urlsafe "
                    "base64), not samples; run `python -c "
                    "\"from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())\"`"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so application startup and dependency injection observe one
    consistent configuration. Tests construct ``Settings`` directly or
    call ``get_settings.cache_clear()`` after mutating the environment.
    """
    return Settings()  # type: ignore[call-arg]  # required values come from the environment
