"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { LucideIcon } from "lucide-react";

export type BottomNavItem = {
  /** Display label shown under the icon. Keep ≤10 chars. */
  label: string;
  /** Icon component from lucide-react. */
  icon: LucideIcon;
  /** Destination route. Required for navigation items; omitted for action items. */
  href?: string;
  /** Click handler for action items (e.g., "More" → open drawer). When set,
   * the item renders as a `<button>` instead of a `<Link>`. */
  onClick?: () => void;
  /** Marks the item as currently selected. Defaults to a pathname match
   * against `href`. Override when the URL semantics don't fit (e.g., the
   * "More" button is never "active"). */
  active?: boolean;
};

type BottomNavProps = {
  items: BottomNavItem[];
};

/**
 * Mobile bottom navigation bar. Renders at the foot of the viewport on
 * narrow screens (≤900px, gated in `globals.css`) and stays hidden on
 * desktop. Each item is either a `<Link>` (navigation) or a `<button>`
 * (action — used for "More" → open drawer in `BotShell`).
 */
export default function BottomNav({ items }: BottomNavProps) {
  const pathname = usePathname();

  return (
    <nav className="bottom-nav" aria-label="Primary mobile navigation">
      {items.map((item) => {
        const isActive =
          item.active ??
          (!!item.href &&
            !!pathname &&
            (pathname === item.href || pathname.startsWith(`${item.href}/`)));
        const cls = isActive ? "bottom-nav-item active" : "bottom-nav-item";

        if (item.onClick) {
          return (
            <button
              key={item.label}
              type="button"
              className={cls}
              onClick={item.onClick}
              aria-label={item.label}
            >
              <item.icon strokeWidth={1.75} size={20} aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          );
        }

        if (!item.href) return null;
        return (
          <Link
            key={item.label}
            href={item.href}
            className={cls}
            aria-current={isActive ? "page" : undefined}
            aria-label={item.label}
          >
            <item.icon strokeWidth={1.75} size={20} aria-hidden="true" />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
