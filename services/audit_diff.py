"""Before/after diffs for audit payloads (C7).

``config_diff`` flattens two config dicts to dotted paths and records only
the paths whose values differ. Anything whose path looks secret (api key,
token, password, ...) is recorded as changed without its values. Long
values are truncated and the number of recorded paths is capped so one
huge edit can't bloat the audit table.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_SECRET_PATH = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key|authorization|signing[_-]?key)",
    re.IGNORECASE,
)
MASK = "***"
MAX_CHANGES = 200
MAX_VALUE_CHARS = 500
_MISSING = object()


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(value, dict) and value:
        for key, child in value.items():
            _flatten(child, f"{prefix}.{key}" if prefix else str(key), out)
    else:
        out[prefix] = value


def _render(value: Any) -> Any:
    if value is _MISSING:
        return None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + f"… ({len(value)} chars)"
    return value


def is_secret_path(path: str) -> bool:
    return bool(_SECRET_PATH.search(path))


def config_diff(before: Optional[dict], after: Optional[dict]) -> dict[str, Any]:
    """Return ``{"changed": {path: {"before": .., "after": ..}}, "changed_count": n,
    "truncated": bool}`` for the paths that differ."""
    flat_before: dict[str, Any] = {}
    flat_after: dict[str, Any] = {}
    _flatten(before or {}, "", flat_before)
    _flatten(after or {}, "", flat_after)

    changed: dict[str, dict[str, Any]] = {}
    for path in sorted(set(flat_before) | set(flat_after)):
        if not path:
            continue
        old = flat_before.get(path, _MISSING)
        new = flat_after.get(path, _MISSING)
        if old == new:
            continue
        if is_secret_path(path):
            changed[path] = {"before": MASK if old is not _MISSING else None,
                             "after": MASK if new is not _MISSING else None}
        else:
            changed[path] = {"before": _render(old), "after": _render(new)}

    total = len(changed)
    if total > MAX_CHANGES:
        changed = dict(list(changed.items())[:MAX_CHANGES])
    return {"changed": changed, "changed_count": total, "truncated": total > MAX_CHANGES}
