"""Phase 8 backend-backfill — async sentiment & intent enrichment.

A background daemon thread reads a small in-process queue and runs a
cheap LLM classification on each user-side message: sentiment label
plus top intent. Results land on the corresponding ``emly_session``
row so the admin Conversations meta panel can stop showing
"Enrichment pending".

Design constraints (per backend-backfill.md):

- **Inline-bypass:** runs after persistence, off the chat hot path.
  Slow classifier latency must never inflate end-user-visible response
  time.
- **Opt-in per bot:** `bots.config_json["enrichment_enabled"]` defaults
  to false. When off, `enqueue()` is a no-op.
- **Cost-bounded:** the queue caps at 1000 pending items; oldest are
  dropped on overflow. Every minute we summarise drop counts for ops.
- **Single-replica only:** an in-process Queue isn't shared across
  workers. The plan's S3+Redis future-items track replaces this with
  a real broker (Celery/Redis) when multi-replica lands.
- **Best-effort SLO:** enrichment lands within ~60 seconds of the user
  message; readers should treat absence as "pending".

The worker doesn't talk to the agent reply path — the chat thread
calls ``enqueue()`` after `EMLYMessages.insert_new_message`, but the
classifier here uses a separately-constructed LLM client to keep
config/model decisions per-bot rather than per-call.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_QUEUE_MAX = 1000
_QUEUE: "queue.Queue[_EnrichmentJob]" = queue.Queue(maxsize=_QUEUE_MAX)
_DROP_COUNTER = 0
_DROP_LOCK = threading.Lock()
_WORKER_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()


@dataclass
class _EnrichmentJob:
    bot_id: str
    session_id: str
    message_id: int
    user_text: str


# ---------------------------------------------------------------------------
# Public API — called from the agent reply path / tests.
# ---------------------------------------------------------------------------
def is_enabled(bot_id: str) -> bool:
    try:
        from models.bots import Bots

        bot = Bots.get_by_id(bot_id)
        if bot is None:
            return False
        cfg = dict(bot.config_json or {})
        return bool(cfg.get("enrichment_enabled", False))
    except Exception:
        return False


def enqueue(*, bot_id: str, session_id: str, message_id: int, user_text: str) -> bool:
    """Queue a classification job. Returns True if accepted, False on overflow
    or if the bot has the feature off. Never raises — failures degrade to a
    silently empty session enrichment row."""
    global _DROP_COUNTER
    if not user_text or not session_id or not bot_id:
        return False
    if not is_enabled(bot_id):
        return False
    job = _EnrichmentJob(
        bot_id=bot_id,
        session_id=session_id,
        message_id=message_id,
        user_text=user_text,
    )
    try:
        _QUEUE.put_nowait(job)
        return True
    except queue.Full:
        with _DROP_LOCK:
            _DROP_COUNTER += 1
        return False


def queue_depth() -> int:
    return _QUEUE.qsize()


def drop_count() -> int:
    return _DROP_COUNTER


# ---------------------------------------------------------------------------
# Classifier prompt — single-call sentiment + top intent.
# ---------------------------------------------------------------------------
_CLASSIFIER_TEMPLATE = """You classify a single end-user chat message for a customer-support bot.

Return ONLY valid JSON, no prose, no markdown fence. Keys:

  "sentiment_score":  number between -1.0 (very negative) and +1.0 (very positive)
  "sentiment_label":  one of "negative", "neutral", "positive"
  "intent":           short snake_case label naming the user's apparent goal
                      (e.g. "refund_status", "order_lookup", "password_reset",
                      "shipping_eta", "general_question").
  "intent_confidence": number between 0.0 and 1.0

