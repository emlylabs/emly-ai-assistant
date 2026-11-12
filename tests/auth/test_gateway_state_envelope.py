"""Round-trip + tamper tests for the centralized-gateway state envelope.

The tenant app (this repo) constructs the envelope; the deployer's
``app-deployer/backend/api/auth_gateway.py:_parse_state_envelope`` validates
it. Tests inline a small parser that mirrors the gateway exactly — that way
a drift in either side breaks the suite even when the deployer isn't on the
Python path.

Envelope format::

    <inner_state>.<b64host>.<b64path>.<b64sig>
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from routes.auth import _b64url_encode, _build_state_envelope


_SECRET = "test-secret-" + "a" * 48  # length-similar to a real 64-hex secret
_INNER = "innerstate1234567890abcd"
_HOST = "acme-ab12cd.example.com"
_PATH = "/api/admin/auth/callback"


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _parse_envelope(state: str, secret: str) -> tuple[str, str, str]:
    """Mirror of ``_parse_state_envelope`` in app-deployer.

    Raises ``ValueError`` for any malformed / forged input; the gateway uses
    a typed ``_EnvelopeError`` but the failure modes are the same.
    """
    if not state or state.count(".") != 3:
        raise ValueError("envelope_malformed")
    inner, host_b64, path_b64, sig_b64 = state.split(".")
    if not inner or not host_b64 or not path_b64 or not sig_b64:
        raise ValueError("envelope_malformed")
    body = f"{inner}.{host_b64}.{path_b64}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected_b64 = (
        base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
    )
    if not hmac.compare_digest(expected_b64, sig_b64):
        raise ValueError("envelope_bad_signature")
    host = _b64url_decode(host_b64).decode("utf-8")
    path = _b64url_decode(path_b64).decode("utf-8")
    return inner, host, path


# -------- happy path --------


def test_envelope_round_trip():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    assert env.count(".") == 3
    inner, host, path = _parse_envelope(env, _SECRET)
    assert inner == _INNER
    assert host == _HOST
    assert path == _PATH


def test_envelope_segments_are_b64url_no_padding():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    parts = env.split(".")
    assert len(parts) == 4
    # The b64 segments should be padless (rstrip("=")) and use the URL
    # alphabet — no "+", "/", or "=" characters.
    for seg in parts[1:]:
        assert "=" not in seg
        assert "+" not in seg
        assert "/" not in seg


def test_inner_state_passes_through_unencoded():
    """The gateway looks up state-store by the inner state verbatim, so we
    must NOT encode it. Catches an accidental b64-everything regression."""
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    assert env.startswith(_INNER + ".")


# -------- tamper detection (each segment) --------


def test_tampered_inner_state_rejected():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    _, host_b64, path_b64, sig_b64 = env.split(".")
    forged = f"{_INNER}X.{host_b64}.{path_b64}.{sig_b64}"
    with pytest.raises(ValueError, match="bad_signature"):
        _parse_envelope(forged, _SECRET)


def test_tampered_host_rejected():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    inner, _host_b64, path_b64, sig_b64 = env.split(".")
    evil_host_b64 = _b64url_encode(b"evil.example.com")
    forged = f"{inner}.{evil_host_b64}.{path_b64}.{sig_b64}"
    with pytest.raises(ValueError, match="bad_signature"):
        _parse_envelope(forged, _SECRET)


def test_tampered_path_rejected():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    inner, host_b64, _path_b64, sig_b64 = env.split(".")
    evil_path_b64 = _b64url_encode(b"/admin/danger")
    forged = f"{inner}.{host_b64}.{evil_path_b64}.{sig_b64}"
    with pytest.raises(ValueError, match="bad_signature"):
        _parse_envelope(forged, _SECRET)


def test_tampered_signature_rejected():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    inner, host_b64, path_b64, sig_b64 = env.split(".")
    # Flip one byte in the signature.
    forged_sig = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
    forged = f"{inner}.{host_b64}.{path_b64}.{forged_sig}"
    with pytest.raises(ValueError, match="bad_signature"):
        _parse_envelope(forged, _SECRET)


def test_wrong_secret_rejected():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    with pytest.raises(ValueError, match="bad_signature"):
        _parse_envelope(env, _SECRET + "extra")


# -------- malformed input --------


def test_malformed_too_few_segments():
    with pytest.raises(ValueError, match="malformed"):
        _parse_envelope("only.three.segments", _SECRET)


def test_malformed_empty_segment():
    env = _build_state_envelope(_INNER, _HOST, _PATH, _SECRET)
    inner, host_b64, path_b64, _ = env.split(".")
    with pytest.raises(ValueError, match="malformed"):
        _parse_envelope(f"{inner}.{host_b64}.{path_b64}.", _SECRET)


# -------- builder input validation --------


def test_builder_rejects_dot_in_inner():
    with pytest.raises(ValueError, match="inner_state"):
        _build_state_envelope("has.dot", _HOST, _PATH, _SECRET)


def test_builder_rejects_relative_path():
    with pytest.raises(ValueError, match="callback_path"):
        _build_state_envelope(_INNER, _HOST, "no-leading-slash", _SECRET)


def test_builder_rejects_missing_host():
    with pytest.raises(ValueError, match="tenant_host"):
        _build_state_envelope(_INNER, "", _PATH, _SECRET)


def test_builder_rejects_missing_secret():
    with pytest.raises(ValueError, match="secret"):
        _build_state_envelope(_INNER, _HOST, _PATH, "")


# -------- login route wiring --------


def _capture_provider(captured: list) -> "object":
    """Tiny stand-in for ``AuthProvider`` used by the login-route tests.

    Records every ``authorize_url`` call and returns a constant URL so the
    test can assert on ``captured`` rather than the redirect body.
    """

    class _P:
        def authorize_url(self, oauth_state, redirect_uri, *, state_override=None):
            captured.append(
                {
                    "inner_state": oauth_state.state,
                    "redirect_uri": redirect_uri,
                    "state_override": state_override,
                }
            )
            return "http://idp.example/authorize?stub=1"

        def verify_token(self, *_a, **_kw):  # not exercised here
            raise NotImplementedError

        def exchange_code(self, *_a, **_kw):  # not exercised here
            raise NotImplementedError

        def logout_url(self, *_a, **_kw):
            return None

    return _P()


def _login_test_client(monkeypatch, captured: list, env: dict[str, str]):
    """Spin up a minimal FastAPI app with just the auth router, with the
    auth-provider singleton replaced by ``_capture_provider``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    for k, v in env.items():
        monkeypatch.setenv(k, v)

    from services.auth import factory as provider_factory

    monkeypatch.setattr(provider_factory, "_provider", _capture_provider(captured))

    from routes import auth as auth_routes

    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/admin")
    # Disable slowapi rate limiting for the test (we hit /login twice in one
    # of the cases). Setting a no-op limiter on app.state is the cheapest path.
    from services.auth.ratelimit import limiter

    app.state.limiter = limiter
    limiter.reset()
    return TestClient(app, base_url="https://acme-ab12cd.example.com")


