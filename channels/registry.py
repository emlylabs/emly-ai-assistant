"""Module-level registry of channel adapters.

Adapters self-register at import time:

    from channels.registry import register
    register(MyAdapter())

The dispatcher looks adapters up by ``type``; the admin /types route
enumerates them so the UI can render type-specific install forms.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from channels.base import ChannelAdapter

log = logging.getLogger(__name__)

_REGISTRY: Dict[str, ChannelAdapter] = {}


def register(adapter: ChannelAdapter) -> None:
    if not adapter.type:
        raise ValueError("Adapter must declare a non-empty `type`")
    if adapter.type in _REGISTRY:
        log.warning("Re-registering channel adapter type=%s", adapter.type)
    _REGISTRY[adapter.type] = adapter


def get(channel_type: str) -> Optional[ChannelAdapter]:
    return _REGISTRY.get(channel_type)


def list_types() -> List[str]:
    return sorted(_REGISTRY.keys())


def list_oauth_types() -> List[str]:
    return sorted(t for t, a in _REGISTRY.items() if a.auth.requires_oauth_callback)


def all_adapters() -> List[ChannelAdapter]:
    return list(_REGISTRY.values())
