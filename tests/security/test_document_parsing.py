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


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF with ``text`` in Helvetica, xref offsets computed."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def test_pdf_upload_extracts_text(extract, tmp_path):
    assert "Opening hours are nine to five" in extract(tmp_path, "hours.pdf",
                                                       _minimal_pdf("Opening hours are nine to five"))


# Alert emails render assistant replies and the LLM summary as markdown, and
# a visitor can steer both. Script links must never survive (mistune
# advisories GHSA-8c25-4j27-2rv3 / GHSA-qfrw-5rxm-mhh2, fixed in 3.3.0).
HOSTILE_LINKS = [
    "[a](javascript:alert(1))",
    "[b](%6Aavascript:alert(1))",
    "[c](java%0Ascript:alert(1))",
    "[d](JaVaScRiPt:alert(1))",
    "[e](data:text/html;base64,PHNjcmlwdD4=)",
]


@pytest.mark.parametrize("link", HOSTILE_LINKS)
def test_email_markdown_drops_script_links(link):
    from services import widget_email

    html = widget_email._normalize_summary_to_html(f"Summary: see {link}").lower()
    assert "javascript" not in html.replace("%6a", "j").replace("%0a", "")
    assert 'href="data:' not in html


def test_email_markdown_keeps_normal_links_and_escapes_html():
    from services import widget_email

    html = widget_email._normalize_summary_to_html("Read [the docs](https://docs.example/x) <script>x</script>")
    assert '<a href="https://docs.example/x">the docs</a>' in html
    assert "<script>" not in html
