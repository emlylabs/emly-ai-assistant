"""C5: built-in sign-in issuer — off by default in prod when an external IdP
is configured, must_change enforced via a change-password step, and local
sign-in failures / lockouts audited."""

from __future__ import annotations

import tempfile
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

EMAIL = "boot@example.com"
TEMP_PASSWORD = "temporary-pass-123"  # gitleaks:allow (test fixture / not a secret)
NEW_PASSWORD = "a-much-better-passphrase"


@pytest.fixture
def issuer(isolated_db, monkeypatch):
    from services.auth import ratelimit

    monkeypatch.setitem(ratelimit._LIMITS, "local_authorize", "1000/minute")
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_ISSUER", "http://testserver")
    monkeypatch.setenv("AUTH_OIDC_AUDIENCE", "emly-admin-api")
    monkeypatch.setenv("AUTH_OIDC_CLIENT_ID", "emly-admin-console")
    monkeypatch.setenv("AUTH_OIDC_ALLOWED_REDIRECT_URIS", "http://testserver/api/admin/auth/callback")
    monkeypatch.setenv("AUTH_LOCAL_ISSUER_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOCAL_KEYS_DIR", tempfile.mkdtemp(prefix="c5-keys-"))
    monkeypatch.setenv("AUTH_LOCAL_KEY_BITS", "2048")
    monkeypatch.setenv("AUTH_LOCAL_LOCKOUT_THRESHOLD", "3")
    from services.auth.factory import reset_for_tests as reset_provider
    from services.auth.issuer.factory import reset_for_tests as reset_issuer

    reset_provider()
    reset_issuer()

    from models.local_credentials import LocalCredentials
    from models.pending_admins import PendingAdmins
    from routes import auth_issuer
    from services.auth.issuer.passwords import hash_password

    PendingAdmins.create(email=EMAIL, is_superadmin=True, bot_assignments=[])
    LocalCredentials.upsert(f"bootstrap:{EMAIL}", hash_password(TEMP_PASSWORD), must_change=True)

    app = FastAPI()
    app.include_router(auth_issuer.router)
    yield TestClient(app, base_url="http://testserver", follow_redirects=False)
    reset_provider()
    reset_issuer()


def _form(**extra):
    return {
        "client_id": "emly-admin-console",
        "redirect_uri": "http://testserver/api/admin/auth/callback",
        "state": "abc-state-1234567890",
        "nonce": "nonce-abcdef1234",
        "code_challenge": "abcdef-12345_67890-abcdef-1234567890ab",
        "code_challenge_method": "S256",
        "scope": "openid email profile",
        "email": EMAIL,
        "password": TEMP_PASSWORD,
        **extra,
    }


def _post(client, **extra):
    return client.post("/api/auth/local/authorize", data=_form(**extra))


def _audit(action):
    from models.admin_audit_log import AdminAuditLogs

    return AdminAuditLogs.list_filtered(action=action)


# --------------------------------------------------------- must_change step


def test_correct_password_shows_change_step_not_a_code(issuer):
    r = _post(issuer)
    assert r.status_code == 200
    assert "Choose a new password" in r.text
    assert 'name="new_password"' in r.text
    assert TEMP_PASSWORD not in r.text  # never echoed into the page


@pytest.mark.parametrize(
    "new, confirm, message",
    [
        ("short", "short", "at least 12 characters"),
        (NEW_PASSWORD, NEW_PASSWORD + "x", "don&#x27;t match"),
        (TEMP_PASSWORD, TEMP_PASSWORD, "different from the current one"),
    ],
)
def test_weak_or_mismatched_new_password_is_refused(issuer, new, confirm, message):
    from models.local_credentials import LocalCredentials

    r = _post(issuer, new_password=new, new_password_confirm=confirm)
    assert r.status_code == 200
    assert message in r.text
    assert LocalCredentials.get(f"bootstrap:{EMAIL}").must_change is True


def test_valid_change_issues_code_and_clears_flag(issuer):
    from models.local_credentials import LocalCredentials
    from services.auth.issuer.passwords import verify_password

    r = _post(issuer, new_password=NEW_PASSWORD, new_password_confirm=NEW_PASSWORD)
    assert r.status_code == 302, r.text
    assert "code" in parse_qs(urlparse(r.headers["location"]).query)
    key = f"bootstrap:{EMAIL}"
    assert LocalCredentials.get(key).must_change is False
    assert verify_password(NEW_PASSWORD, LocalCredentials.get_password_hash(key))
    assert len(_audit("auth.local.password_changed")) == 1
    # The temporary password no longer works; the new one signs straight in.
    assert "Invalid email or password" in _post(issuer).text
    assert _post(issuer, password=NEW_PASSWORD).status_code == 302


