"use client";

import type { ReactNode } from "react";
import Link from "next/link";

export type Crumb = {
  label: string;
  href?: string;
  /** When provided, replaces the default label/link rendering with arbitrary
   * content. Used by `BotShell` to drop a `<BotPickerDropdown>` into the
   * bot-name slot so the breadcrumb doubles as a switcher. */
  node?: ReactNode;
};

type CrumbsProps = {
  items: Crumb[];
  className?: string;
};

/**
 * Breadcrumbs strip rendered inside the topbar. Uses the `.crumbs` family
 * from globals.css. The last item is bold (current page) regardless of
 * whether `href` was passed; intermediate items render as muted links if
 * they have an `href`, otherwise as plain spans.
 */
export default function Crumbs({ items, className }: CrumbsProps) {
  if (!items || items.length === 0) return null;

  return (
    <nav
      className={["crumbs", className].filter(Boolean).join(" ")}
      aria-label="Breadcrumb"
    >
      {items.map((crumb, index) => {
        const isLast = index === items.length - 1;
        const sep =
          index > 0 ? (
            <span key={`sep-${index}`} className="crumb-sep" aria-hidden="true">
              /
            </span>
          ) : null;

        // Real flex wrapper (not display:contents) so responsive rules can
        // hide individual crumbs — the mobile topbar collapses everything
        // except the bot picker.
        const body = crumb.node ? (
          crumb.node
        ) : isLast ? (
          <strong title={crumb.label}>{crumb.label}</strong>
        ) : crumb.href ? (
          <Link href={crumb.href} title={crumb.label}>{crumb.label}</Link>
        ) : (
          <span className="crumb-text" title={crumb.label}>{crumb.label}</span>
        );

        return (
          <span key={`crumb-${index}`} className="crumb-item">
            {sep}
            {body}
          </span>
        );
      })}
    </nav>
  );
}
