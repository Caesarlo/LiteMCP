"""Cross-dialect base type contract (M1-DB-002).

Pins the public contract of ``litemcp.db.types`` as required by
docs/architecture/01-data-model.md §3.1 (L36-46) and
docs/architecture/09-verification.md §4.3:

- the same logical types render the documented physical DDL on the REAL
  PostgreSQL and MySQL dialects, with no single-dialect dependency;
- the same values round-trip consistently on BOTH live dialects, including
  the "MySQL read adds UTC semantics" rule for timestamps and DB-level
  rejection of disallowed enum codes (CHECK constraint, no native ENUM).

This suite requires live PostgreSQL and MySQL. A connection failure is a
hard FAIL, never a skip; there is no SQLite fallback for this contract.

The allowed enum codes are supplied to ``ENUM_CODE`` at construction; the
portable CHECK constraint that enforces them at the database level is
declared on the column (SQLAlchemy does not emit a CHECK for non-native
ENUMs, so the contract pins the CHECK explicitly). Disallowed codes must be
rejected by the DATABASE (IntegrityError on PostgreSQL, OperationalError on
MySQL), not by a Python-side value check.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import CheckConstraint, Column, MetaData, Table, select
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable

from litemcp.db.types import CIPHERTEXT, ENUM_CODE, ID, JSON_DOC, LONG_TEXT, UTC_TS

# --------------------------------------------------------------------------
# Dialect engines (both are live requirements of this contract).
# --------------------------------------------------------------------------

_POSTGRES_URL = os.environ.get(
    "LITEMCP_TEST_POSTGRES_URL",
    "postgresql+asyncpg://litemcp:litemcp@localhost:5433/litemcp",
)
_MYSQL_URL = os.environ.get(
    "LITEMCP_TEST_MYSQL_URL",
    "mysql+aiomysql://litemcp:litemcp@localhost:3307/litemcp",
)

_DIALECT_URLS = [("postgres", _POSTGRES_URL), ("mysql", _MYSQL_URL)]
_DIALECT_IDS = ["postgres", "mysql"]

# --------------------------------------------------------------------------
# Shared column model and values.
# --------------------------------------------------------------------------

_ALLOWED_CODES = ("active", "disabled", "locked")


def _enum_check() -> CheckConstraint:
    """Portable CHECK that enumerates the allowed codes on both dialects."""
    codes = ", ".join(repr(code) for code in _ALLOWED_CODES)
    return CheckConstraint(f"code IN ({codes})")


def _build_table(name: str) -> Table:
    metadata = MetaData()
    return Table(
        name,
        metadata,
        Column("id", ID(), primary_key=True),
        Column("ts", UTC_TS(), nullable=False),
        Column("doc", JSON_DOC(), nullable=False),
        Column("blob", CIPHERTEXT(), nullable=False),
        Column("long_text", LONG_TEXT(), nullable=False),
        Column(
            "code",
            ENUM_CODE(*_ALLOWED_CODES),
            _enum_check(),
            nullable=False,
        ),
    )


_WRITTEN_TS = datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
_UID_JSON_DICT = uuid.UUID("0b7f2a1e-9f0b-4c0d-8e2a-111111111111")
_UID_JSON_LIST = uuid.UUID("0b7f2a1e-9f0b-4c0d-8e2a-222222222222")
_JSON_DICT = {"service": "demo", "nested": {"a": [1, 2, 3], "b": True, "none": None}}
_JSON_LIST = [1, {"k": [True, None]}, "text", 3.5, False]
_CIPHERTEXT_BLOB = bytes(range(256)) * 4
_LONG_TEXT_VALUE = "L" * 5000  # > 4 KiB, exercises TEXT/LONGTEXT

# --------------------------------------------------------------------------
# 1. DDL contract (offline, both dialects).
# --------------------------------------------------------------------------

_DDL_EXPECTATIONS = {
    "postgres": {
        "id": "UUID",
        "ts": "TIMESTAMP WITH TIME ZONE",  # TIMESTAMPTZ
        "doc": "JSONB",
        "blob": "BYTEA",
        "long_text": "TEXT",
        "code": "VARCHAR",  # + CHECK, no native ENUM
    },
    "mysql": {
        "id": "CHAR(36)",  # canonical 36-char UUID string
        "ts": "DATETIME(6)",  # 6 = microsecond precision
        "doc": "JSON",
        "blob": "LONGBLOB",
        "long_text": "LONGTEXT",
        "code": "VARCHAR",  # + CHECK, no native ENUM
    },
}

_DIALECT_INSTANCES = {
    "postgres": postgresql.dialect(),
    "mysql": mysql.dialect(),
}


@pytest.mark.parametrize("dialect_name", _DIALECT_IDS)
def test_ddl_contract(dialect_name: str) -> None:
    """Compiled DDL matches the docs/architecture/01-data-model.md type table."""
    table = _build_table("m1_db002_ddl_contract")
    ddl = str(CreateTable(table).compile(dialect=_DIALECT_INSTANCES[dialect_name]))

    for column, expected in _DDL_EXPECTATIONS[dialect_name].items():
        assert expected in ddl, (
            f"[{dialect_name}] column {column!r}: expected DDL fragment "
            f"{expected!r} not found in:\n{ddl}"
        )

    # ENUM_CODE must be VARCHAR + CHECK on both dialects, never a native ENUM.
    assert "CHECK" in ddl, f"[{dialect_name}] missing CHECK constraint in:\n{ddl}"
    for code in _ALLOWED_CODES:
        assert code in ddl, (
            f"[{dialect_name}] CHECK must enumerate allowed code {code!r} in:\n{ddl}"
        )


# --------------------------------------------------------------------------
# Live round-trip fixtures: the SAME logic runs against both real dialects.
# --------------------------------------------------------------------------


@pytest_asyncio.fixture(params=_DIALECT_URLS, ids=_DIALECT_IDS)
async def types_env(request):
    """Creates the contract table on one live dialect engine, yields it."""
    dialect_name, url = request.param
    engine = create_async_engine(url)
    table = _build_table(f"m1_db002_types_{dialect_name}")
    ready = False
    try:
        async with engine.begin() as conn:
            await conn.run_sync(table.metadata.drop_all)
            await conn.run_sync(table.metadata.create_all)
        ready = True
        yield engine, table
    finally:
        if ready:
            async with engine.begin() as conn:
                await conn.run_sync(table.metadata.drop_all)
        await engine.dispose()


def _as_utc(value: datetime) -> datetime:
    """Interpret a read timestamp as UTC whether the driver returned naive
    or tz-aware. MySQL's driver returns a naive datetime; PostgreSQL returns
    a tz-aware one. Both must equal the written UTC instant."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@pytest.mark.asyncio
