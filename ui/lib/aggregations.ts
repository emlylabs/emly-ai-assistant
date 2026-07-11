/* Client-side aggregation helpers used by the dashboard, workspace overview,
 * and analytics screens. None of these should ever talk to the network — they
 * exist purely so the UI can derive time-series, deltas, and relative-time
 * formatting from data we already fetched.
 *
 * Why client-side: the backend exposes only aggregate snapshots (no daily
 * series in `metric_report`), so charts are derived from paginated message
 * lists. See ui-overhaul.md "Verified data surface".
 */

/** Pad a number to a 2-character string. `padDay(3)` → `"03"`. */
function padDay(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** ISO date prefix in UTC: `2026-05-04`. Used as a stable bucket key for
 * day-grouping. */
function toIsoDay(d: Date): string {
  const y = d.getUTCFullYear();
  const m = padDay(d.getUTCMonth() + 1);
  const day = padDay(d.getUTCDate());
  return `${y}-${m}-${day}`;
}

/** Build an array of the last `n` UTC day keys ending at `today`. */
export function lastNDayKeys(n: number, today: Date = new Date()): string[] {
  const keys: string[] = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    keys.push(toIsoDay(d));
  }
  return keys;
}

/** Short axis label for a day bucket, e.g. `Apr 26`. */
export function shortDayLabel(isoDay: string): string {
  const [, m, d] = isoDay.split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[m - 1]} ${padDay(d)}`;
}

/** Group ISO timestamps into per-UTC-day counts over the last `n` days.
 * Returns an array of length `n` aligned with `lastNDayKeys(n)`. */
export function bucketByDay(
  timestamps: (string | null | undefined)[],
  n: number,
  today: Date = new Date(),
): { keys: string[]; counts: number[] } {
  const keys = lastNDayKeys(n, today);
  const index = new Map<string, number>();
  keys.forEach((k, i) => index.set(k, i));

  const counts = new Array<number>(n).fill(0);
  for (const ts of timestamps) {
    if (!ts) continue;
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) continue;
    const key = toIsoDay(d);
    const slot = index.get(key);
    if (slot !== undefined) counts[slot] += 1;
  }
  return { keys, counts };
}

/** Group ISO timestamps into per-UTC-day **distinct** counts over a key.
 * E.g. unique `user_id` per day. */
export function bucketDistinctByDay(
  rows: { ts: string | null | undefined; key: string | null | undefined }[],
  n: number,
  today: Date = new Date(),
): { keys: string[]; counts: number[] } {
  const keys = lastNDayKeys(n, today);
  const index = new Map<string, number>();
  keys.forEach((k, i) => index.set(k, i));

  const seenPerDay: Set<string>[] = keys.map(() => new Set<string>());
  for (const row of rows) {
    if (!row.ts || !row.key) continue;
    const d = new Date(row.ts);
    if (Number.isNaN(d.getTime())) continue;
    const slot = index.get(toIsoDay(d));
    if (slot === undefined) continue;
    seenPerDay[slot].add(row.key);
  }
  const counts = seenPerDay.map((set) => set.size);
  return { keys, counts };
}

/** Format an ISO timestamp as a short relative duration: `12m`, `4h`, `3d`,
 * `2w`, `Apr 26`. Mirrors the mockup's compact feed timestamps. */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const ms = now.getTime() - d.getTime();
  if (ms < 0) return "just now";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d`;
  if (day < 30) return `${Math.floor(day / 7)}w`;
  return shortDayLabel(toIsoDay(d));
}

/** Short clock time `HH:mm` for a recent timestamp. Used by the conversations
 * list to mirror the mockup's row times. */
export function shortClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const h = padDay(d.getHours());
  const m = padDay(d.getMinutes());
  return `${h}:${m}`;
}

/** Compute a percent-change delta string from two scalars. Returns null when
 * the previous value is zero (we don't lie with `Infinity%`). */
export function percentDelta(current: number, previous: number): { value: string; direction: "up" | "down" | "neutral" } | null {
  if (previous === 0) return null;
  const pct = ((current - previous) / previous) * 100;
  if (Math.abs(pct) < 0.05) {
    return { value: "0.0%", direction: "neutral" };
  }
  const sign = pct > 0 ? "+" : "";
  return {
    value: `${sign}${pct.toFixed(1)}%`,
    direction: pct > 0 ? "up" : "down",
  };
}

/** Compute a "split last week vs prior week" delta from a daily series.
 * Returns null when either window is empty. */
export function weekOverWeekDelta(daily: number[]): { value: string; direction: "up" | "down" | "neutral" } | null {
  if (daily.length < 14) return null;
  const last = daily.slice(-7).reduce((a, b) => a + b, 0);
  const prior = daily.slice(-14, -7).reduce((a, b) => a + b, 0);
  return percentDelta(last, prior);
}
