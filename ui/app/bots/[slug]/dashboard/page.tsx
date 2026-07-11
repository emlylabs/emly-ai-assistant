"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
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
      api.listBotFiles(bot.slug),
      api.listCrawlJobs(bot.slug),
      api.getBotMessagesDaily(bot.slug, { from: fromTs, to: toTs }),
    ]);
    if (results[0].status === "fulfilled") setStats(results[0].value);
    else setError(results[0].reason instanceof Error ? results[0].reason.message : "Failed to load stats");
    if (results[1].status === "fulfilled") setMessages(results[1].value.items);
    if (results[2].status === "fulfilled") setFiles(results[2].value);
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
          messages.map((m) => ({ ts: m.created_on, key: m.user_id })),
          SPARKLINE_DAYS,
        )
      : null;
    return {
      messageCounts,
      messageKeys,
      userBuckets,
    };
  }, [messageDaily, messages]);

  // "+N this week" for files — computed from `created_on` if available.
  const filesThisWeek = useMemo(() => {
    if (!files) return null;
    const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
    let count = 0;
    for (const f of files) {
      if (!f.created_on) continue;
      const t = new Date(f.created_on).getTime();
      if (Number.isFinite(t) && t >= cutoff) count += 1;
    }
    return count;
  }, [files]);

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
              label="Files"
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
