"""Contract tests for versioned MultiFernet service-secret encryption."""

import base64

import pytest
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from litemcp.security.encryption import MissingCurrentKeyError, SecretEncryption


def _key() -> bytes:
    """Return a deterministic Fernet key for an individual test."""
    return base64.urlsafe_b64encode(bytes(range(32)))


def _key_with_seed(seed: int) -> bytes:
    return base64.urlsafe_b64encode(bytes((seed + offset) % 256 for offset in range(32)))


def test_service_secrets_use_multifernet() -> None:
    current = _key_with_seed(1)
    previous = _key_with_seed(2)

    service = SecretEncryption(current_key=current, old_keys=[previous])

    assert isinstance(service.fernet, MultiFernet)
    assert service.fernet._fernets[0]._signing_key == base64.urlsafe_b64decode(current)[:16]


def test_encryption_writes_with_current_key() -> None:
    current = _key_with_seed(10)
    previous = _key_with_seed(20)
    plaintext = b"service-secret-canary"
    service = SecretEncryption(current_key=current, old_keys=[previous])

    ciphertext = service.encrypt(plaintext)

    assert Fernet(current).decrypt(ciphertext) == plaintext
    with pytest.raises(InvalidToken):
        Fernet(previous).decrypt(ciphertext)


def test_ciphertext_written_before_rotation_remains_decryptable_with_current_and_old_key() -> None:
    old = _key_with_seed(30)
    current = _key_with_seed(40)
    plaintext = b"rotating-service-secret"
    before_rotation = SecretEncryption(current_key=old)
    after_rotation = SecretEncryption(current_key=current, old_keys=[old])

    ciphertext = before_rotation.encrypt(plaintext)

    assert after_rotation.decrypt(ciphertext) == plaintext


def test_retired_old_key_alone_cannot_decrypt_ciphertext_written_with_current_key() -> None:
    retired = _key_with_seed(50)
    current = _key_with_seed(60)
    ciphertext = SecretEncryption(current_key=current).encrypt(b"retired-key-negative-case")

    with pytest.raises(InvalidToken):
        SecretEncryption(current_key=retired).decrypt(ciphertext)


def test_ciphertext_does_not_contain_reversible_plaintext_substring() -> None:
    service = SecretEncryption(current_key=_key_with_seed(70))
    plaintext = b"super-secret-api-token-plaintext"

    ciphertext = service.encrypt(plaintext)

    assert plaintext not in ciphertext
    assert b"api-token" not in ciphertext


def test_missing_current_key_fails_fast_instead_of_using_old_key() -> None:
    old = _key_with_seed(80)

    with pytest.raises(MissingCurrentKeyError):
        SecretEncryption.from_config({"current_key": None, "old_keys": [old]})


def test_blank_current_key_is_not_accepted_when_old_key_is_present() -> None:
    old = _key_with_seed(90)

    with pytest.raises(MissingCurrentKeyError):
        SecretEncryption.from_config({"current_key": "", "old_keys": [old]})
