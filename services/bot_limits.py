"""G13a: enforce a bot's cost limits before any model call.

``BotConfig.limits`` has three knobs that used to be stored but never
enforced:

- ``messages_per_minute_per_user``: visitor messages per minute from one
  end user of this bot;
- ``messages_per_minute_per_bot``: visitor messages per minute across all
  of the bot's end users;
- ``daily_token_cap``: answer tokens (prompt + completion, as recorded on
  assistant messages) the bot may spend in a rolling 24 hours.

Empty or 0 means no limit. ``check()`` runs in
``AgentService.process_message``, the one path the widget and every chat
channel use, so a refused message never reaches the model and the
visitor gets a short reply instead.

Per-minute counters are per process (the same in-memory storage as the
``AUTH_RATE_*`` limits). The token total is cached for
``TOKEN_TOTAL_TTL_SECONDS`` per bot, so the cap can be overshot by about
that much traffic. Router, slot-extraction, summary and enrichment calls
don't record tokens, so they don't count towards the cap.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

BUSY_REPLY = "Sorry, this assistant is getting a lot of messages right now. Please try again in a minute."
DAILY_CAP_REPLY = "Sorry, this assistant has reached its usage limit for today. Please try again later."

TOKEN_TOTAL_TTL_SECONDS = 30
_token_totals: Dict[str, Tuple[float, int]] = {}
_token_lock = threading.Lock()


def _positive(value) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def tokens_used_last_24h(bot_id: str) -> int:
    """Answer tokens recorded for ``bot_id`` in the last 24 hours (cached)."""
    now = time.monotonic()
    with _token_lock:
        cached = _token_totals.get(bot_id)
        if cached and now - cached[0] < TOKEN_TOTAL_TTL_SECONDS:
            return cached[1]
    from peewee import fn

    from models.emly_messages import EMLYMessage

    since = datetime.utcnow() - timedelta(hours=24)
    total = (
        EMLYMessage.select(
            fn.COALESCE(fn.SUM(fn.COALESCE(EMLYMessage.prompt_tokens, 0)
                               + fn.COALESCE(EMLYMessage.completion_tokens, 0)), 0)
        )
        .where((EMLYMessage.bot == bot_id) & (EMLYMessage.created_at >= since))
        .scalar()
    ) or 0
    with _token_lock:
        _token_totals[bot_id] = (now, int(total))
    return int(total)


def check(bot_id: str, user_id: str) -> Optional[str]:
    """The reply to send instead of answering, or ``None`` to go ahead.
    Never raises: a failure to read the limits lets the message through."""
    try:
        from services.bot_config import get_config_for_bot
        from services.auth.ratelimit import try_hit

        limits = get_config_for_bot(bot_id).limits
    except Exception:
        log.debug("bot limits unreadable for bot=%s; not enforcing", bot_id, exc_info=True)
        return None

    cap = _positive(limits.daily_token_cap)
    if cap is not None and tokens_used_last_24h(bot_id) >= cap:
        log.warning("Bot %s reached its daily token cap (%d); refusing", bot_id, cap)
        return DAILY_CAP_REPLY

    per_bot = _positive(limits.messages_per_minute_per_bot)
    if per_bot is not None and not try_hit(f"{per_bot}/minute", "bot_msgs", bot_id):
        log.info("Bot %s over messages_per_minute_per_bot (%d); refusing", bot_id, per_bot)
        return BUSY_REPLY

    per_user = _positive(limits.messages_per_minute_per_user)
    if per_user is not None and not try_hit(f"{per_user}/minute", "user_msgs", bot_id, user_id):
        log.info("Bot %s user over messages_per_minute_per_user (%d); refusing", bot_id, per_user)
        return BUSY_REPLY
    return None
