"""Cross-dialect base types (M1-DB-002).

The domain layer uses only the logical types below, mapped to the physical
column types each dialect declares via ``load_dialect_impl``:

=============  ======================  ==================  ==================
logical type   SQLAlchemy              PostgreSQL          MySQL 8
=============  ======================  ==================  ==================
``ID``         ``Uuid(as_uuid=True)``  ``UUID``            ``CHAR(36)``
``UTC_TS``     ``DateTime``            ``TIMESTAMPTZ``     ``DATETIME(6)``
``JSON_DOC``   ``JSON``                ``JSONB``           ``JSON``
``CIPHERTEXT`` ``LargeBinary``         ``BYTEA``           ``LONGBLOB``
``LONG_TEXT``  ``Text``                ``TEXT``            ``LONGTEXT``
``ENUM_CODE``  ``String`` + CHECK      ``VARCHAR + CHECK`` ``VARCHAR + CHECK``
=============  ======================  ==================  ==================

The application always writes UTC. On MySQL the timestamp is stored as the
naive UTC wall-clock (``DATETIME`` is naive); readers re-attach UTC. Enum
codes are enforced by a portable ``CHECK`` constraint declared on the column
— never a native DB ENUM.

See docs/architecture/01-data-model.md §3.1.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, String, Text, TypeDecorator, Uuid
from sqlalchemy.dialects.mysql import CHAR, DATETIME, LONGBLOB, LONGTEXT
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import DateTime, LargeBinary

__all__ = [
    "CIPHERTEXT",
    "ENUM_CODE",
    "ID",
    "JSON_DOC",
    "LONG_TEXT",
    "UTC_TS",
]


class ID(TypeDecorator[Any]):
    """A UUID primary key, ``UUID`` on PostgreSQL and ``CHAR(36)`` on MySQL.

    The canonical 36-character UUID string is stored on MySQL; the value is
    always a ``uuid.UUID`` on the Python side, in both directions.
    """

    impl = Uuid
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def coerce_compared_value(self, op: Any, value: Any) -> Any:
        # Bind the comparison operand through THIS type so the canonical
        # CHAR(36) form is produced on MySQL instead of Uuid's CHAR(32) hex.
        return self

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name != "postgresql":
            return str(value)
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if dialect.name != "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return value


class UTC_TS(TypeDecorator[Any]):
    """A UTC timestamp, ``TIMESTAMP WITH TIME ZONE`` on PostgreSQL and
    ``DATETIME(6)`` on MySQL.

    Values are normalized to UTC on write. MySQL's ``DATETIME`` is naive, so
    the UTC wall-clock is stored there and readers re-attach UTC semantics.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.astimezone(UTC)
        if dialect.name == "mysql" and isinstance(value, datetime):
            return value.replace(tzinfo=None)
        return value


class JSON_DOC(TypeDecorator[Any]):
    """A nested JSON document, ``JSONB`` on PostgreSQL and ``JSON`` on MySQL.

    A top-level Python ``None`` binds as SQL ``NULL`` (``none_as_null=True``),
    never as the JSON literal ``null``: a JSON document column either holds a
    document or is absent, so a ``NOT NULL`` JSON column rejects Python
    ``None`` at the database layer (§3.2 L63: enforcement lives in the
    database). Nested ``None`` inside a document is unaffected and round-trips
    as JSON ``null``.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, none_as_null: bool = True) -> None:
        self.none_as_null = none_as_null
        super().__init__(none_as_null=none_as_null)

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB(none_as_null=self.none_as_null))
        return dialect.type_descriptor(JSON(none_as_null=self.none_as_null))


class CIPHERTEXT(TypeDecorator[Any]):
    """Encrypted payload bytes, ``BYTEA`` on PostgreSQL and ``LONGBLOB`` on MySQL."""

    impl = LargeBinary
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(LONGBLOB())
        return dialect.type_descriptor(LargeBinary())


class LONG_TEXT(TypeDecorator[Any]):
    """Long free-form text, ``TEXT`` on PostgreSQL and ``LONGTEXT`` on MySQL."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(LONGTEXT())
        return dialect.type_descriptor(Text())


class ENUM_CODE(TypeDecorator[Any]):
    """A VARCHAR column holding one of the allowed codes, on both dialects.

    ``ENUM_CODE(*codes)`` records the allowed codes so the column has a
    sensible length; enforcement is the portable ``CHECK`` constraint the
    caller declares on the column (no native database ENUM).
    """

    impl = String
    cache_ok = True

    def __init__(self, *codes: str) -> None:
        self.codes = tuple(codes)
        self._length = max((len(code) for code in codes), default=16) + 16
        super().__init__(length=self._length)

    def load_dialect_impl(self, dialect: Any) -> Any:
        return dialect.type_descriptor(String(length=self._length))
