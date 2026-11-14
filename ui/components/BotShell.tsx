"use client";

import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import {
  BarChart3,
  FileText,
  LayoutDashboard,
  Menu,
  MessagesSquare,
  Radio,
  ScrollText,
  Search,
  Settings,
  SlidersHorizontal,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";
import {
  AdminUser,
  ApiError,
  Bot,
  Membership,
  Role,
  api,
  forceRefreshClaims,
  loginUrl,
  startProactiveRefresh,
} from "@/lib/api";
import SystemBanners from "@/components/SystemBanners";
import SidebarFooter from "@/components/SidebarFooter";
import TopBar from "@/components/TopBar";
import BottomNav, { type BottomNavItem } from "@/components/BottomNav";
import Crumbs from "@/components/Crumbs";
import BotPickerDropdown from "@/components/BotPickerDropdown";
import CommandPalette from "@/components/CommandPalette";
import Tooltip from "@/components/Tooltip";
import { useShortcut, useShortcuts } from "@/components/KeyboardShortcuts";
import { noteRecentSlug, readLastTab, writeLastTab } from "@/lib/bot-prefs";
import { useSwipeLeftToClose } from "@/lib/useSwipeLeftToClose";

// Per-bot navigation. Each tab knows its slug from the URL.
//
// Phase 2 of the UI overhaul splits the nav into two semantic groups so the
// sidebar reads as "what the bot does" (Workspace) vs "how the bot is set
// up" (Configure). Phase 7 will insert "Analytics" into WORKSPACE_NAV; we
// don't add it now because the route doesn't exist yet — broken nav links
// hurt more than missing labels.
type NavItem = { href: string; label: string; icon: LucideIcon; chord?: string };

const WORKSPACE_NAV: NavItem[] = [
  { href: "dashboard",     label: "Dashboard",     icon: LayoutDashboard,   chord: "g d" },
  { href: "analytics",     label: "Analytics",     icon: BarChart3,         chord: "g a" },
  { href: "conversations", label: "Conversations", icon: MessagesSquare,    chord: "g v" },
  { href: "files",         label: "Knowledge",     icon: FileText,          chord: "g k" },
  { href: "retrieval",     label: "Retrieval",     icon: Search,            chord: "g r" },
  { href: "channels",      label: "Channels",      icon: Radio,             chord: "g h" },
];

const CONFIGURE_NAV: NavItem[] = [
  { href: "config",        label: "Config",        icon: SlidersHorizontal, chord: "g c" },
  { href: "users",         label: "Bot users",     icon: Users,             chord: "g u" },
  { href: "members",       label: "Members",       icon: UserCog,           chord: "g m" },
  { href: "audit",         label: "Audit",         icon: ScrollText },
  { href: "settings",      label: "Settings",      icon: Settings,          chord: "g s" },
];

/** Combined list, used to look up the current tab's friendly label for the
 * breadcrumbs and to build the bot picker behaviour. */
const ALL_NAV: NavItem[] = [...WORKSPACE_NAV, ...CONFIGURE_NAV];

const COLLAPSE_KEY = "bf-sidebar-collapsed";

function readCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

type BotContextValue = {
  bot: Bot;
  /** The active admin's role on this bot. `null` while loading. */
  currentRole: Role | null;
  /** Trigger a fresh fetch of the bot row (e.g., after a config save). */
  refreshBot: () => Promise<void>;
};

const BotContext = createContext<BotContextValue | null>(null);

export function useBot(): BotContextValue {
  const ctx = useContext(BotContext);
  if (!ctx) throw new Error("useBot() must be called inside a BotShell");
  return ctx;
}

export function useCurrentRole(): Role | null {
  return useBot().currentRole;
}

export default function BotShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  // Static export gotcha: `useParams().slug` returns the build-time
  // placeholder "_" (the value we passed to `generateStaticParams`).
  // The real slug lives in the URL — read it directly. Pathname looks
  // like `/bots/<slug>/<tab>`; the second segment is the slug.
  const slug = (pathname?.split("/")[2] ?? "").trim();

  const [me, setMe] = useState<AdminUser | null>(null);
  const [bot, setBot] = useState<Bot | null>(null);
  const [currentRole, setCurrentRole] = useState<Role | null>(null);
  const [allBots, setAllBots] = useState<Bot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  // Tracks the slug of the request currently in flight. If the user
  // hops bots quickly (A → B → A), in-flight fetches for older slugs
  // resolve after newer ones; we ignore those stale resolutions
  // rather than letting them clobber the visible bot state.
  const activeSlugRef = useRef<string>(slug);

  function toggleCollapse() {
    setCollapsed((v) => {
      const next = !v;
      try { localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0"); } catch {}
      return next;
    });
  }

  const loadBot = useCallback(async () => {
    activeSlugRef.current = slug;
    try {
      const fetched = await api.getBot(slug);
      if (activeSlugRef.current !== slug) return; // newer request supersedes us
      setBot(fetched);
      setNotFound(false);
      setError(null);
      // Resolve the active admin's role on this bot. The list endpoint
      // is the source of truth (JWT bot_ids claim is stale on grants).
      try {
        const memberships = await api.listBotAdmins(slug);
        if (activeSlugRef.current !== slug) return;
        const mine = me ? memberships.find((m) => m.admin_id === me.id) : null;
        setCurrentRole(mine?.role ?? null);
      } catch {
        if (activeSlugRef.current !== slug) return;
        // listBotAdmins requires membership; a 403 means viewer-or-less
        // — leave role null so destructive controls stay disabled.
        setCurrentRole(null);
      }
    } catch (err: unknown) {
      if (activeSlugRef.current !== slug) return;
      if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
        setNotFound(true);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load bot");
      }
    }
  }, [slug, me]);

  useEffect(() => {
    // Kick off the BFF proactive silent-refresh loop. The middleware
    // (or the FastAPI catchall in static-export prod) has already
    // guaranteed a session cookie is present by the time this
    // component mounts, so the timer will land on a real expiry.
    startProactiveRefresh();

    // Initial `me()` probe. The middleware already did the cheap
    // "is the cookie there?" check; this is the real verification
    // (signature + audience + claim shape). Non-blocking — we render
    // the empty shell with a "Loading…" state while it runs, and
    // bounce to /login if it 401s.
    api
      .me()
      .then(setMe)
      .catch((err: any) => {
        if (err?.status === 401) {
          // Preserve the deep-linked URL so the user lands back on the
          // same page (e.g. ?conversation=<id>) after signing in.
          const target = window.location.pathname + window.location.search;
          router.replace(`/login?return_to=${encodeURIComponent(target)}`);
          return;
        }
        setError(err?.message ?? "Failed to load profile");
      });
  }, [router]);

  // Track the slug we last successfully loaded so we can skip the
  // clear-and-refetch cycle when the slug hasn't actually changed
  // (e.g. navigating between tabs within the same bot).
  const loadedSlugRef = useRef<string>("");

  useEffect(() => {
    if (!me) return;
    // Only clear visible state when switching to a different bot.
    // Same-slug navigations (tab switches) keep the existing bot
    // data so the user doesn't see a "Loading bot…" flash.
    if (slug !== loadedSlugRef.current) {
      setBot(null);
      setCurrentRole(null);
      setNotFound(false);
      setError(null);
      loadedSlugRef.current = slug;
    }
    loadBot();
  }, [me, loadBot]);

  // Bot picker dropdown — list every bot the admin can access.
  useEffect(() => {
    if (!me) return;
    api
      .listBots()
      .then(setAllBots)
      .catch(() => setAllBots([]));
  }, [me]);

  // Close sidebar on navigation (mobile)
  useEffect(() => {
    setSidebarOpen(false);
  }, [pathname]);

  async function logout() {
    try {
      const res = await api.logout();
      if (res?.provider_logout_url) {
        window.location.href = res.provider_logout_url;
        return;
      }
    } catch {
      // fall through to login
    }
    router.replace("/login");
  }

  async function refreshPermissions() {
    // Hit the BFF in the background. The backend mints a fresh id_token
    // from the encrypted refresh_token cookie, rolls the session cookies
    // forward, and returns the resolved AdminUser so we can drop it
    // into state without bouncing through the IdP. If the refresh
    // fails (cookie expired / revoked), fall back to the full login
    // flow — that's the same fallback the 401 interceptor uses.
    try {
      const { admin } = await forceRefreshClaims();
      setMe(admin);
      // The bot list also depends on memberships; reload so any
      // newly-granted bot appears in the picker immediately.
      api
        .listBots()
        .then(setAllBots)
        .catch(() => setAllBots([]));
      // Force a re-fetch of the active bot too, since role
      // memberships drive destructive-control visibility.
      loadBot();
    } catch (err) {
      const target = window.location.pathname + window.location.search;
      window.location.href = loginUrl(target);
    }
  }

  function onSwitchBot(targetSlug: string) {
    if (!targetSlug || targetSlug === slug) return;
    // Prefer the user's last-viewed tab on the target bot (so switching to
    // a bot they were last reading conversations on doesn't bounce them to
    // its dashboard). Fall back to the current tab — same as before — when
    // we don't have a memory of where they were.
    const currentTab = pathname?.split("/").slice(3, 4)[0] ?? "dashboard";
    const remembered = readLastTab(me?.id, targetSlug);
    const tab = remembered || currentTab;
    noteRecentSlug(me?.id, targetSlug);
    router.push(`/bots/${targetSlug}/${tab}`);
  }

  // Per-bot `g <key>` jump shortcuts. Mirrors the sidebar tab order so
  // muscle memory builds with hand-eye: g d = dashboard, g a = analytics,
  // g v = conversations, g k = knowledge (files), g r = retrieval,
  // g h = channels, g c = config, g u = bot users, g m = members,
  // g s = settings.
  useShortcuts(
    [
      ["g d", "dashboard", "Dashboard"],
      ["g a", "analytics", "Analytics"],
      ["g v", "conversations", "Conversations"],
      ["g k", "files", "Knowledge"],
      ["g r", "retrieval", "Retrieval"],
      ["g h", "channels", "Channels"],
      ["g c", "config", "Config"],
      ["g u", "users", "Bot users"],
      ["g m", "members", "Members"],
      ["g s", "settings", "Settings"],
    ].map(([chord, tab, label]) => ({
      chord,
      description: `Jump to ${label}`,
      group: "bot" as const,
      when: () => Boolean(slug),
      handler: () => router.push(`/bots/${slug}/${tab}`),
    })),
  );
  useShortcut({
    chord: "c",
    description: "Create a new bot",
    group: "global",
    handler: () => router.push("/bots?new=1"),
  });
  useShortcut({
    chord: "/",
    description: "Focus the search input on this page",
    group: "global",
    handler: () => {
      const target =
        document.querySelector<HTMLElement>('input[type="search"]') ??
        document.querySelector<HTMLElement>('input[placeholder*="earch" i]');
      if (target) target.focus();
    },
  });

  // Remember the last tab the user visited on this bot so the next time
  // they switch to it the shell jumps back to the same screen.
  useEffect(() => {
    if (!me?.id || !slug) return;
    const tab = pathname?.split("/")[3];
    if (!tab) return;
    writeLastTab(me.id, slug, tab);
  }, [me?.id, slug, pathname]);

  // Note the bot in the recents list so the next picker open surfaces it.
  useEffect(() => {
    if (!me?.id || !slug) return;
    noteRecentSlug(me.id, slug);
  }, [me?.id, slug]);

  // Build the context value unconditionally so the hook count stays
  // stable across renders (Rules of Hooks). Consumers gate on the
  // ``bot`` field; before it's loaded, ``ctxValue.bot`` is the
  // sentinel below — no consumer reaches the conditional branches
  // before the loading early-returns fire below.
  const ctxValue: BotContextValue = useMemo(
    () => ({
      bot: (bot ?? ({} as Bot)),
      currentRole,
      refreshBot: loadBot,
    }),
    [bot, currentRole, loadBot]
  );

  // Hook order matters — must be called every render, before any of the
  // early-return branches below.
  const swipeHandlers = useSwipeLeftToClose(() => setSidebarOpen(false));

  // No blocking "Loading…" for the initial `me()` fetch — the
  // middleware (and the FastAPI catchall in static-export prod)
  // already guarantee a session cookie is present. The shell
  // renders immediately; ``me`` populates in the background. If the
  // server-side verifier rejects the cookie (signature, expiry, …)
  // the catch on the `me()` useEffect above redirects to /login.
  if (notFound) {
    return (
      <div className="center">
        <div className="card narrow">
          <p className="eyebrow">404</p>
          <h1 style={{ marginTop: 8 }}>Bot not found</h1>
          <p className="muted" style={{ marginTop: 12 }}>
            <code>{slug}</code> doesn&apos;t exist or you don&apos;t have access.
          </p>
          <p className="muted" style={{ marginTop: 12, fontSize: 13 }}>
            If a teammate just granted you access, your memberships are stale.
            Click below to roll the session forward and reload your bot list.
          </p>
          <div className="row" style={{ marginTop: 20 }}>
            <button onClick={() => router.replace("/bots")}>Back to bots</button>
            <button className="ghost" onClick={refreshPermissions} title="Roll the session cookies forward and reload memberships">
              Refresh to load new bots
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!bot && !error) {
    return (
      <div className="center">
        <p className="muted">Loading bot…</p>
      </div>
    );
  }

  const sidebarClasses = [
    "sidebar",
    sidebarOpen ? "open" : "",
    collapsed ? "collapsed" : "",
  ].filter(Boolean).join(" ");

  // Build the breadcrumbs strip from the current path. The active tab
  // segment is the third path part (`/bots/<slug>/<tab>`); we match it
  // against the combined nav to render a friendly label rather than the raw
  // route slug. Falls back to `Dashboard` if the user is at `/bots/<slug>`.
  const activeTab = (pathname?.split("/")[3] ?? "dashboard").trim();
  const activeLabel =
    ALL_NAV.find((n) => n.href === activeTab)?.label ??
    activeTab.charAt(0).toUpperCase() + activeTab.slice(1);

  const breadcrumbs = bot ? (
    <Crumbs
      items={[
        { label: "Workspace", href: "/bots" },
        {
          label: bot.name,
          // The bot-name crumb is the switcher trigger. We embed the
          // dropdown directly so there's no second `<select>` cluttering
          // the topbar — one source of truth, one click to change bots.
          node: (
            <BotPickerDropdown
              current={{ slug, name: bot.name }}
              bots={allBots}
              adminId={me?.id}
              onSwitch={onSwitchBot}
            />
          ),
        },
        { label: activeLabel },
      ]}
    />
  ) : null;

  function renderNavGroup(items: NavItem[]) {
    return items.map(({ href: tab, label, icon: Icon, chord }) => {
      const href = `/bots/${slug}/${tab}`;
      const active = pathname === href || pathname?.startsWith(`${href}/`);
      const link = (
        <Link
          href={href}
          className={active ? "nav-item active" : "nav-item"}
          aria-current={active ? "page" : undefined}
          aria-label={label}
        >
          <Icon strokeWidth={1.75} aria-hidden="true" />
          <span className="nav-item-label">{label}</span>
          {chord && !collapsed && (
            <span className="nav-chord-hint" aria-hidden="true">
              {chord}
            </span>
          )}
        </Link>
      );
      // Collapsed sidebar hides the inline label, so the tooltip becomes
      // load-bearing — both visually (so the icon means something on hover)
      // and for assistive tech (aria-describedby links the popover to the
      // link). Expanded sidebar already has the inline label; skip the
      // tooltip there.
      return collapsed ? (
        <Tooltip key={tab} label={label} placement="right">
          {link}
        </Tooltip>
      ) : (
        <span key={tab}>{link}</span>
      );
    });
  }

  return (
    <BotContext.Provider value={ctxValue}>
      <CommandPalette
        adminId={me?.id}
        currentBotSlug={slug}
        onCreateBot={() => router.push("/bots?new=1")}
      />
      <TopBar
        email={me?.email}
        role={currentRole}
        breadcrumbs={breadcrumbs}
        onLogout={logout}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        sidebarCollapsed={collapsed}
        onToggleCollapse={toggleCollapse}
      />
      <div className="app-shell">
        {/* Mobile overlay */}
        <div
          className={`sidebar-overlay${sidebarOpen ? " open" : ""}`}
          onClick={() => setSidebarOpen(false)}
        />
        <aside
          className={sidebarClasses}
          aria-label="Bot navigation"
          {...swipeHandlers}
        >
          <nav className="sidebar-nav">
            <div className="nav-label">Workspace</div>
            {renderNavGroup(WORKSPACE_NAV)}
            <div className="nav-label">Configure</div>
            {renderNavGroup(CONFIGURE_NAV)}
          </nav>
          <SidebarFooter collapsed={collapsed} onToggle={toggleCollapse} />
        </aside>
        <main id="main" className="main">
          {error ? (
            <div className="error" role="alert">{error}</div>
          ) : (
            <>
              <SystemBanners />
              {children}
            </>
          )}
        </main>
      </div>
      <BottomNav
        items={[
          {
            href: `/bots/${slug}/dashboard`,
            label: "Home",
            icon: LayoutDashboard,
          },
          {
            href: `/bots/${slug}/conversations`,
            label: "Chats",
            icon: MessagesSquare,
          },
          {
            href: `/bots/${slug}/files`,
            label: "Knowledge",
            icon: FileText,
          },
          {
            href: `/bots/${slug}/config`,
            label: "Config",
            icon: SlidersHorizontal,
          },
          {
            label: "More",
            icon: Menu,
            onClick: () => setSidebarOpen(true),
            active: sidebarOpen,
          },
        ] satisfies BottomNavItem[]}
      />
    </BotContext.Provider>
  );
}
