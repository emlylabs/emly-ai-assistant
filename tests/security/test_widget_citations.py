"""N3: widget visitors get only what the citation pill shows.

The chat route streamed each RAG hit's whole stored record to anonymous
visitors: the server path of uploaded files, bot and file ids, PDF
document properties, ``payload:`` meta tags and the full passage. It did
so even for bots with *Show citations* off.
"""
from __future__ import annotations

import json
import uuid

import pytest

from services.public_citations import EXCERPT_CHARS, public_citations

UPLOAD = {
    "metadata": {
        "source": "/data/bots/bot-1/uploads/abc/handbook.pdf",
        "filename": "handbook.pdf",
        "bot_id": "bot-1",
        "file_id": "file-1",
        "page": 3,
        "author": "J. Smith",
        "producer": "Internal PDF Tool 4.2",
        "relevance_score": 0.81234567,
    },
    "chunk": "x" * 2000,
}
CRAWLED = {
    "metadata": {
        "source": "/data/bots/bot-1/crawl/7f3a.html",
        "filename": "7f3a.html",
        "source_url": "https://example.com/leave",
        "title": "Leave policy",
        "image": "/img/hero.png",
        "og": json.dumps({"title": "Leave", "url": "javascript:alert(1)", "image": "https://example.com/i.png",
                          "description": "How leave works"}),
        "payload": json.dumps({"internal_owner": "hr-team"}),
    },
    "chunk": "Staff get 25 days.",
    "og": "{'title': 'Leave'}",
    "payload": "{'internal_owner': 'hr-team'}",
}


def test_upload_citation_keeps_name_not_path():
    (c,) = public_citations([UPLOAD], show=True)
    assert c == {"metadata": {"title": "handbook.pdf"}, "chunk": c["chunk"], "relevance_score": 0.8123}
    assert len(c["chunk"]) == EXCERPT_CHARS and c["chunk"].endswith("…")
    dumped = json.dumps(c)
    for leaked in ("/data/", "bot-1", "file-1", "J. Smith", "Internal PDF Tool"):
        assert leaked not in dumped


def test_crawled_citation_keeps_public_link_and_og():
    (c,) = public_citations([CRAWLED], show=True)
    assert c["metadata"] == {"title": "Leave", "source_url": "https://example.com/leave"}
    assert c["og"] == {"title": "Leave"}  # top-level og wins; unsafe url dropped
    assert c["chunk"] == "Staff get 25 days."
    dumped = json.dumps(c)
    assert "/data/" not in dumped and "hr-team" not in dumped and "javascript:" not in dumped


def test_og_from_metadata_drops_non_http_urls():
    hit = {"metadata": {**CRAWLED["metadata"]}, "chunk": "t"}
    (c,) = public_citations([hit], show=True)
    assert c["og"] == {"title": "Leave", "description": "How leave works", "image": "https://example.com/i.png"}
    assert "image" not in c["metadata"]  # relative image path dropped


@pytest.mark.parametrize("bad", [None, "x", [None, 3, "s"]])
def test_junk_is_ignored(bad):
    assert public_citations(bad, show=True) == []


def test_hidden_citations_send_nothing():
    assert public_citations([UPLOAD, CRAWLED], show=False) == []


# ------------------------------------------------------------------ route
class _Agent:
    def __init__(self, stream):
        self.stream = stream

    def is_available(self):
        return True

    def process_message(self, **kw):
        if kw["stream"]:
            return iter([{"type": "token", "data": "hi"},
                         {"type": "citations", "data": [UPLOAD, CRAWLED], "message_id": 7}])
        return ("hi", [UPLOAD, CRAWLED])


def _chat(isolated_db, show, stream):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from models.bots import Bots
    from routes import chat
    from services.auth.widget_hmac import issue
    from utils.dependencies import get_agent_service

    cfg = {} if show is None else {"show_citations": show}
    bot = Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"c-{uuid.uuid4().hex[:6]}", name="C",
                      config_json=cfg)
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_agent_service] = lambda: _Agent(stream)
    token = issue(bot_id=bot.id, user_id="alice", session_id="s", key_version=bot.widget_key_version)
    r = TestClient(app).post(
        f"/widget/{bot.id}/chat", headers={"Authorization": f"Bearer {token}"},
        json={"user_id": "alice", "session_id": f"s-{uuid.uuid4()}", "timestamp": 1, "stream": stream,
              "messages": [{"role": "user", "content": "hello"}]},
    )
    assert r.status_code == 200
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    return r.text, [c for line in lines for c in line["citations"]]


@pytest.mark.parametrize("stream", [True, False])
def test_route_sends_only_public_fields(isolated_db, stream):
    body, cites = _chat(isolated_db, True, stream)
    assert [c["metadata"].get("title") for c in cites] == ["handbook.pdf", "Leave"]
    for leaked in ("/data/bots", "file-1", "J. Smith", "hr-team", "x" * (EXCERPT_CHARS + 1)):
        assert leaked not in body


@pytest.mark.parametrize("show", [None, False])
@pytest.mark.parametrize("stream", [True, False])
def test_route_sends_no_citations_when_hidden(isolated_db, show, stream):
    body, cites = _chat(isolated_db, show, stream)
    assert cites == [] and "Staff get" not in body
