"""Application-layer Access JWT verifier contract (M2-AUTH-006).

Pins ``litemcp.auth.access_jwt.verify_access_jwt`` against
docs/architecture/02-admin-auth.md §7.2, §7.3, §10, §11, and §18.

This module does not import any other ``litemcp.auth.*`` package. Tokens are
minted with PyJWT (or assembled by hand for header/claim forgeries). Live
user-status cases run on dedicated PostgreSQL and MySQL databases upgraded to
Alembic head; crypto/header/claim rejections run offline.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiomysql
import asyncpg
import jwt
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from litemcp.core.config import get_settings
from litemcp.db.models import User
from litemcp.db.session import get_session_factory

# ---------------------------------------------------------------------------
# Constants used when minting tokens (must match Settings / architecture).
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
MIGRATIONS_DIR = BACKEND_DIR / "migrations"

POSTGRES_URL = os.environ.get(
    "LITEMCP_TEST_POSTGRES_URL",
    "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp",
)
MYSQL_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_URL",
    "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp",
)
MYSQL_ROOT_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_ROOT_URL",
    "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql",
)

JWT_SECRET = "access-jwt-test-secret-" + ("k" * 32)
JWT_OLD_SECRET = "access-jwt-rotated-old-" + ("o" * 32)
JWT_ISSUER = "https://auth.test.litemcp.invalid/"
JWT_AUDIENCE = "litemcp-admin-api"
AGENT_AUDIENCE = "litemcp-agent-gateway"
CURRENT_KID = "admin-jwt-2026-01"
OLD_KID = "admin-jwt-2025-12"
TYP_ACCESS = "at+jwt"
TOKEN_TYPE_ACCESS = "access"
DUMMY_DB_URL = "postgresql+asyncpg://litemcp:litemcp@127.0.0.1:5433/litemcp"
DUMMY_REDIS_URL = "redis://localhost:6379/0"
DUMMY_FERNET_KEY = "dev-test-key"

_JWT_ENV_NAMES = (
    "LITEMCP_DATABASE_URL",
    "LITEMCP_REDIS_URL",
    "LITEMCP_ENCRYPTION_KEYS",
    "LITEMCP_ADMIN_JWT_SECRET",
    "LITEMCP_ADMIN_JWT_ISSUER",
    "LITEMCP_ADMIN_JWT_AUDIENCE",
    "LITEMCP_ADMIN_JWT_KID",
    "LITEMCP_ADMIN_JWT_ALGORITHM",
    "LITEMCP_ADMIN_JWT_CLOCK_SKEW_SECONDS",
    "LITEMCP_ADMIN_JWT_PREVIOUS_KEYS",
)

_AUDIT_TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Verifier import — the module under test does not exist yet.
# ---------------------------------------------------------------------------


def _load_verifier():
    from litemcp.auth.access_jwt import (
        AccessAuthContext,
        AccessJwtError,
        verify_access_jwt,
    )

    return verify_access_jwt, AccessJwtError, AccessAuthContext


# ---------------------------------------------------------------------------
# Settings / token helpers (PyJWT only; no litemcp.auth.session).
# ---------------------------------------------------------------------------


def _snapshot_env(names: tuple[str, ...]) -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in names}


def _restore_env(saved: dict[str, str | None]) -> None:
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    get_settings.cache_clear()


def _apply_jwt_settings(
    *,
    database_url: str = DUMMY_DB_URL,
    jwt_secret: str = JWT_SECRET,
    kid: str = CURRENT_KID,
    clock_skew_seconds: str = "30",
    previous_keys: str = "",
    algorithm: str = "HS256",
    issuer: str = JWT_ISSUER,
    audience: str = JWT_AUDIENCE,
) -> None:
    os.environ["LITEMCP_DATABASE_URL"] = database_url
    os.environ["LITEMCP_REDIS_URL"] = DUMMY_REDIS_URL
    os.environ["LITEMCP_ENCRYPTION_KEYS"] = DUMMY_FERNET_KEY
    os.environ["LITEMCP_ADMIN_JWT_SECRET"] = jwt_secret
    os.environ["LITEMCP_ADMIN_JWT_ISSUER"] = issuer
    os.environ["LITEMCP_ADMIN_JWT_AUDIENCE"] = audience
    os.environ["LITEMCP_ADMIN_JWT_KID"] = kid
    os.environ["LITEMCP_ADMIN_JWT_ALGORITHM"] = algorithm
    os.environ["LITEMCP_ADMIN_JWT_CLOCK_SKEW_SECONDS"] = clock_skew_seconds
    if previous_keys:
        os.environ["LITEMCP_ADMIN_JWT_PREVIOUS_KEYS"] = previous_keys
    else:
        os.environ.pop("LITEMCP_ADMIN_JWT_PREVIOUS_KEYS", None)
    get_settings.cache_clear()


@pytest.fixture
def jwt_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Offline Settings for header/claim/crypto rejections (no live schema)."""
    saved = _snapshot_env(_JWT_ENV_NAMES)
    try:
        _apply_jwt_settings()
        yield
    finally:
        _restore_env(saved)


