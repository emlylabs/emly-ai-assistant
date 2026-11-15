"""Sentiment & intent enrichment, once per conversation (Q-G1).

A background daemon thread classifies each **finished conversation** of a
bot that has *Analyse conversations* (``enrichment_enabled``) on:
sentiment label and score plus the visitor's main intent. Results land on
the ``emly_session`` row for the admin Conversations panel.

It used to classify every visitor message as it arrived (one model call
per message, while the UI said one per conversation). Now:

- **When:** a conversation is ready once it has been quiet for
  ``IDLE_MINUTES`` (or was ended), and hasn't been classified since its
  last message. A conversation that resumes later is classified again
  once it goes quiet again.
- **What is sent:** the visitor's messages in that conversation, oldest
  first, joined, and cut to the last ``MAX_CHARS`` characters. Not the
  bot's answers, not form submissions, nothing from other conversations.
- **How:** the worker sweeps for ready conversations every
  ``SWEEP_SECONDS``; the queue holds only bot and session ids, so no
  message text sits in memory waiting. Bounded queue, oldest dropped on
  overflow. Single replica only (in-process queue).
- Runs off the chat path, against the bot's own model endpoint.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)

_QUEUE_MAX = 1000
_QUEUE: "queue.Queue[_EnrichmentJob]" = queue.Queue(maxsize=_QUEUE_MAX)
_DROP_COUNTER = 0
_DROP_LOCK = threading.Lock()
_WORKER_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()


IDLE_MINUTES = 30
MAX_CHARS = 4000
SWEEP_SECONDS = 60
SWEEP_BATCH = 200

_PENDING: set = set()  # (bot_id, session_id) queued and not yet done
_PENDING_LOCK = threading.Lock()


@dataclass
class _EnrichmentJob:
    bot_id: str
    session_id: str


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


def enqueue_session(bot_id: str, session_id: str) -> bool:
    """Queue one conversation for classification. False when it's already
    queued, the bot has the feature off, or the queue is full. Never raises."""
    global _DROP_COUNTER
    if not session_id or not bot_id or not is_enabled(bot_id):
        return False
    key = (bot_id, session_id)
    with _PENDING_LOCK:
        if key in _PENDING:
            return False
        _PENDING.add(key)
    try:
        _QUEUE.put_nowait(_EnrichmentJob(bot_id=bot_id, session_id=session_id))
        return True
    except queue.Full:
        with _PENDING_LOCK:
            _PENDING.discard(key)
        with _DROP_LOCK:
            _DROP_COUNTER += 1
        return False


def _enabled_bot_ids() -> list:
    from models.bots import Bot

    out = []
    for row in Bot.select(Bot.id, Bot.config_json).where(Bot.deleted_at.is_null(True)).dicts():
        cfg = row.get("config_json") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except ValueError:
                continue
        if isinstance(cfg, dict) and cfg.get("enrichment_enabled"):
            out.append(row["id"])
    return out


def sweep_finished(now=None) -> int:
    """Queue every finished, unclassified conversation of enabled bots.
    Returns how many were queued."""
    from datetime import timedelta

    from models.emly_sessions import EMLYSession
    from utils.clock import utc_now

    now = now or utc_now()
    bot_ids = _enabled_bot_ids()
    if not bot_ids:
        return 0
    ready = (
        EMLYSession.select(EMLYSession.bot_id, EMLYSession.id)
        .where(
            EMLYSession.bot_id.in_(bot_ids)
            & (EMLYSession.ended_at.is_null(False)
               | (EMLYSession.last_activity_at < now - timedelta(minutes=IDLE_MINUTES)))
            & (EMLYSession.enrichment_at.is_null(True)
               | (EMLYSession.enrichment_at < EMLYSession.last_activity_at))
        )
        .order_by(EMLYSession.last_activity_at)
        .limit(SWEEP_BATCH)
    )
    return sum(1 for s in ready if enqueue_session(s.bot_id, s.id))


def conversation_text(bot_id: str, session_id: str) -> str:
    """The visitor's messages in one conversation, oldest first, cut to the
    last ``MAX_CHARS`` characters. Form submissions are left out."""
    from models.emly_messages import EMLYMessage, is_form_submission

    rows = (
        EMLYMessage.select(EMLYMessage.message, EMLYMessage.expanded_query)
        .where((EMLYMessage.bot == bot_id) & (EMLYMessage.session_id == session_id)
               & (EMLYMessage.role == "user"))
        .order_by(EMLYMessage.created_at, EMLYMessage.id)
    )
    texts = [r.message for r in rows
             if r.message and not is_form_submission(r.expanded_query)
             and (r.expanded_query or "").strip().lower() not in ("view_bot", "bot_viewed")]
    return "\n".join(texts)[-MAX_CHARS:]


def queue_depth() -> int:
    return _QUEUE.qsize()


def drop_count() -> int:
    return _DROP_COUNTER


# ---------------------------------------------------------------------------
# Classifier prompt — single-call sentiment + top intent.
# ---------------------------------------------------------------------------
_CLASSIFIER_TEMPLATE = """You classify an end user's side of one chat conversation with an assistant.

