"""Contract tests for the unified secret redactor (M1-SEC-003)."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

PASSWORD = "pw-canary-M1SEC003-7f4d"
TOKEN = "tok-canary-M1SEC003-2a91"
API_KEY = "key-canary-M1SEC003-c814"
DATABASE_URL = "postgresql://redact-user:db-canary-M1SEC003-5b27@db.internal/app"
SECRETS = (PASSWORD, TOKEN, API_KEY, DATABASE_URL)


@pytest.fixture
def redactor():
    from litemcp.security.redaction import SecretRedactor

    return SecretRedactor(secret_values=SECRETS)


@dataclass
class SecretBearingAudit:
    actor: str
    password: str
    token: str


def assert_no_secret(output: object) -> None:
    rendered = str(output)
    for secret in SECRETS:
        assert secret not in rendered, f"secret leaked: {secret!r} in {rendered!r}"


def test_redaction_covers_audit_payload_and_object_repr(redactor) -> None:
    audit_payload = {
        "action": "service.update",
        "changes": {"password": PASSWORD, "token": TOKEN},
        "metadata": {"api_key": API_KEY, "database_url": DATABASE_URL},
    }
    safe_payload = redactor.redact(audit_payload)
    safe_object_repr = redactor.safe_repr(
        SecretBearingAudit(actor="user-7", password=PASSWORD, token=TOKEN)
    )

    assert_no_secret(safe_payload)
    assert_no_secret(safe_object_repr)
    assert "service.update" in repr(safe_payload)


def test_logging_filter_redacts_message_arguments_and_exception_traceback(redactor) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(redactor)
    logger = logging.getLogger("litemcp.redaction.contract")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        try:
            raise RuntimeError(f"upstream token={TOKEN}; db={DATABASE_URL}")
        except RuntimeError:
            logger.exception("password=%s api_key=%s", PASSWORD, API_KEY)
    finally:
        logger.removeHandler(handler)
        handler.close()

    output = stream.getvalue()
    assert_no_secret(output)
    assert "password=" in output


def test_exception_rendering_redacts_nested_cause_and_context(redactor) -> None:
    cause = RuntimeError(f"cause token={TOKEN}")
    context = ValueError(f"context password={PASSWORD}")
    outer = RuntimeError(f"outer key={API_KEY}; url={DATABASE_URL}")
    outer.__cause__ = cause
    outer.__context__ = context

    rendered = redactor.sanitize_exception(outer)

    assert_no_secret(rendered)
    assert "RuntimeError" in rendered
    assert "ValueError" in rendered


def test_uncaught_500_response_is_redacted(redactor) -> None:
    app = FastAPI()

    @app.get("/raises")
    async def raises() -> None:
        raise RuntimeError(f"uncaught password={PASSWORD}; token={TOKEN}")

    from litemcp.security.redaction import SecretRedactionMiddleware

    client = TestClient(
        SecretRedactionMiddleware(app, redactor=redactor),
        raise_server_exceptions=False,
    )
    response = client.get("/raises")

    assert response.status_code == 500
    assert_no_secret(response.text)
    assert response.text


def test_redactor_failure_fails_closed_without_original_secret(monkeypatch, redactor) -> None:
    def explode(_value):
        raise RuntimeError("redactor implementation failure")

    monkeypatch.setattr(redactor, "redact", explode)

    safe_output = redactor.safe_redact({"password": PASSWORD, "token": TOKEN})

    assert_no_secret(safe_output)
    assert "redaction failed" in str(safe_output).lower()
