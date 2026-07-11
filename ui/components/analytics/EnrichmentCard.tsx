"use client";

import type { EnrichmentSummary } from "@/lib/api";

type EnrichmentCardProps = {
  data: EnrichmentSummary | null;
  rangeDays: number;
};

/**
 * Sentiment & intent enrichment summary. Conditional on Phase 8
 * enrichment being enabled per bot — we lean on
 * `cohort_size > 0 && enriched_count == 0` as the signal that the bot
 * hasn't opted in, and surface that as a clear "needs `enrichment_enabled`"
 * note rather than rendering 100% unrated bars.
 */
export default function EnrichmentCard({ data, rangeDays }: EnrichmentCardProps) {
  if (data === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading enrichment summary…
      </div>
    );
  }

  const optedOut = data.cohort_size > 0 && data.enriched_count === 0;
  const { positive, neutral, negative, unrated } = data.sentiment;
  const sentTotal = positive + neutral + negative;
  const intents = data.intents;

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
          <div style={{ fontSize: 13, fontWeight: 600 }}>Sentiment &amp; intent</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            session-level enrichment · last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {data.enriched_count.toLocaleString()} of{" "}
          {data.cohort_size.toLocaleString()} sessions enriched
        </span>
      </div>

      {data.cohort_size === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No sessions started in window.
        </div>
      ) : optedOut ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          The Phase 8 enrichment worker hasn&apos;t classified any sessions in this
          window. Enable <code>enrichment_enabled</code> in the bot config to
          start populating sentiment and intent labels.
        </div>
      ) : (
        <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 14 }}>
          {/* Sentiment row — single horizontal stacked bar split positive /
              neutral / negative, with unrated trailing for honesty. */}
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: 12.5,
                marginBottom: 4,
              }}
            >
              <span>Sentiment</span>
              <span
                className="num"
                style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--muted)" }}
              >
                {sentTotal.toLocaleString()} labelled · {unrated.toLocaleString()} unrated
              </span>
            </div>
            <div
              style={{
                display: "flex",
                height: 10,
                background: "var(--paper)",
                borderRadius: 3,
                overflow: "hidden",
              }}
              title={`positive ${positive} · neutral ${neutral} · negative ${negative}`}
            >
              {sentTotal > 0 && (
                <>
                  <div
                    style={{
                      width: `${(positive / sentTotal) * 100}%`,
                      background: "#3aa86b",
                    }}
                  />
                  <div
                    style={{
                      width: `${(neutral / sentTotal) * 100}%`,
                      background: "var(--muted)",
                      opacity: 0.55,
                    }}
                  />
                  <div
                    style={{
                      width: `${(negative / sentTotal) * 100}%`,
                      background: "#d05050",
                    }}
                  />
                </>
              )}
            </div>
            <div
              className="muted"
              style={{ display: "flex", gap: 14, fontSize: 11, marginTop: 4 }}
            >
              <span>
                <span style={{ color: "#3aa86b" }}>●</span> Positive {positive}
              </span>
              <span>
                <span style={{ color: "var(--muted)" }}>●</span> Neutral {neutral}
              </span>
              <span>
                <span style={{ color: "#d05050" }}>●</span> Negative {negative}
              </span>
            </div>
          </div>

          {/* Intent list — top 12 from server, sorted desc. */}
          <div>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                fontSize: 12.5,
                marginBottom: 6,
              }}
            >
              <span>Top intents</span>
              <span
                className="muted"
                style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
              >
                from <code>EMLYSession.intent</code>
              </span>
            </div>
            {intents.length === 0 ? (
              <div className="muted" style={{ fontSize: 12 }}>
                No intent labels yet.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {(() => {
                  const max = intents[0]?.count ?? 1;
                  return intents.map((i) => {
                    const w = max > 0 ? (i.count / max) * 100 : 0;
                    return (
                      <div key={i.intent} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                          <span
                            style={{
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                              minWidth: 0,
                              paddingRight: 8,
                            }}
                          >
                            {i.intent}
                          </span>
                          <span
                            className="num"
                            style={{
                              fontFamily: "var(--font-mono)",
                              color: "var(--muted)",
                              fontSize: 11.5,
                            }}
                          >
                            {i.count.toLocaleString()}
                          </span>
                        </div>
                        <div
                          style={{
                            height: 3,
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
                  });
                })()}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
