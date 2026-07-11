"use client";

import type { CitationStats } from "@/lib/api";

type CitationsCardProps = {
  data: CitationStats | null;
  rangeDays: number;
};

/**
 * RAG citation health: share of assistant turns that returned at
 * least one citation, plus the top files cited. Bots that don't run
 * RAG (`citation_rate === null`) get an empty-state hint rather than
 * a misleading 0%.
 */
export default function CitationsCard({ data, rangeDays }: CitationsCardProps) {
  if (data === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading citation stats…
      </div>
    );
  }

  const ratePct =
    data.citation_rate === null
      ? "—"
      : `${(data.citation_rate * 100).toFixed(1)}%`;

  const topFiles = data.top_files;
  const max = topFiles.length > 0 ? topFiles[0].citation_count : 1;

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
          <div style={{ fontSize: 13, fontWeight: 600 }}>RAG citations</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            assistant turns with ≥1 source · last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {data.with_citations.toLocaleString()} / {data.assistant_turns.toLocaleString()} · {ratePct}
        </span>
      </div>

      {data.assistant_turns === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No assistant turns in window.
        </div>
      ) : data.citation_rate === null || data.with_citations === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No citations recorded. The bot either doesn&apos;t use RAG or none of
          its retrievals matched the citation threshold; the{" "}
          <code>citations</code> column on <code>emly_messages</code> stayed
          null for every assistant turn in this window.
        </div>
      ) : (
        <div style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="muted" style={{ fontSize: 11.5 }}>
            Top cited files
          </div>
          {topFiles.length === 0 ? (
            <div className="muted" style={{ fontSize: 12 }}>
              Citations exist but no file metadata could be parsed.
            </div>
          ) : (
            topFiles.map((f) => {
              const w = max > 0 ? (f.citation_count / max) * 100 : 0;
              const label = f.filename || f.file_id || "[unknown]";
              return (
                <div
                  key={(f.file_id ?? "?") + (f.filename ?? "")}
                  style={{ display: "flex", flexDirection: "column", gap: 3 }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: 12,
                      gap: 8,
                    }}
                  >
                    <span
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        minWidth: 0,
                      }}
                      title={f.file_id ?? undefined}
                    >
                      {label}
                    </span>
                    <span
                      className="num"
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: "var(--muted)",
                        fontSize: 11.5,
                      }}
                    >
                      {f.citation_count.toLocaleString()}
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
                        opacity: 0.75,
                      }}
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}
