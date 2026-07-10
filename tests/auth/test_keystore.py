"""Tests for the embedded issuer's RS256 keystore."""

from __future__ import annotations

import json

from services.auth.issuer.keystore import Keystore


def test_first_init_generates_a_keypair(tmp_path):
    ks = Keystore(tmp_path)
    assert len(list(tmp_path.glob("*.pem"))) == 2  # priv + pub
    assert ks.current_kid()
    assert ks.current_private_pem().startswith(b"-----BEGIN PRIVATE KEY-----")


def test_second_init_loads_existing_keys(tmp_path):
    ks1 = Keystore(tmp_path)
    kid1 = ks1.current_kid()
    # Re-construct from same directory.
    ks2 = Keystore(tmp_path)
    assert ks2.current_kid() == kid1
    assert ks2.current_private_pem() == ks1.current_private_pem()


def test_rotate_creates_new_key_and_makes_it_current(tmp_path):
    ks = Keystore(tmp_path)
    old_kid = ks.current_kid()
    new_kid = ks.rotate()
    assert new_kid != old_kid
    assert ks.current_kid() == new_kid
    # Both keys still on disk and in JWKS.
    assert {old_kid, new_kid} == set(ks.all_kids())


def test_jwks_dict_includes_all_keys_with_correct_metadata(tmp_path):
    ks = Keystore(tmp_path)
    ks.rotate()
    jwks = ks.jwks_dict()
    kids = {k["kid"] for k in jwks["keys"]}
    assert kids == set(ks.all_kids())
    for k in jwks["keys"]:
        assert k["kty"] == "RSA"
        assert k["use"] == "sig"
        assert k["alg"] == "RS256"
        # n and e are base64url RSA params.
        assert "n" in k and "e" in k
    # JWKS must be JSON-serialisable (it'll be served at /.well-known/jwks.json).
    json.dumps(jwks)


def test_orphan_private_without_public_is_skipped(tmp_path, caplog):
    # Drop a private-only file in the directory.
    (tmp_path / "orphaned.pem").write_bytes(b"-----BEGIN PRIVATE KEY-----\nbroken\n-----END PRIVATE KEY-----\n")
    ks = Keystore(tmp_path)
    # Generates a fresh keypair instead of dying.
    assert ks.current_kid() != "orphaned"
    assert "orphaned" not in ks.all_kids()


def test_kid_is_stable_across_inits_for_same_keypair(tmp_path):
    """The kid is derived from the public key DER so a re-import yields the same kid."""
    ks1 = Keystore(tmp_path)
    kid1 = ks1.current_kid()
    ks2 = Keystore(tmp_path)
    assert ks2.current_kid() == kid1
