"use client";

import { useEffect, useRef, useState } from "react";
import BotAvatar from "@/components/BotAvatar";
import ChannelChip from "@/components/ChannelChip";
import StatusPill from "@/components/StatusPill";
import { api, type Bot } from "@/lib/api";
import { shortClock } from "@/lib/aggregations";
import MarkdownBubble from "./MarkdownBubble";
import type { ConversationSession } from "./types";

type ConversationThreadProps = {
  bot: Bot;
  session: ConversationSession | null;
};

/**
 * Middle pane of the 3-pane conversation view. Renders bubbles by `role`:
 * user (right, accent), assistant (left, surface), system (centered, warn).
 * `role` is whatever the backend stored — usually "user" / "assistant" — so
 * we treat anything non-user/non-system as assistant.
 *
 * Intentionally omits the model / latency / tool footer that the mockup
 * shows: those fields aren't on `EMLYMessage` today and the plan calls for
 * stubbing rather than fabricating them.
 */
export default function ConversationThread({ bot, session }: ConversationThreadProps) {
  const streamRef = useRef<HTMLDivElement>(null);
  const [resolvedState, setResolvedState] = useState<boolean | null>(null);
  const [resolving, setResolving] = useState(false);
  const [resolveError, setResolveError] = useState<string | null>(null);

  // Auto-scroll to the bottom whenever the active session changes or its
  // message list grows.
  useEffect(() => {
    const el = streamRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [session?.sessionId, session?.messages.length]);

  // Reset per-session UI state when the user picks a different conversation.
  // We don't pre-fetch the EMLYSession row — the resolve endpoint is
  // idempotent, so the button works either way and updates state on success.
  useEffect(() => {
    setResolvedState(null);
    setResolving(false);
    setResolveError(null);
  }, [session?.sessionId]);

  const handleResolve = async (next: boolean) => {
    if (!session || resolving) return;
    setResolving(true);
    setResolveError(null);
    try {
      const updated = await api.resolveBotSession(bot.slug, session.sessionId, {
        is_resolved: next,
      });
      setResolvedState(updated.is_resolved ?? next);
    } catch (err: unknown) {
      setResolveError(err instanceof Error ? err.message : "Failed to update");
    } finally {
      setResolving(false);
    }
  };

  if (!session) {
    return (
      <div className="conv-thread">
        <div className="thread-empty">Select a conversation to view its transcript.</div>
      </div>
    );
  }

  const startedAt = session.startedAt
    ? new Date(session.startedAt).toLocaleString()
    : "—";
  const lastTime = session.lastActivityAt ? new Date(session.lastActivityAt).getTime() : 0;
  const recent = lastTime > Date.now() - 5 * 60 * 1000;

  return (
    <div className="conv-thread">
      <div className="thread-header">
        <BotAvatar slug={bot.slug} name={bot.name} size="md" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 13.5 }}>
            {bot.name} · <span style={{ fontFamily: "var(--font-mono)" }}>{session.sessionId.slice(0, 16)}</span>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--muted)" }}>
            {session.userId ?? "anonymous"} · started {startedAt} · {session.turnCount} turn
            {session.turnCount === 1 ? "" : "s"}
          </div>
        </div>
        <ChannelChip kind={session.channel} />
        <StatusPill variant={recent ? "info" : "neutral"} noDot={!recent}>
          {recent ? "Active" : "Recent"}
        </StatusPill>
        <button
          type="button"
          className="ghost compact"
          disabled={resolving}
          onClick={() => handleResolve(!(resolvedState ?? false))}
          title={
            resolvedState
              ? "Reopen this session"
              : "Mark this session as resolved"
          }
        >
          {resolving
            ? "Saving…"
            : resolvedState
              ? "Reopen"
              : "Mark resolved"}
        </button>
        <button
          type="button"
          className="ghost compact"
          disabled
          title="Live takeover coming soon"
        >
          Take over
        </button>
      </div>
      {resolveError && (
        <div className="error" style={{ margin: "0 12px" }}>
          {resolveError}
        </div>
      )}

      <div className="thread-stream" ref={streamRef}>
        {session.messages.map((m) => {
          const role = m.role === "user" ? "user" : m.role === "system" ? "system" : "bot";
          // Only assistant bubbles get markdown rendering. User-typed
          // messages are intentionally plain — their `*` and `_` are usually
          // emphasis-by-accident, not formatting intent. System events are
          // short status notices that we control, also plain.
          const renderMarkdown = role === "bot";
          return (
            <div key={m.id ?? `${m.session_id}:${m.created_on}`} className={`msg msg-${role}`}>
              <div className="msg-bubble">
                {renderMarkdown ? (
                  <MarkdownBubble>{m.message ?? ""}</MarkdownBubble>
                ) : (
                  m.message
                )}
              </div>
              <div className="msg-meta">
                {shortClock(m.created_on)}
                {m.user_id ? ` · ${m.user_id}` : ""}
                {m.topic ? ` · ${m.topic}` : ""}
              </div>
            </div>
          );
        })}
      </div>

      <div className="thread-input">
        <input type="text" placeholder="Live reply coming soon" disabled />
        <button className="compact" disabled title="Live reply coming soon">
          Send
        </button>
      </div>
    </div>
  );
}
