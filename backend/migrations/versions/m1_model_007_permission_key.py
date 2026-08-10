"""M1-MODEL-007: create service permissions and API-key metadata tables.

Creates the explicit object-level permission ledger (``mcp_service_permission``,
docs/architecture/01-data-model.md §5.12) and the non-secret API-key metadata
table (``api_key``, §5.13). Both tables carry the generic §3.2 audit fields.
The plaintext API key is intentionally not persisted; ``public_id``,
``display_prefix``, ``secret_hash`` and lifecycle fields are the durable
metadata used by later management and gateway features.

DDL stays portable across PostgreSQL and MySQL by using only the logical types
from ``litemcp.db.types`` (``ID``, ``UTC_TS`` and ``ENUM_CODE``) plus portable
scalar SQLAlchemy types. Enum-like values and cross-field invariants are
enforced with database CHECK constraints on both dialects.

Revision ID: m1_model_007_permission_key
Revises: m1_model_006_condition_task
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_007_permission_key"
down_revision: Union[str, Sequence[str], None] = "m1_model_006_condition_task"
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
    """Create the permission ledger and API-key metadata tables."""
    op.create_table(
        "mcp_service_permission",
        sa.Column("id", ID(), primary_key=True),
        sa.Column(
            "service_id",
            ID(),
            sa.ForeignKey("mcp_service.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "principal_type",
            ENUM_CODE("user", "team", "everyone"),
            nullable=False,
        ),
        sa.Column(
            "user_id", ID(), sa.ForeignKey("user.id"), nullable=True
        ),
        sa.Column(
            "team_id", ID(), sa.ForeignKey("team.id"), nullable=True
        ),
        sa.Column(
            "role", ENUM_CODE("editor", "viewer"), nullable=False
        ),
        sa.Column("principal_key", sa.String(80), nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint(
            "service_id",
            "principal_key",
            name="uq_mcp_service_permission_service_principal_key",
        ),
        sa.CheckConstraint(
            "principal_type IN ('user', 'team', 'everyone')",
            name="ck_mcp_service_permission_principal_type",
        ),
        sa.CheckConstraint(
            "role IN ('editor', 'viewer')",
            name="ck_mcp_service_permission_role",
        ),
        sa.CheckConstraint(
            "(principal_type='user' and user_id is not null) or "
            "(principal_type!='user' and user_id is null)",
            name="ck_mcp_service_permission_user_id_consistency",
        ),
        sa.CheckConstraint(
            "(principal_type='team' and team_id is not null) or "
            "(principal_type!='team' and team_id is null)",
            name="ck_mcp_service_permission_team_id_consistency",
        ),
        sa.CheckConstraint(
            "(principal_type='user') or (role='viewer')",
            name="ck_mcp_service_permission_role_scope",
        ),
    )
    op.create_index(
        "ix_mcp_service_permission_service_principal_type",
        "mcp_service_permission",
        ["service_id", "principal_type"],
    )
    op.create_index(
        "ix_mcp_service_permission_user_role_service",
        "mcp_service_permission",
        ["user_id", "role", "service_id"],
    )
    op.create_index(
        "ix_mcp_service_permission_team_service",
        "mcp_service_permission",
        ["team_id", "service_id"],
    )

    op.create_table(
        "api_key",
        sa.Column("id", ID(), primary_key=True),
        sa.Column(
            "service_id",
            ID(),
            sa.ForeignKey("mcp_service.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(32), nullable=False),
        sa.Column("display_prefix", sa.String(32), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("hash_algorithm", sa.String(32), nullable=False),
        sa.Column("pepper_version", sa.String(64), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "status", ENUM_CODE("active", "revoked"), nullable=False
        ),
        sa.Column("expires_at", UTC_TS(), nullable=True),
        sa.Column("last_used_at", UTC_TS(), nullable=True),
        sa.Column("last_used_ip_hash", sa.String(64), nullable=True),
        sa.Column("revoked_at", UTC_TS(), nullable=True),
        sa.Column("revoked_by", ID(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("rate_limit_qps", sa.Numeric(10, 2), nullable=True),
        sa.Column("rate_limit_burst", sa.Integer(), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint("public_id", name="uq_api_key_public_id"),
        sa.UniqueConstraint("secret_hash", name="uq_api_key_secret_hash"),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_api_key_status"
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_api_key_expires_after_created",
        ),
        sa.CheckConstraint(
            "(status!='revoked') OR (revoked_at IS NOT NULL)",
            name="ck_api_key_revoked_requires_revoked_at",
        ),
        sa.CheckConstraint(
            "rate_limit_qps IS NULL OR rate_limit_qps > 0",
            name="ck_api_key_rate_limit_qps_positive",
        ),
        sa.CheckConstraint(
            "rate_limit_burst IS NULL OR rate_limit_burst >= 1",
            name="ck_api_key_rate_limit_burst_min",
        ),
    )
    op.create_index(
        "ix_api_key_service_status_expires_at",
        "api_key",
        ["service_id", "status", "expires_at"],
    )


def downgrade() -> None:
    """Drop the API-key table, then the permission ledger."""
    op.drop_table("api_key")
    op.drop_table("mcp_service_permission")
