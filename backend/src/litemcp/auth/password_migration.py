"""Legacy bcrypt password upgrade-on-login (M2-AUTH-003).

docs/architecture/02-admin-auth.md §5.2 (verbatim translation): "If
migrating existing bcrypt hashes, upon successful verification immediately
upgrade to Argon2id. bcrypt is only for legacy-data compatibility, not the
default for new passwords."

This module is a pure DB-layer verification+migration function -- there is
no login/HTTP endpoint yet, mirroring how M2-AUTH-001 (bootstrap) and
M2-AUTH-002 (Argon2id hashing) were built as DB/hashing-layer functions
before any endpoint exists. Login rate-limiting, account lockout, password
length/blocklist rules, and the pepper/Secret-Manager requirement are
explicitly out of scope here.
"""

from __future__ import annotations

import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession

from litemcp.auth.password import (
    PasswordVerificationError,
    hash_password,
    verify_password,
)
from litemcp.db.models import User

__all__ = ["verify_and_migrate_password"]

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


async def verify_and_migrate_password(
    session: AsyncSession, user: User, password: str
) -> bool:
    """Verify ``password`` against ``user``'s stored hash, upgrading bcrypt.

    Must run inside a transaction already opened by the caller (``async with
    session.begin(): ...``) -- it never calls ``session.begin()`` or
    ``session.commit()`` itself, following the ``bootstrap.py`` convention.

    - If ``user.password_hash`` is an Argon2id hash (``$argon2id$`` prefix),
      delegates directly to ``litemcp.auth.password.verify_password``. No DB
      write either way; a mismatch propagates ``PasswordVerificationError``.
    - If ``user.password_hash`` is a bcrypt hash (``$2a$``/``$2b$``/``$2y$``
      prefix), verifies with the real ``bcrypt`` library. On success,
      immediately rehashes the plaintext to Argon2id and mutates
      ``user.password_hash`` in place so SQLAlchemy's unit-of-work persists
      it on the caller's eventual commit. On failure, raises
      ``PasswordVerificationError`` and leaves ``user.password_hash``
      completely untouched, in memory and in the database.

    Returns ``True`` only when ``password`` genuinely matches the stored
    hash; every failure mode raises ``PasswordVerificationError`` rather
    than returning ``False``.
    """

    stored_hash = user.password_hash

    if stored_hash.startswith(_BCRYPT_PREFIXES):
        if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
            raise PasswordVerificationError()
        user.password_hash = hash_password(password)
        return True

    return verify_password(stored_hash, password)
