"""Salesforce Leads over the REST API. Search, create, and append onto Description.

Calls go to the org's own ``instance_url`` (allowlisted to ``*.salesforce.com``
when the token was issued) under a pinned API version. A Lead needs
``LastName`` and ``Company``; the capture has no company, so a placeholder
is sent. Converted Leads are left alone: a new capture for that email opens
a fresh Lead instead of editing one that sales already converted.
"""
from __future__ import annotations

from typing import Optional

from crm.contracts import CapturedLead, CrmError, CrmProvider, ExistingLead, require_salesforce_id
from crm.details import clip_details, split_name
from crm.http import make_client, read_json, send
from crm.providers import SALESFORCE_API_VERSION, SALESFORCE_NO_COMPANY, allow_salesforce_instance


def soql_string(value: str) -> str:
    """A SOQL string literal. Backslash and quote are escaped, so an email
    such as ``o'neil@example.com`` cannot end the literal early."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f"'{escaped}'"


def _error_code(response) -> str:
    """Salesforce errors are a JSON list of ``{errorCode, message}``."""
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        code = payload[0].get("errorCode")
        if isinstance(code, str) and code.strip():
            return code.strip()[:200]
    if isinstance(payload, dict):
        for key in ("errorCode", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
    return str(response.status_code)


class SalesforceCrmAdapter(CrmProvider):
    def __init__(self, tokens, api_domain: str, client_factory=None):
        self._tokens = tokens
        self._api = f"{allow_salesforce_instance(api_domain)}/services/data/{SALESFORCE_API_VERSION}"
        self._client_factory = client_factory or make_client

    async def _send(self, method: str, path: str, token: str, *, params=None, json_body=None):
        return await send(
            self._client_factory,
            method,
            f"{self._api}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
            json_body=json_body,
        )

    async def _request(self, method: str, path: str, *, params=None, json_body=None):
        token = await self._tokens.access_token()
        response = await self._send(method, path, token, params=params, json_body=json_body)
        refresh = getattr(self._tokens, "refresh_rejected", None)
        if response.status_code == 401 and refresh is not None:
            # No expiry comes with the token. A rejected one gets one refresh.
            token = await refresh(token)
            response = await self._send(method, path, token, params=params, json_body=json_body)
        if not 200 <= response.status_code < 300:
            raise CrmError(f"salesforce {_error_code(response)}")
        return response

    async def search_by_email(self, email: str) -> Optional[ExistingLead]:
        query = (
            "SELECT Id, Description FROM Lead "
            f"WHERE Email = {soql_string(email)} AND IsConverted = false "
            "ORDER BY CreatedDate DESC LIMIT 1"
        )
        response = await self._request("GET", "/query", params={"q": query})
        records = read_json(response).get("records") or []
        if not records or not isinstance(records[0], dict):
            return None
        row = records[0]
        return ExistingLead(id=require_salesforce_id(row.get("Id")), description=row.get("Description") or "")

    async def create_lead(self, lead: CapturedLead, details: str) -> str:
        first, last = split_name(lead.name, lead.email)
        fields = {
            "LastName": last,
            "Company": SALESFORCE_NO_COMPANY,
            "Email": lead.email[:80],
            "Description": clip_details(details),
        }
        if first:
            fields["FirstName"] = first
        if lead.phone:
            fields["Phone"] = lead.phone[:40]
        response = await self._request("POST", "/sobjects/Lead", json_body=fields)
        payload = read_json(response)
        if payload.get("success") is False:
            raise CrmError("salesforce create failed")
        return require_salesforce_id(payload.get("id"))

    async def append_details(self, existing: ExistingLead, details: str) -> None:
        lead_id = require_salesforce_id(existing.id)
        previous = (existing.description or "").rstrip()
        combined = f"{previous}\n\n{details}".strip() if previous else details
        await self._request(
            "PATCH",
            f"/sobjects/Lead/{lead_id}",
            json_body={"Description": clip_details(combined)},
        )
