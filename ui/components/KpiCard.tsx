"use client";

import type { ReactNode } from "react";

export type KpiDelta = {
  /** Pre-formatted delta string, e.g. `+12.4%` or `+38ms`. We keep this as a
   * string instead of a number because callers know the right unit and
   * formatting (percent points, ms, raw count). */
  value: string;
  /** Direction tints the delta colour. `neutral` keeps the muted text colour
   * — useful for context-only deltas like "2 paused". */
  direction?: "up" | "down" | "neutral";
};

type KpiCardProps = {
  /** Tiny uppercase label (e.g., "Messages today"). */
  label: string;
  /** Primary number; use a string when you've pre-formatted (e.g., "1.04M"). */
  value: ReactNode;
  /** Optional unit suffix rendered smaller and muted (e.g., "%", "ms", "/5"). */
  unit?: string;
  /** Optional inline delta on the right of the value row. */
  delta?: KpiDelta;
  /** Optional sparkline / mini chart. The KPI tile reserves a 32px-tall
   * slot for whatever is passed in (typically `<Sparkline>` or `<Bars>`). */
  spark?: ReactNode;
  /** Optional muted footnote rendered under the value (e.g. "vs. previous 30 days"). */
  footnote?: ReactNode;
  className?: string;
};

/**
 * Mockup-style KPI tile. Composes the `.kpi` family in globals.css. Pages
 * group them inside `<div className="kpi-grid">` (or any grid layout).
 *
 * `delta` is intentionally a string + direction rather than a number so
 * callers can speak in their own units (percent points, ms, raw counts).
 * Showing nothing when data is missing is preferred over fabricating zeros.
 */
export default function KpiCard({
  label,
  value,
  unit,
  delta,
  spark,
  footnote,
  className,
}: KpiCardProps) {
  const deltaClass =
    delta?.direction === "up"
      ? "kpi-delta delta-up"
      : delta?.direction === "down"
        ? "kpi-delta delta-down"
        : "kpi-delta";

  return (
    <div className={["kpi", className].filter(Boolean).join(" ")}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-row">
        <div className="kpi-value">
          {value}
          {unit ? <span className="unit">{unit}</span> : null}
        </div>
        {delta ? <div className={deltaClass}>{delta.value}</div> : null}
      </div>
      {spark ? <div className="kpi-spark">{spark}</div> : null}
      {footnote ? (
        <div style={{ color: "var(--muted)", fontSize: 11 }}>{footnote}</div>
      ) : null}
    </div>
  );
}
