"""G12a: citations from a PDF name the page the passage came from.

The PDF loaders already store ``page`` (0-based) and ``page_label`` on
every chunk, and Qdrant returns them with each hit. They were dropped
when citations were stored, sent to the widget and written as channel
footnotes, so a PDF citation never said which page.
"""
from __future__ import annotations

import json

import pytest


def _two_page_pdf(path):
    """A minimal valid PDF with one line of text on each of two pages."""
    texts = ["Holiday policy lives on page one.", "Parental leave is twenty six weeks."]
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>"]
    font = 7
    for i in range(2):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
                    f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {5 + i} 0 R >>")
    for t in texts:
        stream = f"BT /F1 12 Tf 20 100 Td ({t}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


@pytest.fixture
def ingest(monkeypatch):
    """Run the real ingest pipeline and return each chunk as RAG search
    would hand it back: ``{"metadata": <payload>, "chunk": <text>}``."""
    from services import data_service

    stored = []

    class _Rag:
        def upsert(self, bot_id, file_id, chunks):
            for c in chunks:
                stored.append({"metadata": {**c["metadata"], "bot_id": bot_id, "file_id": file_id,
                                            "relevance_score": 0.8}, "chunk": c["text"]})
            return len(chunks)

    monkeypatch.setattr(data_service, "get_rag_manager", lambda: _Rag())

    def run(path):
        stored.clear()
        data_service.DataService().process_file(str(path), bot_id="bot-p", file_id="f-1")
        return list(stored)

    return run


def _from_page_two(hits):
    (hit,) = [h for h in hits if "Parental leave" in h["chunk"]]
    return hit


def test_a_pdf_citation_names_its_page_everywhere(tmp_path, ingest):
    from agents.conversation_agent import _serialize_citations
    from channels.dispatcher import _citation_label
    from services.public_citations import public_citations

    pdf = tmp_path / "handbook.pdf"
    _two_page_pdf(pdf)
    hit = _from_page_two(ingest(pdf))

    (shown,) = public_citations([hit], show=True)
    assert shown["metadata"]["page"] == "2"
    assert shown["metadata"]["title"] == "handbook.pdf"

    (stored,) = json.loads(_serialize_citations([hit]))
    assert stored["page"] == "2"

    title, _ = _citation_label(hit)
    assert title == "handbook.pdf, p. 2"


def test_page_one_is_numbered_from_one(tmp_path, ingest):
    from services.public_citations import public_citations

    pdf = tmp_path / "h.pdf"
    _two_page_pdf(pdf)
    (hit,) = [h for h in ingest(pdf) if "Holiday" in h["chunk"]]
    assert public_citations([hit], show=True)[0]["metadata"]["page"] == "1"


def test_files_without_pages_have_no_page(tmp_path, ingest):
    from agents.conversation_agent import _serialize_citations
    from channels.dispatcher import _citation_label
    from services.public_citations import public_citations

    txt = tmp_path / "notes.txt"
    txt.write_text("Parental leave is twenty six weeks.")
    (hit,) = ingest(txt)

    assert "page" not in public_citations([hit], show=True)[0]["metadata"]
    assert json.loads(_serialize_citations([hit]))[0]["page"] is None
    assert _citation_label(hit)[0] == "notes.txt"


@pytest.mark.parametrize("meta,expected", [
    ({"page_label": "iv", "page": 3}, "iv"),       # the printed label wins
    ({"page": 0}, "1"),                             # 0-based index, no label
    ({"page_label": "  ", "page": 4}, "5"),
    ({"page": True}, None),                         # not a page number
    ({"page": -1}, None),
    ({"page_label": "x" * 40}, None),               # not a plausible label
    ({}, None),
])
def test_citation_page(meta, expected):
    from services.public_citations import citation_page

    assert citation_page(meta) == expected
