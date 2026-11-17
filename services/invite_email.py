"""Send space-invitation emails for new-style ``pending_admin`` rows.

The shape of the email is a single static HTML template
(``utils/space_invite_email.html``) with literal ``{placeholder}``
substitution — the same minimal pattern ``services/otp.py`` uses for
OTP verification emails, no Jinja runtime. Dispatched via APScheduler
(``services.scheduler``) so the inviting request returns immediately
and the queue persists across restarts.

The accept URL is built off ``PUBLIC_BASE_URL`` (preferred) /
``APP_BASE_URL`` (fallback). If neither is set we **fail closed** —
warn and drop the send rather than email a ``localhost`` link the
invitee can't reach.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from config import (
    BACKEND_DIR,
    EMAIL_FROM,
    EMAIL_PASSWORD,
    EMAIL_PORT,
    EMAIL_SERVER,
    EMAIL_USER,
)
from services.email_service import EmailSender
from services.scheduler import scheduler

log = logging.getLogger(__name__)


_TEMPLATE_PATH = Path(BACKEND_DIR / "utils" / "space_invite_email.html").resolve()


def _read_template() -> str:
    """Read the template every call so operators can hot-swap copy
    without a restart. The file is small enough that the cost is
    irrelevant compared to SMTP latency."""
    try:
        with open(_TEMPLATE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        log.error("Invite email template missing at %s", _TEMPLATE_PATH)
        return ""


def _public_base_url() -> Optional[str]:
    """Return the operator-configured public origin, or None if neither
    ``PUBLIC_BASE_URL`` nor ``APP_BASE_URL`` is set. Trailing slash
    stripped for clean URL concatenation."""
    for key in ("PUBLIC_BASE_URL", "APP_BASE_URL"):
        raw = os.environ.get(key, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def build_accept_url(token: str) -> Optional[str]:
    """Compose the URL recipients click in the invite email.

    Returns ``None`` if no public origin is configured — callers should
    skip sending in that case rather than build a ``localhost`` link.
    """
    base = _public_base_url()
    if base is None:
        return None
    return f"{base}/accept-invite?token={token}"


def _format_expires(expires_at: Optional[datetime]) -> str:
    if expires_at is None:
        return "never"
    try:
        if expires_at.tzinfo is None:
            stamped = expires_at.replace(tzinfo=timezone.utc)
        else:
            stamped = expires_at.astimezone(timezone.utc)
        return stamped.strftime("%b %d, %Y")
    except Exception:
        return str(expires_at)


def schedule_invite_email(
    *,
    pending_id: str,
    inviter_id: str,
    space_name: str,
    role: str,
) -> bool:
    """Enqueue an invitation email send.

    N7: the job store is a file on disk (``actions.db``) that outlives
    the request, so the job carries only ids, the space name and the
    role. The invite token, the accept URL and both email addresses are
    looked up when the job runs (``_send_invite``).

    Returns ``True`` if the job was scheduled, ``False`` if it was
    dropped (e.g. ``PUBLIC_BASE_URL`` unset). The caller can surface
    that to the UI so the operator knows email isn't going out.
    """
    if _public_base_url() is None:
        log.warning(
            "Skipping invite email for pending=%s: PUBLIC_BASE_URL / APP_BASE_URL unset; "
            "set one of them so invitees receive a clickable link.",
            pending_id,
        )
        return False
    scheduler.add_job(_send_invite, args=[pending_id, inviter_id, space_name, role])
    log.info("Queued invite email for pending=%s space=%s role=%s", pending_id, space_name, role)
    return True


def _send_invite(pending_id: str, inviter_id: str, space_name: str, role: str) -> None:
    """Scheduler job: send the email for an invite that is still open."""
    from models.admin_users import AdminUsers
    from models.pending_admins import STATUS_PENDING, PendingAdmins

    pending = PendingAdmins.get_by_id(pending_id)
    if pending is None or pending.status != STATUS_PENDING or not pending.token:
        log.info("Invite email for pending=%s not sent: invite no longer open", pending_id)
        return
    if pending.expires_at is not None and _as_utc(pending.expires_at) < datetime.now(timezone.utc):
        log.info("Invite email for pending=%s not sent: invite expired", pending_id)
        return
    accept_url = build_accept_url(pending.token)
    if accept_url is None:
        log.warning("Invite email for pending=%s not sent: PUBLIC_BASE_URL / APP_BASE_URL unset", pending_id)
        return
    inviter = AdminUsers.get_by_id_including_deleted(inviter_id) if inviter_id else None
    inviter_email = inviter.email if inviter else ""
    _send_invite_email({
        "email": pending.email,
        "inviter_name": (inviter.name if inviter else None) or inviter_email or "An administrator",
        "inviter_email": inviter_email,
        "space_name": space_name,
        "role": role,
        "accept_url": accept_url,
        "expires_human": _format_expires(pending.expires_at),
    }, ref=pending_id)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _send_invite_email(payload: dict, ref: Optional[str] = None) -> None:
    """Render the template, build the MIME envelope, and send via SMTP.

    Also the target of invite jobs queued before N7, whose arguments are
    the rendered payload; those drain on the next start."""
    template = _read_template()
    if not template:
        return
    content = template
    for placeholder, value in payload.items():
        if placeholder == "email":
            continue
        content = content.replace("{" + placeholder + "}", str(value or ""))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"You're invited to {payload['space_name']} on Emly Labs"
    msg["From"] = EMAIL_FROM
    msg["To"] = payload["email"]
    msg.attach(MIMEText(content, "html"))

    try:
        EmailSender(
            smtp_server=EMAIL_SERVER,
            port=EMAIL_PORT,
            username=EMAIL_USER,
            password=EMAIL_PASSWORD,
        ).send_email(msg)
    except Exception as exc:
        log.error("Invite email send failed (pending=%s): %s", ref or "legacy-job", type(exc).__name__)


def sweep_expired_invites() -> int:
    """Scheduler job: mark pending invites past ``expires_at`` as expired.
    Module-level so APScheduler can store it by import path."""
    from models.pending_admins import PendingAdmins

    try:
        n = PendingAdmins.mark_expired_sweep()
    except Exception:
        log.exception("invite expiry sweep failed")
        return 0
    if n:
        log.info("Marked %d invite(s) expired by sweep", n)
    return n
