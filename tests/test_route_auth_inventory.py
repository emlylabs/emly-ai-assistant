"""B1: every route must reject a request that carries no credentials.

Walks ``app.routes`` of the real application (booted with ``MODE=prod`` in
a subprocess so its import-time side effects stay out of this test
process), calls each (method, route) with no cookie, no bearer token and no
body, and asserts the response is 401 or 403.

A route is exempt only if it is listed in ``PUBLIC_ROUTES`` below, with the
reason it is public. Adding a new route without an auth dependency makes
this test fail until the route is either protected or deliberately listed.

Entries are exact ``(METHOD, path template)`` pairs rather than prefixes, so
a new route under an otherwise-public prefix (``/channels/...``,
``/widget/...``) is not exempt by accident.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import pytest
from fastapi import Depends, FastAPI, HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]

# Statuses that count as "rejected without credentials".
REJECTED = frozenset({401, 403})

# Routes that are intentionally reachable without admin credentials.
PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    # --- Embeddable widget: end users are anonymous; per-request widget
    # token verification is B2's job.
    ("POST", "/widget/{bot_slug}/chat"): "widget",
    ("GET", "/widget/{bot_ref}/config"): "widget",
    ("GET", "/widget/{bot_ref}/submissions/counts"): "widget",
    ("POST", "/widget/{bot_ref}/impressions"): "widget",
    ("POST", "/widget/{bot_ref}/init"): "widget",
    ("POST", "/widget/{bot_ref}/messages/{message_id}/rate"): "widget",
    ("POST", "/widget/{bot_ref}/action"): "widget",
    # --- Channel webhooks: authenticated by the provider's signature
    # (verify_signature on each adapter), not by an admin session.
    ("POST", "/channels/{channel_type}/events"): "channel webhook (provider signature)",
    ("POST", "/channels/{channel_type}/{channel_id}/events"): "channel webhook (provider signature)",
    ("GET", "/channels/{channel_type}/{channel_id}/events"): "channel webhook handshake",
    ("GET", "/channels/{channel_type}/oauth/callback"): "OAuth callback (signed state)",
    # --- Admin sign-in (BFF) — must work before a session exists.
    ("GET", "/api/admin/auth/login"): "admin sign-in",
    ("GET", "/api/admin/auth/callback"): "admin sign-in (OIDC callback)",
    ("POST", "/api/admin/auth/logout"): "admin sign-out (idempotent)",
    # --- Embedded OIDC issuer (local sign-in) and discovery.
    ("GET", "/api/auth/local/authorize"): "local issuer login form",
    ("POST", "/api/auth/local/authorize"): "local issuer login submit",
    ("POST", "/api/auth/local/token"): "local issuer token endpoint",
    ("POST", "/api/auth/local/logout"): "local issuer logout",
    ("GET", "/.well-known/openid-configuration"): "OIDC discovery",
    ("GET", "/.well-known/jwks.json"): "OIDC signing keys",
    # --- Invite landing page: the 256-bit invite token in the URL is the
    # credential; the page renders before the invitee signs in.
    ("GET", "/api/admin/admins/invites/by-token/{token}"): "invite landing (token is the credential)",
    # --- Health and build info.
    ("GET", "/api/livez"): "health",
    ("GET", "/api/readyz"): "health",
    ("GET", "/api/health"): "health (legacy alias of /api/livez)",
    ("GET", "/api/version"): "build info",
    # --- Static admin UI shell (pages gate themselves via the BFF; the
    # catch-all also serves /emly-widget.js).
    ("GET", "/"): "static UI",
    ("GET", "/{full_path:path}"): "static UI / widget script",
}

# Routes that are registered but answer 404 when MODE=prod (feature off).
# Empty today; add (METHOD, path) here with a reason if one is introduced.
OFF_IN_PROD: dict[tuple[str, str], str] = {}


def _fill_path(template: str) -> str:
    """Substitute placeholder values for path parameters."""
    return re.sub(r"\{([^}:]+)(?::[^}]+)?\}", lambda m: "1" if m.group(1).endswith("_id") else "x", template)


def probe_routes(app) -> list[dict]:
    """Call every (method, route) on ``app`` with no credentials."""
    from fastapi.testclient import TestClient
    from starlette.routing import Route

    client = TestClient(app, raise_server_exceptions=False)
    results = []
    for route in app.routes:
        if not isinstance(route, Route):
            results.append({"method": "MOUNT", "path": getattr(route, "path", "?"), "status": None})
            continue
        for method in sorted((route.methods or set()) - {"HEAD"}):
            resp = client.request(method, _fill_path(route.path), follow_redirects=False)
            results.append({"method": method, "path": route.path, "status": resp.status_code})
    return results


def unprotected(results: Iterable[dict], public=PUBLIC_ROUTES, off_in_prod=OFF_IN_PROD) -> list[str]:
    bad = []
    for r in results:
        key = (r["method"], r["path"])
        if key in public:
            continue
        if r["status"] in REJECTED:
            continue
        if r["status"] == 404 and key in off_in_prod:
            continue
        bad.append(f'{r["method"]} {r["path"]} -> {r["status"]}')
    return bad


_PROBE = """
import json
import main
from tests.test_route_auth_inventory import probe_routes
print("RESULT=" + json.dumps(probe_routes(main.app)))
"""


def _probe_real_app(tmp_path) -> list[dict]:
    env = {
        **os.environ,
        "MODE": "prod",
        "APP_BASE_URL": "https://emly.example.com",
        "DATA_DIR": str(tmp_path),
        "DATABASE_URL": f"sqlite:///{tmp_path}/inventory.db",
        # Mount the embedded issuer so its routes are inventoried too.
        "AUTH_LOCAL_ISSUER_ENABLED": "true",
        "AUTH_LOCAL_KEYS_DIR": str(tmp_path / "keys"),
        "AUTH_BOOTSTRAP_SUPERADMIN_EMAIL": "",
    }
    env.pop("ENABLE_API_DOCS", None)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300
    )
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT=")), None)
    assert line, f"probe failed:\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-4000:]}"
    return json.loads(line[len("RESULT="):])


@pytest.fixture(scope="module")
def real_app_results(tmp_path_factory):
    return _probe_real_app(tmp_path_factory.mktemp("inventory"))


def test_every_route_rejects_unauthenticated_requests(real_app_results):
    results = real_app_results
    assert len(results) > 100, "route walk found suspiciously few routes"
    bad = unprotected(results)
    assert not bad, (
        "Routes reachable without credentials. Protect them (e.g. Depends(get_admin)) "
        "or add them to PUBLIC_ROUTES with a reason:\n  " + "\n  ".join(bad)
    )

    # Allowlist hygiene: every exempt entry must still exist, so stale
    # entries can't silently cover a future route with the same path.
    seen = {(r["method"], r["path"]) for r in results}
    stale = sorted(set(PUBLIC_ROUTES) - seen) + sorted(set(OFF_IN_PROD) - seen)
    assert not stale, f"PUBLIC_ROUTES / OFF_IN_PROD entries with no matching route: {stale}"


def test_api_docs_not_registered_in_prod(real_app_results):
    paths = {r["path"] for r in real_app_results}
    assert not paths & {"/docs", "/redoc", "/openapi.json"}


def test_inventory_detects_an_unprotected_route():
    """Guard the guard: a new route without an auth dependency is caught,
    while one with a dependency that raises 401 passes."""

    def _require_auth():
        raise HTTPException(status_code=401)

    app = FastAPI()

    @app.get("/api/admin/protected/{item_id}")
    def protected(item_id: str, _=Depends(_require_auth)):
        return {"ok": True}

    @app.post("/api/admin/leaky")
    def leaky(payload: dict):
        return payload

    @app.get("/api/admin/also-leaky/{slug}")
    def also_leaky(slug: str):
        raise HTTPException(status_code=404)  # "not found" before any auth

    bad = unprotected(probe_routes(app), public={}, off_in_prod={})
    assert "POST /api/admin/leaky -> 422" in bad
    assert "GET /api/admin/also-leaky/{slug} -> 404" in bad
    assert not any("protected" in b and "leaky" not in b for b in bad)
