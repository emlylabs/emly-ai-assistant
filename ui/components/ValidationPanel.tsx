"use client";

import { useState } from "react";
import { AlertTriangle, CircleAlert, Info } from "lucide-react";
import { Issue, IssueSeverity } from "@/lib/config-validation";

const ICONS: Record<IssueSeverity, React.ReactNode> = {
  error: <CircleAlert size={14} strokeWidth={1.75} />,
  warning: <AlertTriangle size={14} strokeWidth={1.75} />,
  info: <Info size={14} strokeWidth={1.75} />,
};

const COLORS: Record<IssueSeverity, { bg: string; fg: string; border: string }> = {
  error: {
    bg: "color-mix(in oklch, var(--error, #c4314b) 8%, transparent)",
    fg: "var(--error, #c4314b)",
    border: "color-mix(in oklch, var(--error, #c4314b) 30%, transparent)",
  },
  warning: {
    bg: "color-mix(in oklch, #d4914a 10%, transparent)",
    fg: "#a96b30",
    border: "color-mix(in oklch, #d4914a 30%, transparent)",
  },
  info: {
    bg: "var(--panel-bg)",
    fg: "var(--text-muted)",
    border: "var(--panel-border)",
  },
};

type Props = {
  issues: Issue[];
  onJump?: (issue: Issue) => void;
};

export default function ValidationPanel({ issues, onJump }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const visible = issues.filter((i) => !dismissed.has(i.key));
  if (visible.length === 0) return null;

  // Order: errors > warnings > info; stable within each group.
  const ordered = [
    ...visible.filter((i) => i.severity === "error"),
    ...visible.filter((i) => i.severity === "warning"),
    ...visible.filter((i) => i.severity === "info"),
  ];

  const errorCount = visible.filter((i) => i.severity === "error").length;
  const warningCount = visible.filter((i) => i.severity === "warning").length;
  const infoCount = visible.filter((i) => i.severity === "info").length;

  return (
    <div
      role="region"
      aria-label="Configuration issues"
      aria-live="polite"
      style={{
        marginBottom: 16,
        border: "1px solid var(--panel-border)",
        borderRadius: 6,
        overflow: "hidden",
      }}
    >
      <button
        type="button"
        className="ghost"
        onClick={() => setCollapsed((v) => !v)}
        style={{
          width: "100%",
          justifyContent: "space-between",
          padding: "8px 12px",
          background: "var(--panel-bg)",
          borderRadius: 0,
          border: "none",
        }}
      >
        <span style={{ fontSize: 13 }}>
          <strong>Issues ({visible.length})</strong>
          {errorCount > 0 && <span style={{ color: COLORS.error.fg, marginLeft: 8 }}>{errorCount} error{errorCount === 1 ? "" : "s"}</span>}
          {warningCount > 0 && <span style={{ color: COLORS.warning.fg, marginLeft: 8 }}>{warningCount} warning{warningCount === 1 ? "" : "s"}</span>}
          {infoCount > 0 && <span style={{ color: COLORS.info.fg, marginLeft: 8 }}>{infoCount} info</span>}
        </span>
        <span style={{ fontSize: 12 }}>{collapsed ? "Show" : "Hide"}</span>
      </button>
      {!collapsed && (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {ordered.map((i) => {
            const colors = COLORS[i.severity];
            return (
              <li
                key={i.key}
                style={{
                  borderTop: "1px solid var(--panel-border)",
                  padding: "8px 12px",
                  background: colors.bg,
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  fontSize: 13,
                }}
              >
                <span style={{ color: colors.fg, paddingTop: 2 }}>{ICONS[i.severity]}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div>{i.message}</div>
                  {i.cta && (
                    <button
                      className="ghost compact"
                      onClick={() => onJump?.(i)}
                      style={{ marginTop: 4 }}
                    >
                      {i.cta.label} →
                    </button>
                  )}
                </div>
                {i.severity === "info" && (
                  <button
                    className="ghost compact-icon"
                    onClick={() => setDismissed((prev) => new Set([...prev, i.key]))}
                    aria-label="Dismiss"
                    style={{ color: colors.fg }}
                  >
                    ✕
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
