"""Phase 6 backend-backfill — heuristic deflection classifier.

A "deflected" turn is an assistant reply that resolves the user's
question without escalating to a human. The runtime can't tell with
certainty (that needs a real classifier), so this module ships a
keyword-based heuristic that's auditable, opt-in per bot, and tagged
in the persisted column (``deflection_method='heuristic'``) so
analytics callers can distinguish it from admin overrides.

Default behaviour: **off**. Operators opt in by setting
``bots.config_json["deflection_heuristic_enabled"] = true``. Until
then `compute_deflection()` returns ``None`` and the column stays
``NULL`` — the Analytics UI surfaces ``—`` for the rate.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

# Default escalation keywords. Any reply that mentions one of these is
# treated as NOT deflected (the bot is handing off rather than resolving).
# Bots can override via ``deflection_escalation_keywords`` in config.
_DEFAULT_ESCALATION_KEYWORDS = (
    "escalate",
    "escalating",
    "escalation",
    "human agent",
    "human support",
    "human representative",
    "transfer you",
    "transferring you",
    "connect you to",
    "connect you with",
    "live agent",
    "support team",
    "let me get a human",
    "i'll have someone",
    "let me have someone",
)


def _config_for_bot(bot_id: str) -> Dict[str, Any]:
    """Best-effort fetch of the per-bot config blob. Returns ``{}`` on any
    error so we never block message persistence on config reads.

    Reads the raw `bots.config_json` dict directly rather than the
    Pydantic-validated `ActiveBotConfig` so feature-flag fields that
    haven't been added to the schema yet (like
    `deflection_heuristic_enabled`) don't trip validation.
    """
    try:
        from models.bots import Bots

        bot = Bots.get_by_id(bot_id)
        if bot is None:
            return {}
        return dict(bot.config_json or {})
    except Exception:
        log.debug("deflection: bot config fetch failed for %s", bot_id, exc_info=True)
        return {}


def is_enabled(bot_id: str) -> bool:
    cfg = _config_for_bot(bot_id)
    return bool(cfg.get("deflection_heuristic_enabled", False))


def compute_deflection(
    bot_id: str,
    assistant_text: str,
) -> Optional[Tuple[bool, str]]:
    """Return ``(is_deflected, method)`` or ``None`` if the heuristic is
    disabled for this bot. ``method`` is always ``"heuristic"`` here —
    admin overrides write a different value via the API.

    The heuristic: if the assistant reply contains any escalation keyword,
    the turn was an escalation (not deflected); otherwise it's deflected.
    """
    if not assistant_text:
        return None
    cfg = _config_for_bot(bot_id)
    if not cfg.get("deflection_heuristic_enabled", False):
        return None
    keywords = cfg.get("deflection_escalation_keywords") or list(_DEFAULT_ESCALATION_KEYWORDS)
    text = assistant_text.lower()
    for kw in keywords:
        if not isinstance(kw, str) or not kw:
            continue
        # Simple substring check is sufficient for natural-language replies.
        # `\b` boundaries would over-fragment phrases like "let me get a
        # human" — substring is more forgiving.
        if kw.lower() in text:
            return False, "heuristic"
    return True, "heuristic"
