// UI-side abstraction over `c_forms_selected`.
//
// The persisted shape is an array of single-key dicts:
//   [{ <name>: { form_schema: {...}, trigger: {...} } }, ...]
//
// That shape is preserved on disk. This module presents it as a tagged
// `Action` union so the editor doesn't have to deal with the
// `form_schema` / `trigger` split or the `<internal_code>` LLM
// convention. `actionFromForm` / `actionToForm` round-trip safely so
// unknown extra keys aren't lost (kept in `_raw`).
//
// Kind detection rules (see `inferKind`):
//   - has form fields            → "show_form"
//   - has email_alert or alerts  → "send_alert"
//   - has utm_tracking + URL     → "open_link"
//   - more than one of the above → "advanced"
//   - none of the above          → "show_form" (empty starter)

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type FormFieldType =
  | "text"
  | "email"
  | "phone"
  | "number"
  | "textarea"
  | "date"
  | "time"
  | "checkbox"
  | "select";

export const FIELD_TYPES: { value: FormFieldType; label: string; hint?: string }[] = [
  { value: "text", label: "Text" },
  { value: "email", label: "Email" },
  { value: "phone", label: "Phone" },
  { value: "number", label: "Number" },
  { value: "textarea", label: "Long text" },
  { value: "date", label: "Date" },
  { value: "time", label: "Time" },
  { value: "checkbox", label: "Yes / No" },
  { value: "select", label: "Choices" },
];

export type FormField = {
  /** JSON key (must be unique within a form). */
  id: string;
  /** Visitor-facing label. */
  label: string;
  type: FormFieldType;
  placeholder?: string;
  required?: boolean;
  /** Options for `select` type. UI-side model is a flat string array;
   * persisted to disk as `options:[{value,label}]` (the shape widget.js
   * actually consumes). Legacy `choices:[]` is read for back-compat. */
  choices?: string[];
  /** Default value (e.g. `true` for a pre-checked checkbox). */
  defaultValue?: unknown;
  /** Widget-side rendering hint (e.g. "cphone"). Hidden in primary UI. */
  d_type?: string;
  /** Pass-through for any other fields the widget might use. */
  extra?: Record<string, unknown>;
};

export type ActionTrigger =
  | { kind: "start_chat" }
  | { kind: "starter_message"; quickStartString?: string }
  | { kind: "bot_decides"; description: string; code: string }
  | { kind: "bot_button_link" }
  | { kind: "bot_button_card" };

export const TRIGGER_KINDS: { kind: ActionTrigger["kind"]; label: string; description: string }[] = [
  {
    kind: "start_chat",
    label: "At the start of chat",
    description: "Show as soon as the visitor opens the chat.",
  },
  {
    kind: "starter_message",
    label: "When the visitor clicks a starter message",
    description: "Bound to one of the quick-reply chips above the input.",
  },
  {
    kind: "bot_decides",
    label: "When the bot decides during conversation",
    description: "The bot fires this based on a plain-English rule you write below.",
  },
  {
    kind: "bot_button_link",
    label: "When the bot replies with a button (link)",
    description: "Triggered by a button in a link card the bot returns.",
  },
  {
    kind: "bot_button_card",
    label: "When the bot replies with a button (card)",
    description: "Triggered by a button in a content card the bot returns.",
  },
];

export type ActionFormConfig = {
  /** "auth_form" renders the widget's built-in auth UI; "custom" is the generic form. */
  preset: "auth_form" | "custom";
  title?: string;
  description?: string;
  fields: FormField[];
  submitLabel: string;
  postSubmissionMessage?: string;
  /** Mirror of `form_schema.id`. Defaults to the action name. */
  id?: string;
};

export type ActionAlertConfig = {
  recipients: string[];
  /** What the LLM-generated summary should include. */
  summaryPrompt?: string;
  /** Whether to ask the LLM to summarize the conversation as part of the alert. */
  includeTranscript: boolean;
};

export type ActionLinkConfig = {
  url: string;
  openInBackground: boolean;
};

export type ActionConfirmation = {
  enabled: boolean;
  message?: string;
};

