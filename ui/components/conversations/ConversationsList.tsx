"use client";

import { Search } from "lucide-react";
import ChannelChip from "@/components/ChannelChip";
import StatusPill from "@/components/StatusPill";
import { shortClock } from "@/lib/aggregations";
import type { ConversationSession } from "./types";

const ACTIVE_WINDOW_MS = 5 * 60 * 1000;

type ConversationsListProps = {
  sessions: ConversationSession[];
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  loading?: boolean;
  query: string;
  onQueryChange: (next: string) => void;
};

/**
 * Left pane of the 3-pane conversation view. The search box is controlled
 * by the parent, which sends it to the sessions endpoint so every turn of
 * every session is searched (not just the loaded previews); selecting a row
 * notifies the parent which loads the thread + meta panes.
 */
export default function ConversationsList({
  sessions,
  activeSessionId,
  onSelect,
  loading = false,
  query,
  onQueryChange,
}: ConversationsListProps) {
  return (
    <div className="conv-list">
      <div className="conv-list-header">
        <Search size={14} strokeWidth={1.75} style={{ color: "var(--muted)", flex: "0 0 auto" }} />
        <input
          type="search"
          placeholder="Search transcripts…"
          aria-label="Search transcripts"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
        />
      </div>

      {loading && sessions.length === 0 ? (
        <div style={{ padding: 18, color: "var(--muted)", fontSize: 12.5 }}>
          Loading conversations…
        </div>
      ) : sessions.length === 0 ? (
        <div style={{ padding: 18, color: "var(--muted)", fontSize: 12.5 }}>
          {query ? "No matches." : "No conversations yet."}
        </div>
      ) : (
        sessions.map((s) => {
          const isActive = s.sessionId === activeSessionId;
          const lastTime = s.lastActivityAt ? new Date(s.lastActivityAt).getTime() : 0;
          const recent = lastTime > Date.now() - ACTIVE_WINDOW_MS;
          const preview = (s.last.message ?? "").slice(0, 96);
          return (
            <button
              key={s.sessionId}
              type="button"
              className="conv-row"
              data-active={isActive ? "true" : undefined}
              onClick={() => onSelect(s.sessionId)}
            >
              <div className="conv-row-top">
                <ChannelChip kind={s.channel} />
                <span className="conv-row-bot">{s.userId ?? "anonymous"}</span>
                <span className="conv-row-time">{shortClock(s.lastActivityAt)}</span>
              </div>
              <div className="conv-row-msg">{preview || <em>(empty)</em>}</div>
              <div className="conv-row-meta">
                <StatusPill
                  variant={s.isResolved ? "success" : recent ? "info" : "neutral"}
                  noDot={s.isResolved ? false : !recent}
                >
                  {s.isResolved ? "Resolved" : recent ? "Active" : "Recent"}
                </StatusPill>
                <span style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
                  {s.turnCount} {s.turnCount === 1 ? "turn" : "turns"}
                </span>
              </div>
            </button>
          );
        })
      )}
    </div>
  );
}
