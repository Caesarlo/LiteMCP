"""DB-layer contract for the ``service_condition`` and ``mcp_task`` tables (M1-MODEL-006).

``service_condition`` persists the runtime-observed conditions of a service
(independent of the user's desired config); ``mcp_task`` is the async-operation
(MCP Tasks) model. This suite pins the documented schema and constraints
offline against ``Base.metadata`` and then drives both tables on real
PostgreSQL and real MySQL through a unique, freshly-migrated database per run.

No SQLite is involved. A live-database connection failure is a hard failure,
never a skip: these tables only mean something when the real dialects uphold
the constraints.
"""

import os
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from litemcp.db.models import Base
from litemcp.db.types import ENUM_CODE, ID, UTC_TS

_DIALECTS = ("postgres", "mysql")

_TABLES = Base.metadata.tables

# Live-database endpoints. Every value is overridable via environment so CI can
# point the same suite at its own PostgreSQL/MySQL instances.
_POSTGRES_URL = "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp"
_MYSQL_APP_URL = "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp"
_MYSQL_ROOT_URL = "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql"


def _maintenance_url(dialect: str) -> str:
    if dialect == "postgres":
        return os.environ.get("LITEMCP_TEST_POSTGRES_URL", _POSTGRES_URL)
    return os.environ.get("LITEMCP_TEST_MYSQL_URL", _MYSQL_APP_URL)


def _mysql_root_url() -> str:
    return os.environ.get("LITEMCP_TEST_MYSQL_ROOT_URL", _MYSQL_ROOT_URL)


def _database_url(dialect: str, db_name: str) -> str:
    parts = urlparse(_maintenance_url(dialect))
    return urlunparse(parts._replace(path=f"/{db_name}"))


def _unique_db_name() -> str:
    return f"litemcp_opcond_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# --------------------------------------------------------------------------
# Live-database provisioning
# --------------------------------------------------------------------------


async def _grant_mysql_app_user(conn, db_name: str) -> None:
    result = await conn.exec_driver_sql(
        "SELECT host FROM mysql.user WHERE user = 'litemcp'"
    )
    hosts = [row[0] for row in result.fetchall()]
    for host in hosts or ["localhost"]:
        # aiomysql %-formats the statement whenever a parameters tuple is
        # passed (exec_driver_sql passes an empty tuple here), so a literal
        # '%' in a host must be escaped as '%%'.
        safe_host = str(host).replace("%", "%%")
        await conn.exec_driver_sql(
            f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO 'litemcp'@'{safe_host}'"
        )


