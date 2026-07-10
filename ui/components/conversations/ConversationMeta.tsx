"use client";

import type { ConversationSession } from "./types";

type ConversationMetaProps = {
  session: ConversationSession | null;
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString();
}

/**
 * Right rail of the conversation view. Shows what we actually have on
 * `EMLYMessage` (session id, channel, start, turns, user id, topic) and
 * collapses the mockup's classification / sentiment / order-context
 * sections into a single "Enrichment pending" placeholder. Honest by
 * design — none of those fields exist in the schema today.
 */
export default function ConversationMeta({ session }: ConversationMetaProps) {
  if (!session) {
    return (
      <aside className="conv-meta">
        <div className="meta-section">
          <div className="meta-label">Session</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Pick a conversation to see its metadata.
          </div>
        </div>
      </aside>
    );
  }

  const topics = Array.from(
    new Set(
      session.messages
        .map((m) => m.topic)
        .filter((t): t is string => !!t)
    ),
  );

  return (
    <aside className="conv-meta">
      <div className="meta-section">
        <div className="meta-label">Customer</div>
        <div className="meta-row">
          <span className="key">User id</span>
          <span className="val">{session.userId ?? "anonymous"}</span>
        </div>
      </div>

      <div className="meta-section">
        <div className="meta-label">Session</div>
        <div className="meta-row">
          <span className="key">ID</span>
          <span className="val">{session.sessionId}</span>
        </div>
        <div className="meta-row">
          <span className="key">Channel</span>
          <span className="val">{session.channel}</span>
        </div>
        <div className="meta-row">
          <span className="key">Started</span>
          <span className="val">{formatDate(session.startedAt)}</span>
        </div>
        <div className="meta-row">
          <span className="key">Last activity</span>
          <span className="val">{formatDate(session.lastActivityAt)}</span>
        </div>
        <div className="meta-row">
          <span className="key">Turns</span>
          <span className="val">{session.turnCount}</span>
        </div>
      </div>

      {topics.length > 0 && (
        <div className="meta-section">
          <div className="meta-label">Topics seen</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {topics.map((t) => (
              <span key={t} className="tag" style={{ minHeight: 0, padding: "2px 8px", fontSize: 11 }}>
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="meta-section">
        <div className="meta-label">Enrichment</div>
        <div className="meta-pending">
          Classification confidences, sentiment, and order context aren&apos;t
          tracked yet. Once the backend exposes per-message enrichment, this
          panel will fill in.
        </div>
      </div>
    </aside>
  );
}
