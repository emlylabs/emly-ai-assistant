"""G10b: admin-set widget icons load only from approved hosts.

The widget mounts in the host page's DOM, so Emly's CSP can't restrict
its images. With WIDGET_ICON_HOSTS set, an icon on any other host is
refused when the config is saved (R25, unforceable) and dropped when the
widget config is served.
"""
from __future__ import annotations

import uuid

import pytest

from services.config_validation import FIELD_FORMAT_RULE_PREFIXES, blocking_format_issues, validate_config

TRACKER = "https://tracker.example/pixel.png"


@pytest.fixture
def hosts(monkeypatch):
    monkeypatch.setenv("WIDGET_ICON_HOSTS", "cdn.corp.example, *.assets.example")
    monkeypatch.setenv("APP_BASE_URL", "https://emly.corp.example")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)


def _cfg(path, value):
    if path.startswith("widget."):
        return {"widget": {path.split(".", 1)[1]: value}}
    return {path: value}


def _keys(config):
    return {i.key for i in validate_config(config)
            if i.severity == "error" and i.key.startswith(FIELD_FORMAT_RULE_PREFIXES)}


@pytest.mark.parametrize("path", ["widget.logo", "widget.avatar", "open_icon", "close_icon"])
def test_any_host_is_allowed_when_unset(monkeypatch, path):
    monkeypatch.delenv("WIDGET_ICON_HOSTS", raising=False)
    assert _keys(_cfg(path, TRACKER)) == set()


@pytest.mark.parametrize("path", ["widget.logo", "widget.avatar", "open_icon", "close_icon"])
def test_an_unlisted_host_cant_be_saved(hosts, path):
    config = _cfg(path, TRACKER)
    name = path.rsplit(".", 1)[-1]
    issues = blocking_format_issues(validate_config(config), config, {})
    assert [i.key for i in issues] == [f"R25_icon_host_not_allowed_{name}"]
    assert "tracker.example" in issues[0].message and "WIDGET_ICON_HOSTS" in issues[0].message


@pytest.mark.parametrize("value,allowed", [
    ("https://cdn.corp.example/logo.png", True),
    ("http://cdn.corp.example/logo.png", True),
    ("//cdn.corp.example/logo.png", True),
    ("https://img.assets.example/a.png", True),
    ("https://assets.example/a.png", False),            # *.domain is subdomains only
    ("https://cdn.corp.example.evil.example/a.png", False),
    ("https://emly.corp.example/static/a.png", True),   # this app's own host
    ("https://emly.corp.example:8443/a.png", False),    # but not another port
    ("//evil.example/a.png", False),
    ("/static/logo.png", True),                         # the embedding site's own path
    ("./logo.png", True),
    ("data:image/png;base64,AAAA", True),               # inline, nothing fetched
])
def test_which_icons_are_allowed(hosts, value, allowed):
    from services.widget_icons import is_icon_allowed

    assert is_icon_allowed(value) is allowed
    assert ("R25_icon_host_not_allowed_logo" in _keys(_cfg("widget.logo", value))) is (not allowed)


def test_every_icon_field_gets_the_format_check():
    """R17 used to check only the logo."""
    for path in ("widget.avatar", "open_icon", "close_icon"):
        name = path.rsplit(".", 1)[-1]
        assert _keys(_cfg(path, "javascript:alert(1)")) == {f"R17_invalid_icon_url_{name}"}
    assert _keys(_cfg("widget.logo", "javascript:alert(1)")) == {"R17_invalid_logo_url"}


def test_stored_off_list_icons_are_not_served(isolated_db, hosts):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.bots import Bots
    from routes import widget

    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"i-{uuid.uuid4().hex[:6]}", name="I",
                      config_json={"widget": {"logo": TRACKER, "avatar": "https://cdn.corp.example/a.png"},
                                   "open_icon": "//evil.example/o.png", "close_icon": "/img/close.png"})
    app = FastAPI()
    app.include_router(widget.router)
    body = TestClient(app).get(f"/widget/{bot.id}/config").json()

    assert body["logo"] is None and body["open_icon"] is None
    assert body["avatar"] == "https://cdn.corp.example/a.png"
    assert body["close_icon"] == "/img/close.png"
