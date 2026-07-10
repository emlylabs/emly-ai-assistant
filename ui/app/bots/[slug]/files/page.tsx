"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CircleCheck,
  CircleX,
  Clock,
  Info,
  Loader2,
  RefreshCw,
  RotateCw,
  Search,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useBot } from "@/components/BotShell";
import { ApiError, BotFile, DOCUMENT_TYPES, DocumentType, Role, api } from "@/lib/api";
import CrawlTab from "./CrawlTab";
import CollectionsRail, {
  type CollectionKey,
  matchCollection,
} from "@/components/files/CollectionsRail";

type FilesTab = "uploads" | "crawl";

const POLL_MS = 3500;
const MAX_PARALLEL_UPLOADS = 3;

type LocalUpload = {
  key: string;
  file: File;
  loaded: number;
  total: number;
  status: "queued" | "uploading" | "done" | "failed";
  error?: string;
  abort?: () => void;
};

function canWrite(role: Role | null): boolean {
  return role === "owner" || role === "admin";
}

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export default function BotFilesPage() {
  const { bot, currentRole } = useBot();
  const slug = bot.slug;
  const writable = canWrite(currentRole);

  const [files, setFiles] = useState<BotFile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploads, setUploads] = useState<LocalUpload[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [activeTab, setActiveTab] = useState<FilesTab>("uploads");
  const [uploadType, setUploadType] = useState<DocumentType>("document");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Knowledge collections rail (Phase 6) — UI-only grouping over the
  // existing `document_type`. Both filters are client-side; the file list
  // never re-fetches when they change.
  const [selectedCollection, setSelectedCollection] = useState<CollectionKey>("all");
  type StatusFilter = "all" | "indexed" | "in_progress" | "errors";
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [fileQuery, setFileQuery] = useState("");

  // Filtered file list driving the table render below.
  const visibleFiles = useMemo(() => {
    const q = fileQuery.trim().toLowerCase();
    return files.filter((f) => {
      if (!matchCollection(selectedCollection, f)) return false;
      if (statusFilter === "indexed" && f.embedding_status !== "embedded") return false;
      if (
        statusFilter === "in_progress" &&
        f.embedding_status !== "pending" &&
        f.embedding_status !== "embedding"
      )
        return false;
      if (statusFilter === "errors" && f.embedding_status !== "failed") return false;
      if (q) {
        const hit =
          f.file_name.toLowerCase().includes(q) ||
          (f.mime_type ?? "").toLowerCase().includes(q);
        if (!hit) return false;
      }
      return true;
    });
  }, [files, selectedCollection, statusFilter, fileQuery]);

  const statusCounts = useMemo(() => {
    const inCollection = files.filter((f) => matchCollection(selectedCollection, f));
    return {
      all: inCollection.length,
      indexed: inCollection.filter((f) => f.embedding_status === "embedded").length,
      in_progress: inCollection.filter(
        (f) => f.embedding_status === "pending" || f.embedding_status === "embedding",
      ).length,
      errors: inCollection.filter((f) => f.embedding_status === "failed").length,
    };
  }, [files, selectedCollection]);

  // Most recent `updated_on` across all files — used by the rail's
  // "Last update" line.
  const lastSyncedAt = useMemo(() => {
    let best: string | null = null;
    for (const f of files) {
      const ts = f.updated_on ?? f.created_on;
      if (!ts) continue;
      if (!best || ts > best) best = ts;
    }
    return best;
  }, [files]);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listBotFiles(slug);
      setFiles(list);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load files");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while anything is in pending/embedding. Pause when the tab is hidden.
  useEffect(() => {
    const anyInFlight = files.some(
      (f) => f.embedding_status === "pending" || f.embedding_status === "embedding"
    );
    if (!anyInFlight) return;
    let inFlight = false;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      if (inFlight) return; // don't queue if previous fetch outstanding
      inFlight = true;
      try {
        await refresh();
      } finally {
        inFlight = false;
      }
    };
    const id = setInterval(tick, POLL_MS);
    return () => clearInterval(id);
  }, [files, refresh]);

  // Concurrent-upload throttle: drain the queue with at most N in flight.
  // Pass the upload object directly to startUpload — no stale-ref race.
  useEffect(() => {
    const queued = uploads.filter((u) => u.status === "queued");
    const uploading = uploads.filter((u) => u.status === "uploading").length;
    if (uploading >= MAX_PARALLEL_UPLOADS || queued.length === 0) return;
    const slots = MAX_PARALLEL_UPLOADS - uploading;
    queued.slice(0, slots).forEach(startUpload);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploads]);

  function enqueueFiles(list: FileList | File[]) {
    if (!writable) {
      setError("Your role doesn't permit uploads on this bot.");
      return;
    }
    const arr = Array.from(list);
    setUploads((prev) => [
      ...prev,
      ...arr.map((file) => ({
        key: `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
        file,
        loaded: 0,
        total: file.size,
        status: "queued" as const,
      })),
    ]);
  }

  function startUpload(upload: LocalUpload) {
    const { key, file } = upload;
    setUploads((prev) =>
      prev.map((u) => (u.key === key ? { ...u, status: "uploading" } : u))
    );
    const handle = api.uploadBotFile(
      slug,
      file,
      (loaded, total) => {
        setUploads((prev) =>
          prev.map((u) => (u.key === key ? { ...u, loaded, total } : u))
        );
      },
      uploadType
    );
    setUploads((prev) =>
      prev.map((u) => (u.key === key ? { ...u, abort: handle.abort } : u))
    );
    handle.promise
      .then(() => {
        setUploads((prev) =>
          prev.map((u) => (u.key === key ? { ...u, status: "done" } : u))
        );
        refresh();
        // Clear completed rows after a beat so the user sees them settle.
        setTimeout(() => {
          setUploads((prev) => prev.filter((u) => u.key !== key));
        }, 1500);
      })
      .catch((err: unknown) => {
        const msg =
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
            ? err.message
            : "Upload failed";
        setUploads((prev) =>
          prev.map((u) => (u.key === key ? { ...u, status: "failed", error: msg } : u))
        );
      });
  }

  async function onDelete(file: BotFile) {
    if (!confirm(`Delete "${file.file_name}"? This drops the file's vectors as well.`)) return;
    try {
      await api.deleteBotFile(slug, file.id);
      refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function onReindex(file: BotFile) {
    try {
      await api.reindexBotFile(slug, file.id);
      refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Reindex failed");
    }
  }

  async function onChangeType(file: BotFile, document_type: DocumentType) {
    try {
      const updated = await api.patchBotFile(slug, file.id, { document_type });
      setFiles((prev) => prev.map((f) => (f.id === file.id ? updated : f)));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Type update failed");
    }
  }

  async function onReindexAll() {
    if (
      !confirm(
        "Drop the bot's entire vector store and re-embed every file? Search will return no results until re-embedding completes."
      )
    )
      return;
    try {
      await api.reindexBot(slug);
      refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Reindex-all failed");
    }
  }

  // Storage usage
  const totalBytes = files.reduce((acc, f) => acc + (f.size_bytes ?? 0), 0);

  return (
    <>
      <div className="header">
        <h1>Files — {bot.name}</h1>
        <div className="row">
          <button className="ghost" onClick={refresh}>
            <RefreshCw strokeWidth={1.75} />
            Refresh
          </button>
          <button className="ghost" disabled={!writable} onClick={onReindexAll}>
            <RotateCw strokeWidth={1.75} />
            Re-index all
          </button>
        </div>
      </div>

      <div className="banner advisory banner-icon">
        <Info strokeWidth={1.75} />
        <div>
          Files are stored on this pod&apos;s local disk. If the pod is replaced,
          re-uploads will be needed. Object storage support is on the roadmap.
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div
        role="tablist"
        aria-label="Files sections"
        style={{
          display: "flex",
          gap: 4,
          borderBottom: "1px solid var(--panel-border)",
          marginBottom: 16,
        }}
      >
        {([
          { id: "uploads", label: "Uploads" },
          { id: "crawl", label: "Crawl website" },
        ] as { id: FilesTab; label: string }[]).map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={activeTab === t.id}
            className="ghost"
            onClick={() => setActiveTab(t.id)}
            style={{
              borderRadius: 0,
              border: "none",
              borderBottom:
                activeTab === t.id ? "2px solid var(--accent)" : "2px solid transparent",
              color: activeTab === t.id ? "var(--text)" : "var(--text-muted)",
              paddingBottom: 8,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === "crawl" && (
        <CrawlTab slug={slug} writable={writable} onCrawlComplete={refresh} />
      )}

      {activeTab === "uploads" && (
        <>
      <div
        className={`dropzone ${dragActive ? "active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (writable) setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          if (e.dataTransfer.files && writable) enqueueFiles(e.dataTransfer.files);
        }}
      >
        <p style={{ marginTop: 0 }}>
          {writable
            ? "Drag and drop files here, or"
            : "Your role doesn't permit uploads on this bot."}
        </p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files) enqueueFiles(e.target.files);
            if (fileInputRef.current) fileInputRef.current.value = "";
          }}
        />
        <button
          className="ghost"
          disabled={!writable}
          onClick={() => fileInputRef.current?.click()}
        >
          <UploadCloud strokeWidth={1.75} />
          Choose files
        </button>
        <div
          className="row"
          style={{ marginTop: 12, gap: 8, alignItems: "center", justifyContent: "center" }}
        >
          <label className="muted" style={{ fontSize: 12 }} htmlFor="upload-doc-type">
            Type:
          </label>
          <select
            id="upload-doc-type"
            value={uploadType}
            onChange={(e) => setUploadType(e.target.value as DocumentType)}
            disabled={!writable}
          >
            {DOCUMENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
          PDF / DOCX / TXT / HTML / MD / CSV — archive formats are rejected.
          Per-bot size and quota limits live in the bot config.
        </div>
      </div>

      {uploads.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2 style={{ marginTop: 0, fontSize: 14 }}>Uploads</h2>
          <table>
            <thead>
              <tr>
                <th>File</th>
                <th>Progress</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {uploads.map((u) => {
                const pct = u.total > 0 ? Math.round((u.loaded / u.total) * 100) : 0;
                return (
                  <tr key={u.key}>
                    <td className="cell-truncate" style={{ maxWidth: 280 }}>
                      {u.file.name}
                    </td>
                    <td style={{ minWidth: 160 }}>
                      <div
                        style={{
                          background: "var(--paper)",
                          border: "1px solid var(--border)",
                          borderRadius: 4,
                          height: 6,
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            width: `${pct}%`,
                            background: u.status === "failed" ? "var(--error)" : "var(--accent)",
                            height: "100%",
                            transition: "width 0.2s",
                          }}
                        />
                      </div>
                      <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                        {pct}% — {formatBytes(u.loaded)} / {formatBytes(u.total)}
                      </div>
                    </td>
                    <td>
                      {u.status === "queued" && (
                        <span className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <Clock size={12} strokeWidth={1.75} /> queued
                        </span>
                      )}
                      {u.status === "uploading" && (
                        <span className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <Loader2 size={12} strokeWidth={1.75} className="spin" /> uploading…
                        </span>
                      )}
                      {u.status === "done" && (
                        <span className="status-pill embedded">
                          <CircleCheck strokeWidth={1.75} /> uploaded
                        </span>
                      )}
                      {u.status === "failed" && (
                        <span className="status-pill failed" title={u.error}>
                          <CircleX strokeWidth={1.75} /> failed
                        </span>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {u.status === "uploading" && u.abort && (
                        <button className="ghost" onClick={() => u.abort?.()}>
                          Cancel
                        </button>
                      )}
                      {u.status === "failed" && (
                        <button
                          className="ghost"
                          onClick={() =>
                            setUploads((prev) => prev.filter((x) => x.key !== u.key))
                          }
                        >
                          Dismiss
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <RAGSearchPanel slug={bot.slug} />

      <div className="kb-grid" style={{ marginTop: 24 }}>
        <CollectionsRail
          files={files}
          selected={selectedCollection}
          onSelect={(k) => {
            setSelectedCollection(k);
            // Reset the status chip when changing collection so the user
            // doesn't end up looking at "Errors in Web pages" by accident.
            setStatusFilter("all");
          }}
          lastSyncedAt={lastSyncedAt}
        />
        <div className="card" style={{ padding: 0 }}>
          <div
            className="row"
            style={{
              padding: "12px 16px",
              borderBottom: "1px solid var(--border)",
              justifyContent: "space-between",
              alignItems: "baseline",
              gap: 8,
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>
                {selectedCollection === "all" ? "All files" : `Files · ${selectedCollection}`}
              </div>
              <div className="muted" style={{ fontSize: 11.5 }}>
                {visibleFiles.length} of {files.length} · {formatBytes(totalBytes)} total
              </div>
            </div>
          </div>

          <div className="toolbar">
            <input
              type="search"
              placeholder="Filter files…"
              value={fileQuery}
              onChange={(e) => setFileQuery(e.target.value)}
            />
            <span
              className={statusFilter === "all" ? "filter-chip active" : "filter-chip"}
              onClick={() => setStatusFilter("all")}
            >
              All <span className="count">{statusCounts.all}</span>
            </span>
            <span
              className={statusFilter === "indexed" ? "filter-chip active" : "filter-chip"}
              onClick={() => setStatusFilter("indexed")}
            >
              Indexed <span className="count">{statusCounts.indexed}</span>
            </span>
            <span
              className={statusFilter === "in_progress" ? "filter-chip active" : "filter-chip"}
              onClick={() => setStatusFilter("in_progress")}
            >
              In progress <span className="count">{statusCounts.in_progress}</span>
            </span>
            <span
              className={statusFilter === "errors" ? "filter-chip active" : "filter-chip"}
              onClick={() => setStatusFilter("errors")}
            >
              Errors <span className="count">{statusCounts.errors}</span>
            </span>
          </div>

        {loading ? (
          <p className="muted" style={{ padding: 16 }}>Loading…</p>
        ) : files.length === 0 ? (
          <p className="muted" style={{ padding: 16 }}>No files yet. Upload your first one above.</p>
        ) : visibleFiles.length === 0 ? (
          <p className="muted" style={{ padding: 16 }}>
            No files match this filter.
          </p>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Size</th>
                <th>MIME</th>
                <th>Doc type</th>
                <th>Status</th>
                <th>Uploaded</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visibleFiles.map((f) => (
                <tr key={f.id}>
                  <td className="cell-truncate" style={{ maxWidth: 280 }}>
                    {f.file_name}
                  </td>
                  <td className="muted">{formatBytes(f.size_bytes)}</td>
                  <td className="muted">{f.mime_type ?? "—"}</td>
                  <td>
                    <select
                      value={f.document_type}
                      onChange={(e) => onChangeType(f, e.target.value as DocumentType)}
                      disabled={!writable}
                      title="Editing the type triggers a re-embed so chunks pick up the new tag"
                    >
                      {DOCUMENT_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t.replace(/_/g, " ")}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <span className={`status-pill ${f.embedding_status}`} title={f.error_message ?? undefined}>
                      {f.embedding_status === "pending" && <Clock strokeWidth={1.75} />}
                      {f.embedding_status === "embedding" && <Loader2 strokeWidth={1.75} className="spin" />}
                      {f.embedding_status === "embedded" && <CircleCheck strokeWidth={1.75} />}
                      {f.embedding_status === "failed" && <CircleX strokeWidth={1.75} />}
                      {f.embedding_status}
                    </span>
                  </td>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>
                    {f.created_on ? new Date(f.created_on).toLocaleString() : "—"}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button
                      className="ghost"
                      disabled={!writable}
                      onClick={() => onReindex(f)}
                      aria-label={`Re-index ${f.file_name}`}
                      title="Re-index"
                    >
                      <RotateCw strokeWidth={1.75} />
                      Re-index
                    </button>{" "}
                    <button
                      className="ghost"
                      disabled={!writable}
                      onClick={() => onDelete(f)}
                      aria-label={`Delete ${f.file_name}`}
                      title="Delete"
                    >
                      <Trash2 strokeWidth={1.75} />
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        </div>
      </div>
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// RAG search inspector — "what does the bot see for this query?"
// ---------------------------------------------------------------------------
type RAGHit = {
  score: number | null;
  chunk: string;
  metadata: Record<string, unknown>;
};

function RAGSearchPanel({ slug }: { slug: string }) {
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

  return (
    <div className="card" style={{ marginTop: 24 }}>
      <h2 style={{ marginTop: 0 }}>Query the knowledge base</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        Run the same retrieval the chat runtime would use. The hits below are
        what the LLM would see as <code>{"{context}"}</code> when answering this
        query.
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
                  {h.score != null && (
                    <span className="tag" title="Relevance score from the vector search">
                      score {h.score.toFixed(3)}
                    </span>
                  )}
                </div>
                <pre
                  className="snippet"
                  style={{ marginTop: 8, marginBottom: 0, whiteSpace: "pre-wrap", maxHeight: 240, overflowY: "auto" }}
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
