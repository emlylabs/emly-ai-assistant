"""Build a ``CapturedLead`` from a widget submission or filled chat slots."""
from __future__ import annotations

import json
from typing import Any, Optional

from crm.contracts import CapturedLead

_EMAIL_KEYS = ("email", "email_address", "work_email")
_NAME_KEYS = ("full_name", "name")
_PHONE_KEYS = ("phone", "phone_number", "mobile")
_USE_CASE_KEYS = ("use_case", "message", "notes", "requirement", "details")
# A stored visitor profile fills gaps only when this submission itself
# collected something. A button click must not open a CRM lead.
_CONTACT_KEYS = _EMAIL_KEYS + _NAME_KEYS + _PHONE_KEYS + ("first_name", "last_name", "use_case")


def looks_like_email(value: str) -> bool:
    if not value or len(value) > 320 or any(ch.isspace() for ch in value):
        return False
    local, sep, domain = value.partition("@")
    return bool(sep and local and domain and "." in domain and "@" not in domain)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _first(sources: list, keys: tuple) -> str:
    for source in sources:
        for key in keys:
            text = _text(source.get(key))
            if text:
                return text
    return ""


def _any_email(source: dict) -> str:
    for value in source.values():
        text = _text(value)
        if looks_like_email(text):
            return text
    return ""


def _has_contact_field(sources: list) -> bool:
    for source in sources:
        for key in _CONTACT_KEYS:
            if _text(source.get(key)):
                return True
    return False


def lead_from_slots(slots: Any, source: str) -> Optional[CapturedLead]:
    """A finished slot form. No usable email means there is nothing to match on."""
    if not isinstance(slots, dict):
        return None
    email = _first([slots], _EMAIL_KEYS)
    if not looks_like_email(email):
        for value in slots.values():
            text = _text(value)
            if looks_like_email(text):
                email = text
                break
    if not looks_like_email(email):
        return None
    name = _first([slots], _NAME_KEYS)
    if not name:
        name = " ".join(
            part for part in (_text(slots.get("first_name")), _text(slots.get("last_name"))) if part
        )
    return CapturedLead(
        email=email,
        name=name,
        phone=_first([slots], _PHONE_KEYS),
        use_case=_first([slots], _USE_CASE_KEYS),
        source=source,
    )


def lead_from_widget(payload: Any, user: Any = None) -> Optional[CapturedLead]:
    """A stored widget action. The email has to be on this submission."""
    if not isinstance(payload, dict):
        return None
    action = _as_dict(payload.get("action_payload"))
    sources = [action, payload]
    email = _first(sources, _EMAIL_KEYS)
    if not looks_like_email(email):
        # The form builder lets an admin key an email field anything
        # (``field``, ``contact``...). Take the first submitted value that
        # is an email, as chat slots do.
        email = _any_email(action) or email
    if not looks_like_email(email) and user is not None and _has_contact_field(sources):
        email = _text(getattr(user, "email", None))
    if not looks_like_email(email):
        return None
    name = _first(sources, _NAME_KEYS)
    if not name:
        first = _text(action.get("first_name")) or _text(payload.get("first_name"))
        last = _text(action.get("last_name")) or _text(payload.get("last_name"))
        if not first and not last and user is not None:
            # The form did not ask for a name. Use the visitor profile.
            first = _text(getattr(user, "first_name", None))
            last = _text(getattr(user, "last_name", None))
        name = " ".join(part for part in (first, last) if part)
    phone = _first(sources, _PHONE_KEYS)
    if not phone and user is not None:
        phone = _text(getattr(user, "phone", None))
    title = _text(payload.get("form_title")) or "widget"
    return CapturedLead(
        email=email,
        name=name,
        phone=phone,
        use_case=_first(sources, _USE_CASE_KEYS),
        source=f"widget:{title}"[:80],
    )
