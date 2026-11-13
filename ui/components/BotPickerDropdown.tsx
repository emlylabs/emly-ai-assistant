"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ChevronDown, Pin, PinOff, Search } from "lucide-react";
import type { Bot } from "@/lib/api";
import { api } from "@/lib/api";
import StatusPill from "@/components/StatusPill";
import {
  noteRecentSlug,
  readPinnedSlugs,
  readRecentSlugs,
  togglePinnedSlug,
} from "@/lib/bot-prefs";

type BotPickerDropdownProps = {
  /** The bot the user is currently viewing. `null` when the picker is
   * rendered from a workspace-level page (no active bot). */
  current?: { slug: string; name: string } | null;
  /** Full list of bots the admin can switch to. `null` means still loading. */
  bots: Bot[] | null;
  /** Active admin id, used to namespace localStorage pins and recents. */
  adminId?: string | null;
  /** Called when the user picks a bot. */
  onSwitch: (slug: string) => void;
  /** Override the trigger label (used when no `current` bot — e.g. the
   * workspace "Go to bot…" affordance in AdminShell). */
  triggerLabel?: string;
};

const SEARCH_THRESHOLD = 10;

/**
 * Bot switcher rendered either inline inside a breadcrumb (BotShell) or as
 * a standalone "Go to bot…" affordance in the workspace top bar (AdminShell).
 *
 * Adds three things on top of the original dropdown:
 *   1. A live status pill per row (Live / Paused).
 *   2. Pinned + Recent sections backed by localStorage, so a user with 20
 *      bots can find their daily three without scrolling.
 *   3. An inline search input when the list exceeds {@link SEARCH_THRESHOLD}.
 */
