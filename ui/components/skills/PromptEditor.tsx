"use client";

import { useRef, useState } from "react";
import { BookOpen, Eye, EyeOff } from "lucide-react";
import { detectPlaceholders, previewAssembledPrompt } from "@/lib/prompt-preview";
import { PROMPT_SNIPPETS } from "@/lib/prompt-snippets";

// PromptEditor — the "Bot response prompt" textarea, with:
//   - Variable chips that insert at cursor (with auto-append hints)
//   - Inline preview of the prompt the LLM will actually see
//   - Snippet library ("Insert from library")

const VAR_CHIPS: { var: string; label: string; description: string }[] = [
  { var: "{filled_slots}", label: "{filled_slots}", description: "Inserts the fields you've collected (e.g. name, email)." },
  { var: "{context}", label: "{context}", description: "Inserts knowledge-base hits when this skill answers from knowledge." },
  { var: "{user_input}", label: "{user_input}", description: "Inserts the visitor's most recent message." },
  { var: "{history}", label: "{history}", description: "Inserts the recent conversation history." },
];

type Props = {
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  /** Used by the live preview to compute auto-appended sections. */
  config: Record<string, unknown>;
  topicName: string;
  slots: Array<{ name: string }>;
};

export default function PromptEditor({ value, onChange, disabled, config, topicName, slots }: Props) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const [showLibrary, setShowLibrary] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const present = detectPlaceholders(value);

  function insertVar(v: string) {
    if (disabled) return;
    const ta = taRef.current;
    if (!ta) {
      onChange(`${value}\n${v}`);
      return;
    }
    const start = ta.selectionStart ?? value.length;
    const end = ta.selectionEnd ?? value.length;
    const next = value.slice(0, start) + v + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      ta.focus();
      const cursor = start + v.length;
      ta.setSelectionRange(cursor, cursor);
    });
  }

  function insertSnippet(body: string, replace: boolean) {
    if (disabled) return;
    onChange(replace ? body : value ? `${value}\n${body}` : body);
    setShowLibrary(false);
  }

  return (
    <div>
      <div
        className="row"
        style={{ alignItems: "flex-start", justifyContent: "space-between", marginBottom: 6, gap: 8 }}
      >
        <label htmlFor="prompt-editor" style={{ fontWeight: 500 }}>
          Bot response prompt
        </label>
        {!disabled && (
          <div className="row" style={{ gap: 6 }}>
            <button
              type="button"
              className="ghost"
              onClick={() => setShowLibrary((v) => !v)}
              style={{ padding: "4px 8px", fontSize: 12 }}
            >
              <BookOpen size={12} strokeWidth={1.75} /> Insert from library
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => setShowPreview((v) => !v)}
              style={{ padding: "4px 8px", fontSize: 12 }}
              aria-pressed={showPreview}
            >
              {showPreview ? <EyeOff size={12} strokeWidth={1.75} /> : <Eye size={12} strokeWidth={1.75} />}{" "}
              {showPreview ? "Hide preview" : "Show final prompt"}
            </button>
          </div>
        )}
      </div>

      <textarea
        id="prompt-editor"
        ref={taRef}
        rows={10}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        placeholder="Write the prompt the bot follows when this skill is active…"
        style={{
          width: "100%",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          fontSize: 13,
          padding: 12,
          background: "var(--paper)",
          color: "var(--text)",
          border: "1px solid var(--panel-border)",
          borderRadius: 6,
        }}
      />

      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
        {VAR_CHIPS.map((c) => {
          const inUse = present[c.var];
          return (
            <button
              key={c.var}
              type="button"
              className="ghost"
              onClick={() => insertVar(c.var)}
              disabled={disabled}
              title={`${c.description}${inUse ? "" : " (auto-appended at runtime if not inserted)"}`}
              style={{
                padding: "3px 8px",
                fontSize: 11,
                fontFamily: "ui-monospace, Menlo, monospace",
                borderRadius: 999,
                border: `1px solid ${inUse ? "var(--accent)" : "var(--panel-border)"}`,
                background: inUse ? "var(--panel-bg)" : "transparent",
                color: inUse ? "var(--text)" : "var(--text-muted)",
              }}
            >
              {inUse ? "✓ " : "+ "}{c.label}
            </button>
          );
        })}
      </div>

      <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        Variables you skip get appended automatically at the end of your prompt at runtime. Insert
        them inline if you want to control where they appear.
      </p>

      {showLibrary && (
        <div
          className="card"
          style={{
            marginTop: 12,
            padding: 12,
            background: "var(--paper)",
            border: "1px solid var(--panel-border)",
          }}
        >
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <strong>Insert from library</strong>
            <button className="ghost" onClick={() => setShowLibrary(false)} style={{ padding: "2px 6px", fontSize: 12 }}>
              Close
            </button>
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {PROMPT_SNIPPETS.map((s) => (
              <div
                key={s.id}
                style={{
                  border: "1px solid var(--panel-border)",
                  borderRadius: 6,
                  padding: 10,
                }}
              >
                <div style={{ fontSize: 13, fontWeight: 500 }}>{s.label}</div>
                <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{s.description}</div>
                <div className="row" style={{ gap: 6, marginTop: 8 }}>
                  <button onClick={() => insertSnippet(s.body, true)} style={{ padding: "3px 10px", fontSize: 12 }}>
                    Replace
                  </button>
                  <button
                    className="ghost"
                    onClick={() => insertSnippet(s.body, false)}
                    style={{ padding: "3px 10px", fontSize: 12 }}
                  >
                    Append
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {showPreview && (
        <div
          style={{
            marginTop: 12,
            padding: 12,
            background: "var(--panel-bg)",
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
          }}
        >
          <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
            Final prompt the LLM will see at runtime (with auto-appended sections):
          </div>
          <pre
            style={{
              margin: 0,
              fontSize: 12,
              fontFamily: "ui-monospace, Menlo, monospace",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {previewAssembledPrompt({
              promptBody: value,
              slots,
              config,
              topicName,
            })}
          </pre>
        </div>
      )}
    </div>
  );
}
