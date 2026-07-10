"use client";

import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Sparkles, X } from "lucide-react";
import {
  Action,
  ActionAlertConfig,
  ActionFormConfig,
  ActionKind,
  ActionLinkConfig,
  ActionTrigger,
  TRIGGER_KINDS,
  findInvalidEmails,
  generateTriggerCode,
  summarizeAction,
} from "@/lib/actions";
import FormFieldsEditor from "./FormFieldsEditor";

// Multi-step wizard for creating a new action.
//
// Step 1 — kind ("show_form" | "send_alert" | "open_link" | "advanced")
// Step 2 — trigger (start_chat | starter_message | bot_decides | bot_button_*)
// Step 3 — decision rule (only when trigger.kind === "bot_decides")
// Step 4 — configure based on kind
// Step 5 — review + save
//
// Generates trigger_code, sets belongs_to, and validates per-kind on the
// configure step. Closes on save (returns the new Action).

type WizardStep = "kind" | "trigger" | "rule" | "configure" | "review";

const KIND_OPTIONS: { kind: ActionKind; label: string; description: string }[] = [
  {
    kind: "show_form",
    label: "Show a form",
    description: "Open a form so the visitor can fill in structured information (name, email, etc.).",
  },
  {
    kind: "send_alert",
    label: "Send an alert email",
    description: "Email a notification or summary to your team when this action fires.",
  },
  {
    kind: "open_link",
    label: "Open a link",
    description: "Send the visitor to a URL (good for UTM-tagged landing pages or scheduling links).",
  },
  {
    kind: "advanced",
    label: "Combination",
    description: "Mix two or more behaviors (e.g. show a form AND send an alert).",
  },
];

type Props = {
  /** Skill the action will attach to. Empty = orphan, but the wizard
   * always populates this from the launching context. */
  skillName: string;
  /** All current skill ids in the bot, for the optional skill picker
   * if the launching context didn't pass one. */
  skillIds: string[];
  /** Existing trigger codes — used to ensure the auto-generated code is unique. */
  existingTriggerCodes: Iterable<string>;
  /** Existing action names — used to derive a unique starter name. */
  existingActionNames: Iterable<string>;
  onCancel: () => void;
  onSave: (action: Action) => void;
};

