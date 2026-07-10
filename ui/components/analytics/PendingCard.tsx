"use client";

import type { ReactNode } from "react";
import { Clock } from "lucide-react";

type PendingCardProps = {
  title: string;
  /** What needs to land in the backend before this card can be filled. Be
   * specific: "needs `is_resolved` flag on EMLYMessages", not "coming soon". */
  reason: ReactNode;
};

/**
 * Empty-by-design analytics card for sections we can't honestly compute
 * from the current schema (resolution funnel, model + cost). Calls out
 * the precise field that would unlock the chart so the gap reads as a
 * tracked TODO rather than a vague "soon". */
export default function PendingCard({ title, reason }: PendingCardProps) {
  return (
    <div className="card" style={{ padding: 0 }}>
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <Clock size={14} strokeWidth={1.75} style={{ color: "var(--muted)" }} />
        <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
        <span
          className="pill pill-neutral"
          style={{
            marginLeft: "auto",
            minHeight: 0,
            padding: "2px 8px",
            fontSize: 11,
          }}
        >
          Pending
        </span>
      </div>
      <div className="meta-pending" style={{ margin: 16 }}>
        {reason}
      </div>
    </div>
  );
}
