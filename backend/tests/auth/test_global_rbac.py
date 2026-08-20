"""M2-RBAC-001: 全局角色授权边界（application-layer）。

Authorization is derived from the database current user.role / status, not JWT claims.
Unknown capabilities default-deny. Team-admin and service roles do not grant global ops.
"""

from __future__ import annotations

import pytest

from litemcp.auth.rbac import (
    AuthorizationDenied,
    Capability,
    CurrentUser,
    LastActiveAdminError,
    authorize,
    authorize_change_global_role,
    authorize_disable_user,
    authorize_enable_user,
    current_user_from_db,
    has_capability,
)

CREATE_USER = Capability.CREATE_USER
DISABLE_USER = Capability.DISABLE_USER
ENABLE_USER = Capability.ENABLE_USER
CHANGE_GLOBAL_ROLE = Capability.CHANGE_GLOBAL_ROLE
VIEW_GLOBAL_AUDIT = Capability.VIEW_GLOBAL_AUDIT

GLOBAL_CAPS = (
    CREATE_USER,
    DISABLE_USER,
    ENABLE_USER,
    CHANGE_GLOBAL_ROLE,
    VIEW_GLOBAL_AUDIT,
)


def _admin(*, user_id: str = "admin-1") -> CurrentUser:
    return current_user_from_db(user_id=user_id, role="admin", status="active")


def _user(*, user_id: str = "user-1") -> CurrentUser:
    return current_user_from_db(user_id=user_id, role="user", status="active")


# --- CurrentUser from DB fields ---


def test_current_user_from_db_uses_id_role_and_status():
    actor = current_user_from_db(user_id="u-42", role="admin", status="active")
    assert actor.id == "u-42"
    assert actor.role == "admin"
    assert actor.status == "active"


def test_current_user_role_is_exactly_admin_or_user():
    admin = current_user_from_db(user_id="a", role="admin", status="active")
    user = current_user_from_db(user_id="u", role="user", status="active")
    assert admin.role == "admin"
    assert user.role == "user"


# --- JWT role claim is not authorization ---


def test_forged_jwt_admin_claim_does_not_grant_capability_when_db_role_is_user():
    actor = current_user_from_db(
        user_id="u-1",
        role="user",
        status="active",
        jwt_claims={"role": "admin", "sub": "u-1"},
    )
    assert actor.role == "user"
    assert has_capability(actor, CREATE_USER) is False
    with pytest.raises(AuthorizationDenied):
        authorize(actor, CREATE_USER)


def test_stale_jwt_user_claim_does_not_revoke_capability_when_db_role_is_admin():
    """Immediate effect (§11): next request reads current DB role, not JWT role."""
    actor = current_user_from_db(
        user_id="u-1",
        role="admin",
        status="active",
        jwt_claims={"role": "user", "sub": "u-1"},
    )
    assert actor.role == "admin"
    assert has_capability(actor, VIEW_GLOBAL_AUDIT) is True
    authorize(actor, VIEW_GLOBAL_AUDIT)


def test_global_role_change_takes_effect_on_next_current_user_construction():
    before = current_user_from_db(
        user_id="u-1",
        role="user",
        status="active",
        jwt_claims={"role": "user"},
    )
    assert has_capability(before, CHANGE_GLOBAL_ROLE) is False

    after = current_user_from_db(
        user_id="u-1",
        role="admin",
        status="active",
        jwt_claims={"role": "user"},
    )
    assert has_capability(after, CHANGE_GLOBAL_ROLE) is True


# --- Admin vs ordinary user matrix ---


@pytest.mark.parametrize("capability", GLOBAL_CAPS)
def test_admin_may_perform_global_user_and_audit_capabilities(capability: object):
    authorize(_admin(), capability)
    assert has_capability(_admin(), capability) is True


@pytest.mark.parametrize("capability", GLOBAL_CAPS)
def test_ordinary_user_must_not_have_global_user_management_or_audit(capability: object):
    actor = _user()
    assert has_capability(actor, capability) is False
    with pytest.raises(AuthorizationDenied):
        authorize(actor, capability)


def test_disabled_admin_must_not_retain_global_capabilities():
    actor = current_user_from_db(user_id="admin-1", role="admin", status="disabled")
    for capability in GLOBAL_CAPS:
        assert has_capability(actor, capability) is False
        with pytest.raises(AuthorizationDenied):
            authorize(actor, capability)


def test_invalid_db_role_does_not_grant_global_capabilities():
    actor = current_user_from_db(user_id="x", role="superadmin", status="active")
    for capability in GLOBAL_CAPS:
        assert has_capability(actor, capability) is False
        with pytest.raises(AuthorizationDenied):
            authorize(actor, capability)


# --- Team-admin / service roles do not confer global role ---