export default function ActionWizard({
  skillName,
  skillIds,
  existingTriggerCodes,
  existingActionNames,
  onCancel,
  onSave,
}: Props) {
  const [step, setStep] = useState<WizardStep>("kind");
  const [draft, setDraft] = useState<Action>(() => starterAction(skillName, existingTriggerCodes, existingActionNames));

  const order: WizardStep[] = useMemo(() => {
    const arr: WizardStep[] = ["kind", "trigger"];
    if (draft.trigger.kind === "bot_decides") arr.push("rule");
    arr.push("configure");
    arr.push("review");
    return arr;
  }, [draft.trigger.kind]);

  const idx = order.indexOf(step);
  const canGoBack = idx > 0;
  const canGoNext = idx >= 0 && idx < order.length - 1 && stepIsValid(step, draft);

  function next() {
    if (!canGoNext) return;
    setStep(order[idx + 1]);
  }
  function back() {
    if (!canGoBack) return;
    setStep(order[idx - 1]);
  }

  function save() {
    onSave(draft);
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="action-wizard-title"
      onClick={onCancel}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 60,
        padding: 20,
      }}
    >
      <div
        className="card"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(720px, 100%)",
          maxHeight: "90vh",
          overflow: "auto",
          padding: 0,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <div
          className="row"
          style={{
            justifyContent: "space-between",
            alignItems: "center",
            padding: "16px 20px",
            borderBottom: "1px solid var(--panel-border)",
          }}
        >
          <h2 id="action-wizard-title" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
            <Sparkles size={16} strokeWidth={1.75} />
            New action
          </h2>
          <button className="ghost" onClick={onCancel} aria-label="Close">
            <X size={16} strokeWidth={1.75} />
          </button>
        </div>

        <StepIndicator order={order} current={step} />

        <div style={{ padding: 20, flex: 1 }}>
          {step === "kind" && (
            <KindStep
              draft={draft}
              onChange={setDraft}
            />
          )}
          {step === "trigger" && (
            <TriggerStep
              draft={draft}
              onChange={setDraft}
            />
          )}
          {step === "rule" && (
            <RuleStep
              draft={draft}
              onChange={setDraft}
            />
          )}
          {step === "configure" && (
            <ConfigureStep
              draft={draft}
              onChange={setDraft}
              skillIds={skillIds}
            />
          )}
          {step === "review" && (
            <ReviewStep draft={draft} />
          )}
        </div>

        <div
          className="row"
          style={{
            justifyContent: "space-between",
            padding: "16px 20px",
            borderTop: "1px solid var(--panel-border)",
            background: "var(--paper)",
          }}
        >
          <button className="ghost" onClick={back} disabled={!canGoBack}>
            <ChevronLeft size={14} strokeWidth={1.75} /> Back
          </button>
          {step === "review" ? (
            <button onClick={save}>Create action</button>
          ) : (
            <button onClick={next} disabled={!canGoNext}>
              Continue <ChevronRight size={14} strokeWidth={1.75} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Starter / validation
// ---------------------------------------------------------------------------

function starterAction(skillName: string, existingCodes: Iterable<string>, existingNames: Iterable<string>): Action {
  const used = new Set(existingNames);
  let name = skillName ? `${skillName}_action` : "action";
  let n = 1;
  while (used.has(name)) {
    n += 1;
    name = `${skillName || "action"}_${n}`;
  }
  return {
    name,
    kind: "show_form",
    trigger: {
      kind: "bot_decides",
      description: "",
      code: generateTriggerCode(skillName, existingCodes),
    },
    belongsTo: skillName ? [skillName] : [],
    form: {
      preset: "custom",
      fields: [],
      submitLabel: "Submit",
    },
  };
}

function stepIsValid(step: WizardStep, draft: Action): boolean {
  switch (step) {
    case "kind":
      return Boolean(draft.kind);
    case "trigger":
      return Boolean(draft.trigger.kind);
    case "rule":
      return draft.trigger.kind === "bot_decides" && draft.trigger.description.trim().length > 0;
    case "configure":
      return configureIsValid(draft);
    case "review":
      return true;
  }
}

function configureIsValid(draft: Action): boolean {
  if (!draft.name.trim()) return false;
  switch (draft.kind) {
    case "show_form":
      return Boolean(draft.form);
    case "send_alert":
      return Boolean(draft.alert && draft.alert.recipients.length > 0);
    case "open_link":
      return Boolean(draft.link && draft.link.url.trim().length > 0);
    case "advanced":
      return Boolean(
        (draft.form && draft.form.fields.length > 0) ||
        (draft.alert && draft.alert.recipients.length > 0) ||
        (draft.link && draft.link.url.trim().length > 0),
      );
  }
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

function StepIndicator({ order, current }: { order: WizardStep[]; current: WizardStep }) {
  const labels: Record<WizardStep, string> = {
    kind: "What",
    trigger: "When",
    rule: "How",
    configure: "Configure",
    review: "Review",
  };
  return (
    <div
      className="row"
      style={{
        gap: 0,
        padding: "10px 20px",
        background: "var(--paper)",
        borderBottom: "1px solid var(--panel-border)",
        fontSize: 12,
      }}
    >
      {order.map((s, i) => {
        const active = s === current;
        const past = order.indexOf(current) > i;
        return (
          <div key={s} className="row" style={{ alignItems: "center", gap: 6 }}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: 20,
                height: 20,
                borderRadius: 999,
                fontSize: 11,
                fontWeight: 600,
                background: active ? "var(--accent)" : past ? "var(--panel-bg)" : "transparent",
                color: active ? "white" : "var(--text-muted)",
                border: `1px solid ${active ? "var(--accent)" : "var(--panel-border)"}`,
              }}
            >
              {i + 1}
            </span>
            <span style={{ color: active ? "var(--text)" : "var(--text-muted)" }}>{labels[s]}</span>
            {i < order.length - 1 && (
              <span style={{ width: 24, height: 1, background: "var(--panel-border)", margin: "0 8px" }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Kind
// ---------------------------------------------------------------------------

function KindStep({ draft, onChange }: { draft: Action; onChange: (a: Action) => void }) {
  function setKind(kind: ActionKind) {
    if (kind === draft.kind) return;
    onChange({
      ...draft,
      kind,
      // Seed empty configs so subsequent steps have something to render.
      form: kind === "show_form" || kind === "advanced"
        ? draft.form ?? { preset: "custom", fields: [], submitLabel: "Submit" }
        : undefined,
      alert: kind === "send_alert" || kind === "advanced"
        ? draft.alert ?? { recipients: [], includeTranscript: false }
        : undefined,
      link: kind === "open_link" || kind === "advanced"
        ? draft.link ?? { url: "", openInBackground: false }
        : undefined,
    });
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>What should this action do?</h3>
      <p className="muted" style={{ fontSize: 13 }}>
        Pick the main behavior. You can add more on the configure step.
      </p>
      <div style={{ display: "grid", gap: 10 }}>
        {KIND_OPTIONS.map((opt) => {
          const active = draft.kind === opt.kind;
          return (
            <button
              key={opt.kind}
              type="button"
              className="ghost"
              onClick={() => setKind(opt.kind)}
              style={{
                textAlign: "left",
                padding: 14,
                borderRadius: 6,
                border: `1px solid ${active ? "var(--accent)" : "var(--panel-border)"}`,
                background: active ? "var(--panel-bg)" : "transparent",
                display: "block",
              }}
            >
              <div style={{ fontSize: 14, fontWeight: 500 }}>{opt.label}</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{opt.description}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Trigger
// ---------------------------------------------------------------------------

function TriggerStep({ draft, onChange }: { draft: Action; onChange: (a: Action) => void }) {
  function setTrigger(kind: ActionTrigger["kind"]) {
    let next: ActionTrigger;
    switch (kind) {
      case "start_chat":
        next = { kind: "start_chat" };
        break;
      case "starter_message":
        next = { kind: "starter_message", quickStartString: undefined };
        break;
      case "bot_decides":
        next = {
          kind: "bot_decides",
          description: draft.trigger.kind === "bot_decides" ? draft.trigger.description : "",
          code: draft.trigger.kind === "bot_decides" ? draft.trigger.code : generateTriggerCode(draft.name, []),
        };
        break;
      case "bot_button_link":
        next = { kind: "bot_button_link" };
        break;
      case "bot_button_card":
        next = { kind: "bot_button_card" };
        break;
    }
    onChange({ ...draft, trigger: next });
  }

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>When should this fire?</h3>
      <div style={{ display: "grid", gap: 10 }}>
        {TRIGGER_KINDS.map((t) => {
          const active = draft.trigger.kind === t.kind;
          return (
            <button
              key={t.kind}
              type="button"
              className="ghost"
              onClick={() => setTrigger(t.kind)}
              style={{
                textAlign: "left",
                padding: 14,
                borderRadius: 6,
                border: `1px solid ${active ? "var(--accent)" : "var(--panel-border)"}`,
                background: active ? "var(--panel-bg)" : "transparent",
                display: "block",
              }}
            >
              <div style={{ fontSize: 14, fontWeight: 500 }}>{t.label}</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{t.description}</div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Rule (only when trigger.kind === "bot_decides")
// ---------------------------------------------------------------------------

function RuleStep({ draft, onChange }: { draft: Action; onChange: (a: Action) => void }) {
  if (draft.trigger.kind !== "bot_decides") return null;
  const description = draft.trigger.description;

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>How should the bot decide?</h3>
      <p className="muted" style={{ fontSize: 13 }}>
        Describe in plain English when the bot should fire this action. The bot weighs your
        rule against the conversation to make the decision.
      </p>
      <div className="field">
        <label htmlFor="wiz-rule">Decision rule</label>
        <textarea
          id="wiz-rule"
          rows={4}
          value={description}
          onChange={(e) =>
            onChange({
              ...draft,
              trigger: { ...draft.trigger, description: e.target.value } as ActionTrigger,
            })
          }
          placeholder="When the visitor agrees to a callback…"
          style={{
            width: "100%",
            padding: 12,
            background: "var(--paper)",
            color: "var(--text)",
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
          }}
        />
      </div>
      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--text-muted)" }}>
          Examples
        </summary>
        <ul style={{ marginTop: 8, marginBottom: 0, paddingLeft: 20, fontSize: 12 }}>
          <li>When the visitor agrees to a callback</li>
          <li>When the visitor asks for pricing or a quote</li>
          <li>When the bot can&apos;t answer from the knowledge base</li>
          <li>When the visitor mentions an emergency or urgent issue</li>
        </ul>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Configure
// ---------------------------------------------------------------------------

function ConfigureStep({
  draft,
  onChange,
  skillIds,
}: {
  draft: Action;
  onChange: (a: Action) => void;
  skillIds: string[];
}) {
  const showForm = draft.kind === "show_form" || draft.kind === "advanced";
  const sendAlert = draft.kind === "send_alert" || draft.kind === "advanced";
  const openLink = draft.kind === "open_link" || draft.kind === "advanced";

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <h3 style={{ marginTop: 0 }}>Configure</h3>

      <div className="field" style={{ marginBottom: 0 }}>
        <label htmlFor="wiz-name">Action name</label>
        <input
          id="wiz-name"
          type="text"
          value={draft.name}
          onChange={(e) => onChange({ ...draft, name: e.target.value })}
          placeholder="callback_form"
        />
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          Internal identifier; must be unique. Visitors don&apos;t see this.
        </div>
      </div>

      {showForm && draft.form && (
        <div className="card" style={{ background: "var(--paper)" }}>
          <h4 style={{ marginTop: 0 }}>Form fields</h4>
          <FormFieldsEditor
            fields={draft.form.fields}
            onChange={(fields) => onChange({ ...draft, form: { ...draft.form!, fields } })}
            submitLabel={draft.form.submitLabel}
            onSubmitLabelChange={(submitLabel) =>
              onChange({ ...draft, form: { ...draft.form!, submitLabel } })
            }
          />
        </div>
      )}

      {sendAlert && draft.alert && (
        <AlertConfigure
          alert={draft.alert}
          onChange={(next) => onChange({ ...draft, alert: next })}
        />
      )}

      {openLink && draft.link && (
        <LinkConfigure
          link={draft.link}
          onChange={(next) => onChange({ ...draft, link: next })}
        />
      )}

      {/* Skill picker — surfaced if the parent didn't auto-attach */}
      {draft.belongsTo.length === 0 && skillIds.length > 0 && (
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Attach to skill</label>
          <SkillPicker
            selected={draft.belongsTo}
            skillIds={skillIds}
            onChange={(belongsTo) => onChange({ ...draft, belongsTo })}
          />
        </div>
      )}
    </div>
  );
}

function AlertConfigure({
  alert,
  onChange,
}: {
  alert: ActionAlertConfig;
  onChange: (next: ActionAlertConfig) => void;
}) {
  const invalidEmails = findInvalidEmails(alert.recipients);
  const hasInvalid = invalidEmails.length > 0;
  return (
    <div className="card" style={{ background: "var(--paper)" }}>
      <h4 style={{ marginTop: 0 }}>Alert email</h4>
      <div className="field" style={{ marginBottom: 8 }}>
        <label>Recipients (comma-separated)</label>
        <input
          type="text"
          value={alert.recipients.join(", ")}
          onChange={(e) =>
            onChange({
              ...alert,
              recipients: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="ops@acme.com"
          aria-invalid={hasInvalid || undefined}
          style={hasInvalid ? { borderColor: "var(--error, #c4314b)" } : undefined}
        />
        {hasInvalid && (
          <div
            role="alert"
            style={{ fontSize: 11, marginTop: 4, color: "var(--error, #c4314b)" }}
          >
            Doesn&apos;t look like a valid email: {invalidEmails.map((e) => `"${e}"`).join(", ")}
          </div>
        )}
      </div>
      <div className="field" style={{ marginBottom: 8 }}>
        <label>
          <input
            type="checkbox"
            checked={alert.includeTranscript}
            onChange={(e) => onChange({ ...alert, includeTranscript: e.target.checked })}
            style={{ marginRight: 8 }}
          />
          Include a conversation summary
        </label>
      </div>
      {alert.includeTranscript && (
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Summary instructions</label>
          <input
            type="text"
            value={alert.summaryPrompt ?? ""}
            onChange={(e) => onChange({ ...alert, summaryPrompt: e.target.value || undefined })}
            placeholder="Summarize the visitor's interest in 100 words."
          />
        </div>
      )}
    </div>
  );
}

function LinkConfigure({
  link,
  onChange,
}: {
  link: ActionLinkConfig;
  onChange: (next: ActionLinkConfig) => void;
}) {
  return (
    <div className="card" style={{ background: "var(--paper)" }}>
      <h4 style={{ marginTop: 0 }}>Link</h4>
      <div className="field" style={{ marginBottom: 8 }}>
        <label>URL</label>
        <input
          type="url"
          value={link.url}
          onChange={(e) => onChange({ ...link, url: e.target.value })}
          placeholder="https://example.com/landing?utm_source=bot"
        />
      </div>
      <div className="field" style={{ marginBottom: 0 }}>
        <label>
          <input
            type="checkbox"
            checked={link.openInBackground}
            onChange={(e) => onChange({ ...link, openInBackground: e.target.checked })}
            style={{ marginRight: 8 }}
          />
          Open in background tab
        </label>
      </div>
    </div>
  );
}

function SkillPicker({
  selected,
  skillIds,
  onChange,
}: {
  selected: string[];
  skillIds: string[];
  onChange: (next: string[]) => void;
}) {
  function toggle(id: string) {
    if (selected.includes(id)) onChange(selected.filter((x) => x !== id));
    else onChange([...selected, id]);
  }
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {skillIds.map((id) => {
        const active = selected.includes(id);
        return (
          <button
            key={id}
            type="button"
            className="ghost"
            onClick={() => toggle(id)}
            style={{
              padding: "4px 10px",
              fontSize: 12,
              borderRadius: 999,
              border: `1px solid ${active ? "var(--accent)" : "var(--panel-border)"}`,
              background: active ? "var(--panel-bg)" : "transparent",
            }}
          >
            {active ? "✓ " : ""}{id}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step: Review
// ---------------------------------------------------------------------------

function ReviewStep({ draft }: { draft: Action }) {
  const formInfo = draft.form
    ? `Show a form with ${draft.form.fields.length} field${draft.form.fields.length === 1 ? "" : "s"}.`
    : null;
  const alertInfo = draft.alert
    ? `Send an alert email to ${draft.alert.recipients.length || "(no recipients)"} ${
        draft.alert.recipients.length === 1 ? "address" : "addresses"
      }.`
    : null;
  const linkInfo = draft.link?.url
    ? `Open the URL ${draft.link.url}${draft.link.openInBackground ? " in the background" : ""}.`
    : null;
  const triggerInfo = (() => {
    switch (draft.trigger.kind) {
      case "start_chat":
        return "as soon as the visitor opens the chat";
      case "starter_message":
        return draft.trigger.quickStartString
          ? `when the visitor clicks the starter "${draft.trigger.quickStartString}"`
          : "when the visitor clicks a starter message";
      case "bot_decides":
        return `when the bot decides during conversation, based on the rule "${draft.trigger.description}"`;
      case "bot_button_link":
        return "when the bot replies with a link button";
      case "bot_button_card":
        return "when the bot replies with a card button";
    }
  })();

  return (
    <div>
      <h3 style={{ marginTop: 0 }}>Ready to create</h3>
      <p className="muted" style={{ fontSize: 13 }}>
        Review the action below. You can edit any of these later from the action&apos;s detail page.
      </p>
      <div className="card" style={{ background: "var(--paper)" }}>
        <p style={{ margin: 0 }}>
          <strong>{draft.name}</strong> will fire {triggerInfo}.
        </p>
        <ul style={{ marginTop: 12, marginBottom: 0, paddingLeft: 20, fontSize: 13 }}>
          {formInfo && <li>{formInfo}</li>}
          {alertInfo && <li>{alertInfo}</li>}
          {linkInfo && <li>{linkInfo}</li>}
        </ul>
        {draft.belongsTo.length > 0 && (
          <p style={{ marginTop: 12, marginBottom: 0, fontSize: 13 }}>
            Attached to skill: <code>{draft.belongsTo.join(", ")}</code>
          </p>
        )}
      </div>
      <p className="muted" style={{ marginTop: 16, fontSize: 12 }}>
        Summary: {summarizeAction(draft)}
      </p>
    </div>
  );
}
