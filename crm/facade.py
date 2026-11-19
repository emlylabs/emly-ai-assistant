"""The one call the widget and the chat agent make."""
from __future__ import annotations

import logging

from crm.capture import looks_like_email
from crm.contracts import CapturedLead, CrmError
from crm.factory import CrmProviderFactory
from crm.strategy import UpsertLeadStrategy
from models.crm_connections import CrmConnections

log = logging.getLogger(__name__)


class LeadSyncFacade:
    """Load a bot's connected CRMs and run the upsert strategy on each.

    A failure is recorded on that connection and swallowed. The chat and
    the form still succeed.
    """

    def __init__(self, factory: CrmProviderFactory | None = None, strategy: UpsertLeadStrategy | None = None):
        self._factory = factory or CrmProviderFactory.instance()
        self._strategy = strategy or UpsertLeadStrategy()

    async def sync_bot(self, bot_id: str, lead: CapturedLead) -> None:
        if not looks_like_email((lead.email or "").strip()):
            return
        for row in CrmConnections.list_syncable(bot_id):
            try:
                provider = self._factory.build(row)
                await self._strategy.sync(provider, lead)
                if row.status != "connected" or row.last_error:
                    CrmConnections.mark_synced(row.id)
                log.info("crm lead synced bot=%s provider=%s", bot_id, row.provider)
            except Exception as exc:
                message = str(exc) if isinstance(exc, CrmError) else "lead sync failed"
                try:
                    CrmConnections.record_error(row.id, message)
                except Exception:
                    log.exception("crm error status could not be saved bot=%s", bot_id)
                log.exception("crm sync failed bot=%s provider=%s", bot_id, row.provider)
