"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { LayoutGrid, Plus, RefreshCw, Table as TableIcon, X } from "lucide-react";
import AdminShell from "@/components/AdminShell";
import BotAvatar from "@/components/BotAvatar";
import KpiCard from "@/components/KpiCard";
import StatusPill from "@/components/StatusPill";
import { ApiError, Bot, api } from "@/lib/api";
import { relativeTime } from "@/lib/aggregations";

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/;

type Template = {
  id: string;
  name: string;
  description: string;
  preview_topics: string[];
};

type Filter = "all" | "live" | "paused";
type ViewMode = "table" | "cards";
const PAGE_SIZE = 20;
const VIEW_KEY = "bf-bots-view";

/** Aggregated workspace KPIs — populated by paralleling `dashboard/stats`
 * across every bot the admin can see. Null until the first call resolves. */
type WorkspaceKpis = {
  active: number;
  paused: number;
  totalMessages: number | null;
  totalEndUsers: number | null;
  totalFiles: number | null;
};

export default function BotsLandingPage() {
  const router = useRouter();
  const [bots, setBots] = useState<Bot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [view, setView] = useState<ViewMode>(() => {
    if (typeof window === "undefined") return "table";
    try {
      return (localStorage.getItem(VIEW_KEY) as ViewMode) || "table";
    } catch {
      return "table";
    }
  });
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [kpis, setKpis] = useState<WorkspaceKpis | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const list = await api.listBots();
      setBots(list);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load bots");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Aggregate workspace KPIs by paralleling `dashboard/stats` per bot.
  // With small workspaces (<30 bots) this completes in a few hundred ms;
  // failures per bot are silently skipped so a single down bot doesn't
  // wipe the strip.
  useEffect(() => {
    if (!bots) return;
    if (bots.length === 0) {
      setKpis({ active: 0, paused: 0, totalMessages: 0, totalEndUsers: 0, totalFiles: 0 });
      return;
    }
    let cancelled = false;
    (async () => {
      const active = bots.filter((b) => b.is_active).length;
      const paused = bots.length - active;
      // Set partial KPIs immediately so the strip isn't blank.
      setKpis({
        active,
        paused,
        totalMessages: null,
        totalEndUsers: null,
        totalFiles: null,
      });
      const results = await Promise.allSettled(
        bots.map((b) => api.botDashboardStats(b.slug)),
      );
      if (cancelled) return;
      let totalMessages = 0;
      let totalEndUsers = 0;
      let totalFiles = 0;
      let any = false;
      for (const r of results) {
        if (r.status !== "fulfilled") continue;
        any = true;
        totalMessages += r.value.message_count ?? 0;
        totalEndUsers += r.value.end_user_count ?? 0;
        totalFiles += r.value.file_count ?? 0;
      }
      setKpis({
        active,
        paused,
        totalMessages: any ? totalMessages : null,
        totalEndUsers: any ? totalEndUsers : null,
        totalFiles: any ? totalFiles : null,
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [bots]);

  function setViewPersistent(next: ViewMode) {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      /* localStorage may be disabled */
    }
  }

  // Apply search + filter on the bots list. Sort by updated_at desc so the
  // most recently touched bot is on top.
  const filtered = useMemo(() => {
    if (!bots) return [];
    const q = search.trim().toLowerCase();
    return bots
      .filter((b) => {
        if (filter === "live" && !b.is_active) return false;
        if (filter === "paused" && b.is_active) return false;
        if (!q) return true;
        return b.name.toLowerCase().includes(q) || b.slug.toLowerCase().includes(q);
      })
      .slice()
      .sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
  }, [bots, search, filter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const visiblePage = Math.min(page, totalPages - 1);
  const pageRows = filtered.slice(
    visiblePage * PAGE_SIZE,
    visiblePage * PAGE_SIZE + PAGE_SIZE,
  );

  // Page title flips between "Overview" (multi-bot admin) and "Bots"
  // (single bot). Subtitle restates the cardinality.
  const isMulti = (bots?.length ?? 0) > 1;
  const heading = isMulti ? "Overview" : "Bots";
  const subtitle = bots
    ? `${bots.length} ${bots.length === 1 ? "bot" : "bots"}` +
      (bots.length > 0
        ? ` · ${bots.filter((b) => b.is_active).length} active · ${bots.filter((b) => !b.is_active).length} paused`
        : "")
    : null;

  return (
    <AdminShell>
      <div className="header">
        <div>
          <h1>{heading}</h1>
          {subtitle && (
            <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
              {subtitle}
            </div>
          )}
        </div>
        <div className="row">
          <button className="ghost compact" onClick={refresh} disabled={refreshing}>
            <RefreshCw
              size={14}
              strokeWidth={1.75}
              className={refreshing ? "spin" : undefined}
            />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <button onClick={() => setShowCreate(true)}>
            <Plus strokeWidth={1.75} />
            Create bot
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {bots === null && !error && <p className="muted">Loading…</p>}

      {bots && bots.length === 0 && (
        <div className="card narrow" style={{ textAlign: "center", padding: 32 }}>
          <h2 style={{ marginTop: 0 }}>No bots yet</h2>
          <p className="muted">
            You don&apos;t have access to any bots. Create one from a template
            to get started, or ask a teammate to grant you membership on an
            existing bot.
          </p>
          <p className="muted" style={{ fontSize: 13, marginTop: 12 }}>
            Just got granted access? Your sign-in token is stale — sign in again
            to refresh permissions.
          </p>
          <button onClick={() => setShowCreate(true)} style={{ marginTop: 16 }}>
            <Plus strokeWidth={1.75} />
            Create your first bot
          </button>
        </div>
      )}

      {bots && bots.length > 0 && (
        <>
          {/* Workspace KPI strip. Lifetime totals only — no time-window
              filtering (we don't have a backend endpoint for that yet). */}
          <div className="kpi-grid" style={{ marginBottom: 16 }}>
            <KpiCard
              label="Bots"
              value={kpis ? kpis.active.toString() : "—"}
              unit={kpis ? `/ ${kpis.active + kpis.paused}` : undefined}
              footnote={kpis && kpis.paused > 0 ? `${kpis.paused} paused` : undefined}
            />
            <KpiCard
              label="Total messages"
              value={kpis?.totalMessages !== null && kpis ? kpis.totalMessages.toLocaleString() : "—"}
              footnote={kpis?.totalMessages === null ? "Aggregating…" : undefined}
            />
            <KpiCard
              label="Total end users"
              value={kpis?.totalEndUsers !== null && kpis ? kpis.totalEndUsers.toLocaleString() : "—"}
              footnote={kpis?.totalEndUsers === null ? "Aggregating…" : undefined}
            />
            <KpiCard
              label="Total files"
              value={kpis?.totalFiles !== null && kpis ? kpis.totalFiles.toLocaleString() : "—"}
              footnote={kpis?.totalFiles === null ? "Aggregating…" : undefined}
            />
          </div>

          {view === "cards" ? (
            <>
              <BotsToolbar
                view={view}
                onViewChange={setViewPersistent}
                filter={filter}
                onFilterChange={(f) => {
                  setFilter(f);
                  setPage(0);
                }}
                search={search}
                onSearchChange={(s) => {
                  setSearch(s);
                  setPage(0);
                }}
                bots={bots}
              />
              <div className="bot-grid">
                {filtered.map((b) => (
                  <button
                    key={b.id}
                    className="bot-card"
                    onClick={() => router.push(`/bots/${b.slug}/dashboard`)}
                  >
                    <div className="row" style={{ gap: 10, alignItems: "center" }}>
                      <BotAvatar slug={b.slug} name={b.name} size="md" />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="bot-card-name">{b.name}</div>
                        <div className="muted" style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}>
                          {b.slug}
                        </div>
                      </div>
                    </div>
                    <div style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center" }}>
                      <StatusPill variant={b.is_active ? "success" : "neutral"}>
                        {b.is_active ? "Live" : "Paused"}
                      </StatusPill>
                      <span className="muted" style={{ fontSize: 12 }}>
                        {relativeTime(b.updated_at)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <div className="card" style={{ padding: 0 }}>
              <BotsToolbar
                view={view}
                onViewChange={setViewPersistent}
                filter={filter}
                onFilterChange={(f) => {
                  setFilter(f);
                  setPage(0);
                }}
                search={search}
                onSearchChange={(s) => {
                  setSearch(s);
                  setPage(0);
                }}
                bots={bots}
              />
              <table className="table">
                <thead>
                  <tr>
                    <th>Bot</th>
                    <th>Status</th>
                    <th>Channels</th>
                    <th>Model</th>
                    <th className="col-right">Msgs (24h)</th>
                    <th className="col-right">CSAT</th>
                    <th className="col-right">p95</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((b) => (
                    <tr
                      key={b.id}
                      style={{ cursor: "pointer" }}
                      onClick={() => router.push(`/bots/${b.slug}/dashboard`)}
                    >
                      <td>
                        <div className="bot-cell">
                          <BotAvatar slug={b.slug} name={b.name} size="md" />
                          <div className="bot-info">
                            <div className="bot-name">{b.name}</div>
                            <div className="bot-id">{b.slug}</div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <StatusPill variant={b.is_active ? "success" : "neutral"}>
                          {b.is_active ? "Live" : "Paused"}
                        </StatusPill>
                      </td>
                      {/* Channels / Model / Msgs (24h) / CSAT / p95 are
                          deliberately stubbed `—`. Backend doesn't expose
                          them today; see ui-overhaul.md "Out of scope". */}
                      <td className="muted">—</td>
                      <td className="muted">—</td>
                      <td className="col-right muted">—</td>
                      <td className="col-right muted">—</td>
                      <td className="col-right muted">—</td>
                      <td className="muted" style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
                        {relativeTime(b.updated_at)}
                      </td>
                    </tr>
                  ))}
                  {pageRows.length === 0 && (
                    <tr>
                      <td colSpan={8} style={{ textAlign: "center", padding: 24, color: "var(--muted)" }}>
                        No bots match this filter.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {filtered.length > PAGE_SIZE && (
                <div className="pagination" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", margin: 0 }}>
                  <span className="muted">
                    Showing {visiblePage * PAGE_SIZE + 1}–
                    {Math.min((visiblePage + 1) * PAGE_SIZE, filtered.length)} of{" "}
                    {filtered.length}
                  </span>
                  <button
                    className="ghost compact"
                    disabled={visiblePage === 0}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                  >
                    Prev
                  </button>
                  <button
                    className="ghost compact"
                    disabled={visiblePage >= totalPages - 1}
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  >
                    Next
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}

      {showCreate && (
        <CreateBotWizard
          onClose={() => setShowCreate(false)}
          onCreated={(slug) => {
            router.push(`/bots/${slug}/config`);
          }}
        />
      )}
    </AdminShell>
  );
}

type ToolbarProps = {
  view: ViewMode;
  onViewChange: (v: ViewMode) => void;
  filter: Filter;
  onFilterChange: (f: Filter) => void;
  search: string;
  onSearchChange: (s: string) => void;
  bots: Bot[];
};

function BotsToolbar({
  view,
  onViewChange,
  filter,
  onFilterChange,
  search,
  onSearchChange,
  bots,
}: ToolbarProps) {
  const counts = {
    all: bots.length,
    live: bots.filter((b) => b.is_active).length,
    paused: bots.filter((b) => !b.is_active).length,
  };
  return (
    <div className="toolbar">
      <input
        type="search"
        placeholder="Search bots…"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
      />
      <span
        className={filter === "all" ? "filter-chip active" : "filter-chip"}
        onClick={() => onFilterChange("all")}
      >
        All <span className="count">{counts.all}</span>
      </span>
      <span
        className={filter === "live" ? "filter-chip active" : "filter-chip"}
        onClick={() => onFilterChange("live")}
      >
        Live <span className="count">{counts.live}</span>
      </span>
      <span
        className={filter === "paused" ? "filter-chip active" : "filter-chip"}
        onClick={() => onFilterChange("paused")}
      >
        Paused <span className="count">{counts.paused}</span>
      </span>
      <div className="toolbar-spacer" />
      {/* View toggle: cards / table. Persists to localStorage. */}
      <div className="seg" role="tablist" aria-label="View">
        <button
          className={view === "cards" ? "active" : ""}
          aria-pressed={view === "cards"}
          onClick={() => onViewChange("cards")}
          title="Card view"
        >
          <LayoutGrid size={12} strokeWidth={1.75} />
          Cards
        </button>
        <button
          className={view === "table" ? "active" : ""}
          aria-pressed={view === "table"}
          onClick={() => onViewChange("table")}
          title="Table view"
        >
          <TableIcon size={12} strokeWidth={1.75} />
          Table
        </button>
      </div>
    </div>
  );
}

function CreateBotWizard({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (slug: string) => void;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [chosenTemplate, setChosenTemplate] = useState<string | null>(null);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listBotTemplates()
      .then(setTemplates)
      .catch((err) =>
        setTemplatesError(err instanceof Error ? err.message : "Failed to load templates")
      );
  }, []);

  function pick(id: string) {
    setChosenTemplate(id);
    setStep(2);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!chosenTemplate) {
      setError("Pick a template first.");
      setStep(1);
      return;
    }
    if (!SLUG_RE.test(slug)) {
      setError(
        "Slug must be lowercase alphanumeric with optional hyphens (2–64 chars, cannot start/end with hyphen)."
      );
      return;
    }
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    setSubmitting(true);
    try {
      const created = await api.createBot({
        slug,
        name: name.trim(),
        template_id: chosenTemplate,
      });
      onCreated(created.slug);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setError("Slug already in use. Pick a different one.");
      } else {
        setError(err instanceof Error ? err.message : "Create failed");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-bot-title"
      onClick={onClose}
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
        className="card"
        style={{ minWidth: 480, maxWidth: 720, maxHeight: "90vh", overflow: "auto" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 id="create-bot-title" style={{ marginTop: 0 }}>
            Create bot — step {step} of 2
          </h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            <X strokeWidth={1.75} />
          </button>
        </div>

        {step === 1 && (
          <>
            <p className="muted">Pick a starting template. You can edit everything later.</p>
            {templatesError && <div className="error">{templatesError}</div>}
            {!templates && !templatesError && <p className="muted">Loading templates…</p>}
            {templates && (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 8 }}>
                {templates.map((t) => (
                  <button
                    key={t.id}
                    className="bot-card"
                    onClick={() => pick(t.id)}
                    style={{ padding: 16 }}
                  >
                    <div className="bot-card-name">{t.name}</div>
                    <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
                      {t.description}
                    </div>
                    {t.preview_topics.length > 0 && (
                      <div style={{ marginTop: 10 }}>
                        {t.preview_topics.map((topic) => (
                          <span key={topic} className="tag" style={{ marginRight: 6 }}>
                            {topic}
                          </span>
                        ))}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {step === 2 && (
          <form onSubmit={onSubmit}>
            <p className="muted">
              Template: <strong>{templates?.find((t) => t.id === chosenTemplate)?.name ?? chosenTemplate}</strong>{" "}
              <button type="button" className="ghost" onClick={() => setStep(1)} style={{ padding: "2px 8px", fontSize: 12 }}>
                Change
              </button>
            </p>
            <div className="field">
              <label htmlFor="bot-slug">Slug (URL identifier)</label>
              <input
                id="bot-slug"
                type="text"
                autoFocus
                placeholder="acme-support"
                value={slug}
                onChange={(e) => setSlug(e.target.value.toLowerCase())}
                required
              />
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                Lowercase letters, digits, and hyphens. Used in URLs — choose carefully, slugs are hard to change.
              </div>
            </div>
            <div className="field">
              <label htmlFor="bot-name">Display name</label>
              <input
                id="bot-name"
                type="text"
                placeholder="Acme Support Bot"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            {error && <div className="error">{error}</div>}
            <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
              <button type="button" className="ghost" onClick={() => setStep(1)}>
                Back
              </button>
              <button type="submit" disabled={submitting}>
                {submitting ? "Creating…" : "Create"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
