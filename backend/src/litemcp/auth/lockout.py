"""DB-layer login lockout (M2-AUTH-005).

docs/architecture/02-admin-auth.md §6.1 / §6.3 / §18: after a configurable
number of failures inside an observation window, the user row is written
``status=locked`` with ``locked_until``. A successful login zeros the
failure count and window. ``disabled`` outranks lock expiry and must never
be auto-reactivated. An expired lock is cleared back to ``active`` (count
and window reset) *before* the current password attempt is processed.

Every function runs inside a transaction the caller already opened
(``async with session.begin(): ...``) -- it never calls ``session.begin()``
or ``session.commit()`` itself. Each call loads the target row with
``SELECT ... FOR UPDATE`` so concurrent failures cannot drop counts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from litemcp.core.config import get_settings
from litemcp.db.models import User

__all__ = [
    "LoginAttemptDecision",
    "prepare_login_attempt",
    "record_login_failure",
    "record_login_success",
]


@dataclass(frozen=True, slots=True)
class LoginAttemptDecision:
    """Pre-verify gate: whether the caller may check a password for this user."""

    may_verify_password: bool
    denial_reason: str | None
    user: User


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _lock_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    result = await session.execute(
        select(User).where(User.id == user_id).with_for_update()
    )
    return result.scalar_one()


def _clear_lockout_state(user: User) -> None:
    user.status = "active"
    user.failed_login_count = 0
    user.failed_login_window_started_at = None
    user.locked_until = None


def _lock_is_still_active(user: User, now: datetime) -> bool:
    locked_until = _as_utc(user.locked_until)
    return locked_until is not None and locked_until > now


async def prepare_login_attempt(
    session: AsyncSession, user_id: uuid.UUID
) -> LoginAttemptDecision:
    """Row-lock the user, clear an expired lock, then decide if verify is allowed."""

    user = await _lock_user(session, user_id)
    now = _now_utc()

    if user.status == "disabled":
        return LoginAttemptDecision(
            may_verify_password=False,
            denial_reason="disabled",
            user=user,
        )

    if user.status == "locked":
        if _lock_is_still_active(user, now):
            return LoginAttemptDecision(
                may_verify_password=False,
                denial_reason="locked",
                user=user,
            )
        _clear_lockout_state(user)

    return LoginAttemptDecision(
        may_verify_password=True,
        denial_reason=None,
        user=user,
    )


async def record_login_failure(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Record one failed attempt; lock when the configured threshold is reached."""

    user = await _lock_user(session, user_id)
    settings = get_settings()
    now = _now_utc()
    window_started = _as_utc(user.failed_login_window_started_at)
    window = timedelta(seconds=settings.admin_login_failure_window_seconds)

    if window_started is None or now - window_started >= window:
        user.failed_login_count = 1
        user.failed_login_window_started_at = now
    else:
        user.failed_login_count += 1

    if user.failed_login_count >= settings.admin_login_failure_threshold:
        user.status = "locked"
        user.locked_until = now + timedelta(seconds=settings.admin_lock_seconds)

    return user


async def record_login_success(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Zero failure counters and leave the user unlocked after a successful login."""

    user = await _lock_user(session, user_id)
    _clear_lockout_state(user)
    return user
