"""OTP issuance and verification for widget auth flows."""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
import re
import secrets
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    EMAIL_FROM,
    EMAIL_OTP_TEMPLATE,
    EMAIL_PASSWORD,
    EMAIL_PORT,
    EMAIL_SERVER,
    EMAIL_USER,
)
from models.otp_auth import OtpAuthorization
from services.email_service import EmailSender
from services.scheduler import EPHEMERAL_JOBSTORE, scheduler
from services.secrets import derive_key

log = logging.getLogger(__name__)

# A code is invalidated after this many verification attempts.
MAX_ATTEMPTS = 5
DEFAULT_EXPIRES_IN_MINUTES = 5
# Workspace setting key for the code lifetime, and its accepted range. Kept
# short: a 6-digit code is only as strong as its validity window.
WORKSPACE_EXPIRY_KEY = "otp_expires_in_minutes"
MIN_EXPIRES_IN_MINUTES = 1
MAX_EXPIRES_IN_MINUTES = 30


# S3: a code goes to exactly one plain address: no display name, no list,
# no whitespace or header-breaking characters.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                       r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$")
MAX_EMAIL_LENGTH = 254


def normalize_otp_email(value) -> str | None:
    """The single address a code may be sent to, lower-cased, or None."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_EMAIL_LENGTH or not _EMAIL_RE.match(candidate):
        return None
    return candidate.lower()


def bot_verifies_email(cfg) -> bool:
    """Whether any of the bot's forms asks for email verification."""
    for entry in cfg.c_forms_selected or []:
        for form in entry.values():
            if form.trigger.get("verify_action"):
                return True
    return False


def generate_otp() -> str:
    """Six-digit code from a CSPRNG."""
    return str(secrets.randbelow(900000) + 100000)


def hash_code(bot_id: str, user_id: str, code: str) -> str:
    """HMAC-SHA256 of the code, bound to the bot and user so a stored hash
    can't be replayed against another (bot, user) row."""
    key = derive_key("otp-v1")
    msg = f"{bot_id}\x00{user_id}\x00{code}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def valid_expiry_minutes(value) -> bool:
    """True for a whole number of minutes within the accepted range."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_EXPIRES_IN_MINUTES <= value <= MAX_EXPIRES_IN_MINUTES
    )


def _otp_expires_in(bot_id: str) -> int:
    """Code lifetime in minutes: the workspace default
    (``otp_expires_in_minutes``) when set and valid, else 5."""
    try:
        from models.workspace_settings import WorkspaceSettingsStore

        value = WorkspaceSettingsStore.get().settings.get(WORKSPACE_EXPIRY_KEY)
    except Exception:
        log.warning("workspace settings read failed; using default OTP expiry", exc_info=True)
        return DEFAULT_EXPIRES_IN_MINUTES
    if value is None:
        return DEFAULT_EXPIRES_IN_MINUTES
    if not valid_expiry_minutes(value):
        log.warning("ignoring invalid workspace %s; using default", WORKSPACE_EXPIRY_KEY)
        return DEFAULT_EXPIRES_IN_MINUTES
    return value


def create_otp(payload_data: dict, bot_id: str) -> dict:
    """Issue (or re-issue) an OTP for a user under ``bot_id`` and email it.

    Only the HMAC is stored. The plaintext travels to the email job via an
    in-memory jobstore, so it never touches the database or the persistent
    APScheduler store.
    """
    user_id = payload_data["user_id"]
    code = generate_otp()
    row = OtpAuthorization.upsert_otp(
        bot_id=bot_id,
        user_id=user_id,
        otp_type=payload_data.get("otp_type") or "email",
        otp_hash=hash_code(bot_id, user_id, code),
        expires_in=_otp_expires_in(bot_id),
    )
    scheduler.add_job(
        send_otp_in_email,
        args=[{**payload_data, "otp": code}],
        jobstore=EPHEMERAL_JOBSTORE,
    )
    return {k: v for k, v in row.items() if k != "otp"}


def verify_otp(bot_id: str, user_id: str, code: str) -> bool:
    """Check a submitted code. Each call uses one attempt; the code is
    deleted after ``MAX_ATTEMPTS`` or on first success (single use)."""
    row = OtpAuthorization.get_otp(bot_id=bot_id, user_id=user_id)
    if row is None:
        return False

    expires_at = row.get("expires_at")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if expires_at is None or now >= expires_at:
        OtpAuthorization.delete_by_id(row["id"])
        return False

    if not OtpAuthorization.claim_attempt(row["id"], MAX_ATTEMPTS):
        OtpAuthorization.delete_by_id(row["id"])
        return False

    expected = row.get("otp") or ""
    if not hmac.compare_digest(expected, hash_code(bot_id, user_id, str(code or "").strip())):
        if row.get("attempts", 0) + 1 >= MAX_ATTEMPTS:
            OtpAuthorization.delete_by_id(row["id"])
        return False

    # Single use: only the request that actually deletes the row succeeds.
    return OtpAuthorization.delete_by_id(row["id"])


def authorize_otp(payload_data: dict, bot_id: str) -> dict:
    if verify_otp(bot_id, payload_data["user_id"], payload_data.get("otp", "")):
        return {"code": 200, "message": "OTP authorized successfully"}
    return {"code": 401, "message": "Invalid OTP"}


def send_otp_in_email(payload_data: dict) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Emly Labs Verification Code"
    msg["From"] = EMAIL_FROM
    address = normalize_otp_email(payload_data["user_id"])
    if address is None:  # the route checks first; never send to anything else
        raise ValueError("OTP recipient is not a single email address")
    msg["To"] = address

    content = EMAIL_OTP_TEMPLATE
    content = content.replace("{otp_code}", html.escape(payload_data["otp"]))
    content = content.replace("{email}", html.escape(address))
    first_name, _, _ = address.partition("@")
    content = content.replace("{first_name}", html.escape(first_name))

    msg.attach(MIMEText(content, "html"))
    EmailSender(
        smtp_server=EMAIL_SERVER,
        port=EMAIL_PORT,
        username=EMAIL_USER,
        password=EMAIL_PASSWORD,
    ).send_email(msg)
