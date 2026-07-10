"use client";

import type { ReactNode } from "react";

export type FeedTone = "default" | "success" | "warn" | "danger" | "info";

type FeedItemProps = {
  /** Icon rendered inside the small coloured tile on the left. Typically a
   * Lucide icon. */
  icon?: ReactNode;
  tone?: FeedTone;
  /** Primary line. Use `<strong>` inside to emphasise actor / object. */
  title: ReactNode;
  /** Optional second line in mono-muted ("audit trail" style). */
  meta?: ReactNode;
  /** Right-aligned timestamp. Pre-format on the caller side (e.g., "14:42"). */
  time?: ReactNode;
  className?: string;
};

const TONE_CLASS: Record<FeedTone, string> = {
  default: "",
  success: "success",
  warn: "warn",
  danger: "danger",
  info: "info",
};

export default function FeedItem({
  icon,
  tone = "default",
  title,
  meta,
  time,
  className,
}: FeedItemProps) {
  return (
    <div className={["feed-item", className].filter(Boolean).join(" ")}>
      <div className={["feed-icon", TONE_CLASS[tone]].filter(Boolean).join(" ")}>
        {icon}
      </div>
      <div className="feed-body">
        <div>{title}</div>
        {meta ? <div className="feed-meta">{meta}</div> : null}
      </div>
      {time ? <div className="feed-time">{time}</div> : null}
    </div>
  );
}
