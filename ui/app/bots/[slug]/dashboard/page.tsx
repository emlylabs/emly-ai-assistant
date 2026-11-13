"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, PauseCircle, RefreshCw, Sparkles, X } from "lucide-react";
import { useBot } from "@/components/BotShell";
import KpiCard from "@/components/KpiCard";
import Sparkline from "@/components/charts/Sparkline";
import MessageVolumeCard from "@/components/dashboard/MessageVolumeCard";
import ActivityFeed, { buildActivityEvents } from "@/components/dashboard/ActivityFeed";
import {
  api,
  type BotFile,
  type CrawlJob,
  type DailyMessageBucket,
  type Message,
} from "@/lib/api";
import { bucketDistinctByDay, weekOverWeekDelta } from "@/lib/aggregations";
import { isSetupDismissed, markSetupDismissed } from "@/lib/setup-state";

type Stats = {
  bot_id: string;
  slug: string;
  name: string;
  end_user_count: number;
  message_count: number;
  file_count: number;
  member_count: number;
};

const MESSAGE_FETCH_LIMIT = 500;
const SPARKLINE_DAYS = 14;

export default function BotDashboardPage() {
  const { bot } = useBot();
  const [stats, setStats] = useState<Stats | null>(null);
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [messageDaily, setMessageDaily] = useState<DailyMessageBucket[] | null>(null);
  const [files, setFiles] = useState<BotFile[] | null>(null);
  const [crawlJobs, setCrawlJobs] = useState<CrawlJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    const toTs = Math.floor(Date.now() / 1000);
    const fromTs = toTs - SPARKLINE_DAYS * 86400;
    // Fire reads in parallel; failures inside each settled result keep the
    // page partially usable rather than blanking everything. The 14-day
    // message-volume series comes from the server-aggregated daily endpoint
    // — bucketing the raw message list silently undercounted busy bots
    // because the 500-row sample didn't span the full window.
    const results = await Promise.allSettled([
      api.botDashboardStats(bot.slug),
      api.listBotMessages(bot.slug, { limit: MESSAGE_FETCH_LIMIT }),
      // Page 1 with a generous size — the dashboard widgets need recent
      // files (newest-first) for the activity feed and the "this week"
      // counter, both of which will live on page 1 for any realistic bot.
      api.listBotFiles(bot.slug, { page: 1, pageSize: 200 }),
      api.listCrawlJobs(bot.slug),
      api.getBotMessagesDaily(bot.slug, { from: fromTs, to: toTs }),
    ]);
    if (results[0].status === "fulfilled") setStats(results[0].value);
    else setError(results[0].reason instanceof Error ? results[0].reason.message : "Failed to load stats");
    if (results[1].status === "fulfilled") setMessages(results[1].value.items);
    if (results[2].status === "fulfilled") setFiles(results[2].value.items);
    if (results[3].status === "fulfilled") setCrawlJobs(results[3].value);
    if (results[4].status === "fulfilled") setMessageDaily(results[4].value);
    setRefreshing(false);
  }, [bot.slug]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Server-aggregated daily message counts drive the volume card and the
  // "Messages (total)" sparkline. The unique-users sparkline still uses the
  // raw message sample — it's approximate over recent activity and the
  // KPI label is the lifetime end-user count.
  const series = useMemo(() => {
    if (!messageDaily) return null;
    const messageCounts = messageDaily.map((d) => d.count);
    const messageKeys = messageDaily.map((d) => d.day);
    const userBuckets = messages
      ? bucketDistinctByDay(
          messages.map((m) => ({ ts: m.created_at, key: m.user_id })),
          SPARKLINE_DAYS,
        )
      : null;
    return {
      messageCounts,
      messageKeys,
      userBuckets,
    };
  }, [messageDaily, messages]);

  // "+N this week" for files — computed from `created_at` if available.
  const filesThisWeek = useMemo(() => {
    if (!files) return null;
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    let count = 0;
    for (const f of files) {
      if (!f.created_at) continue;
      const t = new Date(f.created_at).getTime();
      if (Number.isFinite(t) && t >= cutoff) count += 1;
    }
    return count;
  }, [files]);

  // Crawl summary — most-recent crawl by completed_at (falls back to
  // updated_at for jobs still active), so the KPI shows the latest run
  // regardless of outcome. Timestamps can be null when the crawl model
  // hasn't backfilled them yet — coerce to 0 so the sort still terminates.
  const lastCrawl = useMemo(() => {
    if (!crawlJobs || crawlJobs.length === 0) return null;
    const tsOf = (j: typeof crawlJobs[number]) => {
      const t = j.completed_at ?? j.updated_at ?? j.created_at;
      return t ? new Date(t).getTime() : 0;
    };
    const sorted = [...crawlJobs].sort((a, b) => tsOf(b) - tsOf(a));
    return sorted[0];
  }, [crawlJobs]);

  const lastCrawlRelative = useMemo(() => {
    if (!lastCrawl) return null;
    const ts = lastCrawl.completed_at ?? lastCrawl.updated_at ?? lastCrawl.created_at;
    if (!ts) return null;
    const elapsedMs = Date.now() - new Date(ts).getTime();
    if (!Number.isFinite(elapsedMs) || elapsedMs < 0) return null;
    const mins = Math.floor(elapsedMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  }, [lastCrawl]);

  const events = useMemo(
    () =>
      buildActivityEvents({
        bot,
        files: files ?? undefined,
        crawlJobs: crawlJobs ?? undefined,
        messages: messages ?? undefined,
      }),
    [bot, files, crawlJobs, messages],
  );

  // The Resume setup pill shows on a fresh bot until the admin has either
  // uploaded a file or explicitly dismissed setup. We don't gate on the
  // `setup_dismissed` flag alone — if the bot already has files, the
  // setup pill is noise regardless of localStorage state.
  const [setupHidden, setSetupHidden] = useState<boolean>(true);
  useEffect(() => {
    setSetupHidden(isSetupDismissed(bot.id));
  }, [bot.id]);
  const showSetupPill =
    !setupHidden &&
    files !== null &&
    files.length === 0;

  function dismissSetup() {
    markSetupDismissed(bot.id);
    setSetupHidden(true);
  }

  return (
    <>
      <div className="header">
        <div>
          <h1>{bot.name}</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            Live overview · refreshes on demand
          </div>
        </div>
        <button
          className="ghost compact"
          onClick={refresh}
          disabled={refreshing}
          aria-label="Refresh"
        >
          <RefreshCw
            size={14}
            strokeWidth={1.75}
            className={refreshing ? "spin" : undefined}
          />
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {!bot.is_active && (
        <div
          className="banner banner-icon"
          style={{
            marginBottom: 16,
            background: "var(--warn-bg, rgba(202, 138, 4, 0.08))",
            borderColor: "var(--warn-border, rgba(202, 138, 4, 0.4))",
          }}
        >
          <PauseCircle strokeWidth={1.75} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>This bot is paused</div>
            <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
              Visitors can open the widget but it won&apos;t respond. KPIs below reflect
              live data only — they freeze while the bot is paused.
            </div>
          </div>
          <Link
            href={`/bots/${bot.slug}/settings`}
            style={{ textDecoration: "none" }}
          >
            <button type="button">
              Unpause in Settings
              <ArrowRight size={14} strokeWidth={1.75} />
            </button>
          </Link>
        </div>
      )}

      {showSetupPill && (
        <div
          className="banner advisory banner-icon"
          style={{ marginBottom: 16 }}
        >
          <Sparkles strokeWidth={1.75} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600 }}>Finish setting up this bot</div>
            <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
              Add knowledge, try the chat, and grab the embed snippet.
            </div>
          </div>
          <Link
            href={`/bots/${bot.slug}/setup`}
            style={{ textDecoration: "none" }}
          >
            <button type="button">
              Resume setup
              <ArrowRight size={14} strokeWidth={1.75} />
            </button>
          </Link>
          <button
            type="button"
            className="ghost compact"
            onClick={dismissSetup}
            aria-label="Dismiss setup pill"
            title="Dismiss"
          >
            <X size={14} strokeWidth={1.75} />
          </button>
        </div>
      )}

      {error && <div className="error">{error}</div>}

      {!stats && !error ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="kpi-grid" style={{ marginBottom: 16 }}>
            <KpiCard
              label="Messages (total)"
              value={stats?.message_count.toLocaleString() ?? "—"}
              delta={series ? weekOverWeekDelta(series.messageCounts) ?? undefined : undefined}
              spark={
                series ? (
                  <Sparkline points={series.messageCounts} area />
                ) : undefined
              }
            />
            <KpiCard
              label="End users"
              value={stats?.end_user_count.toLocaleString() ?? "—"}
              delta={series?.userBuckets ? weekOverWeekDelta(series.userBuckets.counts) ?? undefined : undefined}
              spark={
                series?.userBuckets ? (
                  <Sparkline points={series.userBuckets.counts} area />
                ) : undefined
              }
            />
            <KpiCard
              label="Knowledge"
              value={stats?.file_count.toLocaleString() ?? "—"}
              delta={
                filesThisWeek !== null && filesThisWeek > 0
                  ? { value: `+${filesThisWeek} this week`, direction: "up" }
                  : undefined
              }
            />
            <KpiCard
              label="Members"
              value={stats?.member_count.toLocaleString() ?? "—"}
            />
            {crawlJobs !== null && (
              <KpiCard
                label="Last crawl"
                value={
                  lastCrawl ? (
                    <Link
                      href={`/bots/${bot.slug}/files`}
                      style={{ color: "inherit", textDecoration: "none" }}
                      title={lastCrawl.seed_url}
                    >
                      {lastCrawlRelative ?? "—"}
                    </Link>
                  ) : (
                    "—"
                  )
                }
                delta={
                  lastCrawl
                    ? {
                        value: `${lastCrawl.pages_done}/${lastCrawl.pages_total} pages${
                          lastCrawl.pages_failed > 0 ? `, ${lastCrawl.pages_failed} failed` : ""
                        }`,
                        direction:
                          lastCrawl.status === "completed"
                            ? "up"
                            : lastCrawl.status === "failed" || lastCrawl.pages_failed > 0
                              ? "down"
                              : "neutral",
                      }
                    : undefined
                }
                footnote={lastCrawl ? `${lastCrawl.status}` : "No crawls yet"}
              />
            )}
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)",
              gap: 16,
              alignItems: "stretch",
            }}
            className="dashboard-split"
          >
            {series ? (
              <MessageVolumeCard
                dailyCounts={series.messageCounts}
                dayKeys={series.messageKeys}
                totalLabel={series.messageCounts
                  .reduce((a, b) => a + b, 0)
                  .toLocaleString()}
              />
            ) : (
              <div className="card" style={{ padding: 24 }}>
                <p className="muted">Loading message volume…</p>
              </div>
            )}
            <ActivityFeed events={events} />
          </div>
        </>
      )}
    </>
  );
}
