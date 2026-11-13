"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { api } from "@/lib/api";

// "What does the bot see for this query?" — admin-side retrieval inspector.
// Previously lived inside the Files tab; promoted to its own component in
// §11 T2 so the dedicated Retrieval tab and other surfaces (config tuning,
// future Test drawer) can mount it without page-coupling.
type RAGHit = {
  score: number | null;
  chunk: string;
  metadata: Record<string, unknown>;
};

export default function RAGSearchPanel({
  slug,
  title = "Query the knowledge base",
  description,
  topMargin = 0,
}: {
  slug: string;
  title?: string;
  description?: React.ReactNode;
  /** Vertical spacing above the panel — 0 by default; the Files tab sets
   * a larger value when embedding the panel below the file table. */
  topMargin?: number;
}) {
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState<number | "">("");
  const [threshold, setThreshold] = useState<number | "">("");
  const [hits, setHits] = useState<RAGHit[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ top_k: number; threshold: number } | null>(null);

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setError(null);
    setLoading(true);
    setHits(null);
    try {
      const res = await api.ragSearch(slug, {
        query: query.trim(),
        top_k: topK === "" ? undefined : Number(topK),
        threshold: threshold === "" ? undefined : Number(threshold),
      });
      setHits(res.hits);
      setMeta({ top_k: res.top_k, threshold: res.threshold });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setLoading(false);
    }
  }

  const defaultDescription = (
    <>
      Run the same retrieval the chat runtime would use. The hits below are
      what the LLM would see as <code>{"{context}"}</code> when answering
      this query.
    </>
  );

  return (
    <div className="card" style={{ marginTop: topMargin }}>
      <h2 style={{ marginTop: 0 }}>{title}</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        {description ?? defaultDescription}
      </p>
      <form onSubmit={onSearch}>
        <div className="field">
          <input
            type="text"
            placeholder="Ask what the bot would retrieve…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
        </div>
        <div className="row" style={{ alignItems: "flex-end", gap: 12 }}>
          <div className="field" style={{ marginBottom: 0, width: 100 }}>
            <label style={{ fontSize: 12 }}>Top K</label>
            <input
              type="number"
              min={1}
              max={50}
              placeholder="(bot default)"
              value={topK}
              onChange={(e) =>
                setTopK(e.target.value === "" ? "" : Number(e.target.value))
              }
            />
          </div>
          <div className="field" style={{ marginBottom: 0, width: 130 }}>
            <label style={{ fontSize: 12 }}>Threshold</label>
            <input
              type="number"
              step={0.05}
              min={0}
              max={1}
              placeholder="(bot default)"
              value={threshold}
              onChange={(e) =>
                setThreshold(e.target.value === "" ? "" : Number(e.target.value))
              }
            />
          </div>
          <button type="submit" disabled={loading || !query.trim()}>
            <Search strokeWidth={1.75} />
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
      </form>

      {error && <div className="error" style={{ marginTop: 12 }}>{error}</div>}

      {hits !== null && (
        <div style={{ marginTop: 16 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
            {hits.length === 0
              ? `No hits above threshold ${meta?.threshold ?? "?"}. The bot would answer with no RAG context — it falls back to the model's general knowledge or refuses.`
              : `${hits.length} hit${hits.length === 1 ? "" : "s"} returned (top_k=${meta?.top_k}, threshold=${meta?.threshold}).`}
          </div>
          {hits.map((h, i) => {
            const filename =
              (h.metadata?.filename as string) ||
              (h.metadata?.source as string) ||
              `chunk ${i + 1}`;
            const fileId = h.metadata?.file_id as string | undefined;
            const docType = h.metadata?.document_type as string | undefined;
            const sourceUrl = h.metadata?.source_url as string | undefined;
            return (
              <div
                key={i}
                className="card"
                style={{ marginBottom: 8, padding: 12, background: "var(--paper)" }}
              >
                <div
                  className="row"
                  style={{ justifyContent: "space-between", alignItems: "baseline", gap: 12 }}
                >
                  <div style={{ fontSize: 13 }}>
                    <strong>#{i + 1}</strong>{" "}
                    <span className="muted">·</span>{" "}
                    <span title={fileId}>{filename}</span>
                    {docType && (
                      <>
                        {" "}
                        <span className="tag" title="Document type">
                          {docType.replace(/_/g, " ")}
                        </span>
                      </>
                    )}
                    {sourceUrl && (
                      <>
                        {" "}
                        <a
                          href={sourceUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="muted"
                          style={{ fontSize: 12 }}
                          title={sourceUrl}
                        >
                          source ↗
                        </a>
                      </>
                    )}
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    {h.score != null && (
                      <span className="tag" title="Cosine similarity from the vector search">
                        score {h.score.toFixed(3)}
                      </span>
                    )}
                    {typeof h.metadata?.rerank_score === "number" && (
                      <span
                        className="tag"
                        title="Cross-encoder rerank score (hybrid search). Final ordering is by this score, not the cosine."
                      >
                        rerank {(h.metadata.rerank_score as number).toFixed(3)}
                      </span>
                    )}
                  </div>
                </div>
                <pre
                  className="snippet"
                  style={{
                    marginTop: 8,
                    marginBottom: 0,
                    whiteSpace: "pre-wrap",
                    maxHeight: 240,
                    overflowY: "auto",
                  }}
                >
                  {h.chunk}
                </pre>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
