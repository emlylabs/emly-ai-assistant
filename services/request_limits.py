"""G13c: a hard cap on widget request bodies, enforced as bytes arrive.

The widget ``action`` route is unauthenticated and accepts file uploads.
Starlette parses (and spools to disk) the whole multipart body before
the route can check anything, so without a cap a stranger could stream
an unbounded body at any known bot. This middleware rejects a POST/PUT/
PATCH to ``/widget/...`` with 413 once its body exceeds
``WIDGET_MAX_REQUEST_MB``: immediately when ``Content-Length`` says so,
otherwise as soon as the running byte count passes it.

The default (110 MB) fits two files at the default per-bot
``max_file_size_mb`` (50) plus the form. Raise it for bots that accept more or larger
files on one form; the absolute ceiling the route allows is 10 files of
100 MB. Per-file limits are still checked by the route.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

MAX_REQUEST_ENV = "WIDGET_MAX_REQUEST_MB"
DEFAULT_MAX_REQUEST_MB = 110
_GUARDED_METHODS = {"POST", "PUT", "PATCH"}


def widget_max_request_bytes() -> int:
    raw = (os.environ.get(MAX_REQUEST_ENV) or "").strip()
    try:
        mb = int(raw) if raw else DEFAULT_MAX_REQUEST_MB
    except ValueError:
        log.warning("%s=%r is not a whole number of MB; using %d", MAX_REQUEST_ENV, raw, DEFAULT_MAX_REQUEST_MB)
        mb = DEFAULT_MAX_REQUEST_MB
    return max(1, mb) * 1024 * 1024


class _TooLarge(Exception):
    pass


class WidgetBodyLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") not in _GUARDED_METHODS
                or not scope.get("path", "").startswith("/widget/")):
            await self.app(scope, receive, send)
            return

        limit = widget_max_request_bytes()
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    if int(value) > limit:
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
                    raise _TooLarge()
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            log.info("widget request body over %s; refused", MAX_REQUEST_ENV)
            if not started:
                await _send_413(send, limit)


async def _send_413(send, limit: int) -> None:
    body = json.dumps({"detail": f"Request too large (max {limit // (1024 * 1024)} MB)"}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
