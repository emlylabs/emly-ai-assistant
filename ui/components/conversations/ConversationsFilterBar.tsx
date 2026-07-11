"use client";

import { useEffect, useState } from "react";
import { Filter, X } from "lucide-react";
import type { SessionRatingFilter } from "@/lib/api";

export type ResolvedFilter = "all" | "resolved" | "unresolved";
export type RatingFilter = "any" | SessionRatingFilter;

export type ConversationFilters = {
  sessionId: string;
  userId: string;
  /** YYYY-MM-DD from the date input — converted to ISO before sending. */
  startedFrom: string;
  startedTo: string;
  resolved: ResolvedFilter;
  rating: RatingFilter;
};

export const EMPTY_FILTERS: ConversationFilters = {
  sessionId: "",
  userId: "",
  startedFrom: "",
  startedTo: "",
  resolved: "all",
  rating: "any",
};

export function isEmptyFilters(f: ConversationFilters): boolean {
  return (
    !f.sessionId &&
    !f.userId &&
    !f.startedFrom &&
    !f.startedTo &&
    f.resolved === "all" &&
    f.rating === "any"
  );
}

type ConversationsFilterBarProps = {
  value: ConversationFilters;
  onChange: (next: ConversationFilters) => void;
};

/**
 * Horizontal filter bar above the conversations list. All filters compose
 * conjunctively and route through to the sessions endpoint. Text inputs
 * commit on blur/Enter (300ms debounce); selects and dates commit
 * immediately. Page reset to 0 is the parent's responsibility — this
 * component is purely controlled.
 */
export default function ConversationsFilterBar({
  value,
  onChange,
}: ConversationsFilterBarProps) {
  // Mirror the text inputs locally so we can debounce without losing keystrokes.
  const [sessionId, setSessionId] = useState(value.sessionId);
  const [userId, setUserId] = useState(value.userId);

  useEffect(() => setSessionId(value.sessionId), [value.sessionId]);
  useEffect(() => setUserId(value.userId), [value.userId]);

  useEffect(() => {
    if (sessionId === value.sessionId) return;
    const t = setTimeout(() => onChange({ ...value, sessionId: sessionId.trim() }), 300);
    return () => clearTimeout(t);
  }, [sessionId, value, onChange]);

  useEffect(() => {
    if (userId === value.userId) return;
    const t = setTimeout(() => onChange({ ...value, userId: userId.trim() }), 300);
    return () => clearTimeout(t);
  }, [userId, value, onChange]);

  const cleared = isEmptyFilters(value);

  return (
    <div className="conv-filter-bar">
      <div className="conv-filter-label">
        <Filter size={13} strokeWidth={1.75} />
        <span>Filter</span>
      </div>

      <input
        type="text"
        placeholder="Session id"
        value={sessionId}
        onChange={(e) => setSessionId(e.target.value)}
        spellCheck={false}
      />

      <input
        type="text"
        placeholder="User id"
        value={userId}
        onChange={(e) => setUserId(e.target.value)}
        spellCheck={false}
      />

      <input
        type="date"
        value={value.startedFrom}
        onChange={(e) => onChange({ ...value, startedFrom: e.target.value })}
        title="Started on or after"
        aria-label="Started after"
      />

      <input
        type="date"
        value={value.startedTo}
        onChange={(e) => onChange({ ...value, startedTo: e.target.value })}
        title="Started on or before"
        aria-label="Started before"
      />

      <select
        className="ghost compact"
        value={value.resolved}
        onChange={(e) =>
          onChange({ ...value, resolved: e.target.value as ResolvedFilter })
        }
        title="Filter by resolution status"
      >
        <option value="all">Any status</option>
        <option value="resolved">Resolved</option>
        <option value="unresolved">Unresolved</option>
      </select>

      <select
        className="ghost compact"
        value={value.rating}
        onChange={(e) =>
          onChange({ ...value, rating: e.target.value as RatingFilter })
        }
        title="Filter by message rating in the session"
      >
        <option value="any">Any rating</option>
        <option value="rated">Rated</option>
        <option value="unrated">Unrated</option>
        <option value="positive">Positive</option>
        <option value="negative">Negative</option>
      </select>

      <button
        type="button"
        className="ghost compact"
        disabled={cleared}
        onClick={() => onChange(EMPTY_FILTERS)}
        title="Clear all filters"
      >
        <X size={12} strokeWidth={2} />
        Clear
      </button>
    </div>
  );
}

/**
 * Translate the UI's filter state into the query params the backend
 * sessions endpoint expects. Empty values drop out so they don't
 * widen the URL needlessly. Date strings are interpreted in the
 * browser's local timezone and converted to ISO-8601 — a `from` date
 * captures the start of that day, a `to` date captures the end.
 */
export function filtersToQuery(f: ConversationFilters): {
  session_id?: string;
  user_id?: string;
  started_after?: string;
  started_before?: string;
  is_resolved?: boolean;
  rating?: SessionRatingFilter;
} {
  const out: ReturnType<typeof filtersToQuery> = {};
  if (f.sessionId) out.session_id = f.sessionId;
  if (f.userId) out.user_id = f.userId;
  if (f.startedFrom) {
    const d = new Date(`${f.startedFrom}T00:00:00`);
    if (!Number.isNaN(d.getTime())) out.started_after = d.toISOString();
  }
  if (f.startedTo) {
    const d = new Date(`${f.startedTo}T23:59:59.999`);
    if (!Number.isNaN(d.getTime())) out.started_before = d.toISOString();
  }
  if (f.resolved === "resolved") out.is_resolved = true;
  else if (f.resolved === "unresolved") out.is_resolved = false;
  if (f.rating !== "any") out.rating = f.rating;
  return out;
}

/**
 * Encode the filter UI state as a flat record suitable for a URL query
 * string. Keys are intentionally human-friendly (`from`/`to`/`status`)
 * rather than the backend's (`started_after`/`is_resolved`) so a shared
 * link reads cleanly. Only non-default keys are emitted.
 */
export function filtersToUrlParams(
  f: ConversationFilters,
): Record<string, string> {
  const out: Record<string, string> = {};
  if (f.sessionId) out.session_id = f.sessionId;
  if (f.userId) out.user_id = f.userId;
  if (f.startedFrom) out.from = f.startedFrom;
  if (f.startedTo) out.to = f.startedTo;
  if (f.resolved !== "all") out.status = f.resolved;
  if (f.rating !== "any") out.rating = f.rating;
  return out;
}

function isResolvedFilter(v: string | null): v is ResolvedFilter {
  return v === "all" || v === "resolved" || v === "unresolved";
}

function isRatingFilter(v: string | null): v is RatingFilter {
  return (
    v === "any" ||
    v === "rated" ||
    v === "unrated" ||
    v === "positive" ||
    v === "negative"
  );
}

/**
 * Inverse of `filtersToUrlParams` — read a snapshot from anything
 * with `.get(key)` (URLSearchParams or ReadonlyURLSearchParams). Unknown
 * or malformed values fall back to the empty default so a hand-crafted
 * URL can't push the UI into an invalid state.
 */
export function filtersFromUrlParams(
  sp: { get(key: string): string | null },
): ConversationFilters {
  const status = sp.get("status");
  const rating = sp.get("rating");
  return {
    sessionId: (sp.get("session_id") ?? "").trim(),
    userId: (sp.get("user_id") ?? "").trim(),
    startedFrom: (sp.get("from") ?? "").trim(),
    startedTo: (sp.get("to") ?? "").trim(),
    resolved: isResolvedFilter(status) ? status : "all",
    rating: isRatingFilter(rating) ? rating : "any",
  };
}
