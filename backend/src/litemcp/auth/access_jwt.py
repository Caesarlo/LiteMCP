"""Application-layer Access JWT verification (M2-AUTH-006).

docs/architecture/02-admin-auth.md §7.2, §7.3, §10, §11, §18.

This module verifies signature, issuer, audience, expiry, and current user
status. It does not implement HTTP ``get_current_user``, refresh rotation,
jti denylist, audit persistence, or CSRF.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

import jwt
from jwt.exceptions import InvalidTokenError, PyJWTError

from litemcp.core.config import Settings, get_settings
from litemcp.db.models import User
from litemcp.db.session import get_session_factory

__all__ = ["AccessAuthContext", "AccessJwtError", "verify_access_jwt"]

_TYP_ACCESS = "at+jwt"
_TOKEN_TYPE_ACCESS = "access"
_USER_STATUS_ACTIVE = "active"
# Bound well below the ~32 KiB padded tokens the contract rejects.
_MAX_TOKEN_BYTES = 8 * 1024


class AccessJwtError(Exception):
    """Access JWT verification failed.

    ``code`` is one of ``INVALID_TOKEN``, ``USER_NOT_ACTIVE``,
    ``TOKEN_STALE``, or ``DATABASE_UNAVAILABLE``.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AccessAuthContext:
    """Authenticated principal derived from a verified access JWT plus DB state."""

    user_id: uuid.UUID
    username: str
    role: str
    sid: str
    jti: str


def _invalid() -> NoReturn:
    raise AccessJwtError("INVALID_TOKEN")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * ((4 - len(segment) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (ValueError, TypeError):
        _invalid()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate jwt claim")
        result[key] = value
    return result


def _load_json_object(raw: bytes) -> dict[str, Any]:
    try:
        parsed: object = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (ValueError, TypeError, json.JSONDecodeError):
        _invalid()
    if not isinstance(parsed, dict):
        _invalid()
    return parsed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _verification_keyring(settings: Settings) -> dict[str, str]:
    keys = dict(settings.admin_jwt_previous_keys)
    keys[settings.admin_jwt_kid] = settings.admin_jwt_secret.get_secret_value()
    return keys


def _parse_header_and_payload(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parts = token.split(".")
    if len(parts) != 3:
        _invalid()
    header = _load_json_object(_b64url_decode(parts[0]))
    payload = _load_json_object(_b64url_decode(parts[1]))
    return header, payload


def _configured_algorithm(settings: Settings) -> str:
    algorithm = settings.admin_jwt_algorithm
    if not algorithm or algorithm.lower() == "none":
        _invalid()
    return algorithm


def _require_str_claim(claims: dict[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        _invalid()
    return value


def _parse_sub(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        _invalid()
    try:
        return uuid.UUID(value)
    except ValueError:
        _invalid()


async def _load_active_user(user_id: uuid.UUID) -> User | None:
    factory = get_session_factory()
    try:
        async with factory.session() as session:
            return await session.get(User, user_id)
    except AccessJwtError:
        raise
    except Exception as exc:
        raise AccessJwtError("DATABASE_UNAVAILABLE") from exc
    finally:
        await factory.dispose()


async def verify_access_jwt(token: str) -> AccessAuthContext:
    """Verify an Access JWT and return the database-backed auth context."""

    if not isinstance(token, str) or not token:
        _invalid()
    if len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
        _invalid()

    settings = get_settings()
    header, _payload = _parse_header_and_payload(token)

    algorithm = _configured_algorithm(settings)
    if header.get("alg") != algorithm:
        _invalid()
    if header.get("typ") != _TYP_ACCESS:
        _invalid()

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        _invalid()
    secret = _verification_keyring(settings).get(kid)
    if secret is None:
        _invalid()

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            issuer=settings.admin_jwt_issuer,
            audience=settings.admin_jwt_audience,
            leeway=settings.admin_jwt_clock_skew_seconds,
            options={
                "require": ["exp", "nbf", "iat", "iss", "aud", "sub"],
                "verify_signature": True,
            },
        )
    except (InvalidTokenError, PyJWTError, ValueError, TypeError, KeyError):
        _invalid()

    if not isinstance(claims, dict):
        _invalid()
    if claims.get("token_type") != _TOKEN_TYPE_ACCESS:
        _invalid()

    user_id = _parse_sub(claims.get("sub"))
    sid = _require_str_claim(claims, "sid")
    jti = _require_str_claim(claims, "jti")
    iat = claims.get("iat")
    if not isinstance(iat, int | float):
        _invalid()

    user = await _load_active_user(user_id)
    if user is None or user.status != _USER_STATUS_ACTIVE:
        raise AccessJwtError("USER_NOT_ACTIVE")

    issued_at = datetime.fromtimestamp(float(iat), tz=UTC)
    if issued_at < _as_utc(user.password_changed_at):
        raise AccessJwtError("TOKEN_STALE")

    return AccessAuthContext(
        user_id=user.id,
        username=user.username,
        role=user.role,
        sid=sid,
        jti=jti,
    )
