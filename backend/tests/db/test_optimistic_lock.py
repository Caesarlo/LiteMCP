"""Cross-dialect contract for service ``row_version`` compare-and-swap."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiomysql
import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    select,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from litemcp.db.models import Base
from litemcp.db.types import ID, JSON_DOC, LONG_TEXT, UTC_TS

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


def _table():
    assert "mcp_service" in Base.metadata.tables, "mcp_service model is required"
    return Base.metadata.tables["mcp_service"]


def _row_for(overrides: dict[str, object], table=None) -> dict[str, object]:
    """Supply portable values for required columns without defaults."""
    values: dict[str, object] = {}
    table = _table() if table is None else table
    for column in table.columns:
        if column.name in overrides or column.nullable:
            continue
        if column.default is not None or column.server_default is not None:
            continue
        typ = column.type
        if isinstance(typ, ID):
            values[column.name] = uuid.uuid4()
        elif isinstance(typ, (UTC_TS, DateTime)):
            values[column.name] = datetime.now(UTC)
        elif isinstance(typ, (JSON_DOC, JSON)):
            values[column.name] = {}
        elif isinstance(typ, (LONG_TEXT, Text, String)):
            values[column.name] = "x"
        elif isinstance(typ, (Integer, BigInteger, Numeric, Boolean)):
            values[column.name] = 1
        else:
            values[column.name] = "x"
    values.update(overrides)
    return values


def _service_row(team_id: uuid.UUID, service_id: uuid.UUID) -> dict[str, object]:
    return _row_for(
        {
            "id": service_id,
            "namespace_key": "default",
            "team_id": team_id,
            "type": "http_api",
            "name": "Concurrent Service",
            "name_normalized": f"concurrent-{service_id}",
            "desired_status": "enabled",
            "runtime_status": "pending",
            "agent_auth_mode": "api_key",
            "description": "initial",
        }
    )


def _make_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def _run_upgrade(url: str) -> None:
    config = _make_config()
    config.set_main_option("sqlalchemy.url", url)
    error: BaseException | None = None

    def worker() -> None:
        nonlocal error
        try:
            command.upgrade(config, "head")
        except Exception as exc:  # noqa: BLE001 - surfaced in caller thread
            error = exc

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if error is not None:
        raise error


def _parse_url(url: str) -> dict[str, object]:
    parsed = make_url(url)
    return {
        "host": parsed.host,
        "port": parsed.port,
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.database,
    }


def _url_with_database(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


async def _pg_create(params: dict[str, object], name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _pg_drop(params: dict[str, object], name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def _mysql_kwargs(url: str, database: str | None = None) -> dict[str, object]:
    parsed = make_url(url)
    return {
        "host": parsed.host,
        "port": parsed.port or 3306,
        "user": parsed.username,
        "password": parsed.password,
        "db": database if database is not None else parsed.database,
        "autocommit": True,
    }


async def _mysql_create(name: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(MYSQL_ROOT_URL))
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{name}`")
            user = make_url(MYSQL_URL).username or "litemcp"
            await cursor.execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{user}'@'%'")
    finally:
        conn.close()


async def _mysql_drop(name: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(MYSQL_ROOT_URL))
    try:
        async with conn.cursor() as cursor:
            await cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        conn.close()


@pytest_asyncio.fixture(
    params=[("postgres", POSTGRES_URL), ("mysql", MYSQL_URL)],
    ids=["postgres", "mysql"],
)
async def live_db(request):
    dialect, base_url = request.param
    name = f"litemcp_optimistic_{time.time_ns()}_{os.getpid()}"
    if dialect == "postgres":
        await _pg_create(_parse_url(POSTGRES_URL), name)
    else:
        await _mysql_create(name)
    url = _url_with_database(base_url, name)
    try:
        _run_upgrade(url)
        engine = create_async_engine(url, pool_size=4, max_overflow=0)
        try:
            yield engine
        finally:
            await engine.dispose()
    finally:
        if dialect == "postgres":
            await _pg_drop(_parse_url(POSTGRES_URL), name)
        else:
            await _mysql_drop(name)


@pytest.fixture
def repository():
    """Load the repository CAS contract (RED until M1-CONC-001 exists)."""
    try:
        from litemcp.db.repository import ConcurrentModificationError, ServiceRepository
    except ImportError as exc:  # pragma: no cover - expected RED result
        raise AssertionError(
            "M1-CONC-001 must expose litemcp.db.repository.ServiceRepository and "
            "ConcurrentModificationError"
        ) from exc
    return ServiceRepository, ConcurrentModificationError


async def _insert_service(engine, team_id: uuid.UUID, service_id: uuid.UUID) -> None:
    team = Base.metadata.tables["team"]
    team_values = _row_for(
        {
            "id": team_id,
            "key": f"team-{team_id}",
            "key_normalized": f"team-{team_id}",
            "name": "Concurrency Team",
            "status": "active",
        },
        table=team,
    )
    async with engine.begin() as conn:
        await conn.execute(team.insert().values(team_values))
        await conn.execute(_table().insert().values(_service_row(team_id, service_id)))


async def _read_service(engine, service_id: uuid.UUID) -> tuple[str, int]:
    async with AsyncSession(engine) as session:
        row = (await session.execute(select(_table()).where(_table().c.id == service_id))).one()
        return row.description, row.row_version


@pytest.mark.asyncio
async def test_same_row_version_has_one_winner_and_no_stale_overwrite(live_db, repository) -> None:
    """Two same-version writes yield one success and one stable conflict."""
    engine = live_db
    service_id = uuid.uuid4()
    await _insert_service(engine, uuid.uuid4(), service_id)
    ServiceRepository, ConcurrentModificationError = repository
    barrier = asyncio.Barrier(2)

    async def write(description: str):
        async with AsyncSession(engine) as session:
            repo = ServiceRepository(session)
            _, version = await _read_service(engine, service_id)
            await barrier.wait()
            async with session.begin():
                return await repo.update_with_row_version(
                    service_id=service_id,
                    expected=version,
                    public_fields={"description": description},
                )

    results = await asyncio.gather(
        write("winner-a"), write("winner-b"), return_exceptions=True
    )
    successes = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [result for result in results if isinstance(result, ConcurrentModificationError)]
    assert len(successes) == 1, results
    assert len(conflicts) == 1, results
    assert conflicts[0].code == "CONCURRENT_MODIFICATION"

    description, version = await _read_service(engine, service_id)
    assert description in {"winner-a", "winner-b"}
    assert version == 2

    async with AsyncSession(engine) as session:
        repo = ServiceRepository(session)
        with pytest.raises(ConcurrentModificationError) as caught:
            async with session.begin():
                await repo.update_with_row_version(
                    service_id=service_id,
                    expected=1,
                    public_fields={"description": "stale-overwrite"},
                )
    assert caught.value.code == "CONCURRENT_MODIFICATION"
    assert await _read_service(engine, service_id) == (description, 2)


@pytest.mark.asyncio
async def test_current_writer_increments_version_atomically(live_db, repository) -> None:
    """A current writer updates data and increments ``row_version`` once."""
    engine = live_db
    service_id = uuid.uuid4()
    await _insert_service(engine, uuid.uuid4(), service_id)
    ServiceRepository, _ = repository

    async with AsyncSession(engine) as session:
        repo = ServiceRepository(session)
        async with session.begin():
            changed = await repo.update_with_row_version(
                service_id=service_id,
                expected=1,
                public_fields={"description": "current-writer"},
            )
        assert changed is True

    assert await _read_service(engine, service_id) == ("current-writer", 2)
