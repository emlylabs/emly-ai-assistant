"""F2: Starlette / python-multipart behaviour we rely on after the upgrade.

* A crafted ``Host`` header or a path that doesn't start with ``/`` must not
  move the host in ``request.url`` (Starlette X41-2026-002 and
  GHSA-jp82-jpqv-5vv3). Without ``APP_BASE_URL`` (dev) we build sign-in
  redirects from ``request.url``, and the widget-chat middleware routes on
  ``request.url.path``.
* ``application/x-www-form-urlencoded`` bodies are held to the same field
  limit as multipart ones (GHSA-82w8-qh3p-5jfq). The local sign-in form is
  unauthenticated and urlencoded.
"""

from __future__ import annotations

import time

import pytest
from starlette.requests import Request

from tests.auth.test_local_issuer_hardening import _form, issuer  # noqa: F401  (fixture)


def _request(path: str, host: str) -> Request:
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [(b"host", host.encode())], "server": ("localhost", 8080),
    })


CRAFTED = [
    ("@evil.example", "localhost:8080"),         # path moves the authority boundary
    ("/widget/b/chat", "evil.example/admin?"),   # host smuggles a path
    ("/widget/b/chat", "evil.example#"),
]


@pytest.mark.parametrize("path, host", CRAFTED)
def test_base_url_never_takes_attacker_host(monkeypatch, path, host):
    import main
    from routes import auth

    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    request = _request(path, host)
    for built in (auth._app_base_url(request), main._app_base_url_from_request(request),
                  auth._tenant_host(request)):
        assert "evil.example" not in built, built


@pytest.mark.parametrize("path, host", CRAFTED)
def test_url_path_is_the_routed_path(path, host):
    request = _request(path, host)
    assert request.url.path.lstrip("/") == path.lstrip("/")
    assert request.url.hostname != "evil.example"


def test_urlencoded_field_flood_rejected(issuer):  # noqa: F811
    body = "&".join(f"f{i}=x" for i in range(20_000)) + "&" + "&".join(f"{k}={v}" for k, v in _form().items())
    started = time.monotonic()
    r = issuer.post("/api/auth/local/authorize", content=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400, r.status_code
    assert time.monotonic() - started < 5
