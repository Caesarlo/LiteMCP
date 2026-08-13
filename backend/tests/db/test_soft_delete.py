"""Cross-dialect service soft-delete contract (M1-DELETE-001).

The service row is a tombstone, not a cascade delete.  These tests deliberately
exercise the database contract directly: deleted services disappear from the
default/active predicates, names can be reused while the tombstone is retained,
restore is retention-bound and conflict-checked, and immutable history keeps its
service/team/audit references.

The suite provisions a fresh PostgreSQL and MySQL database for every test and
runs the Alembic head migration.  A missing table/column or missing constraint is
therefore a RED contract failure rather than a skipped test.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    Numeric,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

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


def _table(name: str) -> Table:
    try:
        return Base.metadata.tables[name]
    except KeyError:
        raise AssertionError(f"required table {name!r} is not registered") from None


def test_soft_delete_scope_has_portable_consistency_check() -> None:
    """The LIVE/deleted scope invariant must be enforced by both databases.

    A partial unique index is not portable.  The documented replacement is a
    ``uniqueness_scope`` column plus a table CHECK that prevents a tombstone
    from claiming LIVE and prevents an active row from using a DELETED scope.
    """
    table = _table("mcp_service")
    checks = [
        str(constraint.sqltext).lower()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]
    joined_checks = " ".join(checks)
    assert all(token in joined_checks for token in ("deleted_at", "uniqueness_scope", "live")), (
        "mcp_service must enforce deleted_at/uniqueness_scope consistency"
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
        except Exception as exc:  # noqa: BLE001  # surfaced below
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
    name = _db_name("litemcp_soft_delete")
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


def _value_for_column(column, now: datetime, actor: str) -> object:
    """Safe filler for required columns outside the rows pinned by this contract."""
    typ = column.type
    name = column.name
    if isinstance(typ, ID):
        return uuid.uuid4()
    if isinstance(typ, (UTC_TS, DateTime)):
        return now
    if isinstance(typ, (JSON_DOC, JSON)):
        return {}
    if isinstance(typ, (LONG_TEXT, Text)):
        return "contract"
    if isinstance(typ, (Integer, BigInteger)):
        return 1
    if isinstance(typ, Numeric):
        return 1
    if isinstance(typ, Boolean):
        return True
    if isinstance(typ, LargeBinary):
        return b"contract"
    if name.endswith("_at"):
        return now
    if name.endswith("_by") or name in {"created_by", "updated_by"}:
        return actor
    return "contract"


def _row(table: Table, now: datetime, actor: str, **overrides: object) -> dict[str, object]:
    values: dict[str, object] = {}
    for column in table.columns:
        if column.name in overrides or column.nullable:
            continue
        if column.default is not None or column.server_default is not None:
            continue
        values[column.name] = _value_for_column(column, now, actor)
    values.update(overrides)
    return values


async def _seed_history(engine: AsyncEngine) -> dict[str, object]:
    now = datetime.now(UTC).replace(microsecond=123000)
    actor = f"actor-{uuid.uuid4().hex[:10]}"
    team_id = uuid.uuid4()
    service_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    toolset_id = uuid.uuid4()
    tool_id = uuid.uuid4()
    team = _table("team")
    service = _table("mcp_service")
    revision = _table("service_config_revision")
    toolset = _table("toolset")
    tool = _table("mcp_tool")
    audit = _table("audit_event")

    team_values = _row(
        team,
        now,
        actor,
        id=team_id,
        key=f"team-{team_id.hex[:8]}",
        key_normalized=f"team-{team_id.hex[:8]}",
        name="History Team",
        status="active",
    )
    service_values = _row(
        service,
        now,
        actor,
        id=service_id,
        namespace_key="default",
        team_id=team_id,
        type="http_api",
        name="Soft Delete Service",
        name_normalized="soft-delete-service",
        uniqueness_scope="LIVE",
        tags=[],
        desired_status="enabled",
        generation=1,
        observed_generation=0,
        runtime_status="ready",
        agent_auth_mode="none",
        created_at=now,
        created_by=actor,
        updated_at=now,
        updated_by=actor,
        row_version=1,
    )
    revision_values = _row(
        revision,
        now,
        actor,
        id=revision_id,
        service_id=service_id,
        generation=1,
        schema_version=1,
        config_kind="http_api",
        public_config={"base_url": "https://example.test"},
        source_mode="manual",
        config_digest="a" * 64,
        state="active",
        created_at=now,
        created_by=actor,
    )
    toolset_values = _row(
        toolset,
        now,
        actor,
        id=toolset_id,
        service_id=service_id,
        config_revision_id=revision_id,
        version_no=1,
        source_kind="manual",
        source_digest="b" * 64,
        mcp_protocol_version="2025-11-25",
        json_schema_dialect="https://json-schema.org/draft/2020-12/schema",
        state="active",
        tool_count=1,
        activated_at=now,
    )
    tool_values = _row(
        tool,
        now,
        actor,
        id=tool_id,
        toolset_id=toolset_id,
        service_id=service_id,
        name="historical_tool",
        input_schema={"type": "object"},
        raw_definition={"name": "historical_tool", "inputSchema": {"type": "object"}},
        definition_digest="c" * 64,
        source="manual",
        enabled=True,
    )
    audit_values = _row(
        audit,
        now,
        actor,
        id=uuid.uuid4(),
        occurred_at=now,
        request_id=f"request-{uuid.uuid4().hex[:8]}",
        actor_type="user",
        actor_id=actor,
        action="service.deleted",
        resource_type="mcp_service",
        resource_id=str(service_id),
        service_id=service_id,
        result="success",
        changes={"deleted_at": {"changed": True}},
        metadata={"contract": "soft-delete"},
    )

    async with engine.begin() as conn:
        await conn.execute(insert(team).values(team_values))
        await conn.execute(insert(service).values(service_values))
        await conn.execute(insert(revision).values(revision_values))
        await conn.execute(insert(toolset).values(toolset_values))
        await conn.execute(insert(tool).values(tool_values))
        await conn.execute(insert(audit).values(audit_values))
        await conn.execute(
            update(service)
            .where(service.c.id == service_id)
            .values(active_config_revision_id=revision_id, active_toolset_id=toolset_id)
        )

    return {
        "now": now,
        "actor": actor,
        "team_id": team_id,
        "service_id": service_id,
        "revision_id": revision_id,
        "toolset_id": toolset_id,
        "tool_id": tool_id,
    }


async def _soft_delete(engine: AsyncEngine, ids: dict[str, object]) -> None:
    service = _table("mcp_service")
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            update(service)
            .where(service.c.id == ids["service_id"])
            .values(
                desired_status="disabled",
                uniqueness_scope=f"DELETED:{ids['service_id']}",
                deleted_at=now,
                deleted_by=ids["actor"],
                updated_at=now,
                updated_by=ids["actor"],
                row_version=2,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_soft_deleted_service_is_hidden_from_default_and_active_access(live_db) -> None:
    engine, _ = live_db
    ids = await _seed_history(engine)
    await _soft_delete(engine, ids)
    service = _table("mcp_service")

    async with engine.connect() as conn:
        visible = (
            await conn.execute(
                select(service.c.id).where(
                    service.c.deleted_at.is_(None), service.c.id == ids["service_id"]
                )
            )
        ).all()
        active = (
            await conn.execute(
                select(service.c.id).where(
                    service.c.deleted_at.is_(None),
                    service.c.desired_status == "enabled",
                    service.c.id == ids["service_id"],
                )
            )
        ).all()
    assert visible == []
    assert active == []


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_scope_invariant_rejects_inconsistent_rows(live_db) -> None:
    """Both dialects reject LIVE tombstones and deleted active rows."""
    engine, _ = live_db
    ids = await _seed_history(engine)
    service = _table("mcp_service")
    for values in (
        {"uniqueness_scope": f"DELETED:{ids['service_id']}", "deleted_at": None},
        {"uniqueness_scope": "LIVE", "deleted_at": datetime.now(UTC)},
    ):
        with pytest.raises((IntegrityError, OperationalError)):
            async with engine.begin() as conn:
                await conn.execute(
                    update(service)
                    .where(service.c.id == ids["service_id"])
                    .values(**values)
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_restore_within_retention_clears_tombstone_but_remains_disabled(live_db) -> None:
    engine, _ = live_db
    ids = await _seed_history(engine)
    await _soft_delete(engine, ids)
    service = _table("mcp_service")
    restored_at = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            update(service)
            .where(service.c.id == ids["service_id"], service.c.deleted_at.is_not(None))
            .values(
                uniqueness_scope="LIVE",
                deleted_at=None,
                deleted_by=None,
                desired_status="disabled",
                updated_at=restored_at,
                updated_by=ids["actor"],
                row_version=3,
            )
        )
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    service.c.uniqueness_scope,
                    service.c.deleted_at,
                    service.c.deleted_by,
                    service.c.desired_status,
                ).where(service.c.id == ids["service_id"])
            )
        ).one()
    assert row.uniqueness_scope == "LIVE"
    assert row.deleted_at is None and row.deleted_by is None
    assert row.desired_status == "disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_deleted_name_can_be_reused_but_restore_conflict_is_rejected(live_db) -> None:
    engine, _ = live_db
    ids = await _seed_history(engine)
    await _soft_delete(engine, ids)
    service = _table("mcp_service")
    replacement_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        base = await conn.execute(
            select(service).where(service.c.id == ids["service_id"])
        )
        original = dict(base.mappings().one())
        original.update(
            id=replacement_id,
            active_config_revision_id=None,
            active_toolset_id=None,
            uniqueness_scope="LIVE",
            deleted_at=None,
            deleted_by=None,
            desired_status="disabled",
            created_at=now,
            updated_at=now,
            created_by=ids["actor"],
            updated_by=ids["actor"],
            row_version=1,
        )
        await conn.execute(insert(service).values(original))

    with pytest.raises((IntegrityError, OperationalError)):
        async with engine.begin() as conn:
            await conn.execute(
                update(service)
                .where(service.c.id == ids["service_id"])
                .values(uniqueness_scope="LIVE", deleted_at=None, deleted_by=None)
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("live_db", ["postgres", "mysql"], indirect=True)
async def test_soft_delete_preserves_revision_toolset_tool_team_and_audit_history(live_db) -> None:
    engine, _ = live_db
    ids = await _seed_history(engine)
    await _soft_delete(engine, ids)
    service = _table("mcp_service")
    revision = _table("service_config_revision")
    toolset = _table("toolset")
    tool = _table("mcp_tool")
    audit = _table("audit_event")
    async with engine.connect() as conn:
        service_row = (
            await conn.execute(
                select(service.c.team_id, service.c.active_config_revision_id, service.c.active_toolset_id)
                .where(service.c.id == ids["service_id"])
            )
        ).one()
        revision_count = (
            await conn.execute(
                select(revision.c.id).where(revision.c.id == ids["revision_id"])
            )
        ).all()
        toolset_count = (
            await conn.execute(select(toolset.c.id).where(toolset.c.id == ids["toolset_id"]))
        ).all()
        tool_count = (
            await conn.execute(select(tool.c.id).where(tool.c.id == ids["tool_id"]))
        ).all()
        audit_count = (
            await conn.execute(
                select(audit.c.id).where(audit.c.service_id == ids["service_id"])
            )
        ).all()
        ownership = (
            await conn.execute(
                select(service.c.id).where(
                    service.c.team_id == ids["team_id"], service.c.id == ids["service_id"]
                )
            )
        ).all()
    assert service_row.team_id == ids["team_id"]
    assert service_row.active_config_revision_id == ids["revision_id"]
    assert service_row.active_toolset_id == ids["toolset_id"]
    assert len(revision_count) == len(toolset_count) == len(tool_count) == len(audit_count) == 1
    assert ownership == [(ids["service_id"],)]
