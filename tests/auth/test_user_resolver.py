"""Tests for ``services/auth/user_resolver.resolve_or_create_admin``.

Uses the ``isolated_db`` fixture from ``tests/conftest.py`` for a fresh
DB per test.
"""

from __future__ import annotations

import pytest

from services.auth.base import Principal


@pytest.fixture(autouse=True)
def _no_relink_env(monkeypatch):
    """Default to the safe behavior in every test; opt-in per test."""
    monkeypatch.delenv("AUTH_ALLOW_EMAIL_RELINK", raising=False)


def _principal(
    *,
    issuer: str = "https://idp.test/",
    subject: str = "sub-1",
    email: str = "alice@example.com",
    email_verified: bool = True,
    name: str | None = "Alice",
) -> Principal:
    return Principal(
        issuer=issuer,
        subject=subject,
        email=email,
        email_verified=email_verified,
        name=name,
        raw_claims={},
    )


# -------- existing OIDC-linked admin --------


def test_existing_issuer_subject_returns_same_admin_and_touches_login(isolated_db):
    from models.admin_users import AdminUsers
    from services.auth.user_resolver import resolve_or_create_admin

    AdminUsers.insert(
        id="adm-1",
        email="alice@example.com",
        issuer="https://idp.test/",
        subject="sub-1",
        email_verified=True,
        is_superadmin=False,
    )
    before = AdminUsers.get_by_id("adm-1")
    assert before is not None and before.last_login_at is None

    result = resolve_or_create_admin(_principal())
    assert result.id == "adm-1"
    after = AdminUsers.get_by_id("adm-1")
    assert after.last_login_at is not None
    # No new admin row was created.
    assert AdminUsers.count() == 1


# -------- email-takeover guard --------


def test_email_linked_to_other_provider_refused_by_default(isolated_db):
    from models.admin_users import AdminUsers
    from services.auth.user_resolver import (
        EmailLinkedToOtherProvider,
        resolve_or_create_admin,
    )

    AdminUsers.insert(
        id="adm-1",
        email="alice@example.com",
        issuer="https://old-idp.test/",
        subject="old-sub",
        email_verified=True,
    )

    with pytest.raises(EmailLinkedToOtherProvider):
        resolve_or_create_admin(_principal(issuer="https://new-idp.test/", subject="new-sub"))


def test_email_relink_allowed_when_env_flag_set(isolated_db, monkeypatch):
    monkeypatch.setenv("AUTH_ALLOW_EMAIL_RELINK", "true")
    from models.admin_users import AdminUsers
    from services.auth.user_resolver import resolve_or_create_admin

    AdminUsers.insert(
        id="adm-1",
        email="alice@example.com",
        issuer="https://old-idp.test/",
        subject="old-sub",
        email_verified=True,
    )
    result = resolve_or_create_admin(_principal(issuer="https://new-idp.test/", subject="new-sub"))
    assert result.id == "adm-1"
    refetched = AdminUsers.get_by_id("adm-1")
    assert refetched.issuer == "https://new-idp.test/"
    assert refetched.subject == "new-sub"


def test_email_already_exists_without_oidc_link_attaches_idp_no_flag_required(isolated_db):
    """A pre-provisioned admin with no issuer/subject yet (e.g. activated locally
    via a different path) gets linked on first OIDC login without the relink flag."""
    from models.admin_users import AdminUsers
    from services.auth.user_resolver import resolve_or_create_admin

    AdminUsers.insert(
        id="adm-1",
        email="alice@example.com",
        issuer=None,
        subject=None,
        email_verified=False,
    )
    result = resolve_or_create_admin(_principal())
    assert result.id == "adm-1"
    refetched = AdminUsers.get_by_id("adm-1")
    assert refetched.issuer == "https://idp.test/"
    assert refetched.subject == "sub-1"


# -------- pending-admin activation --------


def test_pending_admin_consumed_on_first_matching_login(isolated_db):
    from models.pending_admins import PendingAdmins
    from models.admin_users import AdminUsers
    from services.auth.user_resolver import resolve_or_create_admin

    PendingAdmins.create(email="alice@example.com", is_superadmin=True)

    result = resolve_or_create_admin(_principal())
    assert result.is_superadmin is True
    assert result.email == "alice@example.com"
    assert result.issuer == "https://idp.test/"
    assert result.subject == "sub-1"

    # Pending row consumed.
    assert PendingAdmins.get_active_by_email("alice@example.com") is None
    assert AdminUsers.count() == 1


def test_pending_admin_with_bot_assignments_creates_memberships(isolated_db):
    import uuid

    from models.bots import Bot, Bots
    from models.admin_bot_memberships import AdminBotMemberships
    from models.pending_admins import PendingAdmins
    from services.auth.user_resolver import resolve_or_create_admin

    bot_id = f"bot-{uuid.uuid4().hex[:8]}"
    slug = f"test-resolver-{uuid.uuid4().hex[:8]}"
    bot = Bots.insert(id=bot_id, slug=slug, name="Test Bot")
    try:
        PendingAdmins.create(
            email="alice@example.com",
            is_superadmin=False,
            bot_assignments=[{"bot_id": bot.id, "role": "admin"}],
        )

        result = resolve_or_create_admin(_principal())
        membership = AdminBotMemberships.get(result.id, bot.id)
        assert membership is not None
        assert membership.role == "admin"
    finally:
        Bot.delete().where(Bot.id == bot_id).execute()


def test_pending_admin_invalid_bot_assignment_logged_and_skipped(isolated_db, caplog):
    from models.admin_bot_memberships import AdminBotMemberships
    from models.pending_admins import PendingAdmins
    from services.auth.user_resolver import resolve_or_create_admin

    PendingAdmins.create(
        email="alice@example.com",
        bot_assignments=[
            {"bot_id": "nonexistent-bot-id", "role": "owner"},
            {"role": "viewer"},  # missing bot_id
            {"bot_id": "another-bad-id", "role": "junk-role"},
        ],
    )
    # Resolver should still succeed; bad assignments are skipped/logged.
    result = resolve_or_create_admin(_principal())
    # No memberships created.
    assert AdminBotMemberships.list_for_admin(result.id) == []


# -------- not-provisioned --------


def test_unknown_user_with_no_pending_invite_raises_admin_not_provisioned(isolated_db):
    from services.auth.user_resolver import (
        AdminNotProvisioned,
        resolve_or_create_admin,
    )

    with pytest.raises(AdminNotProvisioned):
        resolve_or_create_admin(_principal(email="stranger@example.com"))


# -------- email is normalised --------


def test_email_match_is_case_insensitive(isolated_db):
    from models.pending_admins import PendingAdmins
    from services.auth.user_resolver import resolve_or_create_admin

    PendingAdmins.create(email="ALICE@example.com", is_superadmin=False)
    result = resolve_or_create_admin(_principal(email="alice@EXAMPLE.com"))
    assert result.email == "alice@example.com"
