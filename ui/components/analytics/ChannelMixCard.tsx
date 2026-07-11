"use client";

import type { MessageCountByChannel } from "@/lib/api";

type ChannelMixCardProps = {
  rows: MessageCountByChannel[] | null;
  rangeDays: number;
  /** When non-null, clicking a row applies that channel as the page-wide
   * filter. Pass the currently selected channel so the active row is
   * highlighted. */
  selectedChannelId?: string | null;
  onSelectChannel?: (channelId: string | null) => void;
};

/** Pretty-print a channel row label. Display name (when set) wins;
 * channel type fills in for OAuth-installed channels that didn't get
 * a custom name. The orphan bucket (channel_id null, channel_type
 * null) reads as "[unattributed]" so reports written before Phase 3
 * channel threading don't get folded into the wrong slot. */
function rowLabel(row: MessageCountByChannel): string {
  if (row.display_name) return row.display_name;
  if (row.channel_type) return row.channel_type;
  return "[unattributed]";
}

export default function ChannelMixCard({
  rows,
  rangeDays,
  selectedChannelId,
  onSelectChannel,
}: ChannelMixCardProps) {
  if (rows === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading channel mix…
      </div>
    );
  }

  const total = rows.reduce((a, r) => a + r.count, 0);
  const max = rows.length > 0 ? Math.max(...rows.map((r) => r.count)) : 1;

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
          <div style={{ fontSize: 13, fontWeight: 600 }}>Channel mix</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            messages grouped by channel · last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {total.toLocaleString()} messages
        </span>
      </div>

      {rows.length === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No messages in window.
        </div>
      ) : (
        <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
          {rows.map((r) => {
            const pct = total > 0 ? (r.count / total) * 100 : 0;
            const barW = max > 0 ? (r.count / max) * 100 : 0;
            const isSelected = selectedChannelId === r.channel_id;
            const isOrphan = !r.channel_id;
            const interactive = !!onSelectChannel && !isOrphan;
            return (
              <div
                key={r.channel_id ?? "__orphan__"}
                role={interactive ? "button" : undefined}
                tabIndex={interactive ? 0 : undefined}
                onClick={
                  interactive
                    ? () =>
                        onSelectChannel?.(
                          isSelected ? null : r.channel_id ?? null,
                        )
                    : undefined
                }
                onKeyDown={
                  interactive
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelectChannel?.(
                            isSelected ? null : r.channel_id ?? null,
                          );
                        }
                      }
                    : undefined
                }
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  padding: interactive ? "4px 6px" : 0,
                  margin: interactive ? "-4px -6px" : 0,
                  borderRadius: 4,
                  cursor: interactive ? "pointer" : "default",
                  background: isSelected ? "var(--paper)" : undefined,
                }}
                title={
                  interactive
                    ? isSelected
                      ? "Click to clear filter"
                      : "Click to filter analytics by this channel"
                    : undefined
                }
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 12.5,
                    color: isOrphan ? "var(--muted)" : "var(--fg)",
                  }}
                >
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      minWidth: 0,
                      paddingRight: 12,
                    }}
                  >
                    {rowLabel(r)}
                    {r.display_name && r.channel_type && (
                      <span className="muted" style={{ marginLeft: 6, fontSize: 11 }}>
                        {r.channel_type}
                      </span>
                    )}
                  </span>
                  <span
                    className="num"
                    style={{
                      fontFamily: "var(--font-mono)",
                      color: "var(--muted)",
                      fontSize: 11.5,
                    }}
                  >
                    {r.count.toLocaleString()} · {pct.toFixed(1)}%
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
                      width: `${barW}%`,
                      height: "100%",
                      background: isOrphan
                        ? "var(--muted)"
                        : isSelected
                          ? "var(--accent)"
                          : "var(--accent)",
                      opacity: isOrphan ? 0.5 : isSelected ? 1 : 0.7,
                      borderRadius: 2,
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
