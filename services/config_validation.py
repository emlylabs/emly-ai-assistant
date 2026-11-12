"""Server-side bot config validator.

Authoritative implementation of the rules surfaced in
``ui/lib/config-validation.ts``. The TypeScript module remains as a
client-side preview so the editor can show issues on every keystroke
without a round-trip; this module is what save / publish gates trust.

Rule families:
    R*  — config-internal rules (mirror the TS validator one-for-one).
    RT* — runtime-correctness rules. They look at state the client
          can't see (decrypted API key presence, deployment fallback
          envs, etc.) so they only run server-side.

Severity contract (consumed by ``routes/admin_bot_dashboard.py``):
    error    — blocks publish; blocks save when the workspace toggle is on
    warning  — surfaces in the workspace overview + picker tooltips
    info     — dismissible advisory only

The rule keys (``R1_empty_prompt_<topic>``, ``RT1_missing_api_key`` …)
must stay stable — UIs may dedupe / dismiss by key.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

log = logging.getLogger(__name__)


IssueSeverity = Literal["error", "warning", "info"]

# Tabs in the per-bot config editor. Mirrors the TS ``Issue.section`` union
# so the UI can route a click on an issue to the right tab.
IssueSection = Literal[
    "identity",
    "widget",
    "topics",
    "forms",
    "llm",
    "rag",
    "limits",
    "raw",
    "knowledge",
]


@dataclass
class Issue:
    """One validation finding. Serialized as JSON by the FastAPI layer."""

    severity: IssueSeverity
    key: str
    section: IssueSection
    message: str
    cta: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "severity": self.severity,
            "key": self.key,
            "section": self.section,
            "message": self.message,
        }
        if self.cta is not None:
            out["cta"] = self.cta
        return out


@dataclass
class ValidationContext:
    """Inputs the validator needs beyond the raw config blob.

    The validate-without-save endpoint fills as many of these as it can
    cheaply; the publish/save gates fill all of them because they're
    operating on a known bot.
    """

    bot_id: Optional[str] = None
    bot_slug: Optional[str] = None
    file_count: Optional[int] = None
    # Runtime probes (None = "didn't probe"; the rules skip rather than
    # firing a false positive when a probe is unavailable).
    has_bot_api_key: Optional[bool] = None
    has_deployment_api_key: Optional[bool] = None
    has_deployment_model: Optional[bool] = None
    has_deployment_endpoint: Optional[bool] = None
    known_model_names: Optional[set[str]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_obj(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _as_str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _as_num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v != v or v == float("inf") or v == float("-inf"):  # NaN / inf guard
            return None
        return float(v)
    return None


def _str_array(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    return [x for x in v if isinstance(x, str)]


# ---------------------------------------------------------------------------
# c_forms_selected walker
# ---------------------------------------------------------------------------
# We don't need full ``actionFromForm`` parity here — the validator only
# reads ``name``, kind-flags, ``trigger_code``, ``belongs_to``, ``alert_emails``,
# and the form-fields count. Keeping this minimal avoids duplicating the
# whole TS adapter.
@dataclass
class _ActionView:
    name: str
    kind: str  # "show_form" | "send_alert" | "open_link" | "advanced"
    trigger_kind: str  # "bot_decides" | "start_chat" | "starter_message" | "bot_button_link" | "bot_button_card"
    trigger_code: Optional[str]
    belongs_to: List[str]
    form_preset: Optional[str]  # "auth_form" | "custom" | None
    form_field_count: int
    has_alert: bool
    alert_recipients: List[str]


def _has_form_fields(form_schema: Dict[str, Any]) -> bool:
    form = form_schema.get("form")
    if not isinstance(form, dict):
        return False
    return any(k != "submit" for k in form.keys())


def _form_field_count(form_schema: Dict[str, Any]) -> int:
    form = form_schema.get("form")
    if not isinstance(form, dict):
        return 0
    return sum(1 for k in form.keys() if k != "submit")


def _has_alert(trigger: Dict[str, Any]) -> bool:
    if bool(trigger.get("email_alert")):
        return True
    emails = trigger.get("alert_emails")
    if isinstance(emails, str) and emails.strip():
        return True
    if isinstance(emails, list) and any(isinstance(e, str) and e.strip() for e in emails):
        return True
    return False


def _has_link(trigger: Dict[str, Any]) -> bool:
    if bool(trigger.get("utm_tracking")):
        return True
    q = trigger.get("utm_tracking_query")
    if isinstance(q, dict):
        ct_url = q.get("ct_url")
        return isinstance(ct_url, str) and bool(ct_url)
    return False


def _infer_kind(form_schema: Dict[str, Any], trigger: Dict[str, Any]) -> str:
    flags = [
        _has_form_fields(form_schema),
        _has_alert(trigger),
        _has_link(trigger),
    ]
    set_count = sum(1 for f in flags if f)
    if set_count > 1:
        return "advanced"
    if flags[1]:
        return "send_alert"
    if flags[2]:
        return "open_link"
    return "show_form"


def _parse_trigger_kind(value: Any) -> str:
    if not isinstance(value, str):
        return "bot_decides"
    mapping = {
        "START_CHAT": "start_chat",
        "STARTER_MESSAGES": "starter_message",
        "BUTTON_ON_LINK": "bot_button_link",
        "BUTTON_ON_CARD": "bot_button_card",
        "PROMPT": "bot_decides",
    }
    return mapping.get(value, "bot_decides")


def _walk_actions(c_forms_selected: Any) -> List[_ActionView]:
    out: List[_ActionView] = []
    if not isinstance(c_forms_selected, list):
        return out
    for entry in c_forms_selected:
        if not isinstance(entry, dict):
            continue
        for name, body in entry.items():
            if not isinstance(body, dict):
                continue
            form_schema = _as_obj(body.get("form_schema"))
            trigger = _as_obj(body.get("trigger"))
            kind = _infer_kind(form_schema, trigger)
            trigger_kind = _parse_trigger_kind(trigger.get("value"))
            # ``trigger_code`` only carries semantic weight when the trigger
            # is bot_decides — that's the path the router uses to fire an
            # action. Other trigger kinds don't dedupe on it.
            trigger_code: Optional[str] = None
            if trigger_kind == "bot_decides":
                raw_code = trigger.get("trigger_code")
                if isinstance(raw_code, str) and raw_code:
                    trigger_code = raw_code
            recipients: List[str] = []
            emails = trigger.get("alert_emails")
            if isinstance(emails, str):
                recipients = [e.strip() for e in emails.split(",") if e.strip()]
            elif isinstance(emails, list):
                recipients = [e.strip() for e in emails if isinstance(e, str) and e.strip()]
            form_preset: Optional[str] = None
            if _has_form_fields(form_schema) or form_schema.get("id") == "auth_form":
                form_preset = "auth_form" if form_schema.get("id") == "auth_form" else "custom"
            out.append(
                _ActionView(
                    name=str(name),
                    kind=kind,
                    trigger_kind=trigger_kind,
                    trigger_code=trigger_code,
                    belongs_to=_str_array(trigger.get("belongs_to")),
                    form_preset=form_preset,
                    form_field_count=_form_field_count(form_schema),
                    has_alert=_has_alert(trigger),
                    alert_recipients=recipients,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def validate_config(config: Dict[str, Any], ctx: Optional[ValidationContext] = None) -> List[Issue]:
    """Run every rule against ``config``. Returns issues in stable order.

    ``ctx`` is optional so the validate-without-save endpoint can call us
    without resolving any DB state. Rules that need ``ctx`` skip when their
    required field is ``None``.
    """
    ctx = ctx or ValidationContext()
    issues: List[Issue] = []
    issues.extend(_run_config_rules(config, ctx))
    issues.extend(_run_runtime_rules(config, ctx))
    return issues


def group_issues(issues: List[Issue]) -> Dict[str, List[Issue]]:
    out: Dict[str, List[Issue]] = {"error": [], "warning": [], "info": []}
    for i in issues:
        out[i.severity].append(i)
    return out


def count_by_severity(issues: List[Issue]) -> Dict[str, int]:
    out = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        out[i.severity] += 1
    return out


# ---------------------------------------------------------------------------
# R* rules (config-internal — mirror ui/lib/config-validation.ts one-for-one)
# ---------------------------------------------------------------------------
def _run_config_rules(config: Dict[str, Any], ctx: ValidationContext) -> List[Issue]:
    issues: List[Issue] = []
    topics = _as_obj(config.get("topics"))
    topic_names = list(topics.keys())
    global_prompts = _as_obj(config.get("global_prompts"))
    limits = _as_obj(config.get("limits"))
    rag = _as_obj(config.get("rag"))

    # R13 — empty bot
    if not topic_names:
        issues.append(Issue(
            severity="info",
            key="R13_empty_bot",
            section="topics",
            message="This bot has no skills yet. Add at least one to enable chat.",
        ))

    seen_topic_names: set[str] = set()
    for name in topic_names:
        t = _as_obj(topics.get(name))
        requires_rag = bool(t.get("requires_rag"))
        skip_slot_filling = bool(t.get("skip_slot_filling"))
        slots = t.get("slots") if isinstance(t.get("slots"), list) else []
        prompts = _as_obj(t.get("prompts"))
        llm_response = _as_str(prompts.get("llm_response")).strip()

        # R1 — empty prompt
        if not llm_response:
            issues.append(Issue(
                severity="error",
                key=f"R1_empty_prompt_{name}",
                section="topics",
                message=f'Skill "{name}" has no response prompt. The bot will fall back to a generic reply.',
            ))

        # R2 — RAG enabled but no files
        if requires_rag and ctx.file_count == 0:
            cta: Optional[Dict[str, str]] = None
            if ctx.bot_slug:
                cta = {"label": "Upload files", "href": f"/bots/{ctx.bot_slug}/files"}
            issues.append(Issue(
                severity="error",
                key=f"R2_rag_no_files_{name}",
                section="topics",
                message=f'Skill "{name}" answers from your knowledge base, but no files are uploaded yet.',
                cta=cta,
            ))

        # R3 — collect info but 0 fields
        if not skip_slot_filling and not slots:
            issues.append(Issue(
                severity="warning",
                key=f"R3_no_fields_{name}",
                section="topics",
                message=f'Skill "{name}" is set to collect information but has no fields defined.',
            ))

        # R4 — collects fields but prompt missing {filled_slots}
        if slots and llm_response and "{filled_slots}" not in llm_response:
            issues.append(Issue(
                severity="warning",
                key=f"R4_no_filled_slots_{name}",
                section="topics",
                message=f'Skill "{name}" collects fields but its prompt doesn\'t reference {{filled_slots}} (will be appended automatically).',
            ))

        # R9 — required slot in skip_slot_filling skill
        if skip_slot_filling:
            required_slots = [s for s in slots if isinstance(s, dict) and s.get("required")]
            if required_slots:
                issues.append(Issue(
                    severity="warning",
                    key=f"R9_skip_slot_with_required_{name}",
                    section="topics",
                    message=f'Skill "{name}" has required fields but is set to skip information collection — those fields won\'t be asked.',
                ))

        # R10 — duplicate skill names (shouldn't happen via the UI; possible via Raw JSON)
        if name in seen_topic_names:
            issues.append(Issue(
                severity="error",
                key=f"R10_dup_skill_{name}",
                section="topics",
                message=f'Two skills share the name "{name}". The router will only see one.',
            ))
        seen_topic_names.add(name)

    # Action-level checks (R5, R6, R7, R10b, R11)
    actions = _walk_actions(config.get("c_forms_selected"))
    known_skills = set(topic_names)
    trigger_codes: Dict[str, int] = {}
    action_names: Dict[str, int] = {}
    for a in actions:
        action_names[a.name] = action_names.get(a.name, 0) + 1
        if a.trigger_kind == "bot_decides" and a.trigger_code:
            trigger_codes[a.trigger_code] = trigger_codes.get(a.trigger_code, 0) + 1

        # R5 — belongs_to references unknown skill
        for s in a.belongs_to:
            if s not in known_skills:
                issues.append(Issue(
                    severity="warning",
                    key=f"R5_action_orphan_{a.name}_{s}",
                    section="forms",
                    message=f'Action "{a.name}" is attached to skill "{s}", which doesn\'t exist.',
                ))

        # R6 — show_form with 0 fields (custom preset only)
        if a.kind == "show_form" and a.form_preset == "custom" and a.form_field_count == 0:
            issues.append(Issue(
                severity="warning",
                key=f"R6_form_no_fields_{a.name}",
                section="forms",
                message=f'Action "{a.name}" shows a form but has no fields.',
            ))

        # R7 — alert with no recipients. Mirrors the TS validator's
        # ``if (a.alert && a.alert.recipients.length === 0)``: fire iff the
        # trigger carries any alert config but no recipients resolved.
        if a.has_alert and not a.alert_recipients:
            issues.append(Issue(
                severity="warning",
                key=f"R7_alert_no_recipients_{a.name}",
                section="forms",
                message=f'Action "{a.name}" sends an alert but has no recipients.',
            ))

    # R11 — duplicate trigger codes
    for code, count in trigger_codes.items():
        if count > 1:
            issues.append(Issue(
                severity="error",
                key=f"R11_dup_trigger_{code}",
                section="forms",
                message=f'Trigger code "{code}" is used by {count} actions. The bot will only fire one.',
            ))

    # R10b — duplicate action names
    for n, count in action_names.items():
        if count > 1:
            issues.append(Issue(
                severity="error",
                key=f"R10b_dup_action_{n}",
                section="forms",
                message=f'Two actions share the name "{n}". Saved config keeps only the last.',
            ))

    # R8 — aggressive embedding threshold
    threshold = _as_num(rag.get("embedding_threshold"))
    if threshold is not None and threshold > 0.5:
        issues.append(Issue(
            severity="info",
            key="R8_high_threshold",
            section="rag",
            message=f"Embedding threshold {threshold} is high — many relevant hits may be filtered out.",
        ))

    # R12 — daily token cap without per-user rate limit
    if (
        _as_num(limits.get("daily_token_cap")) is not None
        and _as_num(limits.get("messages_per_minute_per_user")) is None
    ):
        issues.append(Issue(
            severity="warning",
            key="R12_token_cap_no_rate_limit",
            section="limits",
            message="Daily token cap is set but no per-user rate limit. A single visitor could burn the cap quickly.",
        ))

    # R14 — wildcard origin
    allowed_origins = limits.get("widget_allowed_origins") if isinstance(limits.get("widget_allowed_origins"), list) else []
    if "*" in allowed_origins:
        issues.append(Issue(
            severity="info",
            key="R14_wildcard_origin",
            section="limits",
            message="Widget allows embedding on any origin (`*`). Lock this down before going to production.",
        ))

    # R15 — blank welcome + no skills
    if not _as_str(global_prompts.get("welcome_message")).strip() and not topic_names:
        issues.append(Issue(
            severity="warning",
            key="R15_blank_welcome_empty_bot",
            section="identity",
            message="Visitors will land on a blank chat with no welcome message and no skills.",
        ))

    return issues


# ---------------------------------------------------------------------------
# RT* rules (runtime correctness — only server can answer these)
# ---------------------------------------------------------------------------
def _run_runtime_rules(config: Dict[str, Any], ctx: ValidationContext) -> List[Issue]:
    issues: List[Issue] = []
    llm = _as_obj(config.get("llm"))
    limits = _as_obj(config.get("limits"))

    # RT1 — no per-bot API key, no deployment fallback. Skip when we
    # weren't given either probe (the validate-without-save endpoint may
    # call us with neither set; that's an "unknown" not a "broken").
    if ctx.has_bot_api_key is not None and ctx.has_deployment_api_key is not None:
        if not ctx.has_bot_api_key and not ctx.has_deployment_api_key:
            issues.append(Issue(
                severity="error",
                key="RT1_missing_api_key",
                section="llm",
                message="No LLM API key set for this bot and no deployment-wide fallback configured. Chat will fail with a 4xx the moment a visitor sends a message.",
            ))

    # RT4 / RT5 — no model or endpoint for this bot and no deployment
    # default (MODEL / OPENAI_BASE_URL). There is no built-in fallback, so
    # the bot refuses to answer (services/llm_settings.py).
    if ctx.has_deployment_model is not None and not _as_str(llm.get("model")).strip() \
            and not ctx.has_deployment_model:
        issues.append(Issue(
            severity="error",
            key="RT4_missing_model",
            section="llm",
            message="No model set for this bot and no deployment default. The bot will refuse to answer until a model is chosen.",
        ))
    if ctx.has_deployment_endpoint is not None and not _as_str(llm.get("api_endpoint")).strip() \
            and not ctx.has_deployment_endpoint:
        issues.append(Issue(
            severity="error",
            key="RT5_missing_endpoint",
            section="llm",
            message="No API endpoint set for this bot and no deployment default. The bot will refuse to answer until an endpoint is set.",
        ))

    # RT2 — no widget origins configured. Empty is the default for new
    # bots and means only this app's own pages (preview / test link) can
    # load the widget; a customer-site embed is refused until listed.
    allowed_origins = limits.get("widget_allowed_origins") if isinstance(limits.get("widget_allowed_origins"), list) else []
    if len(allowed_origins) == 0:
        issues.append(Issue(
            severity="warning",
            key="RT2_widget_origins_empty",
            section="limits",
            message="No widget origins are allowed yet, so the widget only works on this app's own preview and test pages. Add the sites you embed it on (e.g. https://www.example.com).",
        ))

    # RT3 — model name not in the known list. We only fire when the
    # caller passed a known-models set; otherwise we can't tell.
    model = _as_str(llm.get("model")).strip()
    if model and ctx.known_model_names is not None and model not in ctx.known_model_names:
        issues.append(Issue(
            severity="warning",
            key="RT3_unrecognized_model",
            section="llm",
            message=f'Model "{model}" isn\'t in the provider\'s known list. The chat endpoint will surface the provider error when a visitor hits it.',
        ))

    return issues


# ---------------------------------------------------------------------------
# Probes — called by routes/admin_bot_dashboard.py when it knows the bot
# ---------------------------------------------------------------------------
def has_deployment_api_key() -> bool:
    """True iff a deployment-wide LLM key (``OPENAI_API_KEY``) is set.
    See ``services/llm_settings.py`` for how fallbacks apply."""
    from services.llm_settings import deployment_api_key
    return deployment_api_key() is not None


def has_deployment_model() -> bool:
    from services.llm_settings import deployment_model
    return deployment_model() is not None


def has_deployment_endpoint() -> bool:
    from services.llm_settings import deployment_endpoint
    return deployment_endpoint() is not None


def probe_bot_api_key(bot_id: str) -> bool:
    """True iff the bot has an encrypted API key set. Importing the bots
    model lazily keeps this module side-effect-free at import time."""
    try:
        from models.bots import Bots
        bot = Bots.get_by_id(bot_id)
        return bool(bot and bot.api_key_encrypted)
    except Exception:
        log.exception("probe_bot_api_key failed bot=%s", bot_id)
        return False


__all__ = [
    "Issue",
    "IssueSection",
    "IssueSeverity",
    "ValidationContext",
    "count_by_severity",
    "group_issues",
    "has_deployment_api_key",
    "has_deployment_endpoint",
    "has_deployment_model",
    "probe_bot_api_key",
    "validate_config",
]
