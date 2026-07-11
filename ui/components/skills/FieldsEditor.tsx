"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Copy, Trash2 } from "lucide-react";

// Vertical-card slot/field editor for the Skills tab.
//
// Replaces the cramped row-based SlotsEditor. Surfaces the existing
// `SlotDefinition.prompt_template` (per-field custom question) which
// is part of the data model but was never editable in the UI before.

const SLOT_TYPES: { value: string; label: string; hint: string }[] = [
  { value: "string", label: "Text", hint: "Free-text answer" },
  { value: "email", label: "Email", hint: "An email address" },
  { value: "phone", label: "Phone", hint: "A phone number" },
  { value: "number", label: "Number", hint: "A numeric value" },
  { value: "date", label: "Date", hint: "A calendar date" },
  { value: "boolean", label: "Yes / No", hint: "Yes-or-no answer" },
];

export type SlotDef = {
  name: string;
  slot_type?: string;
  required?: boolean;
  description?: string;
  /** Per-field custom question. Backed by `SlotDefinition.prompt_template`. */
  prompt_template?: string;
};

type Props = {
  slots: SlotDef[];
  onChange: (next: SlotDef[]) => void;
  disabled?: boolean;
};

export default function FieldsEditor({ slots, onChange, disabled }: Props) {
  const requiredCount = slots.filter((s) => s.required).length;

  function update(i: number, fn: (s: SlotDef) => SlotDef) {
    onChange(slots.map((s, idx) => (idx === i ? fn(s) : s)));
  }
  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= slots.length) return;
    const next = slots.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  }
  function remove(i: number) {
    onChange(slots.filter((_, idx) => idx !== i));
  }
  function duplicate(i: number) {
    const s = slots[i];
    const usedNames = new Set(slots.map((x) => x.name));
    let name = `${s.name}_copy`;
    let n = 1;
    while (usedNames.has(name)) {
      n += 1;
      name = `${s.name}_copy_${n}`;
    }
    const next = slots.slice();
    next.splice(i + 1, 0, { ...s, name });
    onChange(next);
  }
  function add() {
    const usedNames = new Set(slots.map((s) => s.name));
    let name = "field";
    let n = 1;
    while (usedNames.has(name)) {
      n += 1;
      name = `field_${n}`;
    }
    onChange([...slots, { name, slot_type: "string", required: true, description: "" }]);
  }
  function rename(i: number, newName: string) {
    const trimmed = newName.trim();
    if (!trimmed) return;
    if (slots.some((s, idx) => idx !== i && s.name === trimmed)) return;
    update(i, (s) => ({ ...s, name: trimmed }));
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div>
          <strong>Information to collect</strong>
          <span className="muted" style={{ fontSize: 12, marginLeft: 8 }}>
            {slots.length} {slots.length === 1 ? "field" : "fields"}
            {requiredCount > 0 ? ` · ${requiredCount} required` : ""}
          </span>
        </div>
      </div>

      {slots.length === 0 && (
        <p className="muted" style={{ fontSize: 13, margin: "8px 0" }}>
          No fields yet. Add one to start collecting information from visitors before answering.
        </p>
      )}

      <div style={{ display: "grid", gap: 12 }}>
        {slots.map((s, i) => (
          <FieldCard
            key={i}
            index={i}
            slot={s}
            disabled={disabled}
            isFirst={i === 0}
            isLast={i === slots.length - 1}
            onChange={(next) => update(i, () => next)}
            onRemove={() => remove(i)}
            onDuplicate={() => duplicate(i)}
            onMove={(dir) => move(i, dir)}
            onRename={(name) => rename(i, name)}
          />
        ))}
      </div>

      {!disabled && (
        <button className="ghost" onClick={add} style={{ marginTop: 12 }}>
          + Add field
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function FieldCard({
  index,
  slot,
  disabled,
  isFirst,
  isLast,
  onChange,
  onRemove,
  onDuplicate,
  onMove,
  onRename,
}: {
  index: number;
  slot: SlotDef;
  disabled?: boolean;
  isFirst: boolean;
  isLast: boolean;
  onChange: (next: SlotDef) => void;
  onRemove: () => void;
  onDuplicate: () => void;
  onMove: (dir: -1 | 1) => void;
  onRename: (name: string) => void;
}) {
  const [showCustomQuestion, setShowCustomQuestion] = useState(Boolean(slot.prompt_template));

  return (
    <div
      className="card"
      style={{
        padding: 12,
        background: "var(--paper)",
        border: "1px solid var(--panel-border)",
      }}
    >
      <div className="row" style={{ alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <span className="muted" style={{ fontSize: 11 }}>Field {index + 1}</span>
        {!disabled && (
          <div className="row" style={{ gap: 4 }}>
            <button className="ghost" type="button" aria-label="Move up" onClick={() => onMove(-1)} disabled={isFirst} style={{ padding: "2px 6px" }}>
              <ChevronUp size={14} strokeWidth={1.75} />
            </button>
            <button className="ghost" type="button" aria-label="Move down" onClick={() => onMove(1)} disabled={isLast} style={{ padding: "2px 6px" }}>
              <ChevronDown size={14} strokeWidth={1.75} />
            </button>
            <button className="ghost" type="button" aria-label="Duplicate" onClick={onDuplicate} style={{ padding: "2px 6px" }}>
              <Copy size={14} strokeWidth={1.75} />
            </button>
            <button className="ghost" type="button" aria-label="Remove" onClick={onRemove} style={{ padding: "2px 6px" }}>
              <Trash2 size={14} strokeWidth={1.75} />
            </button>
          </div>
        )}
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`slot-name-${index}`}>Field name</label>
          <input
            id={`slot-name-${index}`}
            type="text"
            value={slot.name}
            onChange={(e) => onRename(e.target.value)}
            disabled={disabled}
            placeholder="full_name"
            style={{ fontFamily: "ui-monospace, Menlo, monospace" }}
          />
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            Used in prompts as <code>{`{${slot.name || "field_name"}}`}</code>.
          </div>
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label>Type</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {SLOT_TYPES.map((t) => {
              const active = (slot.slot_type ?? "string") === t.value;
              return (
                <button
                  key={t.value}
                  type="button"
                  className="ghost"
                  onClick={() => onChange({ ...slot, slot_type: t.value })}
                  disabled={disabled}
                  title={t.hint}
                  style={{
                    padding: "4px 10px",
                    fontSize: 12,
                    borderRadius: 999,
                    border: `1px solid ${active ? "var(--accent)" : "var(--panel-border)"}`,
                    background: active ? "var(--panel-bg)" : "transparent",
                  }}
                >
                  {t.label}
                </button>
              );
            })}
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            The bot uses the type as a hint when reading the visitor&apos;s reply. It&apos;s not strictly validated.
          </div>
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label>
            <input
              type="checkbox"
              checked={Boolean(slot.required)}
              onChange={(e) => onChange({ ...slot, required: e.target.checked })}
              disabled={disabled}
              style={{ marginRight: 8 }}
            />
            Required
          </label>
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`slot-desc-${index}`}>How the bot recognizes this</label>
          <input
            id={`slot-desc-${index}`}
            type="text"
            value={slot.description ?? ""}
            onChange={(e) => onChange({ ...slot, description: e.target.value || undefined })}
            disabled={disabled}
            placeholder="the visitor's full name"
          />
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            The bot uses this when extracting the value from a visitor&apos;s reply.
          </div>
        </div>

        <div>
          <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              checked={showCustomQuestion}
              onChange={(e) => {
                setShowCustomQuestion(e.target.checked);
                if (!e.target.checked) {
                  onChange({ ...slot, prompt_template: undefined });
                }
              }}
              disabled={disabled}
            />
            <span style={{ fontSize: 13 }}>Use a custom question instead of the default</span>
          </label>
          {showCustomQuestion && (
            <div className="field" style={{ marginTop: 8, marginBottom: 0, paddingLeft: 24 }}>
              <input
                type="text"
                value={slot.prompt_template ?? ""}
                onChange={(e) => onChange({ ...slot, prompt_template: e.target.value || undefined })}
                disabled={disabled}
                placeholder="Could I get your full name to start?"
              />
              <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                Otherwise, the global "Default field question" template is used.
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