export type ActionLimits = {
  /** Cap firings per visitor (`trigger.allow_limit + limit`). */
  perVisitorEnabled: boolean;
  perVisitorLimit?: number;
  postLimitMessage?: string;
  postLimitQuery?: string;
  /** Per-session firing cap (`trigger.instances`). */
  perSessionMax?: number;
};

export type ActionFollowUp = {
  message?: string;
  query?: string;
};

export type ActionKind = "show_form" | "send_alert" | "open_link" | "advanced";

export type Action = {
  /** Internal name; also the JSON key in `c_forms_selected`. */
  name: string;
  kind: ActionKind;
  trigger: ActionTrigger;
  /** Skills this action attaches to. Empty = orphan (unattached). */
  belongsTo: string[];

  // Per-kind configuration. `advanced` may have multiple set.
  form?: ActionFormConfig;
  alert?: ActionAlertConfig;
  link?: ActionLinkConfig;

  // Shared advanced settings.
  confirmation?: ActionConfirmation;
  limits?: ActionLimits;
  followUp?: ActionFollowUp;

  // Display / UI labels (mirror trigger.label / trigger.title).
  label?: string;
  title?: string;

  /** Raw fields we didn't recognize — preserved on save. */
  _rawFormSchema?: Record<string, unknown>;
  _rawTrigger?: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// Legacy shape (the on-disk form item)
// ---------------------------------------------------------------------------

export type LegacyFormItem = {
  name: string;
  form_schema: Record<string, unknown>;
  trigger: Record<string, unknown>;
};

// Pull a string out of a loose record, swallowing nullish/non-string values.
function str(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function bool(v: unknown): boolean {
  return Boolean(v);
}

function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return undefined;
}

function strArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

// ---------------------------------------------------------------------------
// flatten / serialize entry list
// ---------------------------------------------------------------------------

export function flattenForms(arr: unknown): LegacyFormItem[] {
  if (!Array.isArray(arr)) return [];
  const out: LegacyFormItem[] = [];
  for (const entry of arr) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    for (const [name, v] of Object.entries(entry as Record<string, unknown>)) {
      const obj = (v && typeof v === "object" ? (v as Record<string, unknown>) : {});
      out.push({
        name,
        form_schema: (obj.form_schema as Record<string, unknown>) ?? {},
        trigger: (obj.trigger as Record<string, unknown>) ?? {},
      });
    }
  }
  return out;
}

export function serializeForms(items: LegacyFormItem[]): Record<string, unknown>[] {
  return items.map(({ name, form_schema, trigger }) => ({
    [name]: { form_schema, trigger },
  }));
}

// ---------------------------------------------------------------------------
// kind detection
// ---------------------------------------------------------------------------

function hasFormFields(form_schema: Record<string, unknown>): boolean {
  const form = form_schema.form;
  if (!form || typeof form !== "object" || Array.isArray(form)) return false;
  // "submit" is the auto-included submit button — it doesn't count as a real
  // user field.
  return Object.keys(form).some((k) => k !== "submit");
}

function hasAlert(trigger: Record<string, unknown>): boolean {
  return bool(trigger.email_alert) || strArray(trigger.alert_emails).length > 0 || str(trigger.alert_emails) !== undefined;
}

function hasLink(trigger: Record<string, unknown>): boolean {
  if (bool(trigger.utm_tracking)) return true;
  const q = trigger.utm_tracking_query;
  if (q && typeof q === "object" && !Array.isArray(q)) {
    return Boolean(str((q as Record<string, unknown>).ct_url));
  }
  return false;
}

export function inferKind(item: LegacyFormItem): ActionKind {
  const flags = [
    hasFormFields(item.form_schema),
    hasAlert(item.trigger),
    hasLink(item.trigger),
  ];
  const set = flags.filter(Boolean).length;
  if (set > 1) return "advanced";
  if (flags[1]) return "send_alert";
  if (flags[2]) return "open_link";
  return "show_form"; // default for empty new actions and form-only entries
}

// ---------------------------------------------------------------------------
// trigger code generation
// ---------------------------------------------------------------------------

