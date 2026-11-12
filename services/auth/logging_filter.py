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


# -------- process-wide installation --------
#
# A filter added to a *logger* only sees records created on that exact
# logger; records from child loggers (``routes.*``, ``services.*``) propagate
# to the parent's *handlers* without passing the parent's filters. So a
# root-logger filter misses almost every application log line. Installing
# redaction in the LogRecord factory instead covers every record from every
# logger, whichever handler (current or added later) ends up emitting it.

# Loggers whose formatters read ``record.args`` positionally; redact each arg
# in place for these instead of collapsing to a pre-formatted message.
_PRESERVE_ARGS_LOGGERS = ("uvicorn.access",)


def redact_record(record: logging.LogRecord) -> None:
    """Redact secrets in ``record`` in place. Records without secrets are
    left untouched (args included), so other formatters see them as-is."""
    try:
        message = record.getMessage()
        if _redact(message) == message:
            return
        if record.name.startswith(_PRESERVE_ARGS_LOGGERS):
            RedactSecretsFilter(preserve_args=True).filter(record)
        else:
            record.msg = _redact(message)
            record.args = ()
    except Exception:
        # Redaction must never break logging; leave the record as it was.
        pass


def install_global() -> None:
    """Redact secrets from every log record in the process. Idempotent."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_emly_redacting", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        redact_record(record)
        return record

    factory._emly_redacting = True  # type: ignore[attr-defined]
    factory._emly_previous = previous  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)
