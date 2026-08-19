"""Application-layer refresh token rotation and reuse detection.

docs/architecture/02-admin-auth.md §8.1 / §8.3 / §8.4 / §11 / §17.

Issues a new Access JWT and opaque refresh secret for an existing session,
replacing ``current_secret_hash`` with no grace window for the previous
secret. A presented secret that fails the constant-time compare against a
live (not idle/absolute-expired) session revokes the Redis token family and
persists ``auth.refresh_reuse_detected``. HTTP routes, cookies, CSRF, and
logout remain out of scope.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from litemcp.auth.session import _build_access_token, _session_environment
from litemcp.core.config import Settings
from litemcp.db.models import AuditEvent, User
from litemcp.db.session import AsyncSessionFactory

__all__ = [
    "MalformedRefreshToken",
    "RefreshAbsoluteExpired",
    "RefreshIdleExpired",
    "RefreshPasswordChanged",
    "RefreshRejected",
    "RefreshRotation",
    "RefreshSecretMismatch",
    "RefreshSessionMissing",
    "RefreshUserNotActive",
    "compare_refresh_secret",
    "rotate_refresh_token",
]

_USER_STATUS_ACTIVE = "active"
_REFRESH_SECRET_BYTES = 32
_REUSE_ACTION = "auth.refresh_reuse_detected"
_REUSE_RESULT = "denied"
_REUSE_RESOURCE_TYPE = "admin_session"


class RefreshRejected(Exception):
    """Refresh token was not rotated."""


class MalformedRefreshToken(RefreshRejected):
    """Opaque refresh token is not ``<session_id>.<secret>``."""


class RefreshSessionMissing(RefreshRejected):
    """No Redis session exists for the presented session id."""


class RefreshIdleExpired(RefreshRejected):
    """Session idle timeout has elapsed."""


class RefreshAbsoluteExpired(RefreshRejected):
    """Session absolute timeout has elapsed."""


class RefreshSecretMismatch(RefreshRejected):
    """Presented secret does not match the current session hash."""


class RefreshUserNotActive(RefreshRejected):
    """User is missing or not ``active``."""


class RefreshPasswordChanged(RefreshRejected):
    """Session was created before the user's password changed."""


@dataclass(frozen=True, slots=True)
class RefreshRotation:
    """Newly issued access JWT and rotated opaque refresh token."""

    access_token: str
    refresh_token: str
    expires_in: int


class _RedisHashClient(Protocol):
    async def hset(
        self,
        key: str,
        mapping: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> int: ...

    async def hgetall(self, key: str) -> dict[str, str]: ...

    async def exists(self, key: str) -> int: ...

    async def expire(self, key: str, seconds: int) -> bool: ...

    async def delete(self, *keys: str) -> int: ...

    async def srem(self, name: str, *values: str) -> int: ...


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso(moment: datetime) -> str:
    return _as_utc(moment).isoformat()


def _parse_iso(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value))


def _session_key(settings: Settings, session_id: str) -> str:
    environment = _session_environment(settings)
    return f"litemcp:{environment}:admin_session:{session_id}"


def _user_sessions_key(settings: Settings, user_id: str) -> str:
    environment = _session_environment(settings)
    return f"litemcp:{environment}:user_sessions:{user_id}"


async def _persist_refresh_reuse_audit(
    session_factory: AsyncSessionFactory,
    *,
    session_id: str,
    actor_id: str | None,
    now: datetime,
) -> None:
    event = AuditEvent(
        id=uuid.uuid4(),
        occurred_at=now,
        request_id=str(uuid.uuid4()),
        actor_type="user" if actor_id is not None else "anonymous",
        actor_id=actor_id,
        action=_REUSE_ACTION,
        resource_type=_REUSE_RESOURCE_TYPE,
        resource_id=session_id,
        result=_REUSE_RESULT,
        reason_code="refresh_reuse_detected",
    )
    async with session_factory.session() as session:
        session.add(event)
        await session.commit()


async def _revoke_refresh_family_on_reuse(
    *,
    redis: _RedisHashClient,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    session_id: str,
    user_id_raw: str,
    now: datetime,
) -> None:
    await redis.delete(_session_key(settings, session_id))
    await redis.srem(_user_sessions_key(settings, user_id_raw), session_id)
    actor_id = user_id_raw if _parse_user_id(user_id_raw) is not None else None
    await _persist_refresh_reuse_audit(
        session_factory,
        session_id=session_id,
        actor_id=actor_id,
        now=now,
    )