export function generateTriggerCode(skillName: string, existing: Iterable<string>): string {
  const used = new Set<string>();
  for (const c of existing) used.add(c);
  const prefix = (skillName || "action").replace(/[^a-zA-Z0-9_]/g, "_").slice(0, 20) || "action";
  for (let i = 0; i < 64; i++) {
    const suffix = Math.random().toString(36).slice(2, 6);
    const code = `${prefix}_${suffix}`;
    if (!used.has(code)) return code;
  }
  // Pathological fallback — collisions for 64 attempts are statistically
  // impossible at this volume but the loop must terminate.
  return `${prefix}_${Date.now().toString(36)}`;
}

export function collectTriggerCodes(items: LegacyFormItem[]): string[] {
  const out: string[] = [];
  for (const it of items) {
    const c = str(it.trigger.trigger_code);
    if (c) out.push(c);
  }
  return out;
}

// ---------------------------------------------------------------------------
// trigger parse / build
// ---------------------------------------------------------------------------

function parseTrigger(t: Record<string, unknown>, fallbackName: string): ActionTrigger {
  const value = str(t.value) ?? "PROMPT";
  switch (value) {
    case "START_CHAT":
      return { kind: "start_chat" };
    case "STARTER_MESSAGES":
      return { kind: "starter_message", quickStartString: str(t.quick_start_string) };
    case "BUTTON_ON_LINK":
      return { kind: "bot_button_link" };
    case "BUTTON_ON_CARD":
      return { kind: "bot_button_card" };
    case "PROMPT":
    default:
      return {
        kind: "bot_decides",
        description: str(t.trigger_prompt) ?? "",
        // Older configs may not have a code; generate one lazily if blank.
        // Callers that know the parent skill can replace via
        // `generateTriggerCode`.
        code: str(t.trigger_code) ?? `${fallbackName}_${Math.random().toString(36).slice(2, 6)}`,
      };
  }
}

function buildTrigger(action: Action): Record<string, unknown> {
  const base: Record<string, unknown> = { ...(action._rawTrigger ?? {}) };
  // Strip prior-shape fields so we rebuild cleanly.
  delete base.value;
  delete base.label;
  delete base.title;
  delete base.trigger_code;
  delete base.trigger_prompt;
  delete base.quick_start_string;

  switch (action.trigger.kind) {
    case "start_chat":
      base.value = "START_CHAT";
      break;
    case "starter_message":
      base.value = "STARTER_MESSAGES";
      if (action.trigger.quickStartString) base.quick_start_string = action.trigger.quickStartString;
      break;
    case "bot_decides":
      base.value = "PROMPT";
      base.trigger_prompt = action.trigger.description;
      base.trigger_code = action.trigger.code;
      break;
    case "bot_button_link":
      base.value = "BUTTON_ON_LINK";
      break;
    case "bot_button_card":
      base.value = "BUTTON_ON_CARD";
      break;
  }
  if (action.label) base.label = action.label;
  if (action.title) base.title = action.title;

  // belongs_to / shared advanced settings
  base.belongs_to = action.belongsTo.length > 0 ? [...action.belongsTo] : undefined;

  // Alert
  if (action.alert) {
    base.email_alert = true;
    base.alert_emails = action.alert.recipients.join(", ");
    if (action.alert.summaryPrompt) base.analysis_prompt = action.alert.summaryPrompt;
    if (action.alert.includeTranscript) base.submit_query = true;
  } else {
    delete base.email_alert;
    delete base.alert_emails;
    delete base.analysis_prompt;
    delete base.submit_query;
  }

  // Link / UTM
  if (action.link) {
    base.utm_tracking = true;
    base.utm_tracking_query = {
      ct_url: action.link.url,
      open_in_bg: Boolean(action.link.openInBackground),
    };
  } else {
    delete base.utm_tracking;
    delete base.utm_tracking_query;
  }

  // Confirmation
  if (action.confirmation?.enabled) {
    base.user_confirmation = true;
    if (action.confirmation.message) {
      base.user_confirmation_message = action.confirmation.message;
    } else {
      delete base.user_confirmation_message;
    }
  } else {
    delete base.user_confirmation;
    delete base.user_confirmation_message;
  }

  // Limits
  if (action.limits?.perVisitorEnabled) {
    base.allow_limit = true;
    if (action.limits.perVisitorLimit !== undefined) base.limit = action.limits.perVisitorLimit;
    const post: Record<string, unknown> = {};
    if (action.limits.postLimitMessage) post.attention_text = action.limits.postLimitMessage;
    if (action.limits.postLimitQuery) post.attention_query = action.limits.postLimitQuery;
    if (Object.keys(post).length > 0) base.post_limit_query = post;
    else delete base.post_limit_query;
  } else {
    delete base.allow_limit;
    delete base.limit;
    delete base.post_limit_query;
  }
  if (action.limits?.perSessionMax !== undefined) {
    base.instances = action.limits.perSessionMax;
  } else {
    delete base.instances;
  }

  // Follow-up
  if (action.followUp && (action.followUp.message || action.followUp.query)) {
    const post: Record<string, unknown> = {};
    if (action.followUp.message) post.attention_text = action.followUp.message;
    if (action.followUp.query) post.attention_query = action.followUp.query;
    base.post_submit_query = post;
  } else {
    delete base.post_submit_query;
  }

  // Strip undefined to keep JSON small.
  for (const k of Object.keys(base)) {
    if (base[k] === undefined) delete base[k];
  }
  return base;
}

