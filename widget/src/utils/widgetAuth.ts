/**
 * Visitor identity + widget token handling.
 *
 * The backend requires `Authorization: Bearer <token>` on every widget
 * route that reads or writes per-visitor data. The token comes from
 * `POST /widget/{bot}/init`, is bound to this bot and the visitor's
 * user id, and is cached in localStorage until shortly before it expires.
 * `widgetFetch` attaches it and, on a 401, re-initialises once and retries.
 *
 * `/init` only keeps the stored visitor id when the last token we were
 * given proves it is ours (sent as `previous_token`, even if expired) or
 * when the id has no history on the server yet. Otherwise it returns a
 * fresh id, which we adopt as this browser's visitor id.
 */
import { v4 as uuidv4 } from 'uuid';
import { getChatbotConfig } from '../embed';

const USER_ID_EXPIRY_DAYS = 5; // Fallback default
const EMLY_USER_ID_KEY = 'emly.chatbot.userId';
const EMLY_USER_ID_EXPIRY_KEY = 'emly.chatbot.userId.expiry';
const TOKEN_KEY_PREFIX = 'emly.widget.token.';
// Refresh a little before expiry so an in-flight request doesn't race it.
const TOKEN_REFRESH_MARGIN_MS = 60_000;

const storeUserId = (userId: string): void => {
  try {
    const config = getChatbotConfig();
    const expiryDays = config.userIdExpiryDays || USER_ID_EXPIRY_DAYS;
    localStorage.setItem(EMLY_USER_ID_KEY, userId);
    localStorage.setItem(EMLY_USER_ID_EXPIRY_KEY, String(Date.now() + expiryDays * 24 * 60 * 60 * 1000));
  } catch {
    // storage disabled — the id lives for this page view only
  }
};

/** Persistent (per-browser, time-limited) anonymous visitor id. */
export const getEmlyUserId = (): string => {
  try {
    const config = getChatbotConfig();
    const expiryDays = config.userIdExpiryDays || USER_ID_EXPIRY_DAYS;

    const storedId = localStorage.getItem(EMLY_USER_ID_KEY);
    const expiryStr = localStorage.getItem(EMLY_USER_ID_EXPIRY_KEY);

    if (storedId && expiryStr) {
      const expiryTime = Number(expiryStr);
      if (Date.now() < expiryTime) {
        return storedId;
      }
    }

    const newId = `emly-${uuidv4()}`;
    const expiryMs = Date.now() + expiryDays * 24 * 60 * 60 * 1000;
    localStorage.setItem(EMLY_USER_ID_KEY, newId);
    localStorage.setItem(EMLY_USER_ID_EXPIRY_KEY, String(expiryMs));
    return newId;
  } catch {
    return `emly-${uuidv4()}`;
  }
};

interface CachedToken {
  token: string;
  userId: string;
  expiresAt: number; // epoch ms
}

// In-memory copy also covers browsers where localStorage is unavailable.
const memoryTokens: Record<string, CachedToken> = {};
const inflight: Record<string, Promise<CachedToken> | undefined> = {};

const cacheKey = (botId: string): string => `${TOKEN_KEY_PREFIX}${botId}`;

/** Last token issued for this bot, valid or not (used as renewal proof). */
const readAny = (botId: string): CachedToken | null => {
  const inMemory = memoryTokens[botId];
  if (inMemory) return inMemory;
  try {
    const raw = localStorage.getItem(cacheKey(botId));
    return raw ? (JSON.parse(raw) as CachedToken) : null;
  } catch {
    return null;
  }
};

const readCached = (botId: string, userId: string): CachedToken | null => {
  const cached = readAny(botId);
  if (!cached || cached.userId !== userId) return null;
  if (Date.now() >= cached.expiresAt - TOKEN_REFRESH_MARGIN_MS) return null;
  return cached;
};

const storeToken = (botId: string, cached: CachedToken): void => {
  memoryTokens[botId] = cached;
  try {
    localStorage.setItem(cacheKey(botId), JSON.stringify(cached));
  } catch {
    // storage disabled — memory copy still works for this page view
  }
};

/** Force the next request to re-initialise, keeping the old token so it
 * can still be presented to `/init` as proof of identity. */
const markTokenStale = (): void => {
  const { botId } = getChatbotConfig();
  if (!botId) return;
  const cached = readAny(botId);
  if (cached) storeToken(botId, { ...cached, expiresAt: 0 });
};

const getInitUrl = (): string => {
  const { baseUrl, botId } = getChatbotConfig();
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/init`;
};

/** Returns a valid token (and the visitor id it is bound to), calling
 * `/init` when none is cached. Concurrent callers share one request. Call
 * once on mount so the visitor id is settled before the first chat. */
export const ensureWidgetIdentity = async (): Promise<{ token: string; userId: string }> => {
  const { botId } = getChatbotConfig();
  const url = getInitUrl();
  if (!botId || !url) throw new Error('widget not configured');
  const userId = getEmlyUserId();

  const cached = readCached(botId, userId);
  if (cached) return { token: cached.token, userId: cached.userId };

  if (!inflight[botId]) {
    const previous = readAny(botId);
    inflight[botId] = (async () => {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          ...(previous ? { previous_token: previous.token } : {}),
        }),
      });
      if (!res.ok) throw new Error(`widget init failed: ${res.status}`);
      const body = (await res.json()) as { token: string; user_id: string; ttl_seconds: number };
      if (body.user_id !== userId) {
        // The server kept the id our previous token proves, or issued a
        // new one; either way it is now this browser's visitor id.
        storeUserId(body.user_id);
      }
      const fresh: CachedToken = {
        token: body.token,
        userId: body.user_id,
        expiresAt: Date.now() + body.ttl_seconds * 1000,
      };
      storeToken(botId, fresh);
      return fresh;
    })().finally(() => {
      inflight[botId] = undefined;
    });
  }
  const settled = await inflight[botId]!;
  return { token: settled.token, userId: settled.userId };
};

/** `fetch` with the widget token attached. A caller-set `X-Emly-UserID`
 * is replaced by the token's visitor id so the two can't disagree. On 401
 * the token is marked stale, `/init` runs again, and the request is
 * retried once. */
export const widgetFetch = async (url: string, init: RequestInit = {}): Promise<Response> => {
  const send = async (): Promise<Response> => {
    const { token, userId } = await ensureWidgetIdentity();
    const headers = new Headers(init.headers || {});
    headers.set('Authorization', `Bearer ${token}`);
    if (headers.has('X-Emly-UserID')) headers.set('X-Emly-UserID', userId);
    return fetch(url, { ...init, headers });
  };
  const first = await send();
  if (first.status !== 401) return first;
  markTokenStale();
  return send();
};
