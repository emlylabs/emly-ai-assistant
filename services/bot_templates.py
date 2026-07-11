"""Template bots — pre-canned configs for one-click bot creation.

A new admin signs up, picks a template, and gets a working chat
without having to author topics / prompts / RAG knobs by hand. The
"blank" template preserves the empty-config flow for power users.

Templates are pure code (not DB rows) — easy to ship, version, and
type-check. Each template returns a `BotConfigV1`-compatible dict that
gets written into `bots.config_json` at create time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass
class TemplatePreview:
    """Lightweight summary the admin UI shows in the picker."""

    id: str
    name: str
    description: str
    preview_topics: List[str]


@dataclass
class Template:
    """A template: preview metadata + a function that materializes the
    full config_json blob given the bot's slug/name (for prompt fill-ins)."""

    preview: TemplatePreview
    build_config: Callable[[str, str], Dict[str, Any]]


def _support_faq_config(slug: str, name: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "topics": {
            "support": {
                "name": "support",
                "description": "Answer customer questions from the uploaded knowledge base.",
                "requires_rag": True,
                "skip_slot_filling": True,
                "slots": [],
                "prompts": {
                    "llm_response": (
                        "You are a helpful support assistant for "
                        f"{name}. Answer the user's question using only the "
                        "context below. If the answer isn't there, say you "
                        "don't know rather than guessing.\n\n"
                        "### Context starts ###\n{context}\n### Context ends ###\n\n"
                        "### User Question ###\n{user_input}\n\n"
                        "### History ###\n{history}\n"
                    ),
                },
            },
        },
        "global_prompts": {
            "welcome_message": f"Hi! I'm the {name} support assistant. Ask me anything.",
            "goodbye_message": "Thanks for chatting!",
            "error_message": "Sorry, something went wrong. Please try again.",
        },
        "c_forms_selected": [],
        "llm": {"model_type": "openai", "model": "gpt-4o-mini"},
        "rag": {"top_k": 5, "chunk_size": 2048, "chunk_overlap": 256, "enable_hybrid_search": False, "embedding_threshold": 0.20},
        "limits": {"max_file_size_mb": 50, "file_count_cap": 10000},
    }


def _lead_capture_config(slug: str, name: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "topics": {
            "lead_capture": {
                "name": "lead_capture",
                "description": "Collect contact details from interested visitors and book a callback.",
                "requires_rag": False,
                "skip_slot_filling": False,
                "slots": [
                    {"name": "full_name", "slot_type": "string", "required": True, "description": "the visitor's full name"},
                    {"name": "email", "slot_type": "string", "required": True, "description": "the visitor's email address"},
                    {"name": "use_case", "slot_type": "string", "required": True, "description": "what they want to use the product for"},
                ],
                "prompts": {
                    "llm_response": (
                        f"You are a sales assistant for {name}. The visitor "
                        "has shared the following details. Acknowledge them, "
                        "summarize their use case in one sentence, and tell "
                        "them a team member will reach out within one business "
                        "day.\n\n### Details ###\n{filled_slots}\n\n"
                        "### History ###\n{history}\n"
                    ),
                    "slot_question": "Could you share your {slot_name}?",
                },
            },
        },
        "global_prompts": {
            "welcome_message": f"Hi! I help connect visitors with the {name} team. Mind if I ask a few quick questions?",
            "slot_question": "Could you share your {slot_name}?",
            "error_message": "Sorry, something went wrong. Please try again.",
        },
        "c_forms_selected": [],
        "llm": {"model_type": "openai", "model": "gpt-4o-mini"},
        "rag": {"top_k": 5, "chunk_size": 2048, "chunk_overlap": 256, "enable_hybrid_search": False, "embedding_threshold": 0.20},
        "limits": {"max_file_size_mb": 50, "file_count_cap": 10000},
    }


def _internal_kb_config(slug: str, name: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "topics": {
            "knowledge_base": {
                "name": "knowledge_base",
                "description": "Answer internal questions from team-uploaded documentation.",
                "requires_rag": True,
                "skip_slot_filling": True,
                "slots": [],
                "prompts": {
                    "llm_response": (
                        f"You are an internal knowledge assistant for {name}. "
                        "Answer the team member's question using only the "
                        "context below. **Internal use only — do not share "
                        "answers outside the organization.** If the answer "
                        "isn't in the context, say so rather than guessing.\n\n"
                        "### Context starts ###\n{context}\n### Context ends ###\n\n"
                        "### Question ###\n{user_input}\n\n"
                        "### History ###\n{history}\n"
                    ),
                },
            },
        },
        "global_prompts": {
            "welcome_message": f"Hi! I'm the {name} internal knowledge assistant. Ask me about anything in our docs.",
            "error_message": "Sorry, something went wrong. Please try again.",
        },
        "c_forms_selected": [],
        "llm": {"model_type": "openai", "model": "gpt-4o-mini"},
        "rag": {"top_k": 5, "chunk_size": 2048, "chunk_overlap": 256, "enable_hybrid_search": False, "embedding_threshold": 0.20},
        "limits": {"max_file_size_mb": 50, "file_count_cap": 10000},
    }


def _blank_config(slug: str, name: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "topics": {},
        "global_prompts": {},
        "c_forms_selected": [],
        "llm": {"model_type": "openai"},
        "rag": {},
        "limits": {},
    }


_TEMPLATES: Dict[str, Template] = {
    "support-faq": Template(
        preview=TemplatePreview(
            id="support-faq",
            name="Support FAQ",
            description="Customer-facing support agent that answers from your uploaded docs.",
            preview_topics=["support"],
        ),
        build_config=_support_faq_config,
    ),
    "lead-capture": Template(
        preview=TemplatePreview(
            id="lead-capture",
            name="Lead capture",
            description="Slot-filling sales assistant that collects name, email, and use case.",
            preview_topics=["lead_capture"],
        ),
        build_config=_lead_capture_config,
    ),
    "internal-knowledge-base": Template(
        preview=TemplatePreview(
            id="internal-knowledge-base",
            name="Internal knowledge base",
            description="RAG-only assistant for team docs, with an internal-use disclaimer.",
            preview_topics=["knowledge_base"],
        ),
        build_config=_internal_kb_config,
    ),
    "blank": Template(
        preview=TemplatePreview(
            id="blank",
            name="Blank",
            description="Empty config — author your own topics and prompts.",
            preview_topics=[],
        ),
        build_config=_blank_config,
    ),
}


def list_templates() -> List[TemplatePreview]:
    return [t.preview for t in _TEMPLATES.values()]


def get_template_config(template_id: str, slug: str, name: str) -> Dict[str, Any]:
    """Render the full config_json for a template at create time.

    Raises ``KeyError`` for unknown ids; the route handler turns that
    into a 400.
    """
    if template_id not in _TEMPLATES:
        raise KeyError(template_id)
    return _TEMPLATES[template_id].build_config(slug, name)
