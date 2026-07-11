// Starter prompts surfaced in the prompt editor's "Insert from library"
// menu. These are mirrored from `services/bot_templates.py` so admins
// who skipped the first-run wizard can still drop in a working prompt.
//
// Drift mitigation: each snippet's `mirror` field references the
// backend template's name. When updating templates, update both files
// in the same commit.

export type PromptSnippet = {
  id: string;
  label: string;
  description: string;
  /** Source of truth in `services/bot_templates.py`. */
  mirror: string;
  body: string;
};

export const PROMPT_SNIPPETS: PromptSnippet[] = [
  {
    id: "support_faq",
    label: "Helpful support assistant",
    description: "Answers from your knowledge base; says it doesn't know rather than guessing.",
    mirror: "_support_faq_config",
    body: [
      "You are a helpful support assistant. Answer the user's question using only the",
      "context below. If the answer isn't there, say you don't know rather than guessing.",
      "",
      "### Context starts ###",
      "{context}",
      "### Context ends ###",
      "",
      "### User Question ###",
      "{user_input}",
      "",
      "### History ###",
      "{history}",
    ].join("\n"),
  },
  {
    id: "lead_capture",
    label: "Sales lead capture",
    description: "Acknowledges visitor details, summarizes their use case, promises a callback.",
    mirror: "_lead_capture_config",
    body: [
      "You are a sales assistant. The visitor has shared the following details. Acknowledge",
      "them, summarize their use case in one sentence, and tell them a team member will",
      "reach out within one business day.",
      "",
      "### Details ###",
      "{filled_slots}",
      "",
      "### History ###",
      "{history}",
    ].join("\n"),
  },
  {
    id: "internal_kb",
    label: "Internal-only knowledge assistant",
    description: "Adds a confidentiality reminder; answers only from uploaded docs.",
    mirror: "_internal_kb_config",
    body: [
      "You are an internal knowledge assistant. Answer the team member's question using only",
      "the context below. **Internal use only — do not share answers outside the",
      "organization.** If the answer isn't in the context, say so rather than guessing.",
      "",
      "### Context starts ###",
      "{context}",
      "### Context ends ###",
      "",
      "### Question ###",
      "{user_input}",
      "",
      "### History ###",
      "{history}",
    ].join("\n"),
  },
];
