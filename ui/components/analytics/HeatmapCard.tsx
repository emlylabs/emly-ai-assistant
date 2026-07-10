"use client";

import { Fragment } from "react";

import type { HeatmapCell } from "@/lib/api";

type HeatmapCardProps = {
  cells: HeatmapCell[] | null;
  rangeDays: number;
};

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// Hours rendered as a sparse axis — every 4 hours plus 23 — so labels
// don't overlap at the typical card width.
const HOUR_LABELS = [0, 4, 8, 12, 16, 20, 23];

/**
 * 7×24 weekday-by-hour heatmap of message volume in UTC. Empty cells
 * render as the background colour (no border) so peak-load patterns
 * pop without grid clutter. The colour ramp is opacity on `--accent`
 * so it picks up theme changes for free.
 */
export default function HeatmapCard({ cells, rangeDays }: HeatmapCardProps) {
  if (cells === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading volume heatmap…
      </div>
    );
  }

  const grid: number[][] = Array.from({ length: 7 }, () => new Array(24).fill(0));
  let max = 0;
  let total = 0;
  for (const c of cells) {
    if (c.day_of_week < 0 || c.day_of_week > 6) continue;
    if (c.hour < 0 || c.hour > 23) continue;
    grid[c.day_of_week][c.hour] = c.count;
    total += c.count;
    if (c.count > max) max = c.count;
  }

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
          <div style={{ fontSize: 13, fontWeight: 600 }}>Volume heatmap</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            messages by UTC weekday × hour · last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {total.toLocaleString()} messages · peak {max.toLocaleString()}
        </span>
      </div>

      {total === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No messages in window.
        </div>
      ) : (
        <div style={{ padding: "12px 16px", overflowX: "auto" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "auto repeat(24, minmax(14px, 1fr))",
              columnGap: 2,
              rowGap: 2,
              alignItems: "center",
            }}
          >
            {/* Hour axis — sparse labels above the first row. */}
            <div />
            {Array.from({ length: 24 }, (_, h) => (
              <div
                key={`hour-${h}`}
                className="muted"
                style={{
                  fontSize: 10,
                  fontFamily: "var(--font-mono)",
                  textAlign: "center",
                }}
              >
                {HOUR_LABELS.includes(h) ? h : ""}
              </div>
            ))}

            {DAYS.map((label, dow) => (
              <Fragment key={`row-${dow}`}>
                <div
                  className="muted"
                  style={{
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                    paddingRight: 6,
                    textAlign: "right",
                  }}
                >
                  {label}
                </div>
                {Array.from({ length: 24 }, (_, h) => {
                  const v = grid[dow][h];
                  // Opacity ramp: 0 stays at 0 (background), otherwise
                  // 0.18 floor so the cell is visible even at low counts.
                  const opacity = v === 0 ? 0 : 0.18 + (v / max) * 0.82;
                  return (
                    <div
                      key={`cell-${dow}-${h}`}
                      title={`${label} ${String(h).padStart(2, "0")}:00 UTC · ${v.toLocaleString()} msgs`}
                      style={{
                        height: 18,
                        background: "var(--accent)",
                        opacity,
                        borderRadius: 2,
                      }}
                    />
                  );
                })}
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
