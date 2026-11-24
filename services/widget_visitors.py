"""Visitor checks shared by the widget's text chat and voice calls."""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def session_owned_by_other(bot_id: str, session_id: str, user_id: str) -> bool:
    """True when ``session_id`` already belongs to a different visitor.

    Session ids are minted client-side, so a token for visitor A must not
    continue visitor B's thread."""
    from models.emly_sessions import EMLYSessions

    existing = EMLYSessions.get(bot_id, str(session_id))
    return bool(existing is not None and existing.user_id and existing.user_id != user_id)


def ensure_end_user(bot_id: str, user_id: Optional[str], *, ip: str, browser: str) -> None:
    """Create the visitor's row on first contact. Safe to call concurrently."""
    from models.emly_users import EMLYUsers

    if not user_id or EMLYUsers.get_user_by_id(bot_id, user_id) is not None:
        return
    try:
        EMLYUsers.insert_new_user(
            bot_id=bot_id,
            id=user_id,
            first_name=None, last_name=None, email=None, phone=None,
            ip=ip,
            browser=browser,
            meta=None,
        )
    except Exception:
        if EMLYUsers.get_user_by_id(bot_id, user_id) is None:
            log.exception("Failed to auto-create end-user for bot=%s user=%s", bot_id, user_id)
        else:
            log.info("Concurrent insert for bot=%s user=%s — using existing row", bot_id, user_id)
