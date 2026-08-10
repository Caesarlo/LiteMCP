"""User, team, team-membership, service, service-config-revision, toolset and
MCP-tool domain models (M1-MODEL-001/002/003/004).

Maps the ``user``, ``team``, ``team_membership``, ``mcp_service``,
``service_config_revision``, ``toolset`` and ``mcp_tool`` tables declared in
docs/architecture/01-data-model.md §5.1, §5.16, §5.17, §5.2, §5.3, §5.7 and
§5.8 onto SQLAlchemy ORM classes. Every mutable table carries the generic
audit fields (§3.2) and its DB-level UNIQUE / CHECK / FK constraints (L63:
enforcement lives in the database, never in a Python-side value check);
immutable config revisions carry generic CREATE fields only — no
``updated_*`` / ``row_version`` (§5.3).

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
