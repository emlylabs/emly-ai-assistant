"""How a capture is applied. Adapters do not choose create versus append."""
from __future__ import annotations

from abc import ABC, abstractmethod

from crm.capture import looks_like_email
from crm.contracts import CapturedLead, CrmProvider
from crm.details import format_capture


class LeadSyncStrategy(ABC):
    @abstractmethod
    async def sync(self, provider: CrmProvider, lead: CapturedLead) -> None:
        """Send ``lead`` through ``provider``."""


class UpsertLeadStrategy(LeadSyncStrategy):
    """Search by email. Append when a lead exists, otherwise create one."""

    async def sync(self, provider: CrmProvider, lead: CapturedLead) -> None:
        email = (lead.email or "").strip()
        if not looks_like_email(email):
            return
        details = format_capture(lead)
        existing = await provider.search_by_email(email)
        if existing is None:
            await provider.create_lead(lead, details)
        else:
            await provider.append_details(existing, details)
