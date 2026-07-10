"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import AdminShell from "@/components/AdminShell";
import StatusPill from "@/components/StatusPill";
import { api, type AuditLog } from "@/lib/api";
import { relativeTime } from "@/lib/aggregations";

type Filter = "all" | "auth" | "bot" | "failed";

const PAGE_SIZE = 50;

/**
 * Phase 11 backend-backfill: workspace-wide audit log viewer.
 *
 * Superadmin-only at the API level; the page itself is gated by the
 * `/audit-logs` endpoint returning 403 for non-superadmins. Filter chips
 * are client-side facets over the loaded page; querying for other slices
 * (e.g. specific admin_id) would extend `api.listAuditLogs`.
 */
export default function AuditPage() {
  const [items, setItems] = useState<AuditLog[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [skip, setSkip] = useState(0);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await api.listAuditLogs({
        skip,
        limit: PAGE_SIZE,
        success: filter === "failed" ? false : undefined,
      });
      setItems(res.items);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load audit log");
    } finally {
      setRefreshing(false);
    }
  }, [skip, filter]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const visible = useMemo(() => {
    if (filter === "auth") return items.filter((i) => i.action.startsWith("auth."));
    if (filter === "bot") return items.filter((i) => i.action.startsWith("bot.") || i.action.startsWith("session."));
    if (filter === "failed") return items.filter((i) => !i.success);
    return items;
  }, [items, filter]);

  return (
    <AdminShell>
      <div className="header">
        <div>
          <h1>Audit log</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            Workspace-wide auth and admin events · superadmin view
          </div>
        </div>
        <button className="ghost compact" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={14} strokeWidth={1.75} className={refreshing ? "spin" : undefined} />
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card" style={{ padding: 0 }}>
        <div className="toolbar">
          {(["all", "auth", "bot", "failed"] as Filter[]).map((f) => (
            <button
              key={f}
              type="button"
              className={filter === f ? "filter-chip active" : "filter-chip"}
              aria-pressed={filter === f}
              onClick={() => {
                setFilter(f);
                setSkip(0);
              }}
            >
              {f === "all" ? "All" : f === "auth" ? "Auth" : f === "bot" ? "Bot / session" : "Failed"}
            </button>
          ))}
          <div className="toolbar-spacer" />
          <span className="muted" style={{ fontSize: 11.5 }}>
            Showing {visible.length} of {items.length}
          </span>
        </div>

        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Action</th>
              <th>Admin</th>
              <th>Bot</th>
              <th>Target</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td colSpan={6} style={{ textAlign: "center", padding: 24, color: "var(--muted)" }}>
                  No matching events.
                </td>
              </tr>
            ) : (
              visible.map((row) => (
                <tr key={row.id}>
                  <td className="muted text-mono" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    {relativeTime(row.created_at)}
                  </td>
                  <td className="text-mono" style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                    {row.action}
                  </td>
                  <td className="muted">{row.admin_id ?? "—"}</td>
                  <td className="muted">{row.bot_id ?? "—"}</td>
                  <td className="muted" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    {row.target_type ? `${row.target_type}:${row.target_id ?? ""}` : "—"}
                  </td>
                  <td>
                    <StatusPill variant={row.success ? "success" : "danger"} noDot={false}>
                      {row.success ? "ok" : "failed"}
                    </StatusPill>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        <div className="pagination" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", margin: 0 }}>
          <span className="muted">
            {visible.length === 0
              ? "No matching events"
              : `Showing ${skip + 1}–${skip + visible.length}`}
          </span>
          <button
            className="ghost compact"
            disabled={skip === 0}
            onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
          >
            Prev
          </button>
          <button
            className="ghost compact"
            disabled={items.length < PAGE_SIZE}
            onClick={() => setSkip(skip + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      </div>
    </AdminShell>
  );
}
