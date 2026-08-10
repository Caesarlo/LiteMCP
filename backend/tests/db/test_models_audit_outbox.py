"""M1-MODEL-008 contract tests: ``audit_event`` and ``outbox`` models.

Feature behavior (feature_list.json M1-MODEL-008): business changes and their
corresponding audit events are reliably recorded in the same transaction.

- ``audit_event`` is append-only business evidence, not application logs
  (docs/architecture/01-data-model.md §5.14). It carries no §3.2 audit columns
  because the table *is* the audit record.
- ``outbox`` is the controller-adjudicated transactional delivery queue whose
  worker-task dedup key is ``(service_id, requested_generation,
  operation_kind)`` (03-service-crud.md §6.3). On both dialects NULL != NULL,
  so rows where any of the three dedup columns is NULL are NOT deduped; only
  rows where all three are non-NULL are unique.

Two kinds of tests:

1. Offline structural contract: both tables registered on ``Base.metadata``
   with the documented portable column kinds, ENUM code sets, indexes, the
   outbox UNIQUE dedup constraint, and the absence of §3.2 audit columns.
2. Live checks parametrized over real PostgreSQL and real MySQL: each test
   provisions a uniquely-named empty database, runs ``upgrade head``, and
   drives an ``AsyncSessionFactory`` bound to the dedicated URL. The core
   transactional-consistency test commits an audit event and an outbox row in
   the same transaction as a constraint-violating row and asserts the whole
   transaction rolls back.

The ``metadata`` column is referenced through ``Table.c`` (Core) rather than
ORM attributes: ``metadata`` is a reserved attribute name under the Declarative
API, so the mapped Python attribute name is an implementation detail this
contract must not depend on.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import aiomysql
import asyncpg
import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, OperationalError

from litemcp.core.config import get_settings
from litemcp.db.models import AuditEvent, Base, Outbox
from litemcp.db.session import get_session_factory
from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, UTC_TS

BACKEND_DIR = Path(__file__).resolve().parents[2]

PG_PORT = int(os.environ.get("POSTGRES_PORT", "5433"))
PG_USER = "litemcp"
PG_PASSWORD = "litemcp"
PG_ADMIN_DB = "litemcp"
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3307"))
MYSQL_ROOT_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD", "litemcp-root")
MYSQL_APP_USER = "litemcp"
MYSQL_APP_PASSWORD = "litemcp"

AUDIT_TABLE = AuditEvent.__table__
OUTBOX_TABLE = Outbox.__table__

_REQUIRED_ENV = (
    "LITEMCP_DATABASE_URL",
    "LITEMCP_REDIS_URL",
    "LITEMCP_ENCRYPTION_KEYS",
)


# ---------------------------------------------------------------------------
# Value / datetime helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """UTC now truncated to whole seconds (MySQL DATETIME is second-granular)."""
    return datetime.now(UTC).replace(microsecond=0)


def _normalize_dt(value: datetime | None) -> datetime | None:
    """Normalize a datetime for cross-dialect comparison: naive UTC, whole seconds."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0)


def _audit_event_values(**overrides: object) -> dict[str, object]:
    """A full, valid row for ``audit_event`` (all 17 documented columns)."""
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "occurred_at": _utcnow(),
        "request_id": "req-audit-001",
        "actor_type": "user",
        "actor_id": "user-123",
        "action": "service.update",
        "resource_type": "service",
        "resource_id": "svc-456",
        "service_id": uuid.uuid4(),
        "result": "success",
        "reason_code": "ok",
        "source_ip": "10.0.0.7",
        "user_agent": "pytest-agent/1.0",
        "changes": {"display_name": {"changed": True}},
        "metadata": {"origin": "contract-test"},
        "previous_event_hash": None,
        "event_hash": "abcdef0123456789abcdef0123456789",
    }
    values.update(overrides)
    return values


def _outbox_values(**overrides: object) -> dict[str, object]:
    """A full, valid row for ``outbox`` (all 12 documented columns)."""
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "event_type": "service.published",
        "service_id": uuid.uuid4(),
        "requested_generation": 3,
        "operation_kind": "sync",
        "payload": {"service_id": "svc-456"},
        "status": "pending",
        "attempt_count": 0,
        "next_attempt_at": None,
        "last_error": None,
        "created_at": _utcnow(),
        "processed_at": None,
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# Dedicated-database provisioning (PostgreSQL + MySQL)
# ---------------------------------------------------------------------------


