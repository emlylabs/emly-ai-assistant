// Pure validation rules for the bot config blob. Surfaced by the
// validation panel above each section in `config/page.tsx`.
//
// This module is the *client-side preview* — instant feedback as the
// admin types. Authoritative validation lives server-side in
// `services/config_validation.py`; the publish / save gates trust the
// server. Keep the rule keys and severity tags in sync between the two.
//
// Severity contract (consumed by the publish/save gates):
//   - "error"   = blocks publish; blocks save when the workspace
//                 `validation.block_save_on_errors` flag is on
//   - "warning" = surfaces in the workspace overview + picker tooltips,
//                 but doesn't block save or publish
//   - "info"    = advisory observation, dismissible
//
// Runtime checks (missing API key with no fallback, empty origin
// allowlist, unrecognized model) only live on the server (the RT*
// family in `services/config_validation.py`) because the client can't
// read encrypted keys or deployment envs.
//
// Adding a rule: append to `validateConfig`, mirror it in the Python
// validator, and document it in ux-redesign.md §X.1.

import { Action, actionsFromConfig } from "@/lib/actions";

export type IssueSeverity = "error" | "warning" | "info";

export type Issue = {
  severity: IssueSeverity;
  /** Stable id for dismissal / dedupe. */
  key: string;
  /** Tab to navigate to on click. */
  section: "identity" | "widget" | "topics" | "forms" | "llm" | "rag" | "limits" | "raw" | "knowledge";
  message: string;
  /** Optional CTA the panel renders next to the message. */
  cta?: { label: string; href?: string; tab?: string };
};

type Cfg = Record<string, unknown>;

function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

export type ValidationContext = {
  /** File count for the bot, fetched out-of-band by the page. */
  fileCount?: number;
  /** Bot slug, used to build absolute hrefs for CTAs. */
  botSlug?: string;
};

