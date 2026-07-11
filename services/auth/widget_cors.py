"""Per-bot CORS enforcement for ``/widget/{slug}/*`` surfaces.

The global ``CORSMiddleware`` in ``main.py`` is wide-open (``allow_origins=["*"]``)
so the admin UI and legacy ``/emly/*`` endpoints keep their unchanged
posture. This middleware sits *outside* it and overrides the response for
widget paths only, applying each bot's
``LimitsConfig.widget_allowed_origins`` allowlist.

Stack ordering (last-added = outermost):

    add(CustomMiddleware)        # innermost
    add(OriginCheckMiddleware)
    add(CORSMiddleware, allow_origins=["*"])
    add(WidgetCORSMiddleware)    # outermost — intercepts widget preflight,
                                 # overwrites Allow-Origin on widget responses.

Bare ``"*"`` in the allowlist keeps the legacy "any origin" behaviour —
that's the schema default, so existing bots are unaffected. Specific
origins (``https://example.com``) match exactly; ``https://*.example.com``
matches any non-empty subdomain of ``example.com`` (boundary-anchored so
``https://*.com`` can't accidentally allow ``https://evil-example.com``).
Wildcards don't match the apex; admins list both if they want both.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterable, List, Optional, Tuple

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

log = logging.getLogger(__name__)

_WIDGET_PREFIX = "/widget/"
_DEFAULT_PREFLIGHT_HEADERS = "Content-Type, Authorization, X-Emly-UserID, X-Emly-SessionID, X-Emly-PageID, X-Emly-BotID, X-Request-ID"
_EXPOSE_HEADERS = "X-Emly-UserID, X-Emly-SessionID, X-Emly-PageID, X-Emly-BotID"
_PREFLIGHT_MAX_AGE = "600"

# Per-bot config lookups happen on every widget request. A 30s TTL cache
# keeps the hot chat path off Peewee without making admin allowlist edits
# feel sluggish.
_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Lock()
_cache: dict[str, Tuple[float, Optional[str], Tuple[str, ...]]] = {}


def _is_widget_path(path: str) -> Optional[str]:
    """Return the ``bot_ref`` for a ``/widget/{ref}/...`` request, else None.

    Accepts any depth (``/widget/{ref}/chat``,
    ``/widget/{ref}/messages/{id}/rate``, …) so CORS applies uniformly
    across every path-scoped widget surface. Requires a non-empty
    ``ref`` segment."""
    if not path.startswith(_WIDGET_PREFIX):
        return None
    parts = path.split("/")
    if len(parts) < 3 or not parts[2]:
        return None
    return parts[2]


def _normalize(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def origin_allowed(origin: Optional[str], allowlist: Iterable[str]) -> bool:
    """Pure decision function — kept side-effect-free so it's trivially
    testable without spinning up FastAPI."""
    entries = [str(e) for e in allowlist if isinstance(e, str)]
    if any(e.strip() == "*" for e in entries):
        return True
    if not origin:
        return False
    needle = _normalize(origin)
    for entry in entries:
        candidate = _normalize(entry)
        if not candidate or candidate == "*":
            continue
        if "*" not in candidate:
            if needle == candidate:
                return True
            continue
        # Pattern form: <scheme>://*.<suffix>
        if "://*." not in candidate:
            # We don't support mid-host or scheme wildcards — too easy to
            # write a pattern that matches more than the admin intended.
            continue
        scheme, _, host_part = candidate.partition("://")
        if not host_part.startswith("*."):
            continue
        suffix = host_part[2:]  # drop the leading "*."
        if "." not in suffix:
            # "*.com" would let any .com origin in — reject the pattern.
            continue
        needle_scheme, _, needle_host = needle.partition("://")
        if needle_scheme != scheme or not needle_host:
            continue
        # Must be a strict subdomain: ``<sub>.<suffix>`` with non-empty
        # ``<sub>``. Refuse the bare apex match.
        if needle_host == suffix:
            continue
        if needle_host.endswith("." + suffix):
            return True
    return False


def _lookup_allowlist(bot_ref: str) -> Tuple[Optional[str], Tuple[str, ...]]:
    """Resolve ``bot_ref`` → (bot_id, allowlist). Cached for ``_CACHE_TTL_SECONDS``.

    On any resolution failure returns ``(None, ())`` and lets the middleware
    decide whether to fall through (preflight) or 403 — the route handler
    will produce its own 404 for an unknown bot if we pass through.
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(bot_ref)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1], cached[2]

    bot_id: Optional[str] = None
    origins: Tuple[str, ...] = ()
    try:
        from routes.widget import resolve_bot
        from services.bot_config import get_config_for_bot

        bot = resolve_bot(bot_ref)
        bot_id = bot.id
        cfg = get_config_for_bot(bot.id)
        origins = tuple(cfg.limits.widget_allowed_origins or ())
    except HTTPException:
        # Unknown / deleted / inactive bot — leave both empty so the
        # caller falls through and the route handler 404s.
        bot_id = None
        origins = ()
    except Exception:
        log.exception("widget_cors: failed to resolve allowlist for %s", bot_ref)
        bot_id = None
        origins = ()

    with _cache_lock:
        _cache[bot_ref] = (now, bot_id, origins)
    return bot_id, origins


def _preflight_response(origin: Optional[str], allowed: bool, request_headers: Optional[str]) -> Response:
    """Always 200; only emit CORS headers if the origin is allowed."""
    resp = Response(status_code=200)
    if allowed and origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = request_headers or _DEFAULT_PREFLIGHT_HEADERS
        resp.headers["Access-Control-Max-Age"] = _PREFLIGHT_MAX_AGE
    return resp


def _apply_cors(response: Response, origin: str) -> None:
    """Overwrite whatever the inner global ``CORSMiddleware`` set with the
    bot-resolved value. Outermost middleware wins on the way back out."""
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Expose-Headers"] = _EXPOSE_HEADERS


class WidgetCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        bot_ref = _is_widget_path(request.url.path)
        if bot_ref is None:
            return await call_next(request)

        origin = request.headers.get("origin") or request.headers.get("Origin")
        bot_id, allowlist = _lookup_allowlist(bot_ref)

        # Unknown bot: nothing to enforce — let the route handler return
        # its own 404. The browser will see no CORS headers, which is fine
        # (the response body it'd block is just a 404 anyway).
        if bot_id is None and not allowlist:
            if request.method.upper() == "OPTIONS":
                return _preflight_response(origin, allowed=False, request_headers=None)
            return await call_next(request)

        allowed = origin_allowed(origin, allowlist)

        if request.method.upper() == "OPTIONS":
            request_headers = request.headers.get("access-control-request-headers")
            return _preflight_response(origin, allowed=allowed, request_headers=request_headers)

        if origin is not None and not allowed:
            try:
                from services.audit import audit
                audit(
                    "widget.cors.origin_rejected",
                    bot_id=bot_id,
                    request=request,
                    success=False,
                    payload={"path": request.url.path, "origin": origin},
                )
            except Exception:
                log.debug("widget_cors audit log failed", exc_info=True)
            return JSONResponse(
                {"detail": {"code": "widget_origin_rejected", "origin": origin}},
                status_code=403,
            )

        response = await call_next(request)
        if origin is not None:
            # ``allowed`` is True here (we 403'd otherwise above) — overwrite
            # the inner global ``*`` with the resolved origin.
            _apply_cors(response, origin)
        return response


__all__ = ["WidgetCORSMiddleware", "origin_allowed"]