// ---------------------------------------------------------------------------
// form schema parse / build
// ---------------------------------------------------------------------------

function parseField(id: string, def: unknown): FormField | null {
  if (id === "submit") return null; // handled separately
  if (!def || typeof def !== "object" || Array.isArray(def)) return null;
  const d = def as Record<string, unknown>;
  const rawType = str(d.type) ?? "text";
  // Legacy alias: older configs wrote `type: "dropdown"` with `choices: []`.
  // Widget.js only renders dropdowns when `type === "select"` with `options`.
  const normalizedType = rawType === "dropdown" ? "select" : rawType;
  const type: FormFieldType = (FIELD_TYPES.find((t) => t.value === normalizedType)?.value ?? "text");
  // Read `options:[{value,label}]` (current widget contract) first; fall
  // back to legacy `choices:[string]`.
  const optionLabels = Array.isArray(d.options)
    ? (d.options as unknown[])
        .map((o) => (o && typeof o === "object" ? str((o as Record<string, unknown>).label) : undefined))
        .filter((s): s is string => Boolean(s))
    : [];
  const choices = optionLabels.length > 0 ? optionLabels : strArray(d.choices);
  const known = new Set(["label", "type", "placeholder", "required", "d_type", "choices", "options", "value"]);
  const extra: Record<string, unknown> = {};
  for (const k of Object.keys(d)) {
    if (!known.has(k)) extra[k] = d[k];
  }
  return {
    id,
    label: str(d.label) ?? "",
    type,
    placeholder: str(d.placeholder),
    required: bool(d.required),
    choices: choices.length > 0 ? choices : undefined,
    defaultValue: d.value,
    d_type: str(d.d_type),
    extra: Object.keys(extra).length > 0 ? extra : undefined,
  };
}

// Slugify a choice label into a stable `value` (e.g. "Best fit" → "best_fit").
// Empty / collision-prone results fall back to the index-suffixed slug.
function slugifyChoice(label: string, index: number): string {
  const base = label.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  return base || `option_${index + 1}`;
}

function buildField(f: FormField): Record<string, unknown> {
  const out: Record<string, unknown> = {
    label: f.label,
    type: f.type,
  };
  if (f.placeholder) out.placeholder = f.placeholder;
  if (f.required) out.required = true;
  if (f.d_type) out.d_type = f.d_type;
  if (f.type === "select" && f.choices && f.choices.length > 0) {
    // Persist as the shape widget.js actually consumes:
    // `options: [{value, label}, ...]`.
    const seen = new Set<string>();
    out.options = f.choices.map((label, i) => {
      let value = slugifyChoice(label, i);
      while (seen.has(value)) value = `${value}_${i + 1}`;
      seen.add(value);
      return { value, label };
    });
  }
  if (f.defaultValue !== undefined) out.value = f.defaultValue;
  if (f.extra) Object.assign(out, f.extra);
  return out;
}

