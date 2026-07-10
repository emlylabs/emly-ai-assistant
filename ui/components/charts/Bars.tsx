"use client";

import type { CSSProperties } from "react";

type BarsProps = {
  /** Values to plot. The component refuses to render when fewer than 2 points
   * exist — same rationale as `<Sparkline>`. */
  values: number[];
  accent?: string;
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
};

const VIEW_W = 200;
const VIEW_H = 32;
const PAD = 2;
const GAP = 2;

/**
 * Tiny inline-SVG bar series. Companion to `<Sparkline>`; same 200×32 viewBox
 * so the two render at identical heights inside `.kpi-spark` slots.
 */
export default function Bars({
  values,
  accent = "var(--accent)",
  className,
  style,
  ariaLabel,
}: BarsProps) {
  if (!values || values.length < 2) return null;

  const max = Math.max(...values, 1);
  const usableW = VIEW_W - PAD * 2;
  const barWidth = (usableW - GAP * (values.length - 1)) / values.length;
  const usableH = VIEW_H - PAD * 2;

  return (
    <svg
      className={className}
      style={style}
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      role={ariaLabel ? "img" : "presentation"}
      aria-label={ariaLabel}
    >
      {values.map((value, index) => {
        const height = max === 0 ? 0 : (value / max) * usableH;
        const x = PAD + index * (barWidth + GAP);
        const y = VIEW_H - PAD - height;
        return (
          <rect
            key={index}
            x={x}
            y={y}
            width={Math.max(barWidth, 0.5)}
            height={Math.max(height, 0.5)}
            fill={accent}
          />
        );
      })}
    </svg>
  );
}
