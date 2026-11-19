"""Outbound HTTP for Zoho CRM and HubSpot.

This is the only module in ``crm`` that imports a network client, so the
outbound-connection review has one place to look.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from channels.auth._http import DEFAULT_TIMEOUT
from crm.contracts import CrmError

def make_client() -> httpx.AsyncClient:
    # Token and API hosts are fixed. A redirect would be a different host,
    # which we do not follow.
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=False)


async def send(
    client_factory,
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    json_body: Any = None,
    form: Optional[dict] = None,
) -> httpx.Response:
    async with client_factory() as client:
        return await client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            data=form,
        )


def read_json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def error_code(payload: dict) -> str:
    for key in ("error", "code", "message", "status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return ""


def raise_for_status(response: httpx.Response, provider: str) -> None:
    """204 is an empty Zoho search, not a failure. Anything else outside 2xx is."""
    if response.status_code == 204 or 200 <= response.status_code < 300:
        return
    code = error_code(read_json(response)) or str(response.status_code)
    raise CrmError(f"{provider} {code}")