function parseFormConfig(form_schema: Record<string, unknown>): ActionFormConfig {
  const id = str(form_schema.id);
  const preset: "auth_form" | "custom" = id === "auth_form" ? "auth_form" : "custom";
  const form = (form_schema.form && typeof form_schema.form === "object" && !Array.isArray(form_schema.form)
    ? (form_schema.form as Record<string, unknown>)
    : {});
  const submitDef = (form.submit && typeof form.submit === "object" ? (form.submit as Record<string, unknown>) : {});
  const fields: FormField[] = [];
  for (const [k, v] of Object.entries(form)) {
    const parsed = parseField(k, v);
    if (parsed) fields.push(parsed);
  }
  return {
    preset,
    id,
    title: str(form_schema.title),
    description: str(form_schema.description),
    fields,
    submitLabel: str(submitDef.label) ?? "Submit",
    postSubmissionMessage: str(form_schema.post_submission_message),
  };
}

function buildFormConfig(action: Action): Record<string, unknown> | undefined {
  if (!action.form) return undefined;
  const f = action.form;
  const out: Record<string, unknown> = { ...(action._rawFormSchema ?? {}) };
  out.id = f.preset === "auth_form" ? "auth_form" : (f.id || action.name);
  out.name = action.name;
  if (f.title !== undefined) out.title = f.title;
  if (f.description !== undefined) out.description = f.description;
  if (f.postSubmissionMessage !== undefined) out.post_submission_message = f.postSubmissionMessage;

  const formDict: Record<string, unknown> = {};
  for (const fld of f.fields) {
    if (!fld.id) continue;
    formDict[fld.id] = buildField(fld);
  }
  formDict.submit = { label: f.submitLabel || "Submit", type: "submit" };
  out.form = formDict;
  return out;
}

// ---------------------------------------------------------------------------
// adapter: legacy <-> Action
// ---------------------------------------------------------------------------

