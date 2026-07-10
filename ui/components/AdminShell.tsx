"use client";

import { ReactNode, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { Bot, ChevronsLeft, ChevronsRight, Users, type LucideIcon } from "lucide-react";
import { AdminUser, api, loginUrl } from "@/lib/api";
import SystemBanners from "@/components/SystemBanners";
import TopBar from "@/components/TopBar";

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
];

export default function AdminShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [me, setMe] = useState<AdminUser | null>(null);
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
    api
      .me()
      .then(setMe)
      .catch((err: any) => {
        if (err?.status === 401) {
          router.replace("/login");
          return;
        }
        setBootError(err?.message ?? "Failed to load profile");
      });
  }, [router]);

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
      // even if the server didn't respond cleanly, fall through to login
    }
    router.replace("/login");
  }

  if (!me && !bootError) {
    return (
      <div className="center">
        <p className="muted">Loading…</p>
      </div>
    );
  }

  const sidebarClasses = [
    "sidebar",
    sidebarOpen ? "open" : "",
    collapsed ? "collapsed" : "",
  ].filter(Boolean).join(" ");

  return (
    <>
      <TopBar
        email={me?.email}
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
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = pathname === href || pathname?.startsWith(`${href}/`);
              return (
                <Link
                  key={href}
                  href={href}
                  className={active ? "nav-item active" : "nav-item"}
                  data-tooltip={label}
                >
                  <Icon strokeWidth={1.75} />
                  <span className="nav-item-label">{label}</span>
                </Link>
              );
            })}
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
          {bootError ? (
            <div className="error">{bootError}</div>
          ) : (
            <>
              <SystemBanners />
              {children}
            </>
          )}
        </main>
      </div>
    </>
  );
}
