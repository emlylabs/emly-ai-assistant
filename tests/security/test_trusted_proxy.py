"""B5: X-Forwarded-For is honoured only from trusted proxies, so clients
can't spoof the IP used by rate limits and the audit log."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


def _app(trusted: str) -> TestClient:
    from services.audit import _client_ip
    from services.auth.ratelimit import client_ip, rate_limit

    app = FastAPI()

    @app.get("/ip", dependencies=[Depends(rate_limit("probe"))])
    def ip(request: Request):
        return {"limiter": client_ip(request), "audit": _client_ip(request)}

    # TestClient connects from host "testclient".
    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=trusted))


def test_untrusted_client_cannot_spoof_ip():
    client = _app("127.0.0.1")
    body = client.get("/ip", headers={"X-Forwarded-For": "1.2.3.4"}).json()
    assert body == {"limiter": "testclient", "audit": "testclient"}


def test_trusted_proxy_forwarded_ip_is_used():
    client = _app("testclient")
    body = client.get("/ip", headers={"X-Forwarded-For": "1.2.3.4"}).json()
    assert body == {"limiter": "1.2.3.4", "audit": "1.2.3.4"}


def test_rotating_forwarded_for_does_not_bypass_rate_limit(monkeypatch):
    from services.auth import ratelimit

    monkeypatch.setitem(ratelimit._LIMITS, "probe", "3/minute")
    client = _app("127.0.0.1")
    statuses = [
        client.get("/ip", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(4)
    ]
    assert statuses == [200, 200, 200, 429]
