from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pytest

from litemcp.auth.service_rbac import (
    ALLOWED,
    FORBIDDEN,
    NOT_FOUND,
    authorize_service_action,
)

USER_CREATOR = "11111111-1111-1111-1111-111111111111"
USER_EDITOR = "22222222-2222-2222-2222-222222222222"
USER_VIEWER = "33333333-3333-3333-3333-333333333333"
USER_STRANGER = "44444444-4444-4444-4444-444444444444"
USER_ADMIN = "55555555-5555-5555-5555-555555555555"
USER_TEAMMATE = "66666666-6666-6666-6666-666666666666"
SERVICE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SERVICE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
TEAM_1 = "t1111111-1111-1111-1111-111111111111"
TEAM_2 = "t2222222-2222-2222-2222-222222222222"

VIEW_SERVICE = "view_service"
VIEW_API_KEY_METADATA = "view_api_key_metadata"
VIEW_SERVICE_AUDIT_SUMMARY = "view_service_audit_summary"
VIEW_FULL_BUILD_LOGS = "view_full_build_logs"
MUTATE_CONFIG_REVISION = "mutate_config_revision"
TRIGGER_BUILD_SYNC_PUBLISH_ROLLBACK = "trigger_build_sync_publish_rollback"
MANAGE_API_KEYS = "manage_api_keys"
MUTATE_SERVICE_PERMISSIONS = "mutate_service_permissions"
MUTATE_SERVICE_LIFECYCLE = "mutate_service_lifecycle"
CREATE_USER = "create_user"
DISABLE_USER = "disable_user"
MUTATE_GLOBAL_ROLE = "mutate_global_role"
VIEW_GLOBAL_AUDIT = "view_global_audit"
UNKNOWN_ACTION = "not_a_registered_service_action"

VIEWER_ALLOWED_ACTIONS = (
    VIEW_SERVICE,
    VIEW_API_KEY_METADATA,
    VIEW_SERVICE_AUDIT_SUMMARY,
)
EDITOR_ONLY_SERVICE_ACTIONS = (
    VIEW_FULL_BUILD_LOGS,
    MUTATE_CONFIG_REVISION,
    TRIGGER_BUILD_SYNC_PUBLISH_ROLLBACK,
    MANAGE_API_KEYS,
    MUTATE_SERVICE_PERMISSIONS,
    MUTATE_SERVICE_LIFECYCLE,
)
SERVICE_OBJECT_ACTIONS = VIEWER_ALLOWED_ACTIONS + EDITOR_ONLY_SERVICE_ACTIONS
GLOBAL_ONLY_ACTIONS = (
    CREATE_USER,
    DISABLE_USER,
    MUTATE_GLOBAL_ROLE,
    VIEW_GLOBAL_AUDIT,
)
UNMATCHED_ACTIONS = (*GLOBAL_ONLY_ACTIONS, UNKNOWN_ACTION)


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    status: str = "active"
    team_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Grant:
    service_id: str
    principal_type: str
    role: str
    user_id: str | None = None
    team_id: str | None = None


def _authorize(
    principal: Principal,
    service_id: str,
    action: str,
    grants: Sequence[Grant],
    token_claims: Mapping[str, object] | None = None,
):
    return authorize_service_action(
        principal=principal,
        service_id=service_id,
        action=action,
        grants=grants,
        token_claims=token_claims,
    )


def _user(*, user_id: str, team_ids: tuple[str, ...] = ()) -> Principal:
    return Principal(id=user_id, role="user", status="active", team_ids=team_ids)


def _admin(*, user_id: str = USER_ADMIN, status: str = "active") -> Principal:
    return Principal(id=user_id, role="admin", status=status, team_ids=())


def _user_grant(service_id: str, user_id: str, role: str) -> Grant:
    return Grant(
        service_id=service_id,
        principal_type="user",
        role=role,
        user_id=user_id,
    )


def _team_viewer_grant(service_id: str, team_id: str) -> Grant:
    return Grant(
        service_id=service_id,
        principal_type="team",
        role="viewer",
        team_id=team_id,
    )


def _everyone_viewer_grant(service_id: str) -> Grant:
    return Grant(service_id=service_id, principal_type="everyone", role="viewer")


def _creator_grant(service_id: str = SERVICE_A) -> Grant:
    return _user_grant(service_id, USER_CREATOR, "editor")


