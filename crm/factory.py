"""Build a vendor adapter, with a fresh token proxy, for one stored connection."""
from __future__ import annotations

import threading

from crm.contracts import CrmError, CrmProvider
from crm.http import make_client
from crm.hubspot import HubSpotCrmAdapter
from crm.oauth import HubSpotOAuth, ZohoOAuth
from crm.token_proxy import ConnectionTokenSource, RefreshingTokenProxy
from crm.zoho import ZohoCrmAdapter


class CrmProviderFactory:
    """Singleton. Registers Zoho and HubSpot and builds the stack for a row.

    The stack is an OAuth client, a ``ConnectionTokenSource``, a
    ``RefreshingTokenProxy``, and the vendor adapter. Callers get the adapter.
    """

    _instance = None
    _guard = threading.Lock()

    def __init__(self, client_factory=None):
        self._clients = client_factory or make_client

    @classmethod
    def instance(cls) -> "CrmProviderFactory":
        if cls._instance is None:
            with cls._guard:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def build(self, row) -> CrmProvider:
        provider = getattr(row, "provider", None)
        if provider == "zoho":
            if not getattr(row, "api_domain", None):
                raise CrmError("Zoho API domain is not set")
            oauth = ZohoOAuth(client_factory=self._clients)
            proxy = RefreshingTokenProxy(row.id, ConnectionTokenSource(row.id, oauth))
            return ZohoCrmAdapter(proxy, row.api_domain, client_factory=self._clients)
        if provider == "hubspot":
            oauth = HubSpotOAuth(client_factory=self._clients)
            proxy = RefreshingTokenProxy(row.id, ConnectionTokenSource(row.id, oauth))
            return HubSpotCrmAdapter(proxy, client_factory=self._clients)
        raise CrmError("unknown CRM")