async def _create_database(dialect: str, db_name: str) -> None:
    if dialect == "postgres":
        engine = create_async_engine(
            _maintenance_url("postgres"), isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as conn:
                await conn.exec_driver_sql(f'CREATE DATABASE "{db_name}"')
        finally:
            await engine.dispose()
        return

    engine = create_async_engine(_mysql_root_url())
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(
                f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4"
            )
            await _grant_mysql_app_user(conn, db_name)
    finally:
        await engine.dispose()


async def _drop_database(dialect: str, db_name: str) -> None:
    if dialect == "postgres":
        engine = create_async_engine(
            _maintenance_url("postgres"), isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as conn:
                await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await engine.dispose()
        return

    engine = create_async_engine(_mysql_root_url())
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(f"DROP DATABASE IF EXISTS `{db_name}`")
    finally:
        await engine.dispose()


def _run_alembic_upgrade(url: str) -> None:
    backend_dir = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def _upgrade_head(url: str) -> None:
    # env.py drives its engine with asyncio.run(), which cannot be called from
    # inside the pytest-asyncio event-loop thread, so run it on a worker thread.
    # Future.result() re-raises any failure in the caller thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run_alembic_upgrade, url).result()


@dataclass
class _LiveContext:
    engine: AsyncEngine
    dialect: str


@pytest_asyncio.fixture(params=_DIALECTS)
async def live_engine(request: pytest.FixtureRequest) -> AsyncIterator[_LiveContext]:
    dialect = request.param
    db_name = _unique_db_name()
    await _drop_database(dialect, db_name)
    await _create_database(dialect, db_name)
    url = _database_url(dialect, db_name)
    try:
        _upgrade_head(url)
    except Exception:
        await _drop_database(dialect, db_name)
        raise
    engine = create_async_engine(url)
    try:
        yield _LiveContext(engine=engine, dialect=dialect)
    finally:
        await engine.dispose()
        await _drop_database(dialect, db_name)


# --------------------------------------------------------------------------
# Parent-row insert helpers (parent tables come from prior M1-MODEL-* slices)
# --------------------------------------------------------------------------


async def _insert_team(conn, label: str) -> str:
    team_id = str(uuid.uuid4())
    await conn.execute(
        _TABLES["team"].insert().values(
            id=team_id,
            key=f"team_{label}",
            key_normalized=f"team_{label}",
            name=f"Team {label}",
            status="active",
            created_at=_now(),
            created_by="system",
            updated_at=_now(),
            updated_by="system",
            row_version=1,
        )
    )
    return team_id


async def _insert_service(conn, team_id: str, label: str) -> str:
    service_id = str(uuid.uuid4())
    await conn.execute(
        _TABLES["mcp_service"].insert().values(
            id=service_id,
            namespace_key="default",
            team_id=team_id,
            type="http_api",
            name=f"service_{label}",
            name_normalized=f"service_{label}",
            uniqueness_scope="LIVE",
            tags=[],
            desired_status="enabled",
            generation=1,
            observed_generation=0,
            runtime_status="pending",
            agent_auth_mode="api_key",
            created_at=_now(),
            created_by="system",
            updated_at=_now(),
            updated_by="system",
            row_version=1,
        )
    )
    return service_id


async def _insert_toolset(conn, service_id: str, label: str) -> str:
    toolset_id = str(uuid.uuid4())
    now = _now()
    await conn.execute(
        _TABLES["toolset"].insert().values(
            id=toolset_id,
            service_id=service_id,
            version_no=1,
            source_kind="manual",
            source_digest="a" * 64,
            mcp_protocol_version="2025-11-25",
            json_schema_dialect="https://json-schema.org/draft/2020-12/schema",
            state="active",
            tool_count=0,
            created_at=now,
            created_by="system",
            updated_at=now,
            updated_by="system",
            row_version=1,
        )
    )
    return toolset_id


async def _insert_tool(conn, service_id: str, toolset_id: str, label: str) -> str:
    tool_id = str(uuid.uuid4())
    now = _now()
    await conn.execute(
        _TABLES["mcp_tool"].insert().values(
            id=tool_id,
            toolset_id=toolset_id,
            service_id=service_id,
            name=f"tool_{label}",
            input_schema={"type": "object", "properties": {}},
            raw_definition={
                "name": f"tool_{label}",
                "inputSchema": {"type": "object", "properties": {}},
            },
            definition_digest="b" * 64,
            source="manual",
            enabled=True,
            created_at=now,
            created_by="system",
            updated_at=now,
            updated_by="system",
            row_version=1,
        )
    )
    return tool_id


async def _insert_artifact(conn, service_id: str, label: str) -> str:
    artifact_id = str(uuid.uuid4())
    now = _now()
    await conn.execute(
        _TABLES["service_artifact"].insert().values(
            id=artifact_id,
            service_id=service_id,
            kind="build_bundle",
            storage_backend="filesystem",
            object_key=f"artifacts/{label}",
            sha256="c" * 64,
            size_bytes=0,
            media_type="application/zip",
            format="zip",
            state="available",
            created_at=now,
            created_by="system",
            updated_at=now,
            updated_by="system",
            row_version=1,
        )
    )
    return artifact_id


async def _insert_task_parents(conn, label: str) -> dict[str, str]:
    team_id = await _insert_team(conn, label)
    service_id = await _insert_service(conn, team_id, label)
    toolset_id = await _insert_toolset(conn, service_id, label)
    tool_id = await _insert_tool(conn, service_id, toolset_id, label)
    artifact_id = await _insert_artifact(conn, service_id, label)
    return {
        "team_id": team_id,
        "service_id": service_id,
        "toolset_id": toolset_id,
        "tool_id": tool_id,
        "artifact_id": artifact_id,
    }


# --------------------------------------------------------------------------
# Feature-row insert helpers
# --------------------------------------------------------------------------


async def _insert_condition(
    conn,
    service_id: str,
    ctype: str,
    *,
    status: str = "true",
    reason: str = "PROBE_OK",
    message: str | None = None,
    last_probe_at: datetime | None = None,
    observed_generation: int = 1,
) -> str:
    condition_id = str(uuid.uuid4())
    now = _now()
    await conn.execute(
        _TABLES["service_condition"].insert().values(
            id=condition_id,
            service_id=service_id,
            type=ctype,
            status=status,
            reason=reason,
            message=message,
            observed_generation=observed_generation,
            last_transition_at=now,
            last_probe_at=last_probe_at,
            created_at=now,
            created_by="system",
            updated_at=now,
            updated_by="system",
            row_version=1,
        )
    )
    return condition_id


async def _insert_task(
    conn,
    service_id: str,
    tool_id: str,
    *,
    status: str = "working",
    session_id_hash: str | None = None,
    downstream_task_id: str | None = None,
    status_message: str | None = None,
    result_artifact_id: str | None = None,
    expires_at: datetime | None = None,
    poll_interval_ms: int | None = None,
    created_at: datetime | None = None,
    last_updated_at: datetime | None = None,
) -> str:
    task_id = str(uuid.uuid4())
    now = _now()
    await conn.execute(
        _TABLES["mcp_task"].insert().values(
            id=task_id,
            service_id=service_id,
            tool_id=tool_id,
            session_id_hash=session_id_hash,
            downstream_task_id=downstream_task_id,
            status=status,
            status_message=status_message,
            result_artifact_id=result_artifact_id,
            created_at=created_at or now,
            last_updated_at=last_updated_at or now,
            expires_at=expires_at,
            poll_interval_ms=poll_interval_ms,
        )
    )
    return task_id


# --------------------------------------------------------------------------
# Offline structural contract (no database)
# --------------------------------------------------------------------------


def test_service_condition_columns_registered() -> None:
    assert "service_condition" in _TABLES, "service_condition table not registered"
    table = _TABLES["service_condition"]

    assert set(table.c.keys()) == {
        "id",
        "service_id",
        "type",
        "status",
        "reason",
        "message",
        "observed_generation",
        "last_transition_at",
        "last_probe_at",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "row_version",
    }
    # The diagnostic source of truth is not soft-deleted.
    assert "deleted_at" not in table.c
    assert "deleted_by" not in table.c

    assert set(table.primary_key.columns.keys()) == {"id"}
    assert {
        fk.parent.name: (fk.column.table.name, fk.column.name)
        for fk in table.foreign_keys
    } == {"service_id": ("mcp_service", "id")}

    unique_sets = {
        frozenset(uc.columns.keys())
        for uc in table.constraints
        if isinstance(uc, UniqueConstraint)
    }
    assert frozenset({"service_id", "type"}) in unique_sets


def test_service_condition_nullability_and_kinds() -> None:
    table = _TABLES["service_condition"]

    required = {
        "id",
        "service_id",
        "type",
        "status",
        "reason",
        "observed_generation",
        "last_transition_at",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "row_version",
    }
    for name in required:
        assert table.c[name].nullable is False, name
    for name in ("message", "last_probe_at"):
        assert table.c[name].nullable is True, name

    assert isinstance(table.c["id"].type, ID)
    assert isinstance(table.c["service_id"].type, ID)
    assert isinstance(table.c["observed_generation"].type, BigInteger)
    assert isinstance(table.c["row_version"].type, Integer)

    assert isinstance(table.c["reason"].type, String)
    assert table.c["reason"].type.length == 64
    assert isinstance(table.c["message"].type, String)
    assert table.c["message"].type.length == 2048
    assert isinstance(table.c["created_by"].type, String)
    assert table.c["created_by"].type.length == 128
    assert isinstance(table.c["updated_by"].type, String)
    assert table.c["updated_by"].type.length == 128

    for name in ("last_transition_at", "last_probe_at", "created_at", "updated_at"):
        assert isinstance(table.c[name].type, UTC_TS), name

    # Enum-like varchar columns are portable String/ENUM_CODE + CHECK; do not
    # pin widths (ENUM_CODE max+16 convention, M1-MODEL-001..005).
    for name in ("type", "status"):
        assert isinstance(table.c[name].type, (String, ENUM_CODE)), name
        assert not isinstance(table.c[name].type, Text), name


def test_service_condition_check_constraints() -> None:
    table = _TABLES["service_condition"]
    checks = " ".join(
        str(cc.sqltext) for cc in table.constraints if isinstance(cc, CheckConstraint)
    )
    for code in (
        "ConfigReady",
        "BuildReady",
        "ToolsReady",
        "RuntimeHealthy",
        "UpstreamReachable",
    ):
        assert f"'{code}'" in checks, code
    for code in ("true", "false", "unknown"):
        assert f"'{code}'" in checks, code


def test_mcp_task_columns_registered() -> None:
    assert "mcp_task" in _TABLES, "mcp_task table not registered"
    table = _TABLES["mcp_task"]

    assert set(table.c.keys()) == {
        "id",
        "service_id",
        "tool_id",
        "session_id_hash",
        "downstream_task_id",
        "status",
        "status_message",
        "result_artifact_id",
        "created_at",
        "last_updated_at",
        "expires_at",
        "poll_interval_ms",
    }
    # mcp_task carries the MCP time fields, not the §3.2 audit set.
    for absent in (
        "created_by",
        "updated_by",
        "updated_at",
        "row_version",
        "deleted_at",
        "deleted_by",
    ):
        assert absent not in table.c, absent

    assert set(table.primary_key.columns.keys()) == {"id"}
    assert {
        fk.parent.name: (fk.column.table.name, fk.column.name)
        for fk in table.foreign_keys
    } == {
        "service_id": ("mcp_service", "id"),
        "tool_id": ("mcp_tool", "id"),
        "result_artifact_id": ("service_artifact", "id"),
    }
    assert not any(isinstance(c, UniqueConstraint) for c in table.constraints)


def test_mcp_task_nullability_and_kinds() -> None:
    table = _TABLES["mcp_task"]

    required = {
        "id",
        "service_id",
        "tool_id",
        "status",
        "created_at",
        "last_updated_at",
    }
    for name in required:
        assert table.c[name].nullable is False, name
    for name in (
        "session_id_hash",
        "downstream_task_id",
        "status_message",
        "result_artifact_id",
        "expires_at",
        "poll_interval_ms",
    ):
        assert table.c[name].nullable is True, name

    for name in ("id", "service_id", "tool_id", "result_artifact_id"):
        assert isinstance(table.c[name].type, ID), name

    assert isinstance(table.c["session_id_hash"].type, String)
    assert table.c["session_id_hash"].type.length == 64
    assert isinstance(table.c["downstream_task_id"].type, String)
    assert table.c["downstream_task_id"].type.length == 255
    assert isinstance(table.c["status_message"].type, String)
    assert table.c["status_message"].type.length == 2048
    assert isinstance(table.c["status"].type, (String, ENUM_CODE))
    assert not isinstance(table.c["status"].type, Text)

    for name in ("created_at", "last_updated_at", "expires_at"):
        assert isinstance(table.c[name].type, UTC_TS), name
    assert isinstance(table.c["poll_interval_ms"].type, Integer)


def test_mcp_task_check_constraints() -> None:
    table = _TABLES["mcp_task"]
    check_constraints = [
        cc for cc in table.constraints if isinstance(cc, CheckConstraint)
    ]
    checks = " ".join(str(cc.sqltext) for cc in check_constraints)
    for code in ("working", "input_required", "completed", "failed", "cancelled"):
        assert f"'{code}'" in checks, code

    poll_checks = [
        str(cc.sqltext)
        for cc in check_constraints
        if "poll_interval_ms" in str(cc.sqltext)
    ]
    assert len(poll_checks) == 1
    assert "> 0" in poll_checks[0]


# --------------------------------------------------------------------------
# Live contract: service_condition
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_condition_roundtrip_and_time_precision(
    live_engine: _LiveContext,
) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        team_id = await _insert_team(conn, "roundtrip")
        service_id = await _insert_service(conn, team_id, "roundtrip")
        condition_id = str(uuid.uuid4())
        transition_at = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
        probe_at = datetime(2026, 1, 2, 3, 4, 6, 123456, tzinfo=UTC)
        await conn.execute(
            _TABLES["service_condition"].insert().values(
                id=condition_id,
                service_id=service_id,
                type="RuntimeHealthy",
                status="true",
                reason="PROBE_OK",
                message="upstream reachable",
                observed_generation=7,
                last_transition_at=transition_at,
                last_probe_at=probe_at,
                created_at=transition_at,
                created_by="system",
                updated_at=transition_at,
                updated_by="system",
                row_version=1,
            )
        )
        row = (
            await conn.execute(
                select(_TABLES["service_condition"]).where(
                    _TABLES["service_condition"].c.id == condition_id
                )
            )
        ).mappings().one()

    assert str(row["id"]) == condition_id
    assert str(row["service_id"]) == service_id
    assert row["type"] == "RuntimeHealthy"
    assert row["status"] == "true"
    assert row["reason"] == "PROBE_OK"
    assert row["message"] == "upstream reachable"
    assert row["observed_generation"] == 7
    assert row["row_version"] == 1
    assert _as_utc(row["last_transition_at"]) == transition_at
    assert _as_utc(row["last_probe_at"]) == probe_at
    assert _as_utc(row["created_at"]) == transition_at
    assert _as_utc(row["updated_at"]) == transition_at


@pytest.mark.asyncio
async def test_service_condition_status_codes_roundtrip(
    live_engine: _LiveContext,
) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        team_id = await _insert_team(conn, "codes")
        service_id = await _insert_service(conn, team_id, "codes")
        by_type = {
            "ConfigReady": "true",
            "BuildReady": "false",
            "ToolsReady": "unknown",
            "RuntimeHealthy": "true",
            "UpstreamReachable": "false",
        }
        for ctype, status in by_type.items():
            await _insert_condition(conn, service_id, ctype, status=status)
        rows = (
            await conn.execute(
                select(_TABLES["service_condition"])
                .where(_TABLES["service_condition"].c.service_id == service_id)
                .order_by(_TABLES["service_condition"].c.type)
            )
        ).mappings().all()

    assert {row["type"]: row["status"] for row in rows} == by_type


@pytest.mark.asyncio
async def test_service_condition_unique_per_service_type(
    live_engine: _LiveContext,
) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        team_id = await _insert_team(conn, "unique")
        service_id = await _insert_service(conn, team_id, "unique")
        await _insert_condition(conn, service_id, "ConfigReady")

    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await _insert_condition(conn, service_id, "ConfigReady")


@pytest.mark.asyncio
async def test_service_condition_check_constraints_live(
    live_engine: _LiveContext,
) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        team_id = await _insert_team(conn, "check")
        service_id = await _insert_service(conn, team_id, "check")

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await _insert_condition(conn, service_id, "NotARealCondition")

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await _insert_condition(conn, service_id, "ConfigReady", status="sometimes")


@pytest.mark.asyncio
async def test_service_condition_fk_and_required_columns(
    live_engine: _LiveContext,
) -> None:
    engine = live_engine.engine
    now = _now()

    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                _TABLES["service_condition"].insert().values(
                    id=str(uuid.uuid4()),
                    service_id=str(uuid.uuid4()),
                    type="ConfigReady",
                    status="true",
                    reason="PROBE_OK",
                    observed_generation=1,
                    last_transition_at=now,
                    created_at=now,
                    created_by="system",
                    updated_at=now,
                    updated_by="system",
                    row_version=1,
                )
            )

    async with engine.begin() as conn:
        team_id = await _insert_team(conn, "required")
        service_id = await _insert_service(conn, team_id, "required")

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await conn.execute(
                _TABLES["service_condition"].insert().values(
                    id=str(uuid.uuid4()),
                    service_id=service_id,
                    status="true",
                    reason="PROBE_OK",
                    observed_generation=1,
                    last_transition_at=now,
                    created_at=now,
                    created_by="system",
                    updated_at=now,
                    updated_by="system",
                    row_version=1,
                )
            )


# --------------------------------------------------------------------------
# Live contract: mcp_task
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_task_roundtrip_and_time_precision(
    live_engine: _LiveContext,
) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        parents = await _insert_task_parents(conn, "roundtrip")
        task_id = str(uuid.uuid4())
        created_at = datetime(2026, 3, 4, 5, 6, 7, 987654, tzinfo=UTC)
        last_updated_at = datetime(2026, 3, 4, 5, 6, 8, 111222, tzinfo=UTC)
        expires_at = datetime(2026, 3, 5, 5, 6, 8, tzinfo=UTC)
        await conn.execute(
            _TABLES["mcp_task"].insert().values(
                id=task_id,
                service_id=parents["service_id"],
                tool_id=parents["tool_id"],
                session_id_hash="c" * 64,
                downstream_task_id="remote-task-1",
                status="working",
                status_message="in progress",
                result_artifact_id=parents["artifact_id"],
                created_at=created_at,
                last_updated_at=last_updated_at,
                expires_at=expires_at,
                poll_interval_ms=500,
            )
        )
        row = (
            await conn.execute(
                select(_TABLES["mcp_task"]).where(
                    _TABLES["mcp_task"].c.id == task_id
                )
            )
        ).mappings().one()

    assert str(row["id"]) == task_id
    assert str(row["service_id"]) == parents["service_id"]
    assert str(row["tool_id"]) == parents["tool_id"]
    assert str(row["result_artifact_id"]) == parents["artifact_id"]
    assert row["session_id_hash"] == "c" * 64
    assert row["downstream_task_id"] == "remote-task-1"
    assert row["status"] == "working"
    assert row["status_message"] == "in progress"
    assert row["poll_interval_ms"] == 500
    assert _as_utc(row["created_at"]) == created_at
    assert _as_utc(row["last_updated_at"]) == last_updated_at
    assert _as_utc(row["expires_at"]) == expires_at


@pytest.mark.asyncio
async def test_mcp_task_nullable_fields_can_be_null(
    live_engine: _LiveContext,
) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        parents = await _insert_task_parents(conn, "nullable")
        task_id = await _insert_task(conn, parents["service_id"], parents["tool_id"])
        row = (
            await conn.execute(
                select(_TABLES["mcp_task"]).where(
                    _TABLES["mcp_task"].c.id == task_id
                )
            )
        ).mappings().one()

    assert row["session_id_hash"] is None
    assert row["downstream_task_id"] is None
    assert row["status_message"] is None
    assert row["result_artifact_id"] is None
    assert row["expires_at"] is None
    assert row["poll_interval_ms"] is None
    assert row["status"] == "working"


@pytest.mark.asyncio
async def test_mcp_task_check_constraints_live(live_engine: _LiveContext) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        parents = await _insert_task_parents(conn, "check")

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await _insert_task(
                conn, parents["service_id"], parents["tool_id"], status="running"
            )

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await _insert_task(
                conn,
                parents["service_id"],
                parents["tool_id"],
                status="completed",
                poll_interval_ms=0,
            )

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await _insert_task(
                conn,
                parents["service_id"],
                parents["tool_id"],
                status="completed",
                poll_interval_ms=-5,
            )


@pytest.mark.asyncio
async def test_mcp_task_fk_and_required_columns(live_engine: _LiveContext) -> None:
    engine = live_engine.engine
    async with engine.begin() as conn:
        parents = await _insert_task_parents(conn, "fk")

    now = _now()
    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                _TABLES["mcp_task"].insert().values(
                    id=str(uuid.uuid4()),
                    service_id=str(uuid.uuid4()),
                    tool_id=str(uuid.uuid4()),
                    status="working",
                    created_at=now,
                    last_updated_at=now,
                )
            )

    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                _TABLES["mcp_task"].insert().values(
                    id=str(uuid.uuid4()),
                    service_id=parents["service_id"],
                    tool_id=str(uuid.uuid4()),
                    status="working",
                    created_at=now,
                    last_updated_at=now,
                )
            )

    async with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                _TABLES["mcp_task"].insert().values(
                    id=str(uuid.uuid4()),
                    service_id=parents["service_id"],
                    tool_id=parents["tool_id"],
                    result_artifact_id=str(uuid.uuid4()),
                    status="working",
                    created_at=now,
                    last_updated_at=now,
                )
            )

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await conn.execute(
                _TABLES["mcp_task"].insert().values(
                    id=str(uuid.uuid4()),
                    tool_id=parents["tool_id"],
                    status="working",
                    created_at=now,
                    last_updated_at=now,
                )
            )

    async with engine.begin() as conn:
        with pytest.raises((IntegrityError, OperationalError)):
            await conn.execute(
                _TABLES["mcp_task"].insert().values(
                    id=str(uuid.uuid4()),
                    service_id=parents["service_id"],
                    status="working",
                    created_at=now,
                    last_updated_at=now,
                )
            )
