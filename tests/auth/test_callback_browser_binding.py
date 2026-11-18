"""S9: a sign-in callback works only in the browser that started it.

State was kept server-side only and popped by value, and the callback is a
GET (the origin check doesn't apply). A low-privilege admin could start a
sign-in, keep their own unused callback URL and get a victim admin to open
it within five minutes: the victim was then signed in as the attacker, and
keys or documents they entered went into bots the attacker controls.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from tests.auth.test_callback_session_token import bootstrapped_admin, oidc_app  # noqa: F401


def _start_and_authorize(client, admin):
    r = client.get("/api/admin/auth/login")
    params = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    r = client.post("/api/auth/local/authorize",
                    data={**params, "email": admin["email"], "password": admin["password"]})
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    return f"/api/admin/auth/callback?code={code}&state={params['state']}"


def test_callback_refused_in_another_browser(oidc_app, bootstrapped_admin):  # noqa: F811
    attacker = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    victim = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    url = _start_and_authorize(attacker, bootstrapped_admin)

    r = victim.get(url)
    assert r.status_code == 400
    assert "emly_admin_session" not in r.cookies

    # The state is spent: the attacker can't use it afterwards either.
    assert attacker.get(url).status_code == 400


def test_callback_accepted_in_the_same_browser_and_cookie_cleared(oidc_app, bootstrapped_admin):  # noqa: F811
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    url = _start_and_authorize(client, bootstrapped_admin)
    r = client.get(url)
    assert r.status_code == 302
    assert r.cookies.get("emly_admin_session")
    assert not client.cookies.get("emly_auth_state")


def test_two_tabs_can_sign_in(oidc_app, bootstrapped_admin):  # noqa: F811
    client = TestClient(oidc_app, base_url="http://testserver", follow_redirects=False)
    first = _start_and_authorize(client, bootstrapped_admin)
    second = _start_and_authorize(client, bootstrapped_admin)
    assert client.get(first).status_code == 302
    assert client.get(second).status_code == 302
