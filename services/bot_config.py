"""Per-bot configuration store.

Replaces the deprecated ``{DATA_DIR}/emly_config.json`` global file with
a per-bot row in the ``bots`` table. ``bots.config_json`` holds the JSON
blob; ``bots.config_schema_version`` lets quiescent bots self-upgrade
when their config shape evolves.

All access goes through ``get_config_for_bot`` / ``save_config_for_bot``.
Don't read or write ``bots.config_json`` directly — the schema-version
upgrade chain runs here, and centralizing it keeps lazy migration
predictable.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from models.bots import Bots
from services.secrets import decrypt, encrypt

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema version tracking
# ---------------------------------------------------------------------------
CURRENT_CONFIG_SCHEMA_VERSION = 1


class LimitsConfig(BaseModel):
    """Cost / abuse caps applied per bot."""

    model_config = ConfigDict(extra="ignore")

    daily_token_cap: Optional[int] = None
    messages_per_minute_per_user: Optional[int] = None
    messages_per_minute_per_bot: Optional[int] = None
    max_file_size_mb: int = 50
    total_storage_quota_mb: Optional[int] = None
    file_count_cap: int = 10_000
    mime_allowlist: List[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/html",
            "text/markdown",
            "text/csv",
        ]
    )
    # Sites allowed to embed the widget. Empty (the default for new bots)
    # means only this app's own origin — the admin preview and the hosted
    # test page. Existing bots were given an explicit ["*"] by migration 010.
    widget_allowed_origins: List[str] = Field(default_factory=list)
    # Conversations, form submissions and chat attachments older than this
    # many days are deleted by the nightly purge (services/data_retention).
    # None keeps them until deleted by hand.
    retention_days: Optional[int] = Field(default=None, ge=1, le=3650)


class LLMConfig(BaseModel):
    """Provider config; the API key is stored separately in
    ``bots.api_key_encrypted`` and never materialized in this dict."""

    model_config = ConfigDict(extra="ignore")

    model_type: str = "openai"
    model: Optional[str] = None
    api_endpoint: Optional[str] = None
    temperature: Optional[float] = None


class RAGConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    top_k: int = 5
    chunk_size: int = 2048
    chunk_overlap: int = 256
    enable_hybrid_search: bool = False
    embedding_threshold: float = 0.20


class CFormEntry(BaseModel):
    """One configurable form attached to a bot.

    Persisted as a single-key dict ``{<name>: {form_schema, trigger}}``;
    this model validates the inner body. The outer single-key shape is
    enforced by ``c_forms_selected: List[Dict[str, CFormEntry]]``.
    """

    model_config = ConfigDict(extra="forbid")

    form_schema: Dict[str, Any] = Field(default_factory=dict)
    trigger: Dict[str, Any] = Field(default_factory=dict)


class BotConfig(BaseModel):
    """Per-bot config blob.

    All keys are snake_case. ``extra='forbid'`` rejects camelCase drift at
    PUT time — if a new field is needed, declare it here (and bump the
    schema version when the shape changes incompatibly).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = CURRENT_CONFIG_SCHEMA_VERSION
    topics: Dict[str, Any] = Field(default_factory=dict)
    global_prompts: Dict[str, str] = Field(default_factory=dict)
    c_forms_selected: List[Dict[str, CFormEntry]] = Field(default_factory=list)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    # Widget chrome / launcher
    launcher_label: Optional[str] = None
    is_icon_with_label: Optional[bool] = None
    open_icon: Optional[str] = None
    close_icon: Optional[str] = None
    show_min_max: Optional[bool] = None
    show_close: Optional[bool] = None
    show_menu: Optional[bool] = None
    max_window: Optional[bool] = None
    open_link_in_same_tab: Optional[bool] = None
    feedback: Optional[bool] = None
    show_citations: Optional[bool] = None

    # Conversational
    starter_messages: Optional[List[str]] = None
    nudges: Optional[Dict[str, Any]] = None

    # Contact / social / legal
    support_email: Optional[str] = None
    whatsapp_link: Optional[str] = None
    whatsapp_message: Optional[str] = None
    social_handles: Optional[Dict[str, Any]] = None
    terms_of_service: Optional[Dict[str, Any]] = None

    # Optional grouped widget block (theme + layout)
    widget: Optional[Dict[str, Any]] = None

    # What form-alert emails contain. "full": AI summary, transcript,
    # visitor details and attachments. "link_only": just a link to the
    # conversation in the admin console, so visitor data stays out of
    # mailboxes (services/widget_email.py).
    alert_email_content: Literal["full", "link_only"] = "full"


