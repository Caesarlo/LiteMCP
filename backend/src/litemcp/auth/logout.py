"""Application-layer refresh-session logout / revoke (M2-AUTH-009).

docs/architecture/02-admin-auth.md §8.3 / §11 / §17.

Ordinary logout deletes the current Redis admin session and removes its id
from ``user_sessions``. Logout-all deletes every session in that set (and
the set itself). Access JWT ``jti`` values are not denylisted. HTTP routes,
cookies, and CSRF remain out of scope.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from litemcp.auth.refresh import compare_refresh_secret

__all__ = [
    "LogoutRejected",
    "logout_all_sessions",
    "logout_current_session",
]

_LOGOUT_ACTION = "auth.logout"
_LOGOUT_ALL_ACTION = "auth.logout_all"
_LOGOUT_RESULT = "success"
_RESOURCE_TYPE = "admin_session"


class LogoutRejected(Exception):
    """Current-session logout could not be confirmed."""


class _LogoutRedis(Protocol):
    def get(self, key: str) -> str | None: ...

    def delete(self, *keys: str) -> int: ...

    def srem(self, key: str, *members: str) -> int: ...

    def smembers(self, key: str) -> set[str]: ...


def _session_key(environment: str, session_id: str) -> str:
    return f"litemcp:{environment}:admin_session:{session_id}"


def _user_sessions_key(environment: str, user_id: uuid.UUID) -> str:
    return f"litemcp:{environment}:user_sessions:{user_id}"


def _parse_opaque_token(opaque_token: str) -> tuple[str, str]:
    if not isinstance(opaque_token, str):
        raise LogoutRejected
    parts = opaque_token.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise LogoutRejected
    return parts[0], parts[1]


def _persist_logout_audit(
    db: Session,
    *,
    action: str,
    actor_id: str,
    request_id: str,
    resource_id: str,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO audit_event (
                id,
                occurred_at,
                request_id,
                actor_type,
                actor_id,
                action,
                resource_type,
                resource_id,
                result
            ) VALUES (
                :id,
                :occurred_at,
                :request_id,
                :actor_type,
                :actor_id,
                :action,
                :resource_type,
                :resource_id,
                :result
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "occurred_at": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "actor_type": "user",
            "actor_id": actor_id,
            "action": action,
            "resource_type": _RESOURCE_TYPE,
            "resource_id": resource_id,
            "result": _LOGOUT_RESULT,
        },
    )
    db.flush()


def logout_current_session(
    opaque_token: str,
    *,
    redis: _LogoutRedis,
    db: Session,
    environment: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    user_agent: str | None = None,
    cookie_header: str | None = None,
    authorization: str | None = None,
) -> None:
    """Revoke the refresh session identified by ``opaque_token``."""

    del user_agent, cookie_header, authorization
    session_id, presented_secret = _parse_opaque_token(opaque_token)
    key = _session_key(environment, session_id)
    raw = redis.get(key)
    if raw is None:
        raise LogoutRejected

    try:
        payload: dict[str, Any] = json.loads(raw)
        stored_hash = payload["current_secret_hash"]
        session_user_id = payload["user_id"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LogoutRejected from exc

    if not compare_refresh_secret(
        stored_hash=stored_hash, presented_secret=presented_secret
    ):
        raise LogoutRejected

    redis.delete(key)
    redis.srem(_user_sessions_key(environment, uuid.UUID(str(session_user_id))), session_id)
    _persist_logout_audit(
        db,
        action=_LOGOUT_ACTION,
        actor_id=str(actor_user_id),
        request_id=request_id,
        resource_id=session_id,
    )


def logout_all_sessions(
    user_id: uuid.UUID,
    *,
    redis: _LogoutRedis,
    db: Session,
    environment: str,
    actor_user_id: uuid.UUID,
    request_id: str,
    user_agent: str | None = None,
    cookie_header: str | None = None,
    authorization: str | None = None,
) -> None:
    """Revoke every refresh session for ``user_id``."""

    del user_agent, cookie_header, authorization
    set_key = _user_sessions_key(environment, user_id)
    session_ids = redis.smembers(set_key)
    for session_id in session_ids:
        redis.delete(_session_key(environment, session_id))
    redis.delete(set_key)
    _persist_logout_audit(
        db,
        action=_LOGOUT_ALL_ACTION,
        actor_id=str(actor_user_id),
        request_id=request_id,
        resource_id=str(user_id),
    )
