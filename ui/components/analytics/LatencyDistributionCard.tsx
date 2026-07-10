"use client";

import type { LatencyQuantiles } from "@/lib/api";

type LatencyDistributionCardProps = {
  data: LatencyQuantiles | null;
  rangeDays: number;
};

function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

/**
 * Three-stat latency strip: p50 (typical), p95 (tail), p99 (worst-case
 * tail). Computed by the backend with a nearest-rank percentile on the
 * loaded `response_time_ms` column. Renders an empty-state when no
 * assistant turn in the window has a captured latency.
 */
export default function LatencyDistributionCard({
  data,
  rangeDays,
}: LatencyDistributionCardProps) {
  if (data === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading latency distribution…
      </div>
    );
  }

  const stats: { label: string; hint: string; ms: number | null }[] = [
    { label: "p50", hint: "median", ms: data.p50 },
    { label: "p95", hint: "tail", ms: data.p95 },
    { label: "p99", hint: "worst-case tail", ms: data.p99 },
  ];

  // Visual scale on the p99 — keeps the bars from saturating at p50
  // when the distribution is heavily skewed.
  const max = Math.max(...stats.map((s) => s.ms ?? 0), 1);

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
          <div style={{ fontSize: 13, fontWeight: 600 }}>Latency distribution</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            assistant response_time_ms · last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {data.count.toLocaleString()} samples
        </span>
      </div>

      {data.count === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No latency captured. Phase 2 telemetry populates{" "}
          <code>response_time_ms</code> on assistant messages — once new
          traffic flows, this card fills in.
        </div>
      ) : (
        <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
          {stats.map((s) => {
            const w = max > 0 && s.ms !== null ? (s.ms / max) * 100 : 0;
            return (
              <div key={s.label} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
                  <span>
                    {s.label}{" "}
                    <span className="muted" style={{ fontSize: 11 }}>
                      ({s.hint})
                    </span>
                  </span>
                  <span
                    className="num"
                    style={{
                      fontFamily: "var(--font-mono)",
                      color: "var(--muted)",
                      fontSize: 11.5,
                    }}
                  >
                    {formatMs(s.ms)}
                  </span>
                </div>
                <div
                  style={{
                    height: 4,
                    background: "var(--paper)",
                    borderRadius: 2,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${w}%`,
                      height: "100%",
                      background: "var(--accent)",
                      opacity: 0.7,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
