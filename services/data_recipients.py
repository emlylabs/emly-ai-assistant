"""G2g: where one bot's data goes, for its Settings page.

The deployment-wide list is logged at boot (``services.egress``). This
is the same for one bot, from its live config: the model endpoint its
answers, alert summaries and classification use, the chat platforms it
is connected to, who receives its form alerts and what they contain,
and the deployment-wide pieces (embeddings, search index, tracing).

Hosts only, never paths, keys or credentials. Alert recipients are
listed, since they are part of the bot's config that members can
already read.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from services.egress import host_of, hostname_of, is_internal_host

# What each chat platform's traffic reaches, from the channel adapters.
PLATFORM_HOSTS = {
    "slack": ("Slack", "slack.com"),
    "teams": ("Microsoft Teams", "Microsoft Bot Framework (smba.trafficmanager.net)"),
    "google_chat": ("Google Chat", "chat.googleapis.com"),
    "whatsapp_cloud": ("WhatsApp", "graph.facebook.com (Meta)"),
    "telegram": ("Telegram", "api.telegram.org"),
}

_ALERT_CONTENT = {
    "link_only": "a link to the conversation only",
    "full": "AI summary, transcript, visitor details and attachments",
}


def _row(what: str, destination: str, detail: str = "", *, scope: Optional[str] = None,
         url: Optional[str] = None) -> Dict[str, Any]:
    """``scope``: "internal" (inside your network), "external" or None
    (nothing leaves, or not applicable)."""
    if scope is None and url:
        scope = "internal" if is_internal_host(hostname_of(url)) else "external"
    return {"what": what, "destination": destination, "detail": detail, "scope": scope}


def _model_row(bot_id: str, llm_cfg: Dict[str, Any]) -> Dict[str, Any]:
    from services import llm_settings

    own = llm_settings._clean(llm_cfg.get("api_endpoint"))
    settings = llm_settings.resolve(bot_id, llm_cfg)
    what = "Answers, alert summaries and classification"
    if not settings.endpoint:
        reason = ("this bot's endpoint isn't on LLM_ENDPOINT_ALLOWLIST" if own
                  else "no endpoint is set")
        return _row(what, "none", f"The bot can't answer: {reason}.")
    source = "set on this bot" if own else "the deployment default"
    detail = f"Model {settings.model or '(not set)'}, {source}. Each question, the passages found for it "\
             "and recent turns of the conversation are sent."
    return _row(what, host_of(settings.endpoint), detail, url=settings.endpoint)


def _embeddings_row() -> Dict[str, Any]:
    provider = (os.environ.get("EMBEDDING_PROVIDER") or "huggingface").strip().lower()
    what = "Document search (embeddings)"
    if provider != "openai":
        return _row(what, "this server", "A local model; nothing is sent.")
    endpoint = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    return _row(what, host_of(endpoint) or "not set",
                "Every document passage and every question is sent.", url=endpoint or None)


def _index_row() -> Dict[str, Any]:
    url = (os.environ.get("QDRANT_URL") or "").strip()
    what = "Search index (passages of your documents)"
    if not url:
        return _row(what, "this server", "Embedded index files.")
    return _row(what, host_of(url), "Holds the text of every indexed passage.", url=url)


def _channel_rows(bot_id: str) -> List[Dict[str, Any]]:
    from models.bot_channels import BotChannel

    rows = []
    for ch in BotChannel.select().where((BotChannel.bot == bot_id) & (BotChannel.is_active == True)):  # noqa: E712
        if ch.type not in PLATFORM_HOSTS:
            continue  # the web widget talks only to this server
        label, host = PLATFORM_HOSTS[ch.type]
        name = f" ({ch.display_name})" if ch.display_name else ""
        rows.append(_row(f"{label}{name}", host,
                         "Questions and answers in this chat app travel through the platform. "
                         "Emly keeps the sender's name and contact details the platform gives.",
                         scope="external"))
    return rows


def coerce_email_list(value: Any) -> List[str]:
    """Normalize a form trigger's ``alert_emails`` (list | comma-string | None)."""
    if isinstance(value, list):
        return [s.strip() for s in value if isinstance(s, str) and s.strip()]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


def alert_recipients(cfg) -> List[str]:
    """Every form's alert recipients, de-duplicated (case-insensitive)."""
    recipients: List[str] = []
    seen = set()
    for entry in cfg.model_dump(mode="json").get("c_forms_selected") or []:
        if not isinstance(entry, dict):
            continue
        for body in entry.values():
            trigger = (body or {}).get("trigger") if isinstance(body, dict) else None
            for addr in coerce_email_list((trigger or {}).get("alert_emails")):
                if addr.lower() not in seen:
                    seen.add(addr.lower())
                    recipients.append(addr)
    return recipients


def _alert_rows(cfg) -> List[Dict[str, Any]]:
    recipients = alert_recipients(cfg)
    if not recipients:
        return []
    server = (os.environ.get("EMAIL_SERVER") or "").strip()
    what = "Form alert emails"
    if not server:
        return [_row(what, "none", "Email is off on this deployment, so no alerts are sent.")]
    content = _ALERT_CONTENT.get(cfg.alert_email_content, cfg.alert_email_content)
    return [_row(what, ", ".join(recipients),
                 f"Sent through {host_of(server)}. Each alert contains {content}.", url=server)]


def _tracing_row() -> Optional[Dict[str, Any]]:
    from utils.runtime import env_flag

    if not env_flag("ALLOW_LANGSMITH_TRACING", default=False):
        return None
    endpoint = (os.environ.get("LANGSMITH_ENDPOINT") or os.environ.get("LANGCHAIN_ENDPOINT")
                or "https://api.smith.langchain.com")
    return _row("LangSmith tracing", host_of(endpoint),
                "Allowed by ALLOW_LANGSMITH_TRACING: model inputs and outputs are sent.", url=endpoint)


def for_bot(bot_id: str) -> List[Dict[str, Any]]:
    from models.bots import Bots
    from services.bot_config import get_config_for_bot

    cfg = get_config_for_bot(bot_id)
    bot = Bots.get_by_id(bot_id)
    raw_llm = ((bot.config_json or {}).get("llm") if bot else None) or {}
    rows = [_model_row(bot_id, raw_llm if isinstance(raw_llm, dict) else {}),
            _embeddings_row(), _index_row()]
    rows += _channel_rows(bot_id)
    rows += _alert_rows(cfg)
    tracing = _tracing_row()
    if tracing:
        rows.append(tracing)
    return rows