export function validateConfig(config: Cfg, ctx: ValidationContext = {}): Issue[] {
  const issues: Issue[] = [];
  const topics = asObj(config.topics);
  const topicNames = Object.keys(topics);
  const globalPrompts = asObj(config.global_prompts);
  const limits = asObj(config.limits);
  const rag = asObj(config.rag);

  // R13 — empty bot
  if (topicNames.length === 0) {
    issues.push({
      severity: "info",
      key: "R13_empty_bot",
      section: "topics",
      message: "This bot has no skills yet. Add at least one to enable chat.",
    });
  }

  // Per-topic checks
  for (const name of topicNames) {
    const t = asObj(topics[name]);
    const requiresRag = Boolean(t.requires_rag);
    const skipSlotFilling = Boolean(t.skip_slot_filling);
    const slots = Array.isArray(t.slots) ? (t.slots as Record<string, unknown>[]) : [];
    const prompts = asObj(t.prompts);
    const llmResponse = str(prompts.llm_response).trim();

    // R1 — empty prompt
    if (!llmResponse) {
      issues.push({
        severity: "error",
        key: `R1_empty_prompt_${name}`,
        section: "topics",
        message: `Skill "${name}" has no response prompt. The bot will fall back to a generic reply.`,
      });
    }

    // R2 — RAG enabled but no files
    if (requiresRag && ctx.fileCount === 0) {
      issues.push({
        severity: "error",
        key: `R2_rag_no_files_${name}`,
        section: "topics",
        message: `Skill "${name}" answers from your knowledge base, but no files are uploaded yet.`,
        cta: { label: "Upload files", href: ctx.botSlug ? `/bots/${ctx.botSlug}/files` : undefined },
      });
    }

    // R3 — collect info but 0 fields
    if (!skipSlotFilling && slots.length === 0) {
      issues.push({
        severity: "warning",
        key: `R3_no_fields_${name}`,
        section: "topics",
        message: `Skill "${name}" is set to collect information but has no fields defined.`,
      });
    }

    // R4 — collects fields but prompt missing {filled_slots}
    if (slots.length > 0 && llmResponse && !llmResponse.includes("{filled_slots}")) {
      issues.push({
        severity: "warning",
        key: `R4_no_filled_slots_${name}`,
        section: "topics",
        message: `Skill "${name}" collects fields but its prompt doesn't reference {filled_slots} (will be appended automatically).`,
      });
    }

    // R9 — required slot in skip_slot_filling skill
    if (skipSlotFilling) {
      const requiredSlots = slots.filter((s) => s && (s as Record<string, unknown>).required);
      if (requiredSlots.length > 0) {
        issues.push({
          severity: "warning",
          key: `R9_skip_slot_with_required_${name}`,
          section: "topics",
          message: `Skill "${name}" has required fields but is set to skip information collection — those fields won't be asked.`,
        });
      }
    }
  }

  // Action-level checks (R5, R6, R7, R10, R11)
  let actions: Action[];
  try {
    actions = actionsFromConfig(config.c_forms_selected);
  } catch {
    actions = [];
  }
  const knownSkills = new Set(topicNames);
  const triggerCodes = new Map<string, number>();
  const actionNames = new Map<string, number>();

  for (const a of actions) {
    actionNames.set(a.name, (actionNames.get(a.name) ?? 0) + 1);
    if (a.trigger.kind === "bot_decides" && a.trigger.code) {
      triggerCodes.set(a.trigger.code, (triggerCodes.get(a.trigger.code) ?? 0) + 1);
    }
    // R5 — belongs_to references unknown skill
    for (const s of a.belongsTo) {
      if (!knownSkills.has(s)) {
        issues.push({
          severity: "warning",
          key: `R5_action_orphan_${a.name}_${s}`,
          section: "forms",
          message: `Action "${a.name}" is attached to skill "${s}", which doesn't exist.`,
        });
      }
    }
    // R6 — show_form with 0 fields
    if (a.kind === "show_form" && a.form && a.form.preset === "custom" && a.form.fields.length === 0) {
      issues.push({
        severity: "warning",
        key: `R6_form_no_fields_${a.name}`,
        section: "forms",
        message: `Action "${a.name}" shows a form but has no fields.`,
      });
    }
    // R7 — alert with no recipients
    if (a.alert && a.alert.recipients.length === 0) {
      issues.push({
        severity: "warning",
        key: `R7_alert_no_recipients_${a.name}`,
        section: "forms",
        message: `Action "${a.name}" sends an alert but has no recipients.`,
      });
    }
  }
  // R10 — duplicate skill names (shouldn't happen via UI; possible via Raw JSON)
  const seenSkillNames = new Set<string>();
  for (const n of topicNames) {
    if (seenSkillNames.has(n)) {
      issues.push({
        severity: "error",
        key: `R10_dup_skill_${n}`,
        section: "topics",
        message: `Two skills share the name "${n}". The router will only see one.`,
      });
    }
    seenSkillNames.add(n);
  }
  // R11 — duplicate trigger codes
  for (const [code, count] of triggerCodes) {
    if (count > 1) {
      issues.push({
        severity: "error",
        key: `R11_dup_trigger_${code}`,
        section: "forms",
        message: `Trigger code "${code}" is used by ${count} actions. The bot will only fire one.`,
      });
    }
  }
  // R10b — duplicate action names
  for (const [n, count] of actionNames) {
    if (count > 1) {
      issues.push({
        severity: "error",
        key: `R10b_dup_action_${n}`,
        section: "forms",
        message: `Two actions share the name "${n}". Saved config keeps only the last.`,
      });
    }
  }

  // R8 — aggressive embedding threshold
  const threshold = num(rag.embedding_threshold);
  if (threshold !== undefined && threshold > 0.5) {
    issues.push({
      severity: "info",
      key: "R8_high_threshold",
      section: "rag",
      message: `Embedding threshold ${threshold} is high — many relevant hits may be filtered out.`,
    });
  }

  // R12 — daily token cap without per-user rate limit
  if (num(limits.daily_token_cap) !== undefined && num(limits.messages_per_minute_per_user) === undefined) {
    issues.push({
      severity: "warning",
      key: "R12_token_cap_no_rate_limit",
      section: "limits",
      message: "Daily token cap is set but no per-user rate limit. A single visitor could burn the cap quickly.",
    });
  }

  // R14 — wildcard origin
  const allowedOrigins = Array.isArray(limits.widget_allowed_origins) ? limits.widget_allowed_origins : [];
  if (allowedOrigins.includes("*")) {
    issues.push({
      severity: "info",
      key: "R14_wildcard_origin",
      section: "limits",
      message: "Widget allows embedding on any origin (`*`). Lock this down before going to production.",
    });
  }

  // R15 — blank welcome + no skills
  if (!str(globalPrompts.welcome_message).trim() && topicNames.length === 0) {
    issues.push({
      severity: "warning",
      key: "R15_blank_welcome_empty_bot",
      section: "identity",
      message: "Visitors will land on a blank chat with no welcome message and no skills.",
    });
  }

  issues.push(...fieldFormatIssues(config));
  return issues;
}

