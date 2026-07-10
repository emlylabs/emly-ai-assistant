"use client";

import type { ReactNode } from "react";

export type StatusVariant = "success" | "warn" | "danger" | "info" | "neutral";

type StatusPillProps = {
  variant?: StatusVariant;
  /** Use the tag-shaped border radius instead of the pill shape. */
  square?: boolean;
  /** Skip the leading status dot. Useful for "Staging" / "Pro" tags that
   * read more like labels than live signals. */
  noDot?: boolean;
  children: ReactNode;
  className?: string;
  title?: string;
};

/**
 * Status badge. Composes the `.pill` base + a `.pill-<variant>` modifier from
 * globals.css. Distinct from the existing `.status-pill.embedded` family used
 * by file embedding lifecycle — the two coexist.
 */
export default function StatusPill({
  variant = "neutral",
  square = false,
  noDot = false,
  children,
  className,
  title,
}: StatusPillProps) {
  const classes = [
    "pill",
    `pill-${variant}`,
    square ? "pill-square" : "",
    noDot ? "no-dot" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={classes} title={title}>
      {children}
    </span>
  );
}
