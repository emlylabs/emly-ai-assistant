"""Tests for ``services/auth/widget_hmac.py``.

Uses the BOT_SECRETS_KEY whatever's configured by the test conftest. We
don't need a fresh keystore per test because the HMAC is content-keyed.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from services.auth.widget_hmac import (
    WidgetTokenError,
    issue,
    verify,
)


def test_round_trip_returns_principal():
    token = issue(bot_id="bot-1", user_id="u-1", session_id="s-1", key_version=1)
    p = verify(token, expected_bot_id="bot-1", current_key_version=1)
    assert p.bot_id == "bot-1"
    assert p.user_id == "u-1"
    assert p.session_id == "s-1"
    assert p.key_version == 1
    assert p.expires_at > p.issued_at


def test_token_for_one_bot_rejected_on_another():
    token = issue(bot_id="bot-A", user_id="u", session_id="s", key_version=1)
    with pytest.raises(WidgetTokenError, match="bot_mismatch"):
        verify(token, expected_bot_id="bot-B", current_key_version=1)


def test_tampered_payload_rejected():
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1)
    payload, sig = token.rsplit(".", 1)
    # Flip a bit in the payload by swapping a character.
    tampered = payload[:-1] + ("A" if payload[-1] != "A" else "B") + "." + sig
    with pytest.raises(WidgetTokenError):
        verify(tampered, expected_bot_id="bot-1", current_key_version=1)


def test_tampered_signature_rejected():
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1)
    payload, sig = token.rsplit(".", 1)
    tampered = payload + "." + sig[:-1] + ("A" if sig[-1] != "A" else "B")
    with pytest.raises(WidgetTokenError, match="bad_signature"):
        verify(tampered, expected_bot_id="bot-1", current_key_version=1)


def test_expired_token_rejected():
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1, ttl_seconds=-1)
    with pytest.raises(WidgetTokenError, match="expired"):
        verify(token, expected_bot_id="bot-1", current_key_version=1)


def test_token_signed_by_different_bot_rejected_even_when_replayed_with_same_id():
    """Per-bot HKDF: changing the bot_id changes the signing key, so a token
    minted for bot-A and replayed claiming to be bot-B has the wrong sig."""
    token = issue(bot_id="bot-A", user_id="u", session_id="s", key_version=1)
    # Manually rewrite the payload's bot field to bot-B but keep the signature.
    import base64, json
    payload_b64, sig = token.rsplit(".", 1)
    pad = "=" * (-len(payload_b64) % 4)
    decoded = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    decoded["bot"] = "bot-B"
    new_payload_b64 = base64.urlsafe_b64encode(json.dumps(decoded, separators=(",", ":")).encode("utf-8")).rstrip(b"=").decode("ascii")
    forged = f"{new_payload_b64}.{sig}"
    with pytest.raises(WidgetTokenError):
        verify(forged, expected_bot_id="bot-B", current_key_version=1)


def test_old_version_rejected_when_no_grace_window():
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1)
    with pytest.raises(WidgetTokenError, match="stale_key_version"):
        verify(token, expected_bot_id="bot-1", current_key_version=2)


def test_old_version_accepted_during_grace_window(monkeypatch):
    """Token at version=1 still verifies if rotation to v2 happened recently."""
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1)
    just_rotated = datetime.now(timezone.utc) - timedelta(seconds=10)
    p = verify(
        token,
        expected_bot_id="bot-1",
        current_key_version=2,
        rotated_at=just_rotated,
    )
    assert p.user_id == "u"


def test_old_version_rejected_after_grace_window(monkeypatch):
    monkeypatch.setenv("WIDGET_TOKEN_ROTATION_GRACE_SECONDS", "1")
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1)
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=3600)
    with pytest.raises(WidgetTokenError, match="stale_key_version"):
        verify(token, expected_bot_id="bot-1", current_key_version=2, rotated_at=long_ago)


def test_two_versions_back_always_rejected_even_in_grace():
    token = issue(bot_id="bot-1", user_id="u", session_id="s", key_version=1)
    just_rotated = datetime.now(timezone.utc)
    with pytest.raises(WidgetTokenError, match="stale_key_version"):
        verify(token, expected_bot_id="bot-1", current_key_version=3, rotated_at=just_rotated)


def test_malformed_token_rejected():
    with pytest.raises(WidgetTokenError, match="malformed_token"):
        verify("not-a-token", expected_bot_id="bot-1", current_key_version=1)
    with pytest.raises(WidgetTokenError, match="malformed_token"):
        verify("only-one-part-no-dot", expected_bot_id="bot-1", current_key_version=1)


def test_two_tokens_for_same_user_have_different_iat():
    """Sanity: tokens minted at different times differ in payload."""
    t1 = issue(bot_id="b", user_id="u", session_id="s", key_version=1)
    time.sleep(1.01)  # ensure iat increments
    t2 = issue(bot_id="b", user_id="u", session_id="s", key_version=1)
    assert t1 != t2
