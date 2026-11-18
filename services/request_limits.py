"""Hard caps on request bodies, enforced as bytes arrive (G13c, S2).

Starlette reads (and for multipart, spools) a whole body before a route
or its auth dependency runs, so without a cap a stranger could send an
unbounded body to any endpoint, including ones that need a token they
don't have. This middleware rejects a POST/PUT/PATCH/DELETE with 413 once
its body exceeds the limit for its path: immediately when
``Content-Length`` says so, otherwise as soon as the running byte count
passes it.

Limits:

- ``REQUEST_MAX_KB`` (default 1024): every route that doesn't take files.
  JSON chat messages, configs, channel webhooks and forms fit easily.
- ``WIDGET_MAX_REQUEST_MB`` (default 110): the widget form ``action``
  route, which carries visitor attachments. It fits two files at the
  default per-bot ``max_file_size_mb`` (50) plus the form; per-file limits
  are still checked by the route.
- ``ADMIN_UPLOAD_MAX_MB`` (default 1024): an admin document upload. The
  bot's *Max file size* is still checked by the route.

An over-limit body raises :class:`RequestTooLarge` from ``receive``. Inner
middleware must let it propagate so this one can answer 413.
"""
from __future__ import annotations

import json
import logging
import os
import re

from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)

REQUEST_MAX_ENV = "REQUEST_MAX_KB"
DEFAULT_REQUEST_MAX_KB = 1024
MAX_REQUEST_ENV = "WIDGET_MAX_REQUEST_MB"
DEFAULT_MAX_REQUEST_MB = 110
ADMIN_UPLOAD_ENV = "ADMIN_UPLOAD_MAX_MB"
DEFAULT_ADMIN_UPLOAD_MB = 1024

_GUARDED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_WIDGET_UPLOAD_PATH = re.compile(r"^/widget/[^/]+/action/?$")
_ADMIN_UPLOAD_PATH = re.compile(r"^/api/admin/bots/[^/]+/files/?$")

KB = 1024
MB = 1024 * 1024


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        log.warning("%s=%r is not a whole number; using %d", name, raw, default)
        return default


def widget_max_request_bytes() -> int:
    return _env_int(MAX_REQUEST_ENV, DEFAULT_MAX_REQUEST_MB) * MB


def admin_upload_max_bytes() -> int:
    return _env_int(ADMIN_UPLOAD_ENV, DEFAULT_ADMIN_UPLOAD_MB) * MB


def request_max_bytes() -> int:
    return _env_int(REQUEST_MAX_ENV, DEFAULT_REQUEST_MAX_KB) * KB


def limit_for(method: str, path: str) -> tuple[int, str]:
    """The byte limit for a request and the env var that sets it."""
    if method == "POST" and _WIDGET_UPLOAD_PATH.match(path):
        return widget_max_request_bytes(), MAX_REQUEST_ENV
    if method == "POST" and _ADMIN_UPLOAD_PATH.match(path):
        return admin_upload_max_bytes(), ADMIN_UPLOAD_ENV
    return request_max_bytes(), REQUEST_MAX_ENV


class RequestTooLarge(HTTPException):
    """Raised from ``receive`` when a body passes its limit.

    An ``HTTPException`` so FastAPI's body parser re-raises it instead of
    turning it into a 400, and the app answers 413 if it gets that far."""

    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413, detail=f"Request too large (max {_describe(limit)})")


class RequestBodyLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        method = scope.get("method")
        if scope["type"] != "http" or method not in _GUARDED_METHODS:
            await self.app(scope, receive, send)
            return

        limit, env = limit_for(method, scope.get("path", ""))
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
                        log.info("request body over %s; refused", env)
                        await _send_413(send, limit)
                        return
                except ValueError:
                    pass
                break

        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestTooLarge(limit)
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestTooLarge:
            log.info("request body over %s; refused", env)
            if not started:
                await _send_413(send, limit)


def _describe(limit: int) -> str:
    return f"{limit // MB} MB" if limit >= MB and limit % MB == 0 else f"{limit // KB} KB"


async def _send_413(send, limit: int) -> None:
    body = json.dumps({"detail": f"Request too large (max {_describe(limit)})"}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