User message:
\"\"\"
{user_text}
\"\"\"
"""


_LLM_CACHE: Dict[str, Any] = {}


def _build_llm_for_bot(bot_id: str):
    """Construct (and cache) a ChatOpenAI client for the bot's config.

    We don't reuse the agent's per-bot runtime because that's keyed by
    `(bot, user, session)` and may TTL out under conversation load. The
    enrichment worker holds its own simple cache keyed by bot_id.
    """
    if bot_id in _LLM_CACHE:
        return _LLM_CACHE[bot_id]
    try:
        from langchain_openai import ChatOpenAI

        from config import OPENAI_API_KEY, OPENAI_BASE_URL
        from models.bots import Bots
        from services.bot_config import get_decrypted_api_key

        bot = Bots.get_by_id(bot_id)
        if bot is None:
            return None
        cfg = dict(bot.config_json or {})
        # Allow opting into a cheaper enrichment model than the chat
        # model — defaults to gpt-4o-mini which is suitable for short
        # classification tasks.
        model = cfg.get("enrichment_model") or cfg.get("model_name") or "gpt-4o-mini"
        api_key = get_decrypted_api_key(bot_id) or OPENAI_API_KEY
        kwargs: Dict[str, Any] = {"model": model, "temperature": 0.0, "timeout": 20, "max_retries": 1}
        if api_key:
            kwargs["api_key"] = api_key
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        llm = ChatOpenAI(**kwargs)
        _LLM_CACHE[bot_id] = llm
        return llm
    except Exception:
        log.debug("enrichment: failed to construct LLM for bot=%s", bot_id, exc_info=True)
        return None


def _classify_one(job: _EnrichmentJob) -> Optional[Dict[str, Any]]:
    """Run the LLM classifier for a single job. Returns the parsed result
    dict on success, None on any failure."""
    llm = _build_llm_for_bot(job.bot_id)
    if llm is None:
        return None

    prompt = _CLASSIFIER_TEMPLATE.format(user_text=job.user_text[:2000])
    try:
        result = llm.invoke(prompt)
        content = getattr(result, "content", None) or str(result)
    except Exception:
        log.debug("enrichment: classifier invoke failed", exc_info=True)
        return None

    parsed = _parse_classifier_json(content)
    if parsed is None:
        log.debug("enrichment: classifier returned non-JSON output: %s", content[:200])
    return parsed


def _parse_classifier_json(content: str) -> Optional[Dict[str, Any]]:
    """Tolerant JSON parser — strips ``` fences and trailing prose."""
    if not content:
        return None
    text = content.strip()
    # Some models wrap output in ```json fences despite our instruction.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    # Trim to the first { ... } block.
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    blob = text[start : end + 1]
    try:
        return json.loads(blob)
    except Exception:
        return None


def _apply_result(job: _EnrichmentJob, result: Dict[str, Any]) -> None:
    """Persist classifier output to the session row."""
    try:
        from models.emly_sessions import EMLYSessions

        sentiment_score = result.get("sentiment_score")
        sentiment_label = result.get("sentiment_label")
        intent = result.get("intent")
        intent_confidence = result.get("intent_confidence")
        # Coerce types defensively.
        try:
            sentiment_score = float(sentiment_score) if sentiment_score is not None else None
        except (TypeError, ValueError):
            sentiment_score = None
        try:
            intent_confidence = float(intent_confidence) if intent_confidence is not None else None
        except (TypeError, ValueError):
            intent_confidence = None
        EMLYSessions.set_enrichment(
            job.bot_id,
            job.session_id,
            sentiment_score=sentiment_score,
            sentiment_label=str(sentiment_label) if sentiment_label else None,
            intent=str(intent) if intent else None,
            intent_confidence=intent_confidence,
        )
    except Exception:
        log.exception("enrichment: persist failed for session=%s", job.session_id)


# ---------------------------------------------------------------------------
# Worker loop & lifecycle.
# ---------------------------------------------------------------------------
def _worker_loop() -> None:
    log.info("enrichment worker started")
    last_drop_log = 0.0
    while not _STOP.is_set():
        try:
            job = _QUEUE.get(timeout=1.0)
        except queue.Empty:
            now = time.time()
            if _DROP_COUNTER and (now - last_drop_log) > 60:
                log.warning("enrichment: %d jobs dropped (queue full)", _DROP_COUNTER)
                last_drop_log = now
            continue
        try:
            result = _classify_one(job)
            if result:
                _apply_result(job, result)
        except Exception:
            log.exception("enrichment: unhandled error processing job")
        finally:
            _QUEUE.task_done()
    log.info("enrichment worker stopped")


def start_worker() -> None:
    """Idempotent: starts the daemon thread on the first call. Safe to call
    from `main.py` startup."""
    global _WORKER_THREAD
    if os.environ.get("ENRICHMENT_DISABLED") == "1":
        log.info("enrichment worker disabled via ENRICHMENT_DISABLED=1")
        return
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return
    _STOP.clear()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop,
        name="enrichment-worker",
        daemon=True,
    )
    _WORKER_THREAD.start()


def stop_worker(timeout: float = 2.0) -> None:
    """Best-effort shutdown — used by tests and Uvicorn lifespan handlers."""
    _STOP.set()
    if _WORKER_THREAD is not None:
        _WORKER_THREAD.join(timeout=timeout)
