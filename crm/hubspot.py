"""HubSpot contacts, leads, and notes.

A HubSpot lead has no Description property. The capture text is a CRM note
on that lead. A new email creates or reuses the contact, then the lead
(which must be associated with the contact), then the first note.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from crm.contracts import CapturedLead, CrmError, CrmProvider, ExistingLead, require_record_id
from crm.details import clip_details, split_name
from crm.http import make_client, raise_for_status, read_json, send
from crm.providers import HUBSPOT_API, HUBSPOT_LEAD_TO_CONTACT


class HubSpotCrmAdapter(CrmProvider):
    def __init__(self, tokens, api_domain: Optional[str] = None, client_factory=None):
        # The API host is fixed. ``api_domain`` is accepted so the factory can
        # build both vendors the same way; it is not a request target.
        del api_domain
        self._tokens = tokens
        self._api = HUBSPOT_API
        self._client_factory = client_factory or make_client

    async def _request(self, method: str, path: str, *, json_body=None):
        token = await self._tokens.access_token()
        response = await send(
            self._client_factory,
            method,
            f"{self._api}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            json_body=json_body,
        )
        raise_for_status(response, "hubspot")
        return response

    async def search_by_email(self, email: str) -> Optional[ExistingLead]:
        contact_id = await self._find_contact(email)
        if not contact_id:
            return None
        lead_id = await self._lead_for_contact(contact_id)
        if not lead_id:
            return None
        return ExistingLead(id=lead_id)

    async def create_lead(self, lead: CapturedLead, details: str) -> str:
        contact_id = await self._find_contact(lead.email)
        if not contact_id:
            contact_id = await self._create_contact(lead)
        existing = await self._lead_for_contact(contact_id)
        lead_id = existing or await self._create_lead_record(lead, contact_id)
        await self._add_note(lead_id, details)
        return lead_id

    async def append_details(self, existing: ExistingLead, details: str) -> None:
        await self._add_note(require_record_id(existing.id), details)

    async def _find_contact(self, email: str) -> Optional[str]:
        response = await self._request(
            "POST",
            "/crm/v3/objects/contacts/search",
            json_body={
                "filterGroups": [
                    {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
                ],
                "properties": ["email"],
                "limit": 1,
            },
        )
        results = read_json(response).get("results") or []
        if not results:
            return None
        return require_record_id(results[0].get("id"))

    async def _create_contact(self, lead: CapturedLead) -> str:
        first, last = split_name(lead.name, lead.email)
        properties = {"email": lead.email}
        if first:
            properties["firstname"] = first
        if last and lead.name:
            properties["lastname"] = last
        if lead.phone:
            properties["phone"] = lead.phone
        token = await self._tokens.access_token()
        response = await send(
            self._client_factory,
            "POST",
            f"{self._api}/crm/v3/objects/contacts",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json_body={"properties": properties},
        )
        # 409 means the contact was created between the search and this call.
        if response.status_code == 409:
            found = await self._find_contact(lead.email)
            if found:
                return found
        raise_for_status(response, "hubspot")
        return require_record_id(read_json(response).get("id"))

    async def _lead_for_contact(self, contact_id: str) -> Optional[str]:
        token = await self._tokens.access_token()
        response = await send(
            self._client_factory,
            "GET",
            f"{self._api}/crm/v4/objects/contacts/{contact_id}/associations/leads",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code == 404:
            return None
        raise_for_status(response, "hubspot")
        for item in read_json(response).get("results") or []:
            raw = item.get("toObjectId", item.get("id"))
            if raw is not None:
                return require_record_id(raw)
        return None

    async def _create_lead_record(self, lead: CapturedLead, contact_id: str) -> str:
        name = (lead.name or lead.email).strip()[:200] or lead.email
        response = await self._request(
            "POST",
            "/crm/v3/objects/leads",
            json_body={
                "properties": {"hs_lead_name": name},
                "associations": [
                    {
                        "to": {"id": contact_id},
                        "types": [
                            {
                                "associationCategory": "HUBSPOT_DEFINED",
                                "associationTypeId": HUBSPOT_LEAD_TO_CONTACT,
                            }
                        ],
                    }
                ],
            },
        )
        return require_record_id(read_json(response).get("id"))

    async def _add_note(self, lead_id: str, details: str) -> None:
        response = await self._request(
            "POST",
            "/crm/v3/objects/notes",
            json_body={
                "properties": {
                    "hs_timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "hs_note_body": clip_details(details),
                }
            },
        )
        note_id = require_record_id(read_json(response).get("id"))
        await self._request(
            "PUT",
            f"/crm/v4/objects/notes/{note_id}/associations/default/leads/{lead_id}",
        )
