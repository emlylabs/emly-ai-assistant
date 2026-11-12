"""End-visitor erasure, per-bot retention and nightly housekeeping (D4).

Visitor data for one bot lives in several tables that are joined by
``(bot, user_id)`` rather than by foreign keys, so deleting the
``emly_user`` row cascades nowhere: every table is cleared explicitly.
Chat attachments are kept on disk under
``DATA_DIR/attachments/{user_id}/{file_id}/`` (outside the bot's folder)
and are removed with their rows.

Timestamps in these tables are naive and mostly UTC (a few use server
local time), so retention cut-offs are accurate to within the server's UTC
offset; retention is configured in days.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from config import DATA_DIR
from db.db import DB

log = logging.getLogger(__name__)

RETENTION_MIN_DAYS = 1
RETENTION_MAX_DAYS = 3650
# Expired OTP rows are kept this long after expiry (for the attempt
# counter), then deleted.
OTP_ROW_GRACE = timedelta(days=1)


def attachments_root() -> Path:
    return Path(DATA_DIR) / "attachments"


def _remove_attachment_dirs(files: Iterable[Tuple[str, str]]) -> int:
    """Delete ``attachments/{user_id}/{file_id}`` for each ``(user_id,
    file_id)``, and the user folder when it ends up empty. Returns the
    number of folders removed."""
    root = attachments_root().resolve()
    removed = 0
    for user_id, file_id in files:
        target = (root / str(user_id) / str(file_id)).resolve()
        if root not in target.parents:  # ids are ours, but never leave the root
            log.warning("refusing to delete attachment outside %s", root)
            continue
        if target.exists():
            try:
                shutil.rmtree(target)
                removed += 1
            except OSError:
                log.exception("could not delete attachment folder for file=%s", file_id)
        try:
            target.parent.rmdir()  # only succeeds when empty
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- erasure


def erase_end_user(bot_id: str, user_id: str) -> Dict[str, int]:
    """Delete everything stored about one visitor of one bot: their
    profile row, sessions, messages, form submissions, attachments (rows
    and files), channel identities and OTP rows. Returns per-kind counts.
    """
    from models.emly_files import EMLYFiles
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession
    from models.emly_user_action import EMLYUserActions
    from models.emly_user_channel_identities import EMLYUserChannelIdentity
    from models.emly_users import EMLYUser
    from models.otp_auth import OtpAuth

    user = EMLYUser.get_or_none((EMLYUser.bot == bot_id) & (EMLYUser.id == user_id))
    # OTP rows are keyed by the address the code was sent to.
    otp_keys = {user_id} | ({user.email} if user is not None and user.email else set())
    files = [(user_id, f.id) for f in
             EMLYFiles.select(EMLYFiles.id).where((EMLYFiles.bot == bot_id) & (EMLYFiles.user == user_id))]

    counts: Dict[str, int] = {}
    with DB.atomic():
        counts["submissions"] = EMLYUserActions.delete().where(
            (EMLYUserActions.bot == bot_id) & (EMLYUserActions.user == user_id)).execute()
        counts["messages"] = EMLYMessage.delete().where(
            (EMLYMessage.bot == bot_id) & (EMLYMessage.user_id == user_id)).execute()
        counts["sessions"] = EMLYSession.delete().where(
            (EMLYSession.bot_id == bot_id) & (EMLYSession.user_id == user_id)).execute()
        counts["attachments"] = EMLYFiles.delete().where(
            (EMLYFiles.bot == bot_id) & (EMLYFiles.user == user_id)).execute()
        counts["channel_identities"] = EMLYUserChannelIdentity.delete().where(
            (EMLYUserChannelIdentity.bot == bot_id) & (EMLYUserChannelIdentity.emly_user == user_id)).execute()
        counts["otps"] = OtpAuth.delete().where(
            (OtpAuth.bot == bot_id) & (OtpAuth.user_id.in_(list(otp_keys)))).execute()
        counts["profile"] = EMLYUser.delete().where(
            (EMLYUser.bot == bot_id) & (EMLYUser.id == user_id)).execute()
    counts["attachment_files"] = _remove_attachment_dirs(files)
    return counts


# -------------------------------------------------------------- retention


def valid_retention_days(value) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and RETENTION_MIN_DAYS <= value <= RETENTION_MAX_DAYS)


def purge_bot_conversations(bot_id: str, days: int, *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Delete one bot's conversations and submissions older than ``days``.

    Messages, submissions and attachments are removed by their own age;
    sessions by their last activity. A visitor profile is removed once it
    is itself older than the cut-off and nothing of theirs is left."""
    from models.emly_files import EMLYFiles
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession
    from models.emly_user_action import EMLYUserActions
    from models.emly_user_channel_identities import EMLYUserChannelIdentity
    from models.emly_users import EMLYUser
    from models.otp_auth import OtpAuth

    cutoff = (now or datetime.utcnow()) - timedelta(days=days)
    old_files = [(f.user_id, f.id) for f in EMLYFiles.select(EMLYFiles.id, EMLYFiles.user).where(
        (EMLYFiles.bot == bot_id) & EMLYFiles.user.is_null(False) & (EMLYFiles.created_at < cutoff))]

    counts: Dict[str, int] = {}
    with DB.atomic():
        counts["submissions"] = EMLYUserActions.delete().where(
            (EMLYUserActions.bot == bot_id) & (EMLYUserActions.created_at < cutoff)).execute()
        counts["messages"] = EMLYMessage.delete().where(
            (EMLYMessage.bot == bot_id) & (EMLYMessage.created_at < cutoff)).execute()
        counts["sessions"] = EMLYSession.delete().where(
            (EMLYSession.bot_id == bot_id) & (EMLYSession.last_activity_at < cutoff)).execute()
        counts["attachments"] = EMLYFiles.delete().where(
            EMLYFiles.id.in_([fid for _, fid in old_files])).execute() if old_files else 0

        def remaining(model, user_col, bot_col):
            return model.select(user_col).where(bot_col == bot_id)

        idle = (EMLYUser.select(EMLYUser.id).where(
            (EMLYUser.bot == bot_id)
            & (EMLYUser.created_at < cutoff)
            & ((EMLYUser.updated_at.is_null()) | (EMLYUser.updated_at < cutoff))
            & EMLYUser.id.not_in(remaining(EMLYMessage, EMLYMessage.user_id, EMLYMessage.bot))
            & EMLYUser.id.not_in(remaining(EMLYSession, EMLYSession.user_id, EMLYSession.bot_id))
            & EMLYUser.id.not_in(remaining(EMLYUserActions, EMLYUserActions.user, EMLYUserActions.bot))
            & EMLYUser.id.not_in(remaining(EMLYFiles, EMLYFiles.user, EMLYFiles.bot))
        ))
        idle_ids: List[str] = [u.id for u in idle]
        counts["profiles"] = 0
        if idle_ids:
            EMLYUserChannelIdentity.delete().where(
                (EMLYUserChannelIdentity.bot == bot_id) & EMLYUserChannelIdentity.emly_user.in_(idle_ids)).execute()
            OtpAuth.delete().where((OtpAuth.bot == bot_id) & OtpAuth.user_id.in_(idle_ids)).execute()
            counts["profiles"] = EMLYUser.delete().where(
                (EMLYUser.bot == bot_id) & EMLYUser.id.in_(idle_ids)).execute()
    counts["attachment_files"] = _remove_attachment_dirs(old_files)
    return counts


