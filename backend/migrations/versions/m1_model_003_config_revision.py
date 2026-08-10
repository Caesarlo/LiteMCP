"""M1-MODEL-003: create the service_config_revision table.

Creates the immutable desired-config revision table pinned by the M1-MODEL-003
model contract (docs/architecture/01-data-model.md §5.3) with generic CREATE
fields only (§3.2) — immutable revisions carry no ``updated_*`` /
``row_version``. The ``secret_blob_id`` pointer column is created ID-typed and
nullable but WITHOUT a foreign key, because its target table
(``service_secret``) is not created in this feature — its enforcement is a
later feature (§5.3).

DDL stays portable across PostgreSQL and MySQL by using only the logical types
from ``litemcp.db.types`` (``ID``, ``UTC_TS``, ``JSON_DOC``, ``ENUM_CODE``) —
never a single-dialect type.

Revision ID: m1_model_003_config_revision
Revises: m1_model_002_service
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_003_config_revision"
down_revision: Union[str, Sequence[str], None] = "m1_model_002_service"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the service_config_revision table."""
    op.create_table(
        "service_config_revision",
        sa.Column("id", ID(), primary_key=True),
        sa.Column(
            "service_id",
            ID(),
            sa.ForeignKey("mcp_service.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "config_kind",
            ENUM_CODE("http_api", "stdio", "mcp_http"),
            sa.CheckConstraint(
                "config_kind IN ('http_api', 'stdio', 'mcp_http')",
                name="ck_service_config_revision_config_kind",
            ),
            nullable=False,
        ),
        sa.Column("public_config", JSON_DOC(), nullable=False),
        # DEFERRED: FK enforcement of secret_blob_id -> service_secret.id is a
        # later feature (service_secret is not created in M1-MODEL-003).
        sa.Column("secret_blob_id", ID(), nullable=True),
        sa.Column("source_descriptor", JSON_DOC(), nullable=True),
        sa.Column(
            "source_mode",
            ENUM_CODE(
                "fastmcp_introspection", "descriptor", "manual", "remote_sync"
            ),
            sa.CheckConstraint(
                "source_mode IN "
                "('fastmcp_introspection', 'descriptor', 'manual', 'remote_sync')",
                name="ck_service_config_revision_source_mode",
            ),
            nullable=False,
        ),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column(
            "state",
            ENUM_CODE(
                "draft",
                "validating",
                "validated",
                "active",
                "rejected",
                "superseded",
            ),
            sa.CheckConstraint(
                "state IN ('draft', 'validating', 'validated', 'active', "
                "'rejected', 'superseded')",
                name="ck_service_config_revision_state",
            ),
            nullable=False,
        ),
        sa.Column("validation_report", JSON_DOC(), nullable=True),
        sa.Column("activated_at", UTC_TS(), nullable=True),
        sa.Column("superseded_at", UTC_TS(), nullable=True),
        sa.Column("created_at", UTC_TS(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.UniqueConstraint(
            "service_id",
            "generation",
            name="uq_service_config_revision_service_generation",
        ),
        sa.UniqueConstraint(
            "id", "service_id", name="uq_service_config_revision_id_service"
        ),
    )


def downgrade() -> None:
    """Drop the service_config_revision table (its constraints drop with it)."""
    op.drop_table("service_config_revision")
