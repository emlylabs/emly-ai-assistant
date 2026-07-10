"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useBot } from "@/components/BotShell";
import StatusPill from "@/components/StatusPill";
import { api, type AuditLog } from "@/lib/api";
import { relativeTime } from "@/lib/aggregations";

const PAGE_SIZE = 50;

/**
 * Phase 11 backend-backfill: per-bot audit log. Any role on the bot can
 * read; the API layer enforces membership. Mirrors the workspace page in
 * `/admins/audit` but scoped to the active bot.
 */
export default function BotAuditPage() {
  const { bot } = useBot();
  const [items, setItems] = useState<AuditLog[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [skip, setSkip] = useState(0);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const res = await api.listBotAuditLogs(bot.slug, { skip, limit: PAGE_SIZE });
      setItems(res.items);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load audit log");
    } finally {
      setRefreshing(false);
    }
  }, [bot.slug, skip]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <>
      <div className="header">
        <div>
          <h1>Audit — {bot.name}</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            Audit events scoped to this bot
          </div>
        </div>
        <button className="ghost compact" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={14} strokeWidth={1.75} className={refreshing ? "spin" : undefined} />
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card" style={{ padding: 0 }}>
        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Action</th>
              <th>Admin</th>
              <th>Target</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ textAlign: "center", padding: 24, color: "var(--muted)" }}>
                  No events recorded for this bot yet.
                </td>
              </tr>
            ) : (
              items.map((row) => (
                <tr key={row.id}>
                  <td className="muted" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    {relativeTime(row.created_at)}
                  </td>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{row.action}</td>
                  <td className="muted">{row.admin_id ?? "—"}</td>
                  <td className="muted" style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    {row.target_type ? `${row.target_type}:${row.target_id ?? ""}` : "—"}
                  </td>
                  <td>
                    <StatusPill variant={row.success ? "success" : "danger"}>
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
            Showing {skip + 1}–{skip + items.length}
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
    </>
  );
}
