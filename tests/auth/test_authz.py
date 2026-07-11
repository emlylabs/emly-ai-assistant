"""Tests for ``services/authz.py``.

Each predicate gets positive and negative cases plus the superadmin bypass.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from models.admin_users import AdminUserModel


def _admin(*, id="adm-1", email="a@b.c", is_superadmin=False, is_active=True) -> AdminUserModel:
    now = datetime.now(timezone.utc)
    return AdminUserModel(
        id=id, email=email, is_active=is_active, issuer=None, subject=None,
        email_verified=True, is_superadmin=is_superadmin, last_login_at=None,
        name=None, created_on=now, updated_on=now,
    )


@pytest.fixture
def grant_membership(isolated_db):
    """Returns a callable that grants an admin a membership on a bot. Each call
    creates a fresh bot so the tests can run in any order."""
    import uuid as _uuid
    from models.admin_bot_memberships import AdminBotMemberships
    from models.admin_users import AdminUsers
    from models.bots import Bot, Bots

    created_bot_ids: list[str] = []
    created_admin_ids: list[str] = []

    def _grant(admin_id: str, role: str, *, email: str | None = None) -> str:
        bot_id = f"bot-{_uuid.uuid4().hex[:8]}"
        slug = f"slug-{_uuid.uuid4().hex[:8]}"
        Bots.insert(id=bot_id, slug=slug, name="Test")
        # admin_user row must exist for the FK; insert if missing.
        if AdminUsers.get_by_id(admin_id) is None:
            AdminUsers.insert(
                id=admin_id,
                email=email or f"{admin_id}-{_uuid.uuid4().hex[:6]}@example.com",
                is_active=True,
            )
            created_admin_ids.append(admin_id)
        AdminBotMemberships.grant(f"mbr-{_uuid.uuid4().hex[:8]}", admin_id, bot_id, role)
        created_bot_ids.append(bot_id)
        return bot_id

    yield _grant

    for bid in created_bot_ids:
        Bot.delete().where(Bot.id == bid).execute()


# -------- require_superadmin --------


def test_require_superadmin_passes_for_superadmin():
    from services.authz import require_superadmin
    require_superadmin(_admin(is_superadmin=True))  # does not raise


def test_require_superadmin_raises_for_regular_admin():
    from services.authz import require_superadmin
    with pytest.raises(HTTPException) as exc:
        require_superadmin(_admin(is_superadmin=False))
    assert exc.value.status_code == 403
    assert exc.value.detail == {"code": "not_superadmin"}


# -------- require_member --------


def test_require_member_returns_membership_for_viewer(grant_membership):
    from services.authz import require_member
    bot_id = grant_membership("adm-1", "viewer")
    m = require_member(_admin(id="adm-1"), bot_id)
    assert m.role == "viewer"


def test_require_member_returns_membership_for_admin(grant_membership):
    from services.authz import require_member
    bot_id = grant_membership("adm-1", "admin")
    assert require_member(_admin(id="adm-1"), bot_id).role == "admin"


def test_require_member_returns_membership_for_owner(grant_membership):
    from services.authz import require_member
    bot_id = grant_membership("adm-1", "owner")
    assert require_member(_admin(id="adm-1"), bot_id).role == "owner"


def test_require_member_raises_for_non_member(grant_membership):
    from services.authz import require_member
    bot_id = grant_membership("other-admin", "owner")
    with pytest.raises(HTTPException) as exc:
        require_member(_admin(id="not-a-member"), bot_id)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "not_member"


def test_require_member_superadmin_bypass(grant_membership):
    """Superadmin gets through even with no membership row."""
    from services.authz import require_member
    bot_id = grant_membership("other-admin", "owner")
    m = require_member(_admin(id="super", is_superadmin=True), bot_id)
    assert m.role == "owner"  # synthetic superadmin membership


# -------- require_writer --------


def test_require_writer_passes_for_owner_and_admin(grant_membership):
    from services.authz import require_writer
    bot_a = grant_membership("adm-1", "owner")
    bot_b = grant_membership("adm-1", "admin")
    assert require_writer(_admin(id="adm-1"), bot_a).role == "owner"
    assert require_writer(_admin(id="adm-1"), bot_b).role == "admin"


def test_require_writer_raises_for_viewer(grant_membership):
    from services.authz import require_writer
    bot_id = grant_membership("adm-1", "viewer")
    with pytest.raises(HTTPException) as exc:
        require_writer(_admin(id="adm-1"), bot_id)
    assert exc.value.detail["code"] == "not_writer"


def test_require_writer_raises_for_non_member(grant_membership):
    from services.authz import require_writer
    bot_id = grant_membership("other", "owner")
    with pytest.raises(HTTPException) as exc:
        require_writer(_admin(id="stranger"), bot_id)
    assert exc.value.detail["code"] == "not_writer"


def test_require_writer_superadmin_bypass(grant_membership):
    from services.authz import require_writer
    bot_id = grant_membership("other", "viewer")
    assert require_writer(_admin(id="super", is_superadmin=True), bot_id).role == "owner"


# -------- require_owner --------


def test_require_owner_passes_for_owner(grant_membership):
    from services.authz import require_owner
    bot_id = grant_membership("adm-1", "owner")
    assert require_owner(_admin(id="adm-1"), bot_id).role == "owner"


def test_require_owner_raises_for_admin(grant_membership):
    from services.authz import require_owner
    bot_id = grant_membership("adm-1", "admin")
    with pytest.raises(HTTPException) as exc:
        require_owner(_admin(id="adm-1"), bot_id)
    assert exc.value.detail["code"] == "not_owner"


def test_require_owner_superadmin_bypass(grant_membership):
    from services.authz import require_owner
    bot_id = grant_membership("other", "viewer")
    assert require_owner(_admin(id="super", is_superadmin=True), bot_id).role == "owner"


# -------- has_role_at_least --------


def test_has_role_at_least_compares_correctly(grant_membership):
    from services.authz import has_role_at_least
    bot_id = grant_membership("adm-1", "admin")
    a = _admin(id="adm-1")
    assert has_role_at_least(a, bot_id, "viewer") is True
    assert has_role_at_least(a, bot_id, "admin") is True
    assert has_role_at_least(a, bot_id, "owner") is False


def test_has_role_at_least_false_for_non_member(grant_membership):
    from services.authz import has_role_at_least
    bot_id = grant_membership("other", "owner")
    assert has_role_at_least(_admin(id="stranger"), bot_id, "viewer") is False


def test_has_role_at_least_true_for_superadmin(grant_membership):
    from services.authz import has_role_at_least
    bot_id = grant_membership("other", "viewer")
    assert has_role_at_least(_admin(id="super", is_superadmin=True), bot_id, "owner") is True


def test_has_role_at_least_rejects_unknown_role():
    from services.authz import has_role_at_least
    with pytest.raises(ValueError, match="unknown role"):
        has_role_at_least(_admin(), "some-bot", "wizard")
