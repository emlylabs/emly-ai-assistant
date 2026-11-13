"""Pinned Hugging Face models and how to load them (D2).

The embedding model and the optional re-ranking cross-encoder are loaded
at an exact commit, both when the Docker image downloads them and when the
app loads them. ``Alibaba-NLP/gte-base-en-v1.5`` runs custom model code
from a second repo (``Alibaba-NLP/new-impl``) via ``trust_remote_code``;
that code is pinned too, so a change upstream can't run inside the app.

When ``HF_HUB_OFFLINE`` is set (the Docker image sets it), models are
loaded from the local cache only and nothing is fetched at runtime.

Run ``python -m utils.model_pins`` to download the pinned models into the
cache folders (the Dockerfile does this at build time).

This module must stay free of project imports: the Dockerfile copies it in
on its own, before the rest of the source, so the model layer is cached.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

DEFAULT_EMBEDDING_MODEL = "Alibaba-NLP/gte-base-en-v1.5"
DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-TinyBERT-L2-v2"

# Commit hashes. To update: pick the new commit on huggingface.co (the
# model's "Files and versions" tab), change it here and rebuild the image.
PINNED_REVISIONS: Dict[str, str] = {
    DEFAULT_EMBEDDING_MODEL: "a829fd0e060bb84554da0dfd354d0de0f7712b7f",
    DEFAULT_RERANK_MODEL: "81d1926f67cb8eee2c2be17ca9f793c7c3bd20cc",
}
# Revision of the remote code a pinned model runs (repo ``Alibaba-NLP/new-impl``).
PINNED_CODE_REVISIONS: Dict[str, str] = {
    DEFAULT_EMBEDDING_MODEL: "40ced75c3017eb27626c9d4ea981bde21a2662f4",
}

# Old repo names that Hugging Face now redirects; redirects don't work offline.
_RENAMED = {"cross-encoder/ms-marco-TinyBERT-L-2-v2": DEFAULT_RERANK_MODEL}

_TRUE = {"1", "true", "yes", "on"}


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name, "").strip()
    return value or None


def hf_offline() -> bool:
    return (os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in _TRUE
            or os.environ.get("TRANSFORMERS_OFFLINE", "").strip().lower() in _TRUE)


def embedding_model_name() -> str:
    return _env("RAG_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL


def rerank_model_name() -> str:
    name = _env("RE_RANKING_MODEL")
    if not name:
        return DEFAULT_RERANK_MODEL
    # Historically RE_RANKING_MODEL was the bare name under cross-encoder/.
    name = name if "/" in name else f"cross-encoder/{name}"
    return _RENAMED.get(name, name)


def embedding_cache_dir() -> str:
    return _env("SENTENCE_TRANSFORMERS_HOME") or "./data/models/embedding"


def rerank_cache_dir() -> str:
    return _env("RE_RANKING_MODEL_HOME") or "./data/models/re_ranking"


def load_kwargs(model_name: str, *, revision_env: str, trust_env: Optional[str] = None) -> Dict[str, Any]:
    """Keyword arguments for ``SentenceTransformer`` / ``CrossEncoder``.

    Pinned models get their commit (and remote-code commit). Any other
    model uses ``revision_env`` if set, and runs remote code only when
    ``trust_env`` is explicitly true, because its code isn't pinned.
    """
    kwargs: Dict[str, Any] = {"local_files_only": hf_offline()}
    revision = PINNED_REVISIONS.get(model_name) or _env(revision_env)
    if revision:
        kwargs["revision"] = revision
    code_revision = PINNED_CODE_REVISIONS.get(model_name)
    if code_revision:
        kwargs["trust_remote_code"] = True
        kwargs["config_kwargs"] = {"code_revision": code_revision}
        kwargs["model_kwargs"] = {"code_revision": code_revision}
        # The tokenizer load reads the config again, through its own kwargs.
        kwargs["processor_kwargs"] = {"code_revision": code_revision}
    elif trust_env and (_env(trust_env) or "").lower() in _TRUE:
        kwargs["trust_remote_code"] = True
    return kwargs


def embedding_load_kwargs() -> Dict[str, Any]:
    return load_kwargs(embedding_model_name(), revision_env="RAG_EMBEDDING_MODEL_REVISION",
                       trust_env="RAG_EMBEDDING_TRUST_REMOTE_CODE")


def rerank_load_kwargs() -> Dict[str, Any]:
    return load_kwargs(rerank_model_name(), revision_env="RE_RANKING_MODEL_REVISION")


def missing_model_error(kind: str, model_name: str, cache_dir: str, exc: BaseException) -> RuntimeError:
    return RuntimeError(
        f"Could not load the {kind} model {model_name} from {cache_dir} ({exc.__class__.__name__}). "
        "Hugging Face downloads are off (HF_HUB_OFFLINE), so the model must already be in that "
        "folder. The Docker image ships the pinned models under /opt/models; if your .env sets "
        "SENTENCE_TRANSFORMERS_HOME or RE_RANKING_MODEL_HOME to a path under /app/data, remove it."
    )


def download_all() -> None:
    """Fetch the configured models into their cache folders (build time)."""
    from sentence_transformers import CrossEncoder, SentenceTransformer

    SentenceTransformer(embedding_model_name(), cache_folder=embedding_cache_dir(), device="cpu",
                        **embedding_load_kwargs())
    CrossEncoder(rerank_model_name(), cache_folder=rerank_cache_dir(), device="cpu", max_length=512,
                 **rerank_load_kwargs())


if __name__ == "__main__":
    download_all()
