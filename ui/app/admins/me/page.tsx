"use client";

/**
 * /admins/me — in-app "My invites" view.
 *
 * Lists the signed-in admin's pending space invites and lets them
 * accept or decline inline. Mirrors the picker UX on /accept-invite
 * but lives inside the AdminShell so the user can review without
 * being bounced through OIDC.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import AdminShell from "@/components/AdminShell";
import { MyInvite, api } from "@/lib/api";

export default function MyInvitesPage() {
  const router = useRouter();
  const [invites, setInvites] = useState<MyInvite[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listMyInvites();
      setInvites(data);
    } catch (err: any) {
      setError(err?.message ?? "Failed to load invitations");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onAccept(token: string) {
    setBusy(token);
    setError(null);
    try {
      const res = await api.acceptInvite(token);
      if (res.bot_slug) {
        router.replace(`/bots/${encodeURIComponent(res.bot_slug)}/dashboard`);
      } else {
        await refresh();
      }
    } catch (err: any) {
      setError(err?.message ?? "Failed to accept");
    } finally {
      setBusy(null);
    }
  }

  async function onReject(token: string) {
    if (!confirm("Decline this invitation?")) return;
    setBusy(token);
    setError(null);
    try {
      await api.rejectInvite(token);
      await refresh();
    } catch (err: any) {
      setError(err?.message ?? "Failed to decline");
    } finally {
      setBusy(null);
    }
  }

  return (
    <AdminShell>
      <div className="header">
        <h1>My invitations</h1>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card">
        {invites === null ? (
          <p className="muted">Loading…</p>
        ) : invites.length === 0 ? (
          <p className="muted">You have no pending invitations.</p>
        ) : (
          <div className="table-stack">
            <table>
              <thead>
                <tr>
                  <th>Space</th>
                  <th>Role</th>
                  <th>Invited by</th>
                  <th>Sent</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {invites.map((inv) => (
                  <tr key={inv.token}>
                    <td data-label="Space">
                      <strong>{inv.bot_name ?? "—"}</strong>
                      {inv.is_superadmin && (
                        <span className="tag" style={{ marginLeft: 6 }}>
                          superadmin
                        </span>
                      )}
                    </td>
                    <td data-label="Role">
                      <span className="tag">{inv.role}</span>
                    </td>
                    <td data-label="Invited by" className="muted">
                      {inv.inviter_name || inv.inviter_email || "—"}
                    </td>
                    <td data-label="Sent" className="muted">
                      {new Date(inv.created_at).toLocaleString()}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
                        <button
                          onClick={() => onAccept(inv.token)}
                          disabled={busy !== null}
                        >
                          {busy === inv.token ? "…" : "Accept"}
                        </button>
                        <button
                          className="ghost"
                          onClick={() => onReject(inv.token)}
                          disabled={busy !== null}
                        >
                          Decline
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AdminShell>
  );
}
