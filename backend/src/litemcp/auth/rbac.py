"""Global role authorization (M2-RBAC-001).

docs/architecture/02-admin-auth.md §4.2 / §11 / §12.1.

Authorization is derived from the database current ``user.role`` and
``user.status``, never from JWT claims. Team-admin and service roles do not
confer global capabilities. Unknown capabilities default-deny. Disabling or
demoting the last active admin is refused as a domain invariant.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "AuthorizationDenied",
    "Capability",
    "CurrentUser",
    "LastActiveAdminError",
    "authorize",
    "authorize_change_global_role",
    "authorize_disable_user",
    "authorize_enable_user",
    "current_user_from_db",
    "has_capability",
]

_GLOBAL_ADMIN_ROLE: Final = "admin"
_ACTIVE_STATUS: Final = "active"


class AuthorizationDenied(Exception):
    """Raised when the actor lacks a requested global capability."""

    code: str = "AUTHORIZATION_DENIED"

    def __init__(self, message: str = "authorization denied") -> None:
        super().__init__(message)


class LastActiveAdminError(Exception):
    """Raised when an action would leave the system with no active admin."""

    code: str = "LAST_ACTIVE_ADMIN"

    def __init__(
        self, message: str = "cannot disable or demote the last active admin"
    ) -> None:
        super().__init__(message)


class Capability(StrEnum):
    """Global management capabilities gated by ``user.role``."""

    CREATE_USER = "create_user"
    DISABLE_USER = "disable_user"
    ENABLE_USER = "enable_user"
    CHANGE_GLOBAL_ROLE = "change_global_role"
    VIEW_GLOBAL_AUDIT = "view_global_audit"


_ADMIN_CAPABILITIES: Final[frozenset[Capability]] = frozenset(Capability)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Authenticated actor whose role and status come from the database."""

    id: str
    role: str
    status: str


def current_user_from_db(
    *,
    user_id: str,
    role: str,
    status: str,
    jwt_claims: Mapping[str, Any] | None = None,
    is_team_admin: bool = False,
    service_role: str | None = None,
) -> CurrentUser:
    """Build ``CurrentUser`` from current database fields.

    ``jwt_claims``, ``is_team_admin``, and ``service_role`` are accepted so
    callers can pass ambient request context, but they never affect the
    resulting identity or global authorization.
    """

    del jwt_claims, is_team_admin, service_role
    return CurrentUser(id=user_id, role=role, status=status)


def _as_capability(capability: object) -> Capability | None:
    if isinstance(capability, Capability):
        return capability
    if isinstance(capability, str):
        try:
            return Capability(capability)
        except ValueError:
            return None
    return None


def has_capability(actor: CurrentUser, capability: object) -> bool:
    """Return whether ``actor`` currently holds ``capability``."""

    cap = _as_capability(capability)
    if cap is None:
        return False
    if actor.status != _ACTIVE_STATUS or actor.role != _GLOBAL_ADMIN_ROLE:
        return False
    return cap in _ADMIN_CAPABILITIES


def authorize(actor: CurrentUser, capability: object) -> None:
    """Require ``capability`` or raise ``AuthorizationDenied``."""

    if not has_capability(actor, capability):
        raise AuthorizationDenied()


def _would_remove_last_active_admin(
    *,
    target_id: str,
    target_role: str,
    target_status: str,
    active_admin_ids: Iterable[str],
) -> bool:
    if target_role != _GLOBAL_ADMIN_ROLE or target_status != _ACTIVE_STATUS:
        return False
    remaining = {admin_id for admin_id in active_admin_ids if admin_id != target_id}
    return not remaining


def authorize_disable_user(
    actor: CurrentUser,
    *,
    target_id: str,
    target_role: str,
    target_status: str,
    active_admin_ids: Iterable[str],
) -> None:
    """Allow an admin to disable a user unless they are the last active admin."""

    authorize(actor, Capability.DISABLE_USER)
    if _would_remove_last_active_admin(
        target_id=target_id,
        target_role=target_role,
        target_status=target_status,
        active_admin_ids=active_admin_ids,
    ):
        raise LastActiveAdminError()


def authorize_enable_user(
    actor: CurrentUser,
    *,
    target_id: str,
    target_role: str,
    target_status: str,
) -> None:
    """Allow an admin to enable a disabled user."""

    del target_id, target_role, target_status
    authorize(actor, Capability.ENABLE_USER)


def authorize_change_global_role(
    actor: CurrentUser,
    *,
    target_id: str,
    target_role: str,
    target_status: str,
    new_role: str,
    active_admin_ids: Iterable[str],
) -> None:
    """Allow an admin to change a global role unless demoting the last admin."""

    authorize(actor, Capability.CHANGE_GLOBAL_ROLE)
    if new_role == _GLOBAL_ADMIN_ROLE:
        return
    if _would_remove_last_active_admin(
        target_id=target_id,
        target_role=target_role,
        target_status=target_status,
        active_admin_ids=active_admin_ids,
    ):
        raise LastActiveAdminError()