def _now() -> datetime:
    return datetime.now(UTC)


def _epoch(when: datetime) -> int:
    return int(when.timestamp())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _mint_access_token(
    *,
    sub: str,
    secret: str = JWT_SECRET,
    algorithm: str = "HS256",
    kid: str = CURRENT_KID,
    typ: str = TYP_ACCESS,
    issuer: str = JWT_ISSUER,
    audience: str | list[str] = JWT_AUDIENCE,
    token_type: str = TOKEN_TYPE_ACCESS,
    sid: str | None = None,
    jti: str | None = None,
    iat: datetime | None = None,
    nbf: datetime | None = None,
    exp: datetime | None = None,
    extra_claims: dict[str, Any] | None = None,
    headers_extra: dict[str, Any] | None = None,
) -> str:
    moment = _now()
    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "sid": sid if sid is not None else str(uuid.uuid4()),
        "jti": jti if jti is not None else str(uuid.uuid4()),
        "iat": _epoch(iat if iat is not None else moment),
        "nbf": _epoch(nbf if nbf is not None else moment),
        "exp": _epoch(exp if exp is not None else moment + timedelta(seconds=900)),
        "token_type": token_type,
    }
    if extra_claims:
        payload.update(extra_claims)
    headers: dict[str, Any] = {"kid": kid, "typ": typ}
    if headers_extra:
        headers.update(headers_extra)
    return jwt.encode(payload, secret, algorithm=algorithm, headers=headers)


def _assemble_token(header: dict[str, Any], payload: dict[str, Any], *, sig: bytes = b"") -> str:
    header_seg = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_seg = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig_seg = _b64url(sig) if sig else ""
    return f"{header_seg}.{payload_seg}.{sig_seg}"


def _valid_payload(sub: str) -> dict[str, Any]:
    moment = _now()
    return {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": sub,
        "sid": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "iat": _epoch(moment),
        "nbf": _epoch(moment),
        "exp": _epoch(moment + timedelta(seconds=900)),
        "token_type": TOKEN_TYPE_ACCESS,
    }


async def _expect_invalid_token(token: str) -> None:
    verify_access_jwt, AccessJwtError, _ctx = _load_verifier()
    with pytest.raises(AccessJwtError) as caught:
        await verify_access_jwt(token)
    assert caught.value.code == "INVALID_TOKEN"


# ---------------------------------------------------------------------------
# Live dialect: unique empty DB, Alembic upgrade head, process Settings.
# ---------------------------------------------------------------------------


