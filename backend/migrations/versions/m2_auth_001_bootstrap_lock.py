"""M2-AUTH-001: create the bootstrap_lock table for first-admin bootstrap.

The first-admin bootstrap flow (docs/architecture/02-admin-auth.md §4.1) must
serialize concurrent "is the user table empty" checks against a concurrent
transaction doing the same check, on both PostgreSQL and MySQL, and across
separate OS processes -- not just concurrent asyncio tasks in one process.

A plain ``SELECT count(*) FROM user`` followed by an ``INSERT`` is not safe:
two concurrent transactions can both observe zero rows and both insert. Row
locking via ``SELECT ... FOR UPDATE`` on the ``user`` table itself does not
help either, because an empty table has no rows to lock.

This migration creates a tiny dedicated ``bootstrap_lock`` table with exactly
one seeded row. The bootstrap flow takes ``SELECT ... FOR UPDATE`` on that row
inside its transaction before checking whether ``user`` is empty; a second
concurrent transaction blocks on that row lock until the first commits or
rolls back, then correctly observes the first transaction's outcome.

Revision ID: m2_auth_001_bootstrap_lock
Revises: m1_delete_001_soft_delete
Create Date: 2026-08-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m2_auth_001_bootstrap_lock"
down_revision: str | Sequence[str] | None = "m1_delete_001_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create bootstrap_lock and seed its single lock row."""
    bootstrap_lock = op.create_table(
        "bootstrap_lock",
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    op.bulk_insert(bootstrap_lock, [{"id": 1}])


def downgrade() -> None:
    """Drop the bootstrap_lock table."""
    op.drop_table("bootstrap_lock")
