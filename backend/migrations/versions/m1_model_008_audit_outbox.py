"""M1-MODEL-008: create the audit_event and outbox tables.

Creates the append-only business-evidence ledger (``audit_event``,
docs/architecture/01-data-model.md §5.14) and the controller-adjudicated
transactional delivery queue (``outbox``, 03-service-crud.md §6.3).

``audit_event`` records who did what to which resource, when, and with what
result, written in the same transaction as the business change it documents.
It is business evidence, not an application log: rows are never updated or
soft-deleted, so the table carries NO §3.2 audit columns — the table itself is
the audit record. ``previous_event_hash`` / ``event_hash`` form an append-only
hash chain. ``service_id`` is ID-typed but carries no FK, and the
``actor_type`` / ``result`` codes are enforced by named CHECK constraints.

``outbox`` buffers a business event for asynchronous delivery, written in the
same transaction as the change it describes. ``(service_id,
requested_generation, operation_kind)`` is the worker-task dedup key and is
UNIQUE; because NULL != NULL on both dialects, rows where any of the three is
NULL are not deduped. ``status`` defaults to ``pending``, ``attempt_count`` to
0 (guarded ``>= 0``). ``service_id`` is ID-typed with no FK, and the row is
delivery-work state, so it carries NO §3.2 audit columns.

DDL stays portable across PostgreSQL and MySQL by using only the logical types
from ``litemcp.db.types`` (``ID``, ``UTC_TS``, ``JSON_DOC``, ``ENUM_CODE``)
plus portable scalar SQLAlchemy types. Enum-like values and invariants are
enforced with database CHECK constraints on both dialects.

Revision ID: m1_model_008_audit_outbox
Revises: m1_model_007_permission_key
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

from litemcp.db.types import ENUM_CODE, ID, JSON_DOC, UTC_TS

# revision identifiers, used by Alembic.
revision: str = "m1_model_008_audit_outbox"
down_revision: Union[str, Sequence[str], None] = "m1_model_007_permission_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the audit_event table, then the outbox table."""
    op.create_table(
        "audit_event",
        sa.Column("id", ID(), primary_key=True),
        sa.Column("occurred_at", UTC_TS(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column(
            "actor_type",
            ENUM_CODE("user", "api_key", "system", "anonymous"),
            sa.CheckConstraint(
                "actor_type IN ('user', 'api_key', 'system', 'anonymous')",
                name="ck_audit_event_actor_type",
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("service_id", ID(), nullable=True),
        sa.Column(
            "result",
            ENUM_CODE("success", "denied", "failed"),
            sa.CheckConstraint(
                "result IN ('success', 'denied', 'failed')",
                name="ck_audit_event_result",
            ),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("source_ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(1024), nullable=True),
        sa.Column("changes", JSON_DOC(), nullable=True),
        sa.Column("metadata", JSON_DOC(), nullable=True),
        sa.Column("previous_event_hash", sa.String(64), nullable=True),
        sa.Column("event_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_audit_event_occurred_at",
        "audit_event",
        ["occurred_at"],
    )
    op.create_index(
        "ix_audit_event_service_occurred_at",
        "audit_event",
        ["service_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_event_actor_occurred_at",
        "audit_event",
        ["actor_type", "actor_id", "occurred_at"],
    )

    op.create_table(
        "outbox",
        sa.Column("id", ID(), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("service_id", ID(), nullable=True),
        sa.Column("requested_generation", sa.BigInteger(), nullable=True),
        sa.Column("operation_kind", sa.String(64), nullable=True),
        sa.Column("payload", JSON_DOC(), nullable=True),
        sa.Column(
            "status",
            ENUM_CODE("pending", "in_flight", "done", "failed"),
            sa.CheckConstraint(
                "status IN ('pending', 'in_flight', 'done', 'failed')",
                name="ck_outbox_status",
            ),
            nullable=False,
            server_default="'pending'",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            sa.CheckConstraint(
                "attempt_count >= 0",
                name="ck_outbox_attempt_count_non_negative",
            ),
            nullable=False,
            server_default="0",
        ),
        sa.Column("next_attempt_at", UTC_TS(), nullable=True),
        sa.Column("last_error", sa.String(2048), nullable=True),
        sa.Column("created_at", UTC_TS(), nullable=False),
        sa.Column("processed_at", UTC_TS(), nullable=True),
        sa.UniqueConstraint(
            "service_id",
            "requested_generation",
            "operation_kind",
            name="uq_outbox_service_generation_operation",
        ),
    )


def downgrade() -> None:
    """Drop the outbox table, then the audit_event table."""
    op.drop_table("outbox")
    op.drop_table("audit_event")
