"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  ArrowRight,
  BarChart3,
  Bot as BotIcon,
  FileText,
  LayoutDashboard,
  MessagesSquare,
  Plus,
  Radio,
  Search,
  ScrollText,
  Settings,
  SlidersHorizontal,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { Bot } from "@/lib/api";
import { api } from "@/lib/api";
import { readLastTab, readPinnedSlugs, readRecentSlugs } from "@/lib/bot-prefs";
import { markModalOpen } from "@/components/KeyboardShortcuts";
import { useFocusTrap } from "@/lib/focus-trap";

type CommandPaletteProps = {
  /** Active admin id — needed for pin/recent ordering. */
  adminId: string | null | undefined;
  /** Current bot slug, when the palette is mounted inside BotShell.
   * `null` when the user is on a workspace-level page. Drives the
   * "Jump to … on this bot" tab commands. */
  currentBotSlug?: string | null;
  /** Open the create-bot wizard. The shell wires this to the same modal
   * the /bots page uses; passing it as a prop avoids cross-shell drift. */
  onCreateBot?: () => void;
};

type Command = {
  id: string;
  label: string;
  /** Sub-text shown muted next to the label. */
  hint?: string;
  /** Section grouping, used as the heading above the row. */
  section: string;
  icon: LucideIcon;
  /** Free-form keywords appended to label+hint when matching the query. */
  keywords?: string;
  run: () => void;
};

// Tabs per bot, kept in sync with BotShell's nav. Duplicated rather than
// imported to keep BotShell self-contained — the palette is the only other
// place that needs to know the full list.
const PER_BOT_TABS: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "analytics", label: "Analytics", icon: BarChart3 },
  { href: "conversations", label: "Conversations", icon: MessagesSquare },
  { href: "files", label: "Knowledge", icon: FileText },
  { href: "retrieval", label: "Retrieval", icon: Search },
  { href: "channels", label: "Channels", icon: Radio },
  { href: "config", label: "Config", icon: SlidersHorizontal },
  { href: "users", label: "Bot users", icon: Users },
  { href: "members", label: "Members", icon: UserCog },
  { href: "audit", label: "Audit", icon: ScrollText },
  { href: "settings", label: "Settings", icon: Settings },
];

/**
 * Global Cmd/Ctrl-K command palette. Mounted once per shell — the listener
 * is attached to ``document`` so the trigger keystroke fires anywhere in
 * the admin UI. Commands cover:
 *
 *   - Jump to bot (matches name, slug, tags). Pinned + recent bots rank
 *     above the alphabetical list when there is no query.
 *   - Jump to a specific tab on the *current* bot.
 *   - Create a new bot (invokes the wizard hook the shell passes in).
 *
 * The list of bots is fetched lazily on first open so the palette never
 * blocks first paint. After that we keep the cached list around for the
 * lifetime of the shell — it's tiny (one network round-trip on a 20-bot
 * workspace returns under 2 KB).
 */
