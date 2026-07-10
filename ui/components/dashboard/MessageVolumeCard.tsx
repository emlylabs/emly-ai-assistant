"use client";

import VolumeChart from "@/components/charts/VolumeChart";
import { shortDayLabel } from "@/lib/aggregations";

type MessageVolumeCardProps = {
  /** Daily message counts, oldest → newest. Length defines the X axis. */
  dailyCounts: number[];
  /** ISO day keys aligned with `dailyCounts`. */
  dayKeys: string[];
  /** Total over the window, rendered in the legend. */
  totalLabel?: string;
};

/**
 * "Message volume" card on the per-bot dashboard. Wraps `<VolumeChart>` and
 * adds the mockup's header row (title + sub + legend). The empty state
 * — fewer than 2 days of data — is delegated to `<VolumeChart>` which renders
 * "Not enough data yet" rather than fabricating a curve.
 */
export default function MessageVolumeCard({
  dailyCounts,
  dayKeys,
  totalLabel,
}: MessageVolumeCardProps) {
  const labels = dayKeys.map(shortDayLabel);

  return (
    <div className="card" style={{ padding: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          gap: 8,
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Message volume</div>
          <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
            last 14 days · all channels
          </div>
        </div>
        {totalLabel ? (
          <div
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11.5,
              color: "var(--muted)",
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 8,
                background: "var(--accent)",
                borderRadius: 2,
                marginRight: 6,
                verticalAlign: 1,
              }}
            />
            Messages
            <span
              style={{
                color: "var(--fg)",
                marginLeft: 6,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {totalLabel}
            </span>
          </div>
        ) : null}
      </div>
      <div style={{ padding: "14px 18px" }}>
        <VolumeChart series={{ bars: dailyCounts }} labels={labels} height={220} />
      </div>
    </div>
  );
}
