"""F3: the document and markup parsers still work after the dependency
changes (``unstructured`` dropped, so ``beautifulsoup4`` and ``lxml`` are
direct dependencies now)."""

from __future__ import annotations

import pytest


@pytest.fixture
def extract():
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    from services.data_service import DataService

    service = object.__new__(DataService)  # skip the RAG manager; only extraction is under test
    service.text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=0)

    def run(tmp_path, name: str, content: bytes) -> str:
        path = tmp_path / name
        path.write_bytes(content)
        docs = service._extract_text_from_file(str(path), path.suffix)
        return " ".join(d.page_content for d in docs)

    return run


def test_html_upload_uses_lxml_and_extracts_text(extract, tmp_path):
    from langchain_community.document_loaders import BSHTMLLoader

    page = b"<html><head><title>Refunds</title><script>var secret = 1;</script></head>" \
           b"<body><h1>Refund policy</h1><p>Refunds within 30 days.</p></body></html>"
    text = extract(tmp_path, "policy.html", page)
    assert "Refund policy" in text and "Refunds within 30 days." in text
    # BSHTMLLoader's default parser is lxml: it must be installed, not
    # silently replaced.
    assert BSHTMLLoader(str(tmp_path / "policy.html")).bs_kwargs == {"features": "lxml"}


def test_crawler_link_and_text_extraction():
    from services import crawl_worker

    html = '<html><body><a href="/a">A</a><a href="https://other.example/b">B</a>' \
           '<a href="javascript:alert(1)">x</a><p>Hello there</p></body></html>'
    links = crawl_worker._extract_links(html, "https://site.example/start")
    assert "https://site.example/a" in links
    assert not any(link.startswith("javascript:") for link in links)
    assert crawl_worker._extract_text_length(html) > 0
