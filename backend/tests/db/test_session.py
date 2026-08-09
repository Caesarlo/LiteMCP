"""Contract tests for the async database session factory (M1-DB-001).

Pins the public contract of ``litemcp.db.session`` as required by
docs/architecture/08-implementation-plan.md (L30, L117) and
docs/architecture/09-verification.md (L34):

- every request/job gets its own independent ``AsyncSession``;
- sessions are reliably released on normal exit AND on exception;
- the configured factory is wired from ``get_settings().database_url``;
- the factory can dispose its engine, releasing pooled resources;
- sessions never leak across concurrent tasks.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import Column, Integer, MetaData, Table, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from litemcp.core.config import get_settings
from litemcp.db.session import AsyncSessionFactory, get_session_factory

_metadata = MetaData()

items = Table(
    "items",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("note", Text, nullable=False),
)


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest_asyncio.fixture
async def factory(sqlite_url):
    f = AsyncSessionFactory(
        sqlite_url,
        engine_kwargs={"connect_args": {"timeout": 30}},
    )
    async with f.engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)
    try:
        yield f
    finally:
        await f.dispose()


@pytest.mark.asyncio
async def test_factory_creates_independent_sessions(factory) -> None:
    async with factory.session() as s1:
        async with factory.session() as s2:
            assert isinstance(s1, AsyncSession)
            assert isinstance(s2, AsyncSession)
            assert s1 is not s2

            # Commit in one session, roll back in the other.
            async with s1.begin():
                await s1.execute(items.insert().values(note="committed-one"))
            await s2.execute(items.insert().values(note="uncommitted-two"))

            # s1 must not observe s2's uncommitted write: the two sessions
            # are independent transactions against the same database.
            seen = set((await s1.execute(select(items.c.note))).scalars())
            assert seen == {"committed-one"}

            await s2.rollback()

    # Both context managers have released their sessions; only the
    # committed write is durable.
    async with factory.session() as s3:
        durable = set((await s3.execute(select(items.c.note))).scalars())
    assert durable == {"committed-one"}


@pytest.mark.asyncio
async def test_session_released_on_normal_exit(factory) -> None:
    assert factory.engine.pool.checkedout() == 0
    async with factory.session() as s:
        assert isinstance(s, AsyncSession)
        await s.execute(text("SELECT 1"))
        assert s.is_active is True
        # The session holds one pooled connection while in use.
        assert factory.engine.pool.checkedout() == 1

    # On release the connection is returned to the pool (no leak).
    assert factory.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_session_released_on_exception(factory) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with factory.session() as s:
            await s.execute(text("SELECT 1"))
            raise RuntimeError("boom")

    # The exception propagates AND the connection is still returned to the pool.
    assert factory.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_get_session_factory_binds_to_settings_database_url(
    tmp_path, monkeypatch
) -> None:
    sqlite_url = f"sqlite+aiosqlite:///{(tmp_path / 'configured.db').as_posix()}"

    monkeypatch.setenv("LITEMCP_ENVIRONMENT", "test")
    monkeypatch.setenv("LITEMCP_DATABASE_URL", sqlite_url)
    monkeypatch.setenv("LITEMCP_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("LITEMCP_ENCRYPTION_KEYS", "test-encryption-key")
    get_settings.cache_clear()

    factory = get_session_factory()
    try:
        async with factory.engine.begin() as conn:
            await conn.run_sync(_metadata.create_all)

        async with factory.session() as s:
            async with s.begin():
                await s.execute(items.insert().values(note="from-settings"))

        async with factory.session() as s:
            notes = list((await s.execute(select(items.c.note))).scalars())
        assert notes == ["from-settings"]
    finally:
        await factory.dispose()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_dispose_releases_pooled_resources(factory) -> None:
    async with factory.session() as s:
        await s.execute(text("SELECT 1"))
    assert factory.engine.pool.checkedout() == 0

    # Dispose releases the engine's pooled connections and must not raise.
    await factory.dispose()
    assert factory.engine.pool.checkedout() == 0


@pytest.mark.asyncio
async def test_concurrent_sessions_commit_independently(factory) -> None:
    async def write_row(note: str) -> None:
        async with factory.session() as s:
            async with s.begin():
                await s.execute(items.insert().values(note=note))

    await asyncio.gather(write_row("alpha"), write_row("beta"))

    async with factory.session() as s:
        notes = set((await s.execute(select(items.c.note))).scalars())
    assert notes == {"alpha", "beta"}
