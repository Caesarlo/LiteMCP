"""Database repositories for service persistence.

The service update helper in this module deliberately performs the version
check and increment in one SQL ``UPDATE`` statement.  Keeping the compare and
swap in the database means concurrent sessions cannot both overwrite a row
that they read at the same ``row_version``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from litemcp.db.models import Service

__all__ = ["ConcurrentModificationError", "ServiceRepository"]


class ConcurrentModificationError(RuntimeError):
    """Raised when a service update uses a stale ``row_version``."""

    code = "CONCURRENT_MODIFICATION"

    def __init__(self, message: str = "service was modified concurrently") -> None:
        super().__init__(message)


class ServiceRepository:
    """Persistence operations for ``mcp_service``."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def update_with_row_version(
        self,
        service_id: Any,
        expected: int,
        public_fields: Mapping[str, object],
    ) -> bool:
        """Update public service fields using an atomic version compare-and-swap.

        The row is updated only when its id and current version both match the
        supplied values.  The version expression is evaluated by the database,
        so two concurrent writers using the same version have exactly one
        affected row.  A zero-row update is surfaced as the stable domain
        conflict rather than silently allowing a stale overwrite.
        """

        values = dict(public_fields)
        # ``row_version`` is controlled by this method; callers cannot supply a
        # value that would bypass the atomic increment.
        values.pop("row_version", None)
        values["row_version"] = Service.row_version + 1

        statement = (
            update(Service)
            .where(
                Service.id == service_id,
                Service.row_version == expected,
                Service.deleted_at.is_(None),
            )
            .values(values)
        )
        result = cast(CursorResult[Any], await self.session.execute(statement))
        if result.rowcount != 1:
            raise ConcurrentModificationError()
        return True
