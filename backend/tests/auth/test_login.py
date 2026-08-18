"""Login-success contract (M2-AUTH-004).

docs/architecture/02-admin-auth.md §7.1/§7.2/§8.1/§8.3 (verbatim
translation): "Valid credentials return a short-lived Access JWT and
establish a Refresh Session."

Scope: this feature covers only what happens *after* a caller has already
confirmed the submitted password is correct (that verification lives in
M2-AUTH-002/003). There is no HTTP login endpoint in this codebase yet.
This test pins a single async function that a future login endpoint will
call once the password has been confirmed:

    async def create_login_session(
        redis_client: redis.asyncio.Redis, user: User
    ) -> LoginSession

exposed from ``litemcp.auth.session``. ``LoginSession`` is a small
dataclass/model with at least the fields:

- ``access_token: str`` -- the encoded JWT.
- ``expires_in: int`` -- seconds until ``access_token`` expires, equal to
  the token's own ``exp - iat``.
- ``refresh_token: str`` -- ``"<session_id>.<random_secret>"`` per §8.1.
- ``session_id: str`` -- the same session id embedded in the refresh
  token and in the JWT's ``sid`` claim.

Explicitly OUT of scope for this test file (owned by other feature IDs):
password/credential verification, login failure lockout / rate limiting,
access-JWT *verification* (the auth dependency), refresh rotation,
refresh replay detection, logout/revocation, Redis failure fallback, and
anything HTTP-shaped (endpoints, cookies, CSRF/Origin checks).

Settings this test assumes the implementation reads (following the
``Settings``/``LITEMCP_*`` convention in ``litemcp.core.config``), set
per-test via ``monkeypatch.setenv`` the same way ``tests/core/test_config.py``
does:

- ``LITEMCP_ADMIN_JWT_SECRET`` -> ``Settings.admin_jwt_secret: SecretStr``
- ``LITEMCP_ADMIN_JWT_ISSUER`` -> ``Settings.admin_jwt_issuer: str``
- ``LITEMCP_ADMIN_JWT_AUDIENCE`` -> ``Settings.admin_jwt_audience: str``
- ``LITEMCP_ADMIN_ACCESS_TTL_SECONDS`` -> ``Settings.admin_access_ttl_seconds: int = 900``
- ``LITEMCP_ENVIRONMENT`` (already exists) -- used verbatim as the
  ``<environment>`` segment of every Redis key below.

Redis conventions asserted here (§8.3):

- ``litemcp:<environment>:admin_session:<session_id>`` -- a Redis HASH
  with at least ``user_id``, ``current_secret_hash``, ``created_at``,
  ``last_refreshed_at``, ``idle_expires_at``, ``absolute_expires_at``.
  ``current_secret_hash`` is the lowercase hex SHA-256 digest of the
  refresh token's ``random_secret`` half. The key's own Redis TTL must be
  <= the configured absolute timeout.
- ``litemcp:<environment>:user_sessions:<user_id>`` -- a Redis SET that
  gains ``session_id`` as a member.

Isolation strategy: rather than a global ``FLUSHDB`` (which could nuke
unrelated data on a shared Redis instance), every test run gets its own
unique ``environment`` string (``monkeypatch.setenv("LITEMCP_ENVIRONMENT",
...)`` with a per-test UUID suffix), so every Redis key this test touches
lives under a namespace no other test or process will ever reuse. A
fixture still deletes the handful of keys it created on teardown to keep
the shared Redis instance tidy.

This test requires a live Redis instance, defaulting to
``redis://localhost:6379/0`` (overridable via ``LITEMCP_TEST_REDIS_URL``,
mirroring the ``LITEMCP_TEST_POSTGRES_URL`` pattern in
``tests/auth/test_bootstrap.py``). A missing ``litemcp.auth.session``
module is the expected RED failure -- the login-session flow does not
exist yet.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime

import jwt
import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio

from litemcp.db.models import User

REDIS_URL = os.environ.get("LITEMCP_TEST_REDIS_URL", "redis://localhost:6379/0")

JWT_SECRET = "test-admin-jwt-secret-" + "s" * 32
JWT_ISSUER = "https://auth.test.litemcp.invalid/"
JWT_AUDIENCE = "litemcp-admin-api"
DEFAULT_ACCESS_TTL = 900
DEFAULT_IDLE_TTL = 28800
DEFAULT_ABSOLUTE_TTL = 604800
TOLERANCE_SECONDS = 30


@pytest_asyncio.fixture
async def redis_client():
    client = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 - pragma: no cover - environment problem, not RED
        pytest.skip(f"live Redis not reachable at {REDIS_URL}: {exc}")
    created_keys: list[str] = []
    client._litemcp_created_keys = created_keys  # type: ignore[attr-defined]
    try:
        yield client
    finally:
        if created_keys:
            await client.delete(*created_keys)
        await client.aclose()


@pytest.fixture
def environment_name() -> str:
    return f"pytest-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def admin_auth_settings(monkeypatch: pytest.MonkeyPatch, environment_name: str):
    """Configure a fresh, unique-namespaced settings environment per test."""
    monkeypatch.setenv("LITEMCP_ENVIRONMENT", "dev")
    monkeypatch.setenv(
        "LITEMCP_DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/litemcp"
    )
    monkeypatch.setenv("LITEMCP_REDIS_URL", REDIS_URL)
    from cryptography.fernet import Fernet

    monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("LITEMCP_ADMIN_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("LITEMCP_ADMIN_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("LITEMCP_ADMIN_JWT_AUDIENCE", JWT_AUDIENCE)
    monkeypatch.setenv("LITEMCP_ADMIN_ACCESS_TTL_SECONDS", str(DEFAULT_ACCESS_TTL))
    # The "environment" segment of every Redis key this test touches --
    # unique per test run so runs never collide or leak into each other.
    monkeypatch.setenv("LITEMCP_ADMIN_SESSION_ENVIRONMENT", environment_name)

    from litemcp.core.config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def login_session_flow(admin_auth_settings):
    """Load the login-session contract (RED until M2-AUTH-004 exists)."""
    try:
        from litemcp.auth.session import LoginSession, create_login_session
    except ImportError as exc:  # pragma: no cover - expected RED result
        raise AssertionError(
            "M2-AUTH-004 must expose litemcp.auth.session.create_login_session "
            "and litemcp.auth.session.LoginSession"
        ) from exc
    return create_login_session, LoginSession


def _make_user() -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        username="login-user",
        username_normalized="login-user",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$" + "a" * 22 + "$" + "b" * 43,
        role="user",
        status="active",
        password_changed_at=now,
    )


def _decode(token: str) -> tuple[dict, dict]:
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(
        token,
        key=JWT_SECRET,
        algorithms=["HS256"],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
    )
    return header, claims


async def _session_key(environment_name: str, session_id: str) -> str:
    return f"litemcp:{environment_name}:admin_session:{session_id}"


async def _user_sessions_key(environment_name: str, user_id: str) -> str:
    return f"litemcp:{environment_name}:user_sessions:{user_id}"


@pytest.mark.asyncio
async def test_access_jwt_has_required_header_and_claims(
    redis_client, login_session_flow, environment_name
) -> None:
    create_login_session, _ = login_session_flow
    user = _make_user()

    result = await create_login_session(redis_client, user)
    redis_client._litemcp_created_keys.append(
        await _session_key(environment_name, result.session_id)
    )
    redis_client._litemcp_created_keys.append(
        await _user_sessions_key(environment_name, str(user.id))
    )

    header, claims = _decode(result.access_token)

    assert header["alg"] == "HS256"
    assert header["typ"] == "at+jwt"
    assert header.get("kid")

    assert claims["iss"] == JWT_ISSUER
    assert claims["aud"] == JWT_AUDIENCE
    assert claims["sub"] == str(user.id)
    assert claims["sid"] == result.session_id
    assert claims["jti"]
    assert claims["token_type"] == "access"
    assert claims["nbf"] <= claims["iat"]
    assert claims["exp"] > claims["iat"]


@pytest.mark.asyncio
async def test_access_ttl_matches_default_configured_value(
    redis_client, login_session_flow, environment_name
) -> None:
    create_login_session, _ = login_session_flow
    user = _make_user()

    result = await create_login_session(redis_client, user)
    redis_client._litemcp_created_keys.append(
        await _session_key(environment_name, result.session_id)
    )
    redis_client._litemcp_created_keys.append(
        await _user_sessions_key(environment_name, str(user.id))
    )

    _, claims = _decode(result.access_token)
    actual_ttl = claims["exp"] - claims["iat"]

    assert actual_ttl == DEFAULT_ACCESS_TTL
    assert result.expires_in == actual_ttl


@pytest.mark.asyncio
async def test_access_ttl_reads_overridden_setting_not_hardcoded(
    redis_client, admin_auth_settings, monkeypatch: pytest.MonkeyPatch, environment_name
) -> None:
    overridden_ttl = 1200
    monkeypatch.setenv("LITEMCP_ADMIN_ACCESS_TTL_SECONDS", str(overridden_ttl))

    from litemcp.core.config import get_settings

    get_settings.cache_clear()
    try:
        from litemcp.auth.session import create_login_session
    except ImportError as exc:  # pragma: no cover - expected RED result
        raise AssertionError(
            "M2-AUTH-004 must expose litemcp.auth.session.create_login_session"
        ) from exc

    user = _make_user()
    result = await create_login_session(redis_client, user)
    redis_client._litemcp_created_keys.append(
        await _session_key(environment_name, result.session_id)
    )
    redis_client._litemcp_created_keys.append(
        await _user_sessions_key(environment_name, str(user.id))
    )

    _, claims = _decode(result.access_token)
    actual_ttl = claims["exp"] - claims["iat"]

    assert actual_ttl == overridden_ttl
    assert actual_ttl != DEFAULT_ACCESS_TTL
    assert result.expires_in == overridden_ttl
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_refresh_session_persisted_in_redis_with_required_fields(
    redis_client, login_session_flow, environment_name
) -> None:
    create_login_session, _ = login_session_flow
    user = _make_user()

    result = await create_login_session(redis_client, user)
    key = await _session_key(environment_name, result.session_id)
    redis_client._litemcp_created_keys.append(key)
    redis_client._litemcp_created_keys.append(
        await _user_sessions_key(environment_name, str(user.id))
    )

    stored = await redis_client.hgetall(key)
    assert stored, f"expected a Redis hash at {key}"
    assert stored["user_id"] == str(user.id)
    for field in (
        "current_secret_hash",
        "created_at",
        "last_refreshed_at",
        "idle_expires_at",
        "absolute_expires_at",
    ):
        assert field in stored, f"missing required field {field!r} in {stored!r}"

    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= DEFAULT_ABSOLUTE_TTL


@pytest.mark.asyncio
async def test_refresh_secret_is_hashed_never_stored_raw(
    redis_client, login_session_flow, environment_name
) -> None:
    create_login_session, _ = login_session_flow
    user = _make_user()

    result = await create_login_session(redis_client, user)
    key = await _session_key(environment_name, result.session_id)
    redis_client._litemcp_created_keys.append(key)
    redis_client._litemcp_created_keys.append(
        await _user_sessions_key(environment_name, str(user.id))
    )

    session_id, _, random_secret = result.refresh_token.partition(".")
    assert session_id == result.session_id
    assert random_secret

    stored = await redis_client.hgetall(key)
    expected_hash = hashlib.sha256(random_secret.encode()).hexdigest()
    assert stored["current_secret_hash"] == expected_hash

    for value in stored.values():
        assert random_secret not in value


@pytest.mark.asyncio
async def test_session_registered_in_user_sessions_set(
    redis_client, login_session_flow, environment_name
) -> None:
    create_login_session, _ = login_session_flow
    user = _make_user()

    result = await create_login_session(redis_client, user)
    redis_client._litemcp_created_keys.append(
        await _session_key(environment_name, result.session_id)
    )
    set_key = await _user_sessions_key(environment_name, str(user.id))
    redis_client._litemcp_created_keys.append(set_key)

    members = await redis_client.smembers(set_key)
    assert result.session_id in members


@pytest.mark.asyncio
async def test_two_logins_for_same_user_are_independent(
    redis_client, login_session_flow, environment_name
) -> None:
    """Simulates two concurrent devices/tabs: a second login must not evict
    or overwrite the first -- both sessions must exist independently.
    """
    create_login_session, _ = login_session_flow
    user = _make_user()

    first = await create_login_session(redis_client, user)
    second = await create_login_session(redis_client, user)

    for result in (first, second):
        redis_client._litemcp_created_keys.append(
            await _session_key(environment_name, result.session_id)
        )
    set_key = await _user_sessions_key(environment_name, str(user.id))
    redis_client._litemcp_created_keys.append(set_key)

    assert first.session_id != second.session_id
    assert first.refresh_token != second.refresh_token

    _, first_claims = _decode(first.access_token)
    _, second_claims = _decode(second.access_token)
    assert first_claims["jti"] != second_claims["jti"]

    first_key = await _session_key(environment_name, first.session_id)
    second_key = await _session_key(environment_name, second.session_id)
    first_stored = await redis_client.hgetall(first_key)
    second_stored = await redis_client.hgetall(second_key)
    assert first_stored, "first login's session must still exist"
    assert second_stored, "second login's session must exist"
    assert first_stored["current_secret_hash"] != second_stored["current_secret_hash"]

    members = await redis_client.smembers(set_key)
    assert first.session_id in members
    assert second.session_id in members


@pytest.mark.asyncio
async def test_idle_and_absolute_expiry_use_documented_defaults(
    redis_client, login_session_flow, environment_name
) -> None:
    """§8.3 default idle timeout 8h (28800s), default absolute timeout 7d
    (604800s), measured from "now" with a generous tolerance window to
    avoid flakiness on slow CI.
    """
    create_login_session, _ = login_session_flow
    user = _make_user()

    before = datetime.now(UTC)
    result = await create_login_session(redis_client, user)
    after = datetime.now(UTC)

    key = await _session_key(environment_name, result.session_id)
    redis_client._litemcp_created_keys.append(key)
    redis_client._litemcp_created_keys.append(
        await _user_sessions_key(environment_name, str(user.id))
    )

    stored = await redis_client.hgetall(key)
    idle_expires_at = datetime.fromisoformat(stored["idle_expires_at"])
    absolute_expires_at = datetime.fromisoformat(stored["absolute_expires_at"])

    expected_idle_low = before.timestamp() + DEFAULT_IDLE_TTL - TOLERANCE_SECONDS
    expected_idle_high = after.timestamp() + DEFAULT_IDLE_TTL + TOLERANCE_SECONDS
    assert expected_idle_low <= idle_expires_at.timestamp() <= expected_idle_high

    expected_abs_low = before.timestamp() + DEFAULT_ABSOLUTE_TTL - TOLERANCE_SECONDS
    expected_abs_high = after.timestamp() + DEFAULT_ABSOLUTE_TTL + TOLERANCE_SECONDS
    assert expected_abs_low <= absolute_expires_at.timestamp() <= expected_abs_high
