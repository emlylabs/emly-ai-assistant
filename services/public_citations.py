"""N3: what a widget visitor is told about the sources behind an answer.

RAG search returns each hit as ``{"metadata": <the whole Qdrant payload>,
"chunk": <passage>}``. The widget chat route used to stream that as is to
an anonymous visitor: the server path of uploaded files
(``/data/bots/<id>/uploads/...``), the bot and file ids, PDF document
properties (author, producer, creation tool), any ``payload:`` meta tags
from crawled pages, and the full passage text. It did so even when the
bot's *Show citations* setting was off and the widget displayed nothing.

``public_citations`` keeps only what the widget's citation pill shows:

- nothing at all unless the bot has ``show_citations`` on;
- a title (page title, document title, or the file's name; never a path);
- a link, and an Open Graph description and image, only when they are
  ``http(s)`` URLs;
- the page the passage is on, for documents that have pages (G12a);
- a short excerpt of the passage (the length the widget displays);
- the relevance score.
"""
from __future__ import annotations

import ast
import json
import os
from typing import Any, Dict, List, Optional

# Matches DEFAULT_CHUNK_CHARS in widget/src/components/CitationPills.tsx.
EXCERPT_CHARS = 280


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        for parse in (json.loads, ast.literal_eval):
            try:
                parsed = parse(value)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _http(value: Any) -> Optional[str]:
    v = _text(value)
    return v if v and v.lower().startswith(("http://", "https://")) else None


def _excerpt(chunk: Any) -> Optional[str]:
    text = _text(chunk)
    if not text:
        return None
    if len(text) <= EXCERPT_CHARS:
        return text
    return text[: EXCERPT_CHARS - 1].rstrip() + "…"


def citation_page(meta: Any) -> Optional[str]:
    """The page a passage came from, as readers number it, or None.

    The PDF loaders store ``page_label`` (the printed label, e.g. "iv" or
    "12") and ``page`` (0-based) on every chunk. Prefer the label; else
    count from 1. Other file types have no page.
    """
    if not isinstance(meta, dict):
        return None
    label = _text(meta.get("page_label"))
    if label and len(label) <= 16:
        return label
    page = meta.get("page")
    if isinstance(page, int) and not isinstance(page, bool) and page >= 0:
        return str(page + 1)
    return None


def _score(c: Dict[str, Any], meta: Dict[str, Any]) -> Optional[float]:
    for v in (meta.get("relevance_score"), c.get("relevance_score"), c.get("score")):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return round(float(v), 4)
    return None


def _one(c: Dict[str, Any]) -> Dict[str, Any]:
    meta = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
    og_raw = _as_dict(c.get("og") or meta.get("og"))
    og = {k: v for k, v in {
        "title": _text(og_raw.get("title")),
        "description": _text(og_raw.get("description")),
        "url": _http(og_raw.get("url")),
        "image": _http(og_raw.get("image")),
    }.items() if v}

    url = _http(meta.get("source_url")) or og.get("url") or _http(meta.get("source"))
    title = og.get("title") or _text(meta.get("title"))
    if not title and not url:
        # An uploaded file: its name, never the path it is stored under.
        name = _text(meta.get("filename")) or _text(meta.get("source"))
        title = os.path.basename(name) if name else None

    public_meta = {k: v for k, v in {
        "title": title,
        "source_url": url,
        "image": _http(meta.get("image")),
        "page": citation_page(meta),
    }.items() if v}
    out: Dict[str, Any] = {"metadata": public_meta}
    if og:
        out["og"] = og
    excerpt = _excerpt(c.get("chunk"))
    if excerpt:
        out["chunk"] = excerpt
    score = _score(c, meta)
    if score is not None:
        out["relevance_score"] = score
    return out


def public_citations(citations: Any, *, show: bool) -> List[Dict[str, Any]]:
    """The citations a widget visitor may see (empty unless ``show``)."""
    if not show or not isinstance(citations, list):
        return []
    return [_one(c) for c in citations if isinstance(c, dict)]


def bot_shows_citations(bot_id: str) -> bool:
    """The bot's *Show citations* setting; off when unset or unreadable."""
    try:
        from services.bot_config import get_config_for_bot

        return bool(get_config_for_bot(bot_id).show_citations)
    except Exception:
        return False
