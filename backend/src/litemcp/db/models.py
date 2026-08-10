"""User, team and team-membership domain models (M1-MODEL-001).

Maps the ``user``, ``team`` and ``team_membership`` tables declared in
docs/architecture/01-data-model.md §5.1, §5.16 and §5.17 onto SQLAlchemy ORM
classes. Every table carries the generic audit fields (§3.2) and its DB-level
UNIQUE / CHECK / FK constraints (L63: enforcement lives in the database, never
in a Python-side value check).

Column types use only the portable logical types from ``litemcp.db.types`` —
``ID``, ``UTC_TS``, ``LONG_TEXT``, ``ENUM_CODE`` and the generic scalar/String
types — so the same models render valid DDL on PostgreSQL and MySQL. Enum-like
columns are varchar-sized ``ENUM_CODE`` columns guarded by an explicit portable
table-level ``CheckConstraint`` (no native database ENUM, §3.1 L46).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from litemcp.db.types import ENUM_CODE, ID, LONG_TEXT, UTC_TS


def _now_utc() -> datetime:
    """Current UTC wall-clock as a timezone-aware datetime."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base shared by every LiteMCP domain model."""


class User(Base):
    """A login account. Users are disabled, never soft-deleted (§5.1)."""

    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_user_role"),
        CheckConstraint(
            "status IN ('active', 'disabled', 'locked')",
            name="ck_user_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    username_normalized: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(ENUM_CODE("admin", "user"), nullable=False)
    status: Mapped[str] = mapped_column(
        ENUM_CODE("active", "disabled", "locked"), nullable=False
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_login_window_started_at: Mapped[datetime | None] = mapped_column(
        UTC_TS(), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class Team(Base):
    """An organizational unit partitioning the service marketplace (§5.16).

    Teams are archived (``status``), never soft-deleted via ``uniqueness_scope``.
    """

    __tablename__ = "team"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_team_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    key_normalized: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(LONG_TEXT(), nullable=True)
    status: Mapped[str] = mapped_column(
        ENUM_CODE("active", "archived"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )


class TeamMembership(Base):
    """A user's membership in a team, with a per-team role (§5.17)."""

    __tablename__ = "team_membership"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_membership_team_user"),
        CheckConstraint(
            "team_role IN ('admin', 'member')",
            name="ck_team_membership_team_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    team_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("team.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("user.id"), nullable=False
    )
    team_role: Mapped[str] = mapped_column(
        ENUM_CODE("admin", "member"), nullable=False
    )

    # Relationship metadata: declares the FK dependency so the unit of work
    # inserts the referenced user/team before this membership row.
    team: Mapped[Team] = relationship()
    user: Mapped[User] = relationship()

    created_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