// Field-format rules (R16–R23). The server always blocks save on these.
const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;
const HEX_COLOR_RE = /^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;
const FUNC_COLOR_RE = /^(?:rgb|rgba|hsl|hsla)\(\s*[0-9.,%\s/+-]+\)$/i;
const THEME_COLOR_FIELDS: [string, string][] = [
  ["launcher_background", "Launcher background"],
  ["container_background", "Container background"],
  ["header_background", "Header background"],
  ["header_foreground", "Header foreground"],
  ["user_message_background", "User message background"],
  ["user_message_foreground", "User message foreground"],
  ["bot_message_background", "Bot message background"],
  ["bot_message_foreground", "Bot message foreground"],
];

export function isHttpUrl(value: string): boolean {
  if (!value || /\s/.test(value)) return false;
  try {
    const u = new URL(value);
    return (u.protocol === "http:" || u.protocol === "https:") && !!u.hostname;
  } catch {
    return false;
  }
}

export function isValidColor(value: string): boolean {
  const v = value.trim();
  return v.toLowerCase() === "transparent" || HEX_COLOR_RE.test(v) || FUNC_COLOR_RE.test(v);
}

function present(v: unknown): boolean {
  return v !== undefined && v !== null && !(typeof v === "string" && !v.trim());
}

function inRange(v: unknown, lo: number, hi: number): boolean {
  const n = num(v);
  return n !== undefined && n >= lo && n <= hi;
}

function fieldFormatIssues(config: Cfg): Issue[] {
  const issues: Issue[] = [];
  const widget = asObj(config.widget);
  const theme = asObj(widget.theme);
  const llm = asObj(config.llm);
  const rag = asObj(config.rag);
  const limits = asObj(config.limits);

  if (present(config.support_email)) {
    const bad = String(config.support_email)
      .split(",")
      .map((p) => p.trim())
      .filter((p) => !EMAIL_RE.test(p));
    if (bad.length > 0) {
      issues.push({
        severity: "error",
        key: "R16_invalid_support_email",
        section: "identity",
        message: `Support email "${bad[0]}" is not a valid email address.`,
      });
    }
  }

  if (present(widget.logo)) {
    const logo = String(widget.logo);
    if (!isHttpUrl(logo) && !logo.startsWith("data:image/")) {
      issues.push({
        severity: "error",
        key: "R17_invalid_logo_url",
        section: "identity",
        message: "Logo URL must be a full http:// or https:// address.",
      });
    }
  }

  for (const [field, label] of THEME_COLOR_FIELDS) {
    const color = theme[field];
    if (present(color) && !isValidColor(String(color))) {
      issues.push({
        severity: "error",
        key: `R18_invalid_color_${field}`,
        section: "widget",
        message: `${label} "${String(color)}" is not a colour. Use a hex value like #1a73e8, rgb(…), or transparent.`,
      });
    }
  }

  if (present(llm.api_endpoint) && !isHttpUrl(String(llm.api_endpoint))) {
    issues.push({
      severity: "error",
      key: "R19_invalid_api_endpoint",
      section: "llm",
      message: "API endpoint must be a full http:// or https:// address.",
    });
  }

  if (llm.temperature !== undefined && llm.temperature !== null && !inRange(llm.temperature, 0, 2)) {
    issues.push({
      severity: "error",
      key: "R20_temperature_range",
      section: "llm",
      message: "Temperature must be between 0 and 2.",
    });
  }
  if (rag.top_k !== undefined && rag.top_k !== null && !inRange(rag.top_k, 1, 50)) {
    issues.push({
      severity: "error",
      key: "R21_top_k_range",
      section: "rag",
      message: "Top K must be between 1 and 50.",
    });
  }
  if (
    rag.embedding_threshold !== undefined &&
    rag.embedding_threshold !== null &&
    !inRange(rag.embedding_threshold, 0, 1)
  ) {
    issues.push({
      severity: "error",
      key: "R22_threshold_range",
      section: "rag",
      message: "Score threshold must be between 0 and 1.",
    });
  }
  if (
    limits.max_file_size_mb !== undefined &&
    limits.max_file_size_mb !== null &&
    !inRange(limits.max_file_size_mb, 1, Number.POSITIVE_INFINITY)
  ) {
    issues.push({
      severity: "error",
      key: "R23_max_file_size_range",
      section: "limits",
      message: "Max file size must be at least 1 MB.",
    });
  }
  return issues;
}

export function groupIssues(issues: Issue[]): Record<IssueSeverity, Issue[]> {
  const out: Record<IssueSeverity, Issue[]> = { error: [], warning: [], info: [] };
  for (const i of issues) out[i.severity].push(i);
  return out;
}
