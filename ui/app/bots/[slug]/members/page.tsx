"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useBot } from "@/components/BotShell";
import Modal from "@/components/Modal";
import {
  AdminUser,
  ApiError,
  Membership,
  MyInvite,
  Role,
  api,
} from "@/lib/api";

const ROLES: Role[] = ["owner", "admin", "viewer"];
type LeavingRole = "admin" | "viewer" | "revoke";

export default function BotMembersPage() {
  const { bot, currentRole, refreshBot } = useBot();
  const router = useRouter();
  const slug = bot.slug;
  const isOwner = currentRole === "owner";

  const [members, setMembers] = useState<Membership[] | null>(null);
  const [allAdmins, setAllAdmins] = useState<AdminUser[]>([]);
  const [pendingInvites, setPendingInvites] = useState<MyInvite[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [grantOpen, setGrantOpen] = useState(false);
  const [grantAdminId, setGrantAdminId] = useState("");
  const [grantRole, setGrantRole] = useState<Role>("admin");
  const [submitting, setSubmitting] = useState(false);
  const [inlineError, setInlineError] = useState<{ adminId: string; msg: string } | null>(null);
  const [transferOpen, setTransferOpen] = useState(false);
  const [transferAdminId, setTransferAdminId] = useState("");
  const [transferLeavingRole, setTransferLeavingRole] = useState<LeavingRole>("admin");
  const [transferring, setTransferring] = useState(false);
  const [transferError, setTransferError] = useState<string | null>(null);
  // Invite-by-email form (owner-only) — invites people who aren't yet
  // admins. The recipient gets an email and clicks through to
  // /accept-invite.
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("admin");
  const [inviteSubmitting, setInviteSubmitting] = useState(false);
  const [inviteResult, setInviteResult] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listBotAdmins(slug);
      setMembers(list);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load members");
    }
  }, [slug]);

  const refreshInvites = useCallback(async () => {
    try {
      const data = await api.listInvitesForBot(slug);
      setPendingInvites(data);
    } catch {
      setPendingInvites([]);
    }
  }, [slug]);

  useEffect(() => {
    refresh();
    refreshInvites();
  }, [refresh, refreshInvites]);

  useEffect(() => {
    api
      .listAdmins()
      .then((res) => setAllAdmins(res.admins))
      .catch(() => setAllAdmins([]));
  }, []);

  async function onSendInvite(e: FormEvent) {
    e.preventDefault();
    setInviteSubmitting(true);
    setInviteError(null);
    setInviteResult(null);
    try {
      const res = await api.createSpaceInvite({
        email: inviteEmail,
        bot_slug: slug,
        role: inviteRole,
      });
      if (res.invite_url) {
        setInviteResult(
          `Invitation sent to ${inviteEmail}. Share this link if email is delayed: ${res.invite_url}`,
        );
      } else {
        setInviteResult(
          `Invitation created for ${inviteEmail}. Set PUBLIC_BASE_URL on the server to enable email delivery.`,
        );
      }
      setInviteEmail("");
      setInviteRole("admin");
      await refreshInvites();
    } catch (err: any) {
      const detail = err?.detail as { code?: string; role?: string } | undefined;
      if (detail?.code === "already_member") {
        setInviteError(`That email is already a member (${detail.role}).`);
      } else if (detail?.code === "invite_already_pending") {
        setInviteError("That email already has a pending invite for this space.");
      } else {
        setInviteError(err?.message ?? "Failed to send invitation");
      }
    } finally {
      setInviteSubmitting(false);
    }
  }

  async function onRevokeInvite(token: string) {
    if (!confirm("Revoke this pending invitation?")) return;
    try {
      await api.revokeInvite(token);
      await refreshInvites();
    } catch (err: any) {
      setError(err?.message ?? "Failed to revoke");
    }
  }

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

  async function onTransfer() {
    if (!transferAdminId) return;
    setTransferError(null);
    setTransferring(true);
    try {
      const leaving = transferLeavingRole === "revoke" ? null : transferLeavingRole;
      await api.transferBotOwnership(slug, {
        bot_slug: slug,
        new_owner_admin_id: transferAdminId,
        leaving_owner_new_role: leaving,
      });
      setTransferOpen(false);
      setTransferAdminId("");
      setTransferLeavingRole("admin");
      // Refresh both members and the BotShell context — the caller may
      // have lost owner rights, so role-gated controls need to re-render.
      await refresh();
      await refreshBot();
      if (transferLeavingRole === "revoke") {
        router.replace("/bots");
      }
    } catch (err: unknown) {
      setTransferError(err instanceof Error ? err.message : "Transfer failed");
    } finally {
      setTransferring(false);
    }
  }

  return (
    <>
      <div className="header">
        <h1>Members — {bot.name}</h1>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <button className="ghost" onClick={() => { refresh(); refreshInvites(); }}>
            Refresh
          </button>
          {isOwner && (
            <button
              className="ghost"
              onClick={() => setTransferOpen(true)}
              title="Hand off ownership to another member in a single atomic step."
            >
              Transfer ownership…
            </button>
          )}
          {isOwner && (
            <button onClick={() => setGrantOpen(true)} disabled={grantableAdmins.length === 0}>
              Add member
            </button>
          )}
          {isOwner && (
            <button
              onClick={() => setInviteOpen(true)}
              title="Send an invitation email to someone outside the workspace."
            >
              Invite by email
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
        <div className="table-stack"><table>
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
                  <td data-label="Email">{email}</td>
                  <td data-label="Role">
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
                  <td data-label="Added" className="muted" style={{ whiteSpace: "nowrap" }}>
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
        </table></div>
      )}

      {pendingInvites !== null && pendingInvites.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2 style={{ marginTop: 0 }}>Pending invitations</h2>
          <div className="table-stack">
            <table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Sent</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {pendingInvites.map((inv) => (
                  <tr key={inv.token}>
                    <td data-label="Email">{inv.email}</td>
                    <td data-label="Role"><span className="tag">{inv.role}</span></td>
                    <td data-label="Sent" className="muted" style={{ whiteSpace: "nowrap" }}>
                      {new Date(inv.created_at).toLocaleString()}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      {isOwner && (
                        <button className="ghost" onClick={() => onRevokeInvite(inv.token)}>
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <Modal
        open={inviteOpen}
        onClose={() => {
          setInviteOpen(false);
          setInviteResult(null);
          setInviteError(null);
        }}
        title="Invite by email"
        size="sm"
      >
        <form onSubmit={onSendInvite}>
          <div className="modal-body">
            <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
              The recipient receives an invitation email and clicks through to
              accept or decline. They don&apos;t need to be an admin already.
            </p>
            <div className="field">
              <label htmlFor="invite-by-email">Email</label>
              <input
                id="invite-by-email"
                type="email"
                required
                placeholder="someone@example.com"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="invite-by-role">Role</label>
              <select
                id="invite-by-role"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as Role)}
              >
                <option value="admin">admin</option>
                <option value="viewer">viewer</option>
              </select>
            </div>
            {inviteError && <div className="error">{inviteError}</div>}
            {inviteResult && <div className="success">{inviteResult}</div>}
          </div>
          <div className="modal-footer">
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setInviteOpen(false);
                setInviteResult(null);
                setInviteError(null);
              }}
            >
              Close
            </button>
            <button type="submit" disabled={inviteSubmitting || !inviteEmail}>
              {inviteSubmitting ? "Sending…" : "Send invitation"}
            </button>
          </div>
        </form>
      </Modal>

      <Modal
        open={grantOpen}
        onClose={() => setGrantOpen(false)}
        title="Add member"
        size="sm"
      >
        <div className="modal-body">
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
        </div>
        <div className="modal-footer">
          <button className="ghost" onClick={() => setGrantOpen(false)}>
            Cancel
          </button>
          <button onClick={onGrant} disabled={!grantAdminId || submitting}>
            {submitting ? "Granting…" : "Grant"}
          </button>
        </div>
      </Modal>

      <Modal
        open={transferOpen}
        onClose={() => setTransferOpen(false)}
        closeOnBackdrop={!transferring}
        closeOnEsc={!transferring}
        title="Transfer ownership"
        size="md"
      >
        <div className="modal-body">
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Pick the new owner and what should happen to your role. This
            runs as a single transaction — the bot never has zero owners,
            and you don&apos;t need to remember to demote yourself.
          </p>
          <div className="field">
            <label htmlFor="transfer-admin">New owner</label>
            <select
              id="transfer-admin"
              value={transferAdminId}
              onChange={(e) => setTransferAdminId(e.target.value)}
            >
              <option value="">— pick a member —</option>
              {(members ?? [])
                .filter((m) => m.role !== "owner" || m.admin_id !== undefined)
                .map((m) => {
                  const email = adminEmailById.get(m.admin_id) ?? m.admin_id;
                  return (
                    <option key={m.admin_id} value={m.admin_id}>
                      {email} ({m.role})
                    </option>
                  );
                })}
            </select>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              Only existing members are listed. Grant access first if the
              new owner isn&apos;t here.
            </div>
          </div>
          <div className="field">
            <label htmlFor="transfer-leaving">After the transfer, my role becomes</label>
            <select
              id="transfer-leaving"
              value={transferLeavingRole}
              onChange={(e) => setTransferLeavingRole(e.target.value as LeavingRole)}
            >
              <option value="admin">Admin — keep edit access</option>
              <option value="viewer">Viewer — read-only access</option>
              <option value="revoke">Revoke — leave the bot</option>
            </select>
          </div>
          {transferError && <div className="error" role="alert">{transferError}</div>}
        </div>
        <div className="modal-footer">
          <button
            className="ghost"
            onClick={() => setTransferOpen(false)}
            disabled={transferring}
          >
            Cancel
          </button>
          <button onClick={onTransfer} disabled={!transferAdminId || transferring}>
            {transferring ? "Transferring…" : "Transfer ownership"}
          </button>
        </div>
      </Modal>
    </>
  );
}
