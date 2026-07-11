"use client";

import type { FunnelResponse } from "@/lib/api";

type ResolutionFunnelCardProps = {
  data: FunnelResponse | null;
  rangeDays: number;
  /** When non-null, render a small "vs prior" subtitle on the resolved
   * line so the card carries period-over-period context without doubling
   * the bar count. */
  prior?: FunnelResponse | null;
};

type Stage = {
  label: string;
  hint: string;
  count: number;
  /** Share of `started` (0..1). Null at the top of the funnel. */
  rate: number | null;
};

/**
 * Three-stage cohort funnel: sessions started in the window → those
 * the intent router classified at least once → those an admin (or
 * auto-resolution job) marked resolved. Bars scale by absolute count
 * relative to `started`, so the visual matches the rate label.
 *
 * Honest by design: an empty cohort renders the stage list with `—`
 * rates rather than synthetic 0% drop-offs, and "understood" reads
 * the existing `topic` column rather than a separate "first-turn
 * understood" flag we don't capture today.
 */
function priorRateDelta(current: number | null, prior: number | null | undefined): string {
  if (current === null || prior === null || prior === undefined) return "";
  const diff = (current - prior) * 100;
  if (Math.abs(diff) < 0.05) return "0.0pp vs prior";
  const sign = diff > 0 ? "+" : "−";
  return `${sign}${Math.abs(diff).toFixed(1)}pp vs prior`;
}

export default function ResolutionFunnelCard({ data, rangeDays, prior }: ResolutionFunnelCardProps) {
  if (data === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading funnel…
      </div>
    );
  }

  const { started, understood, resolved, understood_rate, resolved_rate } = data;
  const stages: Stage[] = [
    {
      label: "Started",
      hint: "sessions opened in window",
      count: started,
      rate: started > 0 ? 1 : null,
    },
    {
      label: "Understood",
      hint: "≥1 user turn classified by intent router",
      count: understood,
      rate: understood_rate,
    },
    {
      label: "Resolved",
      hint: "session.is_resolved = true",
      count: resolved,
      rate: resolved_rate,
    },
  ];

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
          <div style={{ fontSize: 13, fontWeight: 600 }}>Resolution funnel</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            cohort: sessions started in last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {started.toLocaleString()} cohort · {(resolved_rate !== null
            ? (resolved_rate * 100).toFixed(1) + "%"
            : "—")}
          {" end-to-end"}
          {prior && resolved_rate !== null && prior.resolved_rate !== null && (
            <span style={{ marginLeft: 8 }}>
              · {priorRateDelta(resolved_rate, prior.resolved_rate)}
            </span>
          )}
        </span>
      </div>

      {started === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No sessions started in the window — the funnel needs an opening cohort.
        </div>
      ) : (
        <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 12 }}>
          {stages.map((s, i) => {
            const widthPct = started > 0 ? (s.count / started) * 100 : 0;
            const ratePct = s.rate === null ? "—" : `${(s.rate * 100).toFixed(1)}%`;
            // Drop-off vs. the previous stage — null at the top.
            const dropFrom = i === 0 ? null : stages[i - 1];
            const drop =
              dropFrom !== null && dropFrom.count > 0
                ? (s.count - dropFrom.count) / dropFrom.count
                : null;
            return (
              <div key={s.label} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    gap: 8,
                  }}
                >
                  <div style={{ fontSize: 12.5, fontWeight: 500 }}>{s.label}</div>
                  <div
                    className="num"
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11.5,
                      color: "var(--muted)",
                    }}
                  >
                    {s.count.toLocaleString()} · {ratePct}
                    {drop !== null && (
                      <span
                        style={{
                          marginLeft: 8,
                          color: drop < 0 ? "var(--muted)" : "var(--fg)",
                        }}
                        title="vs. previous stage"
                      >
                        {drop === 0
                          ? "0%"
                          : `${drop > 0 ? "+" : "−"}${Math.abs(drop * 100).toFixed(1)}%`}
                      </span>
                    )}
                  </div>
                </div>
                <div
                  style={{
                    height: 8,
                    background: "var(--paper)",
                    borderRadius: 3,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${widthPct}%`,
                      height: "100%",
                      background: "var(--accent)",
                      opacity: 0.9 - i * 0.2,
                      borderRadius: 3,
                    }}
                  />
                </div>
                <div className="muted" style={{ fontSize: 11 }}>
                  {s.hint}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
