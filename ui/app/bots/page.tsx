"use client";

import {
  FormEvent,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Check,
  Copy as CopyIcon,
  LayoutGrid,
  Link2,
  Loader2,
  MoreHorizontal,
  Pin,
  PinOff,
  Plus,
  RefreshCw,
  RotateCcw,
  Settings,
  SlidersHorizontal,
  Table as TableIcon,
  Tag as TagIcon,
  Trash2,
  X,
} from "lucide-react";
import AdminShell from "@/components/AdminShell";
import BotAvatar from "@/components/BotAvatar";
import ChannelChip from "@/components/ChannelChip";
import KpiCard from "@/components/KpiCard";
import Modal from "@/components/Modal";
import StatusPill from "@/components/StatusPill";
import {
  ApiError,
  BotSummary,
  ChannelType,
  api,
} from "@/lib/api";
import { relativeTime } from "@/lib/aggregations";
import {
  readPinnedSlugs,
  togglePinnedSlug,
} from "@/lib/bot-prefs";
import { isValidSlug, slugify } from "@/lib/slugify";

const DEFAULT_TEMPLATE_ID = "support-faq";
const BLANK_TEMPLATE_ID = "blank";

type Template = {
  id: string;
  name: string;
  description: string;
  preview_topics: string[];
};

type Filter = "all" | "live" | "paused" | "pinned";
type ViewMode = "table" | "cards";
const PAGE_SIZE = 20;
const VIEW_KEY = "bf-bots-view";

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

/** Normalized row used by the workspace overview, regardless of whether
 * the data came from `listBotsSummary()` (preferred) or the
 * `listBots()` fallback. */
type Row = {
  id: string;
  slug: string;
  name: string;
  is_active: boolean;
  msgs_24h: number | null;
  active_channels: ChannelType[];
  owner_email: string | null;
  tags: string[];
  /** File count + storage bytes from the workspace summary. null when the
   * summary fetch failed and we fell back to `listBots()` (which doesn't
   * include these fields). */
  file_count: number | null;
  storage_bytes: number | null;
  updated_at: string;
  /** Error count from the server-side validation summary; -1 until the
   * `/bots/validation-summary` fetch resolves. */
  validation_errors: number;
};

type WorkspaceKpis = {
  active: number;
  paused: number;
  totalMessages: number | null;
};

/** Page wrapper. The actual page body lives below so we can read
 * useSearchParams() inside a Suspense boundary (Next 14 requirement for
 * client-side search-param hooks during static export). */
export default function BotsLandingPage() {
  return (
    <AdminShell>
      <Suspense fallback={<p className="muted">Loading…</p>}>
        <BotsLandingBody />
      </Suspense>
    </AdminShell>
  );
}

