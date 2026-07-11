"""Tests for the in-memory state store used by the OIDC dance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.auth.base import OAuthState
from services.auth.state_store import InMemoryStateStore


def _make_state(name: str = "S") -> OAuthState:
    return OAuthState(
        state=name,
        code_verifier="V",
        nonce="N",
        return_to=None,
        created_at=datetime.now(timezone.utc),
    )


def test_put_then_pop_returns_value():
    store = InMemoryStateStore()
    s = _make_state()
    store.put("k", s)
    assert store.pop("k") == s


def test_pop_returns_none_for_missing_key():
    store = InMemoryStateStore()
    assert store.pop("never-stored") is None


def test_pop_is_one_shot():
    store = InMemoryStateStore()
    store.put("k", _make_state())
    assert store.pop("k") is not None
    assert store.pop("k") is None


def test_expired_entries_return_none_on_pop():
    store = InMemoryStateStore()
    store.put("k", _make_state(), ttl=timedelta(seconds=-1))
    assert store.pop("k") is None


def test_sweep_removes_expired_entries_only():
    store = InMemoryStateStore()
    store.put("expired", _make_state(), ttl=timedelta(seconds=-1))
    store.put("fresh", _make_state(), ttl=timedelta(minutes=5))
    removed = store.sweep()
    assert removed == 1
    assert store.pop("fresh") is not None