def test_creator_user_editor_can_read_and_write_granted_service() -> None:
    principal = _user(user_id=USER_CREATOR)
    grants = (_creator_grant(),)
    for action in SERVICE_OBJECT_ACTIONS:
        assert _authorize(principal, SERVICE_A, action, grants) == ALLOWED


def test_viewer_can_perform_viewer_reads_and_cannot_write() -> None:
    principal = _user(user_id=USER_VIEWER)
    grants = (_user_grant(SERVICE_A, USER_VIEWER, "viewer"),)
    for action in VIEWER_ALLOWED_ACTIONS:
        assert _authorize(principal, SERVICE_A, action, grants) == ALLOWED
    for action in EDITOR_ONLY_SERVICE_ACTIONS:
        assert _authorize(principal, SERVICE_A, action, grants) == FORBIDDEN


def test_non_creator_user_editor_matches_editor_matrix() -> None:
    principal = _user(user_id=USER_EDITOR)
    grants = (_creator_grant(), _user_grant(SERVICE_A, USER_EDITOR, "editor"))
    for action in SERVICE_OBJECT_ACTIONS:
        assert _authorize(principal, SERVICE_A, action, grants) == ALLOWED
    for action in GLOBAL_ONLY_ACTIONS:
        assert _authorize(principal, SERVICE_A, action, grants) == FORBIDDEN


def test_global_admin_can_read_and_write_without_permission_row() -> None:
    principal = _admin()
    for action in SERVICE_OBJECT_ACTIONS:
        assert _authorize(principal, SERVICE_B, action, ()) == ALLOWED


def test_grant_on_service_a_does_not_authorize_service_b() -> None:
    principal = _user(user_id=USER_CREATOR)
    grants = (_creator_grant(SERVICE_A),)
    for action in SERVICE_OBJECT_ACTIONS:
        assert _authorize(principal, SERVICE_B, action, grants) == NOT_FOUND


def test_ungranted_service_id_is_not_found_not_forbidden() -> None:
    principal = _user(user_id=USER_STRANGER)
    grants = (_creator_grant(SERVICE_A), _user_grant(SERVICE_A, USER_VIEWER, "viewer"))
    assert _authorize(principal, SERVICE_A, VIEW_SERVICE, grants) == NOT_FOUND
    assert _authorize(principal, SERVICE_B, VIEW_SERVICE, grants) == NOT_FOUND
    assert _authorize(principal, SERVICE_A, MUTATE_CONFIG_REVISION, grants) == NOT_FOUND


def test_team_membership_alone_does_not_grant_visibility() -> None:
    teammate = _user(user_id=USER_TEAMMATE, team_ids=(TEAM_1,))
    team_admin = Principal(
        id=USER_TEAMMATE,
        role="user",
        status="active",
        team_ids=(TEAM_1,),
    )
    grants = (_creator_grant(SERVICE_A),)
    assert _authorize(teammate, SERVICE_A, VIEW_SERVICE, grants) == NOT_FOUND
    assert _authorize(team_admin, SERVICE_A, VIEW_SERVICE, grants) == NOT_FOUND
    assert _authorize(teammate, SERVICE_A, MUTATE_CONFIG_REVISION, grants) == NOT_FOUND


def test_explicit_team_viewer_grant_is_viewer_only() -> None:
    member = _user(user_id=USER_TEAMMATE, team_ids=(TEAM_1,))
    outsider = _user(user_id=USER_STRANGER, team_ids=(TEAM_2,))
    grants = (_creator_grant(SERVICE_A), _team_viewer_grant(SERVICE_A, TEAM_1))
    for action in VIEWER_ALLOWED_ACTIONS:
        assert _authorize(member, SERVICE_A, action, grants) == ALLOWED
    for action in EDITOR_ONLY_SERVICE_ACTIONS:
        assert _authorize(member, SERVICE_A, action, grants) == FORBIDDEN
    assert _authorize(outsider, SERVICE_A, VIEW_SERVICE, grants) == NOT_FOUND


def test_everyone_viewer_grant_is_viewer_only() -> None:
    stranger = _user(user_id=USER_STRANGER)
    grants = (_creator_grant(SERVICE_A), _everyone_viewer_grant(SERVICE_A))
    for action in VIEWER_ALLOWED_ACTIONS:
        assert _authorize(stranger, SERVICE_A, action, grants) == ALLOWED
    for action in EDITOR_ONLY_SERVICE_ACTIONS:
        assert _authorize(stranger, SERVICE_A, action, grants) == FORBIDDEN


