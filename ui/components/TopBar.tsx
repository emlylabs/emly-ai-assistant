"use client";

import { ReactNode } from "react";
import Link from "next/link";
import { ChevronsLeft, ChevronsRight, LogOut, Menu, Sparkles, X } from "lucide-react";
import ThemePicker from "@/components/ThemePicker";
import type { Role } from "@/lib/api";

type TopBarProps = {
  /** Admin email to display */
  email?: string | null;
  /** Role on the current bot, if any. Scoped to BotShell; null elsewhere. */
  role?: Role | null;
  /** Breadcrumbs strip; in BotShell this includes the bot-picker dropdown. */
  breadcrumbs?: ReactNode;
  /** Optional search slot. Reserved for the global ⌘K palette later. */
  search?: ReactNode;
  /** Sign-out handler */
  onLogout: () => void;
  /** Mobile sidebar state */
  sidebarOpen: boolean;
  /** Toggle mobile sidebar */
  onToggleSidebar: () => void;
  /** Desktop sidebar collapsed state */
  sidebarCollapsed: boolean;
  /** Toggle desktop sidebar collapse */
  onToggleCollapse: () => void;
};

export default function TopBar({
  email,
  role,
  breadcrumbs,
  search,
  onLogout,
  sidebarOpen,
  onToggleSidebar,
  sidebarCollapsed,
  onToggleCollapse,
}: TopBarProps) {
  return (
    <header className="topbar">
      {/* Hamburger — visible on mobile only (CSS hides on desktop) */}
      <button
        type="button"
        className="topbar-hamburger"
        onClick={onToggleSidebar}
        aria-label={sidebarOpen ? "Close menu" : "Open menu"}
      >
        {sidebarOpen ? (
          <X size={20} strokeWidth={1.75} />
        ) : (
          <Menu size={20} strokeWidth={1.75} />
        )}
      </button>

      {/* Brand */}
      <Link href="/bots" className="topbar-brand">
        <span className="brand-mark">
          <Sparkles strokeWidth={1.75} />
        </span>
        Emly admin
      </Link>

      {/* Center area — breadcrumbs (BotShell embeds the bot-picker dropdown
          inside its middle crumb) plus an optional search slot. Both are
          optional; the topbar reads cleanly with neither. */}
      <div className="topbar-center">
        {breadcrumbs}
        {search}
      </div>

      {/* Right area — theme, identity (email + bot role), sign out */}
      <div className="topbar-right">
        <ThemePicker />
        {email && (
          <span className="topbar-identity">
            <span className="topbar-email">{email}</span>
            {role && <span className="topbar-role" title="Your role on this bot">{role}</span>}
          </span>
        )}
        <button
          className="ghost compact-icon"
          onClick={onLogout}
          aria-label="Sign out"
          title="Sign out"
        >
          <LogOut size={15} strokeWidth={1.75} />
        </button>
      </div>
    </header>
  );
}
