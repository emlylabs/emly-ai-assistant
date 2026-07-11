import { v4 as uuidv4 } from 'uuid';
import { getBotId } from './botContext';

/** Inline form payload attached to a bot message. When present, the
 * message renders the form instead of (or alongside) text. Mirrors the
 * `c_forms_selected` schema in the backend (`form_schema` + `trigger`). */
export interface MessageForm {
    formSchema: Record<string, unknown>;
    trigger: Record<string, unknown>;
    /** Lifecycle: 'pending' → user filling, 'submitted' → success post-message,
     * 'cancelled' → dismissed, 'post-limit' → visitor hit the configured
     *  per-form limit; render the engagement message instead of fields. */
    status?: 'pending' | 'submitted' | 'cancelled' | 'post-limit';
    /** Optional Yes/No confirmation state — when the trigger sets
     * `user_confirmation`, the message starts as a confirm prompt and
     * only flips to the form proper after the visitor accepts. */
    confirm?: 'pending' | 'accepted' | 'declined';
    /** Drives the "N of M submissions remaining" footnote inside the
     * form bubble. Populated when the trigger has `allow_limit && limit`. */
    formCount?: { current: number; limit: number };
    /** OTP verification state for forms with `trigger.verify_action`.
     * `none` (or absent) renders the form fields; `awaitingOtp` swaps
     * to the OTP entry sub-component. */
    otpStep?: 'none' | 'awaitingOtp';
    /** Email captured from the form's email field — used as the
     * `user_id` parameter when posting `auth_authentication` /
     * `auth_verification`. */
    otpEmail?: string;
    /** Form values held back until the visitor verifies the OTP. After
     * verification they're replayed through the normal submit path. */
    pendingValues?: Record<string, unknown>;
}

/** Per-bot localStorage key for the START_CHAT capture form. Once the
 * visitor submits the form, we persist the captured values here so
 * future loads skip the gate. Mirrors `Bot_Core_Capture_Info_${appId}`
 * from the legacy `your-assistant` widget. */
export const startChatKey = (botId: string): string => `chatbot.startChat.${botId}`;

/** RAG citation as it arrives in the chat NDJSON `done` chunk. The
 * agent runs `get_filtered_citations` server-side, which deduplicates by
 * source and returns entries shaped roughly as `{metadata, chunk,
 * relevance_score, og?, payload?}`. The widget treats every field as
 * optional and parses `metadata.og` lazily (it can be a JSON string from
 * older ingestions). */
export interface Citation {
    metadata?: {
        source?: string;
        source_url?: string;
        title?: string;
        og?: string | Record<string, unknown>;
        payload?: string | Record<string, unknown>;
        image?: string;
        [key: string]: unknown;
    };
    chunk?: string;
    relevance_score?: number;
    og?: Record<string, unknown> | string;
    payload?: Record<string, unknown> | string;
    [key: string]: unknown;
}

export interface Message {
    id: string;
    text: string;
    isUserMessage: boolean;
    timestamp: string;
    data?: unknown;
    form?: MessageForm;
    /** Backend-assigned message id (`EMLYMessages.id`). Required to
     * post a CSAT rating via `POST /widget/{bot}/messages/{id}/rate`.
     * Only set on assistant messages once the chat response completes. */
    messageId?: number;
    /** RAG citations attached to an assistant reply. Rendered as
     * pills under the bubble when the bot's `show_citations` flag
     * is on. */
    citations?: Citation[];
}

export interface ChatSession {
    id: string;
    messages: Message[];
    name: string;
}

// Storage keys are namespaced per bot so previewing bot A on the admin
// config page never leaks history from a previous preview of bot B
// (and vice versa for production embeds when two bots happen to share a
// localStorage origin). When no botId is set yet — the slice is created
// at module load, before `initializeChatbot` runs — every accessor is a
// safe no-op so we don't pollute an unscoped key.
const SESSION_PREFIX = 'chatSessions';
const ACTIVE_PREFIX = 'activeChatSessionId';

const sessionsKey = (botId: string): string | null =>
    botId ? `${SESSION_PREFIX}.${botId}` : null;
const activeKey = (botId: string): string | null =>
    botId ? `${ACTIVE_PREFIX}.${botId}` : null;

const resolveBotId = (override?: string): string =>
    override ?? getBotId();

export const getSessions = (botId?: string): ChatSession[] => {
    const key = sessionsKey(resolveBotId(botId));
    if (!key) return [];
    const sessionsJson = localStorage.getItem(key);
    return sessionsJson ? JSON.parse(sessionsJson) : [];
};

export const saveSessions = (sessions: ChatSession[], botId?: string): void => {
    const key = sessionsKey(resolveBotId(botId));
    if (!key) return;
    localStorage.setItem(key, JSON.stringify(sessions));
};

export const getActiveSessionId = (botId?: string): string | null => {
    const key = activeKey(resolveBotId(botId));
    if (!key) return null;
    return localStorage.getItem(key);
};

export const setActiveSessionId = (sessionId: string, botId?: string): void => {
    const key = activeKey(resolveBotId(botId));
    if (!key) return;
    localStorage.setItem(key, sessionId);
};

export const clearActiveSessionId = (botId?: string): void => {
    const key = activeKey(resolveBotId(botId));
    if (!key) return;
    localStorage.removeItem(key);
};

export const createNewSession = (
    name?: string,
    botId?: string,
): ChatSession => {
    const id = resolveBotId(botId);
    const sessions = getSessions(id);
    const newSession: ChatSession = {
        id: uuidv4(),
        messages: [],
        name: name ?? `Session ${sessions.length + 1}`,
    };
    sessions.push(newSession);
    saveSessions(sessions, id);
    setActiveSessionId(newSession.id, id);
    return newSession;
};

export const getActiveSession = (botId?: string): ChatSession | null => {
    const id = resolveBotId(botId);
    const activeSessionId = getActiveSessionId(id);
    const sessions = getSessions(id);
    return sessions.find(session => session.id === activeSessionId) || null;
};

export const updateSessionMessages = (
    sessionId: string,
    messages: Message[],
    botId?: string,
): void => {
    const id = resolveBotId(botId);
    const sessions = getSessions(id);
    const sessionIndex = sessions.findIndex(session => session.id === sessionId);
    if (sessionIndex !== -1) {
        sessions[sessionIndex].messages = messages;
        saveSessions(sessions, id);
    }
};

export const deleteSession = (sessionId: string, botId?: string): void => {
    const id = resolveBotId(botId);
    let sessions = getSessions(id);
    sessions = sessions.filter(session => session.id !== sessionId);
    saveSessions(sessions, id);

    const activeSessionId = getActiveSessionId(id);
    if (activeSessionId === sessionId) {
        if (sessions.length > 0) {
            setActiveSessionId(sessions[0].id, id);
        } else {
            clearActiveSessionId(id);
        }
    }
};