def _make_alembic_config() -> Config:
    assert ALEMBIC_INI.is_file(), f"missing Alembic config: {ALEMBIC_INI}"
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _run_upgrade_in_thread(cfg: Config, url: str) -> None:
    cfg.set_main_option("sqlalchemy.url", url)
    error: BaseException | None = None

    def _upgrade() -> None:
        nonlocal error
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:  # noqa: BLE001 - surfaced in the caller thread
            error = exc

    thread = threading.Thread(target=_upgrade, name="alembic-upgrade-head")
    thread.start()
    thread.join()
    if error is not None:
        raise error


def _url_with_database(base_url: str, database: str) -> str:
    return make_url(base_url).set(database=database).render_as_string(
        hide_password=False
    )


def _unique_database_name(prefix: str) -> str:
    return f"{prefix}_{time.time_ns()}_{os.getpid()}"


def _parse_url(base_url: str) -> dict[str, Any]:
    url = make_url(base_url)
    return {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }


async def _pg_create_database(params: dict[str, Any], name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _pg_drop_database(params: dict[str, Any], name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def _mysql_kwargs(url: str, database: str | None = None) -> dict[str, Any]:
    parsed = make_url(url)
    return {
        "host": parsed.host,
        "port": parsed.port or 3306,
        "user": parsed.username,
        "password": parsed.password,
        "db": database if database is not None else parsed.database,
        "autocommit": True,
    }


async def _mysql_create_database(admin_url: str, name: str, app_user: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(admin_url))
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}`")
            safe_user = app_user.replace("'", "''")
            await cur.execute(
                f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{safe_user}'@'%'"
            )
    finally:
        conn.close()


async def _mysql_drop_database(admin_url: str, name: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(admin_url))
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        conn.close()


@pytest_asyncio.fixture(params=["postgres", "mysql"], ids=["postgres", "mysql"])
async def live_db(request: pytest.FixtureRequest) -> AsyncIterator[str]:
    """Fresh dialect database at Alembic head; process settings point at it."""
    saved = _snapshot_env(_JWT_ENV_NAMES)
    dialect = request.param
    db_name = ""
    factory = None
    try:
        if dialect == "postgres":
            params = _parse_url(POSTGRES_URL)
            db_name = _unique_database_name("litemcp_ajwt")
            await _pg_create_database(params, db_name)
            database_url = _url_with_database(POSTGRES_URL, db_name)
        else:
            app_user = make_url(MYSQL_URL).username
            assert app_user is not None
            db_name = _unique_database_name("litemcp_ajwt")
            await _mysql_create_database(MYSQL_ROOT_URL, db_name, app_user)
            database_url = _url_with_database(MYSQL_URL, db_name)

        _run_upgrade_in_thread(_make_alembic_config(), database_url)
        _apply_jwt_settings(database_url=database_url)
        factory = get_session_factory()
        yield dialect
    finally:
        if factory is not None:
            await factory.dispose()
        _restore_env(saved)
        if db_name:
            if dialect == "postgres":
                await _pg_drop_database(_parse_url(POSTGRES_URL), db_name)
            else:
                await _mysql_drop_database(MYSQL_ROOT_URL, db_name)


def _make_user(**overrides: Any) -> User:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "username": "alice",
        "username_normalized": "alice",
        "password_hash": "argon2id$v=19$m=65536,t=3,p=4$dGVzdA",
        "role": "user",
        "status": "active",
        "password_changed_at": datetime(2020, 1, 1, tzinfo=UTC),
        "created_at": _AUDIT_TS,
        "created_by": "test",
        "updated_at": _AUDIT_TS,
        "updated_by": "test",
        "row_version": 1,
    }
    values.update(overrides)
    return User(**values)


async def _persist_user(**overrides: Any) -> User:
    user = _make_user(**overrides)
    factory = get_session_factory()
    async with factory.session() as session:
        session.add(user)
        await session.commit()
    return user


# ---------------------------------------------------------------------------
# Offline: header, algorithm, signature, issuer, audience, time, shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alg_none_is_rejected(jwt_settings: None) -> None:
    token = _assemble_token(
        {"alg": "none", "kid": CURRENT_KID, "typ": TYP_ACCESS},
        _valid_payload(str(uuid.uuid4())),
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_algorithm_outside_server_allowlist_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), algorithm="HS384")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_wrong_signature_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), secret="not-the-admin-jwt-secret-" + ("x" * 16))
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_signature_from_encryption_key_is_not_accepted(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), secret=DUMMY_FERNET_KEY)
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_unknown_kid_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), kid="unknown-kid")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_missing_kid_is_rejected(jwt_settings: None) -> None:
    moment = _now()
    token = jwt.encode(
        {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "iat": _epoch(moment),
            "nbf": _epoch(moment),
            "exp": _epoch(moment + timedelta(seconds=900)),
            "token_type": TOKEN_TYPE_ACCESS,
        },
        JWT_SECRET,
        algorithm="HS256",
        headers={"typ": TYP_ACCESS},
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_wrong_typ_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), typ="JWT")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_step_up_typ_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), typ="stepup+jwt")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_wrong_token_type_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), token_type="refresh")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_wrong_issuer_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), issuer="https://evil.example/")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_issuer_is_not_derived_from_request_host(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), issuer="localhost:8000")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_wrong_audience_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), audience="other-api")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_agent_gateway_audience_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()), audience=AGENT_AUDIENCE)
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_expired_token_beyond_skew_is_rejected(jwt_settings: None) -> None:
    moment = _now()
    token = _mint_access_token(
        sub=str(uuid.uuid4()),
        iat=moment - timedelta(seconds=120),
        nbf=moment - timedelta(seconds=120),
        exp=moment - timedelta(seconds=60),
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_nbf_in_future_beyond_skew_is_rejected(jwt_settings: None) -> None:
    moment = _now()
    token = _mint_access_token(
        sub=str(uuid.uuid4()),
        iat=moment,
        nbf=moment + timedelta(seconds=120),
        exp=moment + timedelta(seconds=900),
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_malformed_token_is_rejected(jwt_settings: None) -> None:
    await _expect_invalid_token("not-a-jwt")


@pytest.mark.asyncio
async def test_truncated_token_is_rejected(jwt_settings: None) -> None:
    await _expect_invalid_token("abc.def")


@pytest.mark.asyncio
async def test_abnormally_large_token_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(
        sub=str(uuid.uuid4()),
        extra_claims={"pad": "x" * (32 * 1024)},
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_unparseable_payload_is_rejected(jwt_settings: None) -> None:
    header = _b64url(
        json.dumps(
            {"alg": "HS256", "kid": CURRENT_KID, "typ": TYP_ACCESS},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    await _expect_invalid_token(f"{header}.%%%not-json%%%.sig")


@pytest.mark.asyncio
async def test_duplicate_issuer_claim_is_rejected(jwt_settings: None) -> None:
    payload = _valid_payload(str(uuid.uuid4()))
    payload_json = json.dumps(payload, separators=(",", ":"))
    payload_json = payload_json[:-1] + ',"iss":"https://evil.example/"}'
    header_seg = _b64url(
        json.dumps(
            {"alg": "HS256", "kid": CURRENT_KID, "typ": TYP_ACCESS},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    payload_seg = _b64url(payload_json.encode("utf-8"))
    signing_input = f"{header_seg}.{payload_seg}"
    signature = hmac.new(
        JWT_SECRET.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    token = f"{signing_input}.{_b64url(signature)}"
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_wrong_typed_exp_claim_is_rejected(jwt_settings: None) -> None:
    payload = _valid_payload(str(uuid.uuid4()))
    payload["exp"] = "not-a-timestamp"
    token = jwt.encode(
        payload,
        JWT_SECRET,
        algorithm="HS256",
        headers={"kid": CURRENT_KID, "typ": TYP_ACCESS},
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_non_uuid_sub_is_rejected(jwt_settings: None) -> None:
    token = _mint_access_token(sub="not-a-uuid")
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_numeric_sub_is_rejected(jwt_settings: None) -> None:
    payload = _valid_payload(str(uuid.uuid4()))
    payload["sub"] = 12345
    token = jwt.encode(
        payload,
        JWT_SECRET,
        algorithm="HS256",
        headers={"kid": CURRENT_KID, "typ": TYP_ACCESS},
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_missing_jti_is_rejected(jwt_settings: None) -> None:
    moment = _now()
    token = jwt.encode(
        {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "iat": _epoch(moment),
            "nbf": _epoch(moment),
            "exp": _epoch(moment + timedelta(seconds=900)),
            "token_type": TOKEN_TYPE_ACCESS,
        },
        JWT_SECRET,
        algorithm="HS256",
        headers={"kid": CURRENT_KID, "typ": TYP_ACCESS},
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_missing_sid_is_rejected(jwt_settings: None) -> None:
    moment = _now()
    token = jwt.encode(
        {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "sub": str(uuid.uuid4()),
            "jti": str(uuid.uuid4()),
            "iat": _epoch(moment),
            "nbf": _epoch(moment),
            "exp": _epoch(moment + timedelta(seconds=900)),
            "token_type": TOKEN_TYPE_ACCESS,
        },
        JWT_SECRET,
        algorithm="HS256",
        headers={"kid": CURRENT_KID, "typ": TYP_ACCESS},
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_removed_kid_does_not_verify(jwt_settings: None) -> None:
    token = _mint_access_token(
        sub=str(uuid.uuid4()), kid=OLD_KID, secret=JWT_OLD_SECRET
    )
    await _expect_invalid_token(token)


@pytest.mark.asyncio
async def test_clock_skew_setting_is_honored_for_expiry(jwt_settings: None) -> None:
    _apply_jwt_settings(clock_skew_seconds="5")
    moment = _now()
    token = _mint_access_token(
        sub=str(uuid.uuid4()),
        iat=moment - timedelta(seconds=30),
        nbf=moment - timedelta(seconds=30),
        exp=moment - timedelta(seconds=20),
    )
    await _expect_invalid_token(token)


# ---------------------------------------------------------------------------
# Live dialects: user row, DB role, TOKEN_STALE, fail-closed, key rotation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_token_returns_database_role_not_jwt_role(live_db: str) -> None:
    user = await _persist_user(role="user", status="active")
    sid = str(uuid.uuid4())
    jti = str(uuid.uuid4())
    token = _mint_access_token(
        sub=str(user.id),
        sid=sid,
        jti=jti,
        extra_claims={"role": "admin"},
    )
    verify_access_jwt, _err, _ctx = _load_verifier()
    context = await verify_access_jwt(token)
    assert context.user_id == user.id
    assert context.username == user.username
    assert context.role == "user"
    assert context.sid == sid
    assert context.jti == jti


@pytest.mark.asyncio
async def test_admin_database_role_is_used_when_jwt_role_is_user(live_db: str) -> None:
    user = await _persist_user(role="admin", status="active")
    token = _mint_access_token(sub=str(user.id), extra_claims={"role": "user"})
    verify_access_jwt, _err, _ctx = _load_verifier()
    context = await verify_access_jwt(token)
    assert context.role == "admin"


@pytest.mark.asyncio
async def test_missing_user_is_rejected(live_db: str) -> None:
    token = _mint_access_token(sub=str(uuid.uuid4()))
    verify_access_jwt, AccessJwtError, _ctx = _load_verifier()
    with pytest.raises(AccessJwtError) as caught:
        await verify_access_jwt(token)
    assert caught.value.code == "USER_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_disabled_user_is_rejected(live_db: str) -> None:
    user = await _persist_user(status="disabled")
    token = _mint_access_token(sub=str(user.id))
    verify_access_jwt, AccessJwtError, _ctx = _load_verifier()
    with pytest.raises(AccessJwtError) as caught:
        await verify_access_jwt(token)
    assert caught.value.code == "USER_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_locked_user_is_rejected(live_db: str) -> None:
    user = await _persist_user(status="locked")
    token = _mint_access_token(sub=str(user.id))
    verify_access_jwt, AccessJwtError, _ctx = _load_verifier()
    with pytest.raises(AccessJwtError) as caught:
        await verify_access_jwt(token)
    assert caught.value.code == "USER_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_iat_before_password_changed_at_is_token_stale(live_db: str) -> None:
    changed = _now()
    user = await _persist_user(password_changed_at=changed)
    token = _mint_access_token(
        sub=str(user.id),
        iat=changed - timedelta(seconds=60),
        nbf=changed - timedelta(seconds=60),
        exp=changed + timedelta(seconds=900),
    )
    verify_access_jwt, AccessJwtError, _ctx = _load_verifier()
    with pytest.raises(AccessJwtError) as caught:
        await verify_access_jwt(token)
    assert caught.value.code == "TOKEN_STALE"


@pytest.mark.asyncio
async def test_iat_at_or_after_password_changed_at_succeeds(live_db: str) -> None:
    changed = _now() - timedelta(seconds=30)
    user = await _persist_user(password_changed_at=changed)
    token = _mint_access_token(
        sub=str(user.id),
        iat=changed + timedelta(seconds=1),
        nbf=changed + timedelta(seconds=1),
        exp=changed + timedelta(seconds=900),
    )
    verify_access_jwt, _err, _ctx = _load_verifier()
    context = await verify_access_jwt(token)
    assert context.user_id == user.id


@pytest.mark.asyncio
async def test_expired_within_default_clock_skew_is_accepted(live_db: str) -> None:
    user = await _persist_user()
    moment = _now()
    token = _mint_access_token(
        sub=str(user.id),
        iat=moment - timedelta(seconds=40),
        nbf=moment - timedelta(seconds=40),
        exp=moment - timedelta(seconds=10),
    )
    verify_access_jwt, _err, _ctx = _load_verifier()
    context = await verify_access_jwt(token)
    assert context.user_id == user.id


@pytest.mark.asyncio
async def test_old_kid_still_verifies_while_in_keyring(live_db: str) -> None:
    _apply_jwt_settings(
        database_url=get_settings().database_url.get_secret_value(),
        previous_keys=json.dumps({OLD_KID: JWT_OLD_SECRET}),
    )
    user = await _persist_user()
    token = _mint_access_token(sub=str(user.id), kid=OLD_KID, secret=JWT_OLD_SECRET)
    verify_access_jwt, _err, _ctx = _load_verifier()
    context = await verify_access_jwt(token)
    assert context.user_id == user.id


@pytest.mark.asyncio
async def test_old_kid_rejected_after_removed_from_keyring(live_db: str) -> None:
    user = await _persist_user()
    token = _mint_access_token(sub=str(user.id), kid=OLD_KID, secret=JWT_OLD_SECRET)
    verify_access_jwt, AccessJwtError, _ctx = _load_verifier()
    with pytest.raises(AccessJwtError) as caught:
        await verify_access_jwt(token)
    assert caught.value.code == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_database_unavailable_fails_closed_without_trusting_jwt_role(
    live_db: str,
) -> None:
    user = await _persist_user(role="user", status="active")
    token = _mint_access_token(sub=str(user.id), extra_claims={"role": "admin"})
    saved_url = get_settings().database_url.get_secret_value()
    os.environ["LITEMCP_DATABASE_URL"] = (
        "postgresql+asyncpg://litemcp:litemcp@127.0.0.1:1/does_not_exist"
    )
    get_settings.cache_clear()
    try:
        verify_access_jwt, AccessJwtError, _ctx = _load_verifier()
        with pytest.raises(AccessJwtError) as caught:
            await verify_access_jwt(token)
        assert caught.value.code == "DATABASE_UNAVAILABLE"
    finally:
        os.environ["LITEMCP_DATABASE_URL"] = saved_url
        get_settings.cache_clear()
