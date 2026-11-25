"""Salesforce Leads over the REST API. Search, create, and append onto Description.

Calls go to the org's own ``instance_url`` (allowlisted to ``*.salesforce.com``
when the token was issued) under a pinned API version. A Lead needs
``LastName`` and ``Company``; the capture has no company, so a placeholder
is sent. Converted Leads are left alone: a new capture for that email opens
a fresh Lead instead of editing one that sales already converted.

The org's duplicate rules can refuse a create even when no open Lead has
the email (the standard Lead rule also matches on name and company, and
the company is always the placeholder). The refusal lists the matching
records, so an open Lead among them gets the capture appended instead.
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


# Lets a create through a duplicate rule whose action is "Allow" (alert only).
_ALLOW_DUPLICATE = {"Sforce-Duplicate-Rule-Header": "allowSave=true"}


def _duplicate_matches(response) -> tuple[list[str], bool] | None:
    """``(Lead ids, allowSave)`` from a DUPLICATES_DETECTED refusal, else None.

    The refusal is a list of errors; each carries ``duplicateResult`` with
    ``matchResults[].matchRecords[].record.Id`` per matched object type.
    """
    try:
        payload = response.json()
    except Exception:
        return None
    if not isinstance(payload, list):
        return None
    for error in payload:
        if not isinstance(error, dict) or error.get("errorCode") != "DUPLICATES_DETECTED":
            continue
        result = error.get("duplicateResult") or {}
        lead_ids = []
        for match in result.get("matchResults") or []:
            if not isinstance(match, dict) or match.get("entityType") != "Lead":
                continue
            for record in match.get("matchRecords") or []:
                raw = ((record or {}).get("record") or {}).get("Id") if isinstance(record, dict) else None
                try:
                    lead_id = require_salesforce_id(raw)
                except (CrmError, ValueError):
                    continue
                if lead_id not in lead_ids:
                    lead_ids.append(lead_id)
        return lead_ids, bool(result.get("allowSave"))
    return None


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

    async def _send(self, method: str, path: str, token: str, *, params=None, json_body=None, headers=None):
        return await send(
            self._client_factory,
            method,
            f"{self._api}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})},
            params=params,
            json_body=json_body,
        )

    async def _call(self, method: str, path: str, *, params=None, json_body=None, headers=None):
        """The response, whatever its status, after one refresh on a 401."""
        token = await self._tokens.access_token()
        response = await self._send(method, path, token, params=params, json_body=json_body, headers=headers)
        refresh = getattr(self._tokens, "refresh_rejected", None)
        if response.status_code == 401 and refresh is not None:
            # No expiry comes with the token. A rejected one gets one refresh.
            token = await refresh(token)
            response = await self._send(method, path, token, params=params, json_body=json_body, headers=headers)
        return response

    async def _request(self, method: str, path: str, *, params=None, json_body=None, headers=None):
        response = await self._call(method, path, params=params, json_body=json_body, headers=headers)
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
        response = await self._call("POST", "/sobjects/Lead", json_body=fields)
        if response.status_code == 400:
            duplicate = _duplicate_matches(response)
            if duplicate is not None:
                return await self._on_duplicate(duplicate, fields, details)
        if not 200 <= response.status_code < 300:
            raise CrmError(f"salesforce {_error_code(response)}")
        return self._created_id(response)

    @staticmethod
    def _created_id(response) -> str:
        payload = read_json(response)
        if payload.get("success") is False:
            raise CrmError("salesforce create failed")
        return require_salesforce_id(payload.get("id"))

    async def _open_lead(self, lead_id: str) -> Optional[ExistingLead]:
        """The Lead if it can still be edited (not converted), else None."""
        response = await self._request(
            "GET", f"/sobjects/Lead/{lead_id}", params={"fields": "Description,IsConverted"}
        )
        row = read_json(response)
        if row.get("IsConverted"):
            return None
        return ExistingLead(id=lead_id, description=row.get("Description") or "")

    async def _on_duplicate(self, duplicate: tuple[list[str], bool], fields: dict, details: str) -> str:
        """A duplicate rule refused the create. Append to the open Lead it matched,
        or, when the rule only alerts, create the Lead anyway."""
        lead_ids, allow_save = duplicate
        for lead_id in lead_ids:
            existing = await self._open_lead(lead_id)
            if existing is not None:
                await self.append_details(existing, details)
                return lead_id
        if allow_save:
            # The matches are converted Leads or Contacts, which we do not edit.
            response = await self._request("POST", "/sobjects/Lead", json_body=fields, headers=_ALLOW_DUPLICATE)
            return self._created_id(response)
        raise CrmError(
            "salesforce DUPLICATES_DETECTED: a duplicate rule blocks this Lead and it matches no open Lead"
        )

    async def append_details(self, existing: ExistingLead, details: str) -> None:
        lead_id = require_salesforce_id(existing.id)
        previous = (existing.description or "").rstrip()
        combined = f"{previous}\n\n{details}".strip() if previous else details
        await self._request(
            "PATCH",
            f"/sobjects/Lead/{lead_id}",
            json_body={"Description": clip_details(combined)},
        )
