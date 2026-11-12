"use client";

import { ReactNode, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { Bot, Globe, Settings as SettingsIcon, Users, type LucideIcon } from "lucide-react";
import { AdminUser, Bot as BotType, api, loginUrl, startProactiveRefresh } from "@/lib/api";
import SystemBanners from "@/components/SystemBanners";
import SidebarFooter from "@/components/SidebarFooter";
import TopBar from "@/components/TopBar";
import BottomNav, { type BottomNavItem } from "@/components/BottomNav";
import BotPickerDropdown from "@/components/BotPickerDropdown";
import CommandPalette from "@/components/CommandPalette";
import Tooltip from "@/components/Tooltip";
import { useShortcut } from "@/components/KeyboardShortcuts";
import { readLastTab } from "@/lib/bot-prefs";
import { useSwipeLeftToClose } from "@/lib/useSwipeLeftToClose";

const COLLAPSE_KEY = "bf-sidebar-collapsed";

function readCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

// Cross-bot navigation only. Per-bot pages use `<BotShell>` instead.
const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/bots", label: "Bots", icon: Bot },
  { href: "/admins", label: "Admins", icon: Users },
  { href: "/workspace/crawls", label: "Crawls", icon: Globe },
  { href: "/workspace/settings", label: "Workspace", icon: SettingsIcon },
];

export default function AdminShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<AdminUser | null>(null);
  const [bots, setBots] = useState<BotType[] | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);

  function toggleCollapse() {
    setCollapsed((v) => {
      const next = !v;
      try { localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0"); } catch {}
      return next;
    });
  }

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
          // same page after signing in.
          const target = window.location.pathname + window.location.search;
          router.replace(`/login?return_to=${encodeURIComponent(target)}`);
          return;
        }
        setBootError(err?.message ?? "Failed to load profile");
      });
  }, [router]);

  // Close sidebar on navigation (mobile)
  useEffect(() => {
    setSidebarOpen(false);
  }, [pathname]);

  // Load the bot list once /me succeeds — the topbar picker and command
  // palette both need it. Failure is fine; the picker just shows a "no
  // bots available" empty state and the palette omits the section.
  useEffect(() => {
    if (!me) return;
    api
      .listBots()
      .then(setBots)
      .catch(() => setBots([]));
  }, [me]);

  function onJumpToBot(slug: string) {
    if (!slug) return;
    const tab = readLastTab(me?.id, slug) || "dashboard";
    router.push(`/bots/${slug}/${tab}`);
  }

  // Workspace-level shortcuts. The per-bot shortcuts live in `BotShell`
  // (which knows the active slug). Both shells share `c` for create-bot
  // and `/` for search — registered here too because workspace pages
  // have their own search UI (e.g. `/bots`).
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

  async function logout() {
    try {
      const res = await api.logout();
      if (res?.provider_logout_url) {
        window.location.href = res.provider_logout_url;
        return;
      }
    } catch {
      // even if the server didn't respond cleanly, fall through to login
    }
    router.replace("/login");
  }

  // Hook order matters — must be called every render, before any early
  // return below. The handlers themselves are inert until the drawer is
  // open (touchstart/end fire on the off-screen aside, but the close
  // call is a no-op since `sidebarOpen` is already false).
  const swipeHandlers = useSwipeLeftToClose(() => setSidebarOpen(false));

  // No blocking "Loading…" for the initial `me()` fetch — the
  // middleware (and the FastAPI catchall in static-export prod)
  // already guarantee a session cookie is present. The shell renders
  // immediately; `me` populates in the background. If the server-side
  // verifier rejects the cookie (signature, expiry, …) the catch on
  // the `me()` useEffect above redirects to /login.
  const sidebarClasses = [
    "sidebar",
    sidebarOpen ? "open" : "",
    collapsed ? "collapsed" : "",
  ].filter(Boolean).join(" ");

  // Compact bot picker in the topbar. Workspace-level pages don't have an
  // active bot, so the trigger reads "Go to bot…" and the picker renders
  // its full list (pinned/recent/all) on open.
  const workspaceBotPicker = (
    <BotPickerDropdown
      bots={bots}
      adminId={me?.id}
      onSwitch={onJumpToBot}
      triggerLabel="Go to bot…"
    />
  );

  return (
    <>
      <CommandPalette
        adminId={me?.id}
        currentBotSlug={null}
        onCreateBot={() => router.push("/bots?new=1")}
      />
      <TopBar
        email={me?.email}
        breadcrumbs={workspaceBotPicker}
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
          aria-label="Workspace navigation"
          {...swipeHandlers}
        >
          <nav className="sidebar-nav">
            <div className="nav-label">Workspace</div>
            {NAV.map(({ href, label, icon: Icon }) => {
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
                </Link>
              );
              // When the sidebar is collapsed the label is hidden, so the
              // tooltip is the only thing telling the user (or screen
              // reader) what the icon means. Always rendered; CSS hides it
              // in the expanded state.
              return collapsed ? (
                <Tooltip key={href} label={label} placement="right">
                  {link}
                </Tooltip>
              ) : (
                <span key={href}>{link}</span>
              );
            })}
          </nav>
          <SidebarFooter collapsed={collapsed} onToggle={toggleCollapse} />
        </aside>
        <main id="main" className="main">
          {bootError ? (
            <div className="error" role="alert">{bootError}</div>
          ) : (
            <>
              <SystemBanners />
              {children}
            </>
          )}
        </main>
      </div>
      <BottomNav items={NAV.map(({ href, label, icon }): BottomNavItem => ({ href, label, icon }))} />
    </>
  );
}
