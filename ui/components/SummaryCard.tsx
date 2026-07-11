"use client";

import { Bot, FileText, MessageSquare, Settings, Sparkles, Wrench } from "lucide-react";
import { actionsFromConfig } from "@/lib/actions";
import { LLM_DEFAULTS } from "@/lib/defaults";

// Top-of-page summary so admins see what the bot does at a glance,
// before drilling into any individual editor tab.

type Props = {
  config: Record<string, unknown>;
  fileCount?: number;
  hasApiKey?: boolean;
  onJump: (tab: string) => void;
};

function asObj(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export default function SummaryCard({ config, fileCount, hasApiKey, onJump }: Props) {
  const topics = asObj(config.topics);
  const skillNames = Object.keys(topics);
  const actions = actionsFromConfig(config.c_forms_selected);
  const widget = asObj(config.widget);
  const theme = asObj(widget.theme);
  const themeColor = str(theme.header_background);
  const globalPrompts = asObj(config.global_prompts);
  const welcome = str(globalPrompts.welcome_message) || str(widget.welcome_message);
  const llm = asObj(config.llm);
  const model = str(llm.model) || "(deployment default)";

  const ragSkillCount = skillNames.filter((n) => Boolean(asObj(topics[n]).requires_rag)).length;

  return (
    <div className="card" style={{ marginBottom: 16, padding: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
        <SummaryTile
          icon={<Sparkles size={14} strokeWidth={1.75} />}
          label="Skills"
          primary={`${skillNames.length}`}
          secondary={
            skillNames.length === 0
              ? "None yet — add one to enable chat"
              : skillNames.slice(0, 3).join(" · ") + (skillNames.length > 3 ? ` +${skillNames.length - 3}` : "")
          }
          onClick={() => onJump("topics")}
          ctaLabel={skillNames.length === 0 ? "Add a skill →" : "Edit →"}
        />
        <SummaryTile
          icon={<Bot size={14} strokeWidth={1.75} />}
          label="Actions"
          primary={`${actions.length}`}
          secondary={
            actions.length === 0
              ? "Optional — let the bot show forms or send alerts"
              : actions.map((a) => a.name).slice(0, 2).join(" · ")
          }
          onClick={() => onJump("forms")}
          ctaLabel={actions.length === 0 ? "Add an action →" : "Edit →"}
        />
        <SummaryTile
          icon={<MessageSquare size={14} strokeWidth={1.75} />}
          label="Voice"
          primary={welcome ? truncate(welcome, 30) : "(no welcome)"}
          secondary="Welcome, error, slot prompts"
          onClick={() => onJump("identity")}
          ctaLabel="Edit →"
        />
        <SummaryTile
          icon={<FileText size={14} strokeWidth={1.75} />}
          label="Knowledge"
          primary={fileCount === undefined ? "—" : `${fileCount} file${fileCount === 1 ? "" : "s"}`}
          secondary={ragSkillCount > 0 ? `Used by ${ragSkillCount} skill${ragSkillCount === 1 ? "" : "s"}` : "Not used by any skill"}
          onClick={() => onJump("rag")}
          ctaLabel="Tune →"
        />
        <SummaryTile
          icon={<Settings size={14} strokeWidth={1.75} />}
          label="Appearance"
          primary={themeColor ? "Custom theme" : "Default theme"}
          secondary={`Launcher: ${str(widget.launcher_position) || "right"}`}
          onClick={() => onJump("widget")}
          ctaLabel="Edit →"
        />
        <SummaryTile
          icon={<Wrench size={14} strokeWidth={1.75} />}
          label="Advanced"
          primary={model}
          secondary={
            hasApiKey === undefined
              ? `Temp ${typeof llm.temperature === "number" ? llm.temperature : LLM_DEFAULTS.temperature}`
              : hasApiKey
              ? "API key set"
              : "Falls back to deployment key"
          }
          onClick={() => onJump("llm")}
          ctaLabel="Edit →"
        />
      </div>
    </div>
  );
}

function SummaryTile({
  icon,
  label,
  primary,
  secondary,
  onClick,
  ctaLabel,
}: {
  icon: React.ReactNode;
  label: string;
  primary: string;
  secondary: string;
  onClick?: () => void;
  ctaLabel?: string;
}) {
  return (
    <div
      style={{
        border: "1px solid var(--panel-border)",
        borderRadius: 6,
        padding: 12,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div className="row" style={{ alignItems: "center", gap: 6, color: "var(--text-muted)", fontSize: 12 }}>
        {icon}
        <span>{label}</span>
      </div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{primary}</div>
      <div className="muted" style={{ fontSize: 11, lineHeight: 1.4 }}>{secondary}</div>
      {ctaLabel && onClick && (
        <button
          className="ghost compact"
          onClick={onClick}
          style={{ alignSelf: "flex-start", marginTop: 4 }}
        >
          {ctaLabel}
        </button>
      )}
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
