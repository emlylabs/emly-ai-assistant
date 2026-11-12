"use client";

import { useEffect, useState } from "react";
import { ChevronsLeft, ChevronsRight } from "lucide-react";
import Tooltip from "@/components/Tooltip";

type VersionInfo = {
  version: string;
  git_sha: string;
  built_at: string;
};

type Props = {
  collapsed: boolean;
  onToggle: () => void;
};

/**
 * Sidebar footer: collapse toggle on the left, build version on the right.
 *
 * Mirrors the contract documented at `app-deployer/lat.md/catalog.md`
 * (Deployer's own build provenance). The version comes from `GET
 * /api/version`, stamped into the image at build time via the
 * APP_VERSION / GIT_SHA / BUILT_AT build-args.
 *
 * Used by both `<AdminShell>` and `<BotShell>` so the footer stays
 * consistent across the admin and per-bot views.
 */
export default function SidebarFooter({ collapsed, onToggle }: Props) {
  const [version, setVersion] = useState<VersionInfo | null>(null);

  useEffect(() => {
    let alive = true;
    // Public, unauthenticated endpoint — no `/api/admin` prefix, no cookie
    // needed. A failed fetch leaves the label hidden; the build version is
    // informational, not load-bearing.
    fetch("/api/version")
      .then((r) => (r.ok ? r.json() : null))
      .then((v) => { if (alive && v) setVersion(v as VersionInfo); })
      .catch(() => { /* leave blank */ });
    return () => { alive = false; };
  }, []);

  return (
    <div className="sidebar-footer">
      <button
        type="button"
        className="sidebar-collapse-toggle"
        onClick={onToggle}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        title={collapsed ? "Expand" : "Collapse"}
      >
        {collapsed ? (
          <ChevronsRight size={16} strokeWidth={1.75} />
        ) : (
          <ChevronsLeft size={16} strokeWidth={1.75} />
        )}
      </button>
      {!collapsed && version && (
        <Tooltip
          label={`${version.git_sha} build at ${version.built_at}`}
          placement="top"
        >
          <span className="sidebar-version">v{version.version}</span>
        </Tooltip>
      )}
    </div>
  );
}
