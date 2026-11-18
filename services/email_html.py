"""Allow-list sanitiser for model-written HTML that goes into emails (S5).

Alert emails carry a model-written summary and the bot's answers. Both can
be steered by prompt injection, and they are sent from the customer's own
mail relay, so a link or tracking image in them would arrive looking
genuine. Only plain formatting survives: no attributes, no links, no
images, no scripts or styles. Text is always re-escaped.
"""
from __future__ import annotations

import html
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset({
    "p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "code", "pre", "hr",
})
_VOID_TAGS = frozenset({"br", "hr"})
# Elements whose text content is dropped along with the tag.
_DROP_CONTENT = frozenset({"script", "style", "head", "title", "template", "iframe", "object", "svg", "math"})


class _Sanitiser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._open: list[str] = []
        self._dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT:
            self._dropping += 1
            return
        if self._dropping or tag not in ALLOWED_TAGS:
            return
        if tag in _VOID_TAGS:
            self.out.append(f"<{tag}>")
            return
        self.out.append(f"<{tag}>")
        self._open.append(tag)

    def handle_startendtag(self, tag, attrs):
        if not self._dropping and tag in _VOID_TAGS:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT:
            self._dropping = max(0, self._dropping - 1)
            return
        if self._dropping or tag not in ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag in self._open:
            # Close anything left open inside it, so the output stays balanced.
            while self._open:
                inner = self._open.pop()
                self.out.append(f"</{inner}>")
                if inner == tag:
                    break

    def handle_data(self, data):
        if not self._dropping:
            self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self._open:
            self.out.append(f"</{self._open.pop()}>")
        return "".join(self.out)


def sanitize_email_html(value: str) -> str:
    parser = _Sanitiser()
    parser.feed(value or "")
    parser.close()
    return parser.result()
