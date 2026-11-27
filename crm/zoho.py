"""Zoho CRM leads. Search, create, and append onto Description.

API calls use ``Authorization: Zoho-oauthtoken`` against the connection's
``api_domain``. This adapter does not decide whether to create or append.
"""
from __future__ import annotations

from crm.contracts import CapturedLead, CrmError, CrmProvider, ExistingLead, require_record_id
from crm.details import clip_details, split_name
from crm.http import make_client, raise_for_status, read_json, send
from crm.providers import allow_zoho_api_domain


class ZohoCrmAdapter(CrmProvider):
    def __init__(self, tokens, api_domain: str, client_factory=None):
        self._tokens = tokens
        self._api = allow_zoho_api_domain(api_domain)
        self._client_factory = client_factory or make_client

    async def _request(self, method: str, path: str, *, params=None, json_body=None):
        token = await self._tokens.access_token()
        response = await send(
            self._client_factory,
            method,
            f"{self._api}{path}",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "Accept": "application/json",
            },
            params=params,
            json_body=json_body,
        )
        raise_for_status(response, "zoho")
        return response

    async def search_by_email(self, email: str) -> ExistingLead | None:
        response = await self._request("GET", "/crm/v8/Leads/search", params={"email": email})
        if response.status_code == 204:
            return None
        rows = read_json(response).get("data") or []
        if not rows:
            return None
        row = rows[0]
        lead_id = require_record_id(row.get("id"))
        if "Description" not in row:
            full = await self._request("GET", f"/crm/v8/Leads/{lead_id}")
            data = read_json(full).get("data") or []
            row = data[0] if data else row
        return ExistingLead(id=lead_id, description=row.get("Description") or "")

    async def create_lead(self, lead: CapturedLead, details: str) -> str:
        first, last = split_name(lead.name, lead.email)
        fields = {
            "Last_Name": last,
            "Email": lead.email,
            "Description": clip_details(details),
        }
        if first:
            fields["First_Name"] = first
        if lead.phone:
            fields["Phone"] = lead.phone
        response = await self._request("POST", "/crm/v8/Leads", json_body={"data": [fields]})
        return _created_id(read_json(response))

    async def append_details(self, existing: ExistingLead, details: str) -> None:
        lead_id = require_record_id(existing.id)
        previous = (existing.description or "").rstrip()
        combined = f"{previous}\n\n{details}".strip() if previous else details
        await self._request(
            "PUT",
            f"/crm/v8/Leads/{lead_id}",
            json_body={"data": [{"id": lead_id, "Description": clip_details(combined)}]},
        )


def _created_id(payload: dict) -> str:
    rows = payload.get("data") or []
    if not rows:
        raise CrmError("zoho create returned no lead")
    row = rows[0]
    if row.get("status") == "error" or (row.get("code") and row.get("code") != "SUCCESS"):
        raise CrmError(str(row.get("code") or "zoho create failed")[:200])
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    return require_record_id(details.get("id") or row.get("id"))
