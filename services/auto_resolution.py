"""G7d: an automatic "likely resolved" signal, beside manual resolution.

The resolution rate counts only sessions an admin marked resolved, so on
a busy bot nobody triages it stays near zero. This counts, separately and
labelled as automatic, the sessions that look resolved from what
happened in them.

A session started in the window is **likely resolved** when nobody has
marked it either way (``is_resolved`` is unset), and:

- the bot answered at least once;
- no answer was rated thumbs-down;
- staff didn't take it over;
- no answer handed off to a human (the deflection heuristic, when a bot
  has it on, flags those as ``is_deflected = false``);
- and either an answer was rated thumbs-up, or the visitor has been
  quiet for ``IDLE_MINUTES`` (or the session was ended).

It is an estimate: a visitor who gave up without rating looks the same
as one who got their answer. Manual resolution always wins.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

IDLE_MINUTES = 30


def count_likely_resolved(sessions_query, bot_id: str, now: Optional[datetime] = None) -> int:
    """How many sessions in ``sessions_query`` (an ``EMLYSession`` select
    already filtered to the bot, window and channel) are likely resolved."""
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession
    from utils.clock import utc_now

    now = now or utc_now()
    msgs = EMLYMessage.select(EMLYMessage.session_id).where(EMLYMessage.bot == bot_id)
    assistant = msgs.where(EMLYMessage.role == "assistant")
    answered = assistant
    thumbs_down = assistant.where(EMLYMessage.rating == -1)
    thumbs_up = assistant.where(EMLYMessage.rating == 1)
    handed_off = assistant.where(EMLYMessage.is_deflected == False)  # noqa: E712
    finished = (
        EMLYSession.ended_at.is_null(False)
        | (EMLYSession.last_activity_at < now - timedelta(minutes=IDLE_MINUTES))
    )
    return (
        sessions_query
        .where(
            EMLYSession.is_resolved.is_null(True)
            & EMLYSession.taken_over_at.is_null(True)
            & EMLYSession.id.in_(answered)
            & EMLYSession.id.not_in(thumbs_down)
            & EMLYSession.id.not_in(handed_off)
            & (EMLYSession.id.in_(thumbs_up) | finished)
        )
        .count()
    )