async def _run_upgrade(database_url: str) -> None:
    config = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(alembic_command.upgrade, config, "head")


def _postgres_url(db_name: str) -> str:
    return str(
        URL.create(
            "postgresql+asyncpg",
            username=PG_USER,
            password=PG_PASSWORD,
            host="localhost",
            port=PG_PORT,
            database=db_name,
        ).render_as_string(hide_password=False)
    )


def _mysql_url(db_name: str) -> str:
    return str(
        URL.create(
            "mysql+aiomysql",
            username=MYSQL_APP_USER,
            password=MYSQL_APP_PASSWORD,
            host="localhost",
            port=MYSQL_PORT,
            database=db_name,
        ).render_as_string(hide_password=False)
    )


async def _provision_postgres() -> tuple[str, str]:
    db_name = f"litemcp_audit_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(
        host="localhost",
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_ADMIN_DB,
    )
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()
    database_url = _postgres_url(db_name)
    await _run_upgrade(database_url)
    return database_url, db_name


async def _provision_mysql() -> tuple[str, str]:
    db_name = f"litemcp_audit_{uuid.uuid4().hex[:12]}"
    admin = await aiomysql.connect(
        host="localhost",
        port=MYSQL_PORT,
        user="root",
        password=MYSQL_ROOT_PASSWORD,
        autocommit=True,
    )
    try:
        cursor = await admin.cursor()
        await cursor.execute(f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4")
        # MySQL 8 refuses to auto-create a user via GRANT; the compose profile
        # already creates `litemcp`@`%`. The `%%` is escaped to a single `%` by
        # the percent-formatting aiomysql applies when args are supplied.
        await cursor.execute(
            f"GRANT ALL PRIVILEGES ON `{db_name}`.* "
            f"TO '{MYSQL_APP_USER}'@'%%'",
            (),
        )
        await cursor.close()
    finally:
        admin.close()
    database_url = _mysql_url(db_name)
    await _run_upgrade(database_url)
    return database_url, db_name


async def _drop_postgres(db_name: str) -> None:
    admin = await asyncpg.connect(
        host="localhost",
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        database=PG_ADMIN_DB,
    )
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _drop_mysql(db_name: str) -> None:
    admin = await aiomysql.connect(
        host="localhost",
        port=MYSQL_PORT,
        user="root",
        password=MYSQL_ROOT_PASSWORD,
        autocommit=True,
    )
    try:
        cursor = await admin.cursor()
        await cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
        await cursor.close()
    finally:
        admin.close()


@pytest_asyncio.fixture
async def live_factory(request):
    """Migrate a fresh dedicated database and yield a session factory for it.

    The process-wide settings singleton is repointed at the dedicated URL so
    ``get_session_factory()`` builds an ``AsyncSessionFactory`` bound to it.
    The database is dropped and the environment restored in teardown.
    """
    if request.param == "postgres":
        database_url, db_name = await _provision_postgres()
    else:
        database_url, db_name = await _provision_mysql()

    saved_env = {name: os.environ.get(name) for name in _REQUIRED_ENV}
    factory = None
    try:
        os.environ["LITEMCP_DATABASE_URL"] = database_url
        os.environ["LITEMCP_REDIS_URL"] = "redis://localhost:6379/0"
        os.environ["LITEMCP_ENCRYPTION_KEYS"] = "dev-test-key"
        get_settings.cache_clear()
        factory = get_session_factory()
        yield factory
    finally:
        get_settings.cache_clear()
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if factory is not None:
            await factory.dispose()
        if request.param == "postgres":
            await _drop_postgres(db_name)
        else:
            await _drop_mysql(db_name)


# ---------------------------------------------------------------------------
# Offline structural contract
# ---------------------------------------------------------------------------


def test_audit_event_and_outbox_registered_on_base_metadata() -> None:
    assert "audit_event" in Base.metadata.tables
    assert "outbox" in Base.metadata.tables


@pytest.mark.parametrize(
    ("table_name", "column_name", "expected_type", "nullable", "length"),
    [
        # audit_event (§5.14) — 17 documented columns
        ("audit_event", "id", ID, False, None),
        ("audit_event", "occurred_at", UTC_TS, False, None),
        ("audit_event", "request_id", sa.String, False, 128),
        ("audit_event", "actor_type", ENUM_CODE, False, None),
        ("audit_event", "actor_id", sa.String, True, 128),
        ("audit_event", "action", sa.String, False, 64),
        ("audit_event", "resource_type", sa.String, False, 32),
        ("audit_event", "resource_id", sa.String, True, 128),
        ("audit_event", "service_id", ID, True, None),
        ("audit_event", "result", ENUM_CODE, False, None),
        ("audit_event", "reason_code", sa.String, True, 64),
        ("audit_event", "source_ip", sa.String, True, 64),
        ("audit_event", "user_agent", sa.String, True, 1024),
        ("audit_event", "changes", JSON_DOC, True, None),
        ("audit_event", "metadata", JSON_DOC, True, None),
        ("audit_event", "previous_event_hash", sa.String, True, 64),
        ("audit_event", "event_hash", sa.String, True, 64),
        # outbox (controller-adjudicated) — 12 documented columns
        ("outbox", "id", ID, False, None),
        ("outbox", "event_type", sa.String, False, 64),
        ("outbox", "service_id", ID, True, None),
        ("outbox", "requested_generation", sa.BigInteger, True, None),
        ("outbox", "operation_kind", sa.String, True, 64),
        ("outbox", "payload", JSON_DOC, True, None),
        ("outbox", "status", ENUM_CODE, False, None),
        ("outbox", "attempt_count", sa.Integer, False, None),
        ("outbox", "next_attempt_at", UTC_TS, True, None),
        ("outbox", "last_error", sa.String, True, 2048),
        ("outbox", "created_at", UTC_TS, False, None),
        ("outbox", "processed_at", UTC_TS, True, None),
    ],
)
def test_column_contract(
    table_name: str,
    column_name: str,
    expected_type: object,
    nullable: bool,
    length: int | None,
) -> None:
    table = Base.metadata.tables[table_name]
    column = table.columns[column_name]
    assert isinstance(column.type, expected_type)
    assert column.nullable is nullable
    if expected_type is sa.Integer:
        # BigInteger subclasses Integer; the portable contract uses plain
        # Integer for attempt_count, so pin the exact class.
        assert type(column.type) is sa.Integer
    elif expected_type is sa.String:
        assert column.type.length == length


def test_audit_event_actor_type_and_result_enum_codes() -> None:
    table = Base.metadata.tables["audit_event"]
    actor_type = table.columns["actor_type"].type
    result = table.columns["result"].type
    assert isinstance(actor_type, ENUM_CODE)
    assert isinstance(result, ENUM_CODE)
    assert set(actor_type.codes) == {"user", "api_key", "system", "anonymous"}
    assert set(result.codes) == {"success", "denied", "failed"}


def test_outbox_status_enum_codes() -> None:
    table = Base.metadata.tables["outbox"]
    status = table.columns["status"].type
    assert isinstance(status, ENUM_CODE)
    assert set(status.codes) == {"pending", "in_flight", "done", "failed"}


def test_outbox_unique_dedup_constraint() -> None:
    table = Base.metadata.tables["outbox"]
    target = {"service_id", "requested_generation", "operation_kind"}
    found = False
    for constraint in table.constraints:
        if isinstance(constraint, sa.UniqueConstraint):
            cols = {column.name for column in constraint.columns}
            if cols == target:
                found = True
                break
    assert found, (
        "outbox is missing UNIQUE(service_id, requested_generation, operation_kind)"
    )


def test_audit_event_indexes() -> None:
    table = Base.metadata.tables["audit_event"]
    index_column_sets = {
        tuple(column.name for column in index.columns) for index in table.indexes
    }
    assert ("occurred_at",) in index_column_sets
    assert ("service_id", "occurred_at") in index_column_sets
    assert ("actor_type", "actor_id", "occurred_at") in index_column_sets


@pytest.mark.parametrize("table_name", ["audit_event", "outbox"])
def test_no_section_3_2_audit_columns(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    present = set(table.columns.keys())
    assert not (present & {"created_by", "updated_at", "updated_by", "row_version"})


# ---------------------------------------------------------------------------
# Live checks (PostgreSQL + MySQL)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_audit_event_full_field_roundtrip(live_factory) -> None:
    values = _audit_event_values()
    async with live_factory.session() as session:
        await session.execute(sa.insert(AUDIT_TABLE).values(values))
        await session.commit()
        row = (
            await session.execute(
                sa.select(AUDIT_TABLE).where(AUDIT_TABLE.c.id == values["id"])
            )
        ).one()
        mapping = row._mapping
        assert mapping[AUDIT_TABLE.c.id] == values["id"]
        assert _normalize_dt(mapping[AUDIT_TABLE.c.occurred_at]) == _normalize_dt(
            values["occurred_at"]
        )
        assert mapping[AUDIT_TABLE.c.request_id] == values["request_id"]
        assert mapping[AUDIT_TABLE.c.actor_type] == values["actor_type"]
        assert mapping[AUDIT_TABLE.c.actor_id] == values["actor_id"]
        assert mapping[AUDIT_TABLE.c.action] == values["action"]
        assert mapping[AUDIT_TABLE.c.resource_type] == values["resource_type"]
        assert mapping[AUDIT_TABLE.c.resource_id] == values["resource_id"]
        assert mapping[AUDIT_TABLE.c.service_id] == values["service_id"]
        assert mapping[AUDIT_TABLE.c.result] == values["result"]
        assert mapping[AUDIT_TABLE.c.reason_code] == values["reason_code"]
        assert mapping[AUDIT_TABLE.c.source_ip] == values["source_ip"]
        assert mapping[AUDIT_TABLE.c.user_agent] == values["user_agent"]
        assert mapping[AUDIT_TABLE.c.changes] == values["changes"]
        assert mapping[AUDIT_TABLE.c.metadata] == values["metadata"]
        assert mapping[AUDIT_TABLE.c.previous_event_hash] == values[
            "previous_event_hash"
        ]
        assert mapping[AUDIT_TABLE.c.event_hash] == values["event_hash"]


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_audit_event_duplicate_id_rejected(live_factory) -> None:
    event_id = uuid.uuid4()
    async with live_factory.session() as session:
        await session.execute(
            sa.insert(AUDIT_TABLE).values(_audit_event_values(id=event_id))
        )
        await session.commit()
        with pytest.raises(IntegrityError):
            await session.execute(
                sa.insert(AUDIT_TABLE).values(_audit_event_values(id=event_id))
            )
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_audit_event_null_action_rejected(live_factory) -> None:
    async with live_factory.session() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                sa.insert(AUDIT_TABLE).values(_audit_event_values(action=None))
            )
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_audit_event_disallowed_actor_type_rejected(live_factory) -> None:
    async with live_factory.session() as session:
        with pytest.raises((IntegrityError, OperationalError)):
            await session.execute(
                sa.insert(AUDIT_TABLE).values(_audit_event_values(actor_type="root"))
            )
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_audit_event_disallowed_result_rejected(live_factory) -> None:
    async with live_factory.session() as session:
        with pytest.raises((IntegrityError, OperationalError)):
            await session.execute(
                sa.insert(AUDIT_TABLE).values(_audit_event_values(result="maybe"))
            )
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_outbox_full_field_roundtrip(live_factory) -> None:
    values = _outbox_values()
    async with live_factory.session() as session:
        await session.execute(sa.insert(OUTBOX_TABLE).values(values))
        await session.commit()
        row = (
            await session.execute(
                sa.select(OUTBOX_TABLE).where(OUTBOX_TABLE.c.id == values["id"])
            )
        ).one()
        mapping = row._mapping
        assert mapping[OUTBOX_TABLE.c.id] == values["id"]
        assert mapping[OUTBOX_TABLE.c.event_type] == values["event_type"]
        assert mapping[OUTBOX_TABLE.c.service_id] == values["service_id"]
        assert mapping[OUTBOX_TABLE.c.requested_generation] == values[
            "requested_generation"
        ]
        assert mapping[OUTBOX_TABLE.c.operation_kind] == values["operation_kind"]
        assert mapping[OUTBOX_TABLE.c.payload] == values["payload"]
        assert mapping[OUTBOX_TABLE.c.status] == values["status"]
        assert mapping[OUTBOX_TABLE.c.attempt_count] == values["attempt_count"]
        assert _normalize_dt(mapping[OUTBOX_TABLE.c.next_attempt_at]) == _normalize_dt(
            values["next_attempt_at"]
        )
        assert mapping[OUTBOX_TABLE.c.last_error] == values["last_error"]
        assert _normalize_dt(mapping[OUTBOX_TABLE.c.created_at]) == _normalize_dt(
            values["created_at"]
        )
        assert _normalize_dt(mapping[OUTBOX_TABLE.c.processed_at]) == _normalize_dt(
            values["processed_at"]
        )


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_outbox_status_defaults_to_pending(live_factory) -> None:
    values = _outbox_values()
    values.pop("status")
    async with live_factory.session() as session:
        await session.execute(sa.insert(OUTBOX_TABLE).values(values))
        await session.commit()
        status = (
            await session.execute(
                sa.select(OUTBOX_TABLE.c.status).where(
                    OUTBOX_TABLE.c.id == values["id"]
                )
            )
        ).scalar_one()
        assert status == "pending"


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_outbox_negative_attempt_count_rejected(live_factory) -> None:
    async with live_factory.session() as session:
        with pytest.raises((IntegrityError, OperationalError)):
            await session.execute(
                sa.insert(OUTBOX_TABLE).values(_outbox_values(attempt_count=-1))
            )
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_outbox_disallowed_status_rejected(live_factory) -> None:
    async with live_factory.session() as session:
        with pytest.raises((IntegrityError, OperationalError)):
            await session.execute(
                sa.insert(OUTBOX_TABLE).values(_outbox_values(status="bogus"))
            )
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_outbox_duplicate_non_null_triple_rejected(live_factory) -> None:
    service_id = uuid.uuid4()
    first = _outbox_values(
        service_id=service_id, requested_generation=5, operation_kind="sync"
    )
    duplicate = _outbox_values(
        service_id=service_id, requested_generation=5, operation_kind="sync"
    )
    async with live_factory.session() as session:
        await session.execute(sa.insert(OUTBOX_TABLE).values(first))
        await session.commit()
        with pytest.raises(IntegrityError):
            await session.execute(sa.insert(OUTBOX_TABLE).values(duplicate))
            await session.commit()
        await session.rollback()


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_outbox_all_null_triples_are_not_deduped(live_factory) -> None:
    async with live_factory.session() as session:
        await session.execute(
            sa.insert(OUTBOX_TABLE).values(
                _outbox_values(
                    service_id=None, requested_generation=None, operation_kind=None
                )
            )
        )
        await session.execute(
            sa.insert(OUTBOX_TABLE).values(
                _outbox_values(
                    service_id=None, requested_generation=None, operation_kind=None
                )
            )
        )
        await session.commit()
        count = (
            await session.execute(sa.select(sa.func.count()).select_from(OUTBOX_TABLE))
        ).scalar_one()
        assert count == 2


@pytest.mark.parametrize("live_factory", ["postgres", "mysql"], indirect=True)
@pytest.mark.asyncio
async def test_audit_and_outbox_commit_atomically(live_factory) -> None:
    """The feature's core: business change + audit event in one transaction.

    An audit event and an outbox row are inserted in the same transaction as a
    row that violates the outbox UNIQUE dedup constraint. The commit must fail
    and the whole transaction must roll back — neither the audit event nor the
    outbox row survives.
    """
    service_id = uuid.uuid4()
    audit = _audit_event_values()
    outbox_ok = _outbox_values(
        service_id=service_id, requested_generation=9, operation_kind="sync"
    )
    outbox_dup = _outbox_values(
        service_id=service_id, requested_generation=9, operation_kind="sync"
    )

    async with live_factory.session() as session:
        with pytest.raises((IntegrityError, OperationalError)):
            await session.execute(sa.insert(AUDIT_TABLE).values(audit))
            await session.execute(sa.insert(OUTBOX_TABLE).values(outbox_ok))
            # Same non-NULL dedup triple: UNIQUE violation inside the transaction.
            await session.execute(sa.insert(OUTBOX_TABLE).values(outbox_dup))
            await session.commit()
        await session.rollback()

    async with live_factory.session() as session:
        audit_count = (
            await session.execute(sa.select(sa.func.count()).select_from(AUDIT_TABLE))
        ).scalar_one()
        outbox_count = (
            await session.execute(sa.select(sa.func.count()).select_from(OUTBOX_TABLE))
        ).scalar_one()
        assert audit_count == 0
        assert outbox_count == 0
