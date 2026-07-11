// Three skill types replace today's two-checkbox combo
// (`requires_rag` × `skip_slot_filling`) with a name + description picker.
//
// The fourth combination (RAG + slot-filling) maps to "advanced".
//
// `inferType` reads the existing flags so a topic created via the legacy
// editor opens correctly under the new picker. `applyType` mutates the
// flags to match a chosen type.

export type SkillType = "answer_from_knowledge" | "collect_info" | "free_chat" | "advanced";

export const SKILL_TYPE_OPTIONS: {
  type: SkillType;
  label: string;
  description: string;
  example: string;
}[] = [
  {
    type: "answer_from_knowledge",
    label: "Answer from knowledge",
    description: "Bot looks up answers in your uploaded files. No questions asked first.",
    example: "Customer support FAQ over a docs site.",
  },
  {
    type: "collect_info",
    label: "Collect information",
    description: "Bot asks the visitor for things you need (name, email, …) before responding.",
    example: "Lead capture: collect name + email + use case, then confirm a callback.",
  },
  {
    type: "free_chat",
    label: "Free conversation",
    description: "Bot just chats based on the prompt you write. No knowledge lookups, no field collection.",
    example: "A greeter that recommends pages on your site.",
  },
];

export type SkillTypeFlags = {
  requires_rag: boolean;
  skip_slot_filling: boolean;
};

export function inferType(flags: Partial<SkillTypeFlags>): SkillType {
  const ragOn = Boolean(flags.requires_rag);
  const slotOn = !flags.skip_slot_filling;
  if (ragOn && slotOn) return "advanced";
  if (ragOn) return "answer_from_knowledge";
  if (slotOn) return "collect_info";
  return "free_chat";
}

export function applyType(type: SkillType): SkillTypeFlags {
  switch (type) {
    case "answer_from_knowledge":
      return { requires_rag: true, skip_slot_filling: true };
    case "collect_info":
      return { requires_rag: false, skip_slot_filling: false };
    case "free_chat":
      return { requires_rag: false, skip_slot_filling: true };
    case "advanced":
      return { requires_rag: true, skip_slot_filling: false };
  }
}