function BotsLandingBody() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  // null until /me answers; false hides every "create" entry point.
  const [canCreate, setCanCreate] = useState<boolean | null>(null);
  const [duplicateSource, setDuplicateSource] = useState<{ id: string; slug: string; name: string } | null>(null);
  const [undoSlug, setUndoSlug] = useState<string | null>(null);
  const [view, setView] = useState<ViewMode>(() => {
    if (typeof window === "undefined") return "table";
    try {
      return (localStorage.getItem(VIEW_KEY) as ViewMode) || "table";
    } catch {
      return "table";
    }
  });
  // Force cards view below 640px — the 11-column table doesn't fit on a
  // phone. We track viewport width via a media-query listener so users
  // can rotate or resize without re-mounting the page. The persisted
  // preference is preserved (when they widen back, the table returns).
  const [narrow, setNarrow] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(max-width: 640px)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 640px)");
    function update(e: MediaQueryListEvent | MediaQueryList) {
      setNarrow(e.matches);
    }
    update(mq);
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, []);
  const effectiveView: ViewMode = narrow ? "cards" : view;
  const [filter, setFilter] = useState<Filter>(
    () => (searchParams.get("filter") as Filter) || "all",
  );
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [activeTag, setActiveTag] = useState<string | null>(
    () => searchParams.get("tag"),
  );
  const [page, setPage] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [me, setMe] = useState<string | null>(null);
  const [pins, setPins] = useState<string[]>([]);
  // Bulk-action selection — a Set of slugs. Survives filter/search changes
  // so users can stage selections from multiple filtered views before
  // applying. Cleared after a bulk action commits or refresh fires.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<
    "pause" | "unpause" | "delete" | "tag-add" | "tag-remove" | ""
  >("");
  const [bulkTag, setBulkTag] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkResults, setBulkResults] = useState<
    { slug: string; status: string; detail: string | null }[] | null
  >(null);

  // The wizard can be triggered by the command palette via `?new=1`. Open
  // it once on first paint, then strip the param so a refresh doesn't
  // reopen the modal. `?undo=<slug>` is the soft-delete handoff from
  // Settings — it surfaces an undo toast that calls the restore endpoint.
  useEffect(() => {
    const sp = new URLSearchParams(searchParams.toString());
    let dirty = false;
    if (sp.get("new") === "1") {
      setShowCreate(true);
      sp.delete("new");
      dirty = true;
    }
    const undo = sp.get("undo");
    if (undo) {
      setUndoSlug(undo);
      sp.delete("undo");
      dirty = true;
    }
    if (dirty) router.replace(`/bots${sp.toString() ? `?${sp.toString()}` : ""}`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-dismiss the undo toast after 10 seconds — the restore endpoint
  // still works after, but the toast is meant to catch fat-finger deletes
  // immediately. The recently-deleted page (linked from /bots header) is
  // the fallback for older mistakes.
  useEffect(() => {
    if (!undoSlug) return;
    const t = setTimeout(() => setUndoSlug(null), 10_000);
    return () => clearTimeout(t);
  }, [undoSlug]);

  const [welcome, setWelcome] = useState<{ email: string; botCount: number } | null>(null);

  useEffect(() => {
    api
      .me()
      .then((u) => {
        setMe(u.id);
        setCanCreate(u.can_create_bots !== false);
        setPins(readPinnedSlugs(u.id));
        // First-sign-in landing: if we've never marked this admin as
        // welcomed in localStorage and they have at least one membership,
        // show a welcome banner that lists their bots. The banner is
        // dismissible and only fires once per admin per device.
        const seenKey = `bf-welcomed-${u.id}`;
        try {
          const seen = localStorage.getItem(seenKey) === "1";
          if (!seen && u.memberships.length > 0) {
            setWelcome({ email: u.email, botCount: u.memberships.length });
            localStorage.setItem(seenKey, "1");
          }
        } catch {
          /* localStorage may be disabled */
        }
      })
      .catch(() => {
        /* AdminShell handles the auth bounce; ignore here */
      });
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      // Primary path: the summary endpoint. One round-trip for everything
      // we need (msgs_24h, active_channels, owner_email, tags). Falls back
      // to the bare list if the endpoint is missing/broken — older
      // deployments stop showing channel chips & messaging volume but the
      // page still renders.
      let summary: BotSummary[] | null = null;
      try {
        summary = await api.listBotsSummary();
      } catch (err) {
        if (!(err instanceof ApiError) || err.status >= 500) {
          // Don't swallow auth errors — let them bubble.
          if (err instanceof ApiError && err.status === 401) throw err;
        }
        summary = null;
      }
      if (summary) {
        // The summary endpoint omits `id`. We don't need it for routing
        // (everything in this page keys off slug), so synthesize a stable
        // id from the slug to satisfy React's key requirement.
        setRows(
          summary.map((s) => ({
            id: `summary-${s.slug}`,
            slug: s.slug,
            name: s.name,
            is_active: s.is_active,
            msgs_24h: s.msgs_24h,
            active_channels: s.active_channels,
            owner_email: s.owner_email,
            tags: s.tags ?? [],
            file_count: s.file_count,
            storage_bytes: s.storage_bytes,
            updated_at: s.updated_at,
            validation_errors: -1,
          })),
        );
        return;
      }
      const list = await api.listBots();
      setRows(
        list.map((b) => ({
          id: b.id,
          slug: b.slug,
          name: b.name,
          is_active: b.is_active,
          msgs_24h: null,
          active_channels: [],
          owner_email: null,
          tags: b.tags ?? [],
          file_count: null,
          storage_bytes: null,
          updated_at: b.updated_at,
          validation_errors: -1,
        })),
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load bots");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Fetch the workspace-wide validation summary in one round-trip and
  // patch each row with its error count. The endpoint runs the
  // authoritative server validator and caches per `(bot_id,
  // config_version)`, so this is cheap on warm caches and recomputes
  // automatically when any bot's config is saved. A bot whose summary
  // we couldn't load stays at `validation_errors === -1` (icon hidden).
  useEffect(() => {
    if (!rows || rows.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const summary = await api.listBotsValidationSummary();
        if (cancelled) return;
        const bySlug = new Map(summary.map((s) => [s.slug, s.errors]));
        setRows((prev) =>
          prev
            ? prev.map((r) =>
                r.validation_errors === -1 && bySlug.has(r.slug)
                  ? { ...r, validation_errors: bySlug.get(r.slug) ?? 0 }
                  : r,
              )
            : prev,
        );
      } catch {
        // Leave each row at -1 so the icon stays hidden rather than
        // misleading.
      }
    })();
    return () => {
      cancelled = true;
    };
    // We only want to run this when the *set* of rows changes (a new
    // bot appeared / was removed), not on every re-render. Comparing
    // slugs keeps re-runs to actual changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows?.map((r) => r.slug).join("|")]);

  // Keep filter, search, and tag in sync with the URL so admins can
  // bookmark a filtered view (`/bots?filter=live&q=acme&tag=client-foo`).
  useEffect(() => {
    const next = new URLSearchParams();
    if (filter !== "all") next.set("filter", filter);
    if (search.trim()) next.set("q", search.trim());
    if (activeTag) next.set("tag", activeTag);
    const qs = next.toString();
    const target = qs ? `/bots?${qs}` : "/bots";
    if (typeof window !== "undefined") {
      const curr = window.location.pathname + window.location.search;
      if (curr !== target) router.replace(target);
    }
  }, [filter, search, activeTag, router]);

  function setViewPersistent(next: ViewMode) {
    setView(next);
    try {
      localStorage.setItem(VIEW_KEY, next);
    } catch {
      /* localStorage may be disabled */
    }
  }

  function togglePin(slug: string) {
    setPins(togglePinnedSlug(me, slug));
  }

  // Workspace KPI strip — totals over whatever we have. With the summary
  // endpoint live `totalMessages` reflects the last 24h (cached server-side
  // for 60s). Without it we leave the field blank rather than fabricating.
  const kpis = useMemo<WorkspaceKpis | null>(() => {
    if (!rows) return null;
    const active = rows.filter((r) => r.is_active).length;
    const paused = rows.length - active;
    let totalMessages: number | null = 0;
    let any = false;
    for (const r of rows) {
      if (r.msgs_24h !== null) {
        totalMessages! += r.msgs_24h;
        any = true;
      }
    }
    if (!any) totalMessages = null;
    return { active, paused, totalMessages };
  }, [rows]);

  // Distinct tags + counts, used to render the tag filter chip strip.
  const distinctTags = useMemo(() => {
    if (!rows) return [] as { tag: string; count: number }[];
    const counts = new Map<string, number>();
    for (const r of rows) {
      for (const t of r.tags) {
        counts.set(t, (counts.get(t) ?? 0) + 1);
      }
    }
    return [...counts.entries()]
      .map(([tag, count]) => ({ tag, count }))
      .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
  }, [rows]);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = search.trim().toLowerCase();
    return rows
      .filter((r) => {
        if (filter === "live" && !r.is_active) return false;
        if (filter === "paused" && r.is_active) return false;
        if (filter === "pinned" && !pins.includes(r.slug)) return false;
        if (activeTag && !r.tags.includes(activeTag)) return false;
        if (!q) return true;
        return (
          r.name.toLowerCase().includes(q) ||
          r.slug.toLowerCase().includes(q) ||
          r.tags.some((t) => t.toLowerCase().includes(q))
        );
      })
      .slice()
      .sort((a, b) => {
        const aPin = pins.includes(a.slug) ? 0 : 1;
        const bPin = pins.includes(b.slug) ? 0 : 1;
        if (aPin !== bPin) return aPin - bPin;
        return a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0;
      });
  }, [rows, search, filter, pins, activeTag]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const visiblePage = Math.min(page, totalPages - 1);
  const pageRows = filtered.slice(
    visiblePage * PAGE_SIZE,
    visiblePage * PAGE_SIZE + PAGE_SIZE,
  );

  const isMulti = (rows?.length ?? 0) > 1;
  const heading = isMulti ? "Overview" : "Bots";
  const subtitle = rows
    ? `${rows.length} ${rows.length === 1 ? "bot" : "bots"}` +
      (rows.length > 0
        ? ` · ${rows.filter((r) => r.is_active).length} active · ${rows.filter((r) => !r.is_active).length} paused`
        : "")
    : null;

  async function onUndoDelete() {
    if (!undoSlug) return;
    const slug = undoSlug;
    setUndoSlug(null);
    try {
      await api.restoreBot(slug);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Restore failed");
    }
  }

  async function openDuplicate(slug: string) {
    try {
      const fetched = await api.getBot(slug);
      setDuplicateSource({ id: fetched.id, slug: fetched.slug, name: fetched.name });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lookup failed");
    }
  }

  async function onDeleteBot(slug: string) {
    if (!confirm(`Soft-delete "${slug}"? Restore is available for 7 days.`)) return;
    try {
      await api.deleteBot(slug);
      setUndoSlug(slug);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  function toggleSelected(slug: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function clearSelection() {
    setSelected(new Set());
    setBulkResults(null);
  }

  async function runBulkAction() {
    if (selected.size === 0 || !bulkAction) return;
    if (bulkAction === "delete") {
      if (!confirm(`Soft-delete ${selected.size} bot${selected.size > 1 ? "s" : ""}? Restore is available for 7 days.`)) return;
    }
    if ((bulkAction === "tag-add" || bulkAction === "tag-remove") && !bulkTag.trim()) {
      setError("Tag is required for tag actions.");
      return;
    }
    setBulkBusy(true);
    setBulkResults(null);
    setError(null);
    try {
      const res = await api.bulkBotAction({
        action: bulkAction,
        slugs: [...selected],
        tag: bulkTag.trim() || undefined,
      });
      setBulkResults(res.results);
      // Drop only the slugs that succeeded — failed rows stay selected
      // so the admin can retry without re-checking each box.
      const succeeded = new Set(res.results.filter((r) => r.status === "ok").map((r) => r.slug));
      setSelected((prev) => new Set([...prev].filter((s) => !succeeded.has(s))));
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Bulk action failed");
    } finally {
      setBulkBusy(false);
    }
  }

  // Persist tag edits. Optimistic — mirror locally first, send to backend,
  // roll back on failure. Tags don't affect the runtime so a few seconds
  // of staleness is fine if the request lands eventually.
  async function patchTags(slug: string, nextTags: string[]) {
    const original = rows;
    setRows((prev) =>
      prev ? prev.map((r) => (r.slug === slug ? { ...r, tags: nextTags } : r)) : prev,
    );
    try {
      await api.patchBot(slug, { tags: nextTags });
    } catch (err) {
      setRows(original);
      throw err;
    }
  }

  return (
    <>
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
          <button
            className="ghost compact"
            onClick={() => router.push("/bots/deleted")}
            title="Recently deleted bots"
          >
            <Trash2 size={14} strokeWidth={1.75} />
            Recently deleted
          </button>
          {canCreate !== false && (
            <button onClick={() => setShowCreate(true)}>
              <Plus strokeWidth={1.75} />
              Create bot
            </button>
          )}
        </div>
      </div>

      {welcome && (
        <div
          className="banner advisory"
          role="status"
          style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}
        >
          <strong>Welcome, {welcome.email}.</strong>
          <span style={{ flex: 1, minWidth: 200 }}>
            You have access to {welcome.botCount} bot
            {welcome.botCount === 1 ? "" : "s"}. Pick one below to start, or
            create your own.
          </span>
          <button
            type="button"
            className="ghost compact"
            onClick={() => setWelcome(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {rows === null && !error && <p className="muted">Loading…</p>}

      {rows && rows.length === 0 && (
        <div className="card narrow" style={{ textAlign: "center", padding: 32 }}>
          <h2 style={{ marginTop: 0 }}>No bots yet</h2>
          <p className="muted">
            You don&apos;t have access to any bots. Create one from a template
            to get started, or ask a teammate to grant you membership on an
            existing bot.
          </p>
          <p className="muted" style={{ fontSize: 13, marginTop: 12 }}>
            Just got granted access? Your sign-in token is stale — sign in
            again and the new bots will appear once the refreshed token lands.
          </p>
          <div
            className="row"
            style={{
              justifyContent: "center",
              flexWrap: "wrap",
              marginTop: 16,
              gap: 8,
            }}
          >
            {canCreate !== false && (
              <button onClick={() => setShowCreate(true)}>
                <Plus strokeWidth={1.75} />
                Create your first bot
              </button>
            )}
            <button
              className="ghost"
              onClick={() => {
                // Bounce through OIDC to mint a fresh token. The IdP
                // returns us to /bots; the visible cookie is refreshed and
                // ``listBots()`` will return any newly-granted bots.
                window.location.href = "/api/admin/auth/login?return_to=/bots";
              }}
              title="Bounces through the IdP to mint a fresh token"
            >
              Sign in again to load new bots
            </button>
          </div>
        </div>
      )}

      {rows && rows.length > 0 && (
        <>
          <div className="kpi-grid" style={{ marginBottom: 16 }}>
            <KpiCard
              label="Bots"
              value={kpis ? kpis.active.toString() : "—"}
              unit={kpis ? `/ ${kpis.active + kpis.paused}` : undefined}
              footnote={kpis && kpis.paused > 0 ? `${kpis.paused} paused` : undefined}
            />
            <KpiCard
              label="Messages (24h)"
              value={
                kpis?.totalMessages !== null && kpis
                  ? kpis.totalMessages.toLocaleString()
                  : "—"
              }
              footnote={
                kpis?.totalMessages === null
                  ? "Summary endpoint unavailable"
                  : "Across all bots"
              }
            />
            <KpiCard
              label="Pinned"
              value={pins.length.toString()}
              footnote={pins.length === 0 ? "Pin from the row menu" : undefined}
            />
            <KpiCard
              label="Tags"
              value={distinctTags.length.toString()}
              footnote={
                distinctTags.length === 0 ? "Tag bots from the row menu" : undefined
              }
            />
          </div>

          {effectiveView === "cards" ? (
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
                rows={rows}
                pins={pins}
                distinctTags={distinctTags}
                activeTag={activeTag}
                onTagFilterChange={(t) => {
                  setActiveTag(t);
                  setPage(0);
                }}
                forceCards={narrow}
              />
              <div className="bot-grid">
                {filtered.map((r) => (
                  <div key={r.id} className="bot-card">
                    {/* Overlay link covers the card surface — clicking
                      * anywhere in the card navigates, but the absolutely
                      * positioned pin toggle below sits above the overlay
                      * (z-index) so it stays its own interactive surface.
                      * Avoids the previous nested-button invalid markup. */}
                    <a
                      href={`/bots/${r.slug}/dashboard`}
                      className="bot-card-link"
                      aria-label={`Open ${r.name}`}
                      onClick={(e) => {
                        // SPA navigation rather than a full page reload.
                        e.preventDefault();
                        router.push(`/bots/${r.slug}/dashboard`);
                      }}
                    />
                    <div className="row" style={{ gap: 10, alignItems: "center" }}>
                      <BotAvatar slug={r.slug} name={r.name} size="md" />
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="bot-card-name">{r.name}</div>
                        <div
                          className="muted"
                          style={{ fontSize: 12, fontFamily: "var(--font-mono)" }}
                        >
                          {r.slug}
                        </div>
                      </div>
                      <span className="bot-card-pin">
                        <PinToggle
                          pinned={pins.includes(r.slug)}
                          onToggle={() => togglePin(r.slug)}
                        />
                      </span>
                    </div>
                    <div
                      style={{
                        marginTop: 12,
                        display: "flex",
                        gap: 8,
                        alignItems: "center",
                        flexWrap: "wrap",
                      }}
                    >
                      <StatusPill variant={r.is_active ? "success" : "neutral"}>
                        {r.is_active ? "Live" : "Paused"}
                      </StatusPill>
                      {r.validation_errors > 0 && (
                        <span
                          className="health-bad"
                          title={`${r.validation_errors} config error${r.validation_errors > 1 ? "s" : ""}`}
                        >
                          <AlertTriangle size={12} strokeWidth={1.75} aria-hidden="true" />
                          {r.validation_errors}
                        </span>
                      )}
                      <span className="muted" style={{ fontSize: 12 }}>
                        {relativeTime(r.updated_at)}
                      </span>
                    </div>
                    {r.tags.length > 0 && (
                      <div className="tag-row" style={{ marginTop: 8 }}>
                        {r.tags.map((t) => (
                          <span key={t} className="tag">
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
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
                rows={rows}
                pins={pins}
                distinctTags={distinctTags}
                activeTag={activeTag}
                onTagFilterChange={(t) => {
                  setActiveTag(t);
                  setPage(0);
                }}
                forceCards={narrow}
              />
              {selected.size > 0 && (
                <BulkActionBar
                  count={selected.size}
                  action={bulkAction}
                  onActionChange={setBulkAction}
                  tag={bulkTag}
                  onTagChange={setBulkTag}
                  busy={bulkBusy}
                  onApply={runBulkAction}
                  onClear={clearSelection}
                  results={bulkResults}
                />
              )}
              <div className="table-scroll"><table className="table">
                <thead>
                  <tr>
                    <th style={{ width: 28 }} aria-label="Select">
                      <input
                        type="checkbox"
                        aria-label="Select all on this page"
                        checked={
                          pageRows.length > 0 &&
                          pageRows.every((r) => selected.has(r.slug))
                        }
                        onChange={(e) => {
                          setSelected((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) {
                              for (const r of pageRows) next.add(r.slug);
                            } else {
                              for (const r of pageRows) next.delete(r.slug);
                            }
                            return next;
                          });
                        }}
                      />
                    </th>
                    <th style={{ width: 28 }} aria-label="Pin"></th>
                    <th>Bot</th>
                    <th>Status</th>
                    <th>Channels</th>
                    <th>Owner</th>
                    <th>Tags</th>
                    <th className="col-right">Msgs (24h)</th>
                    <th className="col-right">Storage</th>
                    <th>Updated</th>
                    <th style={{ width: 36 }} aria-label="Actions"></th>
                  </tr>
                </thead>
                <tbody>
                  {pageRows.map((r) => (
                    <tr
                      key={r.id}
                      className="row-link"
                      tabIndex={0}
                      role="link"
                      aria-label={`Open ${r.name}`}
                      style={{ cursor: "pointer" }}
                      onClick={(e) => {
                        // Bail out if the click landed on (or inside) an
                        // interactive control — checkbox, pin button, tag
                        // editor, overflow menu. The row should only
                        // navigate when the user clicked an "inert" cell.
                        const target = e.target as HTMLElement;
                        if (
                          target.closest(
                            "input, button, select, textarea, a, [role='menuitem'], [role='menu'], [contenteditable]",
                          )
                        ) {
                          return;
                        }
                        router.push(`/bots/${r.slug}/dashboard`);
                      }}
                      onKeyDown={(e) => {
                        if (e.key !== "Enter" && e.key !== " ") return;
                        // Same guard as click — pressing Enter while focus is
                        // inside the row's checkbox/menu should not navigate.
                        const target = e.target as HTMLElement;
                        if (target !== e.currentTarget) return;
                        e.preventDefault();
                        router.push(`/bots/${r.slug}/dashboard`);
                      }}
                    >
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select ${r.name}`}
                          checked={selected.has(r.slug)}
                          onChange={() => toggleSelected(r.slug)}
                        />
                      </td>
                      <td>
                        <PinToggle
                          pinned={pins.includes(r.slug)}
                          onToggle={() => togglePin(r.slug)}
                        />
                      </td>
                      <td>
                        <div className="bot-cell">
                          <BotAvatar slug={r.slug} name={r.name} size="md" />
                          <div className="bot-info">
                            <div className="bot-name">
                              {r.name}
                              {r.validation_errors > 0 && (
                                <span
                                  className="health-bad inline"
                                  title={`${r.validation_errors} config error${r.validation_errors > 1 ? "s" : ""}`}
                                  style={{ marginLeft: 6 }}
                                >
                                  <AlertTriangle size={11} strokeWidth={1.75} aria-hidden="true" />
                                </span>
                              )}
                            </div>
                            <div className="bot-id">{r.slug}</div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <StatusPill variant={r.is_active ? "success" : "neutral"}>
                          {r.is_active ? "Live" : "Paused"}
                        </StatusPill>
                      </td>
                      <td>
                        {r.active_channels.length === 0 ? (
                          <span className="muted">—</span>
                        ) : (
                          <span className="row" style={{ gap: 4 }}>
                            {r.active_channels.map((c) => (
                              <ChannelChip key={c} kind={c} />
                            ))}
                          </span>
                        )}
                      </td>
                      <td className="muted" style={{ fontSize: 12 }}>
                        {r.owner_email ?? "—"}
                      </td>
                      <td>
                        <TagEditor
                          tags={r.tags}
                          onSave={(next) => patchTags(r.slug, next)}
                        />
                      </td>
                      <td className="col-right" style={{ fontVariantNumeric: "tabular-nums" }}>
                        {r.msgs_24h === null ? (
                          <span className="muted">—</span>
                        ) : (
                          r.msgs_24h.toLocaleString()
                        )}
                      </td>
                      <td
                        className="col-right"
                        style={{ fontVariantNumeric: "tabular-nums" }}
                        title={
                          r.file_count != null && r.file_count > 0
                            ? `${r.file_count} file${r.file_count === 1 ? "" : "s"}`
                            : undefined
                        }
                      >
                        {r.storage_bytes === null ? (
                          <span className="muted">—</span>
                        ) : r.storage_bytes === 0 ? (
                          <span className="muted">empty</span>
                        ) : (
                          formatBytes(r.storage_bytes)
                        )}
                      </td>
                      <td
                        className="muted"
                        style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                      >
                        {relativeTime(r.updated_at)}
                      </td>
                      <td>
                        <RowActionsMenu
                          onConfig={() => router.push(`/bots/${r.slug}/config`)}
                          onSettings={() => router.push(`/bots/${r.slug}/settings`)}
                          onDuplicate={canCreate !== false ? () => openDuplicate(r.slug) : undefined}
                          onDelete={() => onDeleteBot(r.slug)}
                        />
                      </td>
                    </tr>
                  ))}
                  {pageRows.length === 0 && (
                    <tr>
                      <td
                        colSpan={11}
                        style={{
                          textAlign: "center",
                          padding: 24,
                          color: "var(--muted)",
                        }}
                      >
                        No bots match this filter.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table></div>
              {filtered.length > PAGE_SIZE && (
                <div
                  className="pagination"
                  style={{
                    padding: "10px 14px",
                    borderTop: "1px solid var(--border)",
                    margin: 0,
                  }}
                >
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

      {showCreate && canCreate === false && (
        <div className="banner warning" role="status">
          Your role doesn&apos;t allow creating bots. Ask a workspace
          superadmin, or an owner of a bot you work on, for access.{" "}
          <button className="ghost compact" onClick={() => setShowCreate(false)}>
            Dismiss
          </button>
        </div>
      )}

      {showCreate && canCreate !== false && (
        <CreateBotWizard
          onClose={() => setShowCreate(false)}
          onCreated={(slug, templateId) => {
            router.push(`/bots/${slug}/setup?template=${encodeURIComponent(templateId)}`);
          }}
        />
      )}

      {duplicateSource && canCreate !== false && (
        <DuplicateBotModal
          source={duplicateSource}
          onClose={() => setDuplicateSource(null)}
          onCreated={(slug) => router.push(`/bots/${slug}/dashboard`)}
        />
      )}

      {undoSlug && (
        <UndoDeleteToast
          slug={undoSlug}
          onUndo={onUndoDelete}
          onDismiss={() => setUndoSlug(null)}
        />
      )}
    </>
  );
}

/** Sticky strip shown above the bots table whenever 1+ rows are selected.
 * Drives a single ``api.bulkBotAction`` round-trip and renders the
 * per-row result trail so partial failures (e.g. last-owner protection
 * on a delete) are visible without re-clicking each row. */
type BulkAction = "pause" | "unpause" | "delete" | "tag-add" | "tag-remove" | "";
function BulkActionBar({
  count,
  action,
  onActionChange,
  tag,
  onTagChange,
  busy,
  onApply,
  onClear,
  results,
}: {
  count: number;
  action: BulkAction;
  onActionChange: (a: BulkAction) => void;
  tag: string;
  onTagChange: (t: string) => void;
  busy: boolean;
  onApply: () => void;
  onClear: () => void;
  results: { slug: string; status: string; detail: string | null }[] | null;
}) {
  const needsTag = action === "tag-add" || action === "tag-remove";
  return (
    <div
      style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--panel-border)",
        background: "var(--paper)",
        display: "flex",
        gap: 10,
        alignItems: "center",
        flexWrap: "wrap",
      }}
    >
      <strong>
        {count} selected
      </strong>
      <select
        value={action}
        onChange={(e) => onActionChange(e.target.value as BulkAction)}
        disabled={busy}
        style={{ padding: "6px 8px" }}
      >
        <option value="">— pick an action —</option>
        <option value="pause">Pause</option>
        <option value="unpause">Unpause</option>
        <option value="tag-add">Add tag…</option>
        <option value="tag-remove">Remove tag…</option>
        <option value="delete">Delete (soft, 7-day restore)</option>
      </select>
      {needsTag && (
        <input
          type="text"
          placeholder="tag value"
          value={tag}
          onChange={(e) => onTagChange(e.target.value)}
          disabled={busy}
          style={{ padding: "6px 8px", width: 180 }}
        />
      )}
      <button
        type="button"
        onClick={onApply}
        disabled={busy || !action || (needsTag && !tag.trim())}
        className={action === "delete" ? "danger" : ""}
      >
        {busy ? "Working…" : `Apply to ${count}`}
      </button>
      <button type="button" className="ghost compact" onClick={onClear} disabled={busy}>
        Clear
      </button>
      {results && (
        <div
          role="status"
          aria-live="polite"
          style={{
            flex: "1 1 100%",
            marginTop: 6,
            fontSize: 12,
            color: "var(--muted)",
          }}
        >
          {results.filter((r) => r.status === "ok").length} ok ·{" "}
          {results.filter((r) => r.status === "skipped").length} skipped ·{" "}
          {results.filter((r) => r.status === "error").length} error
          {results.filter((r) => r.status === "error").length > 0 && (
            <span style={{ marginLeft: 12, color: "var(--error)" }}>
              Errors:{" "}
              {results
                .filter((r) => r.status === "error")
                .map((r) => `${r.slug} (${r.detail ?? "failed"})`)
                .join(", ")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** Per-row overflow menu. Surfaces the four quick actions (Config, Settings,
 * Duplicate, Delete) so admins don't have to enter the bot to manage it. */
function RowActionsMenu({
  onConfig,
  onSettings,
  onDuplicate,
  onDelete,
}: {
  onConfig: () => void;
  onSettings: () => void;
  /** Omitted when the admin may not create bots (BOT_CREATION). */
  onDuplicate?: () => void;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDown(e: PointerEvent) {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  function closeAndRun(fn: () => void) {
    setOpen(false);
    fn();
  }
  const itemStyle: React.CSSProperties = {
    width: "100%",
    justifyContent: "flex-start",
  };
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        className="ghost compact"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More actions"
        onClick={() => setOpen((v) => !v)}
        title="More actions"
      >
        <MoreHorizontal size={14} strokeWidth={1.75} />
      </button>
      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "100%",
            marginTop: 4,
            background: "var(--paper)",
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
            minWidth: 180,
            zIndex: 30,
            padding: 4,
            boxShadow: "0 8px 24px rgba(0,0,0,0.15)",
          }}
        >
          <button
            type="button"
            role="menuitem"
            className="ghost"
            style={itemStyle}
            onClick={() => closeAndRun(onConfig)}
          >
            <SlidersHorizontal size={13} strokeWidth={1.75} /> Config
          </button>
          <button
            type="button"
            role="menuitem"
            className="ghost"
            style={itemStyle}
            onClick={() => closeAndRun(onSettings)}
          >
            <Settings size={13} strokeWidth={1.75} /> Settings
          </button>
          {onDuplicate && (
            <button
              type="button"
              role="menuitem"
              className="ghost"
              style={itemStyle}
              onClick={() => onDuplicate && closeAndRun(onDuplicate)}
            >
              <CopyIcon size={13} strokeWidth={1.75} /> Duplicate…
            </button>
          )}
          <div
            aria-hidden
            style={{
              height: 1,
              background: "var(--panel-border)",
              margin: "4px 0",
            }}
          />
          <button
            type="button"
            role="menuitem"
            className="ghost"
            style={{
              ...itemStyle,
              borderColor: "var(--error)",
              color: "var(--error)",
            }}
            onClick={() => closeAndRun(onDelete)}
          >
            <Trash2 size={13} strokeWidth={1.75} /> Delete
          </button>
        </div>
      )}
    </div>
  );
}

/** Bottom-left toast offering an Undo after a soft-delete. The toast
 * itself only lives for ~10s; longer recovery is via /bots/deleted. */
function UndoDeleteToast({
  slug,
  onUndo,
  onDismiss,
}: {
  slug: string;
  onUndo: () => void;
  onDismiss: () => void;
}) {
  return (
    <div
      role="status"
      style={{
        position: "fixed",
        bottom: 24,
        left: 24,
        background: "var(--paper)",
        border: "1px solid var(--panel-border)",
        borderRadius: 8,
        padding: "12px 16px",
        display: "flex",
        gap: 12,
        alignItems: "center",
        zIndex: 60,
        boxShadow: "0 8px 24px rgba(0,0,0,0.2)",
        maxWidth: 480,
      }}
    >
      <Trash2 size={16} strokeWidth={1.75} aria-hidden />
      <span>
        Deleted <code style={{ fontFamily: "var(--font-mono)" }}>{slug}</code>.
      </span>
      <button type="button" onClick={onUndo}>
        <RotateCcw size={13} strokeWidth={1.75} /> Undo
      </button>
      <button
        type="button"
        className="ghost compact"
        aria-label="Dismiss"
        onClick={onDismiss}
      >
        <X size={14} strokeWidth={1.75} />
      </button>
    </div>
  );
}

/** Inline-name + slug-only duplicate flow. We skip template choice because
 * the source bot's config is the template. Files/vectors are not copied —
 * v1 contract is "config only" so an admin who needs the knowledge base
 * re-uploads. */
function DuplicateBotModal({
  source,
  onClose,
  onCreated,
}: {
  source: { id: string; slug: string; name: string };
  onClose: () => void;
  onCreated: (slug: string) => void;
}) {
  const [name, setName] = useState(`${source.name} (copy)`);
  const [slug, setSlug] = useState(suggestSlug(source.slug));
  const [slugLinked, setSlugLinked] = useState(true);
  const [slugCheck, setSlugCheck] = useState<SlugCheck>({ status: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slug) {
      setSlugCheck({ status: "idle" });
      return;
    }
    if (!isValidSlug(slug)) {
      setSlugCheck({ status: "invalid" });
      return;
    }
    setSlugCheck({ status: "checking" });
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .checkBotSlugAvailable(slug)
        .then((res) => {
          if (cancelled) return;
          if (res.available) setSlugCheck({ status: "available" });
          else if (res.reason === "taken") setSlugCheck({ status: "taken" });
          else setSlugCheck({ status: "invalid" });
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setSlugCheck({
            status: "error",
            message: err instanceof Error ? err.message : "Check failed",
          });
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [slug]);

  function onNameChange(next: string) {
    setName(next);
    if (slugLinked) setSlug(slugify(next));
  }

  const submitDisabled =
    submitting ||
    !name.trim() ||
    !slug ||
    slugCheck.status === "invalid" ||
    slugCheck.status === "taken" ||
    slugCheck.status === "checking";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!isValidSlug(slug)) {
      setError("Slug must be lowercase alphanumeric with optional hyphens (2–64 chars).");
      return;
    }
    setSubmitting(true);
    try {
      const created = await api.createBot({
        slug,
        name: name.trim(),
        source_bot_id: source.id,
      });
      onCreated(created.slug);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setSlugCheck({ status: "taken" });
      } else {
        setError(err instanceof Error ? err.message : "Duplicate failed");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Duplicate bot"
      size="md"
      closeOnBackdrop={!submitting}
      closeOnEsc={!submitting}
    >
      <form onSubmit={onSubmit}>
        <div className="modal-body">
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Cloning configuration from <strong>{source.name}</strong> (
            <code style={{ fontFamily: "var(--font-mono)" }}>{source.slug}</code>).
            Files and knowledge are <strong>not</strong> copied — re-upload them
            after creation.
          </p>

          <div className="field">
            <label htmlFor="dup-name">Display name</label>
            <input
              id="dup-name"
              type="text"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              required
            />
          </div>

          <div className="field">
            <label>Slug</label>
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              <input
                type="text"
                value={slug}
                onChange={(e) => {
                  setSlugLinked(false);
                  setSlug(e.target.value.toLowerCase());
                }}
                placeholder="acme-support-copy"
                style={{ flex: 1, fontFamily: "var(--font-mono)" }}
                aria-invalid={slugCheck.status === "invalid" || slugCheck.status === "taken"}
              />
              <SlugCheckIcon check={slugCheck} />
            </div>
            {slugCheck.status === "taken" && (
              <div className="error" role="alert" style={{ marginTop: 4, fontSize: 12 }}>
                Slug <code>{slug}</code> is already in use.
              </div>
            )}
            {slugCheck.status === "invalid" && (
              <div className="error" role="alert" style={{ marginTop: 4, fontSize: 12 }}>
                Lowercase letters, digits, and hyphens. Must be 2–64 characters and cannot
                start or end with a hyphen.
              </div>
            )}
          </div>

          {error && <div className="error" role="alert">{error}</div>}
        </div>

        <div className="modal-footer">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={submitDisabled}>
            {submitting ? "Duplicating…" : "Duplicate bot"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function suggestSlug(sourceSlug: string): string {
  // Append `-copy`; truncate if it would breach the 64-char regex limit.
  const max = 64;
  const suffix = "-copy";
  if (sourceSlug.length + suffix.length <= max) return `${sourceSlug}${suffix}`;
  return `${sourceSlug.slice(0, max - suffix.length)}${suffix}`;
}

type ToolbarProps = {
  view: ViewMode;
  onViewChange: (v: ViewMode) => void;
  filter: Filter;
  onFilterChange: (f: Filter) => void;
  search: string;
  onSearchChange: (s: string) => void;
  rows: Row[];
  pins: string[];
  distinctTags: { tag: string; count: number }[];
  activeTag: string | null;
  onTagFilterChange: (t: string | null) => void;
  /** When true the view toggle hides — the page is forcing cards below
   * 640px and toggling has no effect, so the chrome stays clean. */
  forceCards?: boolean;
};

function BotsToolbar({
  view,
  onViewChange,
  filter,
  onFilterChange,
  search,
  onSearchChange,
  rows,
  pins,
  distinctTags,
  activeTag,
  onTagFilterChange,
  forceCards,
}: ToolbarProps) {
  const counts = {
    all: rows.length,
    live: rows.filter((r) => r.is_active).length,
    paused: rows.filter((r) => !r.is_active).length,
    pinned: rows.filter((r) => pins.includes(r.slug)).length,
  };
  return (
    <>
      <div className="toolbar">
        <input
          type="search"
          placeholder="Search bots…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
        />
        <FilterChip label="All" active={filter === "all"} count={counts.all} onClick={() => onFilterChange("all")} />
        <FilterChip label="Live" active={filter === "live"} count={counts.live} onClick={() => onFilterChange("live")} />
        <FilterChip label="Paused" active={filter === "paused"} count={counts.paused} onClick={() => onFilterChange("paused")} />
        <FilterChip label="Pinned" active={filter === "pinned"} count={counts.pinned} onClick={() => onFilterChange("pinned")} />
        <div className="toolbar-spacer" />
        {!forceCards && (
          <div className="seg" role="tablist" aria-label="View">
            <button
              className={view === "cards" ? "active" : ""}
              aria-pressed={view === "cards"}
              onClick={() => onViewChange("cards")}
              title="Card view"
            >
              <LayoutGrid size={12} strokeWidth={1.75} aria-hidden="true" />
              Cards
            </button>
            <button
              className={view === "table" ? "active" : ""}
              aria-pressed={view === "table"}
              onClick={() => onViewChange("table")}
              title="Table view"
            >
              <TableIcon size={12} strokeWidth={1.75} aria-hidden="true" />
              Table
            </button>
          </div>
        )}
      </div>
      {distinctTags.length > 0 && (
        <div className="tag-filter-strip">
          <span className="muted" style={{ fontSize: 11, marginRight: 6 }}>
            Tags
          </span>
          <FilterChip
            label="Any"
            active={activeTag === null}
            count={undefined}
            onClick={() => onTagFilterChange(null)}
          />
          {distinctTags.map(({ tag, count }) => (
            <FilterChip
              key={tag}
              label={tag}
              active={activeTag === tag}
              count={count}
              onClick={() => onTagFilterChange(activeTag === tag ? null : tag)}
            />
          ))}
        </div>
      )}
    </>
  );
}

function FilterChip({
  label,
  active,
  count,
  onClick,
}: {
  label: string;
  active: boolean;
  count?: number;
  onClick: () => void;
}) {
  return (
    <span
      className={active ? "filter-chip active" : "filter-chip"}
      onClick={onClick}
    >
      {label}
      {count !== undefined && <span className="count">{count}</span>}
    </span>
  );
}

function PinToggle({
  pinned,
  onToggle,
}: {
  pinned: boolean;
  /** Called when the user activates the toggle. Receiving the event is
   * optional; callers that need it (e.g. inline `stopPropagation`) may
   * still attach. */
  onToggle: (e: React.MouseEvent) => void;
}) {
  return (
    <button
      type="button"
      className="pin-toggle"
      aria-pressed={pinned}
      aria-label={pinned ? "Unpin bot" : "Pin bot"}
      title={pinned ? "Unpin" : "Pin to top"}
      onClick={onToggle}
    >
      {pinned ? (
        <Pin size={13} strokeWidth={1.75} aria-hidden="true" />
      ) : (
        <PinOff size={13} strokeWidth={1.75} aria-hidden="true" />
      )}
    </button>
  );
}

/** Inline tag editor — a popover with chip rendering, deletion, and a
 * text input that splits on comma/enter. Optimistic updates are handled by
 * the page; we just call `onSave` with the next list. */
function TagEditor({
  tags,
  onSave,
}: {
  tags: string[];
  onSave: (next: string[]) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(tags);
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setDraft(tags);
  }, [tags, open]);

  useEffect(() => {
    if (!open) return;
    function onDown(e: PointerEvent) {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) commit();
    }
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, draft]);

  function pushDraft(raw: string) {
    const parts = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    setDraft((prev) => {
      const seen = new Set(prev);
      const next = [...prev];
      for (const p of parts) {
        if (!seen.has(p)) {
          next.push(p);
          seen.add(p);
        }
      }
      return next;
    });
    setInput("");
  }

  async function commit() {
    if (!open) return;
    setOpen(false);
    if (sameSet(draft, tags)) return;
    setSaving(true);
    setErr(null);
    try {
      await onSave(draft);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Save failed");
      setDraft(tags);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="tag-cell" ref={wrapperRef}>
      <button
        type="button"
        className="tag-cell-trigger"
        onClick={() => setOpen((v) => !v)}
        title="Edit tags"
      >
        {tags.length === 0 ? (
          <span className="muted">
            <TagIcon size={11} strokeWidth={1.75} /> Add
          </span>
        ) : (
          <span className="tag-row">
            {tags.map((t) => (
              <span key={t} className="tag">
                {t}
              </span>
            ))}
          </span>
        )}
      </button>
      {saving && (
        <Loader2
          size={11}
          strokeWidth={1.75}
          className="spin"
          style={{ marginLeft: 4 }}
        />
      )}
      {err && (
        <span className="error" style={{ fontSize: 11, marginLeft: 4 }}>
          {err}
        </span>
      )}
      {open && (
        <div className="tag-cell-popover">
          <div className="tag-row" style={{ marginBottom: 8 }}>
            {draft.map((t) => (
              <span key={t} className="tag">
                {t}
                <button
                  type="button"
                  className="tag-remove"
                  aria-label={`Remove tag ${t}`}
                  onClick={() => setDraft(draft.filter((x) => x !== t))}
                >
                  <X size={10} strokeWidth={2} />
                </button>
              </span>
            ))}
            {draft.length === 0 && (
              <span className="muted" style={{ fontSize: 12 }}>
                No tags
              </span>
            )}
          </div>
          <input
            type="text"
            autoFocus
            placeholder="Add a tag — press Enter"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === ",") {
                e.preventDefault();
                pushDraft(input);
              }
              if (e.key === "Escape") commit();
            }}
            onBlur={() => pushDraft(input)}
            style={{ width: "100%" }}
          />
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
            <button type="button" className="ghost compact" onClick={() => commit()}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function sameSet(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = new Set(a);
  for (const x of b) if (!sa.has(x)) return false;
  return true;
}

/** Inline slug availability state, updated by a debounced backend probe. */
type SlugCheck =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "available" }
  | { status: "invalid" }
  | { status: "taken" }
  | { status: "error"; message: string };

function CreateBotWizard({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (slug: string, templateId: string) => void;
}) {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  // Workspace-saved templates surface after the code-baked ones. We load
  // them in parallel — failure is silently empty rather than fatal.
  const [workspaceTemplates, setWorkspaceTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState<string>(DEFAULT_TEMPLATE_ID);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  // Slug stays linked to the name (auto-derive on every keystroke) until
  // the admin clicks the Edit slug toggle. Once unlinked we never re-sync
  // — that's the "don't silently overwrite their typing" rule from §1.
  const [slugLinked, setSlugLinked] = useState(true);
  const [slugCheck, setSlugCheck] = useState<SlugCheck>({ status: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    api
      .listBotTemplates()
      .then(setTemplates)
      .catch((err) =>
        setTemplatesError(err instanceof Error ? err.message : "Failed to load templates")
      );
    api
      .listWorkspaceTemplates()
      .then((list) =>
        setWorkspaceTemplates(
          list.map((t) => ({
            id: `workspace:${t.id}`,
            name: t.name,
            description: t.description ?? "",
            preview_topics: [],
          })),
        ),
      )
      .catch(() => setWorkspaceTemplates([]));
  }, []);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!slug) {
      setSlugCheck({ status: "idle" });
      return;
    }
    if (!isValidSlug(slug)) {
      setSlugCheck({ status: "invalid" });
      return;
    }
    setSlugCheck({ status: "checking" });
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .checkBotSlugAvailable(slug)
        .then((res) => {
          if (cancelled) return;
          if (res.available) setSlugCheck({ status: "available" });
          else if (res.reason === "taken") setSlugCheck({ status: "taken" });
          else setSlugCheck({ status: "invalid" });
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setSlugCheck({
            status: "error",
            message: err instanceof Error ? err.message : "Check failed",
          });
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [slug]);

  function onNameChange(next: string) {
    setName(next);
    if (slugLinked) {
      setSlug(slugify(next));
    }
  }

  function onSlugChange(next: string) {
    setSlugLinked(false);
    setSlug(next.toLowerCase());
  }

  function relinkSlug() {
    setSlugLinked(true);
    setSlug(slugify(name));
  }

  const visibleTemplates = useMemo(() => {
    if (!templates) return null;
    if (showAdvanced) return templates;
    return templates.filter((t) => t.id !== BLANK_TEMPLATE_ID);
  }, [templates, showAdvanced]);

  const submitDisabled =
    submitting ||
    !name.trim() ||
    !slug ||
    slugCheck.status === "invalid" ||
    slugCheck.status === "taken" ||
    slugCheck.status === "checking";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    if (!isValidSlug(slug)) {
      setError(
        "Slug must be lowercase alphanumeric with optional hyphens (2–64 chars, cannot start/end with hyphen)."
      );
      return;
    }
    setSubmitting(true);
    try {
      const created = await api.createBot({
        slug,
        name: name.trim(),
        template_id: templateId,
      });
      onCreated(created.slug, templateId);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setSlugCheck({ status: "taken" });
      } else {
        setError(err instanceof Error ? err.message : "Create failed");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Create bot"
      size="md"
      closeOnBackdrop={!submitting}
      closeOnEsc={!submitting}
      initialFocusRef={nameRef}
    >
      <form onSubmit={onSubmit}>
        <div className="modal-body">
          <div className="field">
            <label htmlFor="bot-name">Display name</label>
            <input
              id="bot-name"
              ref={nameRef}
              type="text"
              placeholder="Acme Support Bot"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              required
            />
            <SlugRow
              slug={slug}
              linked={slugLinked}
              check={slugCheck}
              onChange={onSlugChange}
              onRelink={relinkSlug}
            />
          </div>

          <div className="field">
            <label>Starting template</label>
            {templatesError && <div className="error" role="alert">{templatesError}</div>}
            {!templates && !templatesError && (
              <p className="muted" style={{ fontSize: 13 }}>
                Loading templates…
              </p>
            )}
            {visibleTemplates && (
              <div style={{ display: "grid", gap: 8 }}>
                {visibleTemplates.map((t) => (
                  <TemplateRadio
                    key={t.id}
                    template={t}
                    selected={templateId === t.id}
                    onSelect={() => setTemplateId(t.id)}
                  />
                ))}
              </div>
            )}
            {workspaceTemplates.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                  Workspace templates
                </div>
                <div style={{ display: "grid", gap: 8 }}>
                  {workspaceTemplates.map((t) => (
                    <TemplateRadio
                      key={t.id}
                      template={t}
                      selected={templateId === t.id}
                      onSelect={() => setTemplateId(t.id)}
                    />
                  ))}
                </div>
              </div>
            )}
            {templates && !showAdvanced && templates.some((t) => t.id === BLANK_TEMPLATE_ID) && (
              <button
                type="button"
                className="ghost"
                onClick={() => setShowAdvanced(true)}
                style={{ padding: "4px 8px", fontSize: 12, marginTop: 6 }}
              >
                Advanced — start from empty config
              </button>
            )}
          </div>

          {error && <div className="error" role="alert">{error}</div>}
        </div>

        <div className="modal-footer">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={submitDisabled}>
            {submitting ? "Creating…" : "Create bot"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SlugRow({
  slug,
  linked,
  check,
  onChange,
  onRelink,
}: {
  slug: string;
  linked: boolean;
  check: SlugCheck;
  onChange: (next: string) => void;
  onRelink: () => void;
}) {
  return (
    <div style={{ marginTop: 8 }}>
      <div
        className="row"
        style={{ gap: 8, alignItems: "center", fontSize: 13 }}
      >
        <span className="muted" style={{ fontSize: 12 }}>
          Slug
        </span>
        {linked ? (
          <>
            <code
              style={{
                flex: 1,
                fontFamily: "var(--font-mono)",
                fontSize: 13,
                color: slug ? "var(--text)" : "var(--muted)",
              }}
            >
              {slug || "auto-generated from name"}
            </code>
            <button
              type="button"
              className="ghost"
              onClick={() => onChange(slug)}
              style={{ padding: "2px 8px", fontSize: 12 }}
              title="Type your own slug"
            >
              Edit slug
            </button>
          </>
        ) : (
          <>
            <input
              type="text"
              value={slug}
              onChange={(e) => onChange(e.target.value)}
              placeholder="acme-support"
              style={{ flex: 1, fontFamily: "var(--font-mono)" }}
              aria-invalid={check.status === "invalid" || check.status === "taken"}
            />
            <button
              type="button"
              className="ghost"
              onClick={onRelink}
              style={{ padding: "2px 8px", fontSize: 12 }}
              title="Re-link to the display name"
            >
              <Link2 size={12} strokeWidth={1.75} /> Re-link
            </button>
          </>
        )}
        <SlugCheckIcon check={check} />
      </div>
      <SlugCheckMessage check={check} slug={slug} />
    </div>
  );
}

function SlugCheckIcon({ check }: { check: SlugCheck }) {
  if (check.status === "checking") {
    return <Loader2 size={14} strokeWidth={1.75} className="spin" aria-label="Checking…" />;
  }
  if (check.status === "available") {
    return (
      <Check
        size={14}
        strokeWidth={2}
        style={{ color: "var(--success)" }}
        aria-label="Slug available"
      />
    );
  }
  if (check.status === "taken" || check.status === "invalid") {
    return (
      <X
        size={14}
        strokeWidth={2}
        style={{ color: "var(--error)" }}
        aria-label="Slug unavailable"
      />
    );
  }
  return null;
}

function SlugCheckMessage({ check, slug }: { check: SlugCheck; slug: string }) {
  // The status flips while the user types — announce changes politely so a
  // screen-reader user hears "taken" / "available" without re-reading the
  // field. The wrapping span owns the live region so re-renders update
  // the same node rather than mounting fresh ones.
  let content: React.ReactNode;
  let cls = "muted";
  if (check.status === "taken") {
    content = (
      <>
        Slug <code>{slug}</code> is already in use.
      </>
    );
    cls = "error";
  } else if (check.status === "invalid") {
    content =
      "Lowercase letters, digits, and hyphens. Must be 2–64 characters and cannot start or end with a hyphen.";
    cls = "error";
  } else if (check.status === "error") {
    content = `Couldn’t check availability (${check.message}). You can still try Create.`;
    cls = "error";
  } else {
    content = "URL-safe identifier — not currently editable after create.";
  }
  return (
    <div
      className={cls}
      role="status"
      aria-live="polite"
      style={{ marginTop: 4, fontSize: 12 }}
    >
      {content}
    </div>
  );
}

function TemplateRadio({
  template,
  selected,
  onSelect,
}: {
  template: Template;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <label
      style={{
        display: "flex",
        gap: 12,
        padding: 12,
        borderRadius: 6,
        border: `1px solid ${selected ? "var(--accent)" : "var(--panel-border)"}`,
        background: selected ? "var(--panel-hover, var(--paper))" : "transparent",
        cursor: "pointer",
      }}
    >
      <input
        type="radio"
        name="template"
        value={template.id}
        checked={selected}
        onChange={onSelect}
        style={{ marginTop: 3 }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>{template.name}</div>
        <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
          {template.description}
        </div>
        {template.preview_topics.length > 0 && (
          <div style={{ marginTop: 6 }}>
            {template.preview_topics.map((topic) => (
              <span
                key={topic}
                className="tag"
                style={{ marginRight: 6, fontSize: 11 }}
              >
                {topic}
              </span>
            ))}
          </div>
        )}
      </div>
    </label>
  );
}
