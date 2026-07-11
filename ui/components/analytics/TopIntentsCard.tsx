"use client";

import type { MessageCountByTopic } from "@/lib/api";

type TopIntentsCardProps = {
  /** Pre-aggregated topic counts from `/bots/{slug}/messages/by-topic`.
   * `null` while the request is in flight. */
  rows: MessageCountByTopic[] | null;
  /** Window length in days, surfaced in the subtitle. */
  rangeDays: number;
  /** Cap how many intents render. Default 8 + `[unclassified]`. */
  limit?: number;
  /** Total user-turn count from the prior window. Renders as a "vs
   * prior" delta on the header. Null in non-compare mode. */
  priorTotal?: number | null;
};

/**
 * Bar list of the bot's most-frequent user-message topics. Honest
 * labelling: messages whose `topic` is null collapse into an
 * `[unclassified]` bucket so the share that the runtime didn't
 * categorise stays visible. The data is server-aggregated; the
 * card never iterates the raw message list.
 */
function priorTotalDelta(current: number, prior: number | null | undefined): string {
  if (prior === null || prior === undefined || prior === 0) return "";
  const pct = ((current - prior) / prior) * 100;
  if (Math.abs(pct) < 0.05) return "0.0% vs prior";
  const sign = pct > 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}% vs prior`;
}

export default function TopIntentsCard({ rows, rangeDays, limit = 8, priorTotal }: TopIntentsCardProps) {
  if (rows === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading top intents…
      </div>
    );
  }

  const total = rows.reduce((acc, r) => acc + r.count, 0);

  // Replace empty-string topic with the explicit `[unclassified]` label.
  const labelled = rows.map((r) => ({
    topic: r.topic === "" ? "[unclassified]" : r.topic,
    count: r.count,
  }));
  const ranked = [...labelled].sort((a, b) => b.count - a.count);
  const sorted = ranked.slice(0, limit);
  const max = sorted.length > 0 ? sorted[0].count : 1;
  // Sum the visible bars so the user can tell at a glance how much of the
  // turn-volume the top-N covers. The header still shows `total` (every
  // intent in the window) — without this the bars look like they should
  // sum to 100% but don't.
  const visibleCount = sorted.reduce((acc, r) => acc + r.count, 0);
  const truncatedCount = ranked.length - sorted.length;

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
          <div style={{ fontSize: 13, fontWeight: 600 }}>Top intents</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            user turns grouped by classification · last {rangeDays} days
            {truncatedCount > 0 && (
              <>
                {" · "}showing top {sorted.length} of {ranked.length} (
                {total > 0 ? Math.round((visibleCount / total) * 100) : 0}% of turns)
              </>
            )}
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {total.toLocaleString()} turns
          {priorTotal !== null && priorTotal !== undefined && (
            <span style={{ marginLeft: 8 }}>
              · {priorTotalDelta(total, priorTotal)}
            </span>
          )}
        </span>
      </div>

      {sorted.length === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No user messages in the window yet.
        </div>
      ) : (
        <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
          {sorted.map(({ topic, count }) => {
            const pct = total > 0 ? (count / total) * 100 : 0;
            const barW = max > 0 ? (count / max) * 100 : 0;
            const isUnclassified = topic === "[unclassified]";
            return (
              <div key={topic} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 12.5,
                    color: isUnclassified ? "var(--muted)" : "var(--fg)",
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
                    {topic}
                  </span>
                  <span
                    className="num"
                    style={{
                      fontFamily: "var(--font-mono)",
                      color: "var(--muted)",
                      fontSize: 11.5,
                    }}
                  >
                    {count.toLocaleString()} · {pct.toFixed(1)}%
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
                      background: isUnclassified ? "var(--muted)" : "var(--accent)",
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
