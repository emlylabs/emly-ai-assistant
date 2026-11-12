"""Deployment-wide widget metadata + per-bot preview QR.

Two surfaces:

- ``GET /api/admin/widget/info`` — exposes the loader's build timestamp so
  the Channels tab can render a pinnable ``?v=`` in the embed snippet.
  The version key is parsed from the bundled ``emly-widget.js`` header
  comment (``/*! Chatbot Widget … Built: <iso8601> */``) and cached by
  file mtime — re-reads only when the loader file changes on disk.

- ``GET /api/admin/bots/{bot_ref}/preview-qr.svg`` — returns an SVG QR
  code that encodes the deployment-hosted ``/widget-test.html?bot=<id>``
  page so stakeholders can scan a phone and preview the bot without a
  customer site. The QR target is always rendered with the bot's stable
  ``bot.id`` (not slug) for the same reason the embed snippet uses it.

Both routes are admin-gated. The version endpoint is functionally public
(``emly-widget.js`` is itself a public asset and its build comment is
trivially readable by anyone), but it lives behind admin auth to keep
the surface consistent — the UI is the only consumer.
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from fastapi.responses import Response

from models.admin_users import AdminUserModel
from routes.widget import resolve_bot
from services.auth.dependencies import get_admin
from services.authz import require_member

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Widget loader version
# ---------------------------------------------------------------------------
_BUILT_RE = re.compile(rb"Built:\s*(\S+?)\s*\*/")
_VERSION_LOCK = threading.Lock()
_VERSION_CACHE: dict[str, Tuple[float, Tuple[Optional[str], Optional[str]]]] = {}


def _resolve_loader_path() -> Optional[str]:
    """Locate ``emly-widget.js`` on disk.

    The container build copies the file to ``ui/out/`` via the Next.js
    static export; the in-repo source-of-truth is ``ui/public/``. We try
    both so the route works in dev (``npm run dev`` against the backend)
    and in prod (Next.js export served by FastAPI).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("../ui/out/emly-widget.js", "../ui/public/emly-widget.js"):
        cand = os.path.normpath(os.path.join(here, rel))
        if os.path.exists(cand):
            return cand
    return None


def _read_loader_version() -> Tuple[Optional[str], Optional[str]]:
    """Return ``(version, built_at)`` for the on-disk loader, or ``(None, None)``.

    ``built_at`` is the raw timestamp from the file header (or mtime
    fallback); ``version`` is the same value trimmed to seconds and with
    the trailing ``Z`` stripped so it pastes cleanly into a ``?v=`` query
    parameter without URL escaping.
    """
    path = _resolve_loader_path()
    if not path:
        return None, None

    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, None

    with _VERSION_LOCK:
        cached = _VERSION_CACHE.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]

    built_at: Optional[str] = None
    try:
        with open(path, "rb") as f:
            head = f.read(256)
        m = _BUILT_RE.search(head)
        if m:
            built_at = m.group(1).decode("ascii", errors="ignore").strip()
    except Exception:
        log.exception("widget_info: failed to read loader header from %s", path)

    if not built_at:
        # No build comment — fall back to file mtime so we still emit a
        # stable version key. This is fine for dev where the bundler may
        # not stamp the header.
        built_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))

    # Drop fractional seconds and trailing Z so the version pastes cleanly
    # into ``?v=`` / ``data-version`` without URL escaping. Granularity is
    # one second, which is finer than any sensible build cadence.
    version = built_at.split(".")[0].rstrip("Zz")

    result = (version, built_at)
    with _VERSION_LOCK:
        _VERSION_CACHE[path] = (mtime, result)
    return result


@router.get("/widget/info")
def widget_info(_: AdminUserModel = Depends(get_admin)) -> dict:
    """Return the widget loader's build metadata for the embed snippet.

    Response shape: ``{"version": "2026-05-09T13:24:10", "built_at":
    "2026-05-09T13:24:10.834Z"}``. Either field may be ``null`` if the
    loader is missing on disk — the UI degrades by omitting the ``?v=``
    pin in that case.
    """
    version, built_at = _read_loader_version()
    return {"version": version, "built_at": built_at}


# ---------------------------------------------------------------------------
# Per-bot share-preview QR
# ---------------------------------------------------------------------------
# The deployment origin we encode in the QR. ``Request.base_url`` reflects
# whatever the admin browser sees, which is also what a phone scanning the
# QR will be able to reach — the same TLS host. ``PUBLIC_BASE_URL`` env
# wins if set (operator override for split admin / public hosts).
_PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def _deployment_origin(request: Request) -> str:
    if _PUBLIC_BASE_URL:
        return _PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


@router.get(
    "/bots/{bot_ref}/preview-qr.svg",
    responses={200: {"content": {"image/svg+xml": {}}}},
)
def bot_preview_qr(
    request: Request,
    bot_ref: str = Path(..., min_length=1),
    admin: AdminUserModel = Depends(get_admin),
) -> Response:
    """Render an SVG QR that points at the bot's share-preview page.
    Members of the bot only (any role), like the other per-bot routes.

    The QR encodes ``<origin>/widget-test.html?bot=<bot.id>``. The
    response is cacheable (the encoded URL is stable per bot) but we keep
    the TTL short so a deployment-origin change (e.g. behind a reverse
    proxy reconfig) doesn't strand stale codes for hours.
    """
    bot = resolve_bot(bot_ref)
    require_member(admin, bot.id)
    origin = _deployment_origin(request)
    preview_url = f"{origin}/widget-test.html?bot={bot.id}"

    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:  # pragma: no cover — install-time guard
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QR rendering dependency missing on this deployment",
        )

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.ERROR_CORRECT_M,
        box_size=10,
        border=2,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    qr.add_data(preview_url)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf)

    return Response(
        content=buf.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300"},
    )


__all__ = ["router"]
