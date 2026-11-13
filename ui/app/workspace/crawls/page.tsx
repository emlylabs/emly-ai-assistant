"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { RefreshCw } from "lucide-react";
import AdminShell from "@/components/AdminShell";
import StatusPill from "@/components/StatusPill";
import { WorkspaceCrawlJobRow, api } from "@/lib/api";
import { relativeTime } from "@/lib/aggregations";

/**
 * Workspace-wide crawl monitor — every crawl across every bot the caller
 * can read, in one table.
 *
 * §11 T7. Previously every crawl was scoped to its bot's Files tab, so an
 * admin operating 20 bots had no way to answer "what's running right now"
 * without clicking through twenty Crawl tabs. Read-only by design: actions
 * (pause/resume/cancel) still happen per-bot so the per-bot audit trail
 * stays the source of truth.
 */
const PAGE_SIZE = 50;
const POLL_MS = 8000;

export default function WorkspaceCrawlsPage() {
  return (
    <AdminShell>
      <Body />
    </AdminShell>
  );
}

function Body() {
  const [items, setItems] = useState<WorkspaceCrawlJobRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await api.listWorkspaceCrawlJobs({
        limit: PAGE_SIZE,
        offset,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [offset]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while any visible job is still active so the table updates live.
  useEffect(() => {
    const anyActive = items.some(
      (j) => j.status === "running" || j.status === "paused",
    );
    if (!anyActive) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [items, refresh]);

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + items.length, total);

  return (
    <>
      <div className="header">
        <div>
          <h1>Crawls</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            Every crawl across every bot you can read · refreshes while jobs
            are active
          </div>
        </div>
        <button
          className="ghost compact"
          onClick={refresh}
          disabled={refreshing}
        >
          <RefreshCw
            size={14}
            strokeWidth={1.75}
            className={refreshing ? "spin" : undefined}
          />
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {loading && items.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="card" style={{ padding: 24 }}>
          <p className="muted" style={{ margin: 0 }}>
            No crawls yet. Start one from any bot&apos;s{" "}
            <em>Files → Crawl website</em> tab.
          </p>
        </div>
      ) : (
        <>
          <div className="table-scroll"><table className="table">
            <thead>
              <tr>
                <th>Bot</th>
                <th>Seed URL</th>
                <th>Status</th>
                <th className="col-right">Progress</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {items.map((j) => (
                <tr key={j.id}>
                  <td>
                    <Link
                      href={`/bots/${j.bot_slug}/files`}
                      style={{ color: "inherit" }}
                    >
                      {j.bot_name || j.bot_slug}
                    </Link>
                  </td>
                  <td
                    className="cell-truncate"
                    style={{ maxWidth: 360 }}
                    title={j.seed_url}
                  >
                    <a
                      href={j.seed_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="muted"
                    >
                      {j.seed_url}
                    </a>
                  </td>
                  <td>
                    <StatusPill
                      variant={
                        j.status === "completed"
                          ? "success"
                          : j.status === "failed" || j.status === "cancelled"
                            ? "danger"
                            : j.status === "paused"
                              ? "neutral"
                              : "info"
                      }
                    >
                      {j.status}
                    </StatusPill>
                  </td>
                  <td
                    className="col-right"
                    style={{ fontVariantNumeric: "tabular-nums", fontSize: 12 }}
                    title={`done ${j.pages_done} · skipped ${j.pages_skipped} · failed ${j.pages_failed}`}
                  >
                    {j.pages_done}/{j.pages_total}
                    {j.pages_failed > 0 && (
                      <span className="muted">
                        {" "}
                        · {j.pages_failed} failed
                      </span>
                    )}
                  </td>
                  <td
                    className="muted"
                    style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                  >
                    {relativeTime(
                      j.completed_at ?? j.updated_at ?? j.created_at ?? "",
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <div
            className="row"
            style={{
              justifyContent: "space-between",
              alignItems: "center",
              marginTop: 12,
              fontSize: 12,
            }}
          >
            <span className="muted">
              {pageStart}–{pageEnd} of {total}
            </span>
            <div className="row" style={{ gap: 6 }}>
              <button
                className="ghost"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                ← Prev
              </button>
              <button
                className="ghost"
                disabled={offset + items.length >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next →
              </button>
            </div>
          </div>
        </>
      )}
    </>
  );
}
