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

        // A `node` override always wins — the consumer decides what goes
        // here. Used to embed the bot-picker dropdown inline.
        if (crumb.node) {
          return (
            <span key={`crumb-${index}`} style={{ display: "contents" }}>
              {sep}
              {crumb.node}
            </span>
          );
        }

        if (isLast) {
          return (
            <span key={`crumb-${index}`} style={{ display: "contents" }}>
              {sep}
              <strong>{crumb.label}</strong>
            </span>
          );
        }

        return (
          <span key={`crumb-${index}`} style={{ display: "contents" }}>
            {sep}
            {crumb.href ? (
              <Link href={crumb.href}>{crumb.label}</Link>
            ) : (
              <span>{crumb.label}</span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