async def test_roundtrip_types(types_env) -> None:
    """Each logical type round-trips identically on PostgreSQL AND MySQL."""
    engine, table = types_env

    rows = (
        (_UID_JSON_DICT, _WRITTEN_TS, _JSON_DICT, _CIPHERTEXT_BLOB, _LONG_TEXT_VALUE, "active"),
        (_UID_JSON_LIST, _WRITTEN_TS, _JSON_LIST, _CIPHERTEXT_BLOB, _LONG_TEXT_VALUE, "disabled"),
    )

    async with engine.begin() as conn:
        for uid, ts, doc, blob, long_text, code in rows:
            await conn.execute(
                table.insert().values(
                    id=uid, ts=ts, doc=doc, blob=blob, long_text=long_text, code=code
                )
            )

    for uid, ts, doc, blob, long_text, code in rows:
        async with engine.begin() as conn:
            row = (await conn.execute(select(table).where(table.c.id == uid))).one()

        # ID: a canonical UUID string round-trips to the same uuid.UUID.
        assert isinstance(row.id, uuid.UUID)
        assert row.id == uid

        # UTC_TS: the written instant is preserved (microsecond precision).
        assert _as_utc(row.ts) == ts

        # JSON_DOC: dict keys order-insensitive, values exact; list exact.
        assert row.doc == doc

        # CIPHERTEXT: byte-for-byte.
        assert bytes(row.blob) == blob

        # LONG_TEXT: exact, including > 4 KiB content.
        assert row.long_text == long_text

        # ENUM_CODE: an allowed code round-trips.
        assert row.code == code


@pytest.mark.asyncio
async def test_enum_code_rejects_disallowed_value(types_env) -> None:
    """A disallowed enum code is rejected by the DB CHECK on both dialects.

    PostgreSQL surfaces the CHECK violation as IntegrityError; MySQL 8
    surfaces it as OperationalError. Rejection must come from the database,
    so both are accepted here.
    """
    engine, table = types_env

    with pytest.raises((IntegrityError, OperationalError)):
        async with engine.begin() as conn:
            await conn.execute(
                table.insert().values(
                    id=uuid.uuid4(),
                    ts=_WRITTEN_TS,
                    doc=_JSON_DICT,
                    blob=_CIPHERTEXT_BLOB,
                    long_text=_LONG_TEXT_VALUE,
                    code="banned",
                )
            )
