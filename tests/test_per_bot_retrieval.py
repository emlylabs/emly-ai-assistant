"""N10: answers retrieve with the bot's own settings.

The editor's Top K, Embedding threshold and Hybrid search settings had no
effect on answers: chat used the RAG_TOP_K env var, read the threshold
from the wrong place in the config (so it was always 0.20) and took
re-ranking from the env only. The admin Retrieval page already used the
bot's settings, so it disagreed with what visitors got.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


def _bot(rag):
    from models.bots import Bots

    return Bots.insert(id=f"bot-{uuid.uuid4().hex[:8]}", slug=f"r-{uuid.uuid4().hex[:6]}", name="R",
                       config_json={"rag": rag})


def _manager(bot_id, seen):
    from agents.conversation_agent import RAGManager

    m = RAGManager.__new__(RAGManager)
    m.bot_id = bot_id
    m.logger = __import__("logging").getLogger("t")
    m.should_use_rag = lambda topic: True
    m.rag = SimpleNamespace(search=lambda **kw: seen.append(kw) or ("", []))
    return m


def test_answers_use_the_bots_settings(isolated_db):
    seen = []
    bot = _bot({"top_k": 9, "embedding_threshold": 0.55, "enable_hybrid_search": True})
    _manager(bot.id, seen).get_context("q", "t")
    assert (seen[0]["top_k"], seen[0]["embedding_threshold"], seen[0]["hybrid"]) == (9, 0.55, True)


def test_defaults_are_unchanged(isolated_db):
    seen = []
    _manager(_bot({}).id, seen).get_context("q", "t")
    assert (seen[0]["top_k"], seen[0]["embedding_threshold"], seen[0]["hybrid"]) == (5, 0.20, False)


class _Hit:
    def __init__(self, i, score):
        self.id, self.score, self.payload = i, score, {"chunk_text": f"c{i}", "source": "s"}


@pytest.fixture
def search(monkeypatch):
    from agents import rag_manager

    calls = []
    hits = [_Hit(i, 0.9 - i * 0.1) for i in range(8)]

    def query_points(**kw):
        calls.append(kw)
        return SimpleNamespace(points=hits[: kw["limit"]])

    m = rag_manager.RAGManager.__new__(rag_manager.RAGManager)
    m.client = SimpleNamespace(query_points=query_points)
    m.collection = "bots"
    m._encode = lambda q: [0.0]
    return m, calls, rag_manager


def test_top_k_and_threshold_apply(search):
    m, calls, _ = search
    _, cites = m.search(bot_id="b", query="q", top_k=6, embedding_threshold=0.55)
    assert calls[0]["limit"] == 6
    assert len(cites) == 4  # scores 0.9, 0.8, 0.7, 0.6 pass 0.55


def test_bot_hybrid_without_the_reranker_falls_back(search, monkeypatch):
    m, calls, rag_manager = search
    monkeypatch.setattr(rag_manager, "RE_RANKING_MODEL", "not-loaded")
    _, cites = m.search(bot_id="b", query="q", top_k=3, embedding_threshold=0.0, hybrid=True)
    assert calls[0]["limit"] == 3 and len(cites) == 3


def test_bot_hybrid_with_the_reranker_reranks(search, monkeypatch):
    m, calls, rag_manager = search
    monkeypatch.setattr(rag_manager, "RE_RANKING_MODEL",
                        SimpleNamespace(predict=lambda pairs: [float(i) for i in range(len(pairs))]))
    _, cites = m.search(bot_id="b", query="q", top_k=2, embedding_threshold=0.0, hybrid=True)
    assert calls[0]["limit"] == 8  # over-fetch for the re-rank
    assert [c["chunk"] for c in cites] == ["c7", "c6"]


def test_bot_without_hybrid_skips_a_loaded_reranker(search, monkeypatch):
    m, calls, rag_manager = search
    monkeypatch.setattr(rag_manager, "RE_RANKING_MODEL",
                        SimpleNamespace(predict=lambda pairs: pytest.fail("re-ranked")))
    m.search(bot_id="b", query="q", top_k=2, embedding_threshold=0.0, hybrid=False)
    assert calls[0]["limit"] == 2
