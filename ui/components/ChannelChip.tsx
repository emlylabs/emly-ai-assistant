"use client";

import type { ChannelType } from "@/lib/api";

type ChannelChipProps = {
  kind: ChannelType;
  /** Optional override letter (defaults to the kind's canonical letter). */
  letter?: string;
  className?: string;
  title?: string;
};

const PALETTE: Record<ChannelType, { letter: string; cls: string; label: string }> = {
  web_widget: { letter: "W", cls: "ch-web", label: "Web widget" },
  slack: { letter: "S", cls: "ch-slack", label: "Slack" },
  teams: { letter: "M", cls: "ch-ms", label: "Microsoft Teams" },
  telegram: { letter: "T", cls: "ch-int", label: "Telegram" },
  whatsapp_cloud: { letter: "W", cls: "ch-wa", label: "WhatsApp" },
  google_chat: { letter: "G", cls: "ch-api", label: "Google Chat" },
};

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
