"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";
import AdminShell from "@/components/AdminShell";
import { AdminUser, Role, api } from "@/lib/api";

type Row = {
  bot_id: string;
  bot_slug: string;
  bot_name: string;
  role: Role | null;
};

const ROLE_OPTIONS: { value: Role | "none"; label: string }[] = [
  { value: "none", label: "No access" },
  { value: "viewer", label: "Viewer" },
  { value: "admin", label: "Admin" },
  { value: "owner", label: "Owner" },
];

/**
 * Cross-bot membership matrix for one admin. Rows are bots; the role
 * picker per row replaces the per-bot Members tab dance for granting
 * access to N bots at once. All edits stage locally and commit in a
 * single bulk PUT — partial failures (last-owner protection, etc.) are
 * surfaced inline per row.
 */
export default function AdminMembershipMatrixPage() {
  const pathname = usePathname();
  const router = useRouter();
  // Pathname looks like /admins/<id>; the second segment is the admin id.
  // Static-export gotcha matches bots/[slug]: useParams() returns the
  // placeholder, so we read directly from pathname.
  const adminId = (pathname?.split("/")[2] ?? "").trim();

  const [admin, setAdmin] = useState<AdminUser | null>(null);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Local overlay of role picks keyed by bot_slug. ``undefined`` means
  // "use the value from rows"; the user can revert by clearing.
  const [draft, setDraft] = useState<Record<string, Role | null>>({});
  const [saving, setSaving] = useState(false);
  const [perRowStatus, setPerRowStatus] = useState<
    Record<string, { status: string; detail: string | null }>
  >({});
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "members" | "non-members">("all");

  const refresh = useCallback(async () => {
    if (!adminId || adminId === "_") return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.getAdminMemberships(adminId);
      setAdmin(res.admin);
      setRows(res.rows);
      setDraft({});
      setPerRowStatus({});
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load memberships");
    } finally {
      setLoading(false);
    }
  }, [adminId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function setRole(slug: string, role: Role | null, current: Role | null) {
    setDraft((prev) => {
      const next = { ...prev };
      if (role === current) {
        delete next[slug];
      } else {
        next[slug] = role;
      }
      return next;
    });
  }

  const changed = useMemo(() => Object.keys(draft).length, [draft]);

  async function commit() {
    if (!rows || changed === 0) return;
    setSaving(true);
    setError(null);
    const assignments = Object.entries(draft).map(([bot_slug, role]) => ({
      bot_slug,
      role,
    }));
    try {
      const res = await api.bulkUpdateAdminMemberships(adminId, assignments);
      const statusMap: Record<string, { status: string; detail: string | null }> = {};
      for (const r of res.results) {
        statusMap[r.bot_slug] = { status: r.status, detail: r.detail };
      }
      setPerRowStatus(statusMap);
      // Re-fetch so the row's "current" role reflects what landed; any
      // failed assignment stays in the matrix as a pending edit so the
      // user can adjust (or revert) without losing the others.
      const fresh = await api.getAdminMemberships(adminId);
      setRows(fresh.rows);
      const stillPending: Record<string, Role | null> = {};
      for (const r of res.results) {
        if (r.status === "error") {
          stillPending[r.bot_slug] = draft[r.bot_slug] ?? null;
        }
      }
      setDraft(stillPending);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const filteredRows = useMemo(() => {
    if (!rows) return [];
    const q = search.trim().toLowerCase();
    return rows.filter((r) => {
      const desired = draft[r.bot_slug] !== undefined ? draft[r.bot_slug] : r.role;
      if (filter === "members" && !desired) return false;
      if (filter === "non-members" && desired) return false;
      if (!q) return true;
      return (
        r.bot_name.toLowerCase().includes(q) ||
        r.bot_slug.toLowerCase().includes(q)
      );
    });
  }, [rows, search, filter, draft]);

  return (
    <AdminShell>
      <div className="header">
        <div>
          <div style={{ marginBottom: 4 }}>
            <Link
              href="/admins"
              style={{
                display: "inline-flex",
                gap: 4,
                alignItems: "center",
                fontSize: 12,
                color: "var(--muted)",
              }}
            >
              <ArrowLeft size={12} strokeWidth={1.75} />
              Admins
            </Link>
          </div>
          <h1>{admin ? admin.email : "Memberships"}</h1>
          {admin && (
            <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
              {admin.is_superadmin && (
                <span className="tag" style={{ marginRight: 8 }}>superadmin</span>
              )}
              {admin.is_active ? "Active" : "Disabled"} ·{" "}
              {admin.last_login_at
                ? `Last sign-in ${new Date(admin.last_login_at).toLocaleString()}`
                : "never signed in"}
            </div>
          )}
        </div>
        <div className="row" style={{ gap: 6 }}>
          <button className="ghost" onClick={refresh} disabled={loading || saving}>
            Reload
          </button>
          <button
            onClick={commit}
            disabled={saving || changed === 0}
            title={
              changed === 0
                ? "Make changes in the role pickers below first."
                : `Apply ${changed} pending change${changed > 1 ? "s" : ""}`
            }
          >
            {saving ? "Saving…" : changed === 0 ? "No changes" : `Apply ${changed} change${changed > 1 ? "s" : ""}`}
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : !rows ? (
        <p className="muted">No data.</p>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <div
            className="toolbar"
            style={{
              gap: 8,
              padding: "10px 14px",
              borderBottom: "1px solid var(--panel-border)",
            }}
          >
            <input
              type="search"
              aria-label="Search bots"
              placeholder="Search bots…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ flex: 1 }}
            />
            <div className="seg" role="tablist" aria-label="Filter">
              {(["all", "members", "non-members"] as const).map((opt) => (
                <button
                  key={opt}
                  role="tab"
                  className={filter === opt ? "active" : ""}
                  aria-selected={filter === opt}
                  onClick={() => setFilter(opt)}
                >
                  {opt === "all" ? "All" : opt === "members" ? "Members" : "Non-members"}
                </button>
              ))}
            </div>
          </div>
          <div className="table-scroll"><table className="table">
            <thead>
              <tr>
                <th>Bot</th>
                <th>Current role</th>
                <th>Set to</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((r) => {
                const pending = draft[r.bot_slug] !== undefined;
                const desired = pending ? draft[r.bot_slug] : r.role;
                const rowStatus = perRowStatus[r.bot_slug];
                return (
                  <tr key={r.bot_id}>
                    <td>
                      <Link
                        href={`/bots/${r.bot_slug}/dashboard`}
                        className="bot-cell"
                        style={{ textDecoration: "none", color: "inherit" }}
                      >
                        <div>
                          <div style={{ fontWeight: 500 }}>{r.bot_name}</div>
                          <div className="muted" style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
                            {r.bot_slug}
                          </div>
                        </div>
                      </Link>
                    </td>
                    <td>
                      {r.role ? (
                        <span className="tag">{r.role}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <select
                        value={desired ?? "none"}
                        onChange={(e) => {
                          const v = e.target.value;
                          setRole(r.bot_slug, v === "none" ? null : (v as Role), r.role);
                        }}
                        style={{
                          borderColor: pending ? "var(--accent)" : undefined,
                        }}
                      >
                        {ROLE_OPTIONS.map((opt) => (
                          <option key={opt.value} value={opt.value}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                      {pending && (
                        <button
                          type="button"
                          className="ghost compact"
                          style={{ marginLeft: 6 }}
                          onClick={() => {
                            setDraft((prev) => {
                              const next = { ...prev };
                              delete next[r.bot_slug];
                              return next;
                            });
                          }}
                        >
                          Revert
                        </button>
                      )}
                    </td>
                    <td style={{ fontSize: 12 }}>
                      {saving && pending && <Loader2 size={12} className="spin" />}
                      {rowStatus && rowStatus.status === "error" && (
                        <span className="error">
                          {rowStatus.detail === "last_owner_protected"
                            ? "Last owner — promote another owner first"
                            : rowStatus.detail ?? "Failed"}
                        </span>
                      )}
                      {rowStatus &&
                        rowStatus.status !== "error" &&
                        rowStatus.status !== "unchanged" && (
                          <span className="success">{rowStatus.status}</span>
                        )}
                    </td>
                  </tr>
                );
              })}
              {filteredRows.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ textAlign: "center", padding: 24, color: "var(--muted)" }}>
                    No bots match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table></div>
        </div>
      )}
    </AdminShell>
  );
}
