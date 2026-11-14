"use client";

import type { SessionChannelType } from "@/lib/api";

type ChannelChipProps = {
  kind: SessionChannelType;
  /** Optional override letter (defaults to the kind's canonical letter). */
  letter?: string;
  className?: string;
  title?: string;
};

const PALETTE: Record<SessionChannelType, { letter: string; cls: string; label: string }> = {
  web_widget: { letter: "W", cls: "ch-web", label: "Web widget" },
  slack: { letter: "S", cls: "ch-slack", label: "Slack" },
  teams: { letter: "M", cls: "ch-ms", label: "Microsoft Teams" },
  telegram: { letter: "T", cls: "ch-int", label: "Telegram" },
  whatsapp_cloud: { letter: "W", cls: "ch-wa", label: "WhatsApp" },
  google_chat: { letter: "G", cls: "ch-api", label: "Google Chat" },
  // A session or message whose channel install has since been deleted.
  unknown: { letter: "?", cls: "ch-api", label: "Removed channel" },
};

/** Human-readable name for a channel type; unknown types pass through. */
export function channelLabel(kind: string): string {
  return PALETTE[kind as SessionChannelType]?.label ?? kind;
}

/**
 * Single-letter coloured tile keyed by channel kind. Used inside table rows
 * and conversation list items where space is tight. The letter is short on
 * purpose (`W` / `S` / `M` / `T` / `G`); the title attribute carries the
 * full label for hover / a11y.
 */
export default function ChannelChip({ kind, letter, className, title }: ChannelChipProps) {
  const config = PALETTE[kind];
  if (!config) {
    return (
      <span className={["ch", "ch-api", className].filter(Boolean).join(" ")} title={title ?? kind}>
        ?
      </span>
    );
  }
  return (
    <span
      className={["ch", config.cls, className].filter(Boolean).join(" ")}
      title={title ?? config.label}
      aria-label={config.label}
    >
      {letter ?? config.letter}
    </span>
  );
}
