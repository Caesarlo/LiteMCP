"""DB-layer contract for the ``toolset`` and ``mcp_tool`` tables (M1-MODEL-004).

Pins the schema mandated by docs/architecture/01-data-model.md:

* §5.7 ``toolset`` — the atomic publish unit — with its enum CHECKs, the
  ``(service_id, version_no)`` and ``(id, service_id)`` UNIQUE keys, the FKs
  to ``mcp_service`` and ``service_config_revision``, ``tool_count >= 0``, and
  the generic §3.2 audit fields.
* §5.8 ``mcp_tool`` — lossless MCP Tool definitions — with its ``(toolset_id,
  name)`` UNIQUE, its THREE foreign-key mechanisms (``toolset_id -> toolset.id
  ON DELETE CASCADE``, the redundant ``service_id -> service.id``, and the
  composite ``(toolset_id, service_id) -> toolset(id, service_id)`` that
  guarantees tool.service == toolset.service), the ``source`` CHECK, the
  ``enabled`` default of true, and the generic §3.2 audit fields.
* The feature behavior: candidate (staging), active, retired and rejected
  toolsets can persist complete MCP Tool Schemas losslessly.

Suite structure follows the repo's DB contract-suite convention:

* OFFLINE structural tests — no database — introspect ``Base.metadata`` for
  columns, portable kinds, nullability, UNIQUE / CHECK / FK constraints.
* LIVE tests parametrized over PostgreSQL and MySQL. Each live test
  provisions a UNIQUE named empty database, runs Alembic ``upgrade head`` on
  it in a worker thread (the async env.py drives its engine with
  ``asyncio.run``, which cannot run on the pytest-asyncio loop thread), drives
  the tables through an async engine, and DROPS the database in teardown. A
  connection failure is a hard failure, never a skip.
"""

import os
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from litemcp.db.models import Base
from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, LONG_TEXT, UTC_TS

BACKEND_DIR = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = BACKEND_DIR / "migrations"

# Live DB URLs (all overridable via env). PostgreSQL's ``litemcp`` user can
# create databases; MySQL needs the privileged root URL because the app user
# only owns ``litemcp.*``.
_POSTGRES_URL = "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp"
_MYSQL_APP_URL = "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp"
_MYSQL_ROOT_URL = "mysql+aiomysql://root:litemcp-root@localhost:3307/mysql"

# The four states named by the feature behavior.
_TOOLSET_STATES = ("staging", "active", "rejected", "retired")

# row_version is a §3.2 audit counter; its exact width is not pinned by the doc.
_INTEGER_TYPES = (Integer, BigInteger)

# --------------------------------------------------------------------------
# Sample MCP Tool Schema payloads used by the lossless round-trip test.
# --------------------------------------------------------------------------

_SAMPLE_INPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["query", "limit"],
    "properties": {
        "query": {"type": "string", "description": "搜索关键词"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
    "additionalProperties": False,
}

_SAMPLE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {"items": {"type": "array", "items": {"type": "object"}}},
}

_SAMPLE_ANNOTATIONS = {
    "title": "Search",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}

_SAMPLE_EXECUTION = {"taskSupport": "optional"}

_SAMPLE_ICONS = [
    {"src": "https://example.invalid/search.svg", "width": 24, "height": 24},
    {"src": "data:image/svg+xml;base64,PHN2Zy8+", "width": 16, "height": 16},
]

_SAMPLE_META = {"unknown_extension": {"depth": 2, "items": [1, 2, 3], "nested": {"a": "b"}}}

_SAMPLE_HTTP_BINDING = {
    "method": "GET",
    "path": "/api/search",
    "parameters": {
        "query": {"source": "query", "name": "q"},
        "limit": {"source": "query", "name": "n", "default": 20},
    },
    "response": {"contentType": "application/json", "statusCode": 200},
    "timeoutMs": 5000,
}

_SAMPLE_RAW_DEFINITION = {
    "name": "search",
    "title": "Search Tools",
    "description": "执行全文搜索并返回结构化结果。",
    "inputSchema": _SAMPLE_INPUT_SCHEMA,
    "outputSchema": _SAMPLE_OUTPUT_SCHEMA,
    "annotations": _SAMPLE_ANNOTATIONS,
    "execution": _SAMPLE_EXECUTION,
    "icons": _SAMPLE_ICONS,
    "_meta": _SAMPLE_META,
}


