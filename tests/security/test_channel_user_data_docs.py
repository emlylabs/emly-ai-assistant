"""N6: the README says what Emly keeps about each chat platform's users.

A new channel adapter must add its row, so the data-protection pack
never silently falls behind what the code stores.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import channels
from channels import registry

README = Path(__file__).resolve().parents[2] / "README.md"
LABELS = {
    "slack": "Slack",
    "teams": "Microsoft Teams",
    "google_chat": "Google Chat",
    "whatsapp_cloud": "WhatsApp",
    "telegram": "Telegram",
}


def _section() -> str:
    text = README.read_text()
    m = re.search(r"^#### Chat-platform users\n(.*?)(?=^#{2,4} )", text, re.S | re.M)
    assert m, "README is missing the 'Chat-platform users' section"
    return m.group(1)


def test_every_chat_platform_has_a_row():
    for mod in pkgutil.iter_modules(channels.__path__):
        importlib.import_module(f"channels.{mod.name}")
    types = set(registry.list_types()) - {"web_widget"}
    assert types <= set(LABELS), f"add a README row and a label here for: {sorted(types - set(LABELS))}"
    section = _section()
    for t in types:
        assert f"| {LABELS[t]} |" in section


def test_slack_email_scope_is_described_as_optional():
    assert "users:read.email" in _section()
    ui = (README.parent / "ui/app/bots/[slug]/channels/page.tsx").read_text()
    assert "users:read.email</code> only if" in ui
