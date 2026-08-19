"""Login-success contract: Access JWT + Refresh Session (M2-AUTH-004).

docs/architecture/02-admin-auth.md §7.1/§7.2/§8.1/§8.3 (verbatim
translation): "Valid credentials return a short-lived Access JWT and
establish a Refresh Session."

Scope: this module covers only what happens *after* a caller has already
confirmed the submitted password is correct (that verification lives in
M2-AUTH-002/003). There is no HTTP login endpoint here -- ``create_login_session``
is the single function a future login endpoint will call once the password
has been confirmed. Out of scope: login failure lockout/rate limiting,
access-JWT *verification* (a separate auth dependency), refresh rotation,
refresh replay detection, logout/revocation, and Redis failure fallback.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import redis.asyncio as redis_asyncio

from litemcp.core.config import get_settings
from litemcp.db.models import User

__all__ = ["LoginSession", "create_login_session"]

_JWT_ALGORITHM = "HS256"
_JWT_TYPE = "at+jwt"
_JWT_TOKEN_TYPE = "access"

# session_id: >=128 bits of CSPRNG, URL-safe (also used verbatim as the
# Redis key segment and as the first half of the "<session_id>.<secret>"
# refresh token, so it must never contain a "." itself).
_SESSION_ID_BYTES = 16
# random_secret: >=256 bits of CSPRNG.
_REFRESH_SECRET_BYTES = 32


@dataclass(frozen=True, slots=True)
class LoginSession:
    """Result of a successful login: an Access JWT plus a Refresh Session."""

    access_token: str
    expires_in: int
    refresh_token: str
    session_id: str


def _session_environment(settings: object) -> str:
    """Resolve the Redis key namespace segment for admin sessions.

    ``admin_session_environment`` is blank by default, in which case the
    general deployment ``environment`` (dev/test/prod) is used instead.
    """

    configured = settings.admin_session_environment  # type: ignore[attr-defined]
    if configured:
        return configured  # type: ignore[no-any-return]
    return settings.environment.value  # type: ignore[attr-defined]


def _build_access_token(*, user: User, session_id: str, settings: object) -> tuple[str, int]:
    now = datetime.now(UTC)
    iat = int(now.timestamp())
    ttl = settings.admin_access_ttl_seconds  # type: ignore[attr-defined]
    exp = iat + ttl

    claims = {
        "iss": settings.admin_jwt_issuer,  # type: ignore[attr-defined]
        "aud": settings.admin_jwt_audience,  # type: ignore[attr-defined]
        "sub": str(user.id),
        "sid": session_id,
        "jti": secrets.token_hex(16),
        "iat": iat,
        "nbf": iat,
        "exp": exp,
        "token_type": _JWT_TOKEN_TYPE,
    }
    headers = {
        "alg": _JWT_ALGORITHM,
        "typ": _JWT_TYPE,
        "kid": settings.admin_jwt_kid,  # type: ignore[attr-defined]
    }
    secret = settings.admin_jwt_secret.get_secret_value()  # type: ignore[attr-defined]
    token = jwt.encode(claims, secret, algorithm=_JWT_ALGORITHM, headers=headers)
    return token, ttl


async def create_login_session(
    redis_client: redis_asyncio.Redis, user: User
) -> LoginSession:
    """Issue an Access JWT and establish a Redis-backed Refresh Session.

    Must only be called after the caller has already verified ``user``'s
    password. Returns a :class:`LoginSession` carrying the encoded access
    token, its TTL, the opaque refresh token, and the session id.
    """

    settings = get_settings()

    session_id = secrets.token_urlsafe(_SESSION_ID_BYTES)
    random_secret = secrets.token_urlsafe(_REFRESH_SECRET_BYTES)
    refresh_token = f"{session_id}.{random_secret}"
    secret_hash = hashlib.sha256(random_secret.encode()).hexdigest()

    access_token, expires_in = _build_access_token(
        user=user, session_id=session_id, settings=settings
    )

    now = datetime.now(UTC)
    idle_expires_at = now + timedelta(seconds=settings.admin_refresh_idle_ttl_seconds)
    absolute_expires_at = now + timedelta(
        seconds=settings.admin_refresh_absolute_ttl_seconds
    )

    environment = _session_environment(settings)
    user_id = str(user.id)
    session_key = f"litemcp:{environment}:admin_session:{session_id}"
    user_sessions_key = f"litemcp:{environment}:user_sessions:{user_id}"

    pipe = redis_client.pipeline()
    pipe.hset(
        session_key,
        mapping={
            "user_id": user_id,
            "current_secret_hash": secret_hash,
            "created_at": now.isoformat(),
            "last_refreshed_at": now.isoformat(),
            "idle_expires_at": idle_expires_at.isoformat(),
            "absolute_expires_at": absolute_expires_at.isoformat(),
        },
    )
    pipe.expire(session_key, settings.admin_refresh_absolute_ttl_seconds)
    pipe.sadd(user_sessions_key, session_id)
    await pipe.execute()

    return LoginSession(
        access_token=access_token,
        expires_in=expires_in,
        refresh_token=refresh_token,
        session_id=session_id,
    )
