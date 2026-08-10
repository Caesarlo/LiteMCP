"""M1-MODEL-004: create the toolset and mcp_tool tables.

Creates the atomic publish unit (``toolset``, docs/architecture/01-data-model.md
§5.7) and its lossless MCP Tool definition home (``mcp_tool``, §5.8), both with
the generic §3.2 audit fields.

``toolset`` records the source and lifecycle state of a published toolset and
carries the UNIQUE ``(service_id, version_no)`` and UNIQUE ``(id, service_id)``
keys (the latter backs the ``mcp_service.active_toolset_id`` ownership pointer
and the ``mcp_tool`` composite FK). ``mcp_tool`` losslessly stores complete MCP
Tool definitions; its composite FK ``(toolset_id, service_id)`` ->
``toolset(id, service_id)`` guarantees a tool's service equals its toolset's
service, and ``toolset_id`` -> ``toolset.id`` is ON DELETE CASCADE (staging
cleanup only, §5.8).

DDL stays portable across PostgreSQL and MySQL by using only the logical types
from ``litemcp.db.types`` (``ID``, ``UTC_TS``, ``JSON_DOC``, ``LONG_TEXT``,
``ENUM_CODE``) — never a single-dialect type. Enum-like columns are varchar
``ENUM_CODE`` columns guarded by portable CHECK constraints.

Revision ID: m1_model_004_toolset
Revises: m1_model_003_config_revision
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, LONG_TEXT, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_004_toolset"
down_revision: Union[str, Sequence[str], None] = "m1_model_003_config_revision"
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
    """Create the toolset table, then the mcp_tool table."""
    op.create_table(
        "toolset",
        sa.Column("id", ID(), primary_key=True),
        sa.Column(
            "service_id",
            ID(),
            sa.ForeignKey("mcp_service.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "config_revision_id",
            ID(),
            sa.ForeignKey("service_config_revision.id"),
            nullable=True,
        ),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column(
            "source_kind",
            ENUM_CODE("manual", "fastmcp", "descriptor", "remote_mcp"),
            sa.CheckConstraint(
                "source_kind IN "
                "('manual', 'fastmcp', 'descriptor', 'remote_mcp')",
                name="ck_toolset_source_kind",
            ),
            nullable=False,
        ),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("mcp_protocol_version", sa.String(16), nullable=False),
        sa.Column(
            "json_schema_dialect",
            sa.String(128),
            nullable=False,
            server_default="https://json-schema.org/draft/2020-12/schema",
        ),
        sa.Column("server_capabilities", JSON_DOC(), nullable=True),
        sa.Column("server_info", JSON_DOC(), nullable=True),
        sa.Column("instructions", LONG_TEXT(), nullable=True),
        sa.Column(
            "state",
            ENUM_CODE(
                "staging",
                "validating",
                "validated",
                "active",
                "rejected",
                "retired",
            ),
            sa.CheckConstraint(
                "state IN ('staging', 'validating', 'validated', 'active', "
                "'rejected', 'retired')",
                name="ck_toolset_state",
            ),
            nullable=False,
        ),
        sa.Column("validation_report", JSON_DOC(), nullable=True),
        sa.Column(
            "tool_count",
            sa.Integer(),
            sa.CheckConstraint("tool_count >= 0", name="ck_toolset_tool_count"),
            nullable=False,
        ),
        sa.Column("activated_at", UTC_TS(), nullable=True),
        sa.Column("retired_at", UTC_TS(), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint(
            "service_id", "version_no", name="uq_toolset_service_version"
        ),
        sa.UniqueConstraint("id", "service_id", name="uq_toolset_id_service"),
    )

    op.create_table(
        "mcp_tool",
        sa.Column("id", ID(), primary_key=True),
        sa.Column(
            "toolset_id",
            ID(),
            sa.ForeignKey("toolset.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "service_id",
            ID(),
            sa.ForeignKey("mcp_service.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("description", LONG_TEXT(), nullable=True),
        sa.Column("input_schema", JSON_DOC(), nullable=False),
        sa.Column("output_schema", JSON_DOC(), nullable=True),
        sa.Column("annotations", JSON_DOC(), nullable=True),
        sa.Column("execution", JSON_DOC(), nullable=True),
        sa.Column("icons", JSON_DOC(), nullable=True),
        sa.Column("meta", JSON_DOC(), nullable=True),
        sa.Column("raw_definition", JSON_DOC(), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column(
            "source",
            ENUM_CODE("manual", "synced"),
            sa.CheckConstraint(
                "source IN ('manual', 'synced')",
                name="ck_mcp_tool_source",
            ),
            nullable=False,
        ),
        sa.Column("http_binding", JSON_DOC(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_audit_columns(),
        sa.UniqueConstraint("toolset_id", "name", name="uq_mcp_tool_toolset_name"),
        sa.ForeignKeyConstraint(
            ["toolset_id", "service_id"],
            ["toolset.id", "toolset.service_id"],
            name="fk_mcp_tool_toolset_service",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    """Drop the mcp_tool table, then the toolset table (FK order)."""
    op.drop_table("mcp_tool")
    op.drop_table("toolset")
