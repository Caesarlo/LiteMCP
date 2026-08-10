"""Application-layer handling for one-time API key plaintexts."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Protocol


class _KeyRepository(Protocol):
    def insert(self, row: dict[str, Any]) -> None:
        """Persist a non-secret API key row."""

    def get_by_public_id(self, public_id: str) -> dict[str, Any] | None:
        """Return a key row by its public selector."""


@dataclass(frozen=True)
class CreatedApiKey:
    """The one-time response returned after creating an API key."""

    plaintext: str
    secret: str
    display_prefix: str


class ApiKeyService:
    """Create and verify API keys without persisting their plaintext."""

    _PREFIX = "litemcp_"
    _PUBLIC_ID_LENGTH = 24
    _DISPLAY_PREFIX_LENGTH = 12

    def __init__(self, repository: _KeyRepository, logger: Any, audit: Any) -> None:
        self.repository = repository
        self.logger = logger
        self.audit = audit

    def generate_secret(self) -> str:
        """Generate a complete API key; override this seam in deterministic tests."""
        public_id = secrets.token_hex(self._PUBLIC_ID_LENGTH // 2)
        secret = secrets.token_hex(32)
        return f"{self._PREFIX}{public_id}_{secret}"

    def create(self, service_id: str, name: str) -> CreatedApiKey:
        """Create a key and return its plaintext exactly once."""
        plaintext = self.generate_secret()
        public_id = self._public_id_from_secret(plaintext) or self._new_public_id()
        display_prefix = plaintext[: self._DISPLAY_PREFIX_LENGTH]
        row = {
            "service_id": service_id,
            "name": name,
            "public_id": public_id,
            "display_prefix": display_prefix,
            "secret_hash": self._digest(plaintext),
            "hash_algorithm": "sha256-v1",
            "status": "active",
        }

        try:
            self.repository.insert(row)
        except Exception:  # noqa: BLE001 - persistence errors must be sanitized
            self._record_failure(public_id, service_id, name)
            raise RuntimeError("API key persistence failed") from None

        self._record_success(public_id, service_id, name, display_prefix)
        return CreatedApiKey(
            plaintext=plaintext,
            secret=plaintext,
            display_prefix=display_prefix,
        )

    def verify(self, plaintext: str) -> bool:
        """Return whether a presented key matches an active persisted digest."""
        if not isinstance(plaintext, str):
            return False
        public_id = self._public_id_from_secret(plaintext)
        if public_id is None:
            return False

        try:
            row = self.repository.get_by_public_id(public_id)
            if not row or row.get("status", "active") != "active":
                return False
            expected = row.get("secret_hash")
            if not isinstance(expected, str) or len(expected) != 64:
                return False
            candidate = self._digest(plaintext)
            return hmac.compare_digest(candidate, expected)
        except Exception:  # noqa: BLE001 - verification fails closed on repository errors
            return False

    @classmethod
    def _digest(cls, plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    @classmethod
    def _public_id_from_secret(cls, plaintext: str) -> str | None:
        if not plaintext.startswith(cls._PREFIX):
            return None
        parts = plaintext.split("_", 2)
        if len(parts) != 3 or len(parts[1]) != cls._PUBLIC_ID_LENGTH:
            return None
        if not parts[1].isalnum() or not parts[2]:
            return None
        return parts[1]

    @classmethod
    def _new_public_id(cls) -> str:
        return secrets.token_hex(cls._PUBLIC_ID_LENGTH // 2)

    def _record_success(
        self, public_id: str, service_id: str, name: str, display_prefix: str
    ) -> None:
        record = {
            "action": "api_key.created",
            "public_id": public_id,
            "display_prefix": display_prefix,
            "service_id": service_id,
            "name": name,
        }
        self._emit(self.logger, "info", record)
        self._emit(self.audit, "write", record)

    def _record_failure(self, public_id: str, service_id: str, name: str) -> None:
        record = {
            "action": "api_key.create_failed",
            "public_id": public_id,
            "service_id": service_id,
            "name": name,
        }
        self._emit(self.logger, "error", record)
        self._emit(self.audit, "write", record)

    @staticmethod
    def _emit(sink: Any, method: str, record: dict[str, str]) -> None:
        try:
            callback = getattr(sink, method, None)
            if callback is not None:
                callback(record)
        except Exception:  # noqa: BLE001,S110 - telemetry must never affect key handling
            pass