export default function CommandPalette({
  adminId,
  currentBotSlug,
  onCreateBot,
}: CommandPaletteProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [bots, setBots] = useState<Bot[] | null>(null);
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // While the palette is open, the keyboard-shortcuts provider should
  // pause its single-key dispatch (so typing into the search field
  // doesn't fire `c` for create-bot). The provider already excludes
  // inputs, but flipping the modal flag is the explicit contract.
  useEffect(() => {
    markModalOpen(open);
    return () => markModalOpen(false);
  }, [open]);

  // Tab cycles within the dialog: search input → command items → back to
  // the search input. Esc is still handled inline (below) so the existing
  // close-on-Escape semantics stay identical.
  useFocusTrap({ open, containerRef: dialogRef });

  // Toggle on Cmd/Ctrl-K. Ignore the keystroke when it would otherwise
  // get swallowed by an interactive native control (search bar inputs
  // explicitly want Cmd-K too, so we don't whitelist tag names — just
  // always intercept and override the default browser-find shortcut).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isMeta = e.metaKey || e.ctrlKey;
      if (isMeta && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((v) => !v);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // Lazy-load bots on first open. Cheap retry on next open if the previous
  // call failed (no exponential backoff — palette opens are user-driven).
  useEffect(() => {
    if (!open) return;
    if (bots === null) {
      api
        .listBots()
        .then(setBots)
        .catch(() => setBots([]));
    }
  }, [open, bots]);

  // Reset selection + focus search when the palette opens.
  useEffect(() => {
    if (!open) return;
    setSelected(0);
    setQuery("");
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  const commands = useMemo<Command[]>(
    () => buildCommands({ bots, adminId, currentBotSlug, query, onCreateBot, router, pathname }),
    [bots, adminId, currentBotSlug, query, onCreateBot, router, pathname],
  );

  // Keep the selected item within bounds whenever the filtered list shrinks.
  useEffect(() => {
    if (selected >= commands.length) setSelected(Math.max(0, commands.length - 1));
  }, [commands.length, selected]);

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((i) => Math.min(commands.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const cmd = commands[selected];
      if (cmd) {
        setOpen(false);
        cmd.run();
      }
    }
  }

  // Group commands by section while preserving the order they were built.
  const grouped = useMemo(() => groupCommands(commands), [commands]);

  if (!open) return null;

  return (
    <div
      className="cmdk-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      onMouseDown={(e) => {
        // Close on outside click only — clicks inside the dialog bubble up,
        // and we filter those via `target === currentTarget`.
        if (e.target === e.currentTarget) setOpen(false);
      }}
    >
      <div className="cmdk-dialog" ref={dialogRef} onKeyDown={handleKey}>
        <div className="cmdk-search">
          <Search size={14} strokeWidth={1.75} aria-hidden="true" />
          <input
            ref={inputRef}
            type="search"
            placeholder="Jump to a bot, tab, or action…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
            aria-label="Command palette query"
          />
          <kbd>ESC</kbd>
        </div>
        <div className="cmdk-list" ref={listRef} role="listbox">
          {commands.length === 0 ? (
            <div className="cmdk-empty">
              {bots === null ? "Loading bots…" : "No matches"}
            </div>
          ) : (
            grouped.map(({ section, items }) => (
              <div key={section} className="cmdk-section">
                <div className="cmdk-section-header">{section}</div>
                {items.map((cmd) => {
                  const idx = commands.indexOf(cmd);
                  const active = idx === selected;
                  return (
                    <button
                      key={cmd.id}
                      type="button"
                      className={active ? "cmdk-item active" : "cmdk-item"}
                      role="option"
                      aria-selected={active}
                      onMouseEnter={() => setSelected(idx)}
                      onClick={() => {
                        setOpen(false);
                        cmd.run();
                      }}
                    >
                      <cmd.icon size={14} strokeWidth={1.75} aria-hidden="true" />
                      <span className="cmdk-item-label">{cmd.label}</span>
                      {cmd.hint && (
                        <span className="cmdk-item-hint">{cmd.hint}</span>
                      )}
                      <ArrowRight
                        size={12}
                        strokeWidth={1.75}
                        className="cmdk-item-arrow"
                        aria-hidden="true"
                      />
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

type BuildArgs = {
  bots: Bot[] | null;
  adminId: string | null | undefined;
  currentBotSlug: string | null | undefined;
  query: string;
  onCreateBot: (() => void) | undefined;
  router: ReturnType<typeof useRouter>;
  pathname: string | null;
};

function buildCommands({
  bots,
  adminId,
  currentBotSlug,
  query,
  onCreateBot,
  router,
  pathname,
}: BuildArgs): Command[] {
  const q = query.trim().toLowerCase();
  const cmds: Command[] = [];

  // 1. Create new bot — first because it's a verb, and rarely typed by
  //    accident. Only appears when the shell provides a handler (the /bots
  //    page does; the per-bot shell doesn't — there we send the user to
  //    /bots which itself can show the wizard).
  if (onCreateBot) {
    cmds.push({
      id: "create-bot",
      label: "Create new bot…",
      section: "Actions",
      icon: Plus,
      keywords: "new make wizard",
      run: () => onCreateBot(),
    });
  } else {
    cmds.push({
      id: "create-bot-fallback",
      label: "Create new bot…",
      section: "Actions",
      icon: Plus,
      keywords: "new make wizard",
      run: () => router.push("/bots?new=1"),
    });
  }

  // 2. Tabs on the current bot. Surfaced as "Open <Tab> on this bot" so the
  //    user can fan out without leaving the keyboard.
  if (currentBotSlug) {
    for (const tab of PER_BOT_TABS) {
      const href = `/bots/${currentBotSlug}/${tab.href}`;
      if (pathname === href) continue;
      cmds.push({
        id: `tab-${tab.href}`,
        label: `${tab.label}`,
        hint: "on this bot",
        section: "This bot",
        icon: tab.icon,
        keywords: `${tab.href} current`,
        run: () => router.push(href),
      });
    }
  }

  // 3. Jump to bot. Pinned + recent bots rank above the alphabetical tail
  //    when the user hasn't typed anything yet; once they're searching, we
  //    drop the bucketing and let the matcher score determine order.
  if (bots) {
    const pinned = new Set(readPinnedSlugs(adminId));
    const recent = readRecentSlugs(adminId);
    const recentIndex = new Map(recent.map((s, i) => [s, i]));
    const matched = bots.filter((b) => {
      if (b.slug === currentBotSlug) return false;
      if (!q) return true;
      return (
        b.name.toLowerCase().includes(q) ||
        b.slug.toLowerCase().includes(q) ||
        (b.tags ?? []).some((t) => t.toLowerCase().includes(q))
      );
    });
    matched.sort((a, b) => {
      if (!q) {
        const ap = pinned.has(a.slug) ? 0 : 1;
        const bp = pinned.has(b.slug) ? 0 : 1;
        if (ap !== bp) return ap - bp;
        const ar = recentIndex.has(a.slug) ? recentIndex.get(a.slug)! : Infinity;
        const br = recentIndex.has(b.slug) ? recentIndex.get(b.slug)! : Infinity;
        if (ar !== br) return ar - br;
      }
      return a.name.localeCompare(b.name);
    });
    for (const b of matched) {
      cmds.push({
        id: `bot-${b.slug}`,
        label: b.name,
        hint: b.slug,
        section: "Jump to bot",
        icon: BotIcon,
        keywords: (b.tags ?? []).join(" "),
        run: () => {
          const tab = readLastTab(adminId, b.slug) || "dashboard";
          router.push(`/bots/${b.slug}/${tab}`);
        },
      });
    }
  }

  if (!q) return cmds;

  // Final cheap matcher: substring on label+hint+keywords. Keep it boring;
  // we'll graduate to a scorer only if it becomes a usability problem.
  return cmds.filter((c) => {
    const hay = `${c.label} ${c.hint ?? ""} ${c.keywords ?? ""}`.toLowerCase();
    return hay.includes(q);
  });
}

function groupCommands(commands: Command[]): { section: string; items: Command[] }[] {
  const order: string[] = [];
  const buckets = new Map<string, Command[]>();
  for (const c of commands) {
    if (!buckets.has(c.section)) {
      buckets.set(c.section, []);
      order.push(c.section);
    }
    buckets.get(c.section)!.push(c);
  }
  return order.map((section) => ({ section, items: buckets.get(section)! }));
}
