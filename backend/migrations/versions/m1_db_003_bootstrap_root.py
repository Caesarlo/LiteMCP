"""M1-DB-003 bootstrap root revision.

Hand-written graph root of the LiteMCP Alembic migration repository. This
revision deliberately creates no tables: it exists so the repository has
exactly one head that a fresh, empty database can be upgraded to. Domain-entity
migrations land in later M1-MODEL-* features and chain onto this root via
``down_revision``.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "m1_db_003_bootstrap_root"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: the bootstrap root establishes the single migration head."""
    pass


def downgrade() -> None:
    """No-op: mirror of ``upgrade()`` for the bootstrap root."""
    pass
