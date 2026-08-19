"""M2-AUTH-007: application-layer refresh token rotation contract.

Pinned public API (implementer must create; this file does not):

- Module: ``litemcp.auth.refresh``
- ``async def rotate_refresh_token(refresh_token, *, redis, session_factory, settings, now=None) -> RefreshRotation``
- ``class RefreshRotation`` with ``access_token``, ``refresh_token``, ``expires_in``
- ``def compare_refresh_secret(*, stored_hash: str, presented_secret: str) -> bool``
- Exceptions (all subclasses of ``RefreshRejected``):
  ``MalformedRefreshToken``, ``RefreshSessionMissing``, ``RefreshIdleExpired``,
  ``RefreshAbsoluteExpired``, ``RefreshSecretMismatch``, ``RefreshUserNotActive``,
  ``RefreshPasswordChanged``

Opaque refresh format is ``<session_id>.<random_secret>``. Redis hash key is
``litemcp:<admin_session_environment>:admin_session:<session_id>`` with fields
from docs/architecture/02-admin-auth.md §8.3. ``current_secret_hash`` is the
lowercase hex SHA-256 of the UTF-8 secret. Timestamps are timezone-aware
ISO-8601 strings. Rotation must not mint tokens for a reused/wrong secret.
M2-AUTH-008 additionally revokes the Redis session family and writes
``auth.refresh_reuse_detected`` on that mismatch path.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
import pytest_asyncio
from pydantic import SecretStr

from litemcp.auth.refresh import (
    MalformedRefreshToken,
    RefreshAbsoluteExpired,
    RefreshIdleExpired,
    RefreshPasswordChanged,
    RefreshRejected,
    RefreshRotation,
    RefreshSecretMismatch,
    RefreshSessionMissing,
    RefreshUserNotActive,
    compare_refresh_secret,
    rotate_refresh_token,
)
from litemcp.core.config import Settings
from litemcp.db.models import Base, User
from litemcp.db.session import AsyncSessionFactory


def _fernet_dev_key() -> str:
    return "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _sha256_hex(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat()


class MemoryRedis:
    """Per-command in-memory Redis hash. Concurrent commands may interleave."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        self._sets: dict[str, set[str]] = {}
        self._expire_at: dict[str, datetime] = {}

    async def hset(
        self,
        key: str,
        mapping: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> int:
        await asyncio.sleep(0)
        fields = {str(k): str(v) for k, v in (mapping or {}).items()}
        bucket = self._hashes.setdefault(key, {})
        bucket.update(fields)
        return len(fields)

    async def hgetall(self, key: str) -> dict[str, str]:
        await asyncio.sleep(0)
        return dict(self._hashes.get(key, {}))

    async def exists(self, key: str) -> int:
        await asyncio.sleep(0)
        return 1 if key in self._hashes else 0

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._hashes:
                del self._hashes[key]
                self._expire_at.pop(key, None)
                removed += 1
        return removed

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self._hashes:
            return False
        self._expire_at[key] = datetime.now(UTC) + timedelta(seconds=seconds)
        return True

    async def sadd(self, name: str, *values: str) -> int:
        bucket = self._sets.setdefault(name, set())
        added = 0
        for value in values:
            if value not in bucket:
                bucket.add(value)
                added += 1
        return added

    async def srem(self, name: str, *values: str) -> int:
        bucket = self._sets.get(name)
        if not bucket:
            return 0
        removed = 0
        for value in values:
            if value in bucket:
                bucket.discard(value)
                removed += 1
        if not bucket:
            del self._sets[name]
        return removed

    async def ttl(self, key: str) -> int:
        if key not in self._hashes:
            return -2
        deadline = self._expire_at.get(key)
        if deadline is None:
            return -1
        remaining = int((deadline - datetime.now(UTC)).total_seconds())
        return remaining if remaining >= 0 else -2

    async def hexists(self, key: str, field: str) -> int:
        return 1 if field in self._hashes.get(key, {}) else 0


def _session_key(settings: Settings, session_id: str) -> str:
    return f"litemcp:{settings.admin_session_environment}:admin_session:{session_id}"


def _new_token_pair() -> tuple[str, str, str]:
    session_id = secrets.token_hex(16)
    secret = secrets.token_urlsafe(32)
    return session_id, secret, f"{session_id}.{secret}"


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 19, 9, 0, 0, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path) -> Settings:
    db_path = (tmp_path / "refresh-rotation.db").as_posix()
    return Settings(
        database_url=SecretStr(f"sqlite+aiosqlite:///{db_path}"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        encryption_keys=[_fernet_dev_key()],
        environment="test",
        admin_session_environment="test",
        admin_jwt_secret=SecretStr("unit-test-admin-jwt-secret-32bytes-min"),
        admin_jwt_issuer="litemcp-test-issuer",
        admin_jwt_audience="litemcp-admin-api",
        admin_jwt_kid="admin-jwt-2026-01",
        admin_access_ttl_seconds=900,
        admin_refresh_idle_ttl_seconds=28800,
        admin_refresh_absolute_ttl_seconds=604800,
    )


@pytest_asyncio.fixture
async def session_factory(settings: Settings):
    factory = AsyncSessionFactory(
        settings.database_url.get_secret_value(),
        engine_kwargs={"connect_args": {"timeout": 30}},
    )
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        await factory.dispose()


@pytest.fixture
def redis() -> MemoryRedis:
    return MemoryRedis()


async def _insert_user(
    session_factory: AsyncSessionFactory,
    *,
    status: str = "active",
    password_changed_at: datetime,
) -> User:
    user = User(
        id=uuid.uuid4(),
        username="alice",
        username_normalized="alice",
        password_hash="not-a-real-hash",
        role="user",
        status=status,
        password_changed_at=password_changed_at,
        created_by="test",
        updated_by="test",
    )
    async with session_factory.session() as session:
        session.add(user)
        await session.commit()
    return user


async def _seed_session(
    redis: MemoryRedis,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    session_id: str,
    secret: str,
    created_at: datetime,
    last_refreshed_at: datetime,
    idle_expires_at: datetime,
    absolute_expires_at: datetime,
) -> str:
    key = _session_key(settings, session_id)
    remaining_absolute = max(
        1, int((absolute_expires_at - last_refreshed_at).total_seconds())
    )
    await redis.hset(
        key,
        mapping={
            "user_id": str(user_id),
            "current_secret_hash": _sha256_hex(secret),
            "created_at": _iso(created_at),
            "last_refreshed_at": _iso(last_refreshed_at),
            "idle_expires_at": _iso(idle_expires_at),
            "absolute_expires_at": _iso(absolute_expires_at),
            "user_agent_hash": "ua",
            "source_ip_hash": "ip",
        },
    )
    await redis.expire(key, remaining_absolute)
    return key


async def _live_session(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
    **user_kwargs: Any,
) -> tuple[User, str, str, str]:
    password_changed_at = user_kwargs.pop(
        "password_changed_at", now - timedelta(days=30)
    )
    status = user_kwargs.pop("status", "active")
    user = await _insert_user(
        session_factory,
        status=status,
        password_changed_at=password_changed_at,
    )
    session_id, secret, token = _new_token_pair()
    await _seed_session(
        redis,
        settings,
        user_id=user.id,
        session_id=session_id,
        secret=secret,
        created_at=now - timedelta(hours=1),
        last_refreshed_at=now - timedelta(minutes=5),
        idle_expires_at=now + timedelta(hours=7),
        absolute_expires_at=now + timedelta(days=6),
    )
    return user, session_id, secret, token


def _decode_access(token: str, settings: Settings) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.admin_jwt_secret.get_secret_value(),
        algorithms=[settings.admin_jwt_algorithm],
        audience=settings.admin_jwt_audience,
        issuer=settings.admin_jwt_issuer,
        leeway=settings.admin_jwt_clock_skew_seconds,
    )


