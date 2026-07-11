// User-facing label map. The persisted JSON keeps `topics`, `slots`,
// `c_forms_selected` etc. unchanged; only the UI surfaces these names.
//
// One file so future reviewers can find the mapping in one place. Keep
// it sorted by JSON key so diffs are clean.

export const SKILL_LABEL = "Skill" as const;
export const SKILL_LABEL_PLURAL = "Skills" as const;
export const FIELD_LABEL = "Field" as const;
export const FIELD_LABEL_PLURAL = "Fields" as const;
export const ACTION_LABEL = "Action" as const;
export const ACTION_LABEL_PLURAL = "Actions" as const;

/** JSON key → friendly name. Used in tooltips and error messages. */
export const JSON_KEY_LABELS: Record<string, string> = {
  topics: "Skills",
  slots: "Fields",
  c_forms_selected: "Actions",
  global_prompts: "Bot voice & messages",
  requires_rag: "Answer from knowledge",
  skip_slot_filling: "Skill type",
  belongs_to: "Attached skills",
  trigger_code: "Decision token (auto-generated)",
  trigger_prompt: "Decision rule",
};

/** Human label for a router-facing topic description. */
export function whenToUseLabel(): string {
  return "When to use this skill";
}

export const SKILL_TYPE_LABELS = {
  answer_from_knowledge: "Answer from knowledge",
  collect_info: "Collect information",
  free_chat: "Free conversation",
  advanced: "Advanced (custom mix)",
} as const;
