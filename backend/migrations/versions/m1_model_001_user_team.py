"""M1-MODEL-001: create the user, team and team_membership tables.

Creates the three entity tables pinned by the M1-MODEL-001 model contract
(docs/architecture/01-data-model.md §5.1, §5.16, §5.17) with the generic audit
fields (§3.2) and the DB-level UNIQUE / CHECK / FK constraints the ORM models
declare. DDL stays portable across PostgreSQL and MySQL by using only the
logical types from ``litemcp.db.types`` (``ID``, ``UTC_TS``, ``LONG_TEXT``,
``ENUM_CODE``) — never a single-dialect type.

Revision ID: m1_model_001_user_team
Revises: m1_db_003_bootstrap_root
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, LONG_TEXT, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_001_user_team"
down_revision: Union[str, Sequence[str], None] = "m1_db_003_bootstrap_root"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _audit_columns() -> list[sa.Column]:
    """The generic audit fields every mutable business table carries (§3.2)."""
    return [
        sa.Column("created_at", UTC_TS(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_at", UTC_TS(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    ]


def upgrade() -> None:
    """Create the user, team and team_membership tables."""
    op.create_table(
        "user",
        sa.Column("id", ID(), primary_key=True),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column(
            "username_normalized", sa.String(128), nullable=False, unique=True
        ),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "role",
            ENUM_CODE("admin", "user"),
            sa.CheckConstraint("role IN ('admin', 'user')", name="ck_user_role"),
            nullable=False,
        ),
        sa.Column(
            "status",
            ENUM_CODE("active", "disabled", "locked"),
            sa.CheckConstraint(
                "status IN ('active', 'disabled', 'locked')",
                name="ck_user_status",
            ),
            nullable=False,
        ),
        sa.Column("password_changed_at", UTC_TS(), nullable=False),
        sa.Column("last_login_at", UTC_TS(), nullable=True),
        sa.Column(
            "failed_login_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("failed_login_window_started_at", UTC_TS(), nullable=True),
        sa.Column("locked_until", UTC_TS(), nullable=True),
        *_audit_columns(),
    )

    op.create_table(
        "team",
        sa.Column("id", ID(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("key_normalized", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", LONG_TEXT(), nullable=True),
        sa.Column(
            "status",
            ENUM_CODE("active", "archived"),
            sa.CheckConstraint(
                "status IN ('active', 'archived')",
                name="ck_team_status",
            ),
            nullable=False,
        ),
        *_audit_columns(),
    )

    op.create_table(
        "team_membership",
        sa.Column("id", ID(), primary_key=True),
        sa.Column("team_id", ID(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("user_id", ID(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "team_role",
            ENUM_CODE("admin", "member"),
            sa.CheckConstraint(
                "team_role IN ('admin', 'member')",
                name="ck_team_membership_team_role",
            ),
            nullable=False,
        ),
        *_audit_columns(),
        sa.UniqueConstraint(
            "team_id", "user_id", name="uq_team_membership_team_user"
        ),
    )


def downgrade() -> None:
    """Drop the team_membership, team and user tables in reverse order."""
    op.drop_table("team_membership")
    op.drop_table("team")
    op.drop_table("user")
