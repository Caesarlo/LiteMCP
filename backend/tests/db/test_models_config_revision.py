"""DB-layer contract suite for the ``service_config_revision`` table (M1-MODEL-003).

Service config is versioned as immutable revisions that hold an encrypted
config reference (``secret_blob_id``) and a content digest (``config_digest``).
This suite pins the DB/model contract declared by docs/architecture/01-data-model.md
§5.3 (and §2/§3.2/§3.3 conventions):

* the table is registered on ``Base.metadata`` with the documented columns and
  portable types (ID / UTC_TS / JSON_DOC, plus plain String / Integer /
  BigInteger);
* generic create fields ``created_at`` / ``created_by`` are present and
  ``updated_at`` / ``updated_by`` are absent (immutable revisions use create
  fields only);
* ``(service_id, generation)`` is UNIQUE (each config change appends a new
  revision) and ``(id, service_id)`` is UNIQUE (ownership target backing the
  ``mcp_service.active_config_revision_id`` pointer);
* ``service_id`` is a RESTRICT foreign key to ``mcp_service.id``;
  ``secret_blob_id`` is present, ID-typed and NULLABLE but carries no foreign
  key in this feature (its target table ``service_secret`` is a later feature);
* enum-like varchar columns are guarded by portable table-level CHECK
  constraints, and the content fields (``public_config``, ``config_digest``)
  are NOT NULL.

Live tests run against real PostgreSQL and MySQL: each one provisions a unique
named empty database, runs Alembic ``upgrade head`` on it, drives the table
through an async engine and drops the database on teardown so runs never
collide and never depend on pre-existing state. A connection failure is a hard
FAIL, never a skip.
"""

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from litemcp.db.models import Base
from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, UTC_TS

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

DEFAULT_POSTGRES_URL = "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp"
DEFAULT_MYSQL_URL = "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp"
DEFAULT_MYSQL_ROOT_URL = "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql"

