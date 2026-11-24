"""The CRM surface the rest of the app programs against.

Adapters speak one vendor's HTTP. They do not decide whether a capture
creates a lead or appends to one; ``UpsertLeadStrategy`` does that.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class CrmError(Exception):
    """A CRM call failed. The message is safe to store and show to an admin."""


class CrmAuthError(CrmError):
    """Token exchange or refresh failed. The message never includes a secret."""


@dataclass(frozen=True)
class CapturedLead:
    """One visitor capture. ``email`` is the match key; without it nothing is sent."""

    email: str
    name: str = ""
    phone: str = ""
    use_case: str = ""
    source: str = ""
    captured_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class ExistingLead:
    """A lead the provider already has for this email."""

    id: str
    description: str = ""


class CrmProvider(ABC):
    """One vendor's lead API. Built per connection, used for a single sync."""

    @abstractmethod
    async def search_by_email(self, email: str) -> Optional[ExistingLead]:
        """Return the lead for this email, or ``None`` when the vendor has none."""

    @abstractmethod
    async def create_lead(self, lead: CapturedLead, details: str) -> str:
        """Create the lead and return its id. ``details`` is the capture text."""

    @abstractmethod
    async def append_details(self, existing: ExistingLead, details: str) -> None:
        """Attach ``details`` to a lead that is already there."""


def require_record_id(value: object) -> str:
    """A CRM record id we are about to put in a URL path.

    Digits only, so a value from a response cannot change the host or the path.
    """
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 32:
        raise CrmError("unexpected CRM record id")
    return text


_SALESFORCE_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


def require_salesforce_id(value: object) -> str:
    """A Salesforce record id (15 or 18 letters and digits) bound for a URL path."""
    text = str(value or "").strip()
    if not _SALESFORCE_ID.match(text):
        raise CrmError("unexpected CRM record id")
    return text
