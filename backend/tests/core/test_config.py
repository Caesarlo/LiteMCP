"""Tests for typed environment configuration (M0-ENV-001).

Covers the feature's declared behavior: the backend loads type-safe
configuration from environment variables, applies documented safe
defaults, fails fast on missing required values, and refuses unsafe
production configuration (M0 exit standard in
docs/architecture/08-implementation-plan.md).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pydantic_settings import BaseSettings

from litemcp.core.config import Environment, Settings, get_settings


def _env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/litemcp",
    redis_url: str = "redis://localhost:6379/0",
    encryption_keys: list[str] | None = None,
    **overrides: str,
) -> None:
    """Set a clean LITEMCP_* environment for a test."""
    monkeypatch.delenv("LITEMCP_DATABASE_URL", raising=False)
    monkeypatch.delenv("LITEMCP_REDIS_URL", raising=False)
    monkeypatch.delenv("LITEMCP_ENCRYPTION_KEYS", raising=False)
    monkeypatch.setenv("LITEMCP_DATABASE_URL", database_url)
    monkeypatch.setenv("LITEMCP_REDIS_URL", redis_url)
    # Real-looking (not placeholder) values so TestProductionRefusal cases
    # exercise only the specific unsafe setting under test, not the M2-AUTH-004
    # admin JWT placeholder-rejection check.
    monkeypatch.setenv(
        "LITEMCP_ADMIN_JWT_SECRET", "test-admin-jwt-secret-" + "s" * 32
    )
    monkeypatch.setenv("LITEMCP_ADMIN_JWT_ISSUER", "https://auth.test.litemcp.invalid/")
    if encryption_keys is not None:
        monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", ",".join(encryption_keys))
    for name, value in overrides.items():
        monkeypatch.setenv(f"LITEMCP_{name.upper()}", value)


def _real_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


class TestDefaults:
    """Documented safe defaults apply when only required values are set."""

    def test_safe_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, encryption_keys=[_real_key()])
        settings = Settings(_env_file=None)

        assert settings.app_name == "litemcp"
        assert settings.environment == Environment.DEV
        assert settings.debug is False
        assert settings.log_level.value == "INFO"
        assert settings.gateway_enabled is False
        assert settings.allowed_origins == []
        assert settings.trusted_proxies == []
        assert settings.storage_backend == "local"
        assert settings.storage_path == "./data/storage"

    def test_required_values_are_secret_typed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, encryption_keys=[_real_key()])
        settings = Settings(_env_file=None)

        assert settings.database_url.get_secret_value() == (
            "postgresql+asyncpg://user:pass@localhost:5432/litemcp"
        )
        assert settings.redis_url.get_secret_value() == "redis://localhost:6379/0"


class TestEnvironmentOverrides:
    """Explicit LITEMCP_* values override defaults."""

    def test_scalars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(
            monkeypatch,
            encryption_keys=[_real_key()],
            app_name="litemcp-test",
            environment="test",
            debug="true",
            log_level="WARNING",
            gateway_enabled="true",
        )
        settings = Settings(_env_file=None)

        assert settings.app_name == "litemcp-test"
        assert settings.environment == Environment.TEST
        assert settings.debug is True
        assert settings.log_level.value == "WARNING"
        assert settings.gateway_enabled is True

    def test_list_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(
            monkeypatch,
            encryption_keys=[_real_key()],
            allowed_origins="https://admin.example.com,https://api.example.com",
            trusted_proxies="10.0.0.1,10.0.0.2",
        )
        settings = Settings(_env_file=None)

        assert settings.allowed_origins == [
            "https://admin.example.com",
            "https://api.example.com",
        ]
        assert settings.trusted_proxies == ["10.0.0.1", "10.0.0.2"]

    def test_storage_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, encryption_keys=[_real_key()])
        monkeypatch.setenv("LITEMCP_STORAGE_PATH", "/var/lib/litemcp")
        settings = Settings(_env_file=None)

        assert settings.storage_path == "/var/lib/litemcp"


class TestMissingRequired:
    """Missing required configuration fails fast at construction time."""

    def test_missing_database_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LITEMCP_DATABASE_URL", raising=False)
        monkeypatch.delenv("LITEMCP_REDIS_URL", raising=False)
        monkeypatch.setenv("LITEMCP_REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", _real_key())

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "database_url" in str(excinfo.value)

    def test_missing_redis_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LITEMCP_REDIS_URL", raising=False)
        monkeypatch.setenv("LITEMCP_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", _real_key())

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "redis_url" in str(excinfo.value)

    def test_missing_encryption_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LITEMCP_ENCRYPTION_KEYS", raising=False)
        monkeypatch.setenv("LITEMCP_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
        monkeypatch.setenv("LITEMCP_REDIS_URL", "redis://localhost:6379/0")

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "encryption_keys" in str(excinfo.value)

    def test_blank_encryption_key_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, encryption_keys=["  "])

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "encryption_keys" in str(excinfo.value)


class TestProductionRefusal:
    """M0 exit standard: prod refuses unsafe configuration at startup."""

    @pytest.fixture(autouse=True)
    def _prod_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, encryption_keys=[_real_key()], environment="prod")

    def test_debug_forbidden_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITEMCP_DEBUG", "true")

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "debug" in str(excinfo.value)

    def test_wildcard_origin_forbidden_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITEMCP_ALLOWED_ORIGINS", "*")

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "allowed_origins" in str(excinfo.value)

    def test_blank_origin_forbidden_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITEMCP_ALLOWED_ORIGINS", "")

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "allowed_origins" in str(excinfo.value)

    def test_sample_encryption_key_forbidden_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", "example-key-value")

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "production requires real Fernet keys" in str(excinfo.value)

    def test_short_encryption_key_forbidden_in_prod(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", "short")

        with pytest.raises(ValidationError) as excinfo:
            Settings(_env_file=None)

        assert "production requires real Fernet keys" in str(excinfo.value)

    def test_explicit_prod_origin_allowed(self) -> None:
        settings = Settings(
            _env_file=None,
            allowed_origins=["https://admin.example.com"],
        )

        assert settings.environment == Environment.PROD
        assert settings.allowed_origins == ["https://admin.example.com"]


class TestSingleton:
    """get_settings returns a cached process-wide instance."""

    def test_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, encryption_keys=[_real_key()])
        get_settings.cache_clear()
        try:
            first = get_settings()
            second = get_settings()
            assert first is second
        finally:
            get_settings.cache_clear()

    def test_settings_is_basesettings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, encryption_keys=[_real_key()])
        assert isinstance(Settings(_env_file=None), BaseSettings)
