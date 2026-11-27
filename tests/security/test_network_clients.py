"""G2e: every module that can open an outbound connection is listed here.

The README's "Outbound connections" section (and the security pack's
third-party table) is only true while the code makes no other outbound
calls. This test walks the source tree and fails when a module imports a
network client that isn't on the list below, so a new outbound path
can't land without someone reviewing it and updating the docs.

It also fails when a listed module stops importing its client, so the
list doesn't go stale, and it enforces "only agents/rag_manager.py may
import qdrant_client".
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Top-level directories that aren't application code shipped in the image.
SKIP_DIRS = {"tests", "migrations", "ui", "widget", "data", "__pycache__"}

# Modules that open network connections. Matched as the module itself or
# any submodule (``requests.adapters`` counts as ``requests``).
NETWORK_MODULES = {
    "aiohttp", "anthropic", "boto3", "botocore", "ftplib", "grpc", "http.client", "httplib2",
    "httpx", "huggingface_hub", "langchain_openai", "langsmith", "openai", "paramiko", "pycurl",
    "qdrant_client", "requests", "sentence_transformers", "smtplib", "socket", "telnetlib",
    "transformers", "urllib.request", "urllib3", "websocket", "websockets",
}

# module -> {file: what it connects to}. Keep in step with README
# "Outbound connections".
ALLOWED = {
    "langchain_openai": {
        "agents/conversation_agent.py": "the bot's model endpoint (answers, routing, details)",
        "services/enrichment.py": "the bot's model endpoint (enrichment)",
        "services/widget_email.py": "the bot's model endpoint (alert summary)",
        "config.py": "OPENAI_BASE_URL, only with EMBEDDING_PROVIDER=openai",
    },
    "qdrant_client": {
        "agents/rag_manager.py": "QDRANT_URL",
    },
    "httpx": {
        "channels/auth/_http.py": "chat platforms (shared client)",
        "channels/auth/service_account_jwt.py": "Google OAuth token endpoint",
        "channels/dispatcher.py": "chat platforms",
        "channels/telegram.py": "Telegram Bot API",
        "crm/http.py": "Zoho CRM and Salesforce",
        "services/auth/oidc.py": "the external OIDC identity provider",
    },
    "requests": {
        "services/crawl_fetch.py": "websites an admin asks to crawl",
    },
    "socket": {
        "services/crawl_fetch.py": "DNS lookups for the crawler's address checks",
    },
    "smtplib": {
        "services/email_service.py": "EMAIL_SERVER",
    },
    "sentence_transformers": {
        "config.py": "Hugging Face Hub, only when model downloads are on",
        "utils/model_pins.py": "Hugging Face Hub, only when model downloads are on",
    },
}

# Document loaders that only read local files. Others in
# langchain_community (WebBaseLoader, SitemapLoader, ...) fetch URLs.
LOCAL_LOADERS = {"BSHTMLLoader", "CSVLoader", "Docx2txtLoader", "PyMuPDFLoader", "PyPDFLoader",
                 "TextLoader"}


def _source_files():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in SKIP_DIRS or any(p.startswith(".") for p in rel.parts):
            continue
        yield rel.as_posix(), ast.parse(path.read_text(), filename=str(rel))


def _network_module(name: str):
    for mod in NETWORK_MODULES:
        if name == mod or name.startswith(mod + "."):
            return mod
    return None


def _imports(tree):
    """(module, imported names) for every import, including dynamic
    ``importlib.import_module("x")`` / ``__import__("x")`` with a literal."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ()
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = tuple(a.name for a in node.names)
            yield node.module, names
            for n in names:  # ``from urllib import request``
                yield f"{node.module}.{n}", ()
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if called in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                yield node.args[0].value, ()


def _scan():
    found = defaultdict(set)
    community = []
    for rel, tree in _source_files():
        for module, names in _imports(tree):
            net = _network_module(module)
            if net:
                found[net].add(rel)
            if module.startswith("langchain_community"):
                community.append((rel, module, names))
    return found, community


def test_no_unreviewed_network_clients():
    found, _ = _scan()
    unlisted = sorted(f"{rel} imports {mod}" for mod, files in found.items()
                      for rel in files if rel not in ALLOWED.get(mod, {}))
    assert not unlisted, (
        "New outbound network path(s). Review what they send, add them to ALLOWED here and to "
        "README 'Outbound connections':\n  " + "\n  ".join(unlisted))


def test_the_list_is_not_stale():
    found, _ = _scan()
    stale = sorted(f"{rel} no longer imports {mod}" for mod, files in ALLOWED.items()
                   for rel in files if rel not in found.get(mod, set()))
    assert not stale, "Remove from ALLOWED and the README:\n  " + "\n  ".join(stale)


def test_only_rag_manager_talks_to_qdrant():
    found, _ = _scan()
    assert found["qdrant_client"] == {"agents/rag_manager.py"}


def test_document_loaders_only_read_local_files():
    _, community = _scan()
    for rel, module, names in community:
        if module == "langchain_community.document_loaders":
            assert set(names) <= LOCAL_LOADERS, f"{rel} imports {set(names) - LOCAL_LOADERS}"
        else:
            assert module.startswith("langchain_community.document_loaders."), f"{rel}: {module}"


def test_readme_lists_every_destination():
    readme = (ROOT / "README.md").read_text()
    start = readme.index("## Outbound connections")
    section = readme[start:readme.index("\n## ", start + 1)]
    for files in ALLOWED.values():
        for rel in files:
            assert f"`{rel}`" in section, f"README 'Outbound connections' doesn't mention {rel}"


def test_the_scan_catches_each_import_form():
    """Guard the guard: each way of importing a client is seen."""
    snippets = {
        "import requests": "requests",
        "import urllib.request": "urllib.request",
        "from urllib import request": "urllib.request",
        "from http.client import HTTPSConnection": "http.client",
        "from requests.adapters import HTTPAdapter": "requests",
        "import importlib\nimportlib.import_module('httpx')": "httpx",
        "__import__('smtplib')": "smtplib",
    }
    for code, mod in snippets.items():
        seen = {_network_module(m) for m, _ in _imports(ast.parse(code))}
        assert mod in seen, code
    assert not {_network_module(m) for m, _ in _imports(ast.parse("import urllib.parse"))} - {None}
