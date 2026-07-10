"""Tests for the authorization-code-with-PKCE dance in services/auth/issuer/flows.py."""

from __future__ import annotations

import base64
import hashlib
import time

import pytest

from services.auth.issuer.flows import (
    CodeExchangeError,
    InMemoryCodeStore,
    InvalidAuthorizeRequest,
    InvalidRedirectUriError,
    UnknownClientError,
    check_client,
    consume_code,
    generate_code,
    issue_code,
    parse_authorize_request,
    verify_pkce,
)

CONFIGURED_CLIENT = "emly-admin-console"
ALLOWED_REDIRECTS = ["http://localhost:8080/api/admin/auth/callback"]


def _make_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(b"x" * 32).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _good_authz(**overrides):
    verifier, challenge = _make_pkce()
    base = {
        "client_id": CONFIGURED_CLIENT,
        "redirect_uri": ALLOWED_REDIRECTS[0],
        "state": "STATE-1",
        "nonce": "NONCE-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "response_type": "code",
        "scope": "openid email",
    }
    base.update(overrides)
    return base, verifier


def test_parse_authorize_request_accepts_valid_input():
    params, _ = _good_authz()
    authz = parse_authorize_request(**params)
    assert authz.client_id == CONFIGURED_CLIENT
    assert authz.state == "STATE-1"


def test_parse_authorize_request_rejects_missing_state():
    params, _ = _good_authz(state=None)
    with pytest.raises(InvalidAuthorizeRequest, match="state"):
        parse_authorize_request(**params)


def test_parse_authorize_request_rejects_missing_pkce():
    params, _ = _good_authz(code_challenge=None)
    with pytest.raises(InvalidAuthorizeRequest, match="code_challenge"):
        parse_authorize_request(**params)


def test_parse_authorize_request_rejects_plain_pkce_method():
    params, _ = _good_authz(code_challenge_method="plain")
    with pytest.raises(InvalidAuthorizeRequest, match="code_challenge_method"):
        parse_authorize_request(**params)


def test_parse_authorize_request_rejects_non_code_response_type():
    params, _ = _good_authz(response_type="token")
    with pytest.raises(InvalidAuthorizeRequest, match="response_type"):
        parse_authorize_request(**params)


def test_check_client_accepts_configured_client_and_redirect():
    params, _ = _good_authz()
    authz = parse_authorize_request(**params)
    check_client(authz, configured_client_id=CONFIGURED_CLIENT, allowed_redirect_uris=ALLOWED_REDIRECTS)


def test_check_client_rejects_unknown_client():
    params, _ = _good_authz(client_id="rogue")
    authz = parse_authorize_request(**params)
    with pytest.raises(UnknownClientError):
        check_client(authz, configured_client_id=CONFIGURED_CLIENT, allowed_redirect_uris=ALLOWED_REDIRECTS)


def test_check_client_rejects_unlisted_redirect_uri():
    params, _ = _good_authz(redirect_uri="https://attacker.example.com/cb")
    authz = parse_authorize_request(**params)
    with pytest.raises(InvalidRedirectUriError):
        check_client(authz, configured_client_id=CONFIGURED_CLIENT, allowed_redirect_uris=ALLOWED_REDIRECTS)


def test_pkce_verifier_matches_challenge():
    verifier, challenge = _make_pkce()
    assert verify_pkce(verifier, challenge) is True


def test_pkce_verifier_mismatch_rejected():
    _verifier, challenge = _make_pkce()
    assert verify_pkce("wrong-verifier", challenge) is False


def test_pkce_only_supports_s256():
    verifier, challenge = _make_pkce()
    assert verify_pkce(verifier, challenge, method="plain") is False


def test_issue_then_consume_round_trip():
    store = InMemoryCodeStore()
    params, verifier = _good_authz()
    authz = parse_authorize_request(**params)
    code = issue_code(
        code_store=store, authz=authz,
        admin_id="adm-1", email="alice@x.y", email_verified=True,
    )
    record = consume_code(
        code_store=store, code=code, code_verifier=verifier,
        redirect_uri=ALLOWED_REDIRECTS[0],
    )
    assert record.admin_id == "adm-1"
    assert record.email == "alice@x.y"
    assert record.nonce == "NONCE-1"


def test_consume_code_is_one_shot():
    store = InMemoryCodeStore()
    params, verifier = _good_authz()
    authz = parse_authorize_request(**params)
    code = issue_code(
        code_store=store, authz=authz,
        admin_id="adm-1", email="alice@x.y", email_verified=True,
    )
    consume_code(
        code_store=store, code=code, code_verifier=verifier,
        redirect_uri=ALLOWED_REDIRECTS[0],
    )
    with pytest.raises(CodeExchangeError, match="invalid or expired"):
        consume_code(
            code_store=store, code=code, code_verifier=verifier,
            redirect_uri=ALLOWED_REDIRECTS[0],
        )


def test_consume_code_rejects_pkce_verifier_mismatch():
    store = InMemoryCodeStore()
    params, _verifier = _good_authz()
    authz = parse_authorize_request(**params)
    code = issue_code(
        code_store=store, authz=authz,
        admin_id="adm-1", email="alice@x.y", email_verified=True,
    )
    with pytest.raises(CodeExchangeError, match="PKCE"):
        consume_code(
            code_store=store, code=code, code_verifier="wrong-verifier",
            redirect_uri=ALLOWED_REDIRECTS[0],
        )


def test_consume_code_rejects_redirect_uri_mismatch():
    store = InMemoryCodeStore()
    params, verifier = _good_authz()
    authz = parse_authorize_request(**params)
    code = issue_code(
        code_store=store, authz=authz,
        admin_id="adm-1", email="alice@x.y", email_verified=True,
    )
    with pytest.raises(CodeExchangeError, match="redirect_uri"):
        consume_code(
            code_store=store, code=code, code_verifier=verifier,
            redirect_uri="https://attacker.example.com/cb",
        )


def test_expired_code_rejected():
    from datetime import timedelta
    store = InMemoryCodeStore()
    params, verifier = _good_authz()
    authz = parse_authorize_request(**params)
    code = issue_code(
        code_store=store, authz=authz,
        admin_id="adm-1", email="alice@x.y", email_verified=True,
    )
    # Force expiry by overwriting with negative TTL.
    record = store.pop(code)  # one-shot pop, but we'll re-put with bad TTL
    assert record is not None
    store.put(record, ttl=timedelta(seconds=-1))
    with pytest.raises(CodeExchangeError, match="invalid or expired"):
        consume_code(
            code_store=store, code=code, code_verifier=verifier,
            redirect_uri=ALLOWED_REDIRECTS[0],
        )


def test_generate_code_is_url_safe_and_unique():
    a, b = generate_code(), generate_code()
    assert a != b
    # url-safe alphabet
    assert all(c.isalnum() or c in "-_" for c in a)
