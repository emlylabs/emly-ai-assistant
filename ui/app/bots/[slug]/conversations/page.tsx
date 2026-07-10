"use client";

import { Suspense } from "react";
import { useBot } from "@/components/BotShell";
import ConversationsSplit from "@/components/conversations/ConversationsSplit";

/**
 * Per-bot conversations page. Phase 5 of the UI overhaul: the page is a
 * thin wrapper around `<ConversationsSplit>`, which owns the data fetch
 * and the 3-pane layout. The previous single-table implementation has
 * been replaced — see `git log -- this file` for the v1 version.
 *
 * Wrapped in Suspense because the split reads filter/page state from
 * `useSearchParams()` for deep-linkable filtered views (Next 16+
 * requirement).
 */
export default function BotConversationsPage() {
  const { bot } = useBot();
  return (
    <Suspense fallback={<div className="muted" style={{ padding: 16 }}>Loading…</div>}>
      <ConversationsSplit bot={bot} />
    </Suspense>
  );
}
