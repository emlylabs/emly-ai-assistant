import { createRoot } from 'react-dom/client';
import type { Root } from 'react-dom/client';
import { Provider } from 'react-redux';
import store from './redux/store';
import { rehydrateForBot } from './redux/messageReducer';
import ChatbotWidget from './components/ChatbotWidget';
import { CHATBOT_THEMES } from './utils/chatbotThemes';
import { setBotId } from './utils/botContext';

// Global type definitions
declare global {
  interface Window {
    ChatbotWidget?: {
      initialize: (options?: ChatbotWidgetOptions, containerId?: string) => void;
      destroy: () => void;
    };
  }
}

interface ChatbotWidgetOptions {
  copyTheme?: boolean;
  position?: 'bottom-left' | 'bottom-right' | 'top-left' | 'top-right';
  theme?: Record<string, string> | string;  // Accept theme ID (string) or raw CSS vars object
  /** Backend root, e.g. `https://api.example.com`. The widget constructs
   * `${baseUrl}/widget/${botId}/chat` and `${baseUrl}/widget/${botId}/config`. */
  baseUrl?: string;
  /** Bot id or slug — path segment in the multi-bot endpoint. */
  botId?: string;
  userIdExpiryDays?: number;
  [key: string]: any;
}

let root: Root | null = null;
const DEFAULT_CONTAINER_ID = 'emls-chatbot-embed';

let chatbotConfig = {
  baseUrl: (import.meta.env.VITE_BACKEND_BASE_URL || '').replace(/\/+$/, ''),
  botId: import.meta.env.VITE_BOT_ID || '',
  userIdExpiryDays: Number(import.meta.env.VITE_USER_ID_EXPIRY_DAYS) || 5,
};

export const getChatbotConfig = () => chatbotConfig;

/** Returns the chat endpoint URL, e.g. `https://api/example/widget/support-faq/chat`.
 * An empty `baseUrl` yields a same-origin relative URL. */
export const getChatUrl = (): string => {
  const { baseUrl, botId } = chatbotConfig;
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/chat`;
};

/** Returns the public widget config endpoint URL. Empty `baseUrl` yields a same-origin relative URL. */
export const getConfigUrl = (): string => {
  const { baseUrl, botId } = chatbotConfig;
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/config`;
};

/** Returns the form/lead-action endpoint URL. Empty `baseUrl` yields a same-origin relative URL. */
export const getActionUrl = (): string => {
  const { baseUrl, botId } = chatbotConfig;
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/action`;
};

/** Returns the per-visitor submission-counts endpoint URL. The widget
 * calls this on mount (with `X-Emly-UserID`) to populate the limit
 * footnote and gate post-limit forms. */
export const getSubmissionCountsUrl = (): string => {
  const { baseUrl, botId } = chatbotConfig;
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/submissions/counts`;
};

