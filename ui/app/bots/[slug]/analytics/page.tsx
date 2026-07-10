"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useBot } from "@/components/BotShell";
import KpiCard from "@/components/KpiCard";
import VolumeChart from "@/components/charts/VolumeChart";
import TopIntentsCard from "@/components/analytics/TopIntentsCard";
import ModelUsageCard, {
  PRICE_TABLE_PER_1K,
  totalSpend,
} from "@/components/analytics/ModelUsageCard";
import SessionFlowCard from "@/components/analytics/SessionFlowCard";
import ResolutionFunnelCard from "@/components/analytics/ResolutionFunnelCard";
import ChannelFilter from "@/components/analytics/ChannelFilter";
import ChannelMixCard from "@/components/analytics/ChannelMixCard";
import EnrichmentCard from "@/components/analytics/EnrichmentCard";
import CitationsCard from "@/components/analytics/CitationsCard";
import LatencyDistributionCard from "@/components/analytics/LatencyDistributionCard";
import HeatmapCard from "@/components/analytics/HeatmapCard";
import {
  api,
  type BotReport,
  type CitationStats,
  type DailyMessageBucket,
  type DailySessionBucket,
  type EnrichmentSummary,
  type FunnelResponse,
  type HeatmapCell,
  type LatencyQuantiles,
  type MessageCountByChannel,
  type MessageCountByTopic,
  type MessageUsageByModel,
} from "@/lib/api";
import { percentDelta, shortDayLabel } from "@/lib/aggregations";

type Direction = "up" | "down" | "neutral";
type Delta = { value: string; direction: Direction };

// Range can be a preset window in days, or "custom" with explicit
// from/to seconds. The two collapse to the same `(fromTs, toTs)` pair
// at fetch time.
type Range = 7 | 30 | 90 | "custom";
type CustomWindow = { fromTs: number; toTs: number };

const PRESET_RANGES: { value: Exclude<Range, "custom">; label: string }[] = [
  { value: 7, label: "7d" },
  { value: 30, label: "30d" },
  { value: 90, label: "90d" },
];

/** Raw percent change. For totals (messages, sessions, spend). Returns
 * `null` when the prior window is empty so we don't lie with `Infinity%`. */
function deltaPct(
  current: number | null | undefined,
  prior: number | null | undefined,
  opts: { lowerIsBetter?: boolean } = {},
): Delta | null {
  if (current === null || current === undefined) return null;
  if (prior === null || prior === undefined) return null;
  const d = percentDelta(current, prior);
  if (d === null) return null;
  if (opts.lowerIsBetter && d.direction !== "neutral") {
    return { value: d.value, direction: d.direction === "up" ? "down" : "up" };
  }
  return d;
}

/** Percentage-point delta for rate KPIs (resolution, deflection). Both
 * inputs are in `[0, 1]`. */
function deltaPctPoint(
  current: number | null | undefined,
  prior: number | null | undefined,
): Delta | null {
  if (current === null || current === undefined) return null;
  if (prior === null || prior === undefined) return null;
  const diff = (current - prior) * 100;
  if (Math.abs(diff) < 0.05) return { value: "0.0pp", direction: "neutral" };
  const sign = diff > 0 ? "+" : "−";
  return {
    value: `${sign}${Math.abs(diff).toFixed(1)}pp`,
    direction: diff > 0 ? "up" : "down",
  };
}

function deltaLatency(
  current: number | null | undefined,
  prior: number | null | undefined,
): Delta | null {
  if (current === null || current === undefined) return null;
  if (prior === null || prior === undefined) return null;
  const diff = current - prior;
  if (Math.abs(diff) < 0.5) return { value: "0ms", direction: "neutral" };
  const sign = diff > 0 ? "+" : "−";
  const abs = Math.abs(diff);
  const formatted = abs >= 1000 ? `${(abs / 1000).toFixed(2)}s` : `${Math.round(abs)}ms`;
  return {
    value: `${sign}${formatted}`,
    direction: diff > 0 ? "down" : "up",
  };
}