Return ONLY valid JSON, no prose, no markdown fence. Keys:

  "sentiment_score":  number between -1.0 (very negative) and +1.0 (very positive)
  "sentiment_label":  one of "negative", "neutral", "positive"
  "intent":           short snake_case label naming the user's main goal
                      (e.g. "refund_status", "order_lookup", "password_reset",
                      "shipping_eta", "general_question").
  "intent_confidence": number between 0.0 and 1.0

The user's messages, oldest first:
\"\"\"
{user_text}
\"\"\"
"""


# bot_id -> (settings fingerprint, client). N9: the fingerprint covers the
# model, endpoint and API key, so a change to any of them (config save,
# key rotation, allowlist or env change) builds a fresh client.
_LLM_CACHE: Dict[str, Tuple[str, Any]] = {}


def _fingerprint(settings) -> str:
    key_hash = hashlib.sha256((settings.api_key or "").encode()).hexdigest()
    return "\x1f".join((settings.model or "", settings.endpoint or "", key_hash))


def _build_llm_for_bot(bot_id: str):
    """A ChatOpenAI client for the bot's current LLM settings.

    We don't reuse the agent's per-bot runtime because that's keyed by
    `(bot, user, session)` and may TTL out under conversation load. The
    settings are resolved on every call (one row read and a decrypt,
    small next to the model call); the client is reused while they stay
    the same.
    """
    try:
        from langchain_openai import ChatOpenAI

        from models.bots import Bots
        from services import llm_settings

        bot = Bots.get_by_id(bot_id)
        if bot is None:
            _LLM_CACHE.pop(bot_id, None)
            return None
        llm_cfg = (bot.config_json or {}).get("llm")
        settings = llm_settings.resolve(bot_id, llm_cfg if isinstance(llm_cfg, dict) else {})
        if settings.missing:
            # Not cached: the admin may finish setting the bot up later.
            _LLM_CACHE.pop(bot_id, None)
            log.debug("enrichment: LLM not configured for bot=%s (missing %s)", bot_id, settings.missing)
            return None
        fp = _fingerprint(settings)
        cached = _LLM_CACHE.get(bot_id)
        if cached is not None and cached[0] == fp:
            return cached[1]
        kwargs: Dict[str, Any] = {**settings.chat_kwargs(), "temperature": 0.0, "timeout": 20, "max_retries": 1}
        llm = ChatOpenAI(**kwargs)
        _LLM_CACHE[bot_id] = (fp, llm)
        return llm
    except Exception:
        log.debug("enrichment: failed to construct LLM for bot=%s", bot_id, exc_info=True)
        return None


def _classify_one(job: _EnrichmentJob) -> Optional[Dict[str, Any]]:
    """Run the LLM classifier for a single job. Returns the parsed result
    dict on success, None on any failure."""
    text = conversation_text(job.bot_id, job.session_id)
    if not text.strip():
        return None
    llm = _build_llm_for_bot(job.bot_id)
    if llm is None:
        return None

    prompt = _CLASSIFIER_TEMPLATE.format(user_text=text)
    try:
        result = llm.invoke(prompt)
        content = getattr(result, "content", None) or str(result)
    except Exception:
        log.debug("enrichment: classifier invoke failed", exc_info=True)
        return None

    parsed = _parse_classifier_json(content)
    if parsed is None:
        log.debug("enrichment: classifier returned non-JSON output (len=%d)", len(content or ""))
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
    last_sweep = 0.0
    while not _STOP.is_set():
        if time.time() - last_sweep >= SWEEP_SECONDS:
            last_sweep = time.time()
            try:
                sweep_finished()
            except Exception:
                log.exception("enrichment: sweep failed")
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
            # Mark the attempt even when it produced nothing (no model,
            # a failed call, non-JSON output), so a conversation isn't
            # re-sent every sweep; new messages make it eligible again.
            _apply_result(job, result or {})
        except Exception:
            log.exception("enrichment: unhandled error processing job")
        finally:
            with _PENDING_LOCK:
                _PENDING.discard((job.bot_id, job.session_id))
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
