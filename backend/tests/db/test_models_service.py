"""Contract tests for the mcp_service model (M1-MODEL-002).

Pins the declared behavior of docs/architecture/01-data-model.md §5.2
(mcp_service), §3.2 (generic audit fields) and §3.3 (soft-delete uniqueness),
as required by docs/architecture/08-implementation-plan.md (L117-119) and
docs/architecture/09-verification.md §4.3:

* OFFLINE (no database): the ``mcp_service`` table is registered on
  ``litemcp.db.models.Base.metadata`` with the documented columns, portable
  types and nullability; the UNIQUE ``(namespace_key, name_normalized,
  uniqueness_scope)``; the portable CHECK constraints (the ``type`` /
  ``desired_status`` / ``runtime_status`` / ``agent_auth_mode`` enumerations,
  ``observed_generation <= generation``, and stdio-only columns NULL for
  non-stdio types); the three documented indexes; the ``team_id`` FK to
  ``team.id`` (RESTRICT); the §3.2 audit fields and §3.3 soft-delete fields;
  and declared defaults for ``generation`` / ``observed_generation`` /
  ``uniqueness_scope`` / ``tags``.

* LIVE (PostgreSQL AND MySQL, each on a fresh dedicated database migrated to
  ``upgrade head``): inserting a service row referencing a real team succeeds
  with the documented defaults (generation=1, observed_generation=0,
  uniqueness_scope='LIVE', tags=[]) and populated audit fields; a duplicate
  ``(namespace_key, name_normalized, uniqueness_scope)`` is rejected; each
  enum CHECK rejects an invalid code; ``observed_generation > generation`` is
  rejected; a non-stdio type with a stdio-only column set is rejected; and the
  team FK (RESTRICT) rejects referencing a missing team.

DEFERRED CONTRACT (documented, intentionally NOT pinned here): the active
version pointers ``active_config_revision_id`` and ``active_toolset_id`` are
only required to be present, ID-typed and nullable at this stage. Their FK
enforcement against ``service_config_revision`` / ``toolset`` (composite FKs
``(active_*_id, id)``, 01-data-model.md §5.2 L165) is a LATER feature's
contract because those tables are not part of M1-MODEL-002.

This suite requires live PostgreSQL and MySQL; a connection failure is a hard
FAIL, never a skip. Every live test provisions a UNIQUE dedicated database,
migrates it with Alembic, and drops it in teardown, so no run collides and no
pre-existing state is depended on.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
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
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from litemcp.db.models import Base
from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, LONG_TEXT, UTC_TS

# ---------------------------------------------------------------------------
# Dialect engines (both are live requirements of this contract).
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
# The litemcp user only owns the litemcp.* database on MySQL, so a privileged
# account is required to create/drop the dedicated empty test database.
MYSQL_ROOT_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_ROOT_URL",
    "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql",
)


# ---------------------------------------------------------------------------
# Table accessors (resolved lazily so RED fails with a clear reason).
# ---------------------------------------------------------------------------


def _service_table() -> Table:
    if "mcp_service" not in Base.metadata.tables:
        raise AssertionError(
            "mcp_service table is not registered on "
            "litemcp.db.models.Base.metadata: M1-MODEL-002 must declare the "
            "Service model so its table is registered on the shared Base"
        )
    return Base.metadata.tables["mcp_service"]


def _team_table() -> Table:
    return Base.metadata.tables["team"]


# ---------------------------------------------------------------------------
# Alembic / database provisioning helpers (mirrors tests/db/test_migrations.py).
# ---------------------------------------------------------------------------


def _make_config() -> Config:
    assert ALEMBIC_INI.is_file(), f"missing Alembic config: {ALEMBIC_INI}"
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def _run_upgrade_in_thread(cfg: Config, url: str) -> None:
    """Run ``upgrade head`` against ``url`` in a worker thread.

    The async Alembic env.py drives its engine with ``asyncio.run()``, which
    cannot be called from the pytest-asyncio event loop already running in this
    thread. A dedicated thread has no running loop, so env.py runs unchanged.
    """
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


def _parse_url(base_url: str) -> dict:
    url = make_url(base_url)
    return {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }


async def _pg_create_database(params: dict, name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _pg_drop_database(params: dict, name: str) -> None:
    conn = await asyncpg.connect(**params)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


def _mysql_kwargs(url: str, database: str | None = None) -> dict:
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
            await cur.execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO '{safe_user}'@'%'")
    finally:
        conn.close()


async def _mysql_drop_database(admin_url: str, name: str) -> None:
    conn = await aiomysql.connect(**_mysql_kwargs(admin_url))
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Live fixture: one fresh, dedicated, migrated database per dialect per test.
# ---------------------------------------------------------------------------

_DIALECTS = [
    ("postgres", POSTGRES_URL),
    ("mysql", MYSQL_URL),
]


@pytest_asyncio.fixture(params=_DIALECTS, ids=["postgres", "mysql"])
async def live_db(request):
    """Yields an async engine on a dedicated database migrated to head.

    The database name is unique per run and the database is dropped in
    teardown, so tests never collide and never depend on pre-existing state.
    """
    dialect, _base_url = request.param
    database = _unique_database_name("litemcp_model_service")
    created = False
    try:
        if dialect == "postgres":
            params = _parse_url(POSTGRES_URL)
            await _pg_create_database(params, database)
            created = True
            url = _url_with_database(POSTGRES_URL, database)
        else:
            app_user = make_url(MYSQL_URL).username
            await _mysql_create_database(MYSQL_ROOT_URL, database, app_user)
            created = True
            url = _url_with_database(MYSQL_URL, database)

        _run_upgrade_in_thread(_make_config(), url)
        engine = create_async_engine(url)
        try:
            yield engine
        finally:
            await engine.dispose()
    finally:
        if created:
            if dialect == "postgres":
                await _pg_drop_database(_parse_url(POSTGRES_URL), database)
            else:
                await _mysql_drop_database(MYSQL_ROOT_URL, database)


# ---------------------------------------------------------------------------
# Offline structural contract (no database required).
# ---------------------------------------------------------------------------

# (column, kind, nullable, is_primary_key)
# kind is one of: "id" | "str" | "enum" | "json" | "longtext" | "ts" | "int"
#                 | "bigint" | "numeric"
_EXPECTED_COLUMNS = [
    ("id", "id", False, True),
    ("namespace_key", "str", False, False),
    ("team_id", "id", False, False),
    ("type", "enum", False, False),
    ("name", "str", False, False),
    ("name_normalized", "str", False, False),
    ("uniqueness_scope", "str", False, False),
    ("tags", "json", False, False),
    ("description", "longtext", True, False),
    ("icon_object_key", "str", True, False),
    ("desired_status", "enum", False, False),
    ("generation", "bigint", False, False),
    ("observed_generation", "bigint", False, False),
    ("runtime_status", "enum", False, False),
    # DEFERRED: FK enforcement of these pointers is a later feature; only
    # presence + nullable + ID typing is pinned here (see module docstring).
    ("active_config_revision_id", "id", True, False),
    ("active_toolset_id", "id", True, False),
    ("agent_auth_mode", "enum", False, False),
    ("rate_limit_qps", "numeric", True, False),
    ("rate_limit_burst", "int", True, False),
    ("queue_max_depth", "int", True, False),
    ("queue_timeout_ms", "int", True, False),
    ("stdio_instance_max", "int", True, False),
    ("stdio_concurrency_per_instance", "int", True, False),
    # §3.2 generic audit fields.
    ("created_at", "ts", False, False),
    ("created_by", "str", False, False),
    ("updated_at", "ts", False, False),
    ("updated_by", "str", False, False),
    ("row_version", "int", False, False),
    # §3.3 soft-delete fields.
    ("deleted_at", "ts", True, False),
    ("deleted_by", "str", True, False),
]


def _column_matches(col: Column, kind: str) -> bool:
    t = col.type
    if kind == "id":
        return isinstance(t, ID)
    if kind == "str":
        return isinstance(t, (String, ENUM_CODE))
    if kind == "enum":
        return isinstance(t, (ENUM_CODE, String))
    if kind == "json":
        return isinstance(t, (JSON_DOC, JSON))
    if kind == "longtext":
        return isinstance(t, (LONG_TEXT, Text))
    if kind == "ts":
        return isinstance(t, (UTC_TS, DateTime))
    if kind == "int":
        return isinstance(t, Integer)  # Integer or BigInteger, both compatible
    if kind == "bigint":
        return isinstance(t, BigInteger)  # docs say bigint explicitly
    if kind == "numeric":
        return isinstance(t, Numeric)
    return False


def test_mcp_service_table_registered_on_base() -> None:
    _service_table()  # raises a clear AssertionError if not registered


def test_mcp_service_columns_types_and_nullability() -> None:
    table = _service_table()
    actual = {c.name: c for c in table.columns}
    for name, kind, nullable, is_pk in _EXPECTED_COLUMNS:
        assert name in actual, f"mcp_service is missing column {name!r}"
        col = actual[name]
        assert col.nullable is nullable, (
            f"mcp_service.{name} nullability: expected {nullable}, "
            f"got {col.nullable}"
        )
        assert col.primary_key is is_pk, (
            f"mcp_service.{name} primary_key: expected {is_pk}, "
            f"got {col.primary_key}"
        )
        assert _column_matches(col, kind), (
            f"mcp_service.{name} has unexpected type {col.type!r} (expected {kind})"
        )


def test_mcp_service_documented_defaults_declared() -> None:
    """generation / observed_generation / uniqueness_scope / tags carry a default."""
    table = _service_table()
    for name in ("generation", "observed_generation", "uniqueness_scope", "tags"):
        col = table.c[name]
        assert not col.nullable, f"mcp_service.{name} must be NOT NULL"
        assert col.default is not None or col.server_default is not None, (
            f"mcp_service.{name} must declare a default "
            f"(docs/architecture/01-data-model.md §5.2)"
        )


def test_mcp_service_unique_name_normalized() -> None:
    """UNIQUE(namespace_key, name_normalized, uniqueness_scope) exists."""
    table = _service_table()
    sets = set()
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            sets.add(tuple(sorted(col.name for col in constraint.columns)))
    for index in table.indexes:
        if index.unique:
            sets.add(tuple(sorted(col.name for col in index.columns)))
    assert ("name_normalized", "namespace_key", "uniqueness_scope") in sets, (
        "mcp_service must declare UNIQUE(namespace_key, name_normalized, "
        "uniqueness_scope) (§5.2 L158)"
    )


def test_mcp_service_indexes() -> None:
    """The three documented market/ownership indexes exist."""
    table = _service_table()
    index_sets = {tuple(sorted(c.name for c in idx.columns)) for idx in table.indexes}
    assert ("desired_status", "namespace_key", "type") in index_sets, (
        "missing INDEX (namespace_key, desired_status, type)"
    )
    assert ("desired_status", "team_id") in index_sets, (
        "missing INDEX (team_id, desired_status)"
    )
    assert ("created_by", "deleted_at") in index_sets, (
        "missing INDEX (created_by, deleted_at)"
    )


def test_mcp_service_check_constraints() -> None:
    """The portable CHECK constraints from §5.2 L162-163 exist."""
    table = _service_table()
    texts = [
        str(cc.sqltext).lower()
        for cc in table.constraints
        if isinstance(cc, CheckConstraint)
    ]
    assert texts, "mcp_service declares no CHECK constraints"

    def any_text(*substrings: str) -> bool:
        return any(all(s in text for s in substrings) for text in texts)

    assert any_text("http_api", "stdio", "mcp_http"), (
        "missing CHECK on type IN ('http_api','stdio','mcp_http')"
    )
    assert any_text("enabled", "disabled"), (
        "missing CHECK on desired_status IN ('enabled','disabled')"
    )
    assert any_text("pending", "ready", "degraded", "unhealthy", "failed"), (
        "missing CHECK on runtime_status IN "
        "('pending','ready','degraded','unhealthy','failed')"
    )
    assert any_text("api_key", "none", "oauth2"), (
        "missing CHECK on agent_auth_mode IN ('api_key','none','oauth2')"
    )
    assert any_text("observed_generation", "generation", "<="), (
        "missing CHECK observed_generation <= generation"
    )
    assert any_text(
        "stdio",
        "queue_max_depth",
        "queue_timeout_ms",
        "stdio_instance_max",
        "stdio_concurrency_per_instance",
    ), (
        "missing CHECK that stdio-only columns are NULL for non-stdio types"
    )


def test_mcp_service_team_fk() -> None:
    """team_id is FK -> team.id with RESTRICT delete semantics."""
    table = _service_table()
    col = table.c["team_id"]
    assert col.foreign_keys, "mcp_service.team_id must declare a foreign key"
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "team", (
        f"team_id FK must reference team, got {fk.column.table.name}"
    )
    assert fk.column.name == "id", (
        f"team_id FK must reference team.id, got team.{fk.column.name}"
    )
    assert fk.ondelete == "RESTRICT", (
        f"team_id FK ondelete must be RESTRICT, got {fk.ondelete!r}"
    )


def test_mcp_service_audit_and_soft_delete_fields() -> None:
    """§3.2 audit fields NOT NULL; §3.3 soft-delete fields nullable."""
    table = _service_table()
    for name in ("created_at", "created_by", "updated_at", "updated_by", "row_version"):
        assert not table.c[name].nullable, f"mcp_service.{name} must be NOT NULL"
    for name in ("deleted_at", "deleted_by"):
        assert table.c[name].nullable, f"mcp_service.{name} must be nullable"


# ---------------------------------------------------------------------------
# Live contract (both dialects, fresh dedicated database each).
# ---------------------------------------------------------------------------


def _required_value_for(col: Column) -> object:
    """A type-appropriate value for a NOT NULL column that has no default."""
    t = col.type
    if isinstance(t, ID):
        return uuid.uuid4()
    if isinstance(t, (UTC_TS, DateTime)):
        return datetime.now(UTC)
    if isinstance(t, (JSON_DOC, JSON)):
        return {}
    if isinstance(t, (LONG_TEXT, Text)):
        return "x"
    if isinstance(t, (Integer, BigInteger)):
        return 1
    if isinstance(t, Numeric):
        return Decimal(1)
    if isinstance(t, Boolean):
        return True
    return "x"


def _row_for(table: Table, overrides: dict) -> dict:
    """Fill every NOT NULL column without a default, then apply overrides.

    This makes inserts resilient to whatever defaults/audit conventions the
    underlying model declares, while the business fields come from overrides.
    """
    values: dict = {}
    for col in table.columns:
        if col.name in overrides:
            continue
        if col.nullable:
            continue
        if col.default is not None or col.server_default is not None:
            continue
        values[col.name] = _required_value_for(col)
    values.update(overrides)
    return values


def _team_row(team_id: uuid.UUID, *, key: str = "demo-team") -> dict:
    return _row_for(
        _team_table(),
        {
            "id": team_id,
            "key": key,
            "key_normalized": key.lower(),
            "name": "Demo Team",
            "status": "active",
        },
    )


def _service_row(team_id: uuid.UUID, **overrides: object) -> dict:
    row: dict = {
        "id": uuid.uuid4(),
        "namespace_key": "default",
        "team_id": team_id,
        "type": "http_api",
        "name": "Demo Service",
        "name_normalized": "demo service",
        "desired_status": "enabled",
        "runtime_status": "pending",
        "agent_auth_mode": "api_key",
    }
    row.update(overrides)
    return _row_for(_service_table(), row)


async def _insert_team(engine, *, key: str = "demo-team") -> uuid.UUID:
    team_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(_team_table().insert().values(_team_row(team_id, key=key)))
    return team_id


async def _insert_service(engine, team_id: uuid.UUID, **overrides: object) -> uuid.UUID:
    service_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            _service_table().insert().values(_service_row(team_id, id=service_id, **overrides))
        )
    return service_id


@pytest.mark.asyncio
async def test_insert_service_with_team_and_defaults(live_db) -> None:
    """A service row referencing a real team inserts and defaults behave."""
    engine = live_db
    service = _service_table()
    team_id = await _insert_team(engine)
    service_id = await _insert_service(engine, team_id)

    async with engine.begin() as conn:
        row = (await conn.execute(select(service).where(service.c.id == service_id))).one()

    assert row.team_id == team_id
    assert row.namespace_key == "default"
    assert row.type == "http_api"
    assert row.name == "Demo Service"
    assert row.name_normalized == "demo service"
    assert row.desired_status == "enabled"
    assert row.runtime_status == "pending"
    assert row.agent_auth_mode == "api_key"
    assert row.generation == 1, "generation must default to 1"
    assert row.observed_generation == 0, "observed_generation must default to 0"
    assert row.uniqueness_scope == "LIVE", "uniqueness_scope must default to LIVE"
    assert row.tags == [], "tags must default to []"
    assert row.deleted_at is None
    assert row.deleted_by is None

    # §3.2 audit fields are populated.
    assert row.created_at is not None
    assert row.created_by is not None
    assert row.updated_at is not None
    assert row.updated_by is not None
    assert row.row_version is not None


@pytest.mark.asyncio
async def test_duplicate_name_normalized_rejected(live_db) -> None:
    """UNIQUE(namespace_key, name_normalized, uniqueness_scope) rejects dupes."""
    engine = live_db
    service = _service_table()
    team_id = await _insert_team(engine)
    await _insert_service(engine, team_id, name="Alpha", name_normalized="alpha")

    with pytest.raises((IntegrityError, OperationalError)):
        async with engine.begin() as conn:
            await conn.execute(
                service.insert().values(
                    _service_row(team_id, name="Alpha", name_normalized="alpha")
                )
            )


@pytest.mark.asyncio
async def test_enum_checks_reject_invalid_values(live_db) -> None:
    """Each enum CHECK rejects a disallowed value at the database level."""
    engine = live_db
    service = _service_table()
    team_id = await _insert_team(engine)

    invalid_cases = [
        {"type": "ftp"},
        {"desired_status": "paused"},
        {"runtime_status": "starting"},
        {"agent_auth_mode": "saml"},
    ]
    for overrides in invalid_cases:
        with pytest.raises((IntegrityError, OperationalError)):
            async with engine.begin() as conn:
                await conn.execute(
                    service.insert().values(_service_row(team_id, **overrides))
                )


@pytest.mark.asyncio
async def test_observed_generation_greater_than_generation_rejected(live_db) -> None:
    engine = live_db
    service = _service_table()
    team_id = await _insert_team(engine)

    with pytest.raises((IntegrityError, OperationalError)):
        async with engine.begin() as conn:
            await conn.execute(
                service.insert().values(
                    _service_row(team_id, generation=1, observed_generation=5)
                )
            )


@pytest.mark.asyncio
async def test_stdio_columns_rejected_for_non_stdio(live_db) -> None:
    """stdio-only columns must be NULL for a non-stdio service type."""
    engine = live_db
    service = _service_table()
    team_id = await _insert_team(engine)

    with pytest.raises((IntegrityError, OperationalError)):
        async with engine.begin() as conn:
            await conn.execute(
                service.insert().values(
                    _service_row(team_id, type="http_api", queue_max_depth=10)
                )
            )


@pytest.mark.asyncio
async def test_team_fk_rejects_missing_team(live_db) -> None:
    """team_id FK (RESTRICT) rejects a reference to a missing team."""
    engine = live_db
    service = _service_table()

    with pytest.raises((IntegrityError, OperationalError)):
        async with engine.begin() as conn:
            await conn.execute(service.insert().values(_service_row(uuid.uuid4())))
