"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { api, type Bot, type SessionRow } from "@/lib/api";
import ConversationsList from "./ConversationsList";
import ConversationThread from "./ConversationThread";
import ConversationMeta from "./ConversationMeta";
import ConversationsFilterBar, {
  filtersFromUrlParams,
  filtersToQuery,
  filtersToUrlParams,
  type ConversationFilters,
} from "./ConversationsFilterBar";
import { fromSessionRow, withDetail, type ConversationSession } from "./types";

const PAGE_SIZE = 50;

type ConversationsSplitProps = {
  bot: Bot;
};

function parsePageParam(raw: string | null): number {
  const n = raw ? parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n - 1 : 0;
}

/**
 * Top-level shell for the 3-pane conversation view. Owns:
 *   - filter state (server-side via the sessions endpoint)
 *   - page state (skip/limit pagination, 50 per page)
 *   - lazy-load of the active session's full thread (separate fetch)
 *   - selection state
 *
 * The split itself is CSS-driven (`.conv-split`); responsive collapse to
 * 2-col / 1-col happens in globals.css media queries.
 */
export default function ConversationsSplit({ bot }: ConversationsSplitProps) {
  // Initial state is hydrated from the URL so deep-linked / shared filter
  // URLs work. URL writes only happen in response to user actions
  // (`updateFilters` / `goToPage`) — never inside a useEffect that
  // depends on `pathname`, because that effect would fire mid-navigation
  // when the user clicks a sidebar link and rewrite the URL on the
  // way out.
  const pathname = usePathname();
  const initialSearchParams = useSearchParams();
  const [filters, setFilters] = useState<ConversationFilters>(() =>
    filtersFromUrlParams(initialSearchParams),
  );
  const [page, setPage] = useState<number>(() =>
    parsePageParam(initialSearchParams.get("page")),
  );
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeDetail, setActiveDetail] = useState<ConversationSession | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const filterQuery = useMemo(() => filtersToQuery(filters), [filters]);

  const writeUrl = useCallback(
    (nextFilters: ConversationFilters, nextPage: number) => {
      if (typeof window === "undefined") return;
      const params = new URLSearchParams(filtersToUrlParams(nextFilters));
      if (nextPage > 0) params.set("page", String(nextPage + 1));
      const qs = params.toString();
      window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
    },
    [pathname],
  );

  const updateFilters = useCallback(
    (next: ConversationFilters) => {
      setFilters(next);
      setPage(0);
      setActiveSessionId(null);
      setActiveDetail(null);
      writeUrl(next, 0);
    },
    [writeUrl],
  );

  const goToPage = useCallback(
    (next: number) => {
      setPage(next);
      writeUrl(filters, next);
    },
    [filters, writeUrl],
  );

  // Reset to page 0 when the bot itself changes so we don't show another
  // bot's data. Skip on first render so a deep-linked `?page=3` survives.
  const firstBotRender = useRef(true);
  useEffect(() => {
    if (firstBotRender.current) {
      firstBotRender.current = false;
      return;
    }
    setPage(0);
    setActiveSessionId(null);
    setActiveDetail(null);
  }, [bot.slug]);

  // List fetch — reruns when bot, filters, or page changes.
  const fetchSeq = useRef(0);
  const refresh = useCallback(async () => {
    const seq = ++fetchSeq.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.listBotSessions(bot.slug, {
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        ...filterQuery,
      });
      if (seq !== fetchSeq.current) return;
      setRows(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      if (seq !== fetchSeq.current) return;
      setError(err instanceof Error ? err.message : "Failed to load conversations");
    } finally {
      if (seq === fetchSeq.current) setLoading(false);
    }
  }, [bot.slug, page, filterQuery]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const sessions = useMemo(() => rows.map(fromSessionRow), [rows]);

  // Auto-select the first row when none is active. Don't override a manual
  // click — only fire when there's no current selection.
  useEffect(() => {
    if (activeSessionId) return;
    if (sessions.length > 0) setActiveSessionId(sessions[0].sessionId);
  }, [sessions, activeSessionId]);

  // Lazy-fetch the full thread for the active session.
  const detailSeq = useRef(0);
  useEffect(() => {
    if (!activeSessionId) {
      setActiveDetail(null);
      return;
    }
    const base = sessions.find((s) => s.sessionId === activeSessionId);
    if (!base) {
      // Active session isn't on the current page (e.g., after page change).
      setActiveDetail(null);
      return;
    }
    // Show the row-derived stub immediately so the panes don't flash empty,
    // then swap in the full message list once the detail fetch returns.
    setActiveDetail(base);
    const seq = ++detailSeq.current;
    setDetailLoading(true);
    (async () => {
      try {
        const res = await api.getBotSession(bot.slug, activeSessionId);
        if (seq !== detailSeq.current) return;
        setActiveDetail(withDetail(base, res.messages));
      } catch (err: unknown) {
        if (seq !== detailSeq.current) return;
        // Keep the stub on failure — it still renders header info — and
        // surface the error in the top-level banner.
        setError(err instanceof Error ? err.message : "Failed to load thread");
      } finally {
        if (seq === detailSeq.current) setDetailLoading(false);
      }
    })();
  }, [activeSessionId, sessions, bot.slug]);

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const showingFrom = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const showingTo = Math.min(total, (page + 1) * PAGE_SIZE);

  return (
    <>
      <div className="header">
        <div>
          <h1>Conversations — {bot.name}</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            {total.toLocaleString()} {total === 1 ? "session" : "sessions"}
            {detailLoading ? " · loading thread…" : ""}
          </div>
        </div>
        <div className="row">
          <button
            className="ghost compact"
            disabled={loading}
            onClick={refresh}
            title="Reload"
          >
            <RefreshCw size={14} strokeWidth={1.75} className={loading ? "spin" : undefined} />
            Refresh
          </button>
        </div>
      </div>

      <ConversationsFilterBar value={filters} onChange={updateFilters} />

      {error && <div className="error">{error}</div>}

      <div className="conv-split">
        <ConversationsList
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelect={setActiveSessionId}
          loading={loading && sessions.length === 0}
        />
        <ConversationThread bot={bot} session={activeDetail} />
        <ConversationMeta session={activeDetail} />
      </div>

      <div className="conv-pagination">
        <span>
          {total === 0
            ? "No sessions match"
            : `Showing ${showingFrom.toLocaleString()}–${showingTo.toLocaleString()} of ${total.toLocaleString()}`}
        </span>
        <div className="conv-pagination-spacer" />
        <button
          type="button"
          className="ghost compact"
          disabled={page === 0 || loading}
          onClick={() => goToPage(Math.max(0, page - 1))}
        >
          <ChevronLeft size={14} strokeWidth={1.75} />
          Prev
        </button>
        <span style={{ fontFamily: "var(--font-mono)" }}>
          {page + 1} / {pageCount}
        </span>
        <button
          type="button"
          className="ghost compact"
          disabled={page + 1 >= pageCount || loading}
          onClick={() => goToPage(page + 1)}
        >
          Next
          <ChevronRight size={14} strokeWidth={1.75} />
        </button>
      </div>
    </>
  );
}