def test_login_without_gateway_uses_per_tenant_redirect_and_no_envelope(monkeypatch):
    captured: list = []
    client = _login_test_client(
        monkeypatch,
        captured,
        env={
            "APP_BASE_URL": "https://acme-ab12cd.example.com",
            "AUTH_OIDC_REDIRECT_URI": "",
            "AUTH_GATEWAY_HMAC_SECRET": "",
        },
    )
    r = client.get("/api/admin/auth/login", follow_redirects=False)
    assert r.status_code == 302
    assert len(captured) == 1
    assert (
        captured[0]["redirect_uri"]
        == "https://acme-ab12cd.example.com/api/admin/auth/callback"
    )
    # No envelope; the raw inner state is what would be sent to the IdP.
    assert captured[0]["state_override"] is None


def test_login_with_gateway_uses_gateway_redirect_and_wraps_state(monkeypatch):
    captured: list = []
    secret = "test-secret-" + "a" * 48
    gateway_uri = "https://deployer.example.com/api/auth-gateway/callback"
    client = _login_test_client(
        monkeypatch,
        captured,
        env={
            "APP_BASE_URL": "https://acme-ab12cd.example.com",
            "AUTH_OIDC_REDIRECT_URI": gateway_uri,
            "AUTH_GATEWAY_HMAC_SECRET": secret,
        },
    )
    r = client.get("/api/admin/auth/login", follow_redirects=False)
    assert r.status_code == 302
    assert len(captured) == 1
    assert captured[0]["redirect_uri"] == gateway_uri

    envelope = captured[0]["state_override"]
    assert envelope is not None, "envelope should be applied when gateway is configured"
    inner, host, path = _parse_envelope(envelope, secret)
    # The envelope's inner_state must match the state we stashed in the
    # state-store (i.e. the inner_state generated by `login`), so the
    # gateway's forwarded callback can look it up.
    assert inner == captured[0]["inner_state"]
    assert host == "acme-ab12cd.example.com"
    assert path == "/api/admin/auth/callback"


def test_login_with_gateway_uri_but_no_secret_skips_envelope(monkeypatch):
    """If the secret is missing we still forward the gateway redirect_uri
    (the IdP allow-list is the gateway's URL) but DON'T forge a half-baked
    envelope. The gateway's parser will reject the empty state cleanly."""
    captured: list = []
    client = _login_test_client(
        monkeypatch,
        captured,
        env={
            "APP_BASE_URL": "https://acme-ab12cd.example.com",
            "AUTH_OIDC_REDIRECT_URI": "https://deployer.example.com/api/auth-gateway/callback",
            "AUTH_GATEWAY_HMAC_SECRET": "",
        },
    )
    r = client.get("/api/admin/auth/login", follow_redirects=False)
    assert r.status_code == 302
    assert captured[0]["state_override"] is None
