"""422 gates for writing a bot config.

Every path that stores a ``bots.config_json`` must run the same checks:
the config editor's PUT, draft publish and history restore, and bot
creation from a built-in template, a workspace template or a duplicate.
A config that skips them can carry a key ``BotConfig`` forbids, and
``boot_validate_all_configs`` then refuses to start the app.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from pydantic import ValidationError as PydanticValidationError

from services.bot_config import parse_raw_config
from services.config_validation import blocking_format_issues, validate_config


def invalid_config_error(exc: PydanticValidationError) -> HTTPException:
    """422 for a config the schema rejects (instead of a 500)."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": "invalid_config",
            "message": "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]
            ),
        },
    )


def raise_on_format_errors(config: Dict[str, Any], before: Optional[Dict[str, Any]]) -> None:
    """422 on malformed field values (bad email / URL / colour,
    out-of-range numbers) that this save introduces or changes. These are
    never a deliberate choice, so they block regardless of the workspace
    save gate and of ``force``. A value already in ``before`` (the live
    config) doesn't block, so one stored before a rule existed can't stop
    every unrelated save."""
    format_issues = blocking_format_issues(validate_config(config), config, before)
    if format_issues:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "validation_failed",
                "message": (
                    f"Save blocked — {len(format_issues)} invalid value"
                    f"{'s' if len(format_issues) != 1 else ''}: "
                    + " ".join(i.message for i in format_issues)
                ),
                "issues": [i.to_dict() for i in format_issues],
                "can_force": False,
            },
        )


def check_new_config(config: Dict[str, Any], *, copied_from: Optional[Dict[str, Any]] = None) -> None:
    """422 unless ``config`` can be stored as a new bot's (or template's)
    config: it must parse under ``BotConfig`` and carry no blocking format
    error.

    ``copied_from`` is the config this one was copied from (a duplicated
    bot, or a bot snapshotted into a template). Values unchanged from it
    don't block on format, matching the editor: a value stored before a
    rule existed shouldn't stop a copy. Schema errors always block.
    """
    try:
        parse_raw_config(config)
    except PydanticValidationError as exc:
        raise invalid_config_error(exc)
    raise_on_format_errors(config, copied_from)
