"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, RotateCcw, Trash2 } from "lucide-react";
import AdminShell from "@/components/AdminShell";
import { ApiError, DeletedBot, api } from "@/lib/api";

export default function DeletedBotsPage() {
  return (
    <AdminShell>
      <DeletedBotsBody />
    </AdminShell>
  );
}

function DeletedBotsBody() {
  const router = useRouter();
  const [rows, setRows] = useState<DeletedBot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const list = await api.listDeletedBots();
      setRows(list);
    } catch (err: unknown) {
      // 403 just means the admin has no owner memberships — show an
      // empty state, not a banner error.
      if (err instanceof ApiError && err.status === 403) {
        setRows([]);
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load deleted bots");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onRestore(slug: string) {
    setRestoring(slug);
    setError(null);
    try {
      await api.restoreBot(slug);
      router.push(`/bots/${slug}/dashboard`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Restore failed");
      setRestoring(null);
    }
  }

  return (
    <>
      <div className="header">
        <div>
          <h1>Recently deleted</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            Bots stay restorable for 7 days after delete. After that, the daily
            purge job removes them permanently.
          </div>
        </div>
        <div className="row">
          <button className="ghost compact" onClick={() => router.push("/bots")}>
            <ArrowLeft size={14} strokeWidth={1.75} />
            Back to bots
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {rows === null && !error && <p className="muted">Loading…</p>}

      {rows && rows.length === 0 && (
        <div className="card narrow" style={{ textAlign: "center", padding: 32 }}>
          <Trash2 size={24} strokeWidth={1.5} />
          <h2 style={{ marginTop: 12 }}>Nothing to restore</h2>
          <p className="muted">
            No bots have been soft-deleted in the last 7 days.
          </p>
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <div className="table-scroll"><table className="table">
            <thead>
              <tr>
                <th>Bot</th>
                <th>Deleted</th>
                <th>Hard-purge at</th>
                <th className="col-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => (
                <tr key={b.id}>
                  <td>
                    <div className="bot-cell">
                      <div className="bot-info">
                        <div className="bot-name">{b.name}</div>
                        <div className="bot-id">{b.slug}</div>
                      </div>
                    </div>
                  </td>
                  <td
                    className="muted"
                    style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                  >
                    {formatDate(b.deleted_at)}
                  </td>
                  <td
                    style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                  >
                    <PurgeCountdown at={b.hard_purge_at} />
                  </td>
                  <td className="col-right">
                    <button
                      onClick={() => onRestore(b.slug)}
                      disabled={restoring === b.slug}
                    >
                      <RotateCcw size={13} strokeWidth={1.75} />
                      {restoring === b.slug ? "Restoring…" : "Restore"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      )}
    </>
  );
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function PurgeCountdown({ at }: { at: string }) {
  const target = new Date(at).getTime();
  const now = Date.now();
  const ms = target - now;
  if (ms <= 0) {
    return <span className="error">overdue — next purge run</span>;
  }
  const days = Math.floor(ms / 86_400_000);
  const hours = Math.floor((ms % 86_400_000) / 3_600_000);
  return (
    <span>
      {days > 0 ? `${days}d ` : ""}
      {hours}h remaining
    </span>
  );
}
