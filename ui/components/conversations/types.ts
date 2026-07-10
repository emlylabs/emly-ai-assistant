import type { ChannelType, EmlySession, Message, SessionRow } from "@/lib/api";

export type ConversationSession = {
  /** Stable id from the messages table. */
  sessionId: string;
  /** Best-effort end-user id for the session. Falls back to "anonymous"
   * when the messages table doesn't have one. */
  userId: string | null;
  /** Channel hint for the row's chip. */
  channel: ChannelType;
  /** Authoritative turn count from `emly_session.turn_count`. Display this
   * instead of `messages.length` — the list view only carries the last
   * message preview, so `messages.length` would lie until the detail
   * fetch lands. */
  turnCount: number;
  /** Started timestamp from the session row (`emly_session.started_at`). */
  startedAt: string;
  /** Last-activity timestamp from the session row. */
  lastActivityAt: string;
  /** Resolution flag from the session row, when known. */
  isResolved: boolean | null;
  /** Populated only for the active session (lazy-fetched). Sorted
   * oldest → newest. List rows leave this as `[]`. */
  messages: Message[];
  /** Latest message in the session (for previews). For list rows this is
   * the `last_message` returned by the sessions list endpoint; for the
   * active session it's recomputed from `messages`. */
  last: Message;
  /** First message in the session. List rows reuse `last` as a stub since
   * they don't yet have the full thread; the detail fetch fills this in
   * with the actual first message. */
  first: Message;
};

function channelKindFor(session: EmlySession): ChannelType {
  // The session row carries a `channel_id`, but we'd need a second lookup
  // against `bot_channel` to translate it into a `ChannelType`. For now
  // default to web_widget — the list's `ChannelChip` styling is identical
  // and we surface the raw `channel_id` in the meta panel.
  return "web_widget";
}

function stubMessage(session: EmlySession): Message {
  // Used as `last`/`first` when the sessions list returns no last_message
  // (extremely rare — every session has at least one turn). Synthesises
  // an empty placeholder so the row still renders.
  return {
    user_id: session.user_id,
    session_id: session.id,
    message: "",
    role: "user",
    created_on: session.last_activity_at,
    updated_on: session.last_activity_at,
    not_useful: false,
    expanded_query: null,
    page: null,
    topic: null,
  };
}

export function fromSessionRow(row: SessionRow): ConversationSession {
  const last = row.last_message ?? stubMessage(row.session);
  return {
    sessionId: row.session.id,
    userId: row.session.user_id || null,
    channel: channelKindFor(row.session),
    turnCount: row.session.turn_count,
    startedAt: row.session.started_at,
    lastActivityAt: row.session.last_activity_at,
    isResolved: row.session.is_resolved,
    messages: [],
    last,
    first: last,
  };
}

export function withDetail(
  base: ConversationSession,
  messages: Message[],
): ConversationSession {
  if (messages.length === 0) return base;
  const sorted = [...messages].sort((a, b) =>
    a.created_on < b.created_on ? -1 : a.created_on > b.created_on ? 1 : 0,
  );
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  return {
    ...base,
    messages: sorted,
    first,
    last,
  };
}
