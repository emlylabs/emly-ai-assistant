"""Per-request logging context.

Phase 6.5: every log line in this process should carry the bot/user/
session it relates to. Setting this up via ``contextvars`` means
existing ``log.info(...)`` call sites pick up the context automatically
— no wide refactor.

To consume the context in a structured-log formatter, read the
``record`` extras the filter below injects.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Optional

bot_id_var: ContextVar[Optional[str]] = ContextVar("bot_id", default=None)
user_id_var: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
channel_type_var: ContextVar[Optional[str]] = ContextVar("channel_type", default=None)
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class ContextInjectingFilter(logging.Filter):
    """Logging filter that decorates every record with the active
    contextvars so JSON formatters can include them automatically."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.bot_id = bot_id_var.get()
        record.user_id = user_id_var.get()
        record.session_id = session_id_var.get()
        record.channel_type = channel_type_var.get()
        record.request_id = request_id_var.get()
        return True


def install() -> None:
    """Attach the filter to the root logger. Idempotent."""
    root = logging.getLogger()
    if any(isinstance(f, ContextInjectingFilter) for f in root.filters):
        return
    root.addFilter(ContextInjectingFilter())
