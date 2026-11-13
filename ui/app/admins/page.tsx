"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ChevronRight, UserPlus } from "lucide-react";
import AdminShell from "@/components/AdminShell";
import { AdminUser, PendingAdmin, api } from "@/lib/api";

/**
 * Admins overview — superadmin-only.
 *
 * The legacy invite-by-token flow is gone. Admins are now pre-staged in
 * `pending_admin` and consumed on first matching IdP login. This page
 * exposes the create/list/revoke flow and a quick view of active admins.
 */
export default function AdminsPage() {
  const [admins, setAdmins] = useState<AdminUser[]>([]);
  const [pending, setPending] = useState<PendingAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteAsSuper, setInviteAsSuper] = useState(false);
  const [inviteSubmitting, setInviteSubmitting] = useState(false);
  const [inviteResult, setInviteResult] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.listAdmins();
      setAdmins(list.admins);
      setPending(list.pending);
    } catch (err: any) {
      setError(err?.message ?? "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onInvite(e: FormEvent) {
    e.preventDefault();
    setInviteSubmitting(true);
    setInviteResult(null);
    setError(null);
    try {
      await api.createPendingAdmin({
        email: inviteEmail,
        is_superadmin: inviteAsSuper,
        bot_assignments: [],
      });
      setInviteResult(
        `${inviteEmail} is staged. They'll be activated automatically on their first sign-in via the configured identity provider.`
      );
      setInviteEmail("");
      setInviteAsSuper(false);
      await refresh();
    } catch (err: any) {
      setError(err?.message ?? "Invite failed");
    } finally {
      setInviteSubmitting(false);
    }
  }

  async function onRevoke(email: string) {
    if (!confirm(`Revoke pending invite for ${email}?`)) return;
    try {
      await api.revokePendingAdmin(email);
      await refresh();
    } catch (err: any) {
      setError(err?.message ?? "Failed to revoke");
    }
  }

  async function onSetActive(admin: AdminUser, value: boolean) {
    try {
      await api.updateAdmin(admin.id, { is_active: value });
      await refresh();
    } catch (err: any) {
      setError(err?.message ?? "Failed to update admin");
    }
  }

  async function onSetSuperadmin(admin: AdminUser, value: boolean) {
    try {
      await api.updateAdmin(admin.id, { is_superadmin: value });
      await refresh();
    } catch (err: any) {
      setError(err?.message ?? "Failed to update admin");
    }
  }

  return (
    <AdminShell>
      <div className="header">
        <h1>Admins</h1>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="stack">
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Stage a new admin</h2>
          <form onSubmit={onInvite}>
            <div className="row" style={{ alignItems: "flex-end" }}>
              <div className="field field-grow" style={{ marginBottom: 0 }}>
                <label htmlFor="invite-email">Email</label>
                <input
                  id="invite-email"
                  type="email"
                  required
                  placeholder="teammate@example.com"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                />
              </div>
              <label style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 0 }}>
                <input
                  type="checkbox"
                  checked={inviteAsSuper}
                  onChange={(e) => setInviteAsSuper(e.target.checked)}
                />
                Superadmin
              </label>
              <button type="submit" disabled={inviteSubmitting}>
                <UserPlus strokeWidth={1.75} />
                {inviteSubmitting ? "Staging…" : "Stage invite"}
              </button>
            </div>
          </form>
          {inviteResult && (
            <div className="success" style={{ marginTop: 16 }}>
              {inviteResult}
            </div>
          )}
        </div>

        <div className="card">
          <h2 style={{ marginTop: 0 }}>Active admins</h2>
          {loading ? (
            <p className="muted">Loading…</p>
          ) : (
            <div className="table-stack"><table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Bots</th>
                  <th>Status</th>
                  <th>Last sign-in</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {admins.map((u) => (
                  <tr key={u.id}>
                    <td data-label="Email">
                      <Link
                        href={`/admins/${encodeURIComponent(u.id)}`}
                        style={{ color: "inherit", textDecoration: "none" }}
                      >
                        {u.email}
                      </Link>
                    </td>
                    <td data-label="Role">
                      <label style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={u.is_superadmin}
                          onChange={(e) => onSetSuperadmin(u, e.target.checked)}
                        />
                        superadmin
                      </label>
                    </td>
                    <td data-label="Bots">
                      <Link
                        href={`/admins/${encodeURIComponent(u.id)}`}
                        className="ghost compact"
                        style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
                      >
                        {(() => {
                          const count = u.memberships?.length ?? 0;
                          if (count === 0) return "Manage…";
                          return `${count} bot${count === 1 ? "" : "s"}`;
                        })()}
                        <ChevronRight size={12} strokeWidth={1.75} />
                      </Link>
                    </td>
                    <td data-label="Status">
                      <span className={u.is_active ? "tag" : "tag muted"}>
                        {u.is_active ? "active" : "disabled"}
                      </span>
                    </td>
                    <td data-label="Last sign-in" className="muted">
                      {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "never"}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button className="ghost" onClick={() => onSetActive(u, !u.is_active)}>
                        {u.is_active ? "Disable" : "Enable"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>

        <div className="card">
          <h2 style={{ marginTop: 0 }}>Pending invites</h2>
          {loading ? (
            <p className="muted">Loading…</p>
          ) : pending.length === 0 ? (
            <p className="muted">No pending invites.</p>
          ) : (
            <div className="table-stack"><table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Created</th>
                  <th>Bot assignments</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {pending.map((p) => (
                  <tr key={p.id}>
                    <td data-label="Email">
                      {p.email}
                      {p.is_superadmin && <span className="tag" style={{ marginLeft: 6 }}>superadmin</span>}
                    </td>
                    <td data-label="Created" className="muted">{new Date(p.created_at).toLocaleString()}</td>
                    <td data-label="Bot assignments" className="muted">
                      {p.bot_assignments.length === 0
                        ? "—"
                        : p.bot_assignments.map((a) => `${a.role}@${a.bot_id}`).join(", ")}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <button className="ghost" onClick={() => onRevoke(p.email)}>
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>
      </div>
    </AdminShell>
  );
}