/** Returns the per-bot widget-impression endpoint URL. */
export const getImpressionUrl = (): string => {
  const { baseUrl, botId } = chatbotConfig;
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/impressions`;
};

/** Fires a `short` (mount/visible) or `long` (launcher opened) impression
 * for the current bot. Deduped per-tab via `sessionStorage` so reloads
 * inside the same tab can't double-count. Best-effort: failures are
 * swallowed so the widget never blocks rendering on telemetry. */
export const recordImpression = (kind: 'short' | 'long'): void => {
  const url = getImpressionUrl();
  if (!url) return;
  const { botId } = chatbotConfig;
  const sentinel = `emly:impression:${botId}:${kind}`;
  try {
    if (typeof sessionStorage !== 'undefined' && sessionStorage.getItem(sentinel)) {
      return;
    }
  } catch {
    // Private mode / storage disabled — still fire, accept the dupe.
  }
  void fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: kind }),
    keepalive: true,
  })
    .then((res) => {
      if (!res.ok) return;
      try {
        sessionStorage.setItem(sentinel, '1');
      } catch {
        // ignore
      }
    })
    .catch(() => {
      // best-effort
    });
};

/** Returns the CSAT rating endpoint URL for a specific assistant message. */
export const getRateUrl = (messageId: number): string => {
  const { baseUrl, botId } = chatbotConfig;
  if (!botId) return '';
  return `${baseUrl}/widget/${encodeURIComponent(botId)}/messages/${messageId}/rate`;
};

/** POSTs a thumbs-up/down rating for an assistant message. Mirrors the
 * `rateMessage` helper from your-assistant; throws on non-2xx so the
 * caller can roll back the optimistic UI update. */
export const rateMessage = async (
  messageId: number,
  rating: -1 | 0 | 1,
  sessionId: string,
  userId?: string,
): Promise<void> => {
  const url = getRateUrl(messageId);
  if (!url) throw new Error('rate endpoint not configured');
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({
      rating,
      session_id: sessionId,
      ...(userId ? { user_id: userId } : {}),
    }),
  });
  if (!response.ok) {
    throw new Error(`rate request failed: ${response.status}`);
  }
};

/** Snake_case mirror of `routes/widget.WidgetConfigResponse` in the backend. */
export interface WidgetBotConfig {
  bot_id: string;
  name: string;
  title?: string | null;
  subtitle?: string | null;
  welcome_message?: string | null;
  input_placeholder?: string | null;
  logo?: string | null;
  avatar?: string | null;
  launcher_position?: 'left' | 'right';
  chat_container_position?: 'left' | 'right';
  open_on_load?: boolean;
  theme?: {
    launcher_background?: string | null;
    header_background?: string | null;
    header_foreground?: string | null;
    user_message_background?: string | null;
    user_message_foreground?: string | null;
    bot_message_background?: string | null;
    bot_message_foreground?: string | null;
    container_background?: string | null;
  };
  launcher_label?: string | null;
  is_icon_with_label?: boolean | null;
  open_icon?: string | null;
  close_icon?: string | null;
  show_min_max?: boolean | null;
  show_close?: boolean | null;
  show_menu?: boolean | null;
  max_window?: boolean | null;
  open_link_in_same_tab?: boolean | null;
  feedback?: boolean | null;
  show_citations?: boolean | null;
  starter_messages?: string[] | null;
  support_email?: string | null;
  whatsapp_link?: string | null;
  whatsapp_message?: string | null;

  /** Launcher tooltip cycle. Mirrors the `nudges` sub-tree in
   * `BotConfigV1`. The widget reads `nudge[]` for entries (each with
   * `nudge_label` + `nudge_query`), `wait_time` ms before the first
   * appearance, `duration` ms each is shown, and `count` for how many
   * appearances total before stopping. */
  nudges?: NudgesConfig | null;

  /** List of single-key entries `{<name>: {form_schema, trigger}}`.
   * Backend strips private trigger keys via `_scrub_cforms`. The
   * widget reads `trigger.value` to decide where each form fires:
   * STARTER_MESSAGES, START_CHAT, PROMPT, BUTTON_ON_LINK,
   * BUTTON_ON_CARD. */
  c_forms_selected?: CFormSelected[] | null;
}

export interface NudgesConfig {
  wait_time?: number;
  duration?: number;
  count?: number;
  nudge?: NudgeEntry[];
}

export interface NudgeEntry {
  nudge_label?: string;
  nudge_query?: string;
}

/** Backend `_scrub_cforms` collapses each entry to `{form_schema, trigger}`
 * — both `Record<string, unknown>` because the inner shape is admin-defined
 * (form fields are open-ended). The widget validates fields it knows about
 * and renders unknown ones as plain text inputs. */
export interface CFormSelected {
  [name: string]: { form_schema?: Record<string, unknown>; trigger?: Record<string, unknown> };
}

/** Fetches the bot's public widget config. Returns null on any failure
 * (network, non-2xx, malformed JSON, timeout) so the widget falls back to
 * hardcoded defaults rather than refusing to render. Mirrors the
 * `fetchWidgetConfig` pattern in your-assistant/src/General/WidgetLoad.js. */
const CONFIG_FETCH_TIMEOUT_MS = 4000;
export const fetchBotConfig = async (): Promise<WidgetBotConfig | null> => {
  const url = getConfigUrl();
  if (!url) return null;
  if (typeof AbortController === 'undefined') {
    try {
      const response = await fetch(url, { method: 'GET' });
      return response.ok ? ((await response.json()) as WidgetBotConfig) : null;
    } catch {
      return null;
    }
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), CONFIG_FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(url, { method: 'GET', signal: ctrl.signal });
    if (!response.ok) return null;
    return (await response.json()) as WidgetBotConfig;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
};

const ChatbotEmbed = () => {
  return (
    <Provider store={store}>
      <ChatbotWidget />
    </Provider>
  );
};

// Main initialization function
const initializeChatbot = (options: ChatbotWidgetOptions = {}, containerId: string = DEFAULT_CONTAINER_ID) => {
  try {
    if (options.baseUrl) {
      chatbotConfig.baseUrl = options.baseUrl.replace(/\/+$/, '');
    }
    if (options.botId) {
      chatbotConfig.botId = options.botId;
    }
    if (options.userIdExpiryDays) {
      chatbotConfig.userIdExpiryDays = options.userIdExpiryDays;
    }

    // Publish the bot id to the storage layer and rehydrate session
    // state from the per-bot localStorage bucket. Both must happen
    // before render so the first paint shows the correct history.
    setBotId(chatbotConfig.botId);
    store.dispatch(rehydrateForBot());

    let container = document.getElementById(containerId);

    // Create container if it doesn't exist
    if (!container) {
      container = document.createElement('div');
      container.id = containerId;
      document.body.appendChild(container);
    }

    // Apply theme if provided
    if (options.theme) {
      let themeVars: Record<string, string>;

      if (typeof options.theme === 'string') {
        // If theme is a string ID, lookup the theme config
        const selectedTheme = CHATBOT_THEMES.find(t => t.id === options.theme);
        if (selectedTheme) {
          themeVars = selectedTheme.vars;
        } else {
          console.warn(`Theme "${options.theme}" not found. Available themes: ${CHATBOT_THEMES.map(t => t.id).join(', ')}`);
          return;
        }
      } else if (typeof options.theme === 'object') {
        // If theme is an object, use it directly
        themeVars = options.theme;
      } else {
        return;
      }

      Object.entries(themeVars).forEach(([key, value]) => {
        document.documentElement.style.setProperty(key, value as string);
      });
    }

    // Apply position class if specified
    if (options.position) {
      container.setAttribute('data-position', options.position);
    }

    root = createRoot(container);
    root.render(<ChatbotEmbed />);

    console.log('✓ Chatbot widget initialized', {
      container: containerId,
      options,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    console.error('✗ Failed to initialize chatbot:', error);
  }
};

const destroyChatbot = () => {
  try {
    if (root) {
      root.unmount();
      root = null;
      console.log('✓ Chatbot widget destroyed');
    }
  } catch (error) {
    console.error('✗ Failed to destroy chatbot:', error);
  }
};

// Export for direct use
export { initializeChatbot, destroyChatbot };

// Expose to window for script tag usage
const ChatbotWidgetAPI = {
  initialize: initializeChatbot,
  destroy: destroyChatbot,
};

// Captured at module-execution time — `document.currentScript` is null
// inside DOMContentLoaded. Falls back to scanning for a `<script>` with
// `data-bot-id` to match the your-assistant loader pattern.
const LOADER_SCRIPT: HTMLScriptElement | null = (() => {
  if (typeof document === 'undefined') return null;
  if (document.currentScript instanceof HTMLScriptElement) {
    return document.currentScript;
  }
  const scripts = document.getElementsByTagName('script');
  for (let i = scripts.length - 1; i >= 0; i--) {
    if (scripts[i].dataset && scripts[i].dataset.botId) {
      return scripts[i] as HTMLScriptElement;
    }
  }
  return null;
})();

const LOG_PREFIX = '[chatbot-widget]';

const parseBoolAttr = (value: string | undefined): boolean | undefined => {
  if (value === undefined) return undefined;
  return value === 'true' || value === '1' || value === '';
};

/** Auto-mount when the loader `<script>` carries `data-bot-id` (and
 * optional `data-base-url`, `data-mount`, etc.). Mirrors the
 * convention in your-assistant/src/widget.js. */
const autoBootFromScriptTag = () => {
  const script = LOADER_SCRIPT;
  if (!script) return;
  const ds = script.dataset;
  if (!ds.botId) return;
  if (!ds.baseUrl) {
    console.error(LOG_PREFIX, 'data-base-url is required when data-bot-id is set');
    return;
  }
  // Resolve the mount target. `data-mount` is a CSS selector; if it
  // resolves we use that element's id (creating one if missing). Else
  // we fall back to the default container id.
  let containerId = DEFAULT_CONTAINER_ID;
  if (ds.mount) {
    try {
      const target = document.querySelector(ds.mount);
      if (target) {
        if (!target.id) target.id = `emly-widget-${ds.botId}`;
        containerId = target.id;
      }
    } catch (err) {
      console.error(LOG_PREFIX, 'invalid data-mount selector:', ds.mount, err);
    }
  }
  initializeChatbot(
    {
      baseUrl: ds.baseUrl,
      botId: ds.botId,
      ...(parseBoolAttr(ds.openOnLoad) !== undefined && { openOnLoad: parseBoolAttr(ds.openOnLoad) }),
    } as ChatbotWidgetOptions,
    containerId,
  );
};

if (typeof window !== 'undefined') {
  (window as any).ChatbotWidget = ChatbotWidgetAPI;
  // Compatibility alias for embed snippets written against the
  // your-assistant API: `EmlyWidget.mount({ botId, baseUrl, mountSelector })`.
  (window as any).EmlyWidget = {
    mount: (opts: { botId?: string; baseUrl?: string; mountSelector?: string; config?: Record<string, unknown> } = {}) => {
      let containerId = DEFAULT_CONTAINER_ID;
      if (opts.mountSelector) {
        const target = document.querySelector(opts.mountSelector);
        if (target) {
          if (!target.id) target.id = `emly-widget-${opts.botId || 'mount'}`;
          containerId = target.id;
        }
      }
      initializeChatbot({ botId: opts.botId, baseUrl: opts.baseUrl, ...(opts.config || {}) }, containerId);
      return { unmount: destroyChatbot };
    },
    unmount: destroyChatbot,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoBootFromScriptTag, { once: true });
  } else {
    autoBootFromScriptTag();
  }
}

// Default export for UMD compatibility
export default ChatbotWidgetAPI;
