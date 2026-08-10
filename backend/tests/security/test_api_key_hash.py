"""Application-layer contract for one-time API key plaintext handling."""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any

import pytest

from litemcp.security.api_keys import ApiKeyService

PLAINTEXT_CANARY = "litemcp_test_only_plaintext_canary_7f3a"
SERVICE_ID = "service-test-only"
KEY_NAME = "test integration key"


@dataclass
class InMemoryKeyStore:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def insert(self, row: dict[str, Any]) -> None:
        self.rows.append(dict(row))

    def get_by_public_id(self, public_id: str) -> dict[str, Any] | None:
        return next((row for row in self.rows if row["public_id"] == public_id), None)


@dataclass
class RecordingSink:
    records: list[Any] = field(default_factory=list)

    def write(self, record: Any, *args: Any, **kwargs: Any) -> None:
        self.records.append(record)

    def info(self, record: Any, *args: Any, **kwargs: Any) -> None:
        self.records.append(record)

    def warning(self, record: Any, *args: Any, **kwargs: Any) -> None:
        self.records.append(record)

    def error(self, record: Any, *args: Any, **kwargs: Any) -> None:
        self.records.append(record)


@pytest.fixture
def key_context() -> tuple[ApiKeyService, InMemoryKeyStore, RecordingSink, RecordingSink]:
    store = InMemoryKeyStore()
    log_sink = RecordingSink()
    audit_sink = RecordingSink()
    service = ApiKeyService(
        repository=store,
        logger=log_sink,
        audit=audit_sink,
    )
    return service, store, log_sink, audit_sink


def _text(value: Any) -> str:
    return repr(value)


def test_creation_returns_plaintext_once_but_persists_only_digest_and_prefix(
    key_context: tuple[ApiKeyService, InMemoryKeyStore, RecordingSink, RecordingSink],
) -> None:
    service, store, log_sink, audit_sink = key_context

    created = service.create(service_id=SERVICE_ID, name=KEY_NAME)

    assert created.plaintext
    assert created.plaintext.startswith("litemcp_")
    assert created.plaintext == created.secret
    row = store.rows[-1]
    assert row["secret_hash"] != created.plaintext
    assert len(row["secret_hash"]) == 64
    assert row["display_prefix"]
    assert row["display_prefix"] == created.display_prefix
    assert created.plaintext not in _text(row)
    assert "secret" not in row or row["secret"] != created.plaintext
    assert created.plaintext not in _text(log_sink.records)
    assert created.plaintext not in _text(audit_sink.records)


def test_same_name_and_service_get_fresh_plaintext_each_time(
    key_context: tuple[ApiKeyService, InMemoryKeyStore, RecordingSink, RecordingSink],
) -> None:
    service, _store, _log_sink, _audit_sink = key_context

    first = service.create(service_id=SERVICE_ID, name=KEY_NAME)
    second = service.create(service_id=SERVICE_ID, name=KEY_NAME)

    assert first.plaintext != second.plaintext


def test_creation_redacts_plaintext_from_failure_text_and_all_observability_sinks(
    key_context: tuple[ApiKeyService, InMemoryKeyStore, RecordingSink, RecordingSink],
) -> None:
    service, store, log_sink, audit_sink = key_context
    service.generate_secret = lambda: PLAINTEXT_CANARY  # type: ignore[method-assign]

    def failing_insert(row: dict[str, Any]) -> None:
        store.rows.append(dict(row))
        raise RuntimeError("simulated persistence failure")

    store.insert = failing_insert  # type: ignore[method-assign]

    with pytest.raises(Exception) as caught:
        service.create(service_id=SERVICE_ID, name=KEY_NAME)

    assert PLAINTEXT_CANARY not in _text(store.rows)
    assert PLAINTEXT_CANARY not in _text(log_sink.records)
    assert PLAINTEXT_CANARY not in _text(audit_sink.records)
    assert PLAINTEXT_CANARY not in _text(caught.value)


def test_verification_uses_constant_time_digest_comparison(
    key_context: tuple[ApiKeyService, InMemoryKeyStore, RecordingSink, RecordingSink],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _store, _log_sink, _audit_sink = key_context
    created = service.create(service_id=SERVICE_ID, name=KEY_NAME)
    calls: list[tuple[bytes | str, bytes | str]] = []
    original_compare_digest = hmac.compare_digest

    def recording_compare_digest(left: bytes | str, right: bytes | str) -> bool:
        calls.append((left, right))
        return original_compare_digest(left, right)

    monkeypatch.setattr(hmac, "compare_digest", recording_compare_digest)

    assert service.verify(created.plaintext) is True
    assert calls


@pytest.mark.parametrize("tampered_suffix", ["x", "0"])
def test_invalid_or_tampered_keys_fail_closed(
    tampered_suffix: str,
    key_context: tuple[ApiKeyService, InMemoryKeyStore, RecordingSink, RecordingSink],
) -> None:
    service, _store, _log_sink, _audit_sink = key_context
    created = service.create(service_id=SERVICE_ID, name=KEY_NAME)
    tampered = created.plaintext[:-1] + tampered_suffix

    assert service.verify(tampered) is False
    assert service.verify("litemcp_nonexistent_public_id_invalid_secret") is False
