"use client";

import type { ReactNode } from "react";

type FeedProps = {
  children: ReactNode;
  className?: string;
};

/**
 * Container for `<FeedItem>` rows. Composes the `.feed` family in globals.css.
 * Keeps a flat structure so callers can interleave items with other elements
 * (e.g., a "Load more" footer).
 */
export default function Feed({ children, className }: FeedProps) {
  return <div className={["feed", className].filter(Boolean).join(" ")}>{children}</div>;
}
