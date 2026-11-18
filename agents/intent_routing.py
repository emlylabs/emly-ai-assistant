"""Keep a contact-collection topic from starting on an unrelated question.

A sales bot's lead form asks "Could you share your full name?" as soon as
that topic is selected. An out-of-scope question ("do you sell air
conditioners?") must be answered, or declined with an offer to involve the
sales team, and must not open the form. The form starts only when the
visitor asks for a quote, a callback, or to be contacted.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

OUT_OF_SCOPE_REPLY = (
    "That's outside what I can help with here. I cover the products and plans "
    "this assistant is set up for. If you'd like a quote or a callback, just "
    "say so and I'll take your details for the sales team."
)

_CONTACT_REQUEST = re.compile(
    r"\b("
    r"quotes?|callbacks?|call\s+me|"
    r"contact(?:\s+me|\s+us)?|"
    r"get\s+in\s+touch|reach\s+out|"
    r"book\s+a\s+(?:demo|call|meeting)|"
    r"talk\s+to\s+(?:sales|someone|a\s+(?:person|human|rep))|"
    r"speak\s+(?:to|with)\s+(?:sales|someone|a\s+(?:person|human|rep))|"
    r"connect\s+me|sign\s+me\s+up|"
    r"my\s+(?:name|email|number)\s+is|"
    r"here(?:'s| is)\s+my\s+(?:name|email|number)"
    r")\b",
    re.IGNORECASE,
)


def wants_to_be_contacted(text: str) -> bool:
    return bool(_CONTACT_REQUEST.search(text or ""))


def collects_contact_details(topic: Any) -> bool:
    """A topic that will ask for required details before it answers."""
    if topic is None or getattr(topic, "skip_slot_filling", False):
        return False
    return any(getattr(slot, "required", False) for slot in (getattr(topic, "slots", None) or []))


def prefer_answer_topic(
    chosen: str,
    user_input: str,
    topics: Mapping[str, Any],
    available: Sequence[str],
) -> str:
    """Return ``chosen`` unless it would open a contact form the visitor didn't ask for.

    Then return the first available topic that answers instead of collecting
    details. If every topic collects details, ``chosen`` is unchanged and the
    topic agent declines to open the form.
    """
    cfg = topics.get(chosen) if topics else None
    if cfg is None or not collects_contact_details(cfg) or wants_to_be_contacted(user_input):
        return chosen
    for name in available:
        other = topics.get(name)
        if other is not None and not collects_contact_details(other):
            return name
    return chosen