# --------------------------------------------------------------------------
# Structural-introspection helpers (offline).
# --------------------------------------------------------------------------


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError as exc:
        raise AssertionError(f"table {name!r} is not registered on Base.metadata") from exc


def _assert_column(table: Table, name: str, type_: type, nullable: bool) -> None:
    column = table.c[name]
    assert column.nullable is nullable, (
        f"{table.name}.{name}: nullable={column.nullable} != {nullable}"
    )
    assert isinstance(column.type, type_), (
        f"{table.name}.{name}: type {column.type!r} is not an instance of {type_!r}"
    )


def _assert_varchar(table: Table, name: str, length: int, nullable: bool) -> None:
    column = table.c[name]
    assert column.nullable is nullable, (
        f"{table.name}.{name}: nullable={column.nullable} != {nullable}"
    )
    assert column.type.length == length, (
        f"{table.name}.{name}: length={column.type.length!r} != {length!r}"
    )


def _assert_enum(table: Table, name: str, nullable: bool) -> None:
    """Enum-like columns are ENUM_CODE-typed; exact varchar width follows the
    ENUM_CODE max+16 convention (M1-MODEL-001/002/003 do not pin enum widths).
    The CHECK enumeration content is pinned separately by _check_constraint."""
    column = table.c[name]
    assert column.nullable is nullable, (
        f"{table.name}.{name}: nullable={column.nullable} != {nullable}"
    )
    assert isinstance(column.type, (ENUM_CODE, String)), (
        f"{table.name}.{name}: type {column.type!r} is not ENUM_CODE/String"
    )


def _unique_constraint(table: Table, columns: list[str]) -> UniqueConstraint | None:
    expected = set(columns)
    for constraint in table.constraints:
        if (
            isinstance(constraint, UniqueConstraint)
            and {column.name for column in constraint.columns} == expected
        ):
            return constraint
    return None


def _check_constraint(table: Table, fragments: list[str]) -> CheckConstraint | None:
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint):
            sql_text = str(constraint.sqltext)
            if all(fragment in sql_text for fragment in fragments):
                return constraint
    return None


def _column_fk(
    table: Table, column: str, target_table: str, target_column: str
) -> ForeignKey | None:
    """Find a single-column FK on ``column`` referencing the given target.

    Accepts both column-level ``ForeignKey`` declarations and single-column
    table-level ``ForeignKeyConstraint`` declarations.
    """
    for fk in table.c[column].foreign_keys:
        if fk.column.table.name == target_table and fk.column.name == target_column:
            return fk
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        if [c.name for c in constraint.columns] != [column]:
            continue
        element = constraint.elements[0]
        if element.column.table.name == target_table and element.column.name == target_column:
            return element
    return None


def _composite_fk(
    table: Table, local: list[str], target: list[tuple[str, str]]
) -> ForeignKeyConstraint | None:
    for constraint in table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        if [column.name for column in constraint.columns] != local:
            continue
        referenced = [(fk.column.table.name, fk.column.name) for fk in constraint.elements]
        if referenced == target:
            return constraint
    return None


# --------------------------------------------------------------------------
# Live-database harness helpers.
# --------------------------------------------------------------------------


@dataclass
class _DedicatedDatabase:
    dialect: str
    name: str
    url: str


def _postgres_url() -> str:
    return os.environ.get("LITEMCP_TEST_POSTGRES_URL", _POSTGRES_URL)


def _mysql_app_url() -> str:
    return os.environ.get("LITEMCP_TEST_MYSQL_URL", _MYSQL_APP_URL)


def _mysql_root_url() -> str:
    return os.environ.get("LITEMCP_TEST_MYSQL_ROOT_URL", _MYSQL_ROOT_URL)


