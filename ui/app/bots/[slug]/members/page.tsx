"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useBot } from "@/components/BotShell";
import { AdminUser, ApiError, Membership, Role, api } from "@/lib/api";

const ROLES: Role[] = ["owner", "admin", "viewer"];

export default function BotMembersPage() {
  const { bot, currentRole, refreshBot } = useBot();
  const slug = bot.slug;
  const isOwner = currentRole === "owner";

  const [members, setMembers] = useState<Membership[] | null>(null);
  const [allAdmins, setAllAdmins] = useState<AdminUser[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [grantOpen, setGrantOpen] = useState(false);
  const [grantAdminId, setGrantAdminId] = useState("");
  const [grantRole, setGrantRole] = useState<Role>("admin");
  const [submitting, setSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<{ adminId: string; msg: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listBotAdmins(slug);
      setMembers(list);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load members");
    }
  }, [slug]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    api
      .listAdmins()
      .then((res) => setAllAdmins(res.admins))
      .catch(() => setAllAdmins([]));
  }, []);

  const adminEmailById = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of allAdmins) m.set(a.id, a.email);
    return m;
  }, [allAdmins]);

  const grantableAdmins = useMemo(() => {
    const memberIds = new Set(members?.map((m) => m.admin_id) ?? []);
    return allAdmins.filter((a) => !memberIds.has(a.id));
  }, [allAdmins, members]);

  async function onGrant() {
    if (!grantAdminId) return;
    setInlineError(null);
    setSubmitting(true);
    try {
      await api.grantMembership(slug, { admin_id: grantAdminId, role: grantRole });
      setGrantOpen(false);
      setGrantAdminId("");
      setGrantRole("admin");
      await refresh();
    } catch (err: unknown) {
      const msg =
        err instanceof ApiError && err.status === 409
          ? "That admin already has a membership on this bot."
          : err instanceof Error
          ? err.message
          : "Grant failed";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  async function onRoleChange(adminId: string, newRole: Role) {
    setInlineError(null);
    try {
      await api.updateMembershipRole(slug, adminId, { role: newRole });
      await refresh();
      // If the active admin downgraded themselves, refresh the BotShell
      // context so role-gated controls re-render with the new role.
      if (members?.find((m) => m.admin_id === adminId)?.role === "owner") {
        await refreshBot();
      }
    } catch (err: unknown) {
      const msg =
        err instanceof ApiError && err.status === 409
          ? "This bot would have no owners. Promote another owner first."
          : err instanceof Error
          ? err.message
          : "Update failed";
      setInlineError({ adminId, msg });
      // Force a re-render so the dropdown reverts to the original value.
      await refresh();
    }
  }

  async function onRevoke(adminId: string) {
    if (!confirm("Revoke this membership?")) return;
    setInlineError(null);
    try {
      await api.revokeMembership(slug, adminId);
      await refresh();
    } catch (err: unknown) {
      const msg =
        err instanceof ApiError && err.status === 409
          ? "This bot would have no owners. Promote another owner first."
          : err instanceof Error
          ? err.message
          : "Revoke failed";
      setInlineError({ adminId, msg });
    }
  }

  return (
    <>
      <div className="header">
        <h1>Members — {bot.name}</h1>
        <div className="row">
          <button className="ghost" onClick={refresh}>
            Refresh
          </button>
          {isOwner && (
            <button onClick={() => setGrantOpen(true)} disabled={grantableAdmins.length === 0}>
              Add member
            </button>
          )}
        </div>
      </div>

      {!isOwner && (
        <div className="banner advisory">
          You can view this list. Only owners can grant, revoke, or change roles.
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {members === null ? (
        <p className="muted">Loading…</p>
      ) : members.length === 0 ? (
        <p className="muted">No members yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Added</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => {
              const email = adminEmailById.get(m.admin_id) ?? m.admin_id;
              const showInline = inlineError?.adminId === m.admin_id;
              return (
                <tr key={m.admin_id}>
                  <td>{email}</td>
                  <td>
                    {isOwner ? (
                      <select
                        value={m.role}
                        onChange={(e) => onRoleChange(m.admin_id, e.target.value as Role)}
                        style={{
                          padding: "4px 8px",
                          background: "var(--paper)",
                          color: "var(--text)",
                          border: "1px solid var(--panel-border)",
                          borderRadius: 6,
                        }}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="tag">{m.role}</span>
                    )}
                  </td>
                  <td className="muted" style={{ whiteSpace: "nowrap" }}>
                    {new Date(m.created_at).toLocaleString()}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {isOwner && (
                      <button className="ghost" onClick={() => onRevoke(m.admin_id)}>
                        Revoke
                      </button>
                    )}
                    {showInline && inlineError && (
                      <div className="error" style={{ marginTop: 6, fontSize: 12 }}>
                        {inlineError.msg}
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {grantOpen && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => setGrantOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
        >
          <div
            className="card narrow"
            style={{ minWidth: 380 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ marginTop: 0 }}>Add member</h2>
            {grantableAdmins.length === 0 ? (
              <p className="muted">
                Every active admin is already on this bot. <Link href="/admins">Invite a new admin</Link> first.
              </p>
            ) : (
              <>
                <div className="field">
                  <label htmlFor="grant-admin">Admin</label>
                  <select
                    id="grant-admin"
                    value={grantAdminId}
                    onChange={(e) => setGrantAdminId(e.target.value)}
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      background: "var(--paper)",
                      color: "var(--text)",
                      border: "1px solid var(--panel-border)",
                      borderRadius: 6,
                    }}
                  >
                    <option value="">— pick an admin —</option>
                    {grantableAdmins.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.email}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="grant-role">Role</label>
                  <select
                    id="grant-role"
                    value={grantRole}
                    onChange={(e) => setGrantRole(e.target.value as Role)}
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      background: "var(--paper)",
                      color: "var(--text)",
                      border: "1px solid var(--panel-border)",
                      borderRadius: 6,
                    }}
                  >
                    {ROLES.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                </div>
              </>
            )}
            <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
              <button className="ghost" onClick={() => setGrantOpen(false)}>
                Cancel
              </button>
              <button onClick={onGrant} disabled={!grantAdminId || submitting}>
                {submitting ? "Granting…" : "Grant"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
