"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Trash2 } from "lucide-react";
import AdminShell from "@/components/AdminShell";
import { AdminUser, WorkspaceTemplate, api } from "@/lib/api";
import { relativeTime } from "@/lib/aggregations";

/**
 * Admin-saved bot templates. The create wizard merges these with the
 * code-baked starter templates so an organization can stamp out their
 * own starting points (e.g. "Acme support bot — RAG enabled").
 * Snapshot writes happen from the per-bot config editor ("Save as
 * workspace template…"); this page is mostly for review + delete.
 */
export default function WorkspaceTemplatesPage() {
  const [me, setMe] = useState<AdminUser | null>(null);
  const [templates, setTemplates] = useState<WorkspaceTemplate[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [meRes, list] = await Promise.all([
        api.me(),
        api.listWorkspaceTemplates(),
      ]);
      setMe(meRes);
      setTemplates(list);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onDelete(t: WorkspaceTemplate) {
    if (!confirm(`Delete template "${t.name}"? Bots already created from it keep their config.`)) return;
    try {
      await api.deleteWorkspaceTemplate(t.id);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <AdminShell>
      <div className="header">
        <div>
          <h1>Workspace templates</h1>
          <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
            Saved snapshots that the create-bot wizard offers alongside the
            built-in starters.{" "}
            <Link href="/workspace/settings">Settings →</Link>
          </div>
        </div>
        <div className="row">
          <button className="ghost" onClick={refresh} disabled={loading}>
            Reload
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : !templates || templates.length === 0 ? (
        <div className="card narrow" style={{ textAlign: "center", padding: 24 }}>
          <p className="muted" style={{ marginTop: 0 }}>
            No workspace templates yet. From any bot&apos;s config editor,
            choose <strong>Save as workspace template</strong> to add one.
          </p>
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <div className="table-scroll"><table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Saved</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {templates.map((t) => (
                <tr key={t.id}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{t.name}</div>
                    <div className="muted" style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
                      workspace:{t.id}
                    </div>
                  </td>
                  <td className="muted" style={{ fontSize: 13 }}>
                    {t.description || "—"}
                  </td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {relativeTime(t.created_at)}
                  </td>
                  <td>
                    {me?.is_superadmin && (
                      <button
                        type="button"
                        className="ghost compact"
                        onClick={() => onDelete(t)}
                        aria-label={`Delete template ${t.name}`}
                      >
                        <Trash2 size={13} strokeWidth={1.75} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      )}
    </AdminShell>
  );
}
