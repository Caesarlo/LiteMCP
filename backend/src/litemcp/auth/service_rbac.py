"""Service-object authorization (M2-RBAC-002).

docs/architecture/02-admin-auth.md §12.3 / §12.4.
docs/architecture/01-data-model.md §5.12.

Authorization is derived from the current user status/role plus
``mcp_service_permission`` rows (``grants``), never from JWT claims.
Invisible services return not-found (IDOR); visible-but-denied actions
return forbidden. Unknown and global-only actions default-deny.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Protocol

__all__ = [
    "ALLOWED",
    "FORBIDDEN",
    "NOT_FOUND",
    "authorize_service_action",
]

ALLOWED: Final = "allowed"
FORBIDDEN: Final = "forbidden"
NOT_FOUND: Final = "not_found"

_ACTIVE_STATUS: Final = "active"
_GLOBAL_ADMIN_ROLE: Final = "admin"
_PRINCIPAL_USER: Final = "user"
_PRINCIPAL_TEAM: Final = "team"
_PRINCIPAL_EVERYONE: Final = "everyone"
_ROLE_EDITOR: Final = "editor"

_VIEWER_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "view_service",
        "view_api_key_metadata",
        "view_service_audit_summary",
    }
)
_EDITOR_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "view_full_build_logs",
        "mutate_config_revision",
        "trigger_build_sync_publish_rollback",
        "manage_api_keys",
        "mutate_service_permissions",
        "mutate_service_lifecycle",
    }
)


class _Principal(Protocol):
    id: str
    role: str
    status: str
    team_ids: Sequence[str]


class _Grant(Protocol):
    service_id: str
    principal_type: str
    role: str
    user_id: str | None
    team_id: str | None


def _is_active_admin(principal: _Principal) -> bool:
    return principal.status == _ACTIVE_STATUS and principal.role == _GLOBAL_ADMIN_ROLE


def _visible(principal: _Principal, grants: Sequence[_Grant], service_id: str) -> bool:
    if _is_active_admin(principal):
        return True
    team_ids = set(principal.team_ids)
    for grant in grants:
        if grant.service_id != service_id:
            continue
        if grant.principal_type == _PRINCIPAL_EVERYONE:
            return True
        if grant.principal_type == _PRINCIPAL_USER and grant.user_id == principal.id:
            return True
        if (
            grant.principal_type == _PRINCIPAL_TEAM
            and grant.team_id is not None
            and grant.team_id in team_ids
        ):
            return True
    return False


def _writable(principal: _Principal, grants: Sequence[_Grant], service_id: str) -> bool:
    if _is_active_admin(principal):
        return True
    for grant in grants:
        if grant.service_id != service_id:
            continue
        if (
            grant.principal_type == _PRINCIPAL_USER
            and grant.user_id == principal.id
            and grant.role == _ROLE_EDITOR
        ):
            return True
    return False


def authorize_service_action(
    *,
    principal: _Principal,
    service_id: str,
    action: str,
    grants: Sequence[_Grant],
    token_claims: Mapping[str, object] | None = None,
) -> str:
    """Authorize a service-scoped action against current grants.

    ``token_claims`` is accepted for call-site convenience and never
    consulted. Grants are reevaluated on every call; nothing is cached.
    """

    del token_claims
    if not _visible(principal, grants, service_id):
        return NOT_FOUND
    if principal.status != _ACTIVE_STATUS:
        return FORBIDDEN
    if action in _VIEWER_ACTIONS:
        return ALLOWED
    if action in _EDITOR_ACTIONS and _writable(principal, grants, service_id):
        return ALLOWED
    return FORBIDDEN