def _bot_retention_days(bot_id: str) -> Optional[int]:
    from services.bot_config import get_config_for_bot

    try:
        days = get_config_for_bot(bot_id).limits.retention_days
    except LookupError:
        return None
    return days if valid_retention_days(days) else None


# ----------------------------------------------------------- housekeeping


def webhook_dedupe_retention() -> timedelta:
    raw = os.environ.get("WEBHOOK_DEDUPE_RETENTION_HOURS", "").strip()
    try:
        hours = int(raw) if raw else 24
    except ValueError:
        log.warning("ignoring invalid WEBHOOK_DEDUPE_RETENTION_HOURS; using 24")
        hours = 24
    return timedelta(hours=max(hours, 1))


def purge_expired_otps(now: Optional[datetime] = None) -> int:
    from models.otp_auth import OtpAuth

    cutoff = (now or datetime.utcnow()) - OTP_ROW_GRACE
    return OtpAuth.delete().where(
        (OtpAuth.expires_at < cutoff) | (OtpAuth.expires_at.is_null() & (OtpAuth.created_at < cutoff))
    ).execute()


def purge_expired_data() -> Dict[str, object]:
    """Nightly job (registered by import path in ``main.py``): apply each
    bot's ``limits.retention_days``, then drop expired OTP rows and old
    webhook dedupe rows. Each step is independent; a failure is logged and
    the rest still run."""
    from models.bots import Bot
    from models.webhook_event_dedupe import WebhookEventDedupes

    summary: Dict[str, object] = {"bots": {}}
    for bot in Bot.select(Bot.id).where(Bot.is_deleted == False):  # noqa: E712
        try:
            days = _bot_retention_days(bot.id)
            if days:
                counts = purge_bot_conversations(bot.id, days)
                summary["bots"][bot.id] = counts
                if any(counts.values()):
                    log.info("Retention purge bot=%s days=%d %s", bot.id, days, counts)
        except Exception:
            log.exception("retention purge failed for bot=%s", bot.id)
    try:
        summary["otps"] = purge_expired_otps()
    except Exception:
        log.exception("expired OTP cleanup failed")
    try:
        summary["webhook_dedupe"] = WebhookEventDedupes.cleanup(older_than=webhook_dedupe_retention())
    except Exception:
        log.exception("webhook dedupe cleanup failed")
    return summary
