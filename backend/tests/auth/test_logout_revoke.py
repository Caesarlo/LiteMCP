"""Application-layer tests for refresh-session logout / revoke (M2-AUTH-009).

Pins `litemcp.auth.logout` and post-revoke `litemcp.auth.refresh.refresh_session`.
Unknown current-session logout is fail-closed: it must not report success and
must not mint tokens.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

ENVIRONMENT = "test"
IDLE_TTL = timedelta(hours=8)
ABSOLUTE_TTL = timedelta(days=7)

# Distinctive values that must never appear in audit rows.
_SECRET_MARKER = "rtsecret_" + "s" * 48
_TOKEN_COOKIE_MARKER = "__Secure-litemcp_rt="
_AUTHORIZATION_MARKER = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.logout-probe"


class FakeRedis:
    """In-memory Redis subset for session keys and user_sessions sets."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    def get(self, key: str) -> str | None:
        return self._kv.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._kv[key] = value
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._kv:
                del self._kv[key]
                removed += 1
            if key in self._sets:
                del self._sets[key]
                removed += 1
        return removed

    def exists(self, *keys: str) -> int:
        return sum(1 for key in keys if key in self._kv or key in self._sets)

    def sadd(self, key: str, *members: str) -> int:
        bucket = self._sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    def srem(self, key: str, *members: str) -> int:
        bucket = self._sets.get(key)
        if not bucket:
            return 0
        removed = 0
        for member in members:
            if member in bucket:
                bucket.remove(member)
                removed += 1
        if not bucket:
            del self._sets[key]
        return removed

    def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))


def _session_key(session_id: str) -> str:
    return f"litemcp:{ENVIRONMENT}:admin_session:{session_id}"


def _user_sessions_key(user_id: uuid.UUID) -> str:
    return f"litemcp:{ENVIRONMENT}:user_sessions:{user_id}"


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_session_id() -> str:
    return secrets.token_hex(16)


def _new_secret() -> str:
    return secrets.token_hex(32)


def _opaque_token(session_id: str, secret: str) -> str:
    return f"{session_id}.{secret}"


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def _seed_session(
    redis: FakeRedis,
    *,
    user_id: uuid.UUID,
    session_id: str,
    secret: str,
) -> None:
    now = datetime.now(UTC)
    payload = {
        "user_id": str(user_id),
        "current_secret_hash": _sha256_hex(secret),
        "created_at": _iso(now),
        "last_refreshed_at": _iso(now),
        "idle_expires_at": _iso(now + IDLE_TTL),
        "absolute_expires_at": _iso(now + ABSOLUTE_TTL),
        "user_agent_hash": _sha256_hex("pytest-agent"),
        "source_ip_hash": _sha256_hex("127.0.0.1"),
    }
    redis.set(_session_key(session_id), json.dumps(payload), ex=int(ABSOLUTE_TTL.total_seconds()))
    redis.sadd(_user_sessions_key(user_id), session_id)


