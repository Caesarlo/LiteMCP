"""M1-MODEL-002: create the mcp_service table.

Creates the service main table pinned by the M1-MODEL-002 model contract
(docs/architecture/01-data-model.md §5.2) with the generic audit fields (§3.2)
and the soft-delete fields (§3.3). The active version pointers
``active_config_revision_id`` / ``active_toolset_id`` are created ID-typed and
nullable but WITHOUT foreign keys, because their FK targets
(``service_config_revision`` / ``toolset``) are not created in this feature —
their enforcement is a later feature (§5.2 L165).

DDL stays portable across PostgreSQL and MySQL by using only the logical types
from ``litemcp.db.types`` (``ID``, ``UTC_TS``, ``JSON_DOC``, ``LONG_TEXT``,
``ENUM_CODE``) — never a single-dialect type.

Revision ID: m1_model_002_service
Revises: m1_model_001_user_team
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, LONG_TEXT, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_002_service"
down_revision: Union[str, Sequence[str], None] = "m1_model_001_user_team"
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
    """Create the mcp_service table and its market/ownership indexes."""
    op.create_table(
        "mcp_service",
        sa.Column("id", ID(), primary_key=True),
        sa.Column("namespace_key", sa.String(64), nullable=False),
        sa.Column(
            "team_id",
            ID(),
            sa.ForeignKey("team.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "type",
            ENUM_CODE("http_api", "stdio", "mcp_http"),
            sa.CheckConstraint(
                "type IN ('http_api', 'stdio', 'mcp_http')",
                name="ck_mcp_service_type",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("name_normalized", sa.String(128), nullable=False),
        sa.Column(
            "uniqueness_scope",
            sa.String(64),
            nullable=False,
            server_default="'LIVE'",
        ),
        sa.Column("tags", JSON_DOC(), nullable=False),
        sa.Column("description", LONG_TEXT(), nullable=True),
        sa.Column("icon_object_key", sa.String(512), nullable=True),
        sa.Column(
            "desired_status",
            ENUM_CODE("enabled", "disabled"),
            sa.CheckConstraint(
                "desired_status IN ('enabled', 'disabled')",
                name="ck_mcp_service_desired_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "generation",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "observed_generation",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "runtime_status",
            ENUM_CODE("pending", "ready", "degraded", "unhealthy", "failed"),
            sa.CheckConstraint(
                "runtime_status IN "
                "('pending', 'ready', 'degraded', 'unhealthy', 'failed')",
                name="ck_mcp_service_runtime_status",
            ),
            nullable=False,
        ),
        # DEFERRED: FK enforcement of these pointers is a later feature
        # (service_config_revision / toolset are not created in M1-MODEL-002).
        sa.Column("active_config_revision_id", ID(), nullable=True),
        sa.Column("active_toolset_id", ID(), nullable=True),
        sa.Column(
            "agent_auth_mode",
            ENUM_CODE("api_key", "none", "oauth2"),
            sa.CheckConstraint(
                "agent_auth_mode IN ('api_key', 'none', 'oauth2')",
                name="ck_mcp_service_agent_auth_mode",
            ),
            nullable=False,
        ),
        sa.Column("rate_limit_qps", sa.Numeric(10, 2), nullable=True),
        sa.Column("rate_limit_burst", sa.Integer(), nullable=True),
        sa.Column("queue_max_depth", sa.Integer(), nullable=True),
        sa.Column("queue_timeout_ms", sa.Integer(), nullable=True),
        sa.Column("stdio_instance_max", sa.Integer(), nullable=True),
        sa.Column("stdio_concurrency_per_instance", sa.Integer(), nullable=True),
        sa.Column("deleted_at", UTC_TS(), nullable=True),
        sa.Column("deleted_by", sa.String(128), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint(
            "namespace_key",
            "name_normalized",
            "uniqueness_scope",
            name="uq_mcp_service_namespace_name_scope",
        ),
        sa.CheckConstraint(
            "observed_generation <= generation",
            name="ck_mcp_service_generation_order",
        ),
        sa.CheckConstraint(
            "(type = 'stdio' OR (queue_max_depth IS NULL "
            "AND queue_timeout_ms IS NULL AND stdio_instance_max IS NULL "
            "AND stdio_concurrency_per_instance IS NULL))",
            name="ck_mcp_service_stdio_only_null_for_non_stdio",
        ),
    )

    op.create_index(
        "ix_mcp_service_namespace_desired_status_type",
        "mcp_service",
        ["namespace_key", "desired_status", "type"],
    )
    op.create_index(
        "ix_mcp_service_team_desired_status",
        "mcp_service",
        ["team_id", "desired_status"],
    )
    op.create_index(
        "ix_mcp_service_created_by_deleted_at",
        "mcp_service",
        ["created_by", "deleted_at"],
    )


def downgrade() -> None:
    """Drop the mcp_service table (its indexes drop with it)."""
    op.drop_table("mcp_service")
