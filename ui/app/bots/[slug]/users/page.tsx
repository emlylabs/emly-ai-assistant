"use client";

import { useCallback, useEffect, useState } from "react";
import { useBot } from "@/components/BotShell";
import { channelLabel } from "@/components/ChannelChip";
import { EndUser, api } from "@/lib/api";

const PAGE_SIZE = 50;

export default function BotUsersPage() {
  const { bot, currentRole } = useBot();
  const canErase = currentRole === "owner";
  const [erasing, setErasing] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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

  async function erase(u: EndUser) {
    const label = u.email || u.id;
    if (
      !confirm(
        `Permanently delete everything stored about ${label} for this bot: profile, conversations, form submissions and attachments? This can't be undone.`,
      )
    )
      return;
    setErasing(u.id);
    setError(null);
    setNotice(null);
    try {
      const res = await api.eraseBotEndUser(bot.slug, u.id);
      const c = res.erased;
      setNotice(
        `Deleted ${label}: ${c.messages ?? 0} messages, ${c.sessions ?? 0} conversations, ` +
          `${c.submissions ?? 0} submissions, ${c.attachments ?? 0} attachments.`,
      );
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setErasing(null);
    }
  }

  return (
    <>
      <div className="header">
        <h1>Bot users — {bot.name}</h1>
        <button className="ghost" onClick={refresh}>
          Refresh
        </button>
      </div>

      {error && <div className="error">{error}</div>}
      {notice && <div className="success">{notice}</div>}

      {loading && items.length === 0 ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted">No end users yet.</p>
      ) : (
        <>
          <div className="table-scroll"><table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Channel</th>
                <th>Email</th>
                <th>Phone</th>
                <th>Country</th>
                <th>Created</th>
                {canErase && <th aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {items.map((u) => (
                <tr key={u.id}>
                  <td className="cell-truncate" style={{ maxWidth: 200 }}>
                    {u.id}
                  </td>
                  <td className="muted">
                    {channelLabel(typeof u.meta?.channel === "string" ? u.meta.channel : "web_widget")}
                  </td>
                  <td>{u.email ?? "—"}</td>
                  <td>{u.phone ?? "—"}</td>
                  <td className="muted">{u.country ?? "—"}</td>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>
                    {u.created_at ? new Date(u.created_at).toLocaleString() : "—"}
                  </td>
                  {canErase && (
                    <td style={{ textAlign: "right" }}>
                      <button
                        type="button"
                        className="ghost compact danger"
                        disabled={erasing !== null}
                        onClick={() => erase(u)}
                      >
                        {erasing === u.id ? "Deleting…" : "Delete data"}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table></div>
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
