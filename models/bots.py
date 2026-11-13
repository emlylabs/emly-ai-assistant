"""Bot model — root of the multi-tenant tree.

A bot is what the platform sells: one configurable AI agent. Every other
user-data table FKs to this row. Bots are created from the admin UI.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    DoesNotExist,
    IntegerField,
    Model,
    TextField,
)
from playhouse.shortcuts import model_to_dict
from pydantic import BaseModel

from db.db import DB

log = logging.getLogger(__name__)


class Bot(Model):
    id = CharField(max_length=255, primary_key=True)
    slug = CharField(max_length=255, unique=True)
    name = CharField(max_length=255)
    is_active = BooleanField(default=True)
    is_deleted = BooleanField(default=False)
    config_json = TextField(null=True)
    # Draft slot. When non-null, the admin editor has unpublished changes;
    # the live widget keeps reading ``config_json`` until ``publish_draft``
    # promotes the draft. See migration 004_drafts_and_attribution.
    config_draft_json = TextField(null=True)
    config_draft_updated_at = DateTimeField(null=True)
    config_draft_updated_by_admin_id = CharField(max_length=64, null=True)
    config_schema_version = IntegerField(default=1)
    config_version = IntegerField(default=0)
    api_key_encrypted = TextField(null=True)
    embed_model_id = CharField(max_length=255, null=True)
    current_owner_count = IntegerField(default=0)
    # Widget HMAC key versioning. Bumped via ``Bots.rotate_widget_key`` to
    # invalidate live widget tokens after a leak; the verifier accepts the
    # previous version for ``WIDGET_TOKEN_ROTATION_GRACE_SECONDS``.
    widget_key_version = IntegerField(default=1)
    widget_key_rotated_at = DateTimeField(null=True)
    # JSON array of free-form admin tags (e.g. ``["client:acme", "env:prod"]``).
    # Used by the workspace overview to filter/group bots; never read by
    # the runtime.
    tags = TextField(null=True)
    # Last-saved attribution. Populated by ``update_config`` so the editor
    # can show "Last saved by Sarah at 14:02" without a separate audit fetch.
    last_updated_by_admin_id = CharField(max_length=64, null=True)
    last_updated_by_admin_email = CharField(max_length=255, null=True)
    # 008_space_invites_and_default_space — flags the auto-created default
    # space for each admin. ``default_for_admin_id`` is the owning admin's
    # id (unique — one default per admin). ``default_renamed`` flips on
    # the first successful rename of a default space, locking further
    # renames so the URL/name stays stable for invite emails.
    is_default = IntegerField(default=0)
    default_for_admin_id = CharField(max_length=64, null=True)
    default_renamed = IntegerField(default=0)
    created_at = DateTimeField()
    updated_at = DateTimeField()
    deleted_at = DateTimeField(null=True)

    class Meta:
        database = DB
        table_name = "bots"


class BotModel(BaseModel):
    id: str
    slug: str
    name: str
    is_active: bool
    is_deleted: bool
    config_json: Optional[Dict[str, Any]] = None
    config_draft_json: Optional[Dict[str, Any]] = None
    config_draft_updated_at: Optional[datetime] = None
    config_draft_updated_by_admin_id: Optional[str] = None
    config_schema_version: int
    config_version: int = 0
    api_key_encrypted: Optional[str] = None
    embed_model_id: Optional[str] = None
    current_owner_count: int
    widget_key_version: int = 1
    widget_key_rotated_at: Optional[datetime] = None
    tags: List[str] = []
    last_updated_by_admin_id: Optional[str] = None
    last_updated_by_admin_email: Optional[str] = None
    is_default: bool = False
    default_for_admin_id: Optional[str] = None
    default_renamed: bool = False
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


def _row_to_model(row: Bot) -> BotModel:
    data = model_to_dict(row)
    if data.get("config_json"):
        try:
            data["config_json"] = json.loads(data["config_json"])
        except (TypeError, ValueError):
            data["config_json"] = None
    if data.get("config_draft_json"):
        try:
            data["config_draft_json"] = json.loads(data["config_draft_json"])
        except (TypeError, ValueError):
            data["config_draft_json"] = None
    raw_tags = data.get("tags")
    if raw_tags:
        try:
            parsed = json.loads(raw_tags)
            data["tags"] = [str(t) for t in parsed] if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            data["tags"] = []
    else:
        data["tags"] = []
    return BotModel(**data)


class BotsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([Bot])

    def insert(
        self,
        id: str,
        slug: str,
        name: str,
        config_json: Optional[Dict[str, Any]] = None,
        api_key_encrypted: Optional[str] = None,
        embed_model_id: Optional[str] = None,
        config_schema_version: int = 1,
        is_default: bool = False,
        default_for_admin_id: Optional[str] = None,
    ) -> BotModel:
        now = datetime.utcnow()
        Bot.create(
            id=id,
            slug=slug,
            name=name,
            is_active=True,
            is_deleted=False,
            config_json=json.dumps(config_json) if config_json is not None else None,
            config_schema_version=config_schema_version,
            api_key_encrypted=api_key_encrypted,
            embed_model_id=embed_model_id,
            current_owner_count=0,
            is_default=is_default,
            default_for_admin_id=default_for_admin_id,
            default_renamed=False,
            created_at=now,
            updated_at=now,
        )
        return self.get_by_id(id)  # type: ignore[return-value]

    def get_by_id(self, id: str) -> Optional[BotModel]:
        try:
            return _row_to_model(Bot.get(Bot.id == id))
        except DoesNotExist:
            return None

    def get_by_slug(self, slug: str) -> Optional[BotModel]:
        try:
            return _row_to_model(Bot.get(Bot.slug == slug))
        except DoesNotExist:
            return None

    def rotate_widget_key(self, id: str) -> Optional[BotModel]:
        """Bump ``widget_key_version`` and stamp ``widget_key_rotated_at``.

        Existing widget tokens signed with the previous version remain valid
        for the rotation grace window (see ``services/auth/widget_hmac``).
        """
        now = datetime.utcnow()
        row = (
            Bot.update(
                widget_key_version=Bot.widget_key_version + 1,
                widget_key_rotated_at=now,
                updated_at=now,
            )
            .where(Bot.id == id)
            .execute()
        )
        if row == 0:
            return None
        return self.get_by_id(id)

    def list_active(self) -> List[BotModel]:
        rows = (
            Bot.select()
            .where((Bot.is_active == True) & (Bot.is_deleted == False))  # noqa: E712
            .order_by(Bot.created_at)
        )
        return [_row_to_model(r) for r in rows]

    def update_config(
        self,
        id: str,
        config_json: Dict[str, Any],
        *,
        admin_id: Optional[str] = None,
        admin_email: Optional[str] = None,
    ) -> bool:
        # ``config_version = config_version + 1`` is the cross-worker
        # invalidation signal (Phase 3.5). Any worker that has a cached
        # ``BotRuntime`` for this bot polls the row's version and rebuilds
        # when it goes stale.
        #
        # Promoting a config also clears any in-progress draft on the row
        # so the next read starts clean — there's no value in showing
        # "draft pending" once the draft is what just got published.
        rows = (
            Bot.update(
                config_json=json.dumps(config_json),
                config_version=Bot.config_version + 1,
                config_draft_json=None,
                config_draft_updated_at=None,
                config_draft_updated_by_admin_id=None,
                last_updated_by_admin_id=admin_id,
                last_updated_by_admin_email=admin_email,
                updated_at=datetime.utcnow(),
            )
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def save_draft(
        self,
        id: str,
        config_json: Dict[str, Any],
        *,
        admin_id: Optional[str] = None,
    ) -> bool:
        """Write the draft slot without touching ``config_json`` or bumping
        ``config_version``. The widget runtime ignores this column, so
        saving a draft never affects live traffic."""
        rows = (
            Bot.update(
                config_draft_json=json.dumps(config_json),
                config_draft_updated_at=datetime.utcnow(),
                config_draft_updated_by_admin_id=admin_id,
                updated_at=datetime.utcnow(),
            )
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def discard_draft(self, id: str) -> bool:
        """Clear the draft slot. Idempotent — calling on a bot without a
        draft is a no-op that still returns ``True`` if the row exists."""
        rows = (
            Bot.update(
                config_draft_json=None,
                config_draft_updated_at=None,
                config_draft_updated_by_admin_id=None,
                updated_at=datetime.utcnow(),
            )
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def update_api_key(self, id: str, api_key_encrypted: Optional[str]) -> bool:
        rows = (
            Bot.update(api_key_encrypted=api_key_encrypted, updated_at=datetime.utcnow())
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def update_tags(self, id: str, tags: List[str]) -> bool:
        """Replace the bot's tag set. ``tags`` is stored as a JSON list of
        normalized strings; duplicates collapse and ordering is preserved
        from the caller. Workspace-overview only; runtime ignores this."""
        normalized: List[str] = []
        seen: set[str] = set()
        for t in tags:
            cleaned = str(t).strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        encoded = json.dumps(normalized) if normalized else None
        rows = (
            Bot.update(tags=encoded, updated_at=datetime.utcnow())
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def soft_delete(self, id: str) -> bool:
        now = datetime.utcnow()
        rows = (
            Bot.update(
                is_active=False,
                is_deleted=True,
                deleted_at=now,
                updated_at=now,
            )
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def set_active(self, id: str, is_active: bool) -> bool:
        """Toggle ``is_active`` without touching the soft-delete flags. Used
        by the pause/unpause UI; runtime treats ``is_active=False`` as
        "bot exists but refuses traffic"."""
        rows = (
            Bot.update(is_active=is_active, updated_at=datetime.utcnow())
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def update_slug(self, id: str, slug: str) -> bool:
        """Rename the slug. Caller is responsible for uniqueness + regex
        validation; this only writes the row. Returns ``False`` on FK or
        unique-constraint failure so the route can return 409."""
        from peewee import IntegrityError
        try:
            rows = (
                Bot.update(slug=slug, updated_at=datetime.utcnow())
                .where(Bot.id == id)
                .execute()
            )
        except IntegrityError:
            return False
        return rows > 0

    def restore(self, id: str) -> bool:
        """Reverse soft-delete: clear ``is_deleted`` / ``deleted_at`` and
        bring the bot back live. Idempotent — calling on an already-live
        bot is a no-op that still returns True."""
        now = datetime.utcnow()
        rows = (
            Bot.update(
                is_active=True,
                is_deleted=False,
                deleted_at=None,
                updated_at=now,
            )
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0

    def list_deleted(self) -> List[BotModel]:
        rows = (
            Bot.select()
            .where(Bot.is_deleted == True)  # noqa: E712
            .order_by(Bot.deleted_at.desc())
        )
        return [_row_to_model(r) for r in rows]

    def count(self) -> int:
        return Bot.select().where(Bot.is_deleted == False).count()  # noqa: E712

    def get_default_for_admin(self, admin_id: str) -> Optional[BotModel]:
        """Return the admin's auto-created default space, if any.

        ``default_for_admin_id`` is unique, so this is a one-row lookup.
        Soft-deleted defaults are still returned — callers decide whether
        to treat that as "needs re-create" or "leave it".
        """
        try:
            return _row_to_model(Bot.get(Bot.default_for_admin_id == admin_id))
        except DoesNotExist:
            return None

    def mark_default_renamed(self, id: str) -> bool:
        """Flip ``default_renamed`` to ``True``. Used after the one-shot
        rename of a default space lands; subsequent renames are then
        refused by the route layer."""
        rows = (
            Bot.update(default_renamed=True, updated_at=datetime.utcnow())
            .where(Bot.id == id)
            .execute()
        )
        return rows > 0


Bots = BotsTable(DB)
