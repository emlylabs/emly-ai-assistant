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
  ChevronsLeft,
  ChevronsRight,
  FileText,
  LayoutDashboard,
  MessagesSquare,
  Radio,
  ScrollText,
  Settings,
  SlidersHorizontal,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";
import { AdminUser, ApiError, Bot, Membership, Role, api, loginUrl } from "@/lib/api";
import SystemBanners from "@/components/SystemBanners";
import TopBar from "@/components/TopBar";
import Crumbs from "@/components/Crumbs";
import BotPickerDropdown from "@/components/BotPickerDropdown";

// Per-bot navigation. Each tab knows its slug from the URL.
//
// Phase 2 of the UI overhaul splits the nav into two semantic groups so the
// sidebar reads as "what the bot does" (Workspace) vs "how the bot is set
// up" (Configure). Phase 7 will insert "Analytics" into WORKSPACE_NAV; we
// don't add it now because the route doesn't exist yet — broken nav links
// hurt more than missing labels.
type NavItem = { href: string; label: string; icon: LucideIcon };

const WORKSPACE_NAV: NavItem[] = [
  { href: "dashboard",     label: "Dashboard",     icon: LayoutDashboard },
  { href: "analytics",     label: "Analytics",     icon: BarChart3 },
  { href: "conversations", label: "Conversations", icon: MessagesSquare },
  { href: "files",         label: "Files",         icon: FileText },
  { href: "channels",      label: "Channels",      icon: Radio },
];

const CONFIGURE_NAV: NavItem[] = [
  { href: "config",        label: "Config",        icon: SlidersHorizontal },
  { href: "users",         label: "Bot users",     icon: Users },
  { href: "members",       label: "Members",       icon: UserCog },
  { href: "audit",         label: "Audit",         icon: ScrollText },
  { href: "settings",      label: "Settings",      icon: Settings },
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
    api
      .me()
      .then(setMe)
      .catch((err: any) => {
        if (err?.status === 401) {
          router.replace("/login");
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

  function refreshPermissions() {
    // Memberships only refresh on the next /me call. Bounce through /login,
    // which redirects through the IdP to mint a fresh access token + cookie.
    window.location.href = loginUrl(window.location.pathname);
  }

  function onSwitchBot(targetSlug: string) {
    if (!targetSlug || targetSlug === slug) return;
    // Preserve the active tab when switching bots.
    const tab = pathname?.split("/").slice(3, 4)[0] ?? "dashboard";
    router.push(`/bots/${targetSlug}/${tab}`);
  }

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

  if (!me && !error) {
    return (
      <div className="center">
        <p className="muted">Loading…</p>
      </div>
    );
  }

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
            If a teammate just granted you access, your sign-in token is stale —
            sign in again to refresh permissions.
          </p>
          <div className="row" style={{ marginTop: 20 }}>
            <button onClick={() => router.replace("/bots")}>Back to bots</button>
            <button className="ghost" onClick={refreshPermissions}>
              Refresh permissions
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
              onSwitch={onSwitchBot}
            />
          ),
        },
        { label: activeLabel },
      ]}
    />
  ) : null;

  function renderNavGroup(items: NavItem[]) {
    return items.map(({ href: tab, label, icon: Icon }) => {
      const href = `/bots/${slug}/${tab}`;
      const active = pathname === href || pathname?.startsWith(`${href}/`);
      return (
        <Link
          key={tab}
          href={href}
          className={active ? "nav-item active" : "nav-item"}
          data-tooltip={label}
        >
          <Icon strokeWidth={1.75} />
          <span className="nav-item-label">{label}</span>
        </Link>
      );
    });
  }

  return (
    <BotContext.Provider value={ctxValue}>
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
        <aside className={sidebarClasses}>
          <nav className="sidebar-nav">
            <div className="nav-label">Workspace</div>
            {renderNavGroup(WORKSPACE_NAV)}
            <div className="nav-label">Configure</div>
            {renderNavGroup(CONFIGURE_NAV)}
          </nav>
          <button
            type="button"
            className="sidebar-collapse-toggle"
            onClick={toggleCollapse}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? (
              <ChevronsRight size={16} strokeWidth={1.75} />
            ) : (
              <ChevronsLeft size={16} strokeWidth={1.75} />
            )}
          </button>
        </aside>
        <main className="main">
          {error ? (
            <div className="error">{error}</div>
          ) : (
            <>
              <SystemBanners />
              {children}
            </>
          )}
        </main>
      </div>
    </BotContext.Provider>
  );
}