export function actionFromForm(item: LegacyFormItem): Action {
  const trigger = parseTrigger(item.trigger, item.name);
  const kind = inferKind(item);
  const formCfg = hasFormFields(item.form_schema) || str(item.form_schema.id) === "auth_form"
    ? parseFormConfig(item.form_schema)
    : undefined;
  const alertCfg: ActionAlertConfig | undefined = hasAlert(item.trigger)
    ? {
        recipients: str(item.trigger.alert_emails)
          ? str(item.trigger.alert_emails)!.split(",").map((s) => s.trim()).filter(Boolean)
          : [],
        summaryPrompt: str(item.trigger.analysis_prompt),
        includeTranscript: bool(item.trigger.submit_query),
      }
    : undefined;
  const utmQ = (item.trigger.utm_tracking_query && typeof item.trigger.utm_tracking_query === "object"
    ? (item.trigger.utm_tracking_query as Record<string, unknown>)
    : null);
  const linkCfg: ActionLinkConfig | undefined = hasLink(item.trigger) && utmQ && str(utmQ.ct_url)
    ? {
        url: str(utmQ.ct_url) ?? "",
        openInBackground: bool(utmQ.open_in_bg),
      }
    : undefined;

  const confirmation: ActionConfirmation | undefined = bool(item.trigger.user_confirmation)
    ? { enabled: true, message: str(item.trigger.user_confirmation_message) }
    : undefined;

  const postLimit = (item.trigger.post_limit_query && typeof item.trigger.post_limit_query === "object"
    ? (item.trigger.post_limit_query as Record<string, unknown>)
    : null);
  const limits: ActionLimits | undefined =
    bool(item.trigger.allow_limit) || num(item.trigger.instances) !== undefined
      ? {
          perVisitorEnabled: bool(item.trigger.allow_limit),
          perVisitorLimit: num(item.trigger.limit),
          postLimitMessage: postLimit ? str(postLimit.attention_text) : undefined,
          postLimitQuery: postLimit ? str(postLimit.attention_query) : undefined,
          perSessionMax: num(item.trigger.instances),
        }
      : undefined;

  const postSubmit = (item.trigger.post_submit_query && typeof item.trigger.post_submit_query === "object"
    ? (item.trigger.post_submit_query as Record<string, unknown>)
    : null);
  const followUp: ActionFollowUp | undefined = postSubmit
    ? {
        message: str(postSubmit.attention_text),
        query: str(postSubmit.attention_query),
      }
    : undefined;

  // Stash unrecognized keys so `actionToForm` round-trips losslessly.
  const knownTriggerKeys = new Set([
    "value", "label", "title",
    "trigger_code", "trigger_prompt", "quick_start_string",
    "analysis_prompt", "alert_emails", "email_alert", "submit_query",
    "user_confirmation", "user_confirmation_message",
    "allow_limit", "limit", "post_limit_query",
    "post_submit_query",
    "utm_tracking", "utm_tracking_query",
    "instances",
    "belongs_to",
  ]);
  const rawTrigger: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(item.trigger)) {
    if (!knownTriggerKeys.has(k)) rawTrigger[k] = v;
  }

  const knownFormKeys = new Set(["id", "name", "title", "description", "post_submission_message", "form"]);
  const rawForm: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(item.form_schema)) {
    if (!knownFormKeys.has(k)) rawForm[k] = v;
  }

  return {
    name: item.name,
    kind,
    trigger,
    belongsTo: strArray(item.trigger.belongs_to),
    form: formCfg,
    alert: alertCfg,
    link: linkCfg,
    confirmation,
    limits,
    followUp,
    label: str(item.trigger.label),
    title: str(item.trigger.title),
    _rawFormSchema: Object.keys(rawForm).length > 0 ? rawForm : undefined,
    _rawTrigger: Object.keys(rawTrigger).length > 0 ? rawTrigger : undefined,
  };
}

export function actionToForm(action: Action): LegacyFormItem {
  const trigger = buildTrigger(action);
  const form_schema = buildFormConfig(action) ?? {
    id: action.name,
    name: action.name,
    title: "",
    description: "",
    post_submission_message: "",
    form: { submit: { label: "Submit", type: "submit" } },
  };
  return {
    name: action.name,
    form_schema,
    trigger,
  };
}

// ---------------------------------------------------------------------------
// helpers used by editors
// ---------------------------------------------------------------------------

export function actionsFromConfig(c_forms_selected: unknown): Action[] {
  return flattenForms(c_forms_selected).map(actionFromForm);
}

export function actionsToConfig(actions: Action[]): Record<string, unknown>[] {
  return serializeForms(actions.map(actionToForm));
}

export function defaultAction(skillName: string, existingCodes: Iterable<string>, existingNames: Iterable<string>): Action {
  const namesSet = new Set(existingNames);
  let name = `${skillName || "action"}_form`;
  let n = 1;
  while (namesSet.has(name)) {
    n += 1;
    name = `${skillName || "action"}_form_${n}`;
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

// Lightweight email check — same shape every browser type="email" uses.
// We deliberately don't enforce RFC-5322; the goal is to catch typos, not
// to validate. False negatives are fine; false positives (valid emails
// flagged invalid) are not.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(s: string): boolean {
  return EMAIL_RE.test(s.trim());
}

export function findInvalidEmails(recipients: string[]): string[] {
  return recipients.filter((r) => !isValidEmail(r));
}

export function summarizeAction(action: Action): string {
  const triggerLabel = TRIGGER_KINDS.find((t) => t.kind === action.trigger.kind)?.label ?? "Unknown trigger";
  const kindLabel = (() => {
    switch (action.kind) {
      case "show_form":
        return "Show a form";
      case "send_alert":
        return "Send an alert";
      case "open_link":
        return "Open a link";
      case "advanced":
        return "Combination";
    }
  })();
  return `${triggerLabel} → ${kindLabel}`;
}
