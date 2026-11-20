"""Run a lead sync off the request that captured it.

The visitor's response does not wait on Zoho or HubSpot. Nothing is
scheduled when the bot has no connected CRM, so the usual chat path
does not start a thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from crm.capture import lead_from_slots, lead_from_widget, looks_like_email
from crm.contracts import CapturedLead
from crm.facade import LeadSyncFacade
from models.crm_connections import CrmConnections

log = logging.getLogger(__name__)


def schedule_lead_sync(bot_id: str, lead: CapturedLead, providers: tuple | None = None) -> None:
    """``providers`` is the form's CRM selection. ``None`` means every connected CRM."""
    if not looks_like_email((lead.email or "").strip()):
        return
    try:
        if not CrmConnections.has_syncable(bot_id, providers):
            return
    except Exception:
        log.exception("crm connection check failed bot=%s", bot_id)
        return
    threading.Thread(
        target=_run_sync,
        args=(bot_id, lead, providers),
        name="crm-lead-sync",
        daemon=True,
    ).start()


def schedule_widget_lead(bot_id: str, payload, user=None, providers: tuple | None = None) -> None:
    try:
        if providers is not None and not providers:
            return
        lead = lead_from_widget(payload, user)
        if lead is not None:
            schedule_lead_sync(bot_id, lead, providers)
    except Exception:
        log.exception("crm lead sync could not be scheduled bot=%s", bot_id)


def schedule_from_slots(bot_id: str, slots, source: str = "chat") -> None:
    try:
        lead = lead_from_slots(slots, source)
        if lead is not None:
            schedule_lead_sync(bot_id, lead)
    except Exception:
        log.exception("crm lead sync could not be scheduled bot=%s", bot_id)


def _run_sync(bot_id: str, lead: CapturedLead, providers: tuple | None = None) -> None:
    from db.db import DB

    DB.connect(reuse_if_open=True)
    try:
        asyncio.run(LeadSyncFacade().sync_bot(bot_id, lead, providers))
    except Exception:
        log.exception("crm lead sync failed bot=%s", bot_id)
    finally:
        if not DB.is_closed():
            DB.close()