def test_wrong_current_password_on_change_step(issuer):
    r = _post(issuer, password="wrong-password-xyz", new_password=NEW_PASSWORD,
              new_password_confirm=NEW_PASSWORD)
    assert "Invalid email or password" in r.text
    assert "Choose a new password" in r.text


def test_must_change_also_enforced_for_activated_admins(issuer):
    from models.admin_users import AdminUsers
    from models.local_credentials import LocalCredentials
    from services.auth.issuer.passwords import hash_password

    admin = AdminUsers.insert(id="adm-active", email="active@example.com", is_active=True)
    LocalCredentials.upsert(admin.id, hash_password(TEMP_PASSWORD), must_change=True)
    r = _post(issuer, email="active@example.com")
    assert "Choose a new password" in r.text


# ------------------------------------------------------------------ audits


def test_unknown_account_is_audited(issuer):
    _post(issuer, email="nobody@example.com")
    rows = _audit("auth.local.login_failed")
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].payload == {"email": "nobody@example.com", "reason": "unknown_account"}
    assert rows[0].ip is not None


def test_bad_passwords_lockout_and_blocked_attempts_are_audited(issuer):
    for _ in range(3):
        _post(issuer, password="wrong-password-xyz")
    assert len(_audit("auth.local.login_failed")) == 3
    locked = _audit("auth.local.locked_out")
    assert len(locked) == 1 and locked[0].payload["failed_attempts"] == 3
    r = _post(issuer)  # correct password, but locked
    assert "temporarily locked" in r.text
    assert len(_audit("auth.local.login_blocked")) == 1


def test_passwords_never_reach_the_audit_payload(issuer):
    _post(issuer, password="wrong-password-xyz")
    _post(issuer, new_password=NEW_PASSWORD, new_password_confirm=NEW_PASSWORD)
    from models.admin_audit_log import AdminAuditLogs

    blob = repr([r.payload for r in AdminAuditLogs.list_filtered()])
    for secret in ("wrong-password-xyz", TEMP_PASSWORD, NEW_PASSWORD):
        assert secret not in blob


# -------------------------------------------------------- issuer default


@pytest.mark.parametrize(
    "mode, issuer_env, app_base, explicit, expected",
    [
        ("dev", "https://idp.example.com", "https://emly.example.com", None, True),
        ("prod", None, "https://emly.example.com", None, True),  # first boot, no IdP
        ("prod", "https://idp.example.com", "https://emly.example.com", None, False),
        ("prod", "https://emly.example.com/", "https://emly.example.com", None, True),  # embedded, custom iss
        ("prod", "https://idp.example.com", "https://emly.example.com", "true", True),
        ("dev", None, None, "false", False),
    ],
)
def test_local_issuer_default(monkeypatch, mode, issuer_env, app_base, explicit, expected):
    from services.auth.issuer.factory import is_local_issuer_enabled

    monkeypatch.setenv("MODE", mode)
    for name, value in (("AUTH_OIDC_ISSUER", issuer_env), ("APP_BASE_URL", app_base),
                        ("AUTH_LOCAL_ISSUER_ENABLED", explicit)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert is_local_issuer_enabled() is expected


# ------------------------------------------------- lockout timestamp bug


def test_lockout_check_does_not_crash(isolated_db):
    """Regression: is_locked compared an aware 'now' with the stored value
    (a str on SQLite, naive on Postgres) and raised TypeError."""
    from models.local_credentials import LocalCredential, LocalCredentials

    LocalCredentials.upsert("lock-k", "h")
    LocalCredentials.record_failure("lock-k", lockout_threshold=1, lockout_duration_seconds=60)
    assert LocalCredentials.is_locked("lock-k") is True
    # Rows written by the old code stored an offset-bearing string.
    LocalCredential.update(locked_until="2000-01-01 00:00:00+00:00").where(
        LocalCredential.admin_id == "lock-k").execute()
    assert LocalCredentials.is_locked("lock-k") is False
    LocalCredentials.delete("lock-k")
