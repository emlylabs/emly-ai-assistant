"""PR #11: browser-facing sign-in errors render an HTML page; API callers
keep the existing JSON shape.

``/api/admin/auth/login`` and ``/callback`` are reached by top-level
navigation, so ``routes.auth._auth_error`` (and the 429 handler in
``main.py``) switch on ``Accept: text/html``.
"""

from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@pytest.fixture
def auth_client(isolated_db):
    """Auth router alone, with a provider stub whose ``authorize_url``
    fails (sign-in server unreachable)."""
    from routes import auth
    from services.auth.dependencies import get_auth_provider
    from services.auth.ratelimit import limiter

    def _unreachable(*a, **kw):
        raise ConnectionError("idp down")

    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth.router, prefix="/api/admin")
    app.dependency_overrides[get_auth_provider] = lambda: SimpleNamespace(authorize_url=_unreachable)
    return TestClient(app, follow_redirects=False)


def _assert_html_page(r, status_code: int, title: str):
    assert r.status_code == status_code, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert f"<h1>{title}</h1>" in r.text
    assert 'href="/api/admin/auth/login"' in r.text


# ------------------------------------------------------------ callback


def test_callback_invalid_state_json_for_api_callers(auth_client):
    r = auth_client.get(
        "/api/admin/auth/callback",
        params={"state": "never-issued", "code": "abc"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": {"code": "callback_state_invalid_or_expired"}}


def test_callback_invalid_state_html_for_browsers(auth_client):
    r = auth_client.get(
        "/api/admin/auth/callback",
        params={"state": "never-issued", "code": "abc"},
        headers={"Accept": BROWSER_ACCEPT},
    )
    _assert_html_page(r, 400, "Your sign-in link expired")
    assert "callback_state_invalid_or_expired" not in r.text


def test_callback_missing_params_json_when_no_accept_html(auth_client):
    # Default TestClient Accept is */*: not a browser navigation.
    r = auth_client.get("/api/admin/auth/callback")
    assert r.status_code == 400
    assert r.json() == {"detail": {"code": "missing_state_or_code"}}


def test_callback_unknown_code_html_uses_generic_copy(auth_client):
    r = auth_client.get("/api/admin/auth/callback", headers={"Accept": BROWSER_ACCEPT})
    _assert_html_page(r, 400, "Sign-in failed")


def test_callback_idp_error_kept_in_json_but_not_reflected_in_html(auth_client):
    evil = "<script>alert(1)</script>"
    j = auth_client.get("/api/admin/auth/callback", params={"error": evil},
                        headers={"Accept": "application/json"})
    assert j.status_code == 400
    assert j.json() == {"detail": {"code": "idp_error", "error": evil}}

    h = auth_client.get("/api/admin/auth/callback", params={"error": evil},
                        headers={"Accept": BROWSER_ACCEPT})
    _assert_html_page(h, 400, "Sign-in failed")
    assert evil not in h.text


# --------------------------------------------------------------- login


def test_login_idp_unreachable_json_for_api_callers(auth_client):
    r = auth_client.get("/api/admin/auth/login", headers={"Accept": "application/json"})
    assert r.status_code == 502
    assert r.json() == {"detail": {"code": "idp_unavailable"}}


def test_login_idp_unreachable_html_for_browsers(auth_client):
    r = auth_client.get("/api/admin/auth/login", headers={"Accept": BROWSER_ACCEPT})
    _assert_html_page(r, 502, "Sign-in is temporarily unavailable")


# ------------------------------------------------------- unit: helpers


def _request(method: str = "GET", accept: str | None = None):
    from starlette.requests import Request

    headers = [(b"accept", accept.encode())] if accept is not None else []
    return Request({"type": "http", "method": method, "path": "/", "headers": headers, "query_string": b""})


@pytest.mark.parametrize(
    "method, accept, expected",
    [
        ("GET", BROWSER_ACCEPT, True),
        ("HEAD", "text/html", True),
        ("GET", "application/json", False),
        ("GET", "*/*", False),
        ("GET", None, False),
        # A form POST from a browser still gets JSON: only navigations get pages.
        ("POST", BROWSER_ACCEPT, False),
    ],
)
def test_wants_html(method, accept, expected):
    from services.auth.error_page import wants_html

    assert wants_html(_request(method, accept)) is expected


def test_auth_error_preserves_full_json_detail():
    from routes.auth import _auth_error

    detail = {"code": "email_not_verified", "email": "a@b.example"}
    r = _auth_error(_request("GET", "application/json"), 401, detail)
    assert r.status_code == 401
    assert r.body == b'{"detail":{"code":"email_not_verified","email":"a@b.example"}}'


def test_auth_error_html_has_mapped_copy_and_no_detail_leak():
    from routes.auth import _auth_error

    r = _auth_error(_request("GET", BROWSER_ACCEPT), 401,
                    {"code": "email_not_verified", "email": "a@b.example"})
    assert r.status_code == 401
    body = r.body.decode()
    assert "<h1>Email not verified</h1>" in body
    assert "a@b.example" not in body


# --------------------------------------------------- main.py 429 handler


def _limited_2_per_hour(request: Request):
    return {"ok": True}


# Decorate once at import: slowapi registers limits by function name, so
# re-decorating per test would stack a second 2/hour limit on the route.
from services.auth.ratelimit import limiter as _limiter  # noqa: E402

_limited_2_per_hour = _limiter.limit("2/hour")(_limited_2_per_hour)


@pytest.fixture
def rate_limited_client():
    """A throwaway app wired with the 429 handler ``main.app`` registers and
    a route limited to 2/hour through the real shared limiter."""
    from slowapi.errors import RateLimitExceeded

    import main
    from services.auth.ratelimit import limiter

    handler = main.app.exception_handlers[RateLimitExceeded]
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, handler)
    app.get("/limited")(_limited_2_per_hour)
    client = TestClient(app)

    def exhaust(accept: str):
        for _ in range(2):
            assert client.get("/limited").status_code == 200
        return client.get("/limited", headers={"Accept": accept})

    return exhaust


def test_rate_limit_json_for_api_callers(rate_limited_client):
    r = rate_limited_client("application/json")
    assert r.status_code == 429
    assert r.json() == {"detail": {"code": "rate_limited", "limit": "2 per 1 hour"}}
    # Retry-After reflects the configured window, not a fixed minute.
    assert 3500 < int(r.headers["retry-after"]) <= 3601


def test_rate_limit_html_for_browsers(rate_limited_client):
    r = rate_limited_client(BROWSER_ACCEPT)
    _assert_html_page(r, 429, "Too many sign-in attempts")
    assert 3500 < int(r.headers["retry-after"]) <= 3601
    assert "Please wait 60 minutes" in r.text
