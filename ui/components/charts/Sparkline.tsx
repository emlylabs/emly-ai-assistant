"use client";

import type { CSSProperties } from "react";

type SparklineProps = {
  /** Values to plot. The component refuses to render when fewer than 2 points
   * exist — a single point can't form a meaningful trend, and we'd rather show
   * nothing than fake one. */
  points: number[];
  /** Override the stroke colour. Defaults to `currentColor`, so the sparkline
   * inherits whatever text colour its container uses (matches `.kpi-spark`
   * conventions in globals.css). */
  accent?: string;
  /** When true, fills the area under the line with a transparent tint of the
   * accent colour. */
  area?: boolean;
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
};

const VIEW_W = 200;
const VIEW_H = 32;
const PAD_TOP = 2;
const PAD_BOTTOM = 2;

/**
 * Tiny inline-SVG sparkline. Normalises arbitrary values into a 200×32 viewBox
 * so the consumer doesn't have to think about scaling. No dependencies.
 */
export default function Sparkline({
  points,
  accent = "var(--accent)",
  area = false,
  className,
  style,
  ariaLabel,
}: SparklineProps) {
  if (!points || points.length < 2) return null;

  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const stepX = VIEW_W / (points.length - 1);
  const usableH = VIEW_H - PAD_TOP - PAD_BOTTOM;

  const coords = points.map((value, index) => {
    const x = index * stepX;
    const normalised = (value - min) / span;
    const y = VIEW_H - PAD_BOTTOM - normalised * usableH;
    return [x, y] as const;
  });

  const linePath = coords
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`)
    .join(" ");

  const areaPath = area
    ? `${linePath} L${VIEW_W},${VIEW_H} L0,${VIEW_H} Z`
    : null;

  return (
    <svg
      className={className}
      style={style}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      role={ariaLabel ? "img" : "presentation"}
      aria-label={ariaLabel}
    >
      {areaPath && (
        <path
          d={areaPath}
          fill={accent}
          fillOpacity={0.16}
          stroke="none"
        />
      )}
      <path
        d={linePath}
        fill="none"
        stroke={accent}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
