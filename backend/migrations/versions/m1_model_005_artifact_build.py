"""M1-MODEL-005: create the service_artifact and build_run tables.

Creates the immutable build/code artifact home (``service_artifact``,
docs/architecture/01-data-model.md §5.5) and the build-attempt ledger
(``build_run``, §5.6), both with the generic §3.2 audit fields.

``service_artifact`` uniformly records source packages, service descriptors,
dependency bundles, built images and build logs as immutable objects
referenced by storage key and content digest; ``(storage_backend, object_key)``
is UNIQUE and the kind / storage_backend / state / size_bytes columns carry
named CHECK constraints. ``build_run`` records one attempt to turn a
``source_artifact_id`` into a validated toolset and links its input, output and
full build log as ``service_artifact`` rows; strategy / status carry named
CHECK constraints and ``(service_id, status, created_at)`` gets a non-unique
index for build-history queries.

DDL stays portable across PostgreSQL and MySQL by using only the logical types
from ``litemcp.db.types`` (``ID``, ``UTC_TS``, ``JSON_DOC``, ``ENUM_CODE``) —
never a single-dialect type. Enum-like columns are varchar ``ENUM_CODE``
columns guarded by portable CHECK constraints. ``service_artifact`` is created
first because ``build_run``'s artifact FKs reference it.

Revision ID: m1_model_005_artifact_build
Revises: m1_model_004_toolset
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_005_artifact_build"
down_revision: Union[str, Sequence[str], None] = "m1_model_004_toolset"
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
    """Create the service_artifact table, then the build_run table (FK order)."""
    op.create_table(
        "service_artifact",
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
        sa.Column(
            "kind",
            ENUM_CODE(
                "source_package",
                "descriptor",
                "build_bundle",
                "container_image",
                "build_log",
            ),
            sa.CheckConstraint(
                "kind IN ('source_package', 'descriptor', 'build_bundle', "
                "'container_image', 'build_log')",
                name="ck_service_artifact_kind",
            ),
            nullable=False,
        ),
        sa.Column(
            "storage_backend",
            ENUM_CODE("filesystem", "s3", "minio", "registry"),
            sa.CheckConstraint(
                "storage_backend IN ('filesystem', 's3', 'minio', 'registry')",
                name="ck_service_artifact_storage_backend",
            ),
            nullable=False,
        ),
        # ``object_key`` is VARCHAR(1024) and participates in the UNIQUE
        # (storage_backend, object_key) index. Under utf8mb4 a 1024-char key is
        # 4096 bytes, exceeding MySQL's 3072-byte index limit (errno 1071), so
        # the column is stored single-byte (latin1, via the latin1_bin
        # collation) on MySQL only — object keys are ASCII paths / digests in
        # practice. This preserves FULL-column uniqueness while keeping the
        # documented varchar(1024) length. ``with_variant`` keeps the plain
        # ``VARCHAR(1024)`` on PostgreSQL, so the DDL stays portable (§3.1).
        sa.Column(
            "object_key",
            sa.String(1024).with_variant(
                sa.String(1024, collation="latin1_bin"), "mysql"
            ),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "size_bytes",
            sa.BigInteger(),
            sa.CheckConstraint(
                "size_bytes >= 0",
                name="ck_service_artifact_size_bytes_non_negative",
            ),
            nullable=False,
        ),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("format", sa.String(32), nullable=False),
        sa.Column(
            "state",
            ENUM_CODE(
                "staging",
                "available",
                "quarantined",
                "gc_pending",
                "deleted",
            ),
            sa.CheckConstraint(
                "state IN ('staging', 'available', 'quarantined', "
                "'gc_pending', 'deleted')",
                name="ck_service_artifact_state",
            ),
            nullable=False,
        ),
        sa.Column("scan_report", JSON_DOC(), nullable=True),
        sa.Column("retain_until", UTC_TS(), nullable=True),
        *_audit_columns(),
        sa.UniqueConstraint(
            "storage_backend", "object_key", name="uq_service_artifact_storage_object_key"
        ),
    )

    op.create_table(
        "build_run",
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
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id",
            ID(),
            sa.ForeignKey("service_artifact.id"),
            nullable=False,
        ),
        sa.Column(
            "strategy",
            ENUM_CODE("fastmcp", "descriptor", "custom_adapter"),
            sa.CheckConstraint(
                "strategy IN ('fastmcp', 'descriptor', 'custom_adapter')",
                name="ck_build_run_strategy",
            ),
            nullable=False,
        ),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("base_image_digest", sa.String(255), nullable=False),
        sa.Column("dependency_digest", sa.String(64), nullable=True),
        sa.Column(
            "status",
            ENUM_CODE(
                "queued",
                "building",
                "validating",
                "succeeded",
                "failed",
                "cancelled",
                "superseded",
            ),
            sa.CheckConstraint(
                "status IN ('queued', 'building', 'validating', 'succeeded', "
                "'failed', 'cancelled', 'superseded')",
                name="ck_build_run_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "output_artifact_id",
            ID(),
            sa.ForeignKey("service_artifact.id"),
            nullable=True,
        ),
        sa.Column("discovered_descriptor", JSON_DOC(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.String(2048), nullable=True),
        sa.Column(
            "log_artifact_id",
            ID(),
            sa.ForeignKey("service_artifact.id"),
            nullable=True,
        ),
        sa.Column("started_at", UTC_TS(), nullable=True),
        sa.Column("finished_at", UTC_TS(), nullable=True),
        *_audit_columns(),
        sa.Index(
            "ix_build_run_service_status_created_at",
            "service_id",
            "status",
            "created_at",
        ),
    )


def downgrade() -> None:
    """Drop the build_run table, then the service_artifact table (FK order)."""
    op.drop_table("build_run")
    op.drop_table("service_artifact")
