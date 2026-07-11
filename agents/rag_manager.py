"""Centralized Qdrant access for the multi-bot runtime.

Every Qdrant call in this codebase MUST go through ``RAGManager``. The
``bot_id`` payload filter is applied here on every read, write, and delete.
A forgotten filter elsewhere would leak vectors across tenants — treat this
module as a security boundary and do not import ``qdrant_client`` directly
anywhere else.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from config import (
    EMBEDDING_MODEL_INSTANCE,
    EMBEDDING_MODEL_NAME,
    ENABLE_RAG_HYBRID_SEARCH,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_DATA_PATH,
    QDRANT_URL,
    RE_RANKING_MODEL,
    TOP_K,
)

log = logging.getLogger(__name__)


def _build_client() -> QdrantClient:
    if QDRANT_URL:
        log.info("Connecting to Qdrant server at %s", QDRANT_URL)
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    log.info("Using embedded Qdrant at %s", QDRANT_DATA_PATH)
    return QdrantClient(path=QDRANT_DATA_PATH)


def _embed_dim() -> int:
    sample = EMBEDDING_MODEL_INSTANCE.encode("dimension probe")
    if isinstance(sample, np.ndarray):
        return int(sample.shape[-1])
    return len(sample)


class RAGManager:
    """Process-shared wrapper around the single Qdrant ``bots`` collection."""

    def __init__(self) -> None:
        self.client = _build_client()
        self.collection = QDRANT_COLLECTION
        self._init_lock = threading.Lock()
        self._ensure_collection()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------
    def _ensure_collection(self) -> None:
        with self._init_lock:
            existing = {c.name for c in self.client.get_collections().collections}
            if self.collection not in existing:
                dim = _embed_dim()
                log.info(
                    "Creating Qdrant collection %s (dim=%d, model=%s)",
                    self.collection,
                    dim,
                    EMBEDDING_MODEL_NAME,
                )
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
                )
            self._ensure_payload_index("bot_id", qm.PayloadSchemaType.KEYWORD)
            self._ensure_payload_index("file_id", qm.PayloadSchemaType.KEYWORD)

    def _ensure_payload_index(self, field: str, schema: qm.PayloadSchemaType) -> None:
        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=schema,
            )
        except Exception as e:
            # Index already exists is the common case here; log at debug.
            log.debug("Payload index %s already present or create failed: %s", field, e)

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def _encode(self, text: str) -> List[float]:
        vec = EMBEDDING_MODEL_INSTANCE.encode(text, normalize_embeddings=True)
        if isinstance(vec, np.ndarray):
            return vec.tolist()
        return list(vec)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def upsert(
        self,
        bot_id: str,
        file_id: str,
        chunks: List[Dict[str, Any]],
    ) -> int:
        """Embed and upsert chunks for a single file.

        ``chunks`` is a list of ``{"text": str, "metadata": dict}`` items.
        Every point's payload carries ``bot_id`` and ``file_id`` (plus the
        per-chunk metadata) so deletes and tenant filters are unambiguous.
        Returns the number of points written.
        """
        if not chunks:
            return 0
        points: List[qm.PointStruct] = []
        for chunk in chunks:
            text = chunk["text"]
            metadata = dict(chunk.get("metadata") or {})
            metadata["bot_id"] = bot_id
            metadata["file_id"] = file_id
            metadata["chunk_text"] = text
            points.append(
                qm.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=self._encode(text),
                    payload=metadata,
                )
            )
        self.client.upsert(collection_name=self.collection, points=points, wait=True)
        log.info("Upserted %d points for bot=%s file=%s", len(points), bot_id, file_id)
        return len(points)

    # ------------------------------------------------------------------
    # Deletes
    # ------------------------------------------------------------------
    def delete_file(self, bot_id: str, file_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(key="bot_id", match=qm.MatchValue(value=bot_id)),
                        qm.FieldCondition(key="file_id", match=qm.MatchValue(value=file_id)),
                    ]
                )
            ),
            wait=True,
        )
        log.info("Deleted points for bot=%s file=%s", bot_id, file_id)

    def delete_bot(self, bot_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[qm.FieldCondition(key="bot_id", match=qm.MatchValue(value=bot_id))]
                )
            ),
            wait=True,
        )
        log.info("Deleted all points for bot=%s", bot_id)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def search(
        self,
        bot_id: str,
        query: str,
        top_k: int = TOP_K,
        embedding_threshold: float = 0.0,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Filtered semantic search scoped to ``bot_id``.

        Returns ``(joined_context, citations)`` where ``citations`` is a
        list of ``{"metadata": dict, "chunk": str}`` mirroring the shape
        the agent code consumes.
        """
        try:
            hits = self.client.query_points(
                collection_name=self.collection,
                query=self._encode(query),
                query_filter=qm.Filter(
                    must=[qm.FieldCondition(key="bot_id", match=qm.MatchValue(value=bot_id))]
                ),
                limit=max(top_k * 4, top_k) if ENABLE_RAG_HYBRID_SEARCH else top_k,
                with_payload=True,
            ).points
        except Exception:
            log.exception("Qdrant search failed for bot=%s", bot_id)
            return "", []

        if not hits:
            return "", []

        # Optional cross-encoder re-rank (hybrid mode).
        if ENABLE_RAG_HYBRID_SEARCH and RE_RANKING_MODEL is not None and not isinstance(RE_RANKING_MODEL, str):
            pairs = [(query, (h.payload or {}).get("chunk_text", "")) for h in hits]
            try:
                scores = RE_RANKING_MODEL.predict(pairs)
                ranked = sorted(
                    zip(hits, scores), key=lambda item: float(item[1]), reverse=True
                )[:top_k]
                hits = [h for h, _ in ranked]
            except Exception:
                log.exception("Cross-encoder re-rank failed; falling back to vector order")
                hits = hits[:top_k]
        else:
            hits = hits[:top_k]

        filtered: List[Dict[str, Any]] = []
        for h in hits:
            score = float(h.score or 0.0)
            if score < embedding_threshold:
                continue
            payload = dict(h.payload or {})
            chunk = payload.pop("chunk_text", "")
            payload["relevance_score"] = score
            filtered.append({"metadata": payload, "chunk": chunk})

        if not filtered:
            return "", []

        joined = "\n---\n".join(item["chunk"] for item in filtered if item["chunk"])
        return joined, filtered

    # ------------------------------------------------------------------
    # Introspection (admin UX)
    # ------------------------------------------------------------------
    def count_for_bot(self, bot_id: str) -> int:
        result = self.client.count(
            collection_name=self.collection,
            count_filter=qm.Filter(
                must=[qm.FieldCondition(key="bot_id", match=qm.MatchValue(value=bot_id))]
            ),
            exact=True,
        )
        return int(result.count)


# ---------------------------------------------------------------------------
# Process-wide singleton.
# Constructed lazily on first access so import-time side effects stay minimal.
# ---------------------------------------------------------------------------
_RAG_MANAGER: Optional[RAGManager] = None
_RAG_MANAGER_LOCK = threading.Lock()


def get_rag_manager() -> RAGManager:
    global _RAG_MANAGER
    if _RAG_MANAGER is None:
        with _RAG_MANAGER_LOCK:
            if _RAG_MANAGER is None:
                _RAG_MANAGER = RAGManager()
    return _RAG_MANAGER