export default function BotPickerDropdown({
  current,
  bots,
  adminId,
  onSwitch,
  triggerLabel,
}: BotPickerDropdownProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pins, setPins] = useState<string[]>([]);
  const [recents, setRecents] = useState<string[]>([]);
  // Error counts per slug. Lazily fetched on first open (and refreshed
  // after the cache TTL) so opening the picker on the cold path doesn't
  // block on a round-trip. ``null`` = not yet fetched, missing slug = 0.
  const [errorsBySlug, setErrorsBySlug] = useState<Record<string, number> | null>(null);
  const lastFetchRef = useRef<number>(0);
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Read pins + recents from localStorage when the menu opens. Doing this
  // on-open (rather than on-mount) means a pin made in one tab is reflected
  // in another tab's next open without needing a storage event listener.
  useEffect(() => {
    if (!open) return;
    setPins(readPinnedSlugs(adminId));
    setRecents(readRecentSlugs(adminId));
    setQuery("");
    // Lazy-load the validation summary on open. Server caches by
    // (bot_id, config_version) so this is cheap on warm caches; we add
    // a 30-second client-side floor so rapid open/close doesn't fan out.
    const now = Date.now();
    if (errorsBySlug === null || now - lastFetchRef.current > 30_000) {
      lastFetchRef.current = now;
      api
        .listBotsValidationSummary()
        .then((rows) => {
          const map: Record<string, number> = {};
          for (const r of rows) map[r.slug] = r.errors;
          setErrorsBySlug(map);
        })
        .catch(() => {
          // Picker still works without health dots — leave errorsBySlug
          // unchanged so the dot is hidden rather than misleading.
        });
    }
    // ``errorsBySlug`` is intentionally read but not in the dep array —
    // we only want to refetch on open transitions, not on state updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, adminId]);

  // Auto-focus the search input on open when it's rendered.
  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  // Close on outside click. Listening on `pointerdown` instead of `click`
  // so the menu disappears as soon as the user reaches outside, before the
  // upstream page handler fires.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function handlePick(targetSlug: string) {
    setOpen(false);
    if (targetSlug !== current?.slug) {
      noteRecentSlug(adminId, targetSlug);
      onSwitch(targetSlug);
    }
  }

  function handleTogglePin(e: React.MouseEvent, targetSlug: string) {
    e.stopPropagation();
    setPins(togglePinnedSlug(adminId, targetSlug));
  }

  const sections = useMemo(
    () => buildSections(bots, pins, recents, query, current?.slug ?? null),
    [bots, pins, recents, query, current?.slug],
  );

  const showSearch = (bots?.length ?? 0) > SEARCH_THRESHOLD;
  const label = triggerLabel ?? current?.name ?? "Go to bot…";

  return (
    <span className="bot-picker" ref={wrapperRef}>
      <button
        type="button"
        className="bot-picker-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{label}</span>
        <ChevronDown
          size={13}
          strokeWidth={1.75}
          className="bot-picker-caret"
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="bot-picker-popover" role="listbox" aria-label="Switch bot">
          {showSearch && (
            <div className="bot-picker-search">
              <Search size={12} strokeWidth={1.75} aria-hidden="true" />
              <input
                ref={inputRef}
                type="search"
                placeholder="Search bots…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search bots"
              />
            </div>
          )}
          {bots === null ? (
            <div className="bot-picker-empty">Loading bots…</div>
          ) : bots.length === 0 ? (
            <div className="bot-picker-empty">No bots available</div>
          ) : sections.every((s) => s.items.length === 0) ? (
            <div className="bot-picker-empty">No matches</div>
          ) : (
            <ul className="bot-picker-list">
              {sections.map((section) =>
                section.items.length === 0 ? null : (
                  <li key={section.title} className="bot-picker-section">
                    <div className="bot-picker-section-header">{section.title}</div>
                    <ul>
                      {section.items.map((b) => {
                        const active = b.slug === current?.slug;
                        const pinned = pins.includes(b.slug);
                        const errorCount = errorsBySlug?.[b.slug] ?? 0;
                        return (
                          <li key={b.id}>
                            <button
                              type="button"
                              className={
                                active ? "bot-picker-item active" : "bot-picker-item"
                              }
                              role="option"
                              aria-selected={active}
                              onClick={() => handlePick(b.slug)}
                            >
                              <span className="bot-picker-item-main">
                                <span className="bot-picker-item-name">
                                  {b.name}
                                </span>
                                <span className="bot-picker-item-slug">
                                  {b.slug}
                                </span>
                              </span>
                              <span className="bot-picker-item-meta">
                                {errorCount > 0 && (
                                  <span
                                    className="health-bad"
                                    title={`${errorCount} config error${errorCount === 1 ? "" : "s"}`}
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: 2,
                                      fontSize: 11,
                                    }}
                                  >
                                    <AlertTriangle size={11} strokeWidth={1.75} />
                                    {errorCount}
                                  </span>
                                )}
                                <StatusPill
                                  variant={b.is_active ? "success" : "neutral"}
                                >
                                  {b.is_active ? "Live" : "Paused"}
                                </StatusPill>
                                <span
                                  role="button"
                                  tabIndex={0}
                                  aria-label={pinned ? "Unpin bot" : "Pin bot"}
                                  className="bot-picker-pin"
                                  onClick={(e) => handleTogglePin(e, b.slug)}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                      e.preventDefault();
                                      handleTogglePin(
                                        e as unknown as React.MouseEvent,
                                        b.slug,
                                      );
                                    }
                                  }}
                                  title={pinned ? "Unpin" : "Pin to top"}
                                >
                                  {pinned ? (
                                    <PinOff size={12} strokeWidth={1.75} />
                                  ) : (
                                    <Pin size={12} strokeWidth={1.75} />
                                  )}
                                </span>
                                {active && (
                                  <Check
                                    size={13}
                                    strokeWidth={2}
                                    aria-hidden="true"
                                  />
                                )}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                ),
              )}
            </ul>
          )}
        </div>
      )}
    </span>
  );
}

type Section = { title: string; items: Bot[] };

/**
 * Bucket bots into Pinned → Recent → All, dropping duplicates as we go so
 * the same row never appears twice. When a search query is present we
 * skip the bucketing and present one flat "Results" list.
 */
function buildSections(
  bots: Bot[] | null,
  pins: string[],
  recents: string[],
  query: string,
  currentSlug: string | null,
): Section[] {
  if (!bots) return [];
  const q = query.trim().toLowerCase();
  const matches = (b: Bot) =>
    !q ||
    b.name.toLowerCase().includes(q) ||
    b.slug.toLowerCase().includes(q) ||
    (b.tags ?? []).some((t) => t.toLowerCase().includes(q));

  if (q) {
    return [{ title: "Results", items: bots.filter(matches) }];
  }

  const bySlug = new Map(bots.map((b) => [b.slug, b]));
  const seen = new Set<string>();
  const pinned: Bot[] = [];
  for (const slug of pins) {
    const b = bySlug.get(slug);
    if (b && !seen.has(slug)) {
      pinned.push(b);
      seen.add(slug);
    }
  }
  const recent: Bot[] = [];
  for (const slug of recents) {
    if (seen.has(slug)) continue;
    if (slug === currentSlug) continue;
    const b = bySlug.get(slug);
    if (b) {
      recent.push(b);
      seen.add(slug);
    }
  }
  const remaining = bots.filter((b) => !seen.has(b.slug));

  return [
    { title: "Pinned", items: pinned },
    { title: "Recent", items: recent },
    { title: "All bots", items: remaining },
  ];
}
