"""Legacy bcrypt password upgrade-on-login (M2-AUTH-003).

docs/architecture/02-admin-auth.md §5.2 (verbatim translation): "If
migrating existing bcrypt hashes, upon successful verification immediately
upgrade to Argon2id. bcrypt is only for legacy-data compatibility, not the
default for new passwords."

This test file is scoped to a pure DB-layer verification+migration
function -- there is no login/HTTP endpoint in this codebase yet, mirroring
how the sibling M2-AUTH-001 (bootstrap) and M2-AUTH-002 (Argon2id hashing)
features were built as DB/hashing-layer functions before any endpoint
exists. Login rate-limiting, account lockout, password length/blocklist
rules, and the pepper/Secret-Manager requirement are explicitly out of
scope here -- they belong to other feature IDs.

Public API contract this test pins (RED until M2-AUTH-003 exists):

    async def verify_and_migrate_password(
        session: AsyncSession, user: User, password: str
    ) -> bool

exposed from ``litemcp.auth.password_migration``. Convention chosen:

- Returns ``True`` when ``password`` matches the user's currently stored
  hash (whether that hash is bcrypt or Argon2id).
- Raises ``litemcp.auth.password.PasswordVerificationError`` (the same
  exception M2-AUTH-002's ``verify_password`` raises) when ``password``
  does not match -- no boolean ``False`` return on failure, so a caller
  cannot accidentally ignore a failed check.
- Bcrypt hash (``$2a$`` / ``$2b$`` / ``$2y$`` prefix): verified via the real
  ``bcrypt`` library. On success, immediately rehashes the plaintext with
  ``litemcp.auth.password.hash_password`` (Argon2id) and persists the new
  hash onto ``user.password_hash`` inside the caller's already-open
  transaction (no internal ``session.begin()``/``commit()``, following the
  ``bootstrap.py`` convention). On failure, the stored digest -- in memory
  and in the database -- is left byte-for-byte untouched.
- Argon2id hash (``$argon2id$`` prefix): verified via M2-AUTH-002's
  ``verify_password`` directly, no migration, no DB write either way.

These tests run against real PostgreSQL and MySQL databases, provisioned
fresh per test and migrated to head, following the same live-DB fixture
pattern used by ``tests/auth/test_bootstrap.py``. A missing
``litemcp.auth.password_migration`` module is the expected RED failure --
the migration flow does not exist yet.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import bcrypt
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from litemcp.auth.password import (
    PasswordVerificationError,
    hash_password,
    verify_password,
)
from litemcp.db.models import User

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


def _make_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _upgrade(url: str) -> None:
    import threading

    cfg = _make_config()
    cfg.set_main_option("sqlalchemy.url", url)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            command.upgrade(cfg, "head")
        except Exception as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


def _db_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


async def _create_db(dialect: str, name: str) -> None:
    base = POSTGRES_URL if dialect == "postgres" else MYSQL_ROOT_URL
    engine = create_async_engine(base, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            if dialect == "postgres":
                await conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
            else:
                await conn.exec_driver_sql(f"CREATE DATABASE `{name}`")
                await conn.exec_driver_sql(
                    "CREATE USER IF NOT EXISTS 'litemcp'@'%%' IDENTIFIED BY 'litemcp'"
                )
                await conn.exec_driver_sql(
                    f"GRANT ALL PRIVILEGES ON `{name}`.* TO 'litemcp'@'%%'"
                )
                await conn.exec_driver_sql("FLUSH PRIVILEGES")
    finally:
        await engine.dispose()


async def _drop_db(dialect: str, name: str) -> None:
    base = POSTGRES_URL if dialect == "postgres" else MYSQL_ROOT_URL
    engine = create_async_engine(base, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            if dialect == "postgres":
                await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            else:
                await conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def live_db(request: pytest.FixtureRequest) -> tuple[AsyncEngine, str]:
    dialect = request.param
    base_url = POSTGRES_URL if dialect == "postgres" else MYSQL_URL
    name = _db_name("litemcp_pwmig")
    await _create_db(dialect, name)
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    try:
        _upgrade(url)
        engine = create_async_engine(url)
    except Exception:
        await _drop_db(dialect, name)
        raise
    try:
        yield engine, dialect
    finally:
        await engine.dispose()
        await _drop_db(dialect, name)


@pytest.fixture
def migration_flow():
    """Load the migration contract (RED until M2-AUTH-003 exists)."""
    try:
        from litemcp.auth.password_migration import verify_and_migrate_password
    except ImportError as exc:  # pragma: no cover - expected RED result
        raise AssertionError(
            "M2-AUTH-003 must expose "
            "litemcp.auth.password_migration.verify_and_migrate_password"
        ) from exc
    return verify_and_migrate_password


async def _seed_user(engine: AsyncEngine, *, password_hash: str, username: str) -> User:
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        username=username,
        username_normalized=username,
        password_hash=password_hash,
        role="user",
        status="active",
        password_changed_at=now,
        created_at=now,
        created_by="seed",
        updated_at=now,
        updated_by="seed",
    )
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(user)
        await session.commit()
    return user


async def _reload_hash(engine: AsyncEngine, user_id: uuid.UUID) -> str:
    async with AsyncSession(engine) as session:
        row = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        return row.password_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_bcrypt_user_correct_password_migrates_to_argon2id(
    live_db, migration_flow
) -> None:
    engine, _ = live_db
    verify_and_migrate_password = migration_flow
    plaintext_password = "legacy bcrypt password 1!"
    bcrypt_hash = bcrypt.hashpw(plaintext_password.encode(), bcrypt.gensalt()).decode()
    user = await _seed_user(engine, password_hash=bcrypt_hash, username="legacy-user")

    async with AsyncSession(engine) as session, session.begin():
        db_user = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()
        result = await verify_and_migrate_password(
            session, db_user, plaintext_password
        )

    assert result is True

    stored_hash = await _reload_hash(engine, user.id)
    assert stored_hash != bcrypt_hash
    assert stored_hash.startswith("$argon2id$")
    assert verify_password(stored_hash, plaintext_password) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_bcrypt_user_wrong_password_leaves_digest_untouched(
    live_db, migration_flow
) -> None:
    engine, _ = live_db
    verify_and_migrate_password = migration_flow
    plaintext_password = "legacy bcrypt password 2!"
    bcrypt_hash = bcrypt.hashpw(plaintext_password.encode(), bcrypt.gensalt()).decode()
    user = await _seed_user(engine, password_hash=bcrypt_hash, username="legacy-user-2")

    async with AsyncSession(engine) as session, session.begin():
        db_user = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()
        with pytest.raises(PasswordVerificationError):
            await verify_and_migrate_password(session, db_user, "wrong password")

    stored_hash = await _reload_hash(engine, user.id)
    assert stored_hash == bcrypt_hash
    assert stored_hash.startswith(("$2a$", "$2b$", "$2y$"))


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_argon2id_user_correct_password_succeeds_without_rehash(
    live_db, migration_flow
) -> None:
    engine, _ = live_db
    verify_and_migrate_password = migration_flow
    plaintext_password = "already argon2id password 3!"
    argon2_hash = hash_password(plaintext_password)
    user = await _seed_user(engine, password_hash=argon2_hash, username="argon2-user")

    async with AsyncSession(engine) as session, session.begin():
        db_user = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()
        result = await verify_and_migrate_password(
            session, db_user, plaintext_password
        )

    assert result is True

    stored_hash = await _reload_hash(engine, user.id)
    assert stored_hash == argon2_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_argon2id_user_wrong_password_fails_and_leaves_hash_unchanged(
    live_db, migration_flow
) -> None:
    engine, _ = live_db
    verify_and_migrate_password = migration_flow
    plaintext_password = "already argon2id password 4!"
    argon2_hash = hash_password(plaintext_password)
    user = await _seed_user(engine, password_hash=argon2_hash, username="argon2-user-2")

    async with AsyncSession(engine) as session, session.begin():
        db_user = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()
        with pytest.raises(PasswordVerificationError):
            await verify_and_migrate_password(session, db_user, "wrong password")

    stored_hash = await _reload_hash(engine, user.id)
    assert stored_hash == argon2_hash


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_bcrypt_failed_login_does_not_rehash_with_garbage_or_clear_field(
    live_db, migration_flow
) -> None:
    """Pins the exact failure-path invariant: not just 'still bcrypt-shaped'
    but byte-for-byte identical to the original stored digest, catching a
    bug where a failed attempt accidentally rehashes with the wrong
    plaintext, appends/mutates the string, or clears the column.
    """
    engine, _ = live_db
    verify_and_migrate_password = migration_flow
    plaintext_password = "legacy bcrypt password 5!"
    bcrypt_hash = bcrypt.hashpw(plaintext_password.encode(), bcrypt.gensalt()).decode()
    user = await _seed_user(engine, password_hash=bcrypt_hash, username="legacy-user-5")

    for attempt_password in ("", "totally wrong", plaintext_password + " "):
        async with AsyncSession(engine) as session, session.begin():
            db_user = (
                await session.execute(select(User).where(User.id == user.id))
            ).scalar_one()
            with pytest.raises(PasswordVerificationError):
                await verify_and_migrate_password(session, db_user, attempt_password)

        stored_hash = await _reload_hash(engine, user.id)
        assert stored_hash == bcrypt_hash, (
            f"digest mutated after failed attempt with password={attempt_password!r}"
        )
