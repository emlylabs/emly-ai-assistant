"""D2: Hugging Face models are pinned and loaded offline in the image."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from utils import model_pins

REPO_ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"^[0-9a-f]{40}$")
ENVS = (
    "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "RAG_EMBEDDING_MODEL", "RAG_EMBEDDING_MODEL_REVISION",
    "RAG_EMBEDDING_TRUST_REMOTE_CODE", "RE_RANKING_MODEL", "RE_RANKING_MODEL_REVISION",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)


def test_pins_are_full_commit_hashes():
    for pins in (model_pins.PINNED_REVISIONS, model_pins.PINNED_CODE_REVISIONS):
        for model, sha in pins.items():
            assert SHA.match(sha), model
    assert model_pins.DEFAULT_EMBEDDING_MODEL in model_pins.PINNED_REVISIONS
    assert model_pins.DEFAULT_RERANK_MODEL in model_pins.PINNED_REVISIONS


def test_default_embedding_pins_model_and_remote_code():
    kw = model_pins.embedding_load_kwargs()
    code = model_pins.PINNED_CODE_REVISIONS[model_pins.DEFAULT_EMBEDDING_MODEL]
    assert kw["revision"] == model_pins.PINNED_REVISIONS[model_pins.DEFAULT_EMBEDDING_MODEL]
    assert kw["trust_remote_code"] is True
    # The config is read by the model, the config loader and the tokenizer
    # loader; each needs the code pin or it fetches the latest code.
    for key in ("config_kwargs", "model_kwargs", "processor_kwargs"):
        assert kw[key] == {"code_revision": code}, key
    assert kw["local_files_only"] is False


@pytest.mark.parametrize("var", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
def test_offline_env_means_local_files_only(monkeypatch, var):
    monkeypatch.setenv(var, "1")
    assert model_pins.embedding_load_kwargs()["local_files_only"] is True
    assert model_pins.rerank_load_kwargs()["local_files_only"] is True


def test_other_embedding_model_runs_no_remote_code_by_default(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "acme/embedder")
    kw = model_pins.embedding_load_kwargs()
    assert "trust_remote_code" not in kw and "revision" not in kw

    monkeypatch.setenv("RAG_EMBEDDING_MODEL_REVISION", "abc123")
    monkeypatch.setenv("RAG_EMBEDDING_TRUST_REMOTE_CODE", "true")
    kw = model_pins.embedding_load_kwargs()
    assert kw["revision"] == "abc123" and kw["trust_remote_code"] is True


@pytest.mark.parametrize("value, expected", [
    (None, model_pins.DEFAULT_RERANK_MODEL),
    ("ms-marco-TinyBERT-L-2-v2", model_pins.DEFAULT_RERANK_MODEL),  # old name, now redirected upstream
    ("ms-marco-MiniLM-L6-v2", "cross-encoder/ms-marco-MiniLM-L6-v2"),
    ("acme/reranker", "acme/reranker"),
])
def test_rerank_model_name(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("RE_RANKING_MODEL", value)
    assert model_pins.rerank_model_name() == expected


def test_default_reranker_is_pinned():
    kw = model_pins.rerank_load_kwargs()
    assert kw["revision"] == model_pins.PINNED_REVISIONS[model_pins.DEFAULT_RERANK_MODEL]
    assert "trust_remote_code" not in kw


def test_config_loads_models_through_pins():
    """config.py must not hard-code remote-code or online loading again."""
    source = (REPO_ROOT / "config.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("SentenceTransformer", "CrossEncoder"):
            names = {k.arg for k in node.keywords}
            assert "trust_remote_code" not in names and "local_files_only" not in names
            assert any(k.arg is None for k in node.keywords), "expected **model_pins.*_load_kwargs()"


def test_dockerfile_bakes_models_outside_data_and_goes_offline():
    text = (REPO_ROOT / "Dockerfile").read_text()
    runtime = text[text.index("AS runtime"):]
    download = runtime.index("RUN python -m utils.model_pins")
    offline = runtime.index("HF_HUB_OFFLINE=1")
    assert download < offline, "downloads must finish before offline mode is switched on"
    assert "TRANSFORMERS_OFFLINE=1" in runtime
    assert re.search(r"SENTENCE_TRANSFORMERS_HOME=/opt/models/", runtime)
    assert re.search(r"RE_RANKING_MODEL_HOME=/opt/models/", runtime)
    assert "/app/data/models" not in runtime
    assert "trust_remote_code=True" not in runtime


def test_env_sample_does_not_point_model_cache_into_data_volume():
    for line in (REPO_ROOT / ".env.sample").read_text().splitlines():
        if line.startswith(("SENTENCE_TRANSFORMERS_HOME=", "RE_RANKING_MODEL_HOME=")):
            assert "/app/data" not in line, line


def test_model_pins_has_no_project_imports():
    """The Dockerfile copies utils/model_pins.py in on its own."""
    tree = ast.parse((REPO_ROOT / "utils" / "model_pins.py").read_text())
    allowed = {"__future__", "os", "typing", "sentence_transformers"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [(node.module or "").split(".")[0]]
        else:
            continue
        assert set(mods) <= allowed, mods