@pytest.mark.asyncio
async def test_rotate_issues_new_access_and_refresh_same_session_family(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    user, session_id, secret, token = await _live_session(
        redis, session_factory, settings, now
    )

    result = await rotate_refresh_token(
        token,
        redis=redis,
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    assert isinstance(result, RefreshRotation)
    assert result.expires_in == settings.admin_access_ttl_seconds
    new_session_id, new_secret = result.refresh_token.split(".", 1)
    assert new_session_id == session_id
    assert new_secret != secret
    assert result.refresh_token != token

    claims = _decode_access(result.access_token, settings)
    header = jwt.get_unverified_header(result.access_token)
    assert header.get("typ") == "at+jwt"
    assert header.get("kid") == settings.admin_jwt_kid
    assert claims["sub"] == str(user.id)
    assert claims["sid"] == session_id
    assert claims["token_type"] == "access"


@pytest.mark.asyncio
async def test_rotate_atomically_replaces_secret_hash_and_refresh_timestamps(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    user, session_id, secret, token = await _live_session(
        redis, session_factory, settings, now
    )
    key = _session_key(settings, session_id)
    before = await redis.hgetall(key)
    previous_absolute = before["absolute_expires_at"]

    await rotate_refresh_token(
        token,
        redis=redis,
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    after = await redis.hgetall(key)
    assert after["user_id"] == str(user.id)
    assert after["current_secret_hash"] != before["current_secret_hash"]
    assert after["current_secret_hash"] != _sha256_hex(secret)
    assert after["last_refreshed_at"] == _iso(now)
    expected_idle = now + timedelta(seconds=settings.admin_refresh_idle_ttl_seconds)
    assert after["idle_expires_at"] == _iso(expected_idle)
    assert after["absolute_expires_at"] == previous_absolute
    assert after["created_at"] == before["created_at"]


@pytest.mark.asyncio
async def test_idle_expiry_extends_but_never_past_absolute(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    user = await _insert_user(
        session_factory, password_changed_at=now - timedelta(days=10)
    )
    session_id, secret, token = _new_token_pair()
    absolute = now + timedelta(hours=1)
    await _seed_session(
        redis,
        settings,
        user_id=user.id,
        session_id=session_id,
        secret=secret,
        created_at=now - timedelta(days=1),
        last_refreshed_at=now - timedelta(minutes=1),
        idle_expires_at=now + timedelta(minutes=10),
        absolute_expires_at=absolute,
    )

    await rotate_refresh_token(
        token,
        redis=redis,
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    after = await redis.hgetall(_session_key(settings, session_id))
    assert after["idle_expires_at"] == _iso(absolute)
    assert after["absolute_expires_at"] == _iso(absolute)
    ttl = await redis.ttl(_session_key(settings, session_id))
    remaining_absolute = int((absolute - now).total_seconds())
    assert 0 < ttl <= remaining_absolute


@pytest.mark.asyncio
async def test_old_refresh_token_fails_immediately_with_no_grace_window(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    _user, _session_id, _secret, token = await _live_session(
        redis, session_factory, settings, now
    )
    rotated = await rotate_refresh_token(
        token,
        redis=redis,
        session_factory=session_factory,
        settings=settings,
        now=now,
    )

    with pytest.raises(RefreshSecretMismatch):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now + timedelta(seconds=1),
        )

    with pytest.raises(RefreshSessionMissing):
        await rotate_refresh_token(
            rotated.refresh_token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now + timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_concurrent_rotate_allows_only_one_success_for_the_same_token(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    _user, _session_id, _secret, token = await _live_session(
        redis, session_factory, settings, now
    )

    outcomes = await asyncio.gather(
        rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        ),
        rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        ),
        return_exceptions=True,
    )

    successes = [item for item in outcomes if isinstance(item, RefreshRotation)]
    failures = [item for item in outcomes if isinstance(item, RefreshRejected)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RefreshSecretMismatch)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "no-separator",
        ".only-secret",
        "only-session.",
        "too.many.parts",
        "sid",
    ],
)
@pytest.mark.asyncio
async def test_malformed_refresh_token_is_rejected(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
    token: str,
) -> None:
    with pytest.raises(MalformedRefreshToken):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )


@pytest.mark.asyncio
async def test_missing_session_is_rejected(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    session_id, _secret, token = _new_token_pair()
    await _insert_user(
        session_factory, password_changed_at=now - timedelta(days=1)
    )
    assert await redis.exists(_session_key(settings, session_id)) == 0

    with pytest.raises(RefreshSessionMissing):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )


@pytest.mark.asyncio
async def test_idle_expired_session_is_rejected(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    user = await _insert_user(
        session_factory, password_changed_at=now - timedelta(days=2)
    )
    session_id, secret, token = _new_token_pair()
    await _seed_session(
        redis,
        settings,
        user_id=user.id,
        session_id=session_id,
        secret=secret,
        created_at=now - timedelta(hours=9),
        last_refreshed_at=now - timedelta(hours=8, minutes=1),
        idle_expires_at=now - timedelta(seconds=1),
        absolute_expires_at=now + timedelta(days=5),
    )

    with pytest.raises(RefreshIdleExpired):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )


@pytest.mark.asyncio
async def test_absolute_expired_session_is_rejected_even_if_idle_is_fresh(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    user = await _insert_user(
        session_factory, password_changed_at=now - timedelta(days=8)
    )
    session_id, secret, token = _new_token_pair()
    await _seed_session(
        redis,
        settings,
        user_id=user.id,
        session_id=session_id,
        secret=secret,
        created_at=now - timedelta(days=7, hours=1),
        last_refreshed_at=now - timedelta(minutes=1),
        idle_expires_at=now + timedelta(hours=7),
        absolute_expires_at=now - timedelta(seconds=1),
    )

    with pytest.raises(RefreshAbsoluteExpired):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )


@pytest.mark.asyncio
async def test_wrong_secret_fails_closed_without_minting_tokens(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    user, session_id, secret, _token = await _live_session(
        redis, session_factory, settings, now
    )
    forged = f"{session_id}.{secrets.token_urlsafe(32)}"
    key = _session_key(settings, session_id)
    before = await redis.hgetall(key)

    with pytest.raises(RefreshSecretMismatch):
        await rotate_refresh_token(
            forged,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )

    assert await redis.exists(key) == 0
    assert before["current_secret_hash"] == _sha256_hex(secret)
    assert before["user_id"] == str(user.id)


def test_compare_refresh_secret_is_constant_time_and_fail_closed() -> None:
    secret = secrets.token_urlsafe(32)
    stored = _sha256_hex(secret)
    assert compare_refresh_secret(stored_hash=stored, presented_secret=secret) is True
    assert (
        compare_refresh_secret(
            stored_hash=stored, presented_secret=secrets.token_urlsafe(32)
        )
        is False
    )
    source = inspect.getsource(compare_refresh_secret)
    assert "compare_digest" in source
    rotate_source = inspect.getsource(rotate_refresh_token)
    assert "compare_refresh_secret" in rotate_source


@pytest.mark.parametrize("status", ["disabled", "locked"])
@pytest.mark.asyncio
async def test_inactive_user_cannot_rotate(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
    status: str,
) -> None:
    _user, _session_id, _secret, token = await _live_session(
        redis, session_factory, settings, now, status=status
    )

    with pytest.raises(RefreshUserNotActive):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )


@pytest.mark.asyncio
async def test_missing_user_cannot_rotate(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    session_id, secret, token = _new_token_pair()
    await _seed_session(
        redis,
        settings,
        user_id=uuid.uuid4(),
        session_id=session_id,
        secret=secret,
        created_at=now - timedelta(hours=1),
        last_refreshed_at=now - timedelta(minutes=1),
        idle_expires_at=now + timedelta(hours=7),
        absolute_expires_at=now + timedelta(days=6),
    )

    with pytest.raises(RefreshUserNotActive):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )


@pytest.mark.asyncio
async def test_session_created_before_password_change_is_rejected(
    redis: MemoryRedis,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime,
) -> None:
    password_changed_at = now - timedelta(minutes=10)
    user = await _insert_user(
        session_factory, password_changed_at=password_changed_at
    )
    session_id, secret, token = _new_token_pair()
    await _seed_session(
        redis,
        settings,
        user_id=user.id,
        session_id=session_id,
        secret=secret,
        created_at=password_changed_at - timedelta(minutes=5),
        last_refreshed_at=now - timedelta(minutes=1),
        idle_expires_at=now + timedelta(hours=7),
        absolute_expires_at=now + timedelta(days=6),
    )

    with pytest.raises(RefreshPasswordChanged):
        await rotate_refresh_token(
            token,
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            now=now,
        )
