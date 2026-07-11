"use client";

import { useCallback, useEffect, useState } from "react";
import { useBot } from "@/components/BotShell";
import { EndUser, api } from "@/lib/api";

const PAGE_SIZE = 50;

export default function BotUsersPage() {
  const { bot } = useBot();
  const [items, setItems] = useState<EndUser[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listBotEndUsers(bot.slug, { skip, limit: PAGE_SIZE });
      setItems(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [bot.slug, skip]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <>
      <div className="header">
        <h1>Bot users — {bot.name}</h1>
        <button className="ghost" onClick={refresh}>
          Refresh
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {loading && items.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">No end users yet.</p>
      ) : (
        <>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Country</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {items.map((u) => (
                <tr key={u.id}>
                  <td className="cell-truncate" style={{ maxWidth: 200 }}>
                    {u.id}
                  </td>
                  <td>{u.email ?? "—"}</td>
                  <td>{u.phone ?? "—"}</td>
                  <td className="muted">{u.country ?? "—"}</td>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>
                    {u.created_on ? new Date(u.created_on).toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pagination">
            <span className="muted">
              Showing {items.length === 0 ? 0 : skip + 1}–{skip + items.length} of {total}
            </span>
            <button
              className="ghost"
              disabled={skip === 0}
              onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
            >
              Prev
            </button>
            <button
              className="ghost"
              disabled={skip + PAGE_SIZE >= total}
              onClick={() => setSkip(skip + PAGE_SIZE)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </>
  );
}
