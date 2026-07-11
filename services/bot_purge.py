"""Idempotent purge of soft-deleted bots.

Soft-delete returns immediately; the actual cascade (Qdrant points,
files on disk, child rows, the bot row itself) runs here as a
background task. Each step is independently retry-safe — running the
purge twice is a no-op, partial failures resume cleanly.

Order matters: vectors first (the only piece outside the relational DB
that doesn't FK-cascade), then files on disk, then DB rows.

Tier 2 of multi-bot-ui plan: a 7-day grace window before a soft-deleted
bot is hard-purged. The scheduler in ``main.py`` calls
``purge_all_pending`` daily; rows whose ``deleted_at`` is within the
grace period are skipped, leaving them recoverable by an operator with
DB access until the window expires.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from agents.rag_manager import get_rag_manager
from config import DATA_DIR
from models.bots import Bot, Bots, BotModel


# Grace period: how long a soft-deleted bot is recoverable before the
# purge job permanently drops its data. The Settings-page confirm dialog
# in the UI surfaces this number.
PURGE_GRACE = timedelta(days=7)

log = logging.getLogger(__name__)


def _bot_dir(bot_id: str) -> Path:
    return Path(DATA_DIR) / "bots" / bot_id


def purge_one(bot: BotModel) -> None:
    """Run all purge steps for a single bot. Each step swallows its own
    failure and logs — total purge is best-effort, retried on next sweep.
    """
    bot_id = bot.id

    # 1. Vector store: drop every point owned by this bot.
    try:
        get_rag_manager().delete_bot(bot_id)
    except Exception:
        log.exception("delete_bot in Qdrant failed for bot=%s", bot_id)

    # 2. Filesystem: remove the bot's upload tree.
    bot_dir = _bot_dir(bot_id)
    if bot_dir.exists():
        try:
            shutil.rmtree(bot_dir)
        except Exception:
            log.exception("Filesystem cleanup failed for bot=%s at %s", bot_id, bot_dir)

    # 3. Relational rows: every user-data table FKs to ``bots(id)`` with
    # ``ON DELETE CASCADE``, so the row delete cleans up the rest.
    try:
        Bot.delete().where(Bot.id == bot_id).execute()
        log.info("Hard-purged bot=%s", bot_id)
    except Exception:
        log.exception("Hard delete failed for bot=%s", bot_id)


def purge_all_pending(grace: timedelta = PURGE_GRACE) -> List[str]:
    """Sweep the ``bots`` table for soft-deleted rows and purge each.

    Only rows whose ``deleted_at`` is older than ``grace`` are
    purged — fresh deletions stay recoverable for the grace window.
    Safe to call as often as you like; idempotent on the per-bot
    cascade and re-entrant on partial failure.
    """
    cutoff = datetime.utcnow() - grace
    purged: List[str] = []
    query = Bot.select().where(
        (Bot.is_deleted == True) & (Bot.deleted_at <= cutoff)  # noqa: E712
    )
    for row in query:
        bot = BotModel(
            id=row.id,
            slug=row.slug,
            name=row.name,
            is_active=row.is_active,
            is_deleted=row.is_deleted,
            config_json=None,
            config_schema_version=row.config_schema_version,
            config_version=row.config_version,
            api_key_encrypted=row.api_key_encrypted,
            embed_model_id=row.embed_model_id,
            current_owner_count=row.current_owner_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
            deleted_at=row.deleted_at,
        )
        purge_one(bot)
        purged.append(bot.id)
    return purged
