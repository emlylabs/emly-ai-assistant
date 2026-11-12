"""Request-level enforcement of widget tokens (B2).

``POST /widget/{ref}/init`` issues an HMAC token (``services.auth.widget_hmac``)
bound to the bot and the visitor's ``user_id``. Every widget route that reads
or writes per-visitor data calls ``require_widget_principal`` with the
resolved bot, which:

* requires ``Authorization: Bearer <token>``,
* verifies signature, expiry, bot binding and key version,
* rejects a request whose ``X-Emly-UserID`` header names a different user.

Routes that also carry a ``user_id`` in the body call ``ensure_same_user``.
Every failure is a 401 with a stable ``code`` so the widget can re-init.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException, Request, status

from services.auth.widget_hmac import WidgetPrincipal, WidgetTokenError, verify

log = logging.getLogger(__name__)

USER_HEADER = "X-Emly-UserID"


def _unauthorized(code: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _as_aware(dt):
    from datetime import timezone

    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def require_bearer(request: Request) -> str:
    """401 unless the request carries a bearer token. Called before the bot
    lookup so credential-less requests are rejected without a DB query."""
    token = _bearer_token(request)
    if token is None:
        raise _unauthorized("widget_token_missing")
    return token


def require_widget_principal(request: Request, bot) -> WidgetPrincipal:
    """Verify the widget token for ``bot`` or raise 401."""
    token = require_bearer(request)
    try:
        principal = verify(
            token,
            expected_bot_id=bot.id,
            current_key_version=int(getattr(bot, "widget_key_version", 1) or 1),
            rotated_at=_as_aware(getattr(bot, "widget_key_rotated_at", None)),
        )
    except WidgetTokenError as exc:
        # Reason only (e.g. "expired", "bot_mismatch") — never the token.
        reason = str(exc).split(":", 1)[0]
        log.info("widget token rejected bot=%s reason=%s", bot.id, reason)
        raise _unauthorized("widget_token_invalid") from None

    header_user = request.headers.get(USER_HEADER)
    if header_user and header_user != principal.user_id:
        log.info("widget user mismatch bot=%s (header vs token)", bot.id)
        raise _unauthorized("widget_user_mismatch")
    return principal


def ensure_same_user(principal: WidgetPrincipal, claimed_user_id: Optional[str]) -> None:
    """401 when a body-supplied ``user_id`` differs from the token's user."""
    if claimed_user_id and str(claimed_user_id) != principal.user_id:
        raise _unauthorized("widget_user_mismatch")


# -------- identity for /init (Q2) --------

_SUPPLIED_ID_RE = __import__("re").compile(r"^[A-Za-z0-9._:@-]{1,128}$")


def _renewal_window_seconds() -> int:
    import os

    return int(os.environ.get("WIDGET_TOKEN_RENEWAL_WINDOW_SECONDS", str(30 * 24 * 3600)))


def _user_has_data(bot_id: str, user_id: str) -> bool:
    """True when ``user_id`` already owns anything on this bot — an end-user
    row, a session or a message. Such an id must not be handed to a caller
    who can't prove it was issued to them."""
    from models.emly_messages import EMLYMessage
    from models.emly_sessions import EMLYSession
    from models.emly_users import EMLYUser

    return (
        EMLYUser.select().where((EMLYUser.bot == bot_id) & (EMLYUser.id == user_id)).exists()
        or EMLYSession.select().where((EMLYSession.bot_id == bot_id) & (EMLYSession.user_id == user_id)).exists()
        or EMLYMessage.select().where((EMLYMessage.bot == bot_id) & (EMLYMessage.user_id == user_id)).exists()
    )


def resolve_init_user(bot, requested_user_id: Optional[str], previous_token: Optional[str]) -> tuple[str, str]:
    """Decide which visitor id ``/init`` issues a token for.

    Returns ``(user_id, how)`` where ``how`` is ``renewed``, ``claimed`` or
    ``minted``:

    * ``renewed`` — ``previous_token`` is a genuine token for this bot
      (expired by at most the renewal window); its user is kept.
    * ``claimed`` — the caller supplied an id that owns no data on this bot
      yet (a first visit with a client-generated id).
    * ``minted`` — anything else, including a supplied id that already has
      history: a fresh server-generated id. The widget adopts it.
    """
    import uuid

    if previous_token:
        try:
            principal = verify(
                previous_token,
                expected_bot_id=bot.id,
                current_key_version=int(getattr(bot, "widget_key_version", 1) or 1),
                rotated_at=_as_aware(getattr(bot, "widget_key_rotated_at", None)),
                expired_leeway_seconds=_renewal_window_seconds(),
            )
            return principal.user_id, "renewed"
        except WidgetTokenError as exc:
            log.info("widget init: previous token not accepted bot=%s reason=%s",
                     bot.id, str(exc).split(":", 1)[0])

    requested = (requested_user_id or "").strip()
    if requested and _SUPPLIED_ID_RE.match(requested) and not _user_has_data(bot.id, requested):
        return requested, "claimed"
    return f"emly-{uuid.uuid4()}", "minted"
