"use client";

import VolumeChart from "@/components/charts/VolumeChart";
import { shortDayLabel } from "@/lib/aggregations";
import type { DailySessionBucket } from "@/lib/api";

type SessionFlowCardProps = {
  buckets: DailySessionBucket[] | null;
  /** Loaded window length in days (used for the subtitle). */
  rangeDays: number;
  /** Prior-window totals to compare against. Null in non-compare mode. */
  priorTotals?: { started: number; resolved: number } | null;
};

/**
 * Daily started-vs-resolved sessions overlay. Replaces the old "Resolution
 * funnel" `<PendingCard>` once the backend ships `/sessions/daily`.
 *
 * The two series share a single Y-axis since both are session counts —
 * there's no scale mismatch the way overlaying messages and sessions
 * would have. Resolved is rendered dashed so the line styling reads as
 * "subset of started" without invoking a stacked-bar chart.
 */
function startedDelta(current: number, prior: number | null | undefined): string {
  if (prior === null || prior === undefined || prior === 0) return "";
  const pct = ((current - prior) / prior) * 100;
  if (Math.abs(pct) < 0.05) return "0.0%";
  const sign = pct > 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

export default function SessionFlowCard({ buckets, rangeDays, priorTotals }: SessionFlowCardProps) {
  const totals = buckets
    ? buckets.reduce(
        (acc, b) => ({ started: acc.started + b.started, resolved: acc.resolved + b.resolved }),
        { started: 0, resolved: 0 },
      )
    : null;

  const resolutionPct =
    totals && totals.started > 0
      ? `${((totals.resolved / totals.started) * 100).toFixed(1)}%`
      : "—";

  return (
    <div className="card" style={{ padding: 0 }}>
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Session flow</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            started vs. resolved · last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {totals
            ? `${totals.started.toLocaleString()} started · ${totals.resolved.toLocaleString()} resolved · ${resolutionPct}`
            : "loading…"}
          {totals && priorTotals && (
            <span style={{ marginLeft: 8 }}>
              · started {startedDelta(totals.started, priorTotals.started)} vs prior
            </span>
          )}
        </span>
      </div>

      <div style={{ padding: "14px 18px" }}>
        {buckets ? (
          <VolumeChart
            series={{
              line: buckets.map((b) => b.started),
              lineDashed: buckets.map((b) => b.resolved),
            }}
            labels={buckets.map((b) => shortDayLabel(b.day))}
            height={200}
            emptyText="Not enough sessions in window"
          />
        ) : (
          <p className="muted">Loading…</p>
        )}
      </div>

      <div
        className="muted"
        style={{
          padding: "8px 16px 12px",
          fontSize: 11,
          display: "flex",
          gap: 14,
          borderTop: "1px solid var(--border)",
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              display: "inline-block",
              width: 14,
              height: 2,
              background: "var(--accent)",
              borderRadius: 1,
            }}
          />
          Started
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              display: "inline-block",
              width: 14,
              height: 0,
              borderTop: "2px dashed var(--muted)",
            }}
          />
          Resolved (by `resolved_at`)
        </span>
      </div>
    </div>
  );
}
