"""The capture text both CRMs store, and the name split Zoho requires."""
from __future__ import annotations

from crm.contracts import CapturedLead

_DETAIL_LIMIT = 30000


def format_capture(lead: CapturedLead) -> str:
    """One block of text: when, who, and why. The same words go to both CRMs."""
    when = lead.captured_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Captured {when}"]
    if lead.name:
        lines.append(f"Name: {lead.name}")
    lines.append(f"Email: {lead.email}")
    if lead.phone:
        lines.append(f"Phone: {lead.phone}")
    if lead.use_case:
        lines.append(f"Use case: {lead.use_case}")
    if lead.source:
        lines.append(f"Source: {lead.source}")
    return "\n".join(lines)


def clip_details(text: str, limit: int = _DETAIL_LIMIT) -> str:
    """Keep the newest part of a description that would otherwise be huge."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def split_name(name: str, email: str) -> tuple[str, str]:
    """Zoho requires ``Last_Name``. A single word is the last name.

    With no name at all, the mailbox of the email stands in, so the create
    call still has the required field.
    """
    parts = [part for part in (name or "").split() if part]
    if len(parts) >= 2:
        return parts[0][:40], " ".join(parts[1:])[:80]
    if len(parts) == 1:
        return "", parts[0][:80]
    local = (email.split("@", 1)[0] if email else "").strip() or "Lead"
    return "", local[:80]
