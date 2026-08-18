"""First-admin bootstrap contract (M2-AUTH-001).

An empty database can only get its first global admin user through a
controlled bootstrap flow (docs/architecture/02-admin-auth.md §4.1):

- Empty database: exactly one call succeeds and creates the first user with
  ``role=admin`` / ``status=active`` and a correctly Argon2-hashed password.
- Non-empty database: the flow is rejected and no second user is created.
- Two concurrent initializations against the same empty database: exactly
  one succeeds, because the flow locks the initialization state within a
  database transaction.

These tests run against real PostgreSQL and MySQL databases, provisioned
fresh per test and migrated to head, following the same live-DB fixture
pattern used by ``tests/db/test_optimistic_lock.py`` and
``tests/db/test_soft_delete.py``. A missing ``litemcp.auth.bootstrap`` module
is the expected RED failure -- the bootstrap flow does not exist yet.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import argon2
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

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
    name = _db_name("litemcp_bootstrap")
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
def bootstrap_flow():
    """Load the bootstrap contract (RED until M2-AUTH-001 exists)."""
    try:
        from litemcp.auth.bootstrap import (
            AlreadyInitializedError,
            bootstrap_first_admin,
        )
    except ImportError as exc:  # pragma: no cover - expected RED result
        raise AssertionError(
            "M2-AUTH-001 must expose litemcp.auth.bootstrap.bootstrap_first_admin "
            "and litemcp.auth.bootstrap.AlreadyInitializedError"
        ) from exc
    return bootstrap_first_admin, AlreadyInitializedError


async def _all_users(engine: AsyncEngine) -> list[User]:
    async with AsyncSession(engine) as session:
        result = await session.execute(select(User))
        return list(result.scalars().all())


async def _seed_existing_user(engine: AsyncEngine) -> User:
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        username="preexisting",
        username_normalized="preexisting",
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$" + "a" * 22 + "$" + "b" * 43,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_empty_database_bootstrap_creates_first_active_admin(
    live_db, bootstrap_flow
) -> None:
    engine, _ = live_db
    bootstrap_first_admin, _ = bootstrap_flow
    plaintext_password = "correct horse battery staple 1!"

    async with AsyncSession(engine) as session, session.begin():
        created = await bootstrap_first_admin(
            session, username="admin", password=plaintext_password
        )

    assert created.role == "admin"
    assert created.status == "active"
    assert created.password_hash != plaintext_password
    assert created.password_hash.startswith("$argon2")

    hasher = argon2.PasswordHasher()
    hasher.verify(created.password_hash, plaintext_password)

    users = await _all_users(engine)
    assert len(users) == 1
    assert users[0].id == created.id
    assert users[0].role == "admin"
    assert users[0].status == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_bootstrap_rejected_when_a_user_already_exists(
    live_db, bootstrap_flow
) -> None:
    engine, _ = live_db
    bootstrap_first_admin, AlreadyInitializedError = bootstrap_flow
    existing = await _seed_existing_user(engine)

    async with AsyncSession(engine) as session:
        with pytest.raises(AlreadyInitializedError):
            async with session.begin():
                await bootstrap_first_admin(
                    session, username="second-admin", password="another password 2!"
                )

    users = await _all_users(engine)
    assert len(users) == 1
    assert users[0].id == existing.id
    assert users[0].username_normalized == "preexisting"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_concurrent_bootstrap_against_empty_database_has_exactly_one_winner(
    live_db, bootstrap_flow
) -> None:
    """The whole reason for locking within a transaction: two racing
    initializations against the same empty database must yield exactly one
    successful admin and exactly one user row, never zero and never two.
    """
    engine, _ = live_db
    bootstrap_first_admin, AlreadyInitializedError = bootstrap_flow

    async def attempt(username: str, password: str):
        async with AsyncSession(engine) as session, session.begin():
            return await bootstrap_first_admin(
                session, username=username, password=password
            )

    results = await asyncio.gather(
        attempt("racer-a", "racer password a 1!"),
        attempt("racer-b", "racer password b 2!"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    rejections = [r for r in results if isinstance(r, AlreadyInitializedError)]
    other_errors = [
        r for r in results if isinstance(r, BaseException) and not isinstance(r, AlreadyInitializedError)
    ]

    assert not other_errors, results
    assert len(successes) == 1, results
    assert len(rejections) == 1, results

    users = await _all_users(engine)
    assert len(users) == 1
    assert users[0].role == "admin"
    assert users[0].status == "active"
    assert users[0].id == successes[0].id


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_bootstrap_sets_password_changed_at_and_stores_no_plaintext(
    live_db, bootstrap_flow
) -> None:
    """A basic pin on 'controlled' bootstrap output: the created row carries a
    real ``password_changed_at`` timestamp and the plaintext never leaks into
    any stored column value.
    """
    engine, _ = live_db
    bootstrap_first_admin, _ = bootstrap_flow
    plaintext_password = "yet another bootstrap password 3!"

    async with AsyncSession(engine) as session, session.begin():
        created = await bootstrap_first_admin(
            session, username="admin2", password=plaintext_password
        )

    assert created.password_changed_at is not None

    async with AsyncSession(engine) as session:
        row = (
            await session.execute(select(User).where(User.id == created.id))
        ).scalar_one()
        stored_values = [
            getattr(row, column.name)
            for column in User.__table__.columns
            if isinstance(getattr(row, column.name), str)
        ]
    assert plaintext_password not in stored_values
