"""S8: production defaults apply unless MODE explicitly says dev.

Before, anything outside prod|production|preprod|stage|staging (including
unset, which is what the Quick Start and the compose file gave you, and a
typo such as ``live``) silently meant dev: API docs on, an auto-generated
secrets key next to the database, audit fail-open, no session maximum.
"""
from __future__ import annotations

import pytest

from utils import runtime


@pytest.mark.parametrize("value", [None, "", "prod", "production", "staging", "live", "PROD ", "qa"])
def test_production_unless_dev(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MODE", raising=False)
    else:
        monkeypatch.setenv("MODE", value)
    assert runtime.is_prod_mode() is True


@pytest.mark.parametrize("value", ["dev", "development", "local", "test", " Dev "])
def test_dev_only_when_asked(monkeypatch, value):
    monkeypatch.setenv("MODE", value)
    assert runtime.is_prod_mode() is False
    assert runtime.mode_warning() is None


@pytest.mark.parametrize("value,expected", [
    (None, "MODE is not set"),
    ("live", "MODE='live' is not a known value"),
    ("prod", None),
])
def test_boot_warning_when_production_is_implicit(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("MODE", raising=False)
    else:
        monkeypatch.setenv("MODE", value)
    warning = runtime.mode_warning()
    assert (warning is None) if expected is None else (expected in warning)


def test_env_sample_ships_prod():
    from pathlib import Path

    sample = (Path(__file__).resolve().parents[2] / ".env.sample").read_text()
    assert "\nMODE=prod\n" in sample
