/**
 * Visitor identity + widget token handling.
 *
 * The backend requires `Authorization: Bearer <token>` on every widget
 * route that reads or writes per-visitor data. The token comes from
 * `POST /widget/{bot}/init`, is bound to this bot and the visitor's
 * user id, and is cached in localStorage until shortly before it expires.
 * `widgetFetch` attaches it and, on a 401, re-initialises once and retries.
 */
import { v4 as uuidv4 } from 'uuid';
import { getChatbotConfig } from '../embed';

const USER_ID_EXPIRY_DAYS = 5; // Fallback default
const EMLY_USER_ID_KEY = 'emly.chatbot.userId';
const EMLY_USER_ID_EXPIRY_KEY = 'emly.chatbot.userId.expiry';
const TOKEN_KEY_PREFIX = 'emly.widget.token.';
// Refresh a little before expiry so an in-flight request doesn't race it.
const TOKEN_REFRESH_MARGIN_MS = 60_000;

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

const readCached = (botId: string, userId: string): CachedToken | null => {
  let cached: CachedToken | null = memoryTokens[botId] ?? null;
  if (!cached) {
    try {
      const raw = localStorage.getItem(cacheKey(botId));
      cached = raw ? (JSON.parse(raw) as CachedToken) : null;
    } catch {
      cached = null;
    }
  }
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

/** Drop the cached token so the next request re-initialises. */
export const clearWidgetToken = (): void => {
  const { botId } = getChatbotConfig();
  if (!botId) return;
  delete memoryTokens[botId];
  try {
    localStorage.removeItem(cacheKey(botId));
  } catch {
    // ignore
  }
};

const getInitUrl = (): string => {
  const { baseUrl, botId } = getChatbotConfig();
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/init`;
};

/** Returns a valid token for the current visitor, calling `/init` when
 * there is none cached. Concurrent callers share one `/init` request. */
export const getWidgetToken = async (): Promise<string> => {
  const { botId } = getChatbotConfig();
  const url = getInitUrl();
  if (!botId || !url) throw new Error('widget not configured');
  const userId = getEmlyUserId();

  const cached = readCached(botId, userId);
  if (cached) return cached.token;

  if (!inflight[botId]) {
    inflight[botId] = (async () => {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId }),
      });
      if (!res.ok) throw new Error(`widget init failed: ${res.status}`);
      const body = (await res.json()) as { token: string; user_id: string; ttl_seconds: number };
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
  return (await inflight[botId]!).token;
};

/** `fetch` with the widget token attached. On 401 the token is dropped,
 * `/init` runs again, and the request is retried once. */
export const widgetFetch = async (url: string, init: RequestInit = {}): Promise<Response> => {
  const send = async (): Promise<Response> => {
    const token = await getWidgetToken();
    const headers = new Headers(init.headers || {});
    headers.set('Authorization', `Bearer ${token}`);
    return fetch(url, { ...init, headers });
  };
  const first = await send();
  if (first.status !== 401) return first;
  clearWidgetToken();
  return send();
};
