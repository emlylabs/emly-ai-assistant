"use client";

import type { CSSProperties } from "react";

export type VolumeSeries = {
  /** Bars (background series). */
  bars?: number[];
  /** Line overlay (foreground series, e.g. resolved sessions). */
  line?: number[];
  /** Optional second line (e.g. escalated). Rendered with a dashed stroke. */
  lineDashed?: number[];
};

type VolumeChartProps = {
  series: VolumeSeries;
  /** X-axis labels. Length should match the longest series. */
  labels?: string[];
  /** Used for the empty state when there isn't enough data. */
  emptyText?: string;
  height?: number;
  className?: string;
  style?: CSSProperties;
};

const VIEW_W = 720;
const VIEW_H = 220;
const MARGIN = { top: 14, right: 18, bottom: 30, left: 40 };

function maxOf(...arrays: (number[] | undefined)[]): number {
  let max = 0;
  for (const arr of arrays) {
    if (!arr) continue;
    for (const v of arr) if (v > max) max = v;
  }
  return max;
}

/**
 * Mid-size inline-SVG chart for the dashboard's "Message volume" card.
 * Bars + 1 or 2 lines; no dependencies. Renders an empty-state placeholder
 * when neither series has at least 2 points. Honest by design — we'd rather
 * show "Not enough data yet" than draw a synthetic line.
 */
export default function VolumeChart({
  series,
  labels,
  emptyText = "Not enough data yet",
  height = VIEW_H,
  className,
  style,
}: VolumeChartProps) {
  const { bars, line, lineDashed } = series;
  const enoughBars = bars && bars.length >= 2;
  const enoughLine = line && line.length >= 2;
  const enoughDashed = lineDashed && lineDashed.length >= 2;

  if (!enoughBars && !enoughLine && !enoughDashed) {
    return (
      <div
        className={className}
        style={{
          padding: "24px 18px",
          color: "var(--muted)",
          fontSize: 12.5,
          textAlign: "center",
          ...style,
        }}
      >
        {emptyText}
      </div>
    );
  }

  const max = maxOf(bars, line, lineDashed) || 1;
  const innerW = VIEW_W - MARGIN.left - MARGIN.right;
  const innerH = VIEW_H - MARGIN.top - MARGIN.bottom;

  // Y-axis ticks at 0, 25, 50, 75, 100% of max.
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => ({
    value: Math.round(max * t),
    y: MARGIN.top + innerH - innerH * t,
  }));

  // Bars across the full inner width with a small gap between each.
  const barSlot = enoughBars && bars ? innerW / bars.length : 0;
  const barW = enoughBars ? Math.max(barSlot - 6, 2) : 0;

  // Line points sit at the centre of each slot so they align with the
  // bars (`x = MARGIN.left + slot/2 + i * slot`). For line-only series,
  // we fall back to even spacing across the inner width.
  function pathFor(values: number[] | undefined): string | null {
    if (!values || values.length < 2) return null;
    const slot = enoughBars && bars ? innerW / bars.length : innerW / values.length;
    return values
      .map((value, i) => {
        const x = MARGIN.left + slot / 2 + i * slot;
        const y = MARGIN.top + innerH - (value / max) * innerH;
        return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
  }

  const linePath = pathFor(line);
  const dashedPath = pathFor(lineDashed);

  return (
    <svg
      className={className}
      style={{ width: "100%", height, display: "block", ...style }}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label="Message volume over time"
    >
      {/* Y-axis grid lines */}
      <g>
        {ticks.map((t, i) => (
          <line
            key={`grid-${i}`}
            x1={MARGIN.left}
            y1={t.y}
            x2={VIEW_W - MARGIN.right}
            y2={t.y}
            stroke="var(--border)"
            strokeDasharray={i === 0 ? undefined : "2 4"}
          />
        ))}
        {ticks.map((t, i) => (
          <text
            key={`y-${i}`}
            x={MARGIN.left - 6}
            y={t.y + 3}
            textAnchor="end"
            fontFamily="var(--font-mono)"
            fontSize={10}
            fill="var(--muted)"
          >
            {t.value}
          </text>
        ))}
      </g>

      {enoughBars && bars
        ? bars.map((value, i) => {
            const h = (value / max) * innerH;
            const x = MARGIN.left + i * barSlot + (barSlot - barW) / 2;
            const y = MARGIN.top + innerH - h;
            return (
              <rect
                key={`bar-${i}`}
                x={x}
                y={y}
                width={barW}
                height={Math.max(h, 0.5)}
                fill="var(--accent)"
                fillOpacity={0.18}
              />
            );
          })
        : null}

      {linePath && (
        <path
          d={linePath}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}

      {dashedPath && (
        <path
          d={dashedPath}
          fill="none"
          stroke="var(--muted)"
          strokeWidth={1.3}
          strokeDasharray="3 3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}

      {/* X-axis labels — centred under each slot so they line up with bars. */}
      {labels && labels.length > 0 && (
        <g>
          {labels.map((label, i) => {
            const slot = innerW / labels.length;
            const x = MARGIN.left + slot / 2 + i * slot;
            // Show every other label when crowded.
            const show =
              labels.length <= 8 || i === 0 || i === labels.length - 1 || i % Math.ceil(labels.length / 6) === 0;
            if (!show) return null;
            return (
              <text
                key={`x-${i}`}
                x={x}
                y={VIEW_H - MARGIN.bottom + 16}
                textAnchor="middle"
                fontFamily="var(--font-mono)"
                fontSize={10}
                fill="var(--muted)"
              >
                {label}
              </text>
            );
          })}
        </g>
      )}
    </svg>
  );
}