def _parse_refresh_token(refresh_token: str) -> tuple[str, str]:
    if not isinstance(refresh_token, str):
        raise MalformedRefreshToken
    parts = refresh_token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise MalformedRefreshToken
    return parts[0], parts[1]


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def compare_refresh_secret(*, stored_hash: str, presented_secret: str) -> bool:
    """Constant-time compare of SHA-256(presented secret) to the stored hash."""

    if not isinstance(stored_hash, str) or not isinstance(presented_secret, str):
        return False
    presented_hash = _secret_hash(presented_secret)
    return hmac.compare_digest(stored_hash, presented_hash)


async def _load_user(
    session_factory: AsyncSessionFactory, user_id: uuid.UUID
) -> User | None:
    async with session_factory.session() as session:
        return await session.get(User, user_id)


def _parse_user_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError, AttributeError):
        return None


async def rotate_refresh_token(
    refresh_token: str,
    *,
    redis: _RedisHashClient,
    session_factory: AsyncSessionFactory,
    settings: Settings,
    now: datetime | None = None,
) -> RefreshRotation:
    """Rotate the refresh secret and mint a new Access JWT for the same session."""

    moment = datetime.now(UTC) if now is None else _as_utc(now)
    session_id, presented_secret = _parse_refresh_token(refresh_token)
    key = _session_key(settings, session_id)

    if await redis.exists(key) == 0:
        raise RefreshSessionMissing

    fields = await redis.hgetall(key)
    if not fields:
        raise RefreshSessionMissing

    try:
        created_at = _parse_iso(fields["created_at"])
        idle_expires_at = _parse_iso(fields["idle_expires_at"])
        absolute_expires_at = _parse_iso(fields["absolute_expires_at"])
        stored_hash = fields["current_secret_hash"]
        user_id_raw = fields["user_id"]
    except (KeyError, ValueError, TypeError) as exc:
        raise RefreshSessionMissing from exc

    if moment >= absolute_expires_at:
        raise RefreshAbsoluteExpired
    if moment >= idle_expires_at:
        raise RefreshIdleExpired

    if not compare_refresh_secret(
        stored_hash=stored_hash, presented_secret=presented_secret
    ):
        await _revoke_refresh_family_on_reuse(
            redis=redis,
            session_factory=session_factory,
            settings=settings,
            session_id=session_id,
            user_id_raw=user_id_raw,
            now=moment,
        )
        raise RefreshSecretMismatch

    idle_ttl = timedelta(seconds=settings.admin_refresh_idle_ttl_seconds)
    next_idle = min(moment + idle_ttl, absolute_expires_at)
    last_refreshed = _iso(moment)
    idle_expires = _iso(next_idle)

    new_secret = secrets.token_urlsafe(_REFRESH_SECRET_BYTES)
    new_hash = _secret_hash(new_secret)
    # Write the new hash (and idle timestamps) before any other await so a
    # concurrent presenter of the old secret cannot mint. Commands interleave,
    # so confirm we still own the hash before issuing tokens.
    await redis.hset(
        key,
        mapping={
            "current_secret_hash": new_hash,
            "last_refreshed_at": last_refreshed,
            "idle_expires_at": idle_expires,
        },
    )
    confirmed = await redis.hgetall(key)
    if confirmed.get("current_secret_hash") != new_hash:
        raise RefreshSecretMismatch

    remaining_absolute = max(1, int((absolute_expires_at - moment).total_seconds()))
    await redis.expire(key, remaining_absolute)

    user_id = _parse_user_id(user_id_raw)
    if user_id is None:
        raise RefreshUserNotActive

    user = await _load_user(session_factory, user_id)
    if user is None or user.status != _USER_STATUS_ACTIVE:
        raise RefreshUserNotActive
    if created_at < _as_utc(user.password_changed_at):
        raise RefreshPasswordChanged

    access_token, expires_in = _build_access_token(
        user=user, session_id=session_id, settings=settings
    )
    return RefreshRotation(
        access_token=access_token,
        refresh_token=f"{session_id}.{new_secret}",
        expires_in=expires_in,
    )
