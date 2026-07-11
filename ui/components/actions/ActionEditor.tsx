"use client";

import { useState } from "react";
import {
  Action,
  ActionAlertConfig,
  ActionConfirmation,
  ActionFollowUp,
  ActionFormConfig,
  ActionLimits,
  ActionLinkConfig,
  ActionTrigger,
  TRIGGER_KINDS,
  findInvalidEmails,
  generateTriggerCode,
  summarizeAction,
} from "@/lib/actions";
import FormFieldsEditor from "./FormFieldsEditor";

// Plain-language editor for an existing Action.
//
// Layout: a series of cards, top-to-bottom:
//   1. Header — name, plain-language summary, "Remove action"
//   2. Trigger — when does it fire (radio cards) + decision rule
//   3. Behavior — what does it do (toggles per kind, with embedded sub-editors)
//   4. Attached to skills — multi-select chips
//   5. Advanced — confirmation, limits, follow-up
//   6. Power-user — generated trigger code (read-only)
//
// `internal_code` plumbing is hidden behind the power-user disclosure.
// `belongs_to` is exposed as a friendlier "Attached to" picker.

type Props = {
  action: Action;
  skillIds: string[];
  /** Existing trigger codes in the bot (to avoid collisions on regenerate). */
  existingTriggerCodes: Iterable<string>;
  writable: boolean;
  onChange: (next: Action) => void;
  onRename: (newName: string) => void;
  onRemove: () => void;
};

