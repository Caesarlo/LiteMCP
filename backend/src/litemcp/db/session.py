"""Async database session factory (M1-DB-001).

Every request or job receives its own independent ``AsyncSession`` bound to
the factory's engine, and each session is reliably released — on normal exit
and on exception — so pooled connections never leak between callers. The
process-wide settings singleton (``get_settings()``) is the source of the
configured database URL.

Contract (docs/architecture/08-implementation-plan.md L30, L117;
docs/architecture/09-verification.md L34):

- ``AsyncSessionFactory(database_url, *, engine_kwargs=None)`` builds an
  async engine from the URL; the engine is exposed as ``factory.engine``.
- ``async with factory.session() as s`` yields a NEW independent
  ``AsyncSession`` and closes it on both normal exit and exception, returning
  the pooled connection.
- ``await factory.dispose()`` releases the engine's pooled connections
  (idempotent).
- ``get_session_factory()`` returns a factory wired from
  ``get_settings().database_url``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from litemcp.core.config import get_settings


class AsyncSessionFactory:
    """Hands each caller an independent, reliably-released async session."""

    def __init__(
        self,
        database_url: str,
        *,
        engine_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Build an async engine from ``database_url``, applying engine kwargs."""
        self.engine: AsyncEngine = create_async_engine(
            database_url, **(engine_kwargs or {})
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a new ``AsyncSession`` bound to the factory's engine.

        The session is closed in a ``finally`` block so the pooled connection
        is released on normal exit and on exception alike.
        """
        # ``expire_on_commit=False`` keeps committed attribute values on the
        # instance so callers can read them back without a session round-trip
        # (M1-MODEL-001 model contract reads committed objects after the
        # session is released).
        session = AsyncSession(bind=self.engine, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()

    async def dispose(self) -> None:
        """Dispose the engine, releasing its pooled connections (idempotent)."""
        await self.engine.dispose()


def get_session_factory() -> AsyncSessionFactory:
    """Return a factory configured from the process-wide settings database URL.

    Not cached: settings may change (tests clear ``get_settings``'s cache),
    so each call reads the current settings and builds a fresh factory.
    """
    return AsyncSessionFactory(get_settings().database_url.get_secret_value())
