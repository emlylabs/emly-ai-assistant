"use client";

import { SKILL_TYPE_OPTIONS, SkillType } from "@/lib/skill-types";

// Three-card picker that replaces the two-checkbox combo
// (`requires_rag`, `skip_slot_filling`). Renders a fourth "advanced"
// option only when the topic is already in that combination.

type Props = {
  value: SkillType;
  onChange: (next: SkillType) => void;
  disabled?: boolean;
};

export default function SkillTypePicker({ value, onChange, disabled }: Props) {
  const options = value === "advanced" ? [...SKILL_TYPE_OPTIONS, ADVANCED_OPTION] : SKILL_TYPE_OPTIONS;
  return (
    <div role="radiogroup" aria-label="Skill type" style={{ display: "grid", gap: 8 }}>
      {options.map((opt) => {
        const active = value === opt.type;
        return (
          <button
            key={opt.type}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(opt.type)}
            disabled={disabled}
            style={{
              textAlign: "left",
              padding: 12,
              borderRadius: 6,
              border: `1px solid ${active ? "var(--accent)" : "var(--panel-border)"}`,
              background: active ? "var(--panel-bg)" : "transparent",
              cursor: disabled ? "default" : "pointer",
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 500 }}>{opt.label}</div>
            <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{opt.description}</div>
            <div className="muted" style={{ fontSize: 11, marginTop: 4, fontStyle: "italic" }}>
              Example: {opt.example}
            </div>
          </button>
        );
      })}
    </div>
  );
}

const ADVANCED_OPTION = {
  type: "advanced" as SkillType,
  label: "Advanced (combine knowledge with field collection)",
  description: "Bot answers from your knowledge base AND collects fields. Rare combination.",
  example: "Pre-sales bot that captures email then answers from product docs.",
};