def _make_audit_engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE audit_event (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    request_id TEXT,
                    actor_type TEXT,
                    actor_id TEXT,
                    action TEXT NOT NULL,
                    resource_type TEXT,
                    resource_id TEXT,
                    service_id TEXT,
                    result TEXT,
                    reason_code TEXT,
                    source_ip TEXT,
                    user_agent TEXT,
                    changes TEXT,
                    metadata TEXT,
                    previous_event_hash TEXT,
                    event_hash TEXT
                )
                """
            )
        )
    return engine


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def db_engine() -> Engine:
    return _make_audit_engine()


@pytest.fixture
def db(db_engine: Engine) -> Session:
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


def _logout_api():
    from litemcp.auth.logout import LogoutRejected as Rejected
    from litemcp.auth.logout import (
        logout_all_sessions,
        logout_current_session,
    )

    return logout_current_session, logout_all_sessions, Rejected


def _refresh_api():
    from litemcp.auth.refresh import refresh_session

    return refresh_session


def _audit_rows(db: Session) -> list[dict[str, Any]]:
    result = db.execute(text("SELECT * FROM audit_event"))
    columns = list(result.keys())
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _audit_serialized(db: Session) -> str:
    return json.dumps(_audit_rows(db), default=str)


def _assert_refresh_rejected(refresh_token: str, redis: FakeRedis, db: Session) -> None:
    refresh_session = _refresh_api()
    with pytest.raises(Exception) as excinfo:
        refresh_session(
            refresh_token,
            redis=redis,
            db=db,
            environment=ENVIRONMENT,
        )
    assert "access_token" not in str(excinfo.value).lower()
    result = excinfo.value
    if isinstance(result, dict):
        pytest.fail("refresh after revoke must not return a token payload")


def test_logout_current_session_deletes_redis_session_and_refresh_fails(
    redis: FakeRedis, db: Session
) -> None:
    logout_current_session, _, _ = _logout_api()
    user_id = uuid.uuid4()
    session_id = _new_session_id()
    secret = _new_secret()
    token = _opaque_token(session_id, secret)
    _seed_session(redis, user_id=user_id, session_id=session_id, secret=secret)

    logout_current_session(
        token,
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-logout-current",
    )

    assert redis.get(_session_key(session_id)) is None
    _assert_refresh_rejected(token, redis, db)


def test_logout_current_session_removes_id_from_user_sessions_set(
    redis: FakeRedis, db: Session
) -> None:
    logout_current_session, _, _ = _logout_api()
    user_id = uuid.uuid4()
    keep_id = _new_session_id()
    drop_id = _new_session_id()
    keep_secret = _new_secret()
    drop_secret = _new_secret()
    _seed_session(redis, user_id=user_id, session_id=keep_id, secret=keep_secret)
    _seed_session(redis, user_id=user_id, session_id=drop_id, secret=drop_secret)

    logout_current_session(
        _opaque_token(drop_id, drop_secret),
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-logout-srem",
    )

    remaining = redis.smembers(_user_sessions_key(user_id))
    assert drop_id not in remaining
    assert keep_id in remaining
    assert redis.get(_session_key(keep_id)) is not None


def test_logout_current_leaves_other_sessions_of_same_user(
    redis: FakeRedis, db: Session
) -> None:
    logout_current_session, _, _ = _logout_api()
    user_id = uuid.uuid4()
    other_id = _new_session_id()
    current_id = _new_session_id()
    other_secret = _new_secret()
    current_secret = _new_secret()
    _seed_session(redis, user_id=user_id, session_id=other_id, secret=other_secret)
    _seed_session(redis, user_id=user_id, session_id=current_id, secret=current_secret)

    logout_current_session(
        _opaque_token(current_id, current_secret),
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-logout-sibling",
    )

    assert redis.get(_session_key(other_id)) is not None
    refresh_session = _refresh_api()
    refresh_session(
        _opaque_token(other_id, other_secret),
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
    )


def test_logout_current_rejects_wrong_secret_and_keeps_session(
    redis: FakeRedis, db: Session
) -> None:
    logout_current_session, _, rejected = _logout_api()
    user_id = uuid.uuid4()
    session_id = _new_session_id()
    secret = _new_secret()
    _seed_session(redis, user_id=user_id, session_id=session_id, secret=secret)

    with pytest.raises(rejected):
        logout_current_session(
            _opaque_token(session_id, _new_secret()),
            redis=redis,
            db=db,
            environment=ENVIRONMENT,
            actor_user_id=user_id,
            request_id="req-logout-wrong-secret",
        )

    assert redis.get(_session_key(session_id)) is not None
    assert session_id in redis.smembers(_user_sessions_key(user_id))
    success_rows = [
        row
        for row in _audit_rows(db)
        if row["action"] == "auth.logout" and row["result"] == "success"
    ]
    assert success_rows == []


def test_logout_all_sessions_deletes_every_refresh_session_for_user(
    redis: FakeRedis, db: Session
) -> None:
    _, logout_all_sessions, _ = _logout_api()
    user_id = uuid.uuid4()
    first_id, second_id = _new_session_id(), _new_session_id()
    first_secret, second_secret = _new_secret(), _new_secret()
    _seed_session(redis, user_id=user_id, session_id=first_id, secret=first_secret)
    _seed_session(redis, user_id=user_id, session_id=second_id, secret=second_secret)

    logout_all_sessions(
        user_id,
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-logout-all",
    )

    assert redis.get(_session_key(first_id)) is None
    assert redis.get(_session_key(second_id)) is None
    assert redis.smembers(_user_sessions_key(user_id)) == set()
    assert redis.exists(_user_sessions_key(user_id)) == 0


def test_logout_all_sessions_does_not_touch_other_users(
    redis: FakeRedis, db: Session
) -> None:
    _, logout_all_sessions, _ = _logout_api()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    a_id, b_id = _new_session_id(), _new_session_id()
    a_secret, b_secret = _new_secret(), _new_secret()
    _seed_session(redis, user_id=user_a, session_id=a_id, secret=a_secret)
    _seed_session(redis, user_id=user_b, session_id=b_id, secret=b_secret)

    logout_all_sessions(
        user_a,
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_a,
        request_id="req-logout-all-isolation",
    )

    assert redis.get(_session_key(a_id)) is None
    assert redis.get(_session_key(b_id)) is not None
    assert b_id in redis.smembers(_user_sessions_key(user_b))
    refresh_session = _refresh_api()
    refresh_session(
        _opaque_token(b_id, b_secret),
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
    )


def test_refresh_fails_after_logout_all(redis: FakeRedis, db: Session) -> None:
    _, logout_all_sessions, _ = _logout_api()
    user_id = uuid.uuid4()
    session_id = _new_session_id()
    secret = _new_secret()
    token = _opaque_token(session_id, secret)
    _seed_session(redis, user_id=user_id, session_id=session_id, secret=secret)

    logout_all_sessions(
        user_id,
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-logout-all-refresh",
    )

    _assert_refresh_rejected(token, redis, db)


def test_logout_current_writes_auth_logout_audit_success(
    redis: FakeRedis, db: Session
) -> None:
    logout_current_session, _, _ = _logout_api()
    user_id = uuid.uuid4()
    session_id = _new_session_id()
    secret = _new_secret()
    _seed_session(redis, user_id=user_id, session_id=session_id, secret=secret)

    logout_current_session(
        _opaque_token(session_id, secret),
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-audit-logout",
    )

    rows = [row for row in _audit_rows(db) if row["action"] == "auth.logout"]
    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "success"
    assert row["actor_type"] == "user"
    assert row["actor_id"] == str(user_id)
    assert row["request_id"] == "req-audit-logout"
    assert row["resource_type"]
    assert row["resource_id"]


def test_logout_all_writes_auth_logout_all_audit_success(
    redis: FakeRedis, db: Session
) -> None:
    _, logout_all_sessions, _ = _logout_api()
    user_id = uuid.uuid4()
    session_id = _new_session_id()
    _seed_session(redis, user_id=user_id, session_id=session_id, secret=_new_secret())

    logout_all_sessions(
        user_id,
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-audit-logout-all",
    )

    rows = [row for row in _audit_rows(db) if row["action"] == "auth.logout_all"]
    assert len(rows) == 1
    row = rows[0]
    assert row["result"] == "success"
    assert row["actor_type"] == "user"
    assert row["actor_id"] == str(user_id)
    assert row["request_id"] == "req-audit-logout-all"
    assert row["resource_type"]
    assert row["resource_id"]


def test_logout_unknown_current_session_is_rejected_without_success_or_new_tokens(
    redis: FakeRedis, db: Session
) -> None:
    # Fail-closed: a missing session cannot be confirmed deleted, so logout
    # must not claim success and must not mint tokens.
    logout_current_session, _, rejected = _logout_api()
    missing_token = _opaque_token(_new_session_id(), _new_secret())

    with pytest.raises(rejected):
        logout_current_session(
            missing_token,
            redis=redis,
            db=db,
            environment=ENVIRONMENT,
            actor_user_id=uuid.uuid4(),
            request_id="req-logout-missing",
        )

    success_rows = [
        row
        for row in _audit_rows(db)
        if row["action"] == "auth.logout" and row["result"] == "success"
    ]
    assert success_rows == []
    _assert_refresh_rejected(missing_token, redis, db)


def test_logout_audit_excludes_token_secret_cookie_and_authorization_plaintext(
    redis: FakeRedis, db: Session
) -> None:
    logout_current_session, logout_all_sessions, _ = _logout_api()
    user_id = uuid.uuid4()
    session_id = _new_session_id()
    token = _opaque_token(session_id, _SECRET_MARKER)
    _seed_session(redis, user_id=user_id, session_id=session_id, secret=_SECRET_MARKER)

    logout_current_session(
        token,
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-audit-redact-current",
        user_agent=_AUTHORIZATION_MARKER,
        cookie_header=_TOKEN_COOKIE_MARKER + token,
        authorization=_AUTHORIZATION_MARKER,
    )

    other_id = _new_session_id()
    _seed_session(redis, user_id=user_id, session_id=other_id, secret=_new_secret())
    logout_all_sessions(
        user_id,
        redis=redis,
        db=db,
        environment=ENVIRONMENT,
        actor_user_id=user_id,
        request_id="req-audit-redact-all",
        user_agent=_AUTHORIZATION_MARKER,
        cookie_header=_TOKEN_COOKIE_MARKER + token,
        authorization=_AUTHORIZATION_MARKER,
    )

    blob = _audit_serialized(db)
    assert _SECRET_MARKER not in blob
    assert token not in blob
    assert _TOKEN_COOKIE_MARKER not in blob
    assert _AUTHORIZATION_MARKER not in blob
    assert "Authorization" not in blob
    assert session_id + "." not in blob
