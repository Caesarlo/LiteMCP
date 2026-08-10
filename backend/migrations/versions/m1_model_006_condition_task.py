"""M1-MODEL-006: create the service_condition and mcp_task tables.

Creates the runtime-observed condition ledger (``service_condition``,
docs/architecture/01-data-model.md §5.11) and the async-operation / MCP Tasks
model (``mcp_task``, §5.15).

``service_condition`` persists what the system actually observed about a
service (ConfigReady / BuildReady / ToolsReady / RuntimeHealthy /
UpstreamReachable), independent of the user's desired config, so a transient
``build_status``/``last_error`` can never overwrite each other.
``(service_id, type)`` is UNIQUE and type / status carry named CHECK
constraints. The table carries the full §3.2 audit set and no soft-delete
fields; ``mcp_service.runtime_status`` is its queryable summary, the condition
rows are the diagnostic source of truth.

``mcp_task`` is the MCP Tasks record: ``id`` doubles as the MCP taskId,
``tool_id`` pins the tool version at task creation, ``session_id_hash`` never
stores the raw session token, and status / poll_interval_ms carry named CHECK
constraints. It carries the MCP time fields (``created_at`` /
``last_updated_at`` / ``expires_at``) — NOT the §3.2 audit set — and declares
no UNIQUE constraints.

DDL stays portable across PostgreSQL and MySQL by using only the logical types
from ``litemcp.db.types`` (``ID``, ``UTC_TS``, ``ENUM_CODE``). Enum-like
columns are varchar ``ENUM_CODE`` columns guarded by portable CHECK
constraints. ``service_condition`` is created before ``mcp_task``; neither
references the other, but ``mcp_task``'s FKs reference ``mcp_service``,
``mcp_tool`` and ``service_artifact``, all of which already exist.

Revision ID: m1_model_006_condition_task
Revises: m1_model_005_artifact_build
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_006_condition_task"
down_revision: Union[str, Sequence[str], None] = "m1_model_005_artifact_build"
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
    """Create the service_condition table, then the mcp_task table."""
    op.create_table(
        "service_condition",
        sa.Column("id", ID(), primary_key=True),
        sa.Column(
            "service_id",
            ID(),
            sa.ForeignKey("mcp_service.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "type",
            ENUM_CODE(
                "ConfigReady",
                "BuildReady",
                "ToolsReady",
                "RuntimeHealthy",
                "UpstreamReachable",
            ),
            sa.CheckConstraint(
                "type IN ('ConfigReady', 'BuildReady', 'ToolsReady', "
                "'RuntimeHealthy', 'UpstreamReachable')",
                name="ck_service_condition_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            ENUM_CODE("true", "false", "unknown"),
            sa.CheckConstraint(
                "status IN ('true', 'false', 'unknown')",
                name="ck_service_condition_status",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("message", sa.String(2048), nullable=True),
        sa.Column("observed_generation", sa.BigInteger(), nullable=False),
        sa.Column("last_transition_at", UTC_TS(), nullable=False),
        sa.Column("last_probe_at", UTC_TS(), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint(
            "service_id", "type", name="uq_service_condition_service_type"
        ),
    )

    op.create_table(
        "mcp_task",
        sa.Column("id", ID(), primary_key=True),
        sa.Column(
            "service_id",
            ID(),
            sa.ForeignKey("mcp_service.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tool_id",
            ID(),
            sa.ForeignKey("mcp_tool.id"),
            nullable=False,
        ),
        sa.Column("session_id_hash", sa.String(64), nullable=True),
        sa.Column("downstream_task_id", sa.String(255), nullable=True),
        sa.Column(
            "status",
            ENUM_CODE(
                "working", "input_required", "completed", "failed", "cancelled"
            ),
            sa.CheckConstraint(
                "status IN ('working', 'input_required', 'completed', 'failed', "
                "'cancelled')",
                name="ck_mcp_task_status",
            ),
            nullable=False,
        ),
        sa.Column("status_message", sa.String(2048), nullable=True),
        sa.Column(
            "result_artifact_id",
            ID(),
            sa.ForeignKey("service_artifact.id"),
            nullable=True,
        ),
        sa.Column("created_at", UTC_TS(), nullable=False),
        sa.Column("last_updated_at", UTC_TS(), nullable=False),
        sa.Column("expires_at", UTC_TS(), nullable=True),
        sa.Column(
            "poll_interval_ms",
            sa.Integer(),
            sa.CheckConstraint(
                "poll_interval_ms > 0",
                name="ck_mcp_task_poll_interval_positive",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the mcp_task table, then the service_condition table."""
    op.drop_table("mcp_task")
    op.drop_table("service_condition")
