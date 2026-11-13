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
    loads = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        assert getattr(node.func, "id", "") not in ("SentenceTransformer", "CrossEncoder"), \
            "load models with model_pins.load_pinned (pin check + buffer repair)"
        if getattr(node.func, "attr", "") == "load_pinned":
            loads.append(node.args[0].id)
            names = {k.arg for k in node.keywords}
            assert "trust_remote_code" not in names and "local_files_only" not in names
            assert any(k.arg is None for k in node.keywords), "expected **model_pins.*_load_kwargs()"
    assert sorted(loads) == ["CrossEncoder", "SentenceTransformer"]


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
    allowed = {"__future__", "inspect", "os", "typing", "sentence_transformers", "torch"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [(node.module or "").split(".")[0]]
        else:
            continue
        assert set(mods) <= allowed, mods


# ---------------------------------------------------------------- F5
# transformers 5 / sentence-transformers 5.6 broke the pinned remote code in
# two ways; these guard the workarounds in model_pins.


def test_code_pin_survives_sentence_transformers_pop():
    """sentence-transformers 5.6+ pops code_revision from model_kwargs
    before loading the model, which made it fetch modeling.py from main."""
    kw = model_pins.embedding_load_kwargs()["model_kwargs"]
    code = model_pins.PINNED_CODE_REVISIONS[model_pins.DEFAULT_EMBEDDING_MODEL]
    assert kw.pop("code_revision", None) == code
    assert kw["code_revision"] == code
    assert kw.pop("other", "x") == "x"


def _module_from(module_path: str):
    import torch

    cls = type("NewModel", (torch.nn.Module,), {"__module__": module_path})
    return cls()


@pytest.mark.parametrize("module_path, ok", [
    ("transformers_modules.Alibaba_hyphen_NLP.new_hyphen_impl.40ced75c3017eb27626c9d4ea981bde21a2662f4.modeling", True),
    ("transformers_modules.Alibaba_hyphen_NLP.new_hyphen_impl.e244c9398934e244c9398934e244c9398934e244.modeling", False),
    ("transformers.models.bert.modeling_bert", False),  # pinned remote code missing entirely
])
def test_verify_pinned_code(module_path, ok):
    import torch

    model = torch.nn.Sequential(_module_from(module_path))
    if ok:
        model_pins.verify_pinned_code(model, model_pins.DEFAULT_EMBEDDING_MODEL)
    else:
        with pytest.raises(RuntimeError, match="pinned commit"):
            model_pins.verify_pinned_code(model, model_pins.DEFAULT_EMBEDDING_MODEL)


def test_verify_pinned_code_ignores_models_without_code_pin():
    import torch

    model_pins.verify_pinned_code(torch.nn.Sequential(_module_from("transformers.models.bert.x")),
                                  model_pins.DEFAULT_RERANK_MODEL)


def test_rebuild_init_buffers_restores_what_init_computes():
    import torch

    class Rotary(torch.nn.Module):  # same shape as the pinned remote code
        def __init__(self, dim, max_position_embeddings=16, base=10000.0, device=None):
            super().__init__()
            self.dim, self.max_position_embeddings, self.base = dim, max_position_embeddings, base
            self.register_buffer("inv_freq", 1.0 / (base ** (torch.arange(0, dim, 2).float() / dim)),
                                 persistent=False)
            self._set_cos_sin_cache(max_position_embeddings, None, torch.float32)

        def _set_cos_sin_cache(self, seq_len, device, dtype):
            t = torch.arange(seq_len, dtype=torch.float32)
            emb = torch.cat([torch.einsum("i,j->ij", t, self.inv_freq)] * 2, dim=-1)
            self.register_buffer("cos_cached", emb.cos().to(dtype), persistent=False)

    class Embeddings(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.rotary_emb = Rotary(8)
            self.register_buffer("position_ids", torch.arange(16), persistent=False)

    good, broken = Embeddings(), Embeddings()
    # What transformers 5 leaves behind: uninitialised memory.
    broken.position_ids.fill_(4340459392)
    broken.rotary_emb.inv_freq.zero_()
    broken.rotary_emb.cos_cached.zero_()

    assert model_pins.rebuild_init_buffers(broken) == 2
    for name, buf in good.named_buffers():
        assert torch.equal(buf, dict(broken.named_buffers())[name]), name


def test_pinned_embedding_model_reproduces_stored_vectors():
    """Vectors already in Qdrant were made with transformers 4; the loaded
    model must still produce them, or every bot's knowledge base silently
    stops matching. Uses the locally cached pinned model."""
    import json
    import math

    from sentence_transformers import SentenceTransformer

    ref = json.loads((REPO_ROOT / "tests" / "security" / "fixtures" / "gte_reference.json").read_text())
    model = model_pins.load_pinned(SentenceTransformer, ref["model"], cache_folder=model_pins.embedding_cache_dir(),
                                   device="cpu", **model_pins.embedding_load_kwargs())
    for text, expected in ref["vectors"].items():
        got = model.encode(text, normalize_embeddings=True).tolist()
        cosine = sum(a * b for a, b in zip(got, expected)) / math.sqrt(sum(a * a for a in expected))
        assert cosine > 0.9999, (text, cosine)