export default function ActionEditor({
  action,
  skillIds,
  existingTriggerCodes,
  writable,
  onChange,
  onRename,
  onRemove,
}: Props) {
  const [showPower, setShowPower] = useState(false);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <HeaderCard
        action={action}
        writable={writable}
        onRename={onRename}
        onRemove={onRemove}
      />

      <TriggerCard
        trigger={action.trigger}
        actionName={action.name}
        existingTriggerCodes={existingTriggerCodes}
        writable={writable}
        onChange={(next) => onChange({ ...action, trigger: next })}
      />

      <BehaviorCard
        action={action}
        writable={writable}
        onChange={onChange}
      />

      <BelongsToCard
        selected={action.belongsTo}
        skillIds={skillIds}
        writable={writable}
        onChange={(belongsTo) => onChange({ ...action, belongsTo })}
      />

      <AdvancedCard
        action={action}
        writable={writable}
        onChange={onChange}
      />

      <div className="card" style={{ background: "transparent", border: "1px dashed var(--panel-border)" }}>
        <button
          className="ghost"
          type="button"
          onClick={() => setShowPower((v) => !v)}
          style={{ justifyContent: "flex-start", padding: 0, fontSize: 12, color: "var(--text-muted)" }}
        >
          {showPower ? "▾" : "▸"} Power-user details
        </button>
        {showPower && (
          <div style={{ marginTop: 8, fontSize: 12 }}>
            <p className="muted" style={{ marginTop: 0 }}>
              These are internal plumbing fields exposed for debugging. You can edit
              them in <em>Raw JSON</em>, but most admins should never touch them.
            </p>
            {action.trigger.kind === "bot_decides" && (
              <div className="field" style={{ marginBottom: 8 }}>
                <label>Generated trigger code</label>
                <code style={{ display: "block", padding: 8, background: "var(--panel-bg)", borderRadius: 4 }}>
                  {action.trigger.code}
                </code>
                <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                  The bot emits this token in <code>{`<internal_code>...</internal_code>`}</code>
                  {" "}tags when it wants to fire this action. Auto-generated; rarely edited by hand.
                </div>
              </div>
            )}
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Action key</label>
              <code style={{ display: "block", padding: 8, background: "var(--panel-bg)", borderRadius: 4 }}>
                {action.name}
              </code>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function HeaderCard({
  action,
  writable,
  onRename,
  onRemove,
}: {
  action: Action;
  writable: boolean;
  onRename: (newName: string) => void;
  onRemove: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(action.name);

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          {renaming ? (
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              <input
                type="text"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoFocus
                style={{ fontSize: 18, fontWeight: 600 }}
              />
              <button
                onClick={() => {
                  if (draft.trim()) onRename(draft.trim());
                  setRenaming(false);
                }}
                disabled={!writable}
              >
                Save
              </button>
              <button
                className="ghost"
                onClick={() => {
                  setDraft(action.name);
                  setRenaming(false);
                }}
              >
                Cancel
              </button>
            </div>
          ) : (
            <div>
              <h2 style={{ marginTop: 0, marginBottom: 4 }}>
                {action.name}{" "}
                {writable && (
                  <button
                    className="ghost"
                    onClick={() => setRenaming(true)}
                    style={{ padding: "2px 8px", fontSize: 12, marginLeft: 4 }}
                  >
                    Rename
                  </button>
                )}
              </h2>
              <p className="muted" style={{ margin: 0, fontSize: 13 }}>
                {summarizeAction(action)}
              </p>
            </div>
          )}
        </div>
        {writable && (
          <button
            className="ghost"
            onClick={onRemove}
            style={{ padding: "4px 10px", fontSize: 12 }}
          >
            Remove action
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Trigger
// ---------------------------------------------------------------------------

function TriggerCard({
  trigger,
  actionName,
  existingTriggerCodes,
  writable,
  onChange,
}: {
  trigger: ActionTrigger;
  actionName: string;
  existingTriggerCodes: Iterable<string>;
  writable: boolean;
  onChange: (next: ActionTrigger) => void;
}) {
  function setKind(kind: ActionTrigger["kind"]) {
    if (kind === trigger.kind) return;
    switch (kind) {
      case "start_chat":
        onChange({ kind: "start_chat" });
        break;
      case "starter_message":
        onChange({ kind: "starter_message", quickStartString: undefined });
        break;
      case "bot_decides":
        onChange({
          kind: "bot_decides",
          description: "",
          code: generateTriggerCode(actionName, existingTriggerCodes),
        });
        break;
      case "bot_button_link":
        onChange({ kind: "bot_button_link" });
        break;
      case "bot_button_card":
        onChange({ kind: "bot_button_card" });
        break;
    }
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>When should this fire?</h3>
      <div style={{ display: "grid", gap: 8 }}>
        {TRIGGER_KINDS.map((t) => {
          const active = trigger.kind === t.kind;
          return (
            <button
              key={t.kind}
              type="button"
              className="ghost"
              onClick={() => setKind(t.kind)}
              disabled={!writable}
              style={{
                textAlign: "left",
                padding: 10,
                borderRadius: 6,
                border: `1px solid ${active ? "var(--accent)" : "var(--panel-border)"}`,
                background: active ? "var(--panel-bg)" : "transparent",
                display: "block",
              }}
            >
              <div style={{ fontSize: 13, fontWeight: 500 }}>{t.label}</div>
              <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{t.description}</div>
            </button>
          );
        })}
      </div>

      {trigger.kind === "bot_decides" && (
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="trigger-desc">Decision rule</label>
          <textarea
            id="trigger-desc"
            rows={3}
            value={trigger.description}
            onChange={(e) =>
              onChange({ ...trigger, description: e.target.value })
            }
            disabled={!writable}
            placeholder="When the visitor agrees to a callback…"
            style={{
              width: "100%",
              fontFamily: "inherit",
              padding: 10,
              background: "var(--paper)",
              color: "var(--text)",
              border: "1px solid var(--panel-border)",
              borderRadius: 6,
            }}
          />
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            Write in plain English when the bot should fire this action. The bot uses
            this rule alongside the conversation to decide.
          </div>
        </div>
      )}

      {trigger.kind === "starter_message" && (
        <div className="field" style={{ marginTop: 12 }}>
          <label htmlFor="trigger-qs">Bound starter message</label>
          <input
            id="trigger-qs"
            type="text"
            value={trigger.quickStartString ?? ""}
            onChange={(e) =>
              onChange({ ...trigger, quickStartString: e.target.value || undefined })
            }
            disabled={!writable}
            placeholder="Book a callback"
          />
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            Match the starter chip text exactly. Configure starter chips on the Identity tab.
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Behavior — what does this action do?
// ---------------------------------------------------------------------------

function BehaviorCard({
  action,
  writable,
  onChange,
}: {
  action: Action;
  writable: boolean;
  onChange: (next: Action) => void;
}) {
  const showForm = action.form !== undefined;
  const sendAlert = action.alert !== undefined;
  const openLink = action.link !== undefined;

  function toggleForm(on: boolean) {
    if (on === showForm) return;
    onChange({
      ...action,
      form: on
        ? action.form ?? { preset: "custom", fields: [], submitLabel: "Submit" }
        : undefined,
      kind: deriveKind({ form: on, alert: sendAlert, link: openLink }),
    });
  }
  function toggleAlert(on: boolean) {
    if (on === sendAlert) return;
    onChange({
      ...action,
      alert: on
        ? action.alert ?? { recipients: [], includeTranscript: false }
        : undefined,
      kind: deriveKind({ form: showForm, alert: on, link: openLink }),
    });
  }
  function toggleLink(on: boolean) {
    if (on === openLink) return;
    onChange({
      ...action,
      link: on
        ? action.link ?? { url: "", openInBackground: false }
        : undefined,
      kind: deriveKind({ form: showForm, alert: sendAlert, link: on }),
    });
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>What does this action do?</h3>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        Pick one or more. Most actions just show a form.
      </p>

      <div style={{ display: "grid", gap: 12 }}>
        <BehaviorRow
          checked={showForm}
          onChange={toggleForm}
          disabled={!writable}
          title="Show a form"
          description="Open a form so the visitor can submit structured information."
        >
          {showForm && action.form && (
            <FormSubEditor
              form={action.form}
              writable={writable}
              onChange={(next) => onChange({ ...action, form: next })}
              actionName={action.name}
            />
          )}
        </BehaviorRow>

        <BehaviorRow
          checked={sendAlert}
          onChange={toggleAlert}
          disabled={!writable}
          title="Send an alert email"
          description="Email a summary or notification to your team when this fires."
        >
          {sendAlert && action.alert && (
            <AlertSubEditor
              alert={action.alert}
              writable={writable}
              onChange={(next) => onChange({ ...action, alert: next })}
            />
          )}
        </BehaviorRow>

        <BehaviorRow
          checked={openLink}
          onChange={toggleLink}
          disabled={!writable}
          title="Open a tracking link"
          description="Open a URL when the action fires (useful for UTM-tagged landing pages)."
        >
          {openLink && action.link && (
            <LinkSubEditor
              link={action.link}
              writable={writable}
              onChange={(next) => onChange({ ...action, link: next })}
            />
          )}
        </BehaviorRow>
      </div>
    </div>
  );
}

function deriveKind({ form, alert, link }: { form: boolean; alert: boolean; link: boolean }): Action["kind"] {
  const set = [form, alert, link].filter(Boolean).length;
  if (set > 1) return "advanced";
  if (alert) return "send_alert";
  if (link) return "open_link";
  return "show_form";
}

function BehaviorRow({
  checked,
  onChange,
  disabled,
  title,
  description,
  children,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--panel-border)",
        borderRadius: 6,
        padding: 12,
        background: checked ? "var(--panel-bg)" : "transparent",
      }}
    >
      <label style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: disabled ? "default" : "pointer" }}>
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          disabled={disabled}
          style={{ marginTop: 4 }}
        />
        <div>
          <div style={{ fontSize: 13, fontWeight: 500 }}>{title}</div>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>{description}</div>
        </div>
      </label>
      {checked && (
        <div style={{ marginTop: 12, paddingLeft: 26 }}>
          {children}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-editors per behavior kind
// ---------------------------------------------------------------------------

function FormSubEditor({
  form,
  writable,
  onChange,
  actionName,
}: {
  form: ActionFormConfig;
  writable: boolean;
  onChange: (next: ActionFormConfig) => void;
  actionName: string;
}) {
  const isAuth = form.preset === "auth_form";

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div className="field" style={{ marginBottom: 0 }}>
        <label>Form preset</label>
        <div className="row" style={{ gap: 8 }}>
          <button
            type="button"
            className="ghost"
            onClick={() => onChange({ ...form, preset: "custom" })}
            disabled={!writable}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: `1px solid ${!isAuth ? "var(--accent)" : "var(--panel-border)"}`,
              background: !isAuth ? "var(--panel-bg)" : "transparent",
            }}
          >
            Custom form
          </button>
          <button
            type="button"
            className="ghost"
            onClick={() => onChange({ ...form, preset: "auth_form" })}
            disabled={!writable}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: `1px solid ${isAuth ? "var(--accent)" : "var(--panel-border)"}`,
              background: isAuth ? "var(--panel-bg)" : "transparent",
            }}
          >
            Built-in login form
          </button>
        </div>
        {isAuth && (
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            Renders the widget&apos;s built-in authentication UI. Field editing is locked.
          </div>
        )}
      </div>

      {!isAuth && (
        <>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor={`form-title-${actionName}`}>Form title</label>
            <input
              id={`form-title-${actionName}`}
              type="text"
              value={form.title ?? ""}
              onChange={(e) => onChange({ ...form, title: e.target.value || undefined })}
              disabled={!writable}
              placeholder="Request a callback"
            />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor={`form-desc-${actionName}`}>Description</label>
            <textarea
              id={`form-desc-${actionName}`}
              rows={2}
              value={form.description ?? ""}
              onChange={(e) => onChange({ ...form, description: e.target.value || undefined })}
              disabled={!writable}
              placeholder="Tell us when's a good time to reach you."
              style={{
                width: "100%",
                padding: 10,
                background: "var(--paper)",
                color: "var(--text)",
                border: "1px solid var(--panel-border)",
                borderRadius: 6,
              }}
            />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label htmlFor={`form-post-${actionName}`}>Message after submit</label>
            <textarea
              id={`form-post-${actionName}`}
              rows={2}
              value={form.postSubmissionMessage ?? ""}
              onChange={(e) => onChange({ ...form, postSubmissionMessage: e.target.value || undefined })}
              disabled={!writable}
              placeholder="Thanks! We'll be in touch."
              style={{
                width: "100%",
                padding: 10,
                background: "var(--paper)",
                color: "var(--text)",
                border: "1px solid var(--panel-border)",
                borderRadius: 6,
              }}
            />
          </div>
          <FormFieldsEditor
            fields={form.fields}
            onChange={(fields) => onChange({ ...form, fields })}
            submitLabel={form.submitLabel}
            onSubmitLabelChange={(submitLabel) => onChange({ ...form, submitLabel })}
            disabled={!writable}
          />
        </>
      )}
    </div>
  );
}

function AlertSubEditor({
  alert,
  writable,
  onChange,
}: {
  alert: ActionAlertConfig;
  writable: boolean;
  onChange: (next: ActionAlertConfig) => void;
}) {
  const invalidEmails = findInvalidEmails(alert.recipients);
  const hasInvalid = invalidEmails.length > 0;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div className="field" style={{ marginBottom: 0 }}>
        <label>Recipients</label>
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
          disabled={!writable}
          placeholder="ops@acme.com, founder@acme.com"
          aria-invalid={hasInvalid || undefined}
          style={hasInvalid ? { borderColor: "var(--error, #c4314b)" } : undefined}
        />
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          Comma-separated list of email addresses to notify.
        </div>
        {hasInvalid && (
          <div
            role="alert"
            style={{ fontSize: 11, marginTop: 4, color: "var(--error, #c4314b)" }}
          >
            Doesn&apos;t look like a valid email: {invalidEmails.map((e) => `"${e}"`).join(", ")}
          </div>
        )}
      </div>
      <div className="field" style={{ marginBottom: 0 }}>
        <label>
          <input
            type="checkbox"
            checked={alert.includeTranscript}
            onChange={(e) => onChange({ ...alert, includeTranscript: e.target.checked })}
            disabled={!writable}
            style={{ marginRight: 8 }}
          />
          Include a conversation summary
        </label>
      </div>
      {alert.includeTranscript && (
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Summary instructions</label>
          <textarea
            rows={2}
            value={alert.summaryPrompt ?? ""}
            onChange={(e) => onChange({ ...alert, summaryPrompt: e.target.value || undefined })}
            disabled={!writable}
            placeholder="Summarize the visitor's interest in 100 words."
            style={{
              width: "100%",
              padding: 10,
              background: "var(--paper)",
              color: "var(--text)",
              border: "1px solid var(--panel-border)",
              borderRadius: 6,
            }}
          />
          <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
            What the LLM should include in the alert summary.
          </div>
        </div>
      )}
    </div>
  );
}

function LinkSubEditor({
  link,
  writable,
  onChange,
}: {
  link: ActionLinkConfig;
  writable: boolean;
  onChange: (next: ActionLinkConfig) => void;
}) {
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div className="field" style={{ marginBottom: 0 }}>
        <label>URL</label>
        <input
          type="url"
          value={link.url}
          onChange={(e) => onChange({ ...link, url: e.target.value })}
          disabled={!writable}
          placeholder="https://example.com/landing?utm_source=bot"
        />
      </div>
      <div className="field" style={{ marginBottom: 0 }}>
        <label>
          <input
            type="checkbox"
            checked={link.openInBackground}
            onChange={(e) => onChange({ ...link, openInBackground: e.target.checked })}
            disabled={!writable}
            style={{ marginRight: 8 }}
          />
          Open in background tab
        </label>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Belongs-to (skill multi-select)
// ---------------------------------------------------------------------------

function BelongsToCard({
  selected,
  skillIds,
  writable,
  onChange,
}: {
  selected: string[];
  skillIds: string[];
  writable: boolean;
  onChange: (next: string[]) => void;
}) {
  const known = new Set(skillIds);
  const all = Array.from(new Set([...skillIds, ...selected]));

  function toggle(id: string) {
    if (selected.includes(id)) onChange(selected.filter((x) => x !== id));
    else onChange([...selected, id]);
  }

  return (
    <div className="card">
      <h3 style={{ marginTop: 0 }}>Attached to skills</h3>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        The bot only considers this action&apos;s decision rule while these skills are active.
        Most actions attach to one skill.
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {all.length === 0 && (
          <span className="muted" style={{ fontSize: 13 }}>
            (no skills defined yet — create one on the Skills tab first)
          </span>
        )}
        {all.map((id) => {
          const isOn = selected.includes(id);
          const isOrphan = !known.has(id);
          return (
            <button
              key={id}
              type="button"
              className="ghost"
              onClick={() => toggle(id)}
              disabled={!writable}
              title={isOrphan ? "This skill no longer exists in the config" : undefined}
              style={{
                padding: "4px 10px",
                fontSize: 12,
                borderRadius: 999,
                border: `1px solid ${isOn ? "var(--accent)" : "var(--panel-border)"}`,
                background: isOn ? "var(--panel-bg)" : "transparent",
                color: isOrphan ? "var(--text-muted)" : "var(--text)",
              }}
            >
              {isOn ? "✓ " : ""}{id}{isOrphan ? " ⚠" : ""}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Advanced — confirmation, limits, follow-up
// ---------------------------------------------------------------------------

function AdvancedCard({
  action,
  writable,
  onChange,
}: {
  action: Action;
  writable: boolean;
  onChange: (next: Action) => void;
}) {
  const [open, setOpen] = useState(false);

  const confirmation = action.confirmation ?? { enabled: false };
  const limits = action.limits ?? { perVisitorEnabled: false };
  const followUp = action.followUp ?? {};

  function setConfirmation(next: ActionConfirmation | undefined) {
    onChange({ ...action, confirmation: next });
  }
  function setLimits(next: ActionLimits | undefined) {
    onChange({ ...action, limits: next });
  }
  function setFollowUp(next: ActionFollowUp | undefined) {
    onChange({ ...action, followUp: next });
  }

  return (
    <div className="card">
      <button
        className="ghost"
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          justifyContent: "flex-start",
          padding: 0,
          fontSize: 14,
          fontWeight: 600,
          color: "var(--text)",
        }}
      >
        {open ? "▾" : "▸"} Advanced settings
      </button>

      {open && (
        <div style={{ marginTop: 16, display: "grid", gap: 16 }}>
          {/* Confirmation */}
          <div>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={confirmation.enabled}
                onChange={(e) =>
                  setConfirmation(e.target.checked ? { enabled: true, message: confirmation.message } : undefined)
                }
                disabled={!writable}
              />
              <strong style={{ fontSize: 13 }}>Ask the visitor to confirm before this fires</strong>
            </label>
            {confirmation.enabled && (
              <div className="field" style={{ marginTop: 8, marginBottom: 0, paddingLeft: 24 }}>
                <label>Confirmation message</label>
                <input
                  type="text"
                  value={confirmation.message ?? ""}
                  onChange={(e) =>
                    setConfirmation({ ...confirmation, message: e.target.value || undefined })
                  }
                  disabled={!writable}
                  placeholder="Would a quick call help you out?"
                />
              </div>
            )}
          </div>

          {/* Limits */}
          <div>
            <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={limits.perVisitorEnabled}
                onChange={(e) => {
                  const enabled = e.target.checked;
                  setLimits(
                    enabled
                      ? { ...limits, perVisitorEnabled: true }
                      : limits.perSessionMax !== undefined
                      ? { ...limits, perVisitorEnabled: false }
                      : undefined,
                  );
                }}
                disabled={!writable}
              />
              <strong style={{ fontSize: 13 }}>Limit how many times this can fire per visitor</strong>
            </label>
            {limits.perVisitorEnabled && (
              <div style={{ marginTop: 8, paddingLeft: 24, display: "grid", gap: 8 }}>
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Max firings per visitor</label>
                  <input
                    type="number"
                    min={0}
                    value={limits.perVisitorLimit ?? ""}
                    onChange={(e) =>
                      setLimits({
                        ...limits,
                        perVisitorLimit: e.target.value === "" ? undefined : Number(e.target.value),
                      })
                    }
                    disabled={!writable}
                    placeholder="10"
                    style={{ width: 120 }}
                  />
                </div>
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Message when limit reached</label>
                  <input
                    type="text"
                    value={limits.postLimitMessage ?? ""}
                    onChange={(e) =>
                      setLimits({ ...limits, postLimitMessage: e.target.value || undefined })
                    }
                    disabled={!writable}
                    placeholder="You've reached the limit for callbacks today."
                  />
                </div>
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Follow-up question after limit</label>
                  <input
                    type="text"
                    value={limits.postLimitQuery ?? ""}
                    onChange={(e) =>
                      setLimits({ ...limits, postLimitQuery: e.target.value || undefined })
                    }
                    disabled={!writable}
                    placeholder="Want to email us instead?"
                  />
                </div>
              </div>
            )}
            <div className="field" style={{ marginTop: 12, marginBottom: 0 }}>
              <label>Max times per session</label>
              <input
                type="number"
                min={0}
                value={limits.perSessionMax ?? ""}
                onChange={(e) => {
                  const v = e.target.value === "" ? undefined : Number(e.target.value);
                  if (v === undefined && !limits.perVisitorEnabled) {
                    setLimits(undefined);
                    return;
                  }
                  setLimits({ ...limits, perSessionMax: v });
                }}
                disabled={!writable}
                placeholder="1 (default)"
                style={{ width: 120 }}
              />
              <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
                A single chat session won&apos;t see this action more than this many times. Defaults to 1.
              </div>
            </div>
          </div>

          {/* Follow-up */}
          <div>
            <strong style={{ fontSize: 13 }}>After submission, ask a follow-up</strong>
            <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
              Pose a follow-up question to keep the conversation going.
            </div>
            <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Follow-up message</label>
                <input
                  type="text"
                  value={followUp.message ?? ""}
                  onChange={(e) => {
                    const message = e.target.value || undefined;
                    if (!message && !followUp.query) setFollowUp(undefined);
                    else setFollowUp({ ...followUp, message });
                  }}
                  disabled={!writable}
                  placeholder="Anything else I can help with?"
                />
              </div>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Follow-up question</label>
                <input
                  type="text"
                  value={followUp.query ?? ""}
                  onChange={(e) => {
                    const query = e.target.value || undefined;
                    if (!followUp.message && !query) setFollowUp(undefined);
                    else setFollowUp({ ...followUp, query });
                  }}
                  disabled={!writable}
                  placeholder="What else are you looking into?"
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
