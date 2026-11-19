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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    # Widget: browser Web Speech API mic input + spoken replies
    # (Chrome/Edge). Voice is processed by the visitor's browser vendor
    # (e.g. Google for Chrome, Microsoft for Edge). Audio does not pass
    # through Emly servers. The microphone needs HTTPS (or localhost).
    enable_voice: Optional[bool] = None
    # BCP-47 tag (e.g. "es-MX") for mic input and spoken replies. None
    # means the widget uses the visitor's browser language.
    voice_language: Optional[str] = Field(
        default=None, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"
    )

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
    # mailboxes (services/widget_email.py). New bots default to
    # "link_only"; migration 013 pinned bots that predate it to "full".
    alert_email_content: Literal["full", "link_only"] = "link_only"

    # Default instructions for the AI conversation summary in form-alert
    # emails; a form's own trigger ``analysis_prompt`` overrides it, and the
    # deployment SUMMARY_RECOMMENDATION_PROMPT applies when both are empty.
    report_analysis_prompt: Optional[str] = None

    # Opt-in for the sentiment / intent classifier (services/enrichment.py).
    enrichment_enabled: bool = False

    # Q-G12: answer only from this bot's documents. Topics that answer
    # directly (no form-style detail collection) and don't search documents
    # search them anyway; with no matching passage the bot replies with
    # the "cannot find" message instead of answering from the model's own
    # knowledge. Topics that already search documents behave that way
    # regardless. New bots start with it on (routes/admin_bots.py).
    require_citation: bool = False


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
def parse_raw_config(raw: Dict[str, Any], schema_version: int = CURRENT_CONFIG_SCHEMA_VERSION) -> BotConfig:
    """Parse a stored-shape config the way ``get_config_for_bot`` does
    (upgrade chain, then ``BotConfig``). Doesn't mutate ``raw``. Raises
    ``pydantic.ValidationError`` when the schema rejects it."""
    raw = dict(raw or {})
    raw.setdefault("schema_version", schema_version)
    return BotConfig.model_validate(_upgrade(raw))


class BotConfigInvalid(ValueError):
    """N4: a stored config that no longer loads under the current schema.

    The message names the failing fields and error types only, never the
    stored values (prompts, emails and other config content)."""

    def __init__(self, bot_id: str, problems: str):
        self.bot_id = bot_id
        self.problems = problems
        super().__init__(f"config for bot {bot_id} can't be loaded: {problems}")


def describe_config_error(exc: BaseException) -> str:
    """Field paths and error types of a config parse failure, no values."""
    if isinstance(exc, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in e.get('loc', ())) or '(root)'}: {e.get('type', 'invalid')}"
            for e in exc.errors(include_input=False, include_url=False)
        ]
        more = f" (+{len(parts) - 5} more)" if len(parts) > 5 else ""
        return "; ".join(parts[:5]) + more
    return type(exc).__name__


def get_config_for_bot(bot_id: str) -> BotConfig:
    """The bot's live config. Raises ``LookupError`` for an unknown bot
    and ``BotConfigInvalid`` when the stored config doesn't load."""
    bot = Bots.get_by_id(bot_id)
    if bot is None:
        raise LookupError(f"bot not found: {bot_id}")
    try:
        return parse_raw_config(bot.config_json or {}, bot.config_schema_version or 1)
    except ValidationError as exc:
        raise BotConfigInvalid(bot_id, describe_config_error(exc)) from None


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


# bot_id -> problem summary, for bots whose config failed to load at boot.
_INVALID_AT_BOOT: Dict[str, str] = {}


def boot_validate_all_configs() -> Dict[str, str]:
    """Load every active bot's config at boot and report the ones that fail.

    N4: this used to re-raise, so one bot with an unloadable config (a
    bad template, a hand-edited row, a schema change without an upgrade
    step) stopped the whole app, every other bot included. Now each
    failure is logged at ERROR and listed on ``/api/readyz``; the bot
    answers with the "not configured" reply until an admin saves a
    valid config. Returns ``{bot_id: problems}``.
    """
    _INVALID_AT_BOOT.clear()
    for bot in Bots.list_active():
        try:
            get_config_for_bot(bot.id)
        except BotConfigInvalid as exc:
            _INVALID_AT_BOOT[bot.id] = exc.problems
        except LookupError:
            continue
        except Exception as exc:  # an upgrade step that crashed
            _INVALID_AT_BOOT[bot.id] = type(exc).__name__
    for bot_id, problems in _INVALID_AT_BOOT.items():
        log.error("Bot %s config can't be loaded under schema v%s (%s); it won't answer until "
                  "an admin saves a valid config", bot_id, CURRENT_CONFIG_SCHEMA_VERSION, problems)
    return dict(_INVALID_AT_BOOT)


def invalid_bot_configs() -> Dict[str, str]:
    """Bots found unloadable at boot that still don't load (for readyz).
    A bot drops off once its config is fixed or the bot is removed."""
    still: Dict[str, str] = {}
    for bot_id, problems in list(_INVALID_AT_BOOT.items()):
        try:
            get_config_for_bot(bot_id)
        except BotConfigInvalid as exc:
            still[bot_id] = exc.problems
            continue
        except LookupError:
            pass
        except Exception:
            still[bot_id] = problems
            continue
        _INVALID_AT_BOOT.pop(bot_id, None)
    return still


__all__ = [
    "BotConfig",
    "BotConfigInvalid",
    "CFormEntry",
    "LLMConfig",
    "LimitsConfig",
    "RAGConfig",
    "CURRENT_CONFIG_SCHEMA_VERSION",
    "boot_validate_all_configs",
    "describe_config_error",
    "invalid_bot_configs",
    "discard_draft_for_bot",
    "get_config_for_bot",
    "get_decrypted_api_key",
    "get_draft_for_bot",
    "save_config_for_bot",
    "save_draft_for_bot",
    "set_api_key",
]
