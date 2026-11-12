"""Logging filter that redacts secrets out of log records.

Mounted on the root logger. Walks each record's ``args`` and ``msg`` and
strips known-sensitive substrings:

- ``Authorization: Bearer <...>``
- ``Cookie: ...`` / ``Set-Cookie: ...``
- token / access_token / refresh_token / id_token / password fields in
  dict-like reprs

The filter is conservative — it leaves the rest of the message intact and
only swaps out the secret-bearing portions. Operators still get readable
error messages without leaking the secret in the access log.
"""

from __future__ import annotations

import logging
import re

_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
     r"\1<redacted>"),
    (re.compile(r"(?i)(cookie\s*[:=]\s*)[^\s,;]+", re.IGNORECASE),
     r"\1<redacted>"),
    (re.compile(r"(?i)(set-cookie\s*[:=]\s*)[^\s,;]+", re.IGNORECASE),
     r"\1<redacted>"),
    # Field-style: 'access_token': 'eyJ...', 'password': 'hunter2', etc.
    (re.compile(r"""(['"]?)(access_token|refresh_token|id_token|password|client_secret|widget-token|code_verifier|code_challenge|emly_admin_session)\1\s*[:=]\s*['"]?[^'",}\s]+""", re.IGNORECASE),
     r"\1\2\1: <redacted>"),
    # Invite tokens travel in the URL path (``/admins/invites/<token>/accept``),
    # so the uvicorn access log would otherwise record them.
    (re.compile(r"(/invites/)[A-Za-z0-9_\-]{16,}"),
     r"\1<redacted>"),
]


def _redact(text: str) -> str:
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


class RedactSecretsFilter(logging.Filter):
    """``preserve_args=True`` redacts each string arg in place instead of
    collapsing the record to a pre-formatted message. Needed for uvicorn's
    ``AccessFormatter``, which unpacks ``record.args`` positionally."""

    def __init__(self, preserve_args: bool = False) -> None:
        super().__init__()
        self.preserve_args = preserve_args

    def filter(self, record: logging.LogRecord) -> bool:
        if self.preserve_args:
            try:
                if isinstance(record.msg, str):
                    record.msg = _redact(record.msg)
                if isinstance(record.args, tuple):
                    record.args = tuple(_redact(a) if isinstance(a, str) else a for a in record.args)
            except Exception:
                pass
            return True
        try:
            # Apply to the formatted message (after % args have been substituted).
            msg = record.getMessage()
            for pattern, replacement in _REPLACEMENTS:
                msg = pattern.sub(replacement, msg)
            # Stash the redacted message back; the logging handler emits this.
            record.msg = msg
            record.args = ()
        except Exception:
            # Filtering must never raise — fall back to the original record.
            pass
        return True


def install(level_logger: logging.Logger | None = None, *, preserve_args: bool = False) -> None:
    """Install the filter on the root logger (default) or a specified logger."""
    target = level_logger or logging.getLogger()
    # Idempotent: don't add the filter twice.
    if any(isinstance(f, RedactSecretsFilter) for f in target.filters):
        return
    target.addFilter(RedactSecretsFilter(preserve_args=preserve_args))
