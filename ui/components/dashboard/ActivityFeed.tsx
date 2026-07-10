"use client";

import { Check, FileText, Globe, MessageSquare, Settings as SettingsIcon } from "lucide-react";
import Feed from "@/components/feed/Feed";
import FeedItem, { type FeedTone } from "@/components/feed/FeedItem";
import type { BotFile, CrawlJob, Message, Bot } from "@/lib/api";
import { relativeTime } from "@/lib/aggregations";

export type ActivityEvent = {
  id: string;
  /** Sort key: ISO timestamp, newest first. */
  ts: string;
  tone: FeedTone;
  icon: React.ReactNode;
  title: React.ReactNode;
  meta?: React.ReactNode;
};

type Inputs = {
  bot?: Pick<Bot, "name" | "slug" | "updated_at"> | null;
  files?: BotFile[];
  crawlJobs?: CrawlJob[];
  messages?: Message[];
};

/**
 * Synthesize a feed from real data sources. Honest by design — every event
 * corresponds to a row that actually exists in the DB. We deliberately
 * don't invent "deflection threshold reached" or "milestone" events.
 *
 * Sort order is newest-first; callers slice the result for display.
 */
export function buildActivityEvents({ bot, files, crawlJobs, messages }: Inputs): ActivityEvent[] {
  const events: ActivityEvent[] = [];

  // File uploads. Skip files without a created_on.
  for (const f of files ?? []) {
    if (!f.created_on) continue;
    events.push({
      id: `file:${f.id}`,
      ts: f.created_on,
      tone: f.embedding_status === "failed" ? "danger" : "default",
      icon: <FileText size={12} strokeWidth={1.75} />,
      title: (
        <>
          File uploaded — <strong>{f.file_name}</strong>
        </>
      ),
      meta: f.embedding_status,
    });
  }

  // Crawl jobs. Use the most recent timestamp available.
  for (const job of crawlJobs ?? []) {
    const ts = job.completed_on ?? job.updated_on ?? job.created_on;
    if (!ts) continue;
    const failedOrCancelled = job.status === "failed" || job.status === "cancelled";
    events.push({
      id: `crawl:${job.id}`,
      ts,
      tone: job.status === "completed" ? "success" : failedOrCancelled ? "warn" : "info",
      icon: <Globe size={12} strokeWidth={1.75} />,
      title: (
        <>
          Crawl <strong>{job.status}</strong> — {job.seed_url}
        </>
      ),
      meta: `${job.pages_done}/${job.pages_total} pages`,
    });
  }

  // Bot config updates — at most one event, derived from `bot.updated_at`.
  if (bot?.updated_at) {
    events.push({
      id: `bot:${bot.slug}:updated`,
      ts: bot.updated_at,
      tone: "default",
      icon: <SettingsIcon size={12} strokeWidth={1.75} />,
      title: (
        <>
          Config updated — <strong>{bot.name}</strong>
        </>
      ),
    });
  }

  // Conversation starts — collapse messages into one event per session,
  // keyed on the earliest message in the session over the fetched window.
  // Avoids hundreds of "new message" rows on busy bots.
  const seenSessions = new Set<string>();
  for (const m of messages ?? []) {
    if (!m.session_id || !m.created_on) continue;
    if (seenSessions.has(m.session_id)) continue;
    seenSessions.add(m.session_id);
    events.push({
      id: `session:${m.session_id}`,
      ts: m.created_on,
      tone: "info",
      icon: <MessageSquare size={12} strokeWidth={1.75} />,
      title: <>New conversation started</>,
      meta: `session ${m.session_id.slice(0, 12)}`,
    });
  }

  events.sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
  return events;
}

type ActivityFeedProps = {
  events: ActivityEvent[];
  /** Cap how many rows render. Default 8. */
  limit?: number;
};

/**
 * Feed card. Wraps the underlying `<Feed>` with a card chrome + empty state.
 * The mockup's "View all" link is omitted — there's no dedicated activity
 * route to link to in v1.
 */
export default function ActivityFeed({ events, limit = 8 }: ActivityFeedProps) {
  const visible = events.slice(0, limit);

  return (
    <div className="card" style={{ padding: 0 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Activity</div>
          <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
            recent events from this bot
          </div>
        </div>
      </div>
      {visible.length === 0 ? (
        <div style={{ padding: "20px 16px", color: "var(--muted)", fontSize: 12.5, textAlign: "center" }}>
          No recent activity yet.
        </div>
      ) : (
        <Feed>
          {visible.map((e) => (
            <FeedItem
              key={e.id}
              icon={e.icon ?? <Check size={12} strokeWidth={1.75} />}
              tone={e.tone}
              title={e.title}
              meta={e.meta}
              time={relativeTime(e.ts)}
            />
          ))}
        </Feed>
      )}
    </div>
  );
}
