"""G2d: list every outbound destination at start-up, by host only.

One INFO block at boot names each system this deployment is configured
to send data to, so an operator (or a security reviewer reading the
logs) can check the list against what they expect. Only hosts and
ports are logged, never paths, query strings, user names or keys.

The code paths behind each line are listed in README "Outbound
connections" and enforced by tests/security/test_network_clients.py.

G2f: Qdrant holds the plain text of every indexed passage.
``qdrant_warnings`` flags a ``QDRANT_URL`` that isn't a private address
or an internal name (Qdrant Cloud, say), on the boot log and readyz.

G8c: in production, such a Qdrant must have ``QDRANT_API_KEY`` set, or
start-up stops (``check_qdrant_settings``). A Qdrant on the private
network (compose's ``http://qdrant:6333``) may run without one.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
from collections import Counter
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
# Suffixes used for names that only resolve inside a network.
_INTERNAL_SUFFIXES = (".internal", ".local", ".localdomain", ".lan", ".intranet", ".corp", ".home.arpa",
                      ".svc", ".cluster.local")
# Channel rows that don't talk to an outside platform.
_LOCAL_CHANNEL_TYPES = {"web_widget"}


def host_of(url: Optional[str]) -> Optional[str]:
    """``host[:port]`` of a URL (or of a bare ``host:port``), nothing else."""
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw if "://" in raw else f"//{raw}")
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "(unparseable)"
    if not host:
        return "(unparseable)"
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def hostname_of(url: Optional[str]) -> Optional[str]:
    """The bare host name or IP of a URL (or ``host:port``), lower-cased."""
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        host = urlsplit(raw if "://" in raw else f"//{raw}").hostname
    except ValueError:
        return None
    return host.lower().rstrip(".") if host else None


def is_internal_host(name: Optional[str]) -> bool:
    """True for loopback/private/link-local addresses and for names that
    only resolve inside a network (single-label names like ``qdrant``,
    ``*.internal``, ``*.svc.cluster.local``). Takes a bare host name or
    IP (see ``hostname_of``). No DNS lookup is made."""
    if not name:
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return name == "localhost" or "." not in name or name.endswith(_INTERNAL_SUFFIXES)
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _env(name: str) -> Optional[str]:
    v = (os.environ.get(name) or "").strip()
    return v or None


def _bot_model_hosts() -> Counter:
    """Model endpoint host -> number of bots that set their own."""
    hosts: Counter = Counter()
    try:
        from models.bots import Bot

        for row in Bot.select(Bot.config_json).where(Bot.deleted_at.is_null(True)).dicts():
            cfg = row.get("config_json") or {}
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            llm = cfg.get("llm") if isinstance(cfg, dict) else None
            host = host_of(llm.get("api_endpoint")) if isinstance(llm, dict) else None
            if host:
                hosts[host] += 1
    except Exception:
        log.debug("egress summary: couldn't list bot endpoints", exc_info=True)
    return hosts


def _channel_platforms() -> List[str]:
    try:
        from models.bot_channels import BotChannel

        rows = BotChannel.select(BotChannel.type).where(BotChannel.is_active == True).distinct()  # noqa: E712
        return sorted({r.type for r in rows} - _LOCAL_CHANNEL_TYPES)
    except Exception:
        log.debug("egress summary: couldn't list channels", exc_info=True)
        return []


def _external_idp() -> Optional[str]:
    issuer = _env("AUTH_OIDC_ISSUER")
    base = (_env("APP_BASE_URL") or "").rstrip("/")
    if not issuer or (base and issuer.rstrip("/") == base):
        return None
    return host_of(issuer)


def _crawl_scope() -> str:
    try:
        from services.crawl_fetch import allowed_private_networks

        nets = allowed_private_networks()
    except Exception:
        nets = ()
    scope = "public addresses"
    if nets:
        scope += " and " + ", ".join(str(n) for n in nets)
    return f"{scope}, when an admin adds a crawl"


def egress_summary() -> List[Tuple[str, str]]:
    """(destination, where it goes) for each kind of outbound traffic."""
    from utils.model_pins import hf_offline

    default_llm = host_of(_env("OPENAI_BASE_URL"))
    bot_hosts = _bot_model_hosts()
    per_bot = ", ".join(f"{h} ({n} bot{'s' if n != 1 else ''})" for h, n in sorted(bot_hosts.items()))
    embeddings = (os.environ.get("EMBEDDING_PROVIDER") or "huggingface").strip().lower()
    qdrant = host_of(_env("QDRANT_URL"))
    email = host_of(_env("EMAIL_SERVER"))
    if email and not email.endswith("]") and ":" not in email:
        email = f"{email}:{_env('EMAIL_PORT') or 465}"  # config.EMAIL_PORT's default
    platforms = _channel_platforms()
    tracing = (os.environ.get("ALLOW_LANGSMITH_TRACING") or "").strip().lower() in _TRUE

    return [
        ("Model endpoint (default)", default_llm or "not set"),
        ("Model endpoints set by bots", per_bot or "none"),
        ("Embeddings", f"{default_llm or 'OPENAI_BASE_URL'} (remote)" if embeddings == "openai"
         else "local model, nothing sent"),
        ("Vector database", qdrant or "embedded, local files"),
        ("Mail server", email or "off"),
        ("Identity provider", _external_idp() or "built-in, nothing sent"),
        ("Chat platforms", ", ".join(platforms) if platforms else "none connected"),
        ("Hugging Face Hub", "off" if hf_offline() else "on, model downloads allowed"),
        ("LangSmith tracing", "ALLOWED by ALLOW_LANGSMITH_TRACING" if tracing else "off"),
        ("Website crawling", _crawl_scope()),
    ]


def qdrant_warnings() -> List[Dict[str, str]]:
    url = _env("QDRANT_URL")
    name = hostname_of(url)
    if not name or is_internal_host(name):
        return []
    host = host_of(url)
    return [{
        "code": "qdrant_remote",
        "message": f"QDRANT_URL points at {host}, which isn't a private address or internal name. The "
                   "vector database holds the text of every indexed document; make sure this host is "
                   "yours, uses https and an API key, and has telemetry off "
                   "(QDRANT__TELEMETRY_DISABLED=true).",
    }]


class QdrantSettingsError(RuntimeError):
    pass


def check_qdrant_settings() -> None:
    """Boot (G8c): refuse a remote Qdrant without an API key in production."""
    from utils.runtime import is_prod_mode

    url = _env("QDRANT_URL")
    name = hostname_of(url)
    if not is_prod_mode() or not name or is_internal_host(name) or _env("QDRANT_API_KEY"):
        return
    raise QdrantSettingsError(
        f"QDRANT_URL points at {host_of(url)}, outside your network, and QDRANT_API_KEY is not set. "
        "The vector database holds the text of every indexed document: set QDRANT_API_KEY (or "
        "QDRANT_API_KEY_FILE), or use a Qdrant on your private network.")


_TLS_SSLMODES = {"require", "verify-ca", "verify-full"}


def postgres_tls_warnings() -> List[Dict[str, str]]:
    """G13e: in production, a Postgres outside the private network should
    be reached over TLS (``?sslmode=require`` or stricter)."""
    from urllib.parse import parse_qs

    from utils.runtime import is_prod_mode

    url = _env("DATABASE_URL") or ""
    if not is_prod_mode() or not url.lower().startswith(("postgres", "postgresql")):
        return []
    name = hostname_of(url)
    sslmode = (parse_qs(urlsplit(url).query).get("sslmode") or [""])[0].lower()
    if not name or is_internal_host(name) or sslmode in _TLS_SSLMODES:
        return []
    return [{
        "code": "postgres_no_tls",
        "message": f"DATABASE_URL points at {host_of(url)} without sslmode=require (or verify-full). "
                   "Conversations and visitor details would cross the network unencrypted. Add "
                   "?sslmode=verify-full&sslrootcert=<CA file> to the URL.",
    }]


def log_egress_summary() -> None:
    rows = egress_summary()
    width = max(len(k) for k, _ in rows)
    lines = "\n".join(f"  {k.ljust(width)}  {v}" for k, v in rows)
    log.info("Outbound destinations (hosts only; see README 'Outbound connections'):\n%s", lines)
    for w in qdrant_warnings() + postgres_tls_warnings():
        log.warning(w["message"])