@pytest.mark.parametrize(
    "grants",
    [
        (_user_grant(SERVICE_A, USER_VIEWER, "viewer"),),
        (_team_viewer_grant(SERVICE_A, TEAM_1),),
        (_everyone_viewer_grant(SERVICE_A),),
    ],
)
def test_user_team_everyone_viewer_grants_are_equivalent(grants: tuple[Grant, ...]) -> None:
    principal = _user(user_id=USER_VIEWER, team_ids=(TEAM_1,))
    for action in VIEWER_ALLOWED_ACTIONS:
        assert _authorize(principal, SERVICE_A, action, grants) == ALLOWED
    for action in EDITOR_ONLY_SERVICE_ACTIONS:
        assert _authorize(principal, SERVICE_A, action, grants) == FORBIDDEN


@pytest.mark.parametrize("status", ["disabled", "locked", "inactive"])
@pytest.mark.parametrize(
    "principal_factory,grants",
    [
        (lambda: _user(user_id=USER_CREATOR), (_creator_grant(),)),
        (lambda: _admin(), ()),
        (
            lambda: _user(user_id=USER_VIEWER),
            (_user_grant(SERVICE_A, USER_VIEWER, "viewer"),),
        ),
    ],
)
def test_inactive_users_are_denied(status: str, principal_factory, grants: tuple[Grant, ...]) -> None:
    base = principal_factory()
    principal = Principal(
        id=base.id,
        role=base.role,
        status=status,
        team_ids=base.team_ids,
    )
    assert _authorize(principal, SERVICE_A, VIEW_SERVICE, grants) != ALLOWED
    assert _authorize(principal, SERVICE_A, MUTATE_CONFIG_REVISION, grants) != ALLOWED


def test_unknown_service_action_default_denies() -> None:
    creator = _user(user_id=USER_CREATOR)
    stranger = _user(user_id=USER_STRANGER)
    admin = _admin()
    grants = (_creator_grant(SERVICE_A),)
    for action in UNMATCHED_ACTIONS:
        assert _authorize(creator, SERVICE_A, action, grants) == FORBIDDEN
        assert _authorize(stranger, SERVICE_A, action, grants) == NOT_FOUND
        assert _authorize(admin, SERVICE_A, action, ()) == FORBIDDEN


def test_token_claims_do_not_grant_access_without_permission_rows() -> None:
    principal = _user(user_id=USER_STRANGER)
    token_claims = {
        "role": "admin",
        "service_role": "editor",
        "service_id": SERVICE_A,
        "permissions": [SERVICE_A],
        "mcp_service_permission": [
            {
                "service_id": SERVICE_A,
                "principal_type": "user",
                "role": "editor",
                "user_id": USER_STRANGER,
            }
        ],
    }
    assert (
        _authorize(
            principal,
            SERVICE_A,
            VIEW_SERVICE,
            (),
            token_claims=token_claims,
        )
        == NOT_FOUND
    )
    assert (
        _authorize(
            principal,
            SERVICE_A,
            MUTATE_CONFIG_REVISION,
            (),
            token_claims=token_claims,
        )
        == NOT_FOUND
    )


def test_token_claims_do_not_escalate_viewer_to_editor() -> None:
    principal = _user(user_id=USER_VIEWER)
    grants = (_user_grant(SERVICE_A, USER_VIEWER, "viewer"),)
    token_claims = {
        "role": "admin",
        "service_role": "editor",
        "svc": SERVICE_A,
    }
    assert (
        _authorize(
            principal,
            SERVICE_A,
            VIEW_SERVICE,
            grants,
            token_claims=token_claims,
        )
        == ALLOWED
    )
    assert (
        _authorize(
            principal,
            SERVICE_A,
            MUTATE_CONFIG_REVISION,
            grants,
            token_claims=token_claims,
        )
        == FORBIDDEN
    )


def test_permission_rows_are_reevaluated_each_call() -> None:
    principal = _user(user_id=USER_CREATOR)
    granted = (_creator_grant(SERVICE_A),)
    assert _authorize(principal, SERVICE_A, MUTATE_CONFIG_REVISION, granted) == ALLOWED
    assert _authorize(principal, SERVICE_A, MUTATE_CONFIG_REVISION, ()) == NOT_FOUND
    assert _authorize(principal, SERVICE_A, VIEW_SERVICE, granted) == ALLOWED
