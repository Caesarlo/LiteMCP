"""First-admin bootstrap flow (M2-AUTH-001).

An empty database can only get its first global admin user through this
controlled flow (docs/architecture/02-admin-auth.md §4.1). The flow runs
inside a transaction the caller already opened (``async with
session.begin(): ...``) -- it never calls ``session.begin()`` or
``session.commit()`` itself, since doing so would conflict with the caller's
transaction management.

Concurrency safety: two concurrent bootstrap attempts against the same empty
database must yield exactly one winner. A plain ``SELECT count(*)`` followed
by an ``INSERT`` is not safe -- both transactions could observe zero rows and
both insert. Locking a row of the (possibly empty) ``user`` table does not
help either, since an empty table has no row to lock. Instead this flow takes
``SELECT ... FOR UPDATE`` on the single seeded row of the dedicated
``bootstrap_lock`` table (created by migration ``m2_auth_001_bootstrap_lock``)
before checking whether ``user`` is empty. That row lock is held by the
database itself for the lifetime of the transaction, so it serializes
concurrent bootstrap attempts across separate sessions, separate asyncio
tasks and separate OS processes alike, on both PostgreSQL and MySQL: the
second transaction blocks on the lock until the first commits or rolls back,
then observes the first transaction's committed state and correctly raises
``AlreadyInitializedError``.
"""

from __future__ import annotations

import unicodedata
import uuid
from datetime import UTC, datetime

import argon2
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from litemcp.db.models import User

__all__ = ["AlreadyInitializedError", "bootstrap_first_admin"]

_BOOTSTRAP_ACTOR = "system:bootstrap"

_password_hasher = argon2.PasswordHasher()


class AlreadyInitializedError(RuntimeError):
    """Raised when the bootstrap flow is invoked on a non-empty user table."""

    code: str = "ALREADY_INITIALIZED"

    def __init__(self, message: str = "an admin already exists") -> None:
        super().__init__(message)


def _normalize_username(username: str) -> str:
    """NFKC-normalize, trim and casefold a username for uniqueness checks."""

    return unicodedata.normalize("NFKC", username).strip().casefold()


async def bootstrap_first_admin(
    session: AsyncSession, *, username: str, password: str
) -> User:
    """Create the first global admin user, or raise if one already exists.

    Must run inside a transaction already opened by the caller (``async with
    session.begin(): ...``). Takes a row lock on the dedicated
    ``bootstrap_lock`` table before checking emptiness so the check-then-insert
    is atomic against a concurrent transaction doing the same thing.
    """

    await session.execute(text("SELECT id FROM bootstrap_lock WHERE id = 1 FOR UPDATE"))

    existing_count = await session.scalar(select(func.count()).select_from(User))
    if existing_count:
        raise AlreadyInitializedError()

    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        username=username,
        username_normalized=_normalize_username(username),
        password_hash=_password_hasher.hash(password),
        role="admin",
        status="active",
        password_changed_at=now,
        created_at=now,
        created_by=_BOOTSTRAP_ACTOR,
        updated_at=now,
        updated_by=_BOOTSTRAP_ACTOR,
    )
    session.add(user)
    await session.flush()
    # Detach from the session's identity map so the caller's eventual
    # ``session.commit()`` (from the ``session.begin()`` context it opened)
    # does not expire this object's already-populated attributes. Without
    # this, accessing the returned ``User`` after the caller's session
    # closes raises ``DetachedInstanceError`` on the default
    # ``expire_on_commit=True`` session behavior.
    session.expunge(user)
    return user
