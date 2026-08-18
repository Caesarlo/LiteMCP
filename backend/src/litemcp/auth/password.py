"""Argon2id password storage (M2-AUTH-002).

New passwords are hashed with configurable Argon2id parameters and stored
as self-describing ``$argon2id$...`` PHC strings (docs/architecture/
02-admin-auth.md §5.2). Verification runs through a single, non-branching
path: every failure mode -- wrong password, malformed hash, well-formed
hash from a different algorithm, empty string -- raises the same
``PasswordVerificationError``, so a caller cannot distinguish *why*
verification failed from the exception type alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import argon2
import argon2.exceptions
from pydantic import ValidationError

from litemcp.core.config import get_settings

__all__ = [
    "Argon2Parameters",
    "PasswordVerificationError",
    "hash_password",
    "verify_password",
]


class PasswordVerificationError(RuntimeError):
    """Raised for any password verification failure, uniformly.

    Wrong password, malformed hash, wrong-algorithm hash, and empty hash
    all raise this same exception type so callers cannot infer the cause
    from the exception class.
    """

    code: str = "PASSWORD_VERIFICATION_FAILED"

    def __init__(self, message: str = "password verification failed") -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Argon2Parameters:
    """Configurable Argon2id cost parameters."""

    memory_cost_kib: int
    time_cost: int
    parallelism: int


_BASELINE_MEMORY_COST_KIB = 19456
_BASELINE_TIME_COST = 2
_BASELINE_PARALLELISM = 1


def _default_parameters() -> Argon2Parameters:
    """Build default parameters from process settings.

    Falls back to the hardcoded baseline floor when application settings
    cannot be constructed (e.g. required infrastructure configuration such
    as ``LITEMCP_DATABASE_URL`` is absent, as in pure hashing unit tests
    that never touch the database). This keeps password hashing usable
    standalone while still reading real deployment configuration when it
    is available.
    """

    try:
        settings = get_settings()
    except ValidationError:
        return Argon2Parameters(
            memory_cost_kib=_BASELINE_MEMORY_COST_KIB,
            time_cost=_BASELINE_TIME_COST,
            parallelism=_BASELINE_PARALLELISM,
        )
    return Argon2Parameters(
        memory_cost_kib=settings.argon2_memory_cost_kib,
        time_cost=settings.argon2_time_cost,
        parallelism=settings.argon2_parallelism,
    )


def _build_hasher(parameters: Argon2Parameters) -> argon2.PasswordHasher:
    return argon2.PasswordHasher(
        time_cost=parameters.time_cost,
        memory_cost=parameters.memory_cost_kib,
        parallelism=parameters.parallelism,
    )


def hash_password(password: str, *, parameters: Argon2Parameters | None = None) -> str:
    """Hash ``password`` into a ``$argon2id$...`` PHC string.

    With no ``parameters`` override, reads the current Argon2id defaults
    from application settings. Each call uses an independently generated
    random salt, so hashing the same password twice yields two different,
    each independently verifiable, hashes.
    """

    resolved = parameters if parameters is not None else _default_parameters()
    return _build_hasher(resolved).hash(password)


def verify_password(hashed: str, password: str) -> bool:
    """Verify ``password`` against a stored ``$argon2id$...`` PHC ``hashed`` string.

    Returns ``True`` for a correct password. Every failure mode -- a wrong
    password, a malformed/garbage hash string, a well-formed hash from a
    different algorithm, or an empty hash string -- raises the same
    ``PasswordVerificationError``, never a raw ``argon2.exceptions.*``
    exception.
    """

    hasher = argon2.PasswordHasher()
    try:
        return hasher.verify(hashed, password)
    except (
        argon2.exceptions.VerificationError,
        argon2.exceptions.InvalidHashError,
        argon2.exceptions.InvalidHash,
    ) as exc:
        raise PasswordVerificationError() from exc