function deltaCsat(
  current: number | null | undefined,
  prior: number | null | undefined,
): Delta | null {
  if (current === null || current === undefined) return null;
  if (prior === null || prior === undefined) return null;
  const diff = current - prior;
  if (Math.abs(diff) < 0.005) return { value: "+0.00", direction: "neutral" };
  const sign = diff > 0 ? "+" : "−";
  return {
    value: `${sign}${Math.abs(diff).toFixed(2)}`,
    direction: diff > 0 ? "up" : "down",
  };
}

function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

function formatLatencyMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
}

function formatCsat(avg: number | null | undefined): string {
  if (avg === null || avg === undefined || !Number.isFinite(avg)) return "—";
  const sign = avg > 0 ? "+" : "";
  return `${sign}${avg.toFixed(2)}`;
}

function formatUsd(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

/** Align a prior-period daily series to the current period's bar count. */
function alignPriorSeries(prior: number[], targetLength: number): number[] {
  if (prior.length === targetLength) return prior;
  if (prior.length > targetLength) return prior.slice(prior.length - targetLength);
  return new Array(targetLength - prior.length).fill(0).concat(prior);
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function csvLine(cells: unknown[]): string {
  return cells.map(csvCell).join(",");
}

function downloadBlob(content: string, filename: string, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Convert an `<input type="date">` value (`YYYY-MM-DD`) to a UTC unix
 * timestamp at midnight. Empty input → null so the caller can decide
 * the default. */
function dateInputToTs(value: string, atEnd = false): number | null {
  if (!value) return null;
  // Treat the input as UTC midnight to match the rest of the page.
  const t = Date.parse(`${value}T${atEnd ? "23:59:59" : "00:00:00"}Z`);
  return Number.isFinite(t) ? Math.floor(t / 1000) : null;
}

function tsToDateInput(ts: number): string {
  // `YYYY-MM-DD` for `<input type="date">`.
  return new Date(ts * 1000).toISOString().slice(0, 10);
}

/**
 * Per-bot analytics. Every chart and KPI reads from a server-aggregated
 * endpoint; the page never iterates the raw message list. SSE keeps the
 * page warm — incoming session events trigger a debounced refetch.
 */
export default function BotAnalyticsPage() {
  const { bot } = useBot();
  const [range, setRange] = useState<Range>(30);
  const [customWindow, setCustomWindow] = useState<CustomWindow>(() => {
    // Default the custom range to the trailing 14 days when first
    // entered — a "fine-grained recent" cut that the presets don't cover.
    const now = Math.floor(Date.now() / 1000);
    return { fromTs: now - 14 * 86400, toTs: now };
  });
  const [channelId, setChannelId] = useState<string | null>(null);
  const [compare, setCompare] = useState(false);

  // Current-window state.
  const [report, setReport] = useState<BotReport | null>(null);
  const [sessionDaily, setSessionDaily] = useState<DailySessionBucket[] | null>(null);
  const [messageDaily, setMessageDaily] = useState<DailyMessageBucket[] | null>(null);
  const [usageByModel, setUsageByModel] = useState<MessageUsageByModel[] | null>(null);
  const [topicCounts, setTopicCounts] = useState<MessageCountByTopic[] | null>(null);
  const [funnel, setFunnel] = useState<FunnelResponse | null>(null);
  const [channelMix, setChannelMix] = useState<MessageCountByChannel[] | null>(null);
  const [enrichment, setEnrichment] = useState<EnrichmentSummary | null>(null);
  const [citations, setCitations] = useState<CitationStats | null>(null);
  const [latency, setLatency] = useState<LatencyQuantiles | null>(null);
  const [heatmap, setHeatmap] = useState<HeatmapCell[] | null>(null);

  // Prior-window state for compare mode.
  const [priorReport, setPriorReport] = useState<BotReport | null>(null);
  const [priorMessageDaily, setPriorMessageDaily] = useState<DailyMessageBucket[] | null>(null);
  const [priorUsageByModel, setPriorUsageByModel] = useState<MessageUsageByModel[] | null>(null);
  const [priorTopicCounts, setPriorTopicCounts] = useState<MessageCountByTopic[] | null>(null);
  const [priorFunnel, setPriorFunnel] = useState<FunnelResponse | null>(null);
  const [priorSessionDaily, setPriorSessionDaily] = useState<DailySessionBucket[] | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Resolve the active window to (fromTs, toTs). Named `viewWindow`
  // rather than `window` to avoid shadowing the browser global, which
  // we still need inside SSE setup below.
  const viewWindow = useMemo<{ fromTs: number; toTs: number; days: number }>(() => {
    if (range === "custom") {
      const days = Math.max(
        1,
        Math.round((customWindow.toTs - customWindow.fromTs) / 86400),
      );
      return { fromTs: customWindow.fromTs, toTs: customWindow.toTs, days };
    }
    const toTs = Math.floor(Date.now() / 1000);
    return { fromTs: toTs - range * 86400, toTs, days: range };
  }, [range, customWindow]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    // Clear the prior-window snapshot before each fetch so a partial
    // failure can't leave the previous compare result mixed with a new
    // primary window. The current-window state stays put until the new
    // values land — clearing it would flash the "Loading…" splash on
    // every refresh.
    setPriorReport(null);
    setPriorMessageDaily(null);
    setPriorUsageByModel(null);
    setPriorTopicCounts(null);
    setPriorFunnel(null);
    setPriorSessionDaily(null);
    try {
      const { fromTs, toTs, days } = viewWindow;
      const priorTo = fromTs;
      const priorFrom = priorTo - days * 86400;
      const channelOpt = channelId ? { channel_id: channelId } : {};
      // Channel-mix is bot-wide by design (it's the source of the
      // filter list), so it never carries the selected channel filter.
      const currentP = Promise.all([
        api.getBotReport(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotSessionsDaily(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotMessagesDaily(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotMessageUsageByModel(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotMessageCountsByTopic(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotFunnel(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotMessageCountsByChannel(bot.slug, { from: fromTs, to: toTs }),
        api.getBotEnrichmentSummary(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotCitationStats(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotLatencyQuantiles(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
        api.getBotMessagesHeatmap(bot.slug, { from: fromTs, to: toTs, ...channelOpt }),
      ]);
      const priorP = compare
        ? Promise.all([
            api.getBotReport(bot.slug, { from: priorFrom, to: priorTo, ...channelOpt }),
            api.getBotMessagesDaily(bot.slug, { from: priorFrom, to: priorTo, ...channelOpt }),
            api.getBotMessageUsageByModel(bot.slug, { from: priorFrom, to: priorTo, ...channelOpt }),
            api.getBotMessageCountsByTopic(bot.slug, { from: priorFrom, to: priorTo, ...channelOpt }),
            api.getBotFunnel(bot.slug, { from: priorFrom, to: priorTo, ...channelOpt }),
            api.getBotSessionsDaily(bot.slug, { from: priorFrom, to: priorTo, ...channelOpt }),
          ])
        : Promise.resolve(null);
      const [current, prior] = await Promise.all([currentP, priorP]);
      const [
        rpt,
        sessions,
        msgsDaily,
        usage,
        topics,
        fnl,
        chanMix,
        enr,
        cits,
        lat,
        heat,
      ] = current;
      setReport(rpt);
      setSessionDaily(sessions);
      setMessageDaily(msgsDaily);
      setUsageByModel(usage);
      setTopicCounts(topics);
      setFunnel(fnl);
      setChannelMix(chanMix);
      setEnrichment(enr);
      setCitations(cits);
      setLatency(lat);
      setHeatmap(heat);
      if (prior) {
        const [
          priorRpt,
          priorMsgsDaily,
          priorUsage,
          priorTopics,
          priorFnl,
          priorSess,
        ] = prior;
        setPriorReport(priorRpt);
        setPriorMessageDaily(priorMsgsDaily);
        setPriorUsageByModel(priorUsage);
        setPriorTopicCounts(priorTopics);
        setPriorFunnel(priorFnl);
        setPriorSessionDaily(priorSess);
      } else {
        setPriorReport(null);
        setPriorMessageDaily(null);
        setPriorUsageByModel(null);
        setPriorTopicCounts(null);
        setPriorFunnel(null);
        setPriorSessionDaily(null);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load analytics");
    } finally {
      setRefreshing(false);
    }
  }, [bot.slug, viewWindow, channelId, compare]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // When the loaded channel-mix no longer contains the active filter
  // (e.g. the user shrank the window and that channel went silent),
  // clear the filter so the page doesn't keep refetching against an
  // option the user can no longer see in the dropdown.
  useEffect(() => {
    if (!channelId || !channelMix) return;
    const stillPresent = channelMix.some((c) => c.channel_id === channelId);
    if (!stillPresent) setChannelId(null);
  }, [channelMix, channelId]);

  // Phase E — SSE: subscribe to the same `/conversations/stream` the
  // Conversations list uses; any session event triggers a debounced
  // refetch. Multi-replica deployments return 503 with a documented
  // header — we silently drop the subscription so the page falls back
  // to the manual Refresh button.
  const sseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    let es: EventSource | null = null;
    let cancelled = false;
    try {
      es = new EventSource(
        `/api/admin/bots/${encodeURIComponent(bot.slug)}/conversations/stream`,
        { withCredentials: true },
      );
      es.onmessage = () => {
        if (cancelled) return;
        // Debounce — bursts of activity (a chatty session) shouldn't
        // trigger 10 simultaneous refetches.
        if (sseTimer.current) {
          clearTimeout(sseTimer.current);
          sseTimer.current = null;
        }
        sseTimer.current = setTimeout(() => {
          sseTimer.current = null;
          if (!cancelled) refresh();
        }, 1500);
      };
      es.onerror = () => {
        // EventSource auto-reconnects. We don't surface a UI banner —
        // the manual Refresh button is always available.
      };
    } catch {
      // EventSource construction can throw in restrictive contexts;
      // there's nothing useful to recover, so we just live without SSE.
    }
    return () => {
      cancelled = true;
      if (sseTimer.current) {
        clearTimeout(sseTimer.current);
        sseTimer.current = null;
      }
      es?.close();
    };
  }, [bot.slug, refresh]);

  // ---------- Derived ----------
  const llmSpend = useMemo(
    () => (usageByModel ? totalSpend(usageByModel) : null),
    [usageByModel],
  );
  const priorLlmSpend = useMemo(
    () => (priorUsageByModel ? totalSpend(priorUsageByModel) : null),
    [priorUsageByModel],
  );

  const messagesCount = report?.messages ?? null;
  const sessionsCount = report?.conversations ?? null;
  const priorMessagesCount = priorReport?.messages ?? null;
  const priorSessionsCount = priorReport?.conversations ?? null;

  const deltas = compare
    ? {
        messages: deltaPct(messagesCount, priorMessagesCount),
        sessions: deltaPct(sessionsCount, priorSessionsCount),
        resolution: deltaPctPoint(report?.resolution_rate, priorReport?.resolution_rate),
        csat: deltaCsat(report?.csat_avg, priorReport?.csat_avg),
        deflection: deltaPctPoint(report?.deflection_rate, priorReport?.deflection_rate),
        latency: deltaLatency(report?.p95_latency_ms, priorReport?.p95_latency_ms),
        widgetViews: deltaPct(report?.short_impressions, priorReport?.short_impressions),
        spend:
          llmSpend?.priced && priorLlmSpend?.priced
            ? deltaPct(llmSpend.total, priorLlmSpend.total, { lowerIsBetter: true })
            : null,
      }
    : null;

  // Volume chart total + role split summary line.
  const volumeSplit = useMemo(() => {
    if (!messageDaily) return null;
    let user = 0;
    let assistant = 0;
    let total = 0;
    for (const d of messageDaily) {
      total += d.count;
      user += d.user_count ?? 0;
      assistant += d.assistant_count ?? 0;
    }
    return { user, assistant, total };
  }, [messageDaily]);

  const priorTopicTotal = useMemo(
    () => (priorTopicCounts ? priorTopicCounts.reduce((a, b) => a + b.count, 0) : null),
    [priorTopicCounts],
  );
  const priorSessionTotals = useMemo(() => {
    if (!priorSessionDaily) return null;
    return priorSessionDaily.reduce(
      (acc, b) => ({
        started: acc.started + b.started,
        resolved: acc.resolved + b.resolved,
      }),
      { started: 0, resolved: 0 },
    );
  }, [priorSessionDaily]);

  const exportCsv = useCallback(() => {
    if (!report || !messageDaily) return;
    const lines: string[] = [];
    const append = (s: string) => lines.push(s);

    append(`# Analytics export — ${bot.name} (${bot.slug})`);
    append(
      `# Window: ${tsToDateInput(viewWindow.fromTs)} → ${tsToDateInput(viewWindow.toTs)} · ${viewWindow.days}d · UTC`,
    );
    if (channelId) append(`# Channel filter: ${channelId}`);
    append(`# Generated ${new Date().toISOString()}`);
    append("");

    append("# KPIs");
    append(csvLine(["metric", "value", "prior", "delta"]));
    const fmtPrior = compare && priorReport ? priorReport : null;
    const kpiRows: Array<[string, unknown, unknown, string]> = [
      ["total_messages", report.messages, fmtPrior?.messages ?? "", deltas?.messages?.value ?? ""],
      ["sessions", report.conversations, fmtPrior?.conversations ?? "", deltas?.sessions?.value ?? ""],
      ["resolution_rate", report.resolution_rate ?? "", fmtPrior?.resolution_rate ?? "", deltas?.resolution?.value ?? ""],
      ["deflection_rate", report.deflection_rate ?? "", fmtPrior?.deflection_rate ?? "", deltas?.deflection?.value ?? ""],
      ["csat_avg", report.csat_avg ?? "", fmtPrior?.csat_avg ?? "", deltas?.csat?.value ?? ""],
      ["csat_count", report.csat_count, fmtPrior?.csat_count ?? "", ""],
      ["p95_latency_ms", report.p95_latency_ms ?? "", fmtPrior?.p95_latency_ms ?? "", deltas?.latency?.value ?? ""],
      [
        "llm_spend_usd",
        llmSpend && llmSpend.priced ? llmSpend.total.toFixed(4) : "",
        priorLlmSpend && priorLlmSpend.priced ? priorLlmSpend.total.toFixed(4) : "",
        deltas?.spend?.value ?? "",
      ],
      [
        "widget_views_short",
        report.short_impressions ?? 0,
        fmtPrior?.short_impressions ?? "",
        deltas?.widgetViews?.value ?? "",
      ],
      [
        "widget_views_long",
        report.impressions ?? 0,
        fmtPrior?.impressions ?? "",
        "",
      ],
    ];
    for (const row of kpiRows) append(csvLine(row));
    append("");

    if (latency) {
      append("# Latency quantiles");
      append(csvLine(["quantile", "ms", "samples"]));
      append(csvLine(["p50", latency.p50 ?? "", latency.count]));
      append(csvLine(["p95", latency.p95 ?? "", latency.count]));
      append(csvLine(["p99", latency.p99 ?? "", latency.count]));
      append("");
    }

    if (citations) {
      append("# Citations");
      append(csvLine(["metric", "value"]));
      append(csvLine(["assistant_turns", citations.assistant_turns]));
      append(csvLine(["with_citations", citations.with_citations]));
      append(csvLine(["citation_rate", citations.citation_rate ?? ""]));
      append("");
      if (citations.top_files.length > 0) {
        append("# Top cited files");
        append(csvLine(["file_id", "filename", "citations"]));
        for (const f of citations.top_files) {
          append(csvLine([f.file_id ?? "", f.filename ?? "", f.citation_count]));
        }
        append("");
      }
    }

    if (funnel) {
      append("# Resolution funnel");
      append(csvLine(["stage", "count", "rate"]));
      append(csvLine(["started", funnel.started, funnel.started > 0 ? 1 : ""]));
      append(csvLine(["understood", funnel.understood, funnel.understood_rate ?? ""]));
      append(csvLine(["resolved", funnel.resolved, funnel.resolved_rate ?? ""]));
      append("");
    }

    append("# Daily messages");
    if (compare && priorMessageDaily) {
      append(csvLine(["date", "messages", "user", "assistant", "prior_messages"]));
      const prior = alignPriorSeries(
        priorMessageDaily.map((d) => d.count),
        messageDaily.length,
      );
      messageDaily.forEach((d, i) => {
        append(csvLine([d.day, d.count, d.user_count ?? "", d.assistant_count ?? "", prior[i]]));
      });
    } else {
      append(csvLine(["date", "messages", "user", "assistant"]));
      for (const d of messageDaily)
        append(csvLine([d.day, d.count, d.user_count ?? "", d.assistant_count ?? ""]));
    }
    append("");

    if (sessionDaily && sessionDaily.length > 0) {
      append("# Daily sessions");
      append(csvLine(["date", "started", "resolved"]));
      for (const d of sessionDaily) append(csvLine([d.day, d.started, d.resolved]));
      append("");
    }

    if (channelMix && channelMix.length > 0) {
      append("# Channel mix");
      append(csvLine(["channel_id", "channel_type", "display_name", "count"]));
      for (const r of channelMix) {
        append(csvLine([r.channel_id ?? "", r.channel_type ?? "", r.display_name ?? "", r.count]));
      }
      append("");
    }

    if (enrichment) {
      append("# Enrichment");
      append(csvLine(["metric", "value"]));
      append(csvLine(["cohort_size", enrichment.cohort_size]));
      append(csvLine(["enriched_count", enrichment.enriched_count]));
      append(csvLine(["sentiment_positive", enrichment.sentiment.positive]));
      append(csvLine(["sentiment_neutral", enrichment.sentiment.neutral]));
      append(csvLine(["sentiment_negative", enrichment.sentiment.negative]));
      append(csvLine(["sentiment_unrated", enrichment.sentiment.unrated]));
      append("");
      if (enrichment.intents.length > 0) {
        append("# Top enrichment intents");
        append(csvLine(["intent", "count"]));
        for (const i of enrichment.intents) append(csvLine([i.intent, i.count]));
        append("");
      }
    }

    append("# Model usage");
    append(csvLine(["model", "turns", "prompt_tokens", "completion_tokens", "cost_usd"]));
    if (usageByModel) {
      for (const row of usageByModel) {
        const price = PRICE_TABLE_PER_1K[row.model];
        const cost = price
          ? (row.prompt_tokens / 1000) * price.prompt +
            (row.completion_tokens / 1000) * price.completion
          : null;
        append(
          csvLine([
            row.model,
            row.turns,
            row.prompt_tokens,
            row.completion_tokens,
            cost === null ? "" : cost.toFixed(4),
          ]),
        );
      }
    }

    const today = new Date().toISOString().slice(0, 10);
    const suffix = channelId ? `-${channelId.slice(0, 8)}` : "";
    downloadBlob(
      lines.join("\n") + "\n",
      `${bot.slug}-analytics-${today}-${viewWindow.days}d${suffix}.csv`,
    );
  }, [
    bot.name,
    bot.slug,
    viewWindow,
    channelId,
    report,
    priorReport,
    compare,
    messageDaily,
    priorMessageDaily,
    sessionDaily,
    usageByModel,
    llmSpend,
    priorLlmSpend,
    deltas,
    funnel,
    channelMix,
    enrichment,
    citations,
    latency,
  ]);

  // ---------- Empty state ----------
  // The bot has loaded but never received traffic in the active window.
  // When `messages_count === 0` and we're not in a custom window, show a
  // friendly splash instead of the per-card empty states.
  // Compare mode is excluded — its prior-window refetch can briefly
  // re-trip the empty heuristic mid-flight and flash this splash even
  // though the analytics page already has loaded data.
  const isFreshBot =
    report !== null &&
    report.messages === 0 &&
    !channelId &&
    range !== "custom" &&
    !compare;

  return (
    <>
      <div className="header">
        <div>
          <h1>Analytics — {bot.name}</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            {bot.name} ·{" "}
            {range === "custom"
              ? `${tsToDateInput(viewWindow.fromTs)} → ${tsToDateInput(viewWindow.toTs)}`
              : `last ${range} days`}
            {channelId ? ` · channel filtered` : ""} · UTC
          </div>
        </div>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <div className="seg" role="tablist" aria-label="Time range">
            {PRESET_RANGES.map((r) => (
              <button
                key={r.value}
                className={range === r.value ? "active" : ""}
                onClick={() => setRange(r.value)}
                aria-pressed={range === r.value}
              >
                {r.label}
              </button>
            ))}
            <button
              className={range === "custom" ? "active" : ""}
              onClick={() => setRange("custom")}
              aria-pressed={range === "custom"}
              title="Choose a custom date range"
            >
              Custom
            </button>
          </div>
          {range === "custom" && (
            <div className="row" style={{ gap: 4, alignItems: "center" }}>
              <input
                type="date"
                className="ghost compact"
                value={tsToDateInput(customWindow.fromTs)}
                onChange={(e) => {
                  const ts = dateInputToTs(e.target.value);
                  if (ts !== null) setCustomWindow((w) => ({ ...w, fromTs: ts }));
                }}
                style={{ fontSize: 12.5, padding: "4px 6px" }}
              />
              <span className="muted" style={{ fontSize: 12 }}>→</span>
              <input
                type="date"
                className="ghost compact"
                value={tsToDateInput(customWindow.toTs)}
                onChange={(e) => {
                  const ts = dateInputToTs(e.target.value, true);
                  if (ts !== null) setCustomWindow((w) => ({ ...w, toTs: ts }));
                }}
                style={{ fontSize: 12.5, padding: "4px 6px" }}
              />
            </div>
          )}
          <ChannelFilter channels={channelMix} value={channelId} onChange={setChannelId} />
          <button
            className={compare ? "ghost compact active" : "ghost compact"}
            onClick={() => setCompare((c) => !c)}
            title={
              compare
                ? "Hide period-over-period comparison"
                : `Compare with prior ${viewWindow.days} days`
            }
            aria-pressed={compare}
          >
            {compare ? "Compare on" : "Compare"}
          </button>
          <button
            className="ghost compact"
            onClick={exportCsv}
            disabled={!report || !messageDaily}
            title="Download a CSV with KPIs, daily series, and per-model usage"
          >
            Export
          </button>
          <button className="ghost compact" onClick={refresh} disabled={refreshing}>
            <RefreshCw size={14} strokeWidth={1.75} className={refreshing ? "spin" : undefined} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {!report && !error ? (
        <p className="muted">Loading…</p>
      ) : isFreshBot ? (
        <div
          className="card"
          style={{
            padding: 32,
            textAlign: "center",
            display: "flex",
            flexDirection: "column",
            gap: 12,
            alignItems: "center",
          }}
        >
          <div style={{ fontSize: 16, fontWeight: 600 }}>
            No traffic yet on {bot.name}
          </div>
          <div className="muted" style={{ fontSize: 13, maxWidth: 480 }}>
            Once messages start flowing through this bot, KPIs and charts will
            populate here automatically. Embed the widget, install a channel,
            or send a test message from the bot&apos;s Conversations page to
            kick things off.
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 6 }}>
            <a className="ghost compact" href={`/bots/${bot.slug}/channels`}>
              Configure channels
            </a>
            <a className="ghost compact" href={`/bots/${bot.slug}/conversations`}>
              Open conversations
            </a>
          </div>
        </div>
      ) : (
        <>
          <div className="kpi-grid" style={{ marginBottom: 12 }}>
            <KpiCard
              label="Total messages"
              value={messagesCount !== null ? messagesCount.toLocaleString() : "—"}
              delta={deltas?.messages ?? undefined}
              footnote={`last ${viewWindow.days} days`}
            />
            <KpiCard
              label="Sessions"
              value={sessionsCount !== null ? sessionsCount.toLocaleString() : "—"}
              delta={deltas?.sessions ?? undefined}
              footnote="distinct session_id values"
            />
            <KpiCard
              label="Resolution rate"
              value={formatPct(report?.resolution_rate)}
              delta={deltas?.resolution ?? undefined}
              footnote={
                report?.resolution_rate === null
                  ? "no resolved sessions in window"
                  : "sessions marked resolved in window"
              }
            />
            <KpiCard
              label="CSAT"
              value={formatCsat(report?.csat_avg)}
              delta={deltas?.csat ?? undefined}
              footnote={
                report?.csat_count
                  ? `${report.csat_count.toLocaleString()} rated · mean of −1 / +1`
                  : "no ratings in window"
              }
            />
          </div>

          <div className="kpi-grid" style={{ marginBottom: 16 }}>
            <KpiCard
              label="Deflection rate"
              value={formatPct(report?.deflection_rate)}
              delta={deltas?.deflection ?? undefined}
              footnote={
                report?.deflection_count
                  ? `${report.deflection_count.toLocaleString()} labelled assistant turns`
                  : "heuristic flag off — opt in per bot config"
              }
            />
            <KpiCard
              label="p95 response time"
              value={formatLatencyMs(report?.p95_latency_ms)}
              delta={deltas?.latency ?? undefined}
              footnote="LLM call wall-clock latency"
            />
            <KpiCard
              label="LLM spend"
              value={llmSpend && llmSpend.priced ? formatUsd(llmSpend.total) : "—"}
              delta={deltas?.spend ?? undefined}
              footnote={
                llmSpend && llmSpend.priced
                  ? `last ${viewWindow.days} days · token-priced`
                  : "no priced model_used on assistant turns"
              }
            />
            <KpiCard
              label="Widget views"
              value={
                report ? (report.short_impressions ?? 0).toLocaleString() : "—"
              }
              delta={deltas?.widgetViews ?? undefined}
              footnote={
                report && report.short_impressions > 0
                  ? `${(report.impressions ?? 0).toLocaleString()} opens · ${(
                      (report.impressions / report.short_impressions) *
                      100
                    ).toFixed(1)}% engaged`
                  : "no widget mounts in window"
              }
            />
          </div>

          <div className="card" style={{ marginBottom: 16, padding: 0 }}>
            <div
              style={{
                padding: "12px 16px",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                gap: 8,
              }}
            >
              <div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Message volume</div>
                <div className="muted" style={{ fontSize: 11.5 }}>
                  daily · last {viewWindow.days} days
                  {compare ? ` · compared with prior ${viewWindow.days}d` : ""}
                </div>
              </div>
              <span
                className="muted"
                style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
              >
                {volumeSplit
                  ? `${volumeSplit.total.toLocaleString()} msgs · ${volumeSplit.user.toLocaleString()} user · ${volumeSplit.assistant.toLocaleString()} assistant`
                  : "loading…"}
              </span>
            </div>
            <div style={{ padding: "14px 18px" }}>
              {messageDaily ? (
                <VolumeChart
                  series={{
                    bars: messageDaily.map((d) => d.count),
                    lineDashed:
                      compare && priorMessageDaily
                        ? alignPriorSeries(
                            priorMessageDaily.map((d) => d.count),
                            messageDaily.length,
                          )
                        : undefined,
                  }}
                  labels={messageDaily.map((d) => shortDayLabel(d.day))}
                  height={240}
                />
              ) : (
                <p className="muted">Loading…</p>
              )}
            </div>
            {compare && (
              <div
                className="muted"
                style={{
                  padding: "8px 16px 12px",
                  fontSize: 11,
                  display: "flex",
                  gap: 14,
                  borderTop: "1px solid var(--border)",
                }}
              >
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 14,
                      height: 8,
                      background: "var(--accent)",
                      opacity: 0.18,
                      borderRadius: 2,
                    }}
                  />
                  Current {viewWindow.days}d
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span
                    style={{
                      display: "inline-block",
                      width: 14,
                      height: 0,
                      borderTop: "2px dashed var(--muted)",
                    }}
                  />
                  Prior {viewWindow.days}d
                </span>
              </div>
            )}
          </div>

          <div style={{ marginBottom: 16 }}>
            <HeatmapCard cells={heatmap} rangeDays={viewWindow.days} />
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
              gap: 16,
              marginBottom: 16,
            }}
            className="dashboard-split"
          >
            <ResolutionFunnelCard
              data={funnel}
              rangeDays={viewWindow.days}
              prior={compare ? priorFunnel : null}
            />
            <ChannelMixCard
              rows={channelMix}
              rangeDays={viewWindow.days}
              selectedChannelId={channelId}
              onSelectChannel={setChannelId}
            />
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
              gap: 16,
              marginBottom: 16,
            }}
            className="dashboard-split"
          >
            <TopIntentsCard
              rows={topicCounts}
              rangeDays={viewWindow.days}
              priorTotal={compare ? priorTopicTotal : null}
            />
            <SessionFlowCard
              buckets={sessionDaily}
              rangeDays={viewWindow.days}
              priorTotals={compare ? priorSessionTotals : null}
            />
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
              gap: 16,
              marginBottom: 16,
            }}
            className="dashboard-split"
          >
            <CitationsCard data={citations} rangeDays={viewWindow.days} />
            <LatencyDistributionCard data={latency} rangeDays={viewWindow.days} />
          </div>

          <div style={{ marginBottom: 16 }}>
            <EnrichmentCard data={enrichment} rangeDays={viewWindow.days} />
          </div>

          <ModelUsageCard
            rows={usageByModel}
            rangeDays={viewWindow.days}
            priorRows={compare ? priorUsageByModel : null}
          />
        </>
      )}
    </>
  );
}
