"""User, team, team-membership, service, service-config-revision, toolset,
MCP-tool, service-artifact, build-run, service-condition, MCP-task, permission,
API-key, audit-event and outbox domain models (M1-MODEL-001..008).

Maps the ``user``, ``team``, ``team_membership``, ``mcp_service``,
``service_config_revision``, ``toolset``, ``mcp_tool``, ``service_artifact``,
``build_run``, ``service_condition``, ``mcp_task``, ``mcp_service_permission``,
``api_key``, ``audit_event`` and ``outbox`` tables declared in
docs/architecture/01-data-model.md §5.1, §5.16, §5.17, §5.2, §5.3, §5.7, §5.8,
§5.5, §5.6, §5.11, §5.15, §5.12, §5.13 and §5.14 (and 03-service-crud.md §6.3
for ``outbox``) onto SQLAlchemy ORM classes. Every mutable table carries the generic
audit fields (§3.2) and its DB-level UNIQUE / CHECK / FK constraints (L63:
enforcement lives in the database, never in a Python-side value check);
immutable config revisions carry generic CREATE fields only — no ``updated_*``
/ ``row_version`` (§5.3), ``mcp_task`` carries the MCP time fields
(``created_at`` / ``last_updated_at`` / ``expires_at``) rather than the §3.2
audit set (§5.15), and ``audit_event`` / ``outbox`` carry no §3.2 audit columns
at all — ``audit_event`` IS the audit record and ``outbox`` is a
delivery-work row written in the same transaction as the change it describes.

Column types use only the portable logical types from ``litemcp.db.types`` —
``ID``, ``UTC_TS``, ``JSON_DOC``, ``LONG_TEXT``, ``ENUM_CODE`` and the generic
scalar/String types — so the same models render valid DDL on PostgreSQL and
MySQL. Enum-like columns are varchar-sized ``ENUM_CODE`` columns guarded by an
explicit portable table-level ``CheckConstraint`` (no native database ENUM,
§3.1 L46).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, LONG_TEXT, UTC_TS


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


class Service(Base):
    """A marketplace service's stable identity and expected state (§5.2).

    ``mcp_service`` stores only the stable identity, the user's desired state
    and the runtime summary; config/build history lives in the versioned
    ``service_config_revision`` / ``toolset`` tables (M1-MODEL-002 does not
    create those tables, so the active version pointers are present, ID-typed
    and nullable but carry no FK here — their enforcement is a later feature,
    §5.2 L165).
    """

    __tablename__ = "mcp_service"
    __table_args__ = (
        UniqueConstraint(
            "namespace_key",
            "name_normalized",
            "uniqueness_scope",
            name="uq_mcp_service_namespace_name_scope",
        ),
        Index(
            "ix_mcp_service_namespace_desired_status_type",
            "namespace_key",
            "desired_status",
            "type",
        ),
        Index(
            "ix_mcp_service_team_desired_status",
            "team_id",
            "desired_status",
        ),
        Index(
            "ix_mcp_service_created_by_deleted_at",
            "created_by",
            "deleted_at",
        ),
        CheckConstraint(
            "type IN ('http_api', 'stdio', 'mcp_http')",
            name="ck_mcp_service_type",
        ),
        CheckConstraint(
            "desired_status IN ('enabled', 'disabled')",
            name="ck_mcp_service_desired_status",
        ),
        CheckConstraint(
            "runtime_status IN "
            "('pending', 'ready', 'degraded', 'unhealthy', 'failed')",
            name="ck_mcp_service_runtime_status",
        ),
        CheckConstraint(
            "agent_auth_mode IN ('api_key', 'none', 'oauth2')",
            name="ck_mcp_service_agent_auth_mode",
        ),
        CheckConstraint(
            "observed_generation <= generation",
            name="ck_mcp_service_generation_order",
        ),
        CheckConstraint(
            "(type = 'stdio' OR (queue_max_depth IS NULL "
            "AND queue_timeout_ms IS NULL AND stdio_instance_max IS NULL "
            "AND stdio_concurrency_per_instance IS NULL))",
            name="ck_mcp_service_stdio_only_null_for_non_stdio",
        ),
        # A portable replacement for a partial unique index: active rows must
        # claim the LIVE scope, while tombstones must move to a distinct
        # non-LIVE scope so the original name can be reused safely.
        CheckConstraint(
            "(deleted_at IS NULL AND uniqueness_scope = 'LIVE') OR "
            "(deleted_at IS NOT NULL AND uniqueness_scope <> 'LIVE')",
            name="ck_mcp_service_deleted_scope_consistency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    namespace_key: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("team.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        ENUM_CODE("http_api", "stdio", "mcp_http"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(128), nullable=False)
    uniqueness_scope: Mapped[str] = mapped_column(
        String(64), nullable=False, default="LIVE", server_default="'LIVE'"
    )
    tags: Mapped[list] = mapped_column(
        JSON_DOC(), nullable=False, default=list
    )
    description: Mapped[str | None] = mapped_column(LONG_TEXT(), nullable=True)
    icon_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    desired_status: Mapped[str] = mapped_column(
        ENUM_CODE("enabled", "disabled"), nullable=False
    )
    generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=1, server_default="1"
    )
    observed_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    runtime_status: Mapped[str] = mapped_column(
        ENUM_CODE("pending", "ready", "degraded", "unhealthy", "failed"),
        nullable=False,
    )
    # DEFERRED: FK enforcement of these version pointers is a later feature;
    # only presence, ID typing and nullability are pinned here (§5.2 L165).
    active_config_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), nullable=True
    )
    active_toolset_id: Mapped[uuid.UUID | None] = mapped_column(ID(), nullable=True)
    agent_auth_mode: Mapped[str] = mapped_column(
        ENUM_CODE("api_key", "none", "oauth2"), nullable=False
    )
    rate_limit_qps: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    rate_limit_burst: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_max_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_timeout_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdio_instance_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stdio_concurrency_per_instance: Mapped[int | None] = mapped_column(
        Integer, nullable=True
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

    # §3.3 soft-delete fields.
    deleted_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ServiceConfigRevision(Base):
    """An immutable desired-config revision for a service (§5.3).

    Content fields (``public_config``, ``secret_blob_id``, ``config_digest``)
    are immutable once created — a config change appends a new revision with
    the next ``generation``. Only the revision's own lifecycle state
    (``state`` / ``validation_report`` / ``activated_at`` / ``superseded_at``)
    may transition in place. Immutable revisions carry generic CREATE fields
    only (``created_at`` / ``created_by``) — no ``updated_*`` / ``row_version``
    (§5.3 L188).
    """

    __tablename__ = "service_config_revision"
    __table_args__ = (
        UniqueConstraint(
            "service_id",
            "generation",
            name="uq_service_config_revision_service_generation",
        ),
        # UNIQUE (id, service_id) backs the mcp_service.active_config_revision_id
        # cross-table ownership constraint (§5.3 / §5.2 L165).
        UniqueConstraint(
            "id", "service_id", name="uq_service_config_revision_id_service"
        ),
        CheckConstraint(
            "config_kind IN ('http_api', 'stdio', 'mcp_http')",
            name="ck_service_config_revision_config_kind",
        ),
        CheckConstraint(
            "source_mode IN "
            "('fastmcp_introspection', 'descriptor', 'manual', 'remote_sync')",
            name="ck_service_config_revision_source_mode",
        ),
        CheckConstraint(
            "state IN ('draft', 'validating', 'validated', 'active', 'rejected', "
            "'superseded')",
            name="ck_service_config_revision_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_kind: Mapped[str] = mapped_column(
        ENUM_CODE("http_api", "stdio", "mcp_http"), nullable=False
    )
    public_config: Mapped[dict] = mapped_column(JSON_DOC(), nullable=False)
    # DEFERRED: FK enforcement of secret_blob_id -> service_secret.id is a later
    # feature; only presence, ID typing and nullability are pinned here (§5.3).
    secret_blob_id: Mapped[uuid.UUID | None] = mapped_column(ID(), nullable=True)
    source_descriptor: Mapped[dict | None] = mapped_column(
        JSON_DOC(), nullable=True
    )
    source_mode: Mapped[str] = mapped_column(
        ENUM_CODE(
            "fastmcp_introspection", "descriptor", "manual", "remote_sync"
        ),
        nullable=False,
    )
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        ENUM_CODE(
            "draft", "validating", "validated", "active", "rejected", "superseded"
        ),
        nullable=False,
    )
    validation_report: Mapped[dict | None] = mapped_column(
        JSON_DOC(), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


class Toolset(Base):
    """An atomic publish unit of MCP Tool definitions for a service (§5.7).

    ``toolset`` records the source of a toolset (manual authoring, FastMCP
    introspection, an uploaded descriptor or a remote MCP server), the server
    ``capabilities`` / ``info`` snapshots and validation outcome, and the
    toolset lifecycle state (staging -> validating -> validated -> active, or
    rejected / retired). A toolset is the unit of atomic publish — only the
    service's active pointer (``mcp_service.active_toolset_id``) designates the
    live one (§5.2 L165).
    """

    __tablename__ = "toolset"
    __table_args__ = (
        UniqueConstraint(
            "service_id", "version_no", name="uq_toolset_service_version"
        ),
        # UNIQUE (id, service_id) backs the mcp_service.active_toolset_id
        # cross-table ownership constraint and the mcp_tool composite FK
        # (toolset_id, service_id) -> toolset(id, service_id) (§5.7 / §5.8).
        UniqueConstraint("id", "service_id", name="uq_toolset_id_service"),
        CheckConstraint(
            "source_kind IN ('manual', 'fastmcp', 'descriptor', 'remote_mcp')",
            name="ck_toolset_source_kind",
        ),
        CheckConstraint(
            "state IN ('staging', 'validating', 'validated', 'active', "
            "'rejected', 'retired')",
            name="ck_toolset_state",
        ),
        CheckConstraint("tool_count >= 0", name="ck_toolset_tool_count"),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("service_config_revision.id"), nullable=True
    )
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_kind: Mapped[str] = mapped_column(
        ENUM_CODE("manual", "fastmcp", "descriptor", "remote_mcp"),
        nullable=False,
    )
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    mcp_protocol_version: Mapped[str] = mapped_column(String(16), nullable=False)
    json_schema_dialect: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        server_default="https://json-schema.org/draft/2020-12/schema",
    )
    server_capabilities: Mapped[dict | None] = mapped_column(
        JSON_DOC(), nullable=True
    )
    server_info: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    instructions: Mapped[str | None] = mapped_column(LONG_TEXT(), nullable=True)
    state: Mapped[str] = mapped_column(
        ENUM_CODE(
            "staging", "validating", "validated", "active", "rejected", "retired"
        ),
        nullable=False,
    )
    validation_report: Mapped[dict | None] = mapped_column(
        JSON_DOC(), nullable=True
    )
    tool_count: Mapped[int] = mapped_column(Integer, nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)

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


class McpTool(Base):
    """A lossless MCP Tool definition owned by a toolset (§5.8).

    ``mcp_tool`` stores the complete Tool object (``raw_definition``) plus the
    parsed ``input_schema`` / ``output_schema``, ``annotations``, ``execution``,
    ``icons``, ``_meta`` and ``http_binding`` so unknown MCP extensions survive
    round-trips. ``enabled`` defaults to true; disabling is a per-toolset local
    opt-out, never an edit of a published definition (§5.8). The composite FK
    ``(toolset_id, service_id) -> toolset(id, service_id)`` guarantees a tool's
    ``service_id`` always equals its toolset's ``service_id``.
    """

    __tablename__ = "mcp_tool"
    __table_args__ = (
        UniqueConstraint("toolset_id", "name", name="uq_mcp_tool_toolset_name"),
        # Composite FK guaranteeing tool.service == toolset.service (§5.8).
        # CASCADE so deleting a toolset also removes its tools (staging cleanup)
        # on both dialects — a NO ACTION composite FK would block the cascade
        # fired by the single-column toolset_id FK on PostgreSQL.
        ForeignKeyConstraint(
            ["toolset_id", "service_id"],
            ["toolset.id", "toolset.service_id"],
            name="fk_mcp_tool_toolset_service",
            ondelete="CASCADE",
        ),
        CheckConstraint("source IN ('manual', 'synced')", name="ck_mcp_tool_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    toolset_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("toolset.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    description: Mapped[str | None] = mapped_column(LONG_TEXT(), nullable=True)
    input_schema: Mapped[dict] = mapped_column(JSON_DOC(), nullable=False)
    output_schema: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    annotations: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    execution: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    icons: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    raw_definition: Mapped[dict] = mapped_column(JSON_DOC(), nullable=False)
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(
        ENUM_CODE("manual", "synced"), nullable=False
    )
    http_binding: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
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


class ServiceArtifact(Base):
    """An immutable build/code artifact for a service (§5.5).

    ``service_artifact`` uniformly records source packages, service
    descriptors, dependency bundles, built images and build logs as immutable
    objects referenced by their storage key and content digest. The object
    bytes live in the ``storage_backend`` (filesystem / s3 / minio / registry);
    the database row only holds the immutable object key, digest and metadata.
    Rows are never soft-deleted — lifecycle is ``state``
    (staging -> available -> quarantined/gc_pending -> deleted), and GC only
    touches ``gc_pending`` artifacts past ``retain_until`` (§5.5 L246). Rows
    carry the full §3.2 audit set (``created_at``/``created_by``/``updated_at``/
    ``updated_by``/``row_version``).
    """

    __tablename__ = "service_artifact"
    __table_args__ = (
        UniqueConstraint(
            "storage_backend",
            "object_key",
            name="uq_service_artifact_storage_object_key",
        ),
        CheckConstraint(
            "kind IN ('source_package', 'descriptor', 'build_bundle', "
            "'container_image', 'build_log')",
            name="ck_service_artifact_kind",
        ),
        CheckConstraint(
            "storage_backend IN ('filesystem', 's3', 'minio', 'registry')",
            name="ck_service_artifact_storage_backend",
        ),
        CheckConstraint(
            "state IN ('staging', 'available', 'quarantined', "
            "'gc_pending', 'deleted')",
            name="ck_service_artifact_state",
        ),
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_service_artifact_size_bytes_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("service_config_revision.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(
        ENUM_CODE(
            "source_package",
            "descriptor",
            "build_bundle",
            "container_image",
            "build_log",
        ),
        nullable=False,
    )
    storage_backend: Mapped[str] = mapped_column(
        ENUM_CODE("filesystem", "s3", "minio", "registry"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(
        ENUM_CODE(
            "staging", "available", "quarantined", "gc_pending", "deleted"
        ),
        nullable=False,
    )
    scan_report: Mapped[dict | None] = mapped_column(
        JSON_DOC(), nullable=True
    )
    retain_until: Mapped[datetime | None] = mapped_column(
        UTC_TS(), nullable=True
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


class BuildRun(Base):
    """A single build attempt for a service (§5.6).

    ``build_run`` records one attempt to turn a ``source_artifact_id`` into a
    validated, published toolset. The first-version strategy is ``fastmcp``;
    ``descriptor`` / ``custom_adapter`` are reserved for later parsers/builders
    but already allowed by the CHECK (§5.6 L258). A run links its input, its
    output and its full build log as ``service_artifact`` rows, and captures
    failures as ``error_code`` / ``error_summary`` (already redacted). Rows
    carry the full §3.2 audit set (``created_at``/``created_by``/``updated_at``/
    ``updated_by``/``row_version``).
    """

    __tablename__ = "build_run"
    __table_args__ = (
        Index(
            "ix_build_run_service_status_created_at",
            "service_id",
            "status",
            "created_at",
        ),
        CheckConstraint(
            "strategy IN ('fastmcp', 'descriptor', 'custom_adapter')",
            name="ck_build_run_strategy",
        ),
        CheckConstraint(
            "status IN ('queued', 'building', 'validating', 'succeeded', "
            "'failed', 'cancelled', 'superseded')",
            name="ck_build_run_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    config_revision_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("service_config_revision.id"), nullable=False
    )
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("service_artifact.id"), nullable=False
    )
    strategy: Mapped[str] = mapped_column(
        ENUM_CODE("fastmcp", "descriptor", "custom_adapter"), nullable=False
    )
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    base_image_digest: Mapped[str] = mapped_column(String(255), nullable=False)
    dependency_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(
        ENUM_CODE(
            "queued",
            "building",
            "validating",
            "succeeded",
            "failed",
            "cancelled",
            "superseded",
        ),
        nullable=False,
    )
    output_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("service_artifact.id"), nullable=True
    )
    discovered_descriptor: Mapped[dict | None] = mapped_column(
        JSON_DOC(), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    log_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("service_artifact.id"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        UTC_TS(), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        UTC_TS(), nullable=True
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


class ServiceCondition(Base):
    """A runtime-observed condition of a service, independent of the user's
    desired config (§5.11).

    ``service_condition`` persists what the system actually observed about a
    service (config applied, build produced, tools ready, runtime healthy,
    upstream reachable) so a transient ``build_status``/``last_error`` can
    never overwrite each other. ``mcp_service.runtime_status`` is the
    queryable summary; the condition rows are the diagnostic source of truth.
    Runtime writes never modify the desired-config ``generation``. One row per
    (service, type), UNIQUE; lifecycle is the ``status`` code, so rows carry
    the full §3.2 audit set and are never soft-deleted.
    """

    __tablename__ = "service_condition"
    __table_args__ = (
        UniqueConstraint(
            "service_id", "type", name="uq_service_condition_service_type"
        ),
        CheckConstraint(
            "type IN ('ConfigReady', 'BuildReady', 'ToolsReady', "
            "'RuntimeHealthy', 'UpstreamReachable')",
            name="ck_service_condition_type",
        ),
        CheckConstraint(
            "status IN ('true', 'false', 'unknown')",
            name="ck_service_condition_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        ENUM_CODE(
            "ConfigReady",
            "BuildReady",
            "ToolsReady",
            "RuntimeHealthy",
            "UpstreamReachable",
        ),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        ENUM_CODE("true", "false", "unknown"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    observed_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_transition_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False
    )
    last_probe_at: Mapped[datetime | None] = mapped_column(
        UTC_TS(), nullable=True
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


class McpTask(Base):
    """An async-operation (MCP Tasks) record for a service tool (§5.15).

    ``mcp_task`` models the MCP Tasks lifecycle used when a tool declares
    ``execution.taskSupport`` as ``optional``/``required`` and the gateway has
    Tasks enabled. ``id`` doubles as the MCP ``taskId``; ``tool_id`` pins the
    exact tool version the task was created against; ``session_id_hash`` never
    stores the raw session token. Terminal states are final (no return to the
    working state) and expired tasks are GC'd, so the table carries the MCP
    time fields (``created_at`` / ``last_updated_at`` / ``expires_at``), NOT
    the §3.2 audit set, and declares no UNIQUE constraints.
    """

    __tablename__ = "mcp_task"
    __table_args__ = (
        CheckConstraint(
            "status IN ('working', 'input_required', 'completed', 'failed', "
            "'cancelled')",
            name="ck_mcp_task_status",
        ),
        CheckConstraint(
            "poll_interval_ms > 0",
            name="ck_mcp_task_poll_interval_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    tool_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_tool.id"), nullable=False
    )
    session_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    downstream_task_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    status: Mapped[str] = mapped_column(
        ENUM_CODE(
            "working", "input_required", "completed", "failed", "cancelled"
        ),
        nullable=False,
    )
    status_message: Mapped[str | None] = mapped_column(
        String(2048), nullable=True
    )
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("service_artifact.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    poll_interval_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class McpServicePermission(Base):
    """An explicit object-level visibility or write grant (§5.12).

    There are no implicit service visibility defaults: an ``everyone`` row is
    itself the explicit public-read grant. ``principal_key`` gives every grant
    a portable, non-null uniqueness value across PostgreSQL and MySQL.
    """

    __tablename__ = "mcp_service_permission"
    __table_args__ = (
        UniqueConstraint(
            "service_id",
            "principal_key",
            name="uq_mcp_service_permission_service_principal_key",
        ),
        CheckConstraint(
            "principal_type IN ('user', 'team', 'everyone')",
            name="ck_mcp_service_permission_principal_type",
        ),
        CheckConstraint(
            "role IN ('editor', 'viewer')",
            name="ck_mcp_service_permission_role",
        ),
        CheckConstraint(
            "(principal_type='user' and user_id is not null) or "
            "(principal_type!='user' and user_id is null)",
            name="ck_mcp_service_permission_user_id_consistency",
        ),
        CheckConstraint(
            "(principal_type='team' and team_id is not null) or "
            "(principal_type!='team' and team_id is null)",
            name="ck_mcp_service_permission_team_id_consistency",
        ),
        CheckConstraint(
            "(principal_type='user') or (role='viewer')",
            name="ck_mcp_service_permission_role_scope",
        ),
        Index(
            "ix_mcp_service_permission_service_principal_type",
            "service_id",
            "principal_type",
        ),
        Index(
            "ix_mcp_service_permission_user_role_service",
            "user_id",
            "role",
            "service_id",
        ),
        Index(
            "ix_mcp_service_permission_team_service",
            "team_id",
            "service_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    principal_type: Mapped[str] = mapped_column(
        ENUM_CODE("user", "team", "everyone"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("user.id"), nullable=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("team.id"), nullable=True
    )
    role: Mapped[str] = mapped_column(ENUM_CODE("editor", "viewer"), nullable=False)
    principal_key: Mapped[str] = mapped_column(String(80), nullable=False)

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


class ApiKey(Base):
    """Persisted non-secret API-key metadata and revocation state (§5.13).

    The plaintext key is never a model field. Only its selector, display
    prefix and one-way digest are persisted, together with lifecycle metadata.
    """

    __tablename__ = "api_key"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_api_key_public_id"),
        UniqueConstraint("secret_hash", name="uq_api_key_secret_hash"),
        CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_api_key_status"
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_api_key_expires_after_created",
        ),
        CheckConstraint(
            "(status!='revoked') OR (revoked_at IS NOT NULL)",
            name="ck_api_key_revoked_requires_revoked_at",
        ),
        CheckConstraint(
            "rate_limit_qps IS NULL OR rate_limit_qps > 0",
            name="ck_api_key_rate_limit_qps_positive",
        ),
        CheckConstraint(
            "rate_limit_burst IS NULL OR rate_limit_burst >= 1",
            name="ck_api_key_rate_limit_burst_min",
        ),
        Index(
            "ix_api_key_service_status_expires_at",
            "service_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ID(), ForeignKey("mcp_service.id", ondelete="RESTRICT"), nullable=False
    )
    public_id: Mapped[str] = mapped_column(String(32), nullable=False)
    display_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    pepper_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        ENUM_CODE("active", "revoked"), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    last_used_ip_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTC_TS(), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        ID(), ForeignKey("user.id"), nullable=True
    )
    rate_limit_qps: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    rate_limit_burst: Mapped[int | None] = mapped_column(Integer, nullable=True)

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


class AuditEvent(Base):
    """An append-only business-evidence record (§5.14).

    ``audit_event`` records who did what to which resource, when, and with
    what result, written in the same transaction as the business change it
    documents (M1-MODEL-008). It is business evidence, not an application log:
    rows are never updated or soft-deleted, so the table carries NO §3.2 audit
    columns — the table itself is the audit record. ``previous_event_hash`` /
    ``event_hash`` form an append-only hash chain. The ``metadata`` column is
    declared under the non-reserved Python attribute ``meta`` because
    ``metadata`` is a reserved name under the Declarative API; the DB column is
    ``metadata`` and contract code reaches it through ``Table.c``.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_event_occurred_at", "occurred_at"),
        Index(
            "ix_audit_event_service_occurred_at",
            "service_id",
            "occurred_at",
        ),
        Index(
            "ix_audit_event_actor_occurred_at",
            "actor_type",
            "actor_id",
            "occurred_at",
        ),
        CheckConstraint(
            "actor_type IN ('user', 'api_key', 'system', 'anonymous')",
            name="ck_audit_event_actor_type",
        ),
        CheckConstraint(
            "result IN ('success', 'denied', 'failed')",
            name="ck_audit_event_result",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[str] = mapped_column(
        ENUM_CODE("user", "api_key", "system", "anonymous"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ID(), nullable=True)
    result: Mapped[str] = mapped_column(
        ENUM_CODE("success", "denied", "failed"), nullable=False
    )
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    changes: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    meta: Mapped[dict | None] = mapped_column(
        "metadata", JSON_DOC(), nullable=True
    )
    previous_event_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Outbox(Base):
    """The controller-adjudicated transactional delivery queue (§6.3).

    ``outbox`` buffers a business event for asynchronous delivery. The row is
    written in the same transaction as the business change it describes, so the
    two commit or roll back together (M1-MODEL-008). ``(service_id,
    requested_generation, operation_kind)`` is the worker-task dedup key and is
    UNIQUE; because NULL != NULL on both dialects, rows where any of the three
    is NULL are not deduped. ``status`` defaults to ``pending`` and
    ``attempt_count`` to 0. The row is delivery-work state, not a mutable
    business entity, so it carries NO §3.2 audit columns.
    """

    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint(
            "service_id",
            "requested_generation",
            "operation_kind",
            name="uq_outbox_service_generation_operation",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_flight', 'done', 'failed')",
            name="ck_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_outbox_attempt_count_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(ID(), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ID(), nullable=True)
    requested_generation: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    operation_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON_DOC(), nullable=True)
    status: Mapped[str] = mapped_column(
        ENUM_CODE("pending", "in_flight", "done", "failed"),
        nullable=False,
        default="pending",
        server_default="'pending'",
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        UTC_TS(), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTC_TS(), nullable=False, default=_now_utc
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        UTC_TS(), nullable=True
    )
