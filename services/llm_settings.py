"""Which model, endpoint and API key a bot's LLM calls use (D1).

Each value comes from the bot first and the deployment second:

    model     bot ``llm.model``         then env ``MODEL``
    endpoint  bot ``llm.api_endpoint``  then env ``OPENAI_BASE_URL``
    api key   bot's encrypted key       then env ``OPENAI_API_KEY``

There is no built-in default for any of them. A bot missing one refuses
to answer (``LLMNotConfigured``) instead of silently sending visitor
messages to a provider nobody chose. Env values are read at call time.

``LLM_ENDPOINT_ALLOWLIST`` (G2a) limits where any bot may send model
traffic. When it is set, an endpoint that isn't on it resolves as no
endpoint at all, so the bot refuses to answer exactly as if none were set.
It doesn't fall back to the deployment endpoint: the bot's API key
belongs to the endpoint its owner chose.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger(__name__)

MODEL_ENV = "MODEL"
ENDPOINT_ENV = "OPENAI_BASE_URL"
API_KEY_ENV = "OPENAI_API_KEY"
ALLOWLIST_ENV = "LLM_ENDPOINT_ALLOWLIST"

# What a visitor sees when the bot can't answer because it isn't set up.
# Deliberately says nothing about why: that is for the admin UI.
NOT_CONFIGURED_REPLY = "Sorry, this assistant isn't available right now. Please try again later."


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def deployment_model() -> Optional[str]:
    return _clean(os.environ.get(MODEL_ENV))


def deployment_endpoint() -> Optional[str]:
    return _clean(os.environ.get(ENDPOINT_ENV))


def deployment_api_key() -> Optional[str]:
    return _clean(os.environ.get(API_KEY_ENV))


# ---------------------------------------------------------------------------
# Endpoint allowlist
# ---------------------------------------------------------------------------
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_port(parsed) -> Tuple[Optional[str], Optional[int]]:
    try:
        port = parsed.port
    except ValueError:
        return None, None
    host = (parsed.hostname or "").rstrip(".").lower() or None
    return host, port or _DEFAULT_PORTS.get(parsed.scheme)


def endpoint_allowlist() -> Optional[List[str]]:
    """Entries of ``LLM_ENDPOINT_ALLOWLIST``, or ``None`` when it is unset
    (no restriction). Comma- or whitespace-separated. Each entry is one of:

    - a base URL, ``https://llm.example.com/v1``: same scheme, host and
      port, and a path under that path;
    - a host, optionally with a port, ``llm.example.com`` or
      ``llm.internal:8443``: any scheme and path on that host;
    - ``*.example.com``: any subdomain of example.com (not example.com).
    """
    raw = os.environ.get(ALLOWLIST_ENV)
    if raw is None or not raw.strip():
        return None
    return [e for e in raw.replace(",", " ").split() if e]


def _entry_allows(entry: str, url) -> bool:
    host, port = _host_port(url)
    if "://" in entry:
        allowed = urlparse(entry)
        a_host, a_port = _host_port(allowed)
        if not a_host or allowed.scheme.lower() != url.scheme.lower() or (a_host, a_port) != (host, port):
            return False
        base = allowed.path.rstrip("/")
        return not base or url.path == base or url.path.startswith(base + "/")
    e_host, _, e_port = entry.lower().rstrip(".").partition(":")
    if e_port and (not e_port.isdigit() or int(e_port) != port):
        return False
    if e_host.startswith("*."):
        return host.endswith(e_host[1:]) and len(host) > len(e_host) - 1
    return host == e_host


def is_endpoint_allowed(endpoint: str) -> bool:
    """Whether ``endpoint`` may receive model traffic. Always true when
    ``LLM_ENDPOINT_ALLOWLIST`` is unset; false for a malformed URL when
    it is set."""
    entries = endpoint_allowlist()
    if entries is None:
        return True
    url = urlparse(endpoint.strip())
    if url.scheme.lower() not in _DEFAULT_PORTS or not _host_port(url)[0]:
        return False
    return any(_entry_allows(e, url) for e in entries)


def endpoint_host(endpoint: str) -> str:
    """Host only, for logs and messages: never the path or credentials."""
    return _host_port(urlparse(endpoint.strip()))[0] or "?"


def embedding_endpoint() -> str:
    """G2b: where remote embeddings go when ``EMBEDDING_PROVIDER=openai``.

    Every document chunk and every search query is sent there, so there
    is no default: without ``OPENAI_BASE_URL`` the OpenAI client would
    silently use api.openai.com. Raises ``RuntimeError`` (refusing to
    boot) when it is unset or not on ``LLM_ENDPOINT_ALLOWLIST``."""
    endpoint = deployment_endpoint()
    if not endpoint:
        raise RuntimeError(
            f"EMBEDDING_PROVIDER=openai requires {ENDPOINT_ENV}: every document chunk and search "
            "query is sent to that endpoint, and there is no default. Set it, or use "
            "EMBEDDING_PROVIDER=huggingface to embed inside this deployment."
        )
    if not is_endpoint_allowed(endpoint):
        raise RuntimeError(
            f"EMBEDDING_PROVIDER=openai: {ENDPOINT_ENV} host {endpoint_host(endpoint)} is not on "
            f"{ALLOWLIST_ENV}."
        )
    log.warning("Remote embeddings are on (EMBEDDING_PROVIDER=openai): every document chunk and "
                "search query is sent to %s", endpoint_host(endpoint))
    return endpoint


def endpoint_policy_warnings() -> List[Dict[str, str]]:
    """Deploy-shape hazards for ``/api/readyz`` and the boot log, as
    ``{code, message}``. Hosts only, never full URLs."""
    from utils.runtime import is_prod_mode

    out: List[Dict[str, str]] = []
    if endpoint_allowlist() is None:
        if is_prod_mode():
            out.append({
                "code": "llm_endpoint_unrestricted",
                "message": f"{ALLOWLIST_ENV} is not set: any bot owner can send conversations to any "
                           "model endpoint. Set it to the endpoints you have approved.",
            })
        return out
    default = deployment_endpoint()
    if default and not is_endpoint_allowed(default):
        out.append({
            "code": "llm_default_endpoint_not_allowed",
            "message": f"{ENDPOINT_ENV} host {endpoint_host(default)} is not on {ALLOWLIST_ENV}: bots "
                       "that rely on the deployment default endpoint won't answer.",
        })
    return out


@dataclass(frozen=True)
class LLMSettings:
    model: Optional[str]
    endpoint: Optional[str]
    api_key: Optional[str] = field(default=None, repr=False)

    @property
    def missing(self) -> List[str]:
        return [name for name, value in (
            ("model", self.model), ("endpoint", self.endpoint), ("api_key", self.api_key),
        ) if not value]

    def chat_kwargs(self) -> Dict[str, Any]:
        """``ChatOpenAI`` keyword arguments for these settings."""
        return {"model": self.model, "base_url": self.endpoint, "api_key": self.api_key}


class LLMNotConfigured(RuntimeError):
    def __init__(self, bot_id: Optional[str], missing: List[str]):
        self.bot_id = bot_id
        self.missing = list(missing)
        super().__init__(f"LLM not configured for bot {bot_id}: missing {', '.join(missing)}")


def _bot_llm_config(bot_id: str) -> Mapping[str, Any]:
    from models.bots import Bots

    bot = Bots.get_by_id(bot_id)
    llm = (bot.config_json or {}).get("llm") if bot else None
    return llm if isinstance(llm, Mapping) else {}


def resolve(
    bot_id: Optional[str],
    llm_cfg: Optional[Mapping[str, Any]] = None,
    *,
    model_override: Optional[str] = None,
) -> LLMSettings:
    """Settings for ``bot_id``. ``llm_cfg`` is the bot's ``llm`` config
    block; it is loaded from the live config when not given."""
    if llm_cfg is None:
        llm_cfg = _bot_llm_config(bot_id) if bot_id else {}
    api_key = None
    if bot_id:
        from services.bot_config import get_decrypted_api_key

        api_key = _clean(get_decrypted_api_key(bot_id))
    endpoint = _clean(llm_cfg.get("api_endpoint")) or deployment_endpoint()
    if endpoint and not is_endpoint_allowed(endpoint):
        log.warning("LLM endpoint host %s for bot %s is not on %s; the bot won't answer",
                    endpoint_host(endpoint), bot_id, ALLOWLIST_ENV)
        endpoint = None
    return LLMSettings(
        model=_clean(model_override) or _clean(llm_cfg.get("model")) or deployment_model(),
        endpoint=endpoint,
        api_key=api_key or deployment_api_key(),
    )


def require(
    bot_id: Optional[str],
    llm_cfg: Optional[Mapping[str, Any]] = None,
    *,
    model_override: Optional[str] = None,
) -> LLMSettings:
    """Like ``resolve`` but raises ``LLMNotConfigured`` if anything is missing."""
    settings = resolve(bot_id, llm_cfg, model_override=model_override)
    if settings.missing:
        raise LLMNotConfigured(bot_id, settings.missing)
    return settings
