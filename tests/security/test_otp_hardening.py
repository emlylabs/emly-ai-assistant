"""A7: one-time passcodes expire, are attempt-limited, single-use and are
never stored in plaintext."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def otp_env(isolated_db, monkeypatch):
    """A bot plus a capture of the codes that would be emailed."""
    from models.bots import Bots
    from models.otp_auth import OtpAuth
    from services import otp as otp_mod

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"o-{uuid.uuid4().hex[:6]}", name="O")
    sent: list[dict] = []

    class _Sched:
        def add_job(self, func, args=None, jobstore=None, **_kw):
            assert jobstore == otp_mod.EPHEMERAL_JOBSTORE, "OTP job must not hit the persistent jobstore"
            sent.append(args[0])

    monkeypatch.setattr(otp_mod, "scheduler", _Sched())
    OtpAuth.delete().execute()
    yield {"bot": bot, "sent": sent, "otp": otp_mod}
    OtpAuth.delete().execute()


def _issue(env, user="alice@example.com") -> str:
    env["otp"].create_otp({"user_id": user, "otp_type": "email"}, bot_id=env["bot"].id)
    return env["sent"][-1]["otp"]


def test_generated_code_is_six_digits_from_secrets(monkeypatch):
    from services import otp as otp_mod

    calls = []
    monkeypatch.setattr(otp_mod.secrets, "randbelow", lambda n: calls.append(n) or 0)
    assert otp_mod.generate_otp() == "100000"
    assert calls == [900000]


def test_correct_code_verifies_once(otp_env):
    code = _issue(otp_env)
    bot_id = otp_env["bot"].id
    assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", code) is True
    # Single use.
    assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", code) is False


def test_code_is_bound_to_user_and_bot(otp_env):
    from models.bots import Bots

    code = _issue(otp_env)
    other_bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"p-{uuid.uuid4().hex[:6]}", name="P")
    assert otp_env["otp"].verify_otp(otp_env["bot"].id, "mallory@example.com", code) is False
    assert otp_env["otp"].verify_otp(other_bot.id, "alice@example.com", code) is False


def test_code_expires(otp_env):
    from models.otp_auth import OtpAuth

    code = _issue(otp_env)
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
    OtpAuth.update(expires_at=past).execute()
    assert otp_env["otp"].verify_otp(otp_env["bot"].id, "alice@example.com", code) is False


def test_expiry_is_stamped_at_issue(otp_env):
    from models.otp_auth import OtpAuthorization

    before = datetime.now(timezone.utc).replace(tzinfo=None)
    _issue(otp_env)
    row = OtpAuthorization.get_otp(otp_env["bot"].id, "alice@example.com")
    delta = row["expires_at"] - before
    assert timedelta(minutes=4, seconds=50) < delta <= timedelta(minutes=5, seconds=5)


def test_sixth_attempt_fails_even_with_correct_code(otp_env):
    code = _issue(otp_env)
    bot_id = otp_env["bot"].id
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", wrong) is False
    assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", code) is False


def test_fifth_attempt_can_still_succeed(otp_env):
    code = _issue(otp_env)
    bot_id = otp_env["bot"].id
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", wrong) is False
    assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", code) is True


def test_reissue_resets_attempts_and_invalidates_old_code(otp_env):
    old = _issue(otp_env)
    bot_id = otp_env["bot"].id
    for _ in range(3):
        otp_env["otp"].verify_otp(bot_id, "alice@example.com", "x")
    new = _issue(otp_env)
    if new != old:
        assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", old) is False
        new = _issue(otp_env)
    for _ in range(4):
        otp_env["otp"].verify_otp(bot_id, "alice@example.com", "x")
    assert otp_env["otp"].verify_otp(bot_id, "alice@example.com", new) is True


def test_no_plaintext_code_in_database(otp_env):
    from config import DATA_DIR
    from db.db import DB

    code = _issue(otp_env)
    # Our model row stores a 64-char hex HMAC, not the code.
    from models.otp_auth import OtpAuthorization

    row = OtpAuthorization.get_otp(otp_env["bot"].id, "alice@example.com")
    assert row["otp"] != code
    assert len(row["otp"]) == 64

    # And the code appears nowhere in the otp_auth table.
    cursor = DB.execute_sql("SELECT * FROM otp_auth")
    for db_row in cursor.fetchall():
        assert all(code not in str(v) for v in db_row)

    # The persistent APScheduler store must not hold it either.
    jobs_db = Path(DATA_DIR) / "actions.db"
    if jobs_db.exists():
        assert code.encode() not in jobs_db.read_bytes()


def test_authorize_otp_route_shape(otp_env):
    code = _issue(otp_env)
    bot_id = otp_env["bot"].id
    bad = otp_env["otp"].authorize_otp({"user_id": "alice@example.com", "otp": "nope"}, bot_id)
    assert bad["code"] == 401
    ok = otp_env["otp"].authorize_otp({"user_id": "alice@example.com", "otp": code}, bot_id)
    assert ok["code"] == 200
