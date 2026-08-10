"""Versioned encryption for service secrets."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from cryptography.fernet import Fernet, MultiFernet


class MissingCurrentKeyError(ValueError):
    """Raised when encryption is configured without an active key."""


class SecretEncryption:
    """Encrypt secrets with the current Fernet key and read rotated keys."""

    def __init__(
        self,
        current_key: bytes | str | None,
        old_keys: Iterable[bytes | str] | None = None,
    ) -> None:
        if current_key is None or (
            isinstance(current_key, bytes)
            and not current_key.strip()
        ) or (
            isinstance(current_key, str)
            and not current_key.strip()
        ):
            raise MissingCurrentKeyError("current encryption key is required")

        keys = [current_key, *(old_keys or ())]
        self.fernet = MultiFernet([self._fernet(key) for key in keys])

    @staticmethod
    def _fernet(key: bytes | str) -> Fernet:
        return Fernet(key)

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> SecretEncryption:
        """Build an encryptor from current/rotation keys in a config mapping."""
        current_key = config.get("current_key")
        old_keys = config.get("old_keys")
        if current_key is None and "encryption_keys" in config:
            keys = config["encryption_keys"]
            if keys:
                current_key, old_keys = keys[0], keys[1:]
        return cls(current_key=current_key, old_keys=old_keys)

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt plaintext using the current (first) key."""
        return self.fernet.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt ciphertext using the current key or rotation history."""
        return self.fernet.decrypt(ciphertext)
