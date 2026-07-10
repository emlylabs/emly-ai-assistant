// Mirror of the auto-append logic in `agents/conversation_agent.py`
// `create_config()` (~lines 964-1008). The Python side appends
// `{context}` / `{user_input}` / `{filled_slots}` / `{history}` blocks
// at runtime if the user-authored prompt didn't include them. The UI
// runs the same logic client-side so the prompt editor can show the
// admin what the LLM will actually see.
//
// This is intentionally duplicated rather than fetched from the
// backend — the only callers are the prompt editor's preview, and a
// tiny lag-free preview is more useful than a backend round-trip.
//
// The Python code also weaves in trigger prompts from `c_forms_selected`
// via `process_trigger_prompts()`. We mirror that here too so the
// preview shows the full assembled prompt including any actions
// attached to this skill.

import { actionsFromConfig } from "@/lib/actions";

const CONTEXT_BLOCK = ["### Context starts: ###", "{context}", "### Context ends. ###"];
const USER_BLOCK = ["### User Request starts: ###", "{user_input}", "### User Request ends. ###"];
const SLOTS_BLOCK = ["### Preferences starts: ###", "{filled_slots}", "### Preferences ends. ###"];
const HISTORY_BLOCK = ["### History starts: ###", "{history}", "### History ends. ###"];

const ALLOWED_VARS = new Set(["{context}", "{user_input}", "{filled_slots}", "{history}"]);

/** Mirror of `clean_template_string` in `utils/utils.py`. */
function cleanTemplateString(text: string): string {
  if (!text) return text;
  // Match {{ }} groups (single or nested braces) — match Python regex r'\{+[^{}]*\}+'.
  let out = text;
  const allVars = out.match(/\{+[^{}]*\}+/g) ?? [];
  for (const v of allVars) {
    if (!ALLOWED_VARS.has(v)) {
      out = out.split(v).join("");
    }
  }
  // Strip empty braces.
  while (/\{\s*\}/.test(out)) {
    out = out.replace(/\{\s*\}+/g, "");
  }
  return out;
}

export type PromptPreviewArgs = {
  /** The user-authored llm_response for this topic. */
  promptBody: string;
  /** This topic's slot definitions (from config.topics[name].slots). */
  slots: Array<{ name: string }>;
  /** The full bot config — used to weave trigger prompts. */
  config: Record<string, unknown>;
  /** Topic name — used to filter actions whose belongs_to includes us. */
  topicName: string;
};

/** Build the same prompt the agent will assemble at runtime. */
export function previewAssembledPrompt(args: PromptPreviewArgs): string {
  const cleaned = cleanTemplateString(args.promptBody ?? "");
  const parts: string[] = cleaned ? [cleaned] : [];

  if (!cleaned.includes("{context}")) {
    parts.push(...CONTEXT_BLOCK);
  }
  if (!cleaned.includes("{user_input}")) {
    parts.push(...USER_BLOCK);
  }

  // Trigger prompts from attached actions.
  const actions = actionsFromConfig(args.config.c_forms_selected);
  const triggerLines: string[] = [];
  for (const a of actions) {
    if (!a.belongsTo.includes(args.topicName)) continue;
    if (a.trigger.kind !== "bot_decides") continue;
    const desc = a.trigger.description.trim();
    const code = a.trigger.code;
    if (desc && code) {
      triggerLines.push(`${desc}  respond with  <internal_code>${code}</internal_code>`);
    }
  }
  if (triggerLines.length > 0) {
    parts.push("");
    parts.push("### Special Instruction: ###");
    parts.push(triggerLines.join("\n"));
    parts.push("### End Special Instructions ###");
  }

  if (args.slots.length > 0 && !cleaned.includes("{filled_slots}")) {
    parts.push(...SLOTS_BLOCK);
  }
  if (!cleaned.includes("{history}")) {
    parts.push(...HISTORY_BLOCK);
  }

  return parts.join("\n");
}

/** Which placeholders are present in the user-authored body. */
export function detectPlaceholders(body: string): Record<string, boolean> {
  return {
    "{context}": body.includes("{context}"),
    "{user_input}": body.includes("{user_input}"),
    "{filled_slots}": body.includes("{filled_slots}"),
    "{history}": body.includes("{history}"),
  };
}
