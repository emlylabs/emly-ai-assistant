"""Tests for argon2id password hashing in the embedded issuer."""

from __future__ import annotations

import pytest

from services.auth.issuer.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)


def test_hash_then_verify_round_trip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("right")
    assert verify_password("wrong", h) is False


def test_verify_returns_false_on_malformed_hash():
    assert verify_password("anything", "not-an-argon2-hash") is False


def test_verify_returns_false_on_empty_inputs():
    assert verify_password("", "$argon2id$v=19$m=65536,t=3,p=4$abc$def") is False
    assert verify_password("password", "") is False


def test_hash_password_rejects_empty_string():
    with pytest.raises(ValueError):
        hash_password("")


def test_hashes_are_argon2id_format():
    h = hash_password("anything")
    assert h.startswith("$argon2id$")


def test_needs_rehash_returns_true_for_malformed_hash():
    assert needs_rehash("not-an-argon2-hash") is True


def test_needs_rehash_returns_false_for_current_params():
    h = hash_password("anything")
    assert needs_rehash(h) is False


def test_two_hashes_of_same_password_differ_due_to_salt():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2
    # Both still verify.
    assert verify_password("same", h1)
    assert verify_password("same", h2)