BASE_URLS = {
    "postgres": os.environ.get("LITEMCP_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL),
    "mysql": os.environ.get("LITEMCP_TEST_MYSQL_URL", DEFAULT_MYSQL_URL),
}
MYSQL_ROOT_URL = os.environ.get("LITEMCP_TEST_MYSQL_ROOT_URL", DEFAULT_MYSQL_ROOT_URL)

TABLE_NAME = "service_config_revision"

# Enum-like varchar columns -> allowed values, guarded by CHECK constraints.
ENUM_CHECKS = {
    "config_kind": ("http_api", "stdio", "mcp_http"),
    "source_mode": ("fastmcp_introspection", "descriptor", "manual", "remote_sync"),
    "state": ("draft", "validating", "validated", "active", "rejected", "superseded"),
}
NULLABLE = {
    "secret_blob_id",
    "source_descriptor",
    "validation_report",
    "activated_at",
    "superseded_at",
}


# ---------------------------------------------------------------------------
# Offline structural contract (no database).
# ---------------------------------------------------------------------------


def _table() -> Table:
    try:
        return Base.metadata.tables[TABLE_NAME]
    except KeyError:
        raise AssertionError(f"{TABLE_NAME} is not registered on Base.metadata") from None


def _unique_column_sets(table: Table) -> set[frozenset[str]]:
    result: set[frozenset[str]] = set()
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            result.add(frozenset(col.name for col in constraint.columns))
    for index in table.indexes:
        if index.unique:
            result.add(frozenset(col.name for col in index.columns))
    return result


def _has_enum_check(table: Table, column: str, values: tuple[str, ...]) -> bool:
    texts = [str(c.sqltext).lower() for c in table.constraints if isinstance(c, CheckConstraint)]
    return any(column in t and all(f"'{v}'" in t for v in values) for t in texts)


def test_table_is_registered() -> None:
    assert TABLE_NAME in Base.metadata.tables


def test_column_set_is_complete_and_immutable() -> None:
    columns = _table().columns
    assert set(columns.keys()) == {
        "id",
        "service_id",
        "generation",
        "schema_version",
        "config_kind",
        "public_config",
        "secret_blob_id",
        "source_descriptor",
        "source_mode",
        "config_digest",
        "state",
        "validation_report",
        "activated_at",
        "superseded_at",
        "created_at",
        "created_by",
    }


def test_no_updated_audit_columns() -> None:
    # Immutable revisions use generic create fields only (01-data-model.md §5.3).
    columns = set(_table().columns.keys())
    assert not {"updated_at", "updated_by"} & columns


def test_column_types_and_nullability() -> None:
    columns = _table().columns
    for name, column in columns.items():
        assert column.nullable == (name in NULLABLE), f"nullability mismatch on {name}"

    for name in ("id", "service_id", "secret_blob_id"):
        assert isinstance(columns[name].type, ID), name
    # §3.2 audit identity is the uniform String(128) actor id convention shared
    # by user/team/team_membership/mcp_service (not an entity FK / ID).
    created_by = columns["created_by"].type
    assert isinstance(created_by, String)
    assert created_by.length == 128
    for name in ("created_at", "activated_at", "superseded_at"):
        assert isinstance(columns[name].type, UTC_TS), name
    for name in ("public_config", "source_descriptor", "validation_report"):
        assert isinstance(columns[name].type, JSON_DOC), name
    # Enum-like columns are ENUM_CODE-typed; exact varchar width follows the
    # ENUM_CODE max+16 convention (M1-MODEL-001/002 do not pin enum widths).
    for name in ENUM_CHECKS:
        assert isinstance(columns[name].type, (ENUM_CODE, String)), name

    digest = columns["config_digest"].type
    assert isinstance(digest, String)
    assert digest.length == 64
    assert isinstance(columns["generation"].type, BigInteger)
    assert isinstance(columns["schema_version"].type, Integer)


def test_primary_key_is_id() -> None:
    assert [c.name for c in _table().primary_key.columns] == ["id"]


def test_generation_is_unique_per_service() -> None:
    unique_sets = _unique_column_sets(_table())
    assert frozenset({"service_id", "generation"}) in unique_sets


def test_ownership_unique_for_active_pointer() -> None:
    # UNIQUE (id, service_id) backs the mcp_service.active_config_revision_id
    # cross-table ownership constraint (01-data-model.md §5.3).
    unique_sets = _unique_column_sets(_table())
    assert frozenset({"id", "service_id"}) in unique_sets


def test_service_foreign_key_restrict() -> None:
    foreign_keys = _table().c.service_id.foreign_keys
    assert len(foreign_keys) == 1
    fk = next(iter(foreign_keys))
    assert fk.column.table.name == "mcp_service"
    assert fk.column.name == "id"
    assert fk.ondelete in (None, "RESTRICT", "NO ACTION")


def test_secret_blob_id_has_no_foreign_key() -> None:
    # service_secret is a later feature's table; the pointer column exists but
    # carries no FK here (01-data-model.md §5.3 / M1-MODEL-003 boundary).
    assert not _table().c.secret_blob_id.foreign_keys


def test_enum_columns_have_check_constraints() -> None:
    table = _table()
    for column, values in ENUM_CHECKS.items():
        assert _has_enum_check(table, column, values), f"missing CHECK guarding {column}"


# ---------------------------------------------------------------------------
# Live contract (real PostgreSQL and MySQL, one unique database per test).
# ---------------------------------------------------------------------------


def _render_database_url(base_url: str, dbname: str) -> str:
    return make_url(base_url).set(database=dbname).render_as_string(hide_password=False)


def _upgrade_head(url: str) -> None:
    """Run ``alembic upgrade head`` in a worker thread.

    The async migrations/env.py drives its engine with ``asyncio.run()``, which
    cannot run on the pytest-asyncio event-loop thread, so it must run on a
    thread with no running loop.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url)
    errors: list[Exception] = []

    def _run() -> None:
        try:
            command.upgrade(config, "head")
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller below
            errors.append(exc)

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


async def _create_database(dialect: str, dbname: str) -> None:
    if dialect == "postgres":
        engine = create_async_engine(BASE_URLS["postgres"], isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
        finally:
            await engine.dispose()
        return

    engine = create_async_engine(MYSQL_ROOT_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    f"CREATE DATABASE `{dbname}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            await conn.execute(
                text("CREATE USER IF NOT EXISTS 'litemcp'@'%' IDENTIFIED BY 'litemcp'")
            )
            await conn.execute(
                text("CREATE USER IF NOT EXISTS 'litemcp'@'localhost' IDENTIFIED BY 'litemcp'")
            )
            await conn.execute(text(f"GRANT ALL PRIVILEGES ON `{dbname}`.* TO 'litemcp'@'%'"))
            await conn.execute(
                text(f"GRANT ALL PRIVILEGES ON `{dbname}`.* TO 'litemcp'@'localhost'")
            )
            await conn.execute(text("FLUSH PRIVILEGES"))
    finally:
        await engine.dispose()


async def _drop_database(dialect: str, dbname: str) -> None:
    if dialect == "postgres":
        engine = create_async_engine(BASE_URLS["postgres"], isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
        finally:
            await engine.dispose()
        return

    engine = create_async_engine(MYSQL_ROOT_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS `{dbname}`"))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def live_db(request: pytest.FixtureRequest) -> tuple[AsyncEngine, str]:
    dialect: str = request.param
    dbname = f"litemcp_cfgrev_{uuid.uuid4().hex[:12]}"
    await _create_database(dialect, dbname)
    url = _render_database_url(BASE_URLS[dialect], dbname)
    try:
        _upgrade_head(url)
        engine = create_async_engine(url)
    except Exception:
        await _drop_database(dialect, dbname)
        raise
    try:
        yield engine, dialect
    finally:
        await engine.dispose()
        await _drop_database(dialect, dbname)


async def _seed_parent_rows(
    conn: AsyncConnection, service_id: uuid.UUID, creator: uuid.UUID, now: datetime
) -> None:
    """Insert a team + an http_api service row (the revision FK parent)."""
    metadata = Base.metadata
    team_id = uuid.uuid4()
    key = f"t-{team_id.hex[:8]}"
    await conn.execute(
        metadata.tables["team"].insert().values(
            id=team_id,
            key=key,
            key_normalized=key,
            name="Default Team",
            status="active",
            created_at=now,
            created_by=creator,
            updated_at=now,
            updated_by=creator,
            row_version=1,
        )
    )
    await conn.execute(
        metadata.tables["mcp_service"].insert().values(
            id=service_id,
            namespace_key="default",
            team_id=team_id,
            type="http_api",
            name=f"api-{service_id.hex[:8]}",
            name_normalized=f"api-{service_id.hex[:8]}",
            uniqueness_scope="LIVE",
            tags=[],
            desired_status="enabled",
            generation=1,
            observed_generation=0,
            runtime_status="pending",
            agent_auth_mode="api_key",
            created_at=now,
            created_by=creator,
            updated_at=now,
            updated_by=creator,
            row_version=1,
        )
    )


def _revision_values(
    *,
    rev_id: uuid.UUID,
    service_id: uuid.UUID,
    generation: int,
    creator: uuid.UUID,
    now: datetime,
    full: bool = False,
) -> dict[str, object]:
    values: dict[str, object] = {
        "id": rev_id,
        "service_id": service_id,
        "generation": generation,
        "schema_version": 1,
        "config_kind": "http_api",
        "public_config": {"base_url": "https://example.com/api", "timeout_ms": 30000},
        "secret_blob_id": None,
        "source_descriptor": None,
        "source_mode": "manual",
        "config_digest": "a" * 64,
        "state": "draft",
        "validation_report": None,
        "activated_at": None,
        "superseded_at": None,
        "created_at": now,
        "created_by": creator,
    }
    if full:
        values.update(
            secret_blob_id=uuid.uuid4(),
            source_descriptor={"schemaVersion": 1, "entrypoint": "main.py"},
            validation_report={"errors": [], "warnings": ["review"]},
            activated_at=now + timedelta(seconds=30),
            superseded_at=now + timedelta(seconds=60),
        )
    return values


def _ts_close(a: datetime | None, b: datetime | None, tolerance: float = 2.0) -> bool:
    if a is None or b is None:
        return a is b
    a_ts = a.timestamp() if a.tzinfo else a.replace(tzinfo=UTC).timestamp()
    b_ts = b.timestamp() if b.tzinfo else b.replace(tzinfo=UTC).timestamp()
    return abs(a_ts - b_ts) <= tolerance


LIVE_DIALECTS = ["postgres", "mysql"]


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", LIVE_DIALECTS, indirect=True)
async def test_revision_round_trip(live_db: tuple[AsyncEngine, str]) -> None:
    engine, _ = live_db
    revision_table = Base.metadata.tables[TABLE_NAME]
    now = datetime.now(UTC)
    creator = f"actor-{uuid.uuid4().hex[:8]}"
    service_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    values = _revision_values(
        rev_id=rev_id, service_id=service_id, generation=1, creator=creator, now=now, full=True
    )
    secret_blob_id = values["secret_blob_id"]
    source_descriptor = values["source_descriptor"]
    validation_report = values["validation_report"]

    async with engine.begin() as conn:
        await _seed_parent_rows(conn, service_id, creator, now)
        await conn.execute(revision_table.insert().values(**values))

    async with engine.connect() as conn:
        result = await conn.execute(
            revision_table.select().where(revision_table.c.id == rev_id)
        )
        row = result.mappings().one()

    assert str(row["id"]) == str(rev_id)
    assert str(row["service_id"]) == str(service_id)
    assert row["generation"] == 1
    assert row["schema_version"] == 1
    assert row["config_kind"] == "http_api"
    assert row["public_config"] == {"base_url": "https://example.com/api", "timeout_ms": 30000}
    assert str(row["secret_blob_id"]) == str(secret_blob_id)
    assert row["source_descriptor"] == source_descriptor
    assert row["source_mode"] == "manual"
    assert row["config_digest"] == "a" * 64
    assert row["state"] == "draft"
    assert row["validation_report"] == validation_report
    assert row["created_by"] == creator
    assert _ts_close(row["created_at"], now)
    assert _ts_close(row["activated_at"], now + timedelta(seconds=30))
    assert _ts_close(row["superseded_at"], now + timedelta(seconds=60))


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", LIVE_DIALECTS, indirect=True)
async def test_duplicate_generation_per_service_rejected(live_db: tuple[AsyncEngine, str]) -> None:
    engine, _ = live_db
    revision_table = Base.metadata.tables[TABLE_NAME]
    now = datetime.now(UTC)
    creator = f"actor-{uuid.uuid4().hex[:8]}"
    service_id = uuid.uuid4()

    async with engine.begin() as conn:
        await _seed_parent_rows(conn, service_id, creator, now)
        await conn.execute(
            revision_table.insert().values(
                **_revision_values(
                    rev_id=uuid.uuid4(),
                    service_id=service_id,
                    generation=1,
                    creator=creator,
                    now=now,
                )
            )
        )

    # A second revision for the same service+generation must be rejected.
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                revision_table.insert().values(
                    **_revision_values(
                        rev_id=uuid.uuid4(),
                        service_id=service_id,
                        generation=1,
                        creator=creator,
                        now=now,
                    )
                )
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", LIVE_DIALECTS, indirect=True)
async def test_revision_requires_existing_service(live_db: tuple[AsyncEngine, str]) -> None:
    engine, _ = live_db
    revision_table = Base.metadata.tables[TABLE_NAME]
    now = datetime.now(UTC)
    values = _revision_values(
        rev_id=uuid.uuid4(),
        service_id=uuid.uuid4(),
        generation=1,
        creator=f"actor-{uuid.uuid4().hex[:8]}",
        now=now,
    )
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(revision_table.insert().values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", LIVE_DIALECTS, indirect=True)
async def test_content_fields_are_not_null(live_db: tuple[AsyncEngine, str]) -> None:
    engine, _ = live_db
    revision_table = Base.metadata.tables[TABLE_NAME]
    now = datetime.now(UTC)
    creator = f"actor-{uuid.uuid4().hex[:8]}"
    service_id = uuid.uuid4()

    async with engine.begin() as conn:
        await _seed_parent_rows(conn, service_id, creator, now)

    for field in ("public_config", "config_digest"):
        values = _revision_values(
            rev_id=uuid.uuid4(),
            service_id=service_id,
            generation=1,
            creator=creator,
            now=now,
        )
        values[field] = None
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(revision_table.insert().values(**values))


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", LIVE_DIALECTS, indirect=True)
async def test_referenced_service_delete_is_blocked(live_db: tuple[AsyncEngine, str]) -> None:
    engine, _ = live_db
    service_table = Base.metadata.tables["mcp_service"]
    revision_table = Base.metadata.tables[TABLE_NAME]
    now = datetime.now(UTC)
    creator = f"actor-{uuid.uuid4().hex[:8]}"
    service_id = uuid.uuid4()

    async with engine.begin() as conn:
        await _seed_parent_rows(conn, service_id, creator, now)
        await conn.execute(
            revision_table.insert().values(
                **_revision_values(
                    rev_id=uuid.uuid4(),
                    service_id=service_id,
                    generation=1,
                    creator=creator,
                    now=now,
                )
            )
        )

    # RESTRICT foreign key: deleting a referenced service is rejected.
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(service_table.delete().where(service_table.c.id == service_id))
