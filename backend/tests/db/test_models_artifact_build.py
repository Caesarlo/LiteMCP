"""DB-layer contract tests for ``service_artifact`` and ``build_run`` (M1-MODEL-005).

Two parts:

* OFFLINE structural tests that introspect ``litemcp.db.models.Base.metadata`` and pin
  the documented columns, portable kinds, nullability, and the UNIQUE / CHECK / FK /
  INDEX constraints from docs/architecture/01-data-model.md §3.2, §5.5 and §5.6.
* LIVE tests parametrized over both PostgreSQL and MySQL. Each live test provisions a
  unique empty database, runs Alembic ``upgrade head`` on it (in a dedicated worker
  thread, because the async migration env drives its engine with ``asyncio.run()``),
  drives the tables through an async engine, and drops the database in teardown so
  runs never collide and never depend on pre-existing state.

A connection failure to either dialect is a hard failure, never a skip.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import BigInteger, Integer, String, Table, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import CompileError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.schema import CheckConstraint, UniqueConstraint

from litemcp.db.models import Base
from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, UTC_TS

DIALECTS = ["postgresql", "mysql"]

POSTGRES_URL = os.getenv(
    "LITEMCP_TEST_POSTGRES_URL",
    "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp",
)
MYSQL_URL = os.getenv(
    "LITEMCP_TEST_MYSQL_URL",
    "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp",
)
MYSQL_ROOT_URL = os.getenv(
    "LITEMCP_TEST_MYSQL_ROOT_URL",
    "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql",
)
BASE_URLS = {"postgresql": POSTGRES_URL, "mysql": MYSQL_URL}

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"
_MIGRATIONS_DIR = _BACKEND_DIR / "migrations"

SERVICE_ARTIFACT_KIND_VALUES = [
    "source_package",
    "descriptor",
    "build_bundle",
    "container_image",
    "build_log",
]
SERVICE_ARTIFACT_STORAGE_VALUES = ["filesystem", "s3", "minio", "registry"]
SERVICE_ARTIFACT_STATE_VALUES = [
    "staging",
    "available",
    "quarantined",
    "gc_pending",
    "deleted",
]
BUILD_RUN_STRATEGY_VALUES = ["fastmcp", "descriptor", "custom_adapter"]
BUILD_RUN_STATUS_VALUES = [
    "queued",
    "building",
    "validating",
    "succeeded",
    "failed",
    "cancelled",
    "superseded",
]

# Column contract: name -> (kind, nullable, length). nullable=None is not pinned.
_SERVICE_ARTIFACT_COLUMNS = {
    "id": ("id", False, None),
    "service_id": ("id", False, None),
    "config_revision_id": ("id", True, None),
    "kind": ("enum", False, None),
    "storage_backend": ("enum", False, None),
    "object_key": ("str", False, 1024),
    "sha256": ("str", False, 64),
    "size_bytes": ("bigint", False, None),
    "media_type": ("str", False, 128),
    "format": ("str", False, 32),
    "state": ("enum", False, None),
    "scan_report": ("json", True, None),
    "retain_until": ("ts", True, None),
    "created_at": ("ts", False, None),
    "created_by": ("str", None, 128),
    "updated_at": ("ts", False, None),
    "updated_by": ("str", None, 128),
    "row_version": ("int", False, None),
}

_BUILD_RUN_COLUMNS = {
    "id": ("id", False, None),
    "service_id": ("id", False, None),
    "config_revision_id": ("id", False, None),
    "source_artifact_id": ("id", False, None),
    "strategy": ("enum", False, None),
    "parser_version": ("str", False, 64),
    "base_image_digest": ("str", False, 255),
    "dependency_digest": ("str", True, 64),
    "status": ("enum", False, None),
    "output_artifact_id": ("id", True, None),
    "discovered_descriptor": ("json", True, None),
    "error_code": ("str", True, 64),
    "error_summary": ("str", True, 2048),
    "log_artifact_id": ("id", True, None),
    "started_at": ("ts", True, None),
    "finished_at": ("ts", True, None),
    "created_at": ("ts", False, None),
    "created_by": ("str", None, 128),
    "updated_at": ("ts", False, None),
    "updated_by": ("str", None, 128),
    "row_version": ("int", False, None),
}


# ---------------------------------------------------------------------------
# Offline structural section (no database).
# ---------------------------------------------------------------------------


def _assert_columns(
    table: Table,
    specs: dict[str, tuple[str, bool | None, int | None]],
) -> None:
    columns = {column.name: column for column in table.columns}
    for name, (kind, nullable, length) in specs.items():
        assert name in columns, f"{table.name} is missing column {name!r}"
        column = columns[name]
        if kind == "id":
            assert isinstance(column.type, ID), f"{table.name}.{name} must use ID"
        elif kind == "ts":
            assert isinstance(column.type, UTC_TS), f"{table.name}.{name} must use UTC_TS"
        elif kind == "json":
            assert isinstance(column.type, JSON_DOC), f"{table.name}.{name} must use JSON_DOC"
        elif kind == "bigint":
            assert isinstance(column.type, BigInteger), f"{table.name}.{name} must use BigInteger"
        elif kind == "int":
            assert isinstance(column.type, (Integer, BigInteger)), (
                f"{table.name}.{name} must be an integer type"
            )
        elif kind == "str":
            assert isinstance(column.type, (String, ENUM_CODE)), (
                f"{table.name}.{name} must be a String type"
            )
            assert getattr(column.type, "length", None) == length, (
                f"{table.name}.{name} must be String({length})"
            )
        elif kind == "enum":
            assert isinstance(column.type, (String, ENUM_CODE)), (
                f"{table.name}.{name} must be an enum-like String type"
            )
        if nullable is not None:
            assert column.nullable is nullable, (
                f"{table.name}.{name} nullable must be {nullable}"
            )


def _check_texts(table: Table) -> list[str]:
    """All rendered CHECK-constraint SQL texts, including literal-bind fallback."""
    texts: list[str] = []
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            sqltext = constraint.sqltext
            texts.append(str(sqltext))
            try:
                texts.append(
                    str(sqltext.compile(compile_kwargs={"literal_binds": True}))
                )
            except CompileError:
                continue
    return texts


def _assert_enum_check(table: Table, column: str, values: list[str]) -> None:
    matching = [chunk for chunk in _check_texts(table) if column in chunk]
    assert matching, f"{table.name}.{column} has no CHECK constraint"
    for value in values:
        assert any(f"'{value}'" in chunk for chunk in matching), (
            f"{table.name}.{column} CHECK does not allow {value!r}"
        )


def test_service_artifact_table_is_registered() -> None:
    assert "service_artifact" in Base.metadata.tables


def test_build_run_table_is_registered() -> None:
    assert "build_run" in Base.metadata.tables


def test_service_artifact_columns() -> None:
    _assert_columns(Base.metadata.tables["service_artifact"], _SERVICE_ARTIFACT_COLUMNS)


def test_build_run_columns() -> None:
    _assert_columns(Base.metadata.tables["build_run"], _BUILD_RUN_COLUMNS)


def test_service_artifact_primary_key() -> None:
    table = Base.metadata.tables["service_artifact"]
    assert [column.name for column in table.primary_key.columns] == ["id"]


def test_build_run_primary_key() -> None:
    table = Base.metadata.tables["build_run"]
    assert [column.name for column in table.primary_key.columns] == ["id"]


def test_service_artifact_foreign_keys() -> None:
    table = Base.metadata.tables["service_artifact"]
    fks = {fk.parent.name: fk.target_fullname for fk in table.foreign_keys}
    assert fks["service_id"] == "mcp_service.id"
    assert fks["config_revision_id"] == "service_config_revision.id"


def test_build_run_foreign_keys() -> None:
    table = Base.metadata.tables["build_run"]
    fks = {fk.parent.name: fk.target_fullname for fk in table.foreign_keys}
    assert fks["service_id"] == "mcp_service.id"
    assert fks["config_revision_id"] == "service_config_revision.id"
    assert fks["source_artifact_id"] == "service_artifact.id"
    assert fks["output_artifact_id"] == "service_artifact.id"
    assert fks["log_artifact_id"] == "service_artifact.id"


def test_service_artifact_unique_storage_backend_object_key() -> None:
    table = Base.metadata.tables["service_artifact"]
    unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("storage_backend", "object_key") in unique


def test_build_run_service_status_created_at_index() -> None:
    table = Base.metadata.tables["build_run"]
    index_cols = {
        tuple(column.name for column in index.columns): index.unique
        for index in table.indexes
    }
    assert ("service_id", "status", "created_at") in index_cols
    assert index_cols[("service_id", "status", "created_at")] is False


def test_service_artifact_kind_check_values() -> None:
    _assert_enum_check(
        Base.metadata.tables["service_artifact"], "kind", SERVICE_ARTIFACT_KIND_VALUES
    )


def test_service_artifact_storage_backend_check_values() -> None:
    _assert_enum_check(
        Base.metadata.tables["service_artifact"],
        "storage_backend",
        SERVICE_ARTIFACT_STORAGE_VALUES,
    )


def test_service_artifact_state_check_values() -> None:
    _assert_enum_check(
        Base.metadata.tables["service_artifact"], "state", SERVICE_ARTIFACT_STATE_VALUES
    )


def test_service_artifact_size_bytes_non_negative_check() -> None:
    texts = [
        chunk
        for chunk in _check_texts(Base.metadata.tables["service_artifact"])
        if "size_bytes" in chunk
    ]
    assert texts, "service_artifact.size_bytes has no CHECK constraint"
    assert any(">" in chunk or "<" in chunk for chunk in texts), (
        "service_artifact.size_bytes CHECK must bound size non-negative"
    )


def test_build_run_strategy_check_values() -> None:
    _assert_enum_check(
        Base.metadata.tables["build_run"], "strategy", BUILD_RUN_STRATEGY_VALUES
    )


def test_build_run_status_check_values() -> None:
    _assert_enum_check(
        Base.metadata.tables["build_run"], "status", BUILD_RUN_STATUS_VALUES
    )


# ---------------------------------------------------------------------------
# Live database section (PostgreSQL + MySQL).
# ---------------------------------------------------------------------------


def _admin_engine(dialect: str) -> AsyncEngine:
    if dialect == "postgresql":
        return create_async_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
    return create_async_engine(MYSQL_ROOT_URL, isolation_level="AUTOCOMMIT")


async def _provision(dialect: str, db_name: str) -> str:
    """Create an empty database and return its connection URL."""
    engine = _admin_engine(dialect)
    try:
        async with engine.connect() as conn:
            if dialect == "postgresql":
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            else:
                await conn.execute(
                    text(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4")
                )
                await conn.execute(
                    text(f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO 'litemcp'@'%'")
                )
    finally:
        await engine.dispose()
    return make_url(BASE_URLS[dialect]).set(database=db_name).render_as_string(
        hide_password=False
    )


async def _drop_database(dialect: str, db_name: str) -> None:
    engine = _admin_engine(dialect)
    try:
        async with engine.connect() as conn:
            if dialect == "postgresql":
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
            else:
                await conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
    finally:
        await engine.dispose()


def _upgrade_to_head(url: str) -> None:
    """Run ``alembic upgrade head`` in a dedicated worker thread.

    The async migration env drives its engine with ``asyncio.run()``, which cannot
    run on the pytest-asyncio event-loop thread.
    """

    failures: list[Exception] = []

    def run() -> None:
        try:
            cfg = AlembicConfig(str(_ALEMBIC_INI))
            cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
            cfg.set_main_option("sqlalchemy.url", url)
            command.upgrade(cfg, "head")
        # Any upgrade error must surface on the pytest thread as a hard failure;
        # a worker thread cannot propagate exceptions via join().
        except Exception as exc:  # noqa: BLE001 - re-raised by caller
            failures.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if failures:
        raise failures[0]


@dataclass
class ProvisionedDb:
    dialect: str
    engine: AsyncEngine
    db_name: str


@pytest_asyncio.fixture
async def provisioned_db(request: pytest.FixtureRequest):
    dialect: str = request.param
    db_name = f"litemcp_artifact_build_{uuid.uuid4().hex[:12]}"
    url = await _provision(dialect, db_name)
    try:
        _upgrade_to_head(url)
    except Exception:
        await _drop_database(dialect, db_name)
        raise
    engine = create_async_engine(url)
    try:
        yield ProvisionedDb(dialect=dialect, engine=engine, db_name=db_name)
    finally:
        await engine.dispose()
        await _drop_database(dialect, db_name)


async def _seed_service(conn):
    """Insert the FK parents needed by artifact/build rows: team + mcp_service +
    service_config_revision. Returns (service_id, revision_id).
    """
    tables = Base.metadata.tables
    now = datetime.now(UTC)
    team_id = uuid.uuid4()
    service_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    await conn.execute(
        tables["team"].insert().values(
            id=team_id,
            key="contract",
            key_normalized="contract",
            name="Contract Team",
            description=None,
            status="active",
            created_at=now,
            created_by="contract-test",
            updated_at=now,
            updated_by="contract-test",
            row_version=1,
        )
    )
    await conn.execute(
        tables["mcp_service"].insert().values(
            id=service_id,
            namespace_key="default",
            team_id=team_id,
            type="stdio",
            name="artifact-svc",
            name_normalized="artifact-svc",
            uniqueness_scope="LIVE",
            tags=[],
            description=None,
            icon_object_key=None,
            desired_status="enabled",
            generation=1,
            observed_generation=0,
            runtime_status="pending",
            active_config_revision_id=None,
            active_toolset_id=None,
            agent_auth_mode="none",
            rate_limit_qps=None,
            rate_limit_burst=None,
            queue_max_depth=None,
            queue_timeout_ms=None,
            stdio_instance_max=None,
            stdio_concurrency_per_instance=None,
            created_at=now,
            created_by="contract-test",
            updated_at=now,
            updated_by="contract-test",
            row_version=1,
        )
    )
    await conn.execute(
        tables["service_config_revision"].insert().values(
            id=revision_id,
            service_id=service_id,
            generation=1,
            schema_version=1,
            config_kind="stdio",
            public_config={},
            secret_blob_id=None,
            source_descriptor=None,
            source_mode="manual",
            config_digest="a" * 64,
            state="validated",
            validation_report=None,
            activated_at=None,
            superseded_at=None,
            created_at=now,
            created_by="contract-test",
        )
    )
    return service_id, revision_id


def _artifact_values(service_id, revision_id, *, artifact_id, **overrides):
    now = datetime.now(UTC)
    values = {
        "id": artifact_id,
        "service_id": service_id,
        "config_revision_id": revision_id,
        "kind": "source_package",
        "storage_backend": "filesystem",
        "object_key": f"pkg/{artifact_id.hex}.zip",
        "sha256": "a" * 64,
        "size_bytes": 2048,
        "media_type": "application/zip",
        "format": "zip",
        "state": "available",
        "scan_report": None,
        "retain_until": None,
        "created_at": now,
        "created_by": "contract-test",
        "updated_at": now,
        "updated_by": "contract-test",
        "row_version": 1,
    }
    values.update(overrides)
    return values


async def _insert_artifact(
    conn,
    service_id,
    revision_id,
    *,
    artifact_id,
    kind,
    object_key,
    **overrides,
):
    await conn.execute(
        Base.metadata.tables["service_artifact"]
        .insert()
        .values(
            **_artifact_values(
                service_id,
                revision_id,
                artifact_id=artifact_id,
                kind=kind,
                object_key=object_key,
                **overrides,
            )
        )
    )


def _build_run_values(
    service_id,
    revision_id,
    run_id,
    source_artifact_id,
    *,
    now=None,
    **overrides,
):
    now = now or datetime.now(UTC)
    values = {
        "id": run_id,
        "service_id": service_id,
        "config_revision_id": revision_id,
        "source_artifact_id": source_artifact_id,
        "strategy": "fastmcp",
        "parser_version": "0.2.0",
        "base_image_digest": "sha256:" + "b" * 64,
        "dependency_digest": "c" * 64,
        "status": "succeeded",
        "output_artifact_id": None,
        "discovered_descriptor": None,
        "error_code": None,
        "error_summary": None,
        "log_artifact_id": None,
        "started_at": None,
        "finished_at": None,
        "created_at": now,
        "created_by": "contract-test",
        "updated_at": now,
        "updated_by": "contract-test",
        "row_version": 1,
    }
    values.update(overrides)
    return values


def _naive_utc(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _assert_row_matches(row, expected: dict) -> None:
    actual = dict(row._mapping)
    for key, want in expected.items():
        got = actual[key]
        if isinstance(want, uuid.UUID):
            assert str(got) == str(want), key
        elif isinstance(want, datetime):
            assert _naive_utc(got) == _naive_utc(want), f"{key}: {got!r} != {want!r}"
        elif isinstance(want, dict):
            if isinstance(got, str):
                got = json.loads(got)
            assert got == want, f"{key}: {got!r} != {want!r}"
        else:
            assert got == want, f"{key}: {got!r} != {want!r}"


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_service_artifact_persists_and_round_trips(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["service_artifact"]
    now = datetime.now(UTC)
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
        artifact_id = uuid.uuid4()
        values = _artifact_values(
            service_id,
            revision_id,
            artifact_id=artifact_id,
            object_key="artifacts/roundtrip.zip",
            sha256="0" * 64,
            size_bytes=4096,
            media_type="application/zip",
            format="zip",
            scan_report={"scanner": "trivy", "level": "low"},
            retain_until=now,
            created_at=now,
            updated_at=now,
        )
        await conn.execute(table.insert().values(**values))
        row = (
            await conn.execute(table.select().where(table.c.id == artifact_id))
        ).one()
    _assert_row_matches(row, values)


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_service_artifact_accepts_all_documented_enum_values(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["service_artifact"]
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
        for index, kind in enumerate(SERVICE_ARTIFACT_KIND_VALUES):
            await conn.execute(
                table.insert().values(
                    **_artifact_values(
                        service_id,
                        revision_id,
                        artifact_id=uuid.uuid4(),
                        kind=kind,
                        object_key=f"kind/{index}",
                    )
                )
            )
        for index, backend in enumerate(SERVICE_ARTIFACT_STORAGE_VALUES):
            await conn.execute(
                table.insert().values(
                    **_artifact_values(
                        service_id,
                        revision_id,
                        artifact_id=uuid.uuid4(),
                        storage_backend=backend,
                        object_key=f"backend/{index}",
                    )
                )
            )
        for index, state in enumerate(SERVICE_ARTIFACT_STATE_VALUES):
            await conn.execute(
                table.insert().values(
                    **_artifact_values(
                        service_id,
                        revision_id,
                        artifact_id=uuid.uuid4(),
                        state=state,
                        object_key=f"state/{index}",
                    )
                )
            )


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_service_artifact_storage_backend_object_key_unique(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["service_artifact"]
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
        await conn.execute(
            table.insert().values(
                **_artifact_values(
                    service_id,
                    revision_id,
                    artifact_id=uuid.uuid4(),
                    object_key="pkg/dup.zip",
                )
            )
        )
    # Same (storage_backend, object_key) must violate the UNIQUE constraint.
    with pytest.raises(IntegrityError):
        async with provisioned_db.engine.begin() as conn:
            await conn.execute(
                table.insert().values(
                    **_artifact_values(
                        service_id,
                        revision_id,
                        artifact_id=uuid.uuid4(),
                        object_key="pkg/dup.zip",
                    )
                )
            )
    # Same storage_backend but a different object_key is allowed.
    async with provisioned_db.engine.begin() as conn:
        await conn.execute(
            table.insert().values(
                **_artifact_values(
                    service_id,
                    revision_id,
                    artifact_id=uuid.uuid4(),
                    object_key="pkg/other.zip",
                )
            )
        )


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_service_artifact_check_constraints(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["service_artifact"]
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
    invalid = [
        {"kind": "not-a-kind"},
        {"storage_backend": "http"},
        {"state": "nope"},
        {"size_bytes": -1},
    ]
    for overrides in invalid:
        with pytest.raises((IntegrityError, OperationalError)):
            async with provisioned_db.engine.begin() as conn:
                await conn.execute(
                    table.insert().values(
                        **_artifact_values(
                            service_id,
                            revision_id,
                            artifact_id=uuid.uuid4(),
                            **overrides,
                        )
                    )
                )


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_build_run_persists_and_round_trips(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["build_run"]
    now = datetime.now(UTC)
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
        source_id = uuid.uuid4()
        output_id = uuid.uuid4()
        log_id = uuid.uuid4()
        await _insert_artifact(
            conn,
            service_id,
            revision_id,
            artifact_id=source_id,
            kind="source_package",
            object_key="pkg/src.zip",
        )
        await _insert_artifact(
            conn,
            service_id,
            revision_id,
            artifact_id=output_id,
            kind="build_bundle",
            object_key="build/out.zip",
        )
        await _insert_artifact(
            conn,
            service_id,
            revision_id,
            artifact_id=log_id,
            kind="build_log",
            object_key="build/log.txt",
        )
        run_id = uuid.uuid4()
        values = _build_run_values(
            service_id,
            revision_id,
            run_id,
            source_id,
            output_artifact_id=output_id,
            log_artifact_id=log_id,
            discovered_descriptor={"name": "svc", "tools": 3},
            started_at=now,
            finished_at=now,
            created_at=now,
            updated_at=now,
            now=now,
        )
        await conn.execute(table.insert().values(**values))
        row = (
            await conn.execute(table.select().where(table.c.id == run_id))
        ).one()
    _assert_row_matches(row, values)


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_build_run_accepts_all_documented_enum_values(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["build_run"]
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
        source_id = uuid.uuid4()
        await _insert_artifact(
            conn,
            service_id,
            revision_id,
            artifact_id=source_id,
            kind="source_package",
            object_key="pkg/enums.zip",
        )
        for strategy in BUILD_RUN_STRATEGY_VALUES:
            await conn.execute(
                table.insert().values(
                    **_build_run_values(
                        service_id,
                        revision_id,
                        uuid.uuid4(),
                        source_id,
                        strategy=strategy,
                    )
                )
            )
        for status in BUILD_RUN_STATUS_VALUES:
            await conn.execute(
                table.insert().values(
                    **_build_run_values(
                        service_id,
                        revision_id,
                        uuid.uuid4(),
                        source_id,
                        status=status,
                    )
                )
            )


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_build_run_foreign_keys_and_non_unique_index(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["build_run"]
    now = datetime.now(UTC)
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
        source_id = uuid.uuid4()
        await _insert_artifact(
            conn,
            service_id,
            revision_id,
            artifact_id=source_id,
            kind="source_package",
            object_key="pkg/fk.zip",
        )
        # output/log artifacts are nullable: a run without them is valid.
        await conn.execute(
            table.insert().values(
                **_build_run_values(
                    service_id,
                    revision_id,
                    uuid.uuid4(),
                    source_id,
                    now=now,
                )
            )
        )
        # (service_id, status, created_at) is a plain index, not a unique one: two
        # runs sharing all three columns must both insert.
        await conn.execute(
            table.insert().values(
                **_build_run_values(
                    service_id,
                    revision_id,
                    uuid.uuid4(),
                    source_id,
                    now=now,
                )
            )
        )
    # source_artifact_id is a NOT NULL FK: a dangling reference is rejected.
    with pytest.raises(IntegrityError):
        async with provisioned_db.engine.begin() as conn:
            await conn.execute(
                table.insert().values(
                    **_build_run_values(
                        service_id,
                        revision_id,
                        uuid.uuid4(),
                        uuid.uuid4(),
                        now=now,
                    )
                )
            )


@pytest.mark.parametrize("provisioned_db", DIALECTS, indirect=True)
@pytest.mark.asyncio
async def test_build_run_check_constraints(
    provisioned_db: ProvisionedDb,
) -> None:
    table = Base.metadata.tables["build_run"]
    async with provisioned_db.engine.begin() as conn:
        service_id, revision_id = await _seed_service(conn)
        source_id = uuid.uuid4()
        await _insert_artifact(
            conn,
            service_id,
            revision_id,
            artifact_id=source_id,
            kind="source_package",
            object_key="pkg/chk.zip",
        )
    for overrides in [{"strategy": "not-a-strategy"}, {"status": "unknown"}]:
        with pytest.raises((IntegrityError, OperationalError)):
            async with provisioned_db.engine.begin() as conn:
                await conn.execute(
                    table.insert().values(
                        **_build_run_values(
                            service_id,
                            revision_id,
                            uuid.uuid4(),
                            source_id,
                            **overrides,
                        )
                    )
                )
