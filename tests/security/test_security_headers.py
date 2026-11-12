"""B6: security headers — unit and in-process checks. The full-app check
lives in tests/test_route_auth_inventory.py (same subprocess probe)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    def _make(base_url: str | None = None):
        from services.security_headers import SecurityHeadersMiddleware

        for name in ("PUBLIC_BASE_URL", "APP_BASE_URL"):
            monkeypatch.delenv(name, raising=False)
        if base_url:
            monkeypatch.setenv("APP_BASE_URL", base_url)

        app = FastAPI()

        @app.get("/api/admin/bots")
        def admin_api():
            return {"ok": True}

        @app.get("/bots/x/config")
        def admin_page():
            return PlainTextResponse("<html>admin</html>", media_type="text/html")

        @app.get("/widget/{ref}/config")
        def widget_cfg(ref: str):
            return {"ok": True}

        @app.get("/emly-widget.js")
        def widget_js():
            return PlainTextResponse("console.log(1)", media_type="application/javascript")

        @app.get("/api/auth/local/authorize")
        def login_form():
            return Response("<form>", media_type="text/html",
                            headers={"Content-Security-Policy": "default-src 'self'"})

        @app.get("/stream")
        def stream():
            return StreamingResponse(iter([b"a\n", b"b\n"]), media_type="application/x-ndjson")

        app.add_middleware(SecurityHeadersMiddleware)
        return TestClient(app)

    return _make


def _framing(resp) -> tuple[str | None, str | None]:
    return resp.headers.get("x-frame-options"), resp.headers.get("content-security-policy")


@pytest.mark.parametrize("path", ["/api/admin/bots", "/bots/x/config"])
def test_admin_responses_are_unframeable(client, path):
    r = client().get(path)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert _framing(r) == ("DENY", "frame-ancestors 'none'")


@pytest.mark.parametrize("path", ["/widget/abc/config", "/emly-widget.js"])
def test_widget_responses_have_no_framing_rules(client, path):
    r = client().get(path)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert _framing(r) == (None, None)


def test_existing_csp_is_kept_and_extended(client):
    r = client().get("/api/auth/local/authorize")
    assert r.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"
    assert r.headers["x-frame-options"] == "DENY"


def test_hsts_only_when_public_url_is_https(client):
    assert "strict-transport-security" not in client("http://localhost:8080").get("/api/admin/bots").headers
    assert "strict-transport-security" not in client(None).get("/api/admin/bots").headers
    r = client("https://emly.example.com").get("/widget/abc/config")
    assert r.headers["strict-transport-security"] == "max-age=31536000"


def test_streaming_response_passes_through(client):
    r = client().get("/stream")
    assert r.text == "a\nb\n"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_widget_path_classification():
    from services.security_headers import is_widget_path

    assert is_widget_path("/widget/bot-1/chat")
    assert is_widget_path("/emly-widget.js")
    assert is_widget_path("/widget-test.html")
    assert not is_widget_path("/widgets")
    assert not is_widget_path("/api/admin/widget/x")
    assert not is_widget_path("/")