def _with_database(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


async def _provision(dialect: str) -> _DedicatedDatabase:
    """Create a unique empty database and return its dedicated app URL."""
    name = f"model004_{uuid.uuid4().hex[:16]}"
    if dialect == "postgres":
        admin = create_async_engine(_postgres_url(), isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            await admin.dispose()
        return _DedicatedDatabase(
            dialect="postgres", name=name, url=_with_database(_postgres_url(), name)
        )
    if dialect == "mysql":
        admin = create_async_engine(_mysql_root_url(), isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(
                    text(
                        f"CREATE DATABASE `{name}` "
                        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    )
                )
                rows = (
                    await conn.execute(
                        text("SELECT User, Host FROM mysql.user WHERE User='litemcp'")
                    )
                ).fetchall()
                hosts = [row[1] for row in rows] or ["%"]
                for host in hosts:
                    await conn.execute(
                        text(f"GRANT ALL PRIVILEGES ON `{name}`.* TO 'litemcp'@'{host}'")
                    )
        finally:
            await admin.dispose()
        return _DedicatedDatabase(
            dialect="mysql", name=name, url=_with_database(_mysql_app_url(), name)
        )
    raise AssertionError(f"unknown dialect: {dialect}")


async def _drop(db: _DedicatedDatabase) -> None:
    if db.dialect == "postgres":
        admin = create_async_engine(_postgres_url(), isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{db.name}"'))
        finally:
            await admin.dispose()
        return
    if db.dialect == "mysql":
        admin = create_async_engine(_mysql_root_url(), isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f"DROP DATABASE IF EXISTS `{db.name}`"))
        finally:
            await admin.dispose()
        return
    raise AssertionError(f"unknown dialect: {db.dialect}")


def _run_alembic_upgrade(url: str) -> None:
    """Run ``alembic upgrade head`` on the dedicated database.

    Runs in a dedicated worker thread because the async migration env.py
    drives its engine with ``asyncio.run()``, which cannot be called from the
    pytest-asyncio event-loop thread.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            command.upgrade(config, "head")
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    thread = threading.Thread(target=_worker, name=f"alembic-upgrade-{uuid.uuid4().hex[:6]}")
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


# --------------------------------------------------------------------------
# Row-insertion helpers (used only by live tests).
# --------------------------------------------------------------------------


async def _ensure_team(conn) -> object:
    team = _table("team")
    row = (await conn.execute(select(team.c.id).limit(1))).first()
    if row is not None:
        return row[0]
    team_id = uuid.uuid4()
    now = datetime.now(UTC)
    await conn.execute(
        team.insert().values(
            id=team_id,
            key="default",
            key_normalized="default",
            name="Default",
            description=None,
            status="active",
            created_at=now,
            created_by="contract-test",
            updated_at=now,
            updated_by="contract-test",
            row_version=0,
        )
    )
    return team_id


async def _insert_service(conn, team_id: object, name: str) -> object:
    mcp_service = _table("mcp_service")
    service_id = uuid.uuid4()
    now = datetime.now(UTC)
    await conn.execute(
        mcp_service.insert().values(
            id=service_id,
            namespace_key="default",
            team_id=team_id,
            type="http_api",
            name=name,
            name_normalized=name,
            uniqueness_scope="LIVE",
            tags=[],
            desired_status="enabled",
            generation=1,
            observed_generation=0,
            runtime_status="pending",
            agent_auth_mode="none",
            created_at=now,
            created_by="contract-test",
            updated_at=now,
            updated_by="contract-test",
            row_version=0,
        )
    )
    return service_id


async def _insert_revision(conn, service_id: object) -> object:
    revision = _table("service_config_revision")
    revision_id = uuid.uuid4()
    now = datetime.now(UTC)
    await conn.execute(
        revision.insert().values(
            id=revision_id,
            service_id=service_id,
            generation=1,
            schema_version=1,
            config_kind="http_api",
            public_config={"baseUrl": "https://example.invalid", "timeoutMs": 3000},
            source_mode="manual",
            config_digest="c" * 64,
            state="validated",
            created_at=now,
            created_by="contract-test",
        )
    )
    return revision_id


async def _insert_toolset(
    conn,
    service_id: object,
    *,
    state: str,
    version_no: int,
    config_revision_id: object | None = None,
    source_kind: str = "manual",
    source_digest: str | None = None,
    mcp_protocol_version: str = "2025-11-25",
    json_schema_dialect: str = "https://json-schema.org/draft/2020-12/schema",
    server_capabilities: object | None = None,
    server_info: object | None = None,
    instructions: str | None = None,
    validation_report: object | None = None,
    tool_count: int = 1,
    activated_at: datetime | None = None,
    retired_at: datetime | None = None,
) -> object:
    toolset = _table("toolset")
    toolset_id = uuid.uuid4()
    now = datetime.now(UTC)
    await conn.execute(
        toolset.insert().values(
            id=toolset_id,
            service_id=service_id,
            config_revision_id=config_revision_id,
            version_no=version_no,
            source_kind=source_kind,
            source_digest=source_digest or ("d" * 64),
            mcp_protocol_version=mcp_protocol_version,
            json_schema_dialect=json_schema_dialect,
            server_capabilities=server_capabilities,
            server_info=server_info,
            instructions=instructions,
            state=state,
            validation_report=validation_report,
            tool_count=tool_count,
            activated_at=activated_at,
            retired_at=retired_at,
            created_at=now,
            created_by="contract-test",
            updated_at=now,
            updated_by="contract-test",
            row_version=0,
        )
    )
    return toolset_id


async def _insert_tool(
    conn,
    toolset_id: object,
    service_id: object,
    *,
    name: str,
    title: str | None = None,
    description: str | None = None,
    input_schema: object | None = None,
    output_schema: object | None = None,
    annotations: object | None = None,
    execution: object | None = None,
    icons: object | None = None,
    meta: object | None = None,
    raw_definition: object | None = None,
    definition_digest: str | None = None,
    source: str = "manual",
    http_binding: object | None = None,
    enabled: bool | None = None,
) -> object:
    mcp_tool = _table("mcp_tool")
    tool_id = uuid.uuid4()
    now = datetime.now(UTC)
    values = {
        "id": tool_id,
        "toolset_id": toolset_id,
        "service_id": service_id,
        "name": name,
        "title": title,
        "description": description,
        "input_schema": input_schema if input_schema is not None else _SAMPLE_INPUT_SCHEMA,
        "output_schema": output_schema,
        "annotations": annotations,
        "execution": execution,
        "icons": icons,
        "meta": meta,
        "raw_definition": raw_definition if raw_definition is not None else _SAMPLE_RAW_DEFINITION,
        "definition_digest": definition_digest or ("f" * 64),
        "source": source,
        "http_binding": http_binding,
        "created_at": now,
        "created_by": "contract-test",
        "updated_at": now,
        "updated_by": "contract-test",
        "row_version": 0,
    }
    if enabled is not None:
        values["enabled"] = enabled
    await conn.execute(mcp_tool.insert().values(**values))
    return tool_id


@pytest.fixture(params=["postgres", "mysql"])
def dialect(request: pytest.FixtureRequest) -> str:
    return request.param


# --------------------------------------------------------------------------
# Offline structural tests.
# --------------------------------------------------------------------------


def test_toolset_table_columns() -> None:
    toolset = _table("toolset")
    assert toolset.c.id.primary_key
    _assert_column(toolset, "id", ID, False)
    _assert_column(toolset, "service_id", ID, False)
    _assert_column(toolset, "config_revision_id", ID, True)
    _assert_column(toolset, "version_no", BigInteger, False)
    _assert_enum(toolset, "source_kind", False)
    _assert_varchar(toolset, "source_digest", 64, False)
    _assert_varchar(toolset, "mcp_protocol_version", 16, False)
    _assert_varchar(toolset, "json_schema_dialect", 128, False)
    _assert_column(toolset, "server_capabilities", JSON_DOC, True)
    _assert_column(toolset, "server_info", JSON_DOC, True)
    _assert_column(toolset, "instructions", LONG_TEXT, True)
    _assert_enum(toolset, "state", False)
    _assert_column(toolset, "validation_report", JSON_DOC, True)
    _assert_column(toolset, "tool_count", Integer, False)
    _assert_column(toolset, "activated_at", UTC_TS, True)
    _assert_column(toolset, "retired_at", UTC_TS, True)
    # §3.2 generic audit fields on toolset.
    _assert_column(toolset, "created_at", UTC_TS, False)
    _assert_varchar(toolset, "created_by", 128, False)
    _assert_column(toolset, "updated_at", UTC_TS, False)
    _assert_varchar(toolset, "updated_by", 128, False)
    _assert_column(toolset, "row_version", _INTEGER_TYPES, False)


def test_toolset_constraints() -> None:
    toolset = _table("toolset")
    assert _unique_constraint(toolset, ["service_id", "version_no"]) is not None
    # §5.7 "额外建立 UNIQUE (id, service_id)": FK target for the composite
    # mcp_tool FK and the service active-toolset pointer.
    assert _unique_constraint(toolset, ["id", "service_id"]) is not None
    assert _column_fk(toolset, "service_id", "mcp_service", "id") is not None
    # config_revision_id is a real (nullable) FK to service_config_revision.id.
    assert _column_fk(toolset, "config_revision_id", "service_config_revision", "id") is not None
    assert _check_constraint(
        toolset, ["source_kind", "manual", "fastmcp", "descriptor", "remote_mcp"]
    ) is not None
    assert _check_constraint(
        toolset,
        ["state", "staging", "validating", "validated", "active", "rejected", "retired"],
    ) is not None
    assert _check_constraint(toolset, ["tool_count", ">=", "0"]) is not None


def test_mcp_tool_table_columns() -> None:
    mcp_tool = _table("mcp_tool")
    assert mcp_tool.c.id.primary_key
    _assert_column(mcp_tool, "id", ID, False)
    _assert_column(mcp_tool, "toolset_id", ID, False)
    _assert_column(mcp_tool, "service_id", ID, False)
    _assert_varchar(mcp_tool, "name", 128, False)
    _assert_varchar(mcp_tool, "title", 256, True)
    _assert_column(mcp_tool, "description", LONG_TEXT, True)
    _assert_column(mcp_tool, "input_schema", JSON_DOC, False)
    _assert_column(mcp_tool, "output_schema", JSON_DOC, True)
    _assert_column(mcp_tool, "annotations", JSON_DOC, True)
    _assert_column(mcp_tool, "execution", JSON_DOC, True)
    _assert_column(mcp_tool, "icons", JSON_DOC, True)
    _assert_column(mcp_tool, "meta", JSON_DOC, True)
    _assert_column(mcp_tool, "raw_definition", JSON_DOC, False)
    _assert_varchar(mcp_tool, "definition_digest", 64, False)
    _assert_enum(mcp_tool, "source", False)
    _assert_column(mcp_tool, "http_binding", JSON_DOC, True)
    _assert_column(mcp_tool, "enabled", Boolean, False)
    assert mcp_tool.c.enabled.server_default is not None or mcp_tool.c.enabled.default is not None
    # §3.2 generic audit fields on mcp_tool.
    _assert_column(mcp_tool, "created_at", UTC_TS, False)
    _assert_varchar(mcp_tool, "created_by", 128, False)
    _assert_column(mcp_tool, "updated_at", UTC_TS, False)
    _assert_varchar(mcp_tool, "updated_by", 128, False)
    _assert_column(mcp_tool, "row_version", _INTEGER_TYPES, False)


def test_mcp_tool_constraints() -> None:
    mcp_tool = _table("mcp_tool")
    assert _unique_constraint(mcp_tool, ["toolset_id", "name"]) is not None
    # (a) toolset_id -> toolset.id with ON DELETE CASCADE (staging cleanup).
    fk = _column_fk(mcp_tool, "toolset_id", "toolset", "id")
    assert fk is not None
    assert fk.ondelete is not None and fk.ondelete.upper() == "CASCADE"
    # (b) redundant service_id FK for authz/query isolation.
    assert _column_fk(mcp_tool, "service_id", "mcp_service", "id") is not None
    # (c) composite FK guaranteeing tool.service == toolset.service.
    assert _composite_fk(
        mcp_tool,
        ["toolset_id", "service_id"],
        [("toolset", "id"), ("toolset", "service_id")],
    ) is not None
    assert _check_constraint(mcp_tool, ["source", "manual", "synced"]) is not None


# --------------------------------------------------------------------------
# Live tests (PostgreSQL + MySQL).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toolset_states_persist_complete_mcp_tool_schema(dialect: str) -> None:
    """Candidate, active, retired and rejected toolsets save full MCP Schemas."""
    db = await _provision(dialect)
    try:
        _run_alembic_upgrade(db.url)
        engine = create_async_engine(db.url)
        try:
            toolset = _table("toolset")
            mcp_tool = _table("mcp_tool")
            async with engine.begin() as conn:
                team_id = await _ensure_team(conn)
                service_id = await _insert_service(conn, team_id, "svc_schema")
                revision_id = await _insert_revision(conn, service_id)

            for index, state in enumerate(_TOOLSET_STATES, start=1):
                now = datetime.now(UTC)
                activated_at = now if state == "active" else None
                retired_at = now if state == "retired" else None
                source = "synced" if state in ("active", "retired") else "manual"
                async with engine.begin() as conn:
                    toolset_id = await _insert_toolset(
                        conn,
                        service_id,
                        state=state,
                        version_no=index,
                        config_revision_id=revision_id,
                        server_capabilities={"tools": {"listChanged": True}, "logging": {}},
                        server_info={"name": "demo", "version": "1.0.0", "locale": "zh-CN"},
                        instructions="This server 提供搜索能力。",
                        validation_report={
                            "errors": [],
                            "warnings": [{"code": "DEPRECATED", "pointer": "/tools/0"}],
                        },
                        activated_at=activated_at,
                        retired_at=retired_at,
                        tool_count=1,
                    )
                    await _insert_tool(
                        conn,
                        toolset_id,
                        service_id,
                        name=f"tool_{index}",
                        title="Search 工具",
                        description="全文搜索，返回结构化结果。",
                        input_schema=_SAMPLE_INPUT_SCHEMA,
                        output_schema=_SAMPLE_OUTPUT_SCHEMA,
                        annotations=_SAMPLE_ANNOTATIONS,
                        execution=_SAMPLE_EXECUTION,
                        icons=_SAMPLE_ICONS,
                        meta=_SAMPLE_META,
                        raw_definition=_SAMPLE_RAW_DEFINITION,
                        definition_digest="a" * 64,
                        source=source,
                        http_binding=_SAMPLE_HTTP_BINDING,
                        enabled=None,  # exercise the DB default (true)
                    )
                    toolset_row = (
                        await conn.execute(
                            select(toolset).where(toolset.c.id == toolset_id)
                        )
                    ).mappings().one()
                    tool_row = (
                        await conn.execute(
                            select(mcp_tool).where(mcp_tool.c.toolset_id == toolset_id)
                        )
                    ).mappings().one()

                assert toolset_row["state"] == state
                assert toolset_row["version_no"] == index
                assert toolset_row["service_id"] == service_id
                assert toolset_row["config_revision_id"] == revision_id
                assert toolset_row["source_kind"] == "manual"
                assert toolset_row["source_digest"] == "d" * 64
                assert toolset_row["mcp_protocol_version"] == "2025-11-25"
                assert toolset_row["json_schema_dialect"] == (
                    "https://json-schema.org/draft/2020-12/schema"
                )
                assert toolset_row["server_capabilities"] == {
                    "tools": {"listChanged": True},
                    "logging": {},
                }
                assert toolset_row["server_info"] == {
                    "name": "demo",
                    "version": "1.0.0",
                    "locale": "zh-CN",
                }
                assert toolset_row["instructions"] == "This server 提供搜索能力。"
                assert toolset_row["validation_report"] == {
                    "errors": [],
                    "warnings": [{"code": "DEPRECATED", "pointer": "/tools/0"}],
                }
                assert toolset_row["tool_count"] == 1
                if state == "active":
                    assert toolset_row["activated_at"] is not None
                if state == "retired":
                    assert toolset_row["retired_at"] is not None

                assert tool_row["toolset_id"] == toolset_id
                assert tool_row["service_id"] == service_id
                assert tool_row["name"] == f"tool_{index}"
                assert tool_row["title"] == "Search 工具"
                assert tool_row["description"] == "全文搜索，返回结构化结果。"
                assert tool_row["input_schema"] == _SAMPLE_INPUT_SCHEMA
                assert tool_row["output_schema"] == _SAMPLE_OUTPUT_SCHEMA
                assert tool_row["annotations"] == _SAMPLE_ANNOTATIONS
                assert tool_row["execution"] == _SAMPLE_EXECUTION
                assert tool_row["icons"] == _SAMPLE_ICONS
                assert tool_row["meta"] == _SAMPLE_META
                assert tool_row["raw_definition"] == _SAMPLE_RAW_DEFINITION
                assert tool_row["definition_digest"] == "a" * 64
                assert tool_row["source"] == source
                assert tool_row["http_binding"] == _SAMPLE_HTTP_BINDING
                assert tool_row["enabled"] is True
        finally:
            await engine.dispose()
    finally:
        await _drop(db)


@pytest.mark.asyncio
async def test_enum_and_range_check_constraints(dialect: str) -> None:
    db = await _provision(dialect)
    try:
        _run_alembic_upgrade(db.url)
        engine = create_async_engine(db.url)
        try:
            async with engine.begin() as conn:
                team_id = await _ensure_team(conn)
                service_id = await _insert_service(conn, team_id, "svc_checks")
                toolset_id = await _insert_toolset(
                    conn, service_id, state="staging", version_no=1
                )

            with pytest.raises((IntegrityError, OperationalError)):
                async with engine.begin() as conn:
                    await _insert_toolset(conn, service_id, state="bogus", version_no=2)

            with pytest.raises((IntegrityError, OperationalError)):
                async with engine.begin() as conn:
                    await _insert_toolset(
                        conn, service_id, state="staging", version_no=3, source_kind="bogus"
                    )

            with pytest.raises((IntegrityError, OperationalError)):
                async with engine.begin() as conn:
                    await _insert_toolset(
                        conn, service_id, state="staging", version_no=4, tool_count=-1
                    )

            with pytest.raises((IntegrityError, OperationalError)):
                async with engine.begin() as conn:
                    await _insert_tool(
                        conn, toolset_id, service_id, name="bad_source", source="bogus"
                    )
        finally:
            await engine.dispose()
    finally:
        await _drop(db)


@pytest.mark.asyncio
async def test_foreign_key_constraints(dialect: str) -> None:
    db = await _provision(dialect)
    try:
        _run_alembic_upgrade(db.url)
        engine = create_async_engine(db.url)
        try:
            async with engine.begin() as conn:
                team_id = await _ensure_team(conn)
                service_a = await _insert_service(conn, team_id, "svc_fk_a")
                service_b = await _insert_service(conn, team_id, "svc_fk_b")
                toolset_a = await _insert_toolset(
                    conn, service_a, state="staging", version_no=1
                )

            # toolset.service_id FK -> mcp_service.id
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await _insert_toolset(conn, uuid.uuid4(), state="staging", version_no=101)

            # toolset.config_revision_id FK -> service_config_revision.id
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await _insert_toolset(
                        conn,
                        service_a,
                        state="staging",
                        version_no=102,
                        config_revision_id=uuid.uuid4(),
                    )

            # mcp_tool.toolset_id FK -> toolset.id
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await _insert_tool(conn, uuid.uuid4(), service_a, name="orphan_tool")

            # composite FK (toolset_id, service_id) -> toolset(id, service_id):
            # the tool's service must equal its toolset's service.
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await _insert_tool(conn, toolset_a, service_b, name="cross_service")
        finally:
            await engine.dispose()
    finally:
        await _drop(db)


@pytest.mark.asyncio
async def test_toolset_delete_cascades_and_uniques(dialect: str) -> None:
    db = await _provision(dialect)
    try:
        _run_alembic_upgrade(db.url)
        engine = create_async_engine(db.url)
        try:
            toolset = _table("toolset")
            mcp_tool = _table("mcp_tool")
            async with engine.begin() as conn:
                team_id = await _ensure_team(conn)
                service_id = await _insert_service(conn, team_id, "svc_uniques")
                toolset_a = await _insert_toolset(
                    conn, service_id, state="staging", version_no=1
                )
                await _insert_tool(conn, toolset_a, service_id, name="cascade_me")

            # ON DELETE CASCADE: deleting a (staging) toolset drops its tools.
            async with engine.begin() as conn:
                await conn.execute(toolset.delete().where(toolset.c.id == toolset_a))
                remaining = (
                    await conn.execute(
                        select(func.count())
                        .select_from(mcp_tool)
                        .where(mcp_tool.c.toolset_id == toolset_a)
                    )
                ).scalar()
            assert remaining == 0

            # UNIQUE (service_id, version_no): old version_no=1 is gone, so a
            # fresh version_no=1 is fine, but a second one collides.
            async with engine.begin() as conn:
                await _insert_toolset(conn, service_id, state="staging", version_no=1)
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await _insert_toolset(conn, service_id, state="staging", version_no=1)

            # UNIQUE (toolset_id, name).
            async with engine.begin() as conn:
                toolset_b = await _insert_toolset(
                    conn, service_id, state="staging", version_no=2
                )
            async with engine.begin() as conn:
                await _insert_tool(conn, toolset_b, service_id, name="dup")
            with pytest.raises(IntegrityError):
                async with engine.begin() as conn:
                    await _insert_tool(conn, toolset_b, service_id, name="dup")
        finally:
            await engine.dispose()
    finally:
        await _drop(db)