# ---------------------------------------------------------------------------
# Lazy upgrade chain
# ---------------------------------------------------------------------------
# Maps source version → upgrade function that bumps to source+1. Plug in
# new upgraders here when bumping the schema; readers walk the chain on
# every load until the JSON matches ``CURRENT_CONFIG_SCHEMA_VERSION``.
UPGRADERS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def _upgrade(raw: Dict[str, Any]) -> Dict[str, Any]:
    version = int(raw.get("schema_version", 1))
    while version < CURRENT_CONFIG_SCHEMA_VERSION:
        if version not in UPGRADERS:
            log.error(
                "No upgrader for config schema version %s -> %s; refusing to load",
                version,
                version + 1,
            )
            raise RuntimeError(f"missing config upgrader v{version}->v{version + 1}")
        raw = UPGRADERS[version](raw)
        version = int(raw.get("schema_version", version + 1))
    return raw


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_config_for_bot(bot_id: str) -> BotConfig:
    bot = Bots.get_by_id(bot_id)
    if bot is None:
        raise LookupError(f"bot not found: {bot_id}")
    raw = bot.config_json or {}
    raw.setdefault("schema_version", bot.config_schema_version or 1)
    raw = _upgrade(raw)
    return BotConfig.model_validate(raw)


def save_config_for_bot(
    bot_id: str,
    config: BotConfig | Dict[str, Any],
    *,
    admin_id: Optional[str] = None,
    admin_email: Optional[str] = None,
) -> BotConfig:
    if isinstance(config, dict):
        config = BotConfig.model_validate(config)
    config.schema_version = CURRENT_CONFIG_SCHEMA_VERSION
    payload = config.model_dump(mode="json")
    if not Bots.update_config(bot_id, payload, admin_id=admin_id, admin_email=admin_email):
        raise LookupError(f"bot not found: {bot_id}")
    return config


def save_draft_for_bot(
    bot_id: str,
    config: BotConfig | Dict[str, Any],
    *,
    admin_id: Optional[str] = None,
) -> BotConfig:
    """Write the draft slot. Pydantic validation happens here so a bad
    draft is rejected at write time — same contract as the live save —
    but ``config_version`` does not bump and the runtime continues to
    serve the published config."""
    if isinstance(config, dict):
        config = BotConfig.model_validate(config)
    config.schema_version = CURRENT_CONFIG_SCHEMA_VERSION
    payload = config.model_dump(mode="json")
    if not Bots.save_draft(bot_id, payload, admin_id=admin_id):
        raise LookupError(f"bot not found: {bot_id}")
    return config


def get_draft_for_bot(bot_id: str) -> Optional[BotConfig]:
    """Return the parsed draft, or ``None`` if no draft is pending."""
    bot = Bots.get_by_id(bot_id)
    if bot is None or not bot.config_draft_json:
        return None
    raw = dict(bot.config_draft_json)
    raw.setdefault("schema_version", bot.config_schema_version or 1)
    raw = _upgrade(raw)
    return BotConfig.model_validate(raw)


def discard_draft_for_bot(bot_id: str) -> bool:
    return Bots.discard_draft(bot_id)


def get_decrypted_api_key(bot_id: str) -> Optional[str]:
    bot = Bots.get_by_id(bot_id)
    if bot is None:
        return None
    return decrypt(bot.api_key_encrypted)


def set_api_key(bot_id: str, api_key: Optional[str]) -> bool:
    return Bots.update_api_key(bot_id, encrypt(api_key))


def boot_validate_all_configs() -> None:
    """Walk every active bot's config through the version chain at boot.

    Fail-fast: a bot whose config can't be loaded under the current schema
    is a deploy-time bug, not a runtime fallback. Better to refuse to boot
    than serve a stale or unparseable config silently.
    """
    for bot in Bots.list_active():
        try:
            get_config_for_bot(bot.id)
        except Exception:
            log.exception("Bot %s has an unparseable config_json under schema v%s",
                          bot.id, CURRENT_CONFIG_SCHEMA_VERSION)
            raise


__all__ = [
    "BotConfig",
    "CFormEntry",
    "LLMConfig",
    "LimitsConfig",
    "RAGConfig",
    "CURRENT_CONFIG_SCHEMA_VERSION",
    "boot_validate_all_configs",
    "discard_draft_for_bot",
    "get_config_for_bot",
    "get_decrypted_api_key",
    "get_draft_for_bot",
    "save_config_for_bot",
    "save_draft_for_bot",
    "set_api_key",
]
