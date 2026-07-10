"use client";

import { useState } from "react";
import { ChevronDown, ChevronUp, Copy, GripVertical, Trash2 } from "lucide-react";
import { FIELD_TYPES, FormField, FormFieldType } from "@/lib/actions";

// Vertical-card field editor. Replaces the cramped single-row layout.
//
// Per-card surface:
//   - Name (id)        — JSON key, used in {filled_slots} substitution
//   - Label            — visitor-facing
//   - Type chips       — text / email / phone / number / textarea / date / time / yes-no / select
//   - Placeholder
//   - Required toggle
//   - Choices (only for select)
//   - Default value (only for checkbox)
//   - Advanced disclosure (d_type)
//
// Reordering: arrow buttons + drag handle (HTML5 dnd, no new deps).

type Props = {
  fields: FormField[];
  onChange: (next: FormField[]) => void;
  submitLabel: string;
  onSubmitLabelChange: (label: string) => void;
  disabled?: boolean;
};

export default function FormFieldsEditor({
  fields,
  onChange,
  submitLabel,
  onSubmitLabelChange,
  disabled,
}: Props) {
  const requiredCount = fields.filter((f) => f.required).length;

  function update(i: number, fn: (f: FormField) => FormField) {
    onChange(fields.map((f, idx) => (idx === i ? fn(f) : f)));
  }
  function remove(i: number) {
    onChange(fields.filter((_, idx) => idx !== i));
  }
  function duplicate(i: number) {
    const f = fields[i];
    const usedIds = new Set(fields.map((x) => x.id));
    let id = `${f.id}_copy`;
    let n = 1;
    while (usedIds.has(id)) {
      n += 1;
      id = `${f.id}_copy_${n}`;
    }
    const next = fields.slice();
    next.splice(i + 1, 0, { ...f, id });
    onChange(next);
  }
  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= fields.length) return;
    const next = fields.slice();
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  }
  function add() {
    const usedIds = new Set(fields.map((f) => f.id));
    let id = "field";
    let n = 1;
    while (usedIds.has(id)) {
      n += 1;
      id = `field_${n}`;
    }
    onChange([
      ...fields,
      { id, label: "", type: "text", required: false },
    ]);
  }
  function rename(i: number, newId: string) {
    const trimmed = newId.trim();
    if (!trimmed) return;
    if (fields.some((f, idx) => idx !== i && f.id === trimmed)) return; // collision
    update(i, (f) => ({ ...f, id: trimmed }));
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <div>
          <strong>Fields</strong>
          <span className="muted" style={{ fontSize: 12, marginLeft: 8 }}>
            {fields.length} {fields.length === 1 ? "field" : "fields"}
            {requiredCount > 0 ? ` · ${requiredCount} required` : ""}
          </span>
        </div>
      </div>

      {fields.length === 0 && (
        <p className="muted" style={{ fontSize: 13, margin: "8px 0" }}>
          No fields yet. Add one to start collecting information from visitors.
        </p>
      )}

      <div style={{ display: "grid", gap: 12 }}>
        {fields.map((f, i) => (
          <FieldCard
            key={i}
            index={i}
            field={f}
            disabled={disabled}
            isFirst={i === 0}
            isLast={i === fields.length - 1}
            onChange={(next) => update(i, () => next)}
            onRemove={() => remove(i)}
            onDuplicate={() => duplicate(i)}
            onMove={(dir) => move(i, dir)}
            onRename={(id) => rename(i, id)}
          />
        ))}
      </div>

      {!disabled && (
        <button
          className="ghost"
          onClick={add}
          style={{ marginTop: 12 }}
        >
          + Add field
        </button>
      )}

      <div className="field" style={{ marginTop: 20 }}>
        <label>Submit button label</label>
        <input
          type="text"
          value={submitLabel}
          onChange={(e) => onSubmitLabelChange(e.target.value || "Submit")}
          disabled={disabled}
          placeholder="Submit"
          style={{ maxWidth: 280 }}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          The button visitors click to send the form.
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-field card
// ---------------------------------------------------------------------------

function FieldCard({
  index,
  field,
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
  field: FormField;
  disabled?: boolean;
  isFirst: boolean;
  isLast: boolean;
  onChange: (next: FormField) => void;
  onRemove: () => void;
  onDuplicate: () => void;
  onMove: (dir: -1 | 1) => void;
  onRename: (id: string) => void;
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false);

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
        <div className="row" style={{ alignItems: "center", gap: 6 }}>
          <span className="muted" aria-hidden style={{ display: "inline-flex" }}>
            <GripVertical size={14} strokeWidth={1.75} />
          </span>
          <span className="muted" style={{ fontSize: 11 }}>Field {index + 1}</span>
        </div>
        {!disabled && (
          <div className="row" style={{ gap: 4 }}>
            <button
              className="ghost"
              type="button"
              aria-label="Move up"
              onClick={() => onMove(-1)}
              disabled={isFirst}
              style={{ padding: "2px 6px" }}
            >
              <ChevronUp size={14} strokeWidth={1.75} />
            </button>
            <button
              className="ghost"
              type="button"
              aria-label="Move down"
              onClick={() => onMove(1)}
              disabled={isLast}
              style={{ padding: "2px 6px" }}
            >
              <ChevronDown size={14} strokeWidth={1.75} />
            </button>
            <button
              className="ghost"
              type="button"
              aria-label="Duplicate"
              onClick={onDuplicate}
              style={{ padding: "2px 6px" }}
            >
              <Copy size={14} strokeWidth={1.75} />
            </button>
            <button
              className="ghost"
              type="button"
              aria-label="Remove field"
              onClick={onRemove}
              style={{ padding: "2px 6px" }}
            >
              <Trash2 size={14} strokeWidth={1.75} />
            </button>
          </div>
        )}
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`fld-id-${index}`}>Name (id)</label>
          <input
            id={`fld-id-${index}`}
            type="text"
            value={field.id}
            onChange={(e) => onRename(e.target.value)}
            disabled={disabled}
            placeholder="full_name"
            style={{ fontFamily: "ui-monospace, Menlo, monospace" }}
          />
          <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
            Used in prompt as <code>{`{${field.id || "field_id"}}`}</code>. Must be unique within this form.
          </div>
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`fld-label-${index}`}>Label visitors see</label>
          <input
            id={`fld-label-${index}`}
            type="text"
            value={field.label}
            onChange={(e) => onChange({ ...field, label: e.target.value })}
            disabled={disabled}
            placeholder="Full name"
          />
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label>Type</label>
          <FieldTypeChips
            value={field.type}
            onChange={(t) => onChange({ ...field, type: t })}
            disabled={disabled}
          />
        </div>

        {field.type === "select" && (
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor={`fld-choices-${index}`}>Choices</label>
            <input
              id={`fld-choices-${index}`}
              type="text"
              value={(field.choices ?? []).join(", ")}
              onChange={(e) => {
                const choices = e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean);
                onChange({ ...field, choices });
              }}
              disabled={disabled}
              placeholder="One, Two, Three"
            />
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              Comma-separated. The widget renders these as the dropdown options.
            </div>
          </div>
        )}

        {field.type === "checkbox" && (
          <div className="field" style={{ marginBottom: 0 }}>
            <label>
              <input
                type="checkbox"
                checked={Boolean(field.defaultValue)}
                onChange={(e) => onChange({ ...field, defaultValue: e.target.checked })}
                disabled={disabled}
                style={{ marginRight: 8 }}
              />
              Pre-checked by default
            </label>
          </div>
        )}

        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor={`fld-ph-${index}`}>Placeholder</label>
          <input
            id={`fld-ph-${index}`}
            type="text"
            value={field.placeholder ?? ""}
            onChange={(e) => onChange({ ...field, placeholder: e.target.value || undefined })}
            disabled={disabled}
            placeholder="e.g. Jane Doe"
          />
        </div>

        <div className="field" style={{ marginBottom: 0 }}>
          <label>
            <input
              type="checkbox"
              checked={Boolean(field.required)}
              onChange={(e) => onChange({ ...field, required: e.target.checked || undefined })}
              disabled={disabled}
              style={{ marginRight: 8 }}
            />
            Required
          </label>
        </div>

        <button
          className="ghost"
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          style={{
            justifyContent: "flex-start",
            padding: "4px 0",
            fontSize: 12,
            color: "var(--text-muted)",
          }}
        >
          {advancedOpen ? "▾" : "▸"} Advanced options
        </button>

        {advancedOpen && (
          <div className="field" style={{ marginBottom: 0, paddingLeft: 16, borderLeft: "2px solid var(--panel-border)" }}>
            <label htmlFor={`fld-dtype-${index}`}>d_type (widget hint)</label>
            <input
              id={`fld-dtype-${index}`}
              type="text"
              value={field.d_type ?? ""}
              onChange={(e) => onChange({ ...field, d_type: e.target.value || undefined })}
              disabled={disabled}
              placeholder="e.g. cphone"
            />
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              Widget-side rendering hint (e.g. <code>cphone</code> for country-coded phone input).
              Most admins should leave this blank.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function FieldTypeChips({
  value,
  onChange,
  disabled,
}: {
  value: FormFieldType;
  onChange: (v: FormFieldType) => void;
  disabled?: boolean;
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {FIELD_TYPES.map((t) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            type="button"
            className="ghost"
            onClick={() => onChange(t.value)}
            disabled={disabled}
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
  );
}
