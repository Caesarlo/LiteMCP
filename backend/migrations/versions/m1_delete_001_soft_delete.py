"""M1-DELETE-001: enforce portable soft-delete uniqueness scope semantics.

The service name uniqueness key includes ``uniqueness_scope`` so a tombstone
can be retained while a replacement service reuses the name.  This CHECK keeps
that scope synchronized with ``deleted_at`` on both PostgreSQL and MySQL,
without relying on dialect-specific partial indexes.

Revision ID: m1_delete_001_soft_delete
Revises: m1_model_008_audit_outbox
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m1_delete_001_soft_delete"
down_revision: str | Sequence[str] | None = "m1_model_008_audit_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_mcp_service_deleted_scope_consistency",
        "mcp_service",
        "(deleted_at IS NULL AND uniqueness_scope = 'LIVE') OR "
        "(deleted_at IS NOT NULL AND uniqueness_scope <> 'LIVE')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_mcp_service_deleted_scope_consistency",
        "mcp_service",
        type_="check",
    )