def test_team_admin_identity_does_not_grant_create_disable_or_role_change():
    actor = current_user_from_db(
        user_id="team-lead",
        role="user",
        status="active",
        is_team_admin=True,
    )
    for capability in (CREATE_USER, DISABLE_USER, ENABLE_USER, CHANGE_GLOBAL_ROLE):
        assert has_capability(actor, capability) is False
        with pytest.raises(AuthorizationDenied):
            authorize(actor, capability)


def test_service_editor_or_viewer_does_not_grant_global_admin_capabilities():
    for service_role in ("editor", "viewer", "owner"):
        actor = current_user_from_db(
            user_id="svc-user",
            role="user",
            status="active",
            service_role=service_role,
        )
        for capability in GLOBAL_CAPS:
            assert has_capability(actor, capability) is False
            with pytest.raises(AuthorizationDenied):
                authorize(actor, capability)


# --- Default deny ---


def test_unknown_capability_is_denied_for_admin():
    actor = _admin()
    assert has_capability(actor, "not_a_defined_capability") is False
    with pytest.raises(AuthorizationDenied):
        authorize(actor, "not_a_defined_capability")


def test_unknown_capability_is_denied_for_user():
    actor = _user()
    with pytest.raises(AuthorizationDenied):
        authorize(actor, "mcp_service_permission")


# --- Subsequent-user operations: only admin; last-active-admin invariant ---


def test_admin_may_disable_a_regular_user():
    authorize_disable_user(
        _admin(),
        target_id="user-1",
        target_role="user",
        target_status="active",
        active_admin_ids=("admin-1",),
    )


def test_user_must_not_disable_another_user():
    with pytest.raises(AuthorizationDenied):
        authorize_disable_user(
            _user(user_id="user-1"),
            target_id="user-2",
            target_role="user",
            target_status="active",
            active_admin_ids=("admin-1",),
        )


def test_admin_may_enable_a_disabled_user():
    authorize_enable_user(
        _admin(),
        target_id="user-1",
        target_role="user",
        target_status="disabled",
    )


def test_user_must_not_enable_a_disabled_user():
    with pytest.raises(AuthorizationDenied):
        authorize_enable_user(
            _user(),
            target_id="user-2",
            target_role="user",
            target_status="disabled",
        )


def test_admin_may_change_another_users_global_role_when_not_last_admin():
    authorize_change_global_role(
        _admin(user_id="admin-1"),
        target_id="admin-2",
        target_role="admin",
        target_status="active",
        new_role="user",
        active_admin_ids=("admin-1", "admin-2"),
    )


def test_user_must_not_change_global_roles():
    with pytest.raises(AuthorizationDenied):
        authorize_change_global_role(
            _user(),
            target_id="user-2",
            target_role="user",
            target_status="active",
            new_role="admin",
            active_admin_ids=("admin-1",),
        )


def test_must_not_disable_the_last_active_admin():
    with pytest.raises(LastActiveAdminError):
        authorize_disable_user(
            _admin(user_id="admin-1"),
            target_id="admin-1",
            target_role="admin",
            target_status="active",
            active_admin_ids=("admin-1",),
        )


def test_must_not_disable_the_last_active_admin_even_when_actor_is_another_admin_who_is_the_same_only_admin():
    with pytest.raises(LastActiveAdminError):
        authorize_disable_user(
            _admin(user_id="only-admin"),
            target_id="only-admin",
            target_role="admin",
            target_status="active",
            active_admin_ids=("only-admin",),
        )


def test_admin_may_disable_one_admin_when_another_active_admin_remains():
    authorize_disable_user(
        _admin(user_id="admin-1"),
        target_id="admin-2",
        target_role="admin",
        target_status="active",
        active_admin_ids=("admin-1", "admin-2"),
    )


def test_must_not_demote_the_last_active_admin():
    with pytest.raises(LastActiveAdminError):
        authorize_change_global_role(
            _admin(user_id="admin-1"),
            target_id="admin-1",
            target_role="admin",
            target_status="active",
            new_role="user",
            active_admin_ids=("admin-1",),
        )


def test_admin_may_promote_a_user_to_admin():
    authorize_change_global_role(
        _admin(),
        target_id="user-1",
        target_role="user",
        target_status="active",
        new_role="admin",
        active_admin_ids=("admin-1",),
    )


def test_disabling_a_non_admin_does_not_trigger_last_active_admin_error():
    authorize_disable_user(
        _admin(),
        target_id="user-1",
        target_role="user",
        target_status="active",
        active_admin_ids=("admin-1",),
    )


def test_team_admin_cannot_disable_users_even_if_not_last_admin():
    actor = current_user_from_db(
        user_id="team-lead",
        role="user",
        status="active",
        is_team_admin=True,
    )
    with pytest.raises(AuthorizationDenied):
        authorize_disable_user(
            actor,
            target_id="user-2",
            target_role="user",
            target_status="active",
            active_admin_ids=("admin-1",),
        )
