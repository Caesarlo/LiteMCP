"""Failing contract tests for refresh-token replay (M2-AUTH-008).

Application-layer only: presenting a retired or forged refresh secret for a
live session must revoke the Redis token family and persist
``auth.refresh_reuse_detected``. Missing or expired sessions are ordinary
rejects and must not write that audit action.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiomysql
import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from cryptography.fernet import Fernet
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.engine import URL

from litemcp.auth.refresh import RefreshRejected, rotate_refresh_token
from litemcp.auth.session import create_login_session
from litemcp.core.config import Settings, get_settings
from litemcp.db.models import User
from litemcp.db.session import AsyncSessionFactory, get_session_factory

BACKEND_DIR = Path(__file__).resolve().parents[2]
REUSE_ACTION = "auth.refresh_reuse_detected"
REUSE_RESULT = "denied"

PG_PORT = int(os.environ.get("POSTGRES_PORT", "5433"))
PG_USER = "litemcp"
PG_PASSWORD = "litemcp"
PG_ADMIN_DB = "litemcp"
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3307"))
MYSQL_ROOT_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "litemcp-root")
MYSQL_APP_USER = "litemcp"
MYSQL_APP_PASSWORD = "litemcp"
REDIS_URL = os.environ.get("LITEMCP_TEST_REDIS_URL", "redis://localhost:6379/0")

_REQUIRED_ENV = (
    "LITEMCP_DATABASE_URL",
    "LITEMCP_REDIS_URL",
    "LITEMCP_ENCRYPTION_KEYS",
    "LITEMCP_ENVIRONMENT",
    "LITEMCP_ADMIN_JWT_SECRET",
    "LITEMCP_ADMIN_JWT_ISSUER",
)

_AUDIT_TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@dataclass(frozen=True)
class LiveAuth:
    dialect: str
    factory: AsyncSessionFactory
    redis: redis_asyncio.Redis
    settings: Settings


async def _run_upgrade(database_url: str) -> None:
    config = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(alembic_command.upgrade, config, "head")


def _postgres_url(db_name: str) -> str:
    return str(
        URL.create(
            "postgresql+asyncpg",
            username=PG_USER,
            password=PG_PASSWORD,
            host="localhost",
            port=PG_PORT,
            database=db_name,
        ).render_as_string(hide_password=False)
    )


def _mysql_url(db_name: str) -> str:
    return str(
        URL.create(
            "mysql+aiomysql",
            username=MYSQL_APP_USER,
            password=MYSQL_APP_PASSWORD,
            host="localhost",
            port=MYSQL_PORT,
            database=db_name,
        ).render_as_string(hide_password=False)
    )


async def _provision_postgres() -> tuple[str, str]:
    db_name = f"litemcp_replay_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(
        host="localhost",
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_ADMIN_DB,
    )
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()
    database_url = _postgres_url(db_name)
    await _run_upgrade(database_url)
    return database_url, db_name


async def _provision_mysql() -> tuple[str, str]:
    db_name = f"litemcp_replay_{uuid.uuid4().hex[:12]}"
    admin = await aiomysql.connect(
        host="localhost",
        port=MYSQL_PORT,
        user="root",
        password=MYSQL_ROOT_PASSWORD,
        autocommit=True,
    )
    try:
        cursor = await admin.cursor()
        await cursor.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4")
        await cursor.execute(
            f"GRANT ALL PRIVILEGES ON `{db_name}`.* "
            f"TO '{MYSQL_APP_USER}'@'%%'",
            (),
        )
        await cursor.close()
    finally:
        admin.close()
    database_url = _mysql_url(db_name)
    await _run_upgrade(database_url)
    return database_url, db_name


async def _drop_postgres(db_name: str) -> None:
    admin = await asyncpg.connect(
        host="localhost",
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_ADMIN_DB,
    )
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _drop_mysql(db_name: str) -> None:
    admin = await aiomysql.connect(
        host="localhost",
        port=MYSQL_PORT,
        user="root",
        password=MYSQL_ROOT_PASSWORD,
        autocommit=True,
    )
    try:
        cursor = await admin.cursor()
        await cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        await cursor.close()
    finally:
        admin.close()


def _session_namespace(settings: Settings) -> str:
    custom = settings.admin_session_environment.strip()
    if custom:
        return custom
    return str(settings.environment.value)


def _admin_session_key(settings: Settings, session_id: str) -> str:
    return f"litemcp:{_session_namespace(settings)}:admin_session:{session_id}"


def _user_sessions_key(settings: Settings, user_id: uuid.UUID) -> str:
    return f"litemcp:{_session_namespace(settings)}:user_sessions:{user_id}"


def _split_refresh(refresh_token: str) -> tuple[str, str]:
    session_id, secret = refresh_token.split(".", 1)
    return session_id, secret


def _forge_secret(refresh_token: str) -> str:
    session_id, secret = _split_refresh(refresh_token)
    candidate = secrets.token_urlsafe(32)
    if len(candidate) > len(secret):
        candidate = candidate[: len(secret)]
    elif len(candidate) < len(secret):
        candidate = (candidate + secret)[: len(secret)]
    if candidate == secret:
        flip = "A" if secret[0] != "A" else "B"
        candidate = flip + secret[1:]
    return f"{session_id}.{candidate}"


def _make_user(**overrides: Any) -> User:
    slug = f"replay-{uuid.uuid4().hex[:8]}"
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "username": slug,
        "username_normalized": slug,
        "password_hash": "argon2id$v=19$m=65536,t=3,p=4$dGVzdA",
        "role": "admin",
        "status": "active",
        "password_changed_at": _AUDIT_TS,
        "last_login_at": None,
        "failed_login_window_started_at": None,
        "locked_until": None,
        "failed_login_count": 0,
        "created_at": _AUDIT_TS,
        "created_by": "test",
        "updated_at": _AUDIT_TS,
        "updated_by": "test",
        "row_version": 1,
    }
    values.update(overrides)
    allowed = {column.name for column in User.__table__.columns}
    return User(**{key: value for key, value in values.items() if key in allowed})


async def _persist_user(factory: AsyncSessionFactory, user: User) -> User:
    async with factory.session() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _reuse_audit_rows(factory: AsyncSessionFactory) -> list[dict[str, Any]]:
    async with factory.session() as session:
        result = await session.execute(
            text(
                "SELECT request_id, actor_type, actor_id, action, "
                "resource_type, resource_id, result, reason_code, "
                "source_ip, user_agent, changes, metadata "
                "FROM audit_event WHERE action = :action"
            ),
            {"action": REUSE_ACTION},
        )
        rows = result.mappings().all()
    return [dict(row) for row in rows]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _assert_no_secret_material(
    rows: list[dict[str, Any]],
    *,
    refresh_tokens: tuple[str, ...],
    secrets_plain: tuple[str, ...],
    secret_hashes: tuple[str, ...],
) -> None:
    blob = "\n".join(
        f"{key}={_stringify(value)}"
        for row in rows
        for key, value in row.items()
    ).lower()
    for token in refresh_tokens:
        assert token.lower() not in blob
    for secret in secrets_plain:
        assert secret.lower() not in blob
    for digest in secret_hashes:
        if digest:
            assert digest.lower() not in blob
    for leaked in ("authorization", "cookie", "set-cookie", "bearer "):
        assert leaked not in blob


async def _family_revoked(
    harness: LiveAuth, *, session_id: str, user_id: uuid.UUID
) -> None:
    session_key = _admin_session_key(harness.settings, session_id)
    user_key = _user_sessions_key(harness.settings, user_id)
    assert await harness.redis.exists(session_key) == 0
    assert await harness.redis.sismember(user_key, session_id) == 0


@pytest_asyncio.fixture(params=["postgres", "mysql"])
async def live_auth(request: pytest.FixtureRequest) -> AsyncIterator[LiveAuth]:
    dialect = str(request.param)
    if dialect == "postgres":
        database_url, db_name = await _provision_postgres()
    else:
        database_url, db_name = await _provision_mysql()

    redis_client = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
    saved_env = {name: os.environ.get(name) for name in _REQUIRED_ENV}
    factory: AsyncSessionFactory | None = None
    try:
        try:
            await redis_client.ping()
        except (OSError, RedisError) as exc:
            pytest.skip(f"live Redis not reachable at {REDIS_URL}: {exc}")
        os.environ["LITEMCP_DATABASE_URL"] = database_url
        os.environ["LITEMCP_REDIS_URL"] = REDIS_URL
        os.environ["LITEMCP_ENCRYPTION_KEYS"] = Fernet.generate_key().decode()
        os.environ["LITEMCP_ENVIRONMENT"] = "test"
        os.environ["LITEMCP_ADMIN_JWT_SECRET"] = "test-admin-jwt-secret-" + ("s" * 32)
        os.environ["LITEMCP_ADMIN_JWT_ISSUER"] = "https://auth.test.litemcp.invalid/"
        get_settings.cache_clear()
        settings = get_settings()
        factory = get_session_factory()
        yield LiveAuth(
            dialect=dialect,
            factory=factory,
            redis=redis_client,
            settings=settings,
        )
    finally:
        get_settings.cache_clear()
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if factory is not None:
            await factory.dispose()
        await redis_client.aclose()
        if dialect == "postgres":
            await _drop_postgres(db_name)
        else:
            await _drop_mysql(db_name)


async def _login_and_rotate(harness: LiveAuth) -> tuple[User, str, str, str]:
    """Return user, original refresh token, rotated refresh token, session id."""
    user = await _persist_user(harness.factory, _make_user())
    issued = await create_login_session(harness.redis, user)
    rotated = await rotate_refresh_token(
        issued.refresh_token,
        redis=harness.redis,
        session_factory=harness.factory,
        settings=harness.settings,
    )
    return user, issued.refresh_token, rotated.refresh_token, issued.session_id


@pytest.mark.asyncio
async def test_rotated_token_replay_revokes_family_audits_and_blocks_successor(
    live_auth: LiveAuth,
) -> None:
    user, old_token, current_token, session_id = await _login_and_rotate(live_auth)
    _, old_secret = _split_refresh(old_token)
    stored_hash = await live_auth.redis.hget(
        _admin_session_key(live_auth.settings, session_id),
        "current_secret_hash",
    )

    with pytest.raises(RefreshRejected):
        await rotate_refresh_token(
            old_token,
            redis=live_auth.redis,
            session_factory=live_auth.factory,
            settings=live_auth.settings,
        )

    await _family_revoked(live_auth, session_id=session_id, user_id=user.id)

    with pytest.raises(RefreshRejected):
        await rotate_refresh_token(
            current_token,
            redis=live_auth.redis,
            session_factory=live_auth.factory,
            settings=live_auth.settings,
        )

    rows = await _reuse_audit_rows(live_auth.factory)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == REUSE_ACTION
    assert row["result"] == REUSE_RESULT
    assert row["resource_id"] == session_id
    _assert_no_secret_material(
        rows,
        refresh_tokens=(old_token, current_token),
        secrets_plain=(old_secret, _split_refresh(current_token)[1]),
        secret_hashes=(str(stored_hash or ""),),
    )


@pytest.mark.asyncio
async def test_forged_secret_for_existing_session_revokes_family_and_audits(
    live_auth: LiveAuth,
) -> None:
    user = await _persist_user(live_auth.factory, _make_user())
    issued = await create_login_session(live_auth.redis, user)
    forged = _forge_secret(issued.refresh_token)
    stored_hash = await live_auth.redis.hget(
        _admin_session_key(live_auth.settings, issued.session_id),
        "current_secret_hash",
    )

    with pytest.raises(RefreshRejected):
        await rotate_refresh_token(
            forged,
            redis=live_auth.redis,
            session_factory=live_auth.factory,
            settings=live_auth.settings,
        )

    await _family_revoked(
        live_auth, session_id=issued.session_id, user_id=user.id
    )
    rows = await _reuse_audit_rows(live_auth.factory)
    assert len(rows) == 1
    assert rows[0]["action"] == REUSE_ACTION
    assert rows[0]["result"] == REUSE_RESULT
    assert rows[0]["resource_id"] == issued.session_id
    _assert_no_secret_material(
        rows,
        refresh_tokens=(issued.refresh_token, forged),
        secrets_plain=(
            _split_refresh(issued.refresh_token)[1],
            _split_refresh(forged)[1],
        ),
        secret_hashes=(str(stored_hash or ""),),
    )


@pytest.mark.asyncio
async def test_unknown_session_id_rejects_without_reuse_audit(
    live_auth: LiveAuth,
) -> None:
    user = await _persist_user(live_auth.factory, _make_user())
    issued = await create_login_session(live_auth.redis, user)
    session_id, secret = _split_refresh(issued.refresh_token)
    flipped = ("0" if session_id[0] != "0" else "1") + session_id[1:]
    unknown = f"{flipped}.{secret}"

    with pytest.raises(RefreshRejected):
        await rotate_refresh_token(
            unknown,
            redis=live_auth.redis,
            session_factory=live_auth.factory,
            settings=live_auth.settings,
        )

    assert await live_auth.redis.exists(
        _admin_session_key(live_auth.settings, issued.session_id)
    )
    assert await _reuse_audit_rows(live_auth.factory) == []


@pytest.mark.parametrize("expiry_kind", ["idle", "absolute"])
@pytest.mark.asyncio
async def test_expired_session_rejects_without_reuse_audit(
    live_auth: LiveAuth, expiry_kind: str
) -> None:
    user = await _persist_user(live_auth.factory, _make_user())
    issued = await create_login_session(live_auth.redis, user)
    assert issued.session_id
    if expiry_kind == "idle":
        frozen_now = datetime.now(UTC) + timedelta(hours=9)
    else:
        frozen_now = datetime.now(UTC) + timedelta(days=8)

    with pytest.raises(RefreshRejected):
        await rotate_refresh_token(
            issued.refresh_token,
            redis=live_auth.redis,
            session_factory=live_auth.factory,
            settings=live_auth.settings,
            now=frozen_now,
        )

    assert await _reuse_audit_rows(live_auth.factory) == []
    assert await live_auth.redis.exists(
        _admin_session_key(live_auth.settings, issued.session_id)
    )
