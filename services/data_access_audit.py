"""G5e: optionally audit who reads conversations and visitor details.

The audit log records sign-ins, permission and configuration changes,
but not reads: nothing showed which admin opened which conversation or
looked up a visitor's contact details. With ``AUDIT_DATA_ACCESS=true``
each such read writes a ``data.read`` row:

- ``what``: ``conversation_list``, ``conversation``, ``message_list``,
  ``message``, ``visitor_list`` or ``visitor``;
- the session, message or visitor id it read (lists have none);
- the route, and which filters a list used (``search`` when the
  full-text search was used). Never the search text or any content.

Like the superadmin audit (G4a), one row per admin, bot, kind and target
per ``WINDOW_SECONDS``, so paging through a list or re-opening a
conversation doesn't flood the log. The window is per process.

When the setting is on, these rows follow ``AUDIT_FAIL_CLOSED``: if one
can't be written, the read fails with 503 rather than happen unaudited.
Off by default.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable, Optional

log = logging.getLogger(__name__)

ENV = "AUDIT_DATA_ACCESS"
ACTION = "data.read"
WINDOW_SECONDS = 600

_TARGET_TYPES = {
    "conversation": "session",
    "message": "message",
    "visitor": "end_user",
}

_seen: dict[tuple, float] = {}
_lock = threading.Lock()


def enabled() -> bool:
    from utils.runtime import env_flag

    return env_flag(ENV, default=False)


def record_read(
    admin,
    bot_id: str,
    what: str,
    target_id: Optional[str] = None,
    *,
    filters: Iterable[str] = (),
) -> None:
    """Audit one read of conversation or visitor data, if enabled.

    Raises ``AuditUnavailable`` (a 503) when the row can't be written and
    fail-closed is on; never raises otherwise."""
    if not enabled():
        return
    from services.audit import audit, current_request_route

    key = (admin.id, bot_id, what, target_id)
    now = time.monotonic()
    with _lock:
        last = _seen.get(key)
        if last is not None and now - last < WINDOW_SECONDS:
            return
    method, route = current_request_route()
    payload = {"what": what, "method": method, "route": route}
    used = sorted(set(filters))
    if used:
        payload["filters"] = used
    audit(ACTION, admin_id=admin.id, bot_id=bot_id,
          target_type=_TARGET_TYPES.get(what), target_id=target_id, payload=payload)
    # The window starts only once the row is written: a fail-closed
    # failure raises above, so the next read tries again.
    with _lock:
        _seen[key] = now
        if len(_seen) > 10_000:
            cutoff = now - WINDOW_SECONDS
            for k in [k for k, t in _seen.items() if t < cutoff]:
                del _seen[k]


def used_filters(**values) -> list[str]:
    """Names of the filters that were given, for the audit payload."""
    return [name for name, value in values.items() if value not in (None, "")]


def reset_for_tests() -> None:
    with _lock:
        _seen.clear()
