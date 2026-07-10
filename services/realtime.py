"""Phase 9 backend-backfill — in-process pub/sub for live conversation feed.

Each subscriber gets its own bounded `queue.Queue`. Publishers iterate the
list of queues registered for the bot and offer the event to each. Slow
consumers drop on full — we'd rather lose an event than block the chat
write path. The SSE endpoint heartbeats every 30s so dropped events still
trigger a "stale" repaint via UI poll fallback.

**Single-replica only.** When `WEB_CONCURRENCY > 1`, pods don't share
the queue, so the SSE endpoint returns 503 with a documented
``X-Reason`` header so the UI can fall back to polling. The Redis
pub/sub replacement is in `multi-bot-plan.md`'s S3+Redis track.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# bot_id → list of subscriber queues. Add/remove under `_LOCK`.
_SUBSCRIBERS: Dict[str, List["queue.Queue[Dict[str, Any]]"]] = {}
_LOCK = threading.Lock()
_QUEUE_MAX = 256


def subscribe(bot_id: str) -> "queue.Queue[Dict[str, Any]]":
    """Register a new subscriber queue. Caller must `unsubscribe()` when
    finished, otherwise we leak the queue."""
    q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=_QUEUE_MAX)
    with _LOCK:
        _SUBSCRIBERS.setdefault(bot_id, []).append(q)
    return q


def unsubscribe(bot_id: str, q: "queue.Queue[Dict[str, Any]]") -> None:
    with _LOCK:
        subs = _SUBSCRIBERS.get(bot_id)
        if not subs:
            return
        try:
            subs.remove(q)
        except ValueError:
            return
        if not subs:
            _SUBSCRIBERS.pop(bot_id, None)


def publish(bot_id: str, event: Dict[str, Any]) -> None:
    """Best-effort fan-out. Full subscriber queues silently drop the event —
    listeners that fall behind get a partial view, which is better than
    blocking the writer."""
    if not bot_id:
        return
    with _LOCK:
        subs = list(_SUBSCRIBERS.get(bot_id, ()))
    for q in subs:
        try:
            q.put_nowait(event)
        except queue.Full:
            # Don't surface drops in the hot path; the SSE endpoint logs
            # its own connection-level diagnostics.
            log.debug("realtime: subscriber queue full for bot=%s", bot_id)


def subscriber_count(bot_id: str) -> int:
    with _LOCK:
        return len(_SUBSCRIBERS.get(bot_id, ()))
