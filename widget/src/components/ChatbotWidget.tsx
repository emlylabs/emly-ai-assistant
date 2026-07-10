import React, { useRef, useState, useEffect } from 'react';
import './ChatbotWidget.css';
import { v4 as uuidv4 } from 'uuid';
import { MessageCircle } from 'lucide-react';
import emlyLabsLogo from '../assets/emly-labs-logo.png';
import ChatbotHeader from './ChatbotHeader';
import ChatbotBody from './ChatbotBody';
import ChatbotFooter from './ChatbotFooter';
import { useDispatch, useSelector } from 'react-redux';
import {
  addMessage as addMessageToStore,
  updateMessageForm,
  addSession,
  switchSession,
  deleteSession,
  updateSessionName,
} from '../redux/messageReducer';
import type { RootState } from '../redux/store';
import { createNewSession as createNewSessionUtil, startChatKey } from '../utils/sessionManager';
import type { MessageForm, Citation } from '../utils/sessionManager';
import { buildCandidateSummary, extractCandidateArray } from '../utils/candidateData';
import ChatSessionManager from './ChatSessionManager'; // Import ChatSessionManager
// import { CHATBOT_THEMES, resolveChatbotTheme, type ChatbotTheme } from '../utils/chatbotThemes';
import { getChatbotConfig, getChatUrl, getActionUrl, getSubmissionCountsUrl, fetchBotConfig, recordImpression } from '../embed';
import type { WidgetBotConfig, CFormSelected } from '../embed';
import Nudge from './Nudge';

const USER_ID_EXPIRY_DAYS = 5;  // Fallback default
const EMLY_USER_ID_KEY = 'emly.chatbot.userId';
const EMLY_USER_ID_EXPIRY_KEY = 'emly.chatbot.userId.expiry';

const getEmlyUserId = (): string => {
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

/* Theme utility functions - hidden
const readStoredThemeId = (): string | null => {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    return null;
  }
};

const writeStoredThemeId = (themeId: string) => {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, themeId);
  } catch {
    // Ignore storage failures (private mode, disabled storage, etc.).
  }
};
*/

const extractBotReply = (payload: unknown): string | null => {
  const priorityKeys = ['message', 'content', 'reply', 'response', 'text', 'output', 'answer'];
  const ignoredStringKeys = new Set([
    'role',
    'id',
    'object',
    'model',
    'created',
    'prompt',
    'input',
    'query',
  ]);

  const visit = (value: unknown, depth: number): string | null => {
    if (depth > 10) {
      return null;
    }

    if (typeof value === 'string') {
      const trimmed = value.trim();
      return trimmed !== '' ? trimmed : null;
    }

    if (Array.isArray(value)) {
      for (const item of value) {
        const reply = visit(item, depth + 1);
        if (reply) {
          return reply;
        }
      }
      return null;
    }

    if (value && typeof value === 'object') {
      const record = value as Record<string, unknown>;

      for (const key of priorityKeys) {
        if (!(key in record)) {
          continue;
        }

        const reply = visit(record[key], depth + 1);
        if (reply) {
          return reply;
        }
      }

      for (const [key, nested] of Object.entries(record)) {
        if (priorityKeys.includes(key)) {
          continue;
        }
        if (typeof nested === 'string' && ignoredStringKeys.has(key)) {
          continue;
        }

        const reply = visit(nested, depth + 1);
        if (reply) {
          return reply;
        }
      }
    }

    return null;
  };

  return visit(payload, 0);
};

// Bot-streamed `<internal_code>{trigger_code}</internal_code>` markers
// signal which configured form to render. The `>?` on the close tag
// stays as a tolerance for legacy responses produced before the
// backend prompt emitted a properly-closed tag. Mirrors
// `Utility/index.js extractInternalCode` from your-assistant.
const INTERNAL_CODE_RE = /<internal_code>(.*?)<\/internal_code>?/s;

const extractInternalCode = (text: string): { code: string | null; cleaned: string } => {
  const m = INTERNAL_CODE_RE.exec(text);
  if (!m) return { code: null, cleaned: text };
  return { code: m[1].trim(), cleaned: text.replace(INTERNAL_CODE_RE, '').trim() };
};

// `c_forms_selected` is a list of single-key entries — flatten to
// `{ name, formSchema, trigger }` so trigger lookups don't have to
// rediscover that each entry has exactly one key.
interface FlatForm {
  name: string;
  formSchema: Record<string, unknown>;
  trigger: Record<string, unknown>;
}

const flattenCForms = (forms?: CFormSelected[] | null): FlatForm[] => {
  if (!Array.isArray(forms)) return [];
  const out: FlatForm[] = [];
  for (const entry of forms) {
    if (!entry || typeof entry !== 'object') continue;
    for (const [name, body] of Object.entries(entry)) {
      out.push({
        name,
        formSchema: (body?.form_schema ?? {}) as Record<string, unknown>,
        trigger: (body?.trigger ?? {}) as Record<string, unknown>,
      });
    }
  }
  return out;
};

const findFormByTriggerCode = (forms: FlatForm[], code: string): FlatForm | null =>
  forms.find(f => (f.trigger?.trigger_code as string | undefined) === code) ?? null;

const formsByTriggerValue = (forms: FlatForm[], value: string): FlatForm[] =>
  forms.filter(f => (f.trigger?.value as string | undefined)?.toUpperCase() === value);

const resolveBotResponse = (payload: unknown): { text: string; data?: unknown } => {
  // Check if the payload has a 'data' key first (for DynamicDataRenderer)
  if (typeof payload === 'object' && payload !== null && 'data' in payload) {
    const rawData = (payload as Record<string, unknown>).data;
    const extractedText = extractBotReply(payload) ?? 'The chatbot did not return a readable response.';
    return { text: extractedText, data: rawData };
  }

  // Then try to extract as candidates for CandidateAnalytics
  const candidates = extractCandidateArray(payload);
  if (candidates) {
    return {
      text: buildCandidateSummary(candidates),
      data: candidates,
    };
  }

  const extractedText = extractBotReply(payload) ?? 'The chatbot did not return a readable response.';

  try {
    const parsed = JSON.parse(extractedText) as unknown;
    const parsedCandidates = extractCandidateArray(parsed);
    if (parsedCandidates) {
      return {
        text: buildCandidateSummary(parsedCandidates),
        data: parsedCandidates,
      };
    }
  } catch {
    // Keep plain text if parsing fails.
  }

  return { text: extractedText };
};

interface ChatbotWidgetProps {
  headerIcon?: string;
  headerBackground?: string;
  headerForeground?: string;
  bodyBackground?: string;
  userMessageBackground?: string;
  userMessageForeground?: string;
  botMessageBackground?: string;
  botMessageForeground?: string;
  footerBackground?: string;
  title?: string;
  subtitle?: string;
  headline?: string;
  showHistory?: boolean;
  placeholderMessage?: string;
  defaultThemeId?: string;
  showThemeButton?: boolean;
  // themes?: ChatbotTheme[];
}

const ChatbotWidget: React.FC<ChatbotWidgetProps> = ({
  headerIcon,
  headerBackground = 'linear-gradient(135deg, #2563eb 0%, #06b6d4 100%)',
  headerForeground = '#ffffff',
  bodyBackground = '#f8fafc',
  userMessageBackground = '#2563eb',
  userMessageForeground = '#ffffff',
  botMessageBackground = '#ffffff',
  botMessageForeground = '#0f172a',
  footerBackground = '#ffffff',
  title = '',
  subtitle = '',
  headline = '',
  showHistory = true,
  placeholderMessage = 'Type a message…',
  // defaultThemeId = 'ocean',
  // showThemeButton = true,
  // themes = CHATBOT_THEMES,
}) => {
  const [newMessage, setNewMessage] = useState('');
  const [isOpen, setIsOpen] = useState(false);
  const [isMaximized, setIsMaximized] = useState(false);
  const [botIsTyping, setBotIsTyping] = useState(false); // New state for bot typing indicator
  const [isClosing, setIsClosing] = useState(false);
  const [isOpening, setIsOpening] = useState(false);
  const [isFirstLoad, setIsFirstLoad] = useState(true); // New state for animation trigger
  const [showSessionDropdown, setShowSessionDropdown] = useState(false); // State for session dropdown visibility
  const [sessionIsTransitioning, setSessionIsTransitioning] = useState(false);
  // const [showThemeMenu, setShowThemeMenu] = useState(false);
  // const [themeId, setThemeId] = useState<string>(() => readStoredThemeId() ?? defaultThemeId);
  const sessionTransitionTimeoutRef = useRef<number | null>(null);
  const dispatch = useDispatch();
  const { messages, sessions, activeSessionId } = useSelector((state: RootState) => state.messages);
  const firstSessionId = sessions[0]?.id ?? null;

  // Pull title/headline/placeholder/logo/theme from the backend's
  // `/widget/{botId}/config` so admin-edited values render without a
  // rebuild. Falls through to props (then hardcoded defaults) on
  // failure or missing fields.
  const [botConfig, setBotConfig] = useState<WidgetBotConfig | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchBotConfig().then((cfg) => {
      if (cancelled) return;
      setBotConfig(cfg);
      // Honor `open_on_load` once on first config delivery — match the
      // initialization flow in your-assistant's WidgetLoad.
      if (cfg?.open_on_load) setIsOpen(true);
      if (cfg?.max_window) setIsMaximized(true);
    });
    recordImpression('short');
    return () => {
      cancelled = true;
    };
  }, []);

  // Pre-chat capture gate. While true, the footer is disabled and a
  // START_CHAT form bubble is the only thing on screen. Cleared on
  // successful submit (and persisted to localStorage so future loads
  // skip the gate).
  const [pendingStartChat, setPendingStartChat] = useState(false);

  // Per-form submission counts (server-truth) for the "N of M
  // remaining" footnote and post-limit engagement bubble. Keyed by
  // `action_value` (= the form_title written on submit).
  const [formCounts, setFormCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    const url = getSubmissionCountsUrl();
    if (!url) return;
    let cancelled = false;
    const userId = getEmlyUserId();
    fetch(url, { headers: { 'X-Emly-UserID': userId } })
      .then(res => (res.ok ? res.json() : null))
      .then((body: { counts?: Record<string, number> } | null) => {
        if (cancelled || !body?.counts) return;
        setFormCounts(body.counts);
      })
      .catch(() => {
        // Counts are best-effort; on failure the footnote stays hidden
        // and the gate stays open. Don't break the chat over this.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const cfgTheme = botConfig?.theme ?? {};
  const resolvedTitle = botConfig?.title || botConfig?.name || title;
  const resolvedSubtitle = botConfig?.subtitle || subtitle;
  const resolvedHeadline = botConfig?.welcome_message || headline;
  const resolvedPlaceholder = botConfig?.input_placeholder || placeholderMessage;
  const resolvedLauncherPosition = botConfig?.launcher_position || 'right';
  const resolvedLauncherLabel = botConfig?.is_icon_with_label ? botConfig?.launcher_label || '' : '';
  const resolvedOpenIcon = botConfig?.open_icon || '';
  const resolvedCloseIcon = botConfig?.close_icon || '';
  const resolvedShowMinMax = botConfig?.show_min_max !== false;
  const resolvedShowClose = botConfig?.show_close !== false;
  // History menu defaults ON; hidden only when the admin explicitly disables it.
  const resolvedShowHistory = showHistory && botConfig?.show_menu !== false;
  const resolvedStarterMessages = Array.isArray(botConfig?.starter_messages)
    ? botConfig!.starter_messages!.filter((s): s is string => typeof s === 'string' && s.trim() !== '')
    : [];
  const showStarterMessages = resolvedStarterMessages.length > 0 && !messages.some((m) => m.isUserMessage);
  const resolvedHeaderIcon = botConfig?.logo || headerIcon;
  const resolvedHeaderBackground = cfgTheme.header_background || headerBackground;
  const resolvedHeaderForeground = cfgTheme.header_foreground || headerForeground;
  const resolvedBodyBackground = cfgTheme.container_background || bodyBackground;
  const resolvedUserMessageBackground = cfgTheme.user_message_background || userMessageBackground;
  const resolvedUserMessageForeground = cfgTheme.user_message_foreground || userMessageForeground;
  const resolvedBotMessageBackground = cfgTheme.bot_message_background || botMessageBackground;
  const resolvedBotMessageForeground = cfgTheme.bot_message_foreground || botMessageForeground;

  const resolvedTheme: { name: string; id: string; vars: Record<string, any>; swatch: string } = {
    name: 'Default',
    id: 'default',
    vars: {},
    swatch: '#ffffff'
  };

  /* Theme management - hidden
  const resolvedTheme = useMemo(() => {
    const fromList = themes.find(theme => theme.id === themeId);
    if (fromList) {
      return fromList;
    }
    return resolveChatbotTheme(themeId);
  }, [themeId, themes]);

  useEffect(() => {
    writeStoredThemeId(themeId);
  }, [themeId]);
  */

  useEffect(() => {
    // Ensure there's always an active session
    if (!activeSessionId && sessions.length === 0) {
      const newSession = createNewSessionUtil("Session 1");
      dispatch(addSession(newSession));
      dispatch(switchSession(newSession.id));
    } else if (!activeSessionId && firstSessionId) {
      dispatch(switchSession(firstSessionId));
    }
  }, [activeSessionId, sessions.length, firstSessionId, dispatch]);

  const addMessage = (
    text: string,
    isUserMessage: boolean,
    data?: unknown,
    form?: MessageForm,
    messageId?: number,
    citations?: Citation[],
  ) => {
    const id = uuidv4();
    const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: true });
    dispatch(addMessageToStore({ id, text, isUserMessage, timestamp, data, form, messageId, citations }));
    return id;
  };

  // Flat lookup table for `c_forms_selected` rebuilt only when config
  // changes. Used by every trigger path (PROMPT match, STARTER chip,
  // BUTTON_ON_LINK, BUTTON_ON_CARD).
  const cForms = React.useMemo(
    () => flattenCForms(botConfig?.c_forms_selected),
    [botConfig?.c_forms_selected],
  );

  // The widget submits with `form_title = schema.name ?? schema.id ??
  // form.name` (see JsonToForm.handleSubmit). Counts are keyed by the
  // same string so the lookup matches what was written.
  const formTitleOf = (form: FlatForm): string => {
    const schema = form.formSchema as { name?: unknown; id?: unknown };
    const name = typeof schema.name === 'string' ? schema.name : undefined;
    const id = typeof schema.id === 'string' ? schema.id : undefined;
    return name ?? id ?? form.name;
  };

  /** Append a form bubble as a bot message. The trigger's
   * `user_confirmation` flag is captured up-front so the bubble starts
   * in the right state. When the trigger sets `allow_limit && limit`
   * and the visitor has already hit it, the bubble starts in
   * `post-limit` mode instead of showing the form fields. */
  const addFormMessage = (form: FlatForm) => {
    const userConfirmation = Boolean(form.trigger.user_confirmation);
    const allowLimit = Boolean(form.trigger.allow_limit);
    const limitRaw = form.trigger.limit;
    const limit = typeof limitRaw === 'number' && limitRaw > 0 ? limitRaw : null;
    const formId = formTitleOf(form);
    const current = formCounts[formId] ?? 0;
    if (allowLimit && limit !== null && current >= limit) {
      addMessage('', false, undefined, {
        formSchema: form.formSchema,
        trigger: form.trigger,
        status: 'post-limit',
      });
      return;
    }
    addMessage('', false, undefined, {
      formSchema: form.formSchema,
      trigger: form.trigger,
      status: 'pending',
      confirm: userConfirmation ? 'pending' : 'accepted',
      formCount: allowLimit && limit !== null ? { current, limit } : undefined,
    });
  };

  // POST a JSON-only payload (no files) to the action URL. Used by
  // OTP send/verify; the auth_authentication / auth_verification paths
  // in the backend never need attachments.
  const postActionPayload = async (
    payloadObj: Record<string, unknown>,
  ): Promise<{ ok: boolean; status: number; message?: string }> => {
    const actionUrl = getActionUrl();
    if (!actionUrl) return { ok: false, status: 0, message: 'not configured' };
    const fd = new FormData();
    fd.append('payload', JSON.stringify(payloadObj));
    const userId = getEmlyUserId();
    const sessionId = activeSessionId || `session-${uuidv4()}`;
    const res = await fetch(actionUrl, {
      method: 'POST',
      headers: { 'X-Emly-UserID': userId, 'X-Emly-SessionID': sessionId },
      body: fd,
    });
    let body: { message?: string } = {};
    try { body = (await res.json()) as { message?: string }; } catch { /* non-JSON */ }
    return { ok: res.ok, status: res.status, message: body.message };
  };

  /** OTP send — used both for the initial submit-with-verify path and
   * the Resend chip. Returns null on success or an error message. */
  const sendOtp = async (email: string): Promise<string | null> => {
    const userId = getEmlyUserId();
    const sessionId = activeSessionId || `session-${uuidv4()}`;
    const result = await postActionPayload({
      user_id: email,  // OTP backend uses the email as user_id
      session_id: sessionId,
      form_title: 'auth_authentication',
      otp_type: 'email',
      // Pass the visitor's persistent id alongside, so the action row
      // (created in the generic branch later) ties back to them.
      visitor_id: userId,
      email,
    });
    if (!result.ok) return result.message || `Failed to send code (${result.status})`;
    return null;
  };

  /** OTP verify — the inverse half. Returns null on success. */
  const verifyOtp = async (email: string, otp: string): Promise<string | null> => {
    const sessionId = activeSessionId || `session-${uuidv4()}`;
    const result = await postActionPayload({
      user_id: email,
      session_id: sessionId,
      form_title: 'auth_verification',
      otp,
      email,
    });
    if (!result.ok) return result.message || 'Invalid code';
    return null;
  };

  const messageFormById = (id: string): MessageForm | undefined => {
    const msg = messages.find(m => m.id === id);
    return msg?.form;
  };

  // Form bubble handlers — submit/cancel/confirm patch the saved
  // message via the reducer so re-renders (and persisted history)
  // stay in sync.
  const handleFormSubmit = async (messageId: string, values: Record<string, unknown>) => {
    const actionUrl = getActionUrl();
    if (!actionUrl) {
      addMessage('Form submission is not configured.', false);
      return;
    }
    // OTP gate — when the trigger asks for verification AND the form
    // has an email field with a value, swap to the OTP entry sub-step
    // before posting. The visitor's typed values are stashed on the
    // message and replayed by `replaySubmitAfterOtp` on success.
    const stored = messageFormById(messageId);
    const verifyAction = Boolean(stored?.trigger?.verify_action);
    const emailValue = typeof values.email === 'string'
      ? values.email
      : typeof values.work_email === 'string'
        ? values.work_email
        : null;
    const alreadyVerified = stored?.otpStep === 'awaitingOtp';
    if (verifyAction && emailValue && !alreadyVerified) {
      const err = await sendOtp(emailValue);
      if (err) {
        addMessage(`Couldn't send the verification code: ${err}`, false);
        return;
      }
      dispatch(updateMessageForm({
        id: messageId,
        form: {
          otpStep: 'awaitingOtp',
          otpEmail: emailValue,
          pendingValues: values,
        },
      }));
      return;
    }
    const userId = getEmlyUserId();
    const sessionId = activeSessionId || `session-${uuidv4()}`;
    // Match the `/widget/{bot}/action` contract from `routes/widget.py`:
    // multipart/form-data with a JSON `payload` part plus optional
    // repeated `files` parts. Partition `values` so File / File[]
    // entries become parts and don't get JSON-serialized into `{}`.
    const fd = new FormData();
    const fileEntries: File[] = [];
    const primitivePayload: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(values)) {
      if (v instanceof File) {
        fileEntries.push(v);
      } else if (Array.isArray(v) && v.some(item => item instanceof File)) {
        for (const item of v) {
          if (item instanceof File) fileEntries.push(item);
        }
      } else {
        primitivePayload[k] = v;
      }
    }
    const payload = {
      user_id: userId,
      session_id: sessionId,
      action_name: 'form_submit',
      action_value: primitivePayload.form_title,
      action_payload: primitivePayload,
      form_title: primitivePayload.form_title,
      first_name: primitivePayload.first_name ?? primitivePayload.full_name ?? primitivePayload.name,
      email: primitivePayload.email ?? primitivePayload.work_email,
      phone: primitivePayload.phone,
    };
    fd.append('payload', JSON.stringify(payload));
    for (const file of fileEntries) {
      fd.append('files', file, file.name);
    }
    try {
      const res = await fetch(actionUrl, {
        method: 'POST',
        headers: { 'X-Emly-UserID': userId, 'X-Emly-SessionID': sessionId },
        body: fd,
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
      dispatch(updateMessageForm({ id: messageId, form: { status: 'submitted' } }));
      // Bump the local count optimistically so opening the same form
      // again in this session sees the latest remaining-count without
      // a refetch. The server is still the source of truth on next mount.
      const formId = primitivePayload.form_title;
      if (typeof formId === 'string' && formId !== '') {
        setFormCounts(prev => ({ ...prev, [formId]: (prev[formId] ?? 0) + 1 }));
      }
      // If this submission cleared the START_CHAT gate, persist the
      // captured values and re-enable the footer. The footer was
      // disabled while `pendingStartChat` was true, so the only form
      // reachable in that state was the start-chat form itself.
      if (pendingStartChat) {
        const botId = getChatbotConfig().botId;
        if (botId) {
          try {
            const persisted = { ...primitivePayload };
            delete (persisted as Record<string, unknown>).submit;
            delete (persisted as Record<string, unknown>).form_title;
            localStorage.setItem(startChatKey(botId), JSON.stringify(persisted));
          } catch {
            // localStorage may be disabled (private mode) — failing to
            // persist just means the visitor sees the gate again next
            // load, which is degraded-but-not-broken.
          }
        }
        setPendingStartChat(false);
      }
    } catch (err) {
      console.error('form submission failed', err);
      addMessage("We couldn't submit the form. Please try again.", false);
    }
  };

  const handleFormCancel = (messageId: string) => {
    dispatch(updateMessageForm({ id: messageId, form: { status: 'cancelled' } }));
  };

  /** Bridges the OTP entry's Verify button to the action endpoint, then
   * replays the held-back submit on success. Returns an error message
   * for the OTP step to display, or null on success. */
  const handleVerifyOtp = async (messageId: string, otp: string): Promise<string | null> => {
    const stored = messageFormById(messageId);
    const email = stored?.otpEmail;
    if (!email) return 'Verification context lost — please re-open the form.';
    const verifyErr = await verifyOtp(email, otp);
    if (verifyErr) return verifyErr;
    // Clear the OTP step so handleFormSubmit's `alreadyVerified`
    // check skips re-sending. The pending values were stored when we
    // entered the OTP step.
    dispatch(updateMessageForm({
      id: messageId,
      form: { otpStep: 'none' },
    }));
    const pending = stored?.pendingValues;
    if (pending) await handleFormSubmit(messageId, pending);
    return null;
  };

  const handleResendOtp = async (messageId: string): Promise<string | null> => {
    const stored = messageFormById(messageId);
    const email = stored?.otpEmail;
    if (!email) return 'Verification context lost — please re-open the form.';
    return sendOtp(email);
  };

  const handleFormConfirm = (messageId: string, choice: 'accepted' | 'declined') => {
    dispatch(updateMessageForm({ id: messageId, form: { confirm: choice } }));
  };

  // START_CHAT gate — render the configured pre-chat capture form on
  // mount and block the footer until it submits. Skipped if the
  // visitor already submitted on a prior load (localStorage hit) or
  // if no START_CHAT form is configured.
  useEffect(() => {
    if (!botConfig) return;
    const botId = getChatbotConfig().botId;
    if (!botId) return;
    if (pendingStartChat) return;
    if (messages.length > 0) return;  // already past the gate
    const startChatForm = formsByTriggerValue(cForms, 'START_CHAT')[0];
    if (!startChatForm) return;
    try {
      if (localStorage.getItem(startChatKey(botId))) return;
    } catch {
      // If localStorage is unreadable, fall through and re-show the
      // gate. The persist on submit also no-ops, so this is at worst
      // a per-load nag — not a hard break.
    }
    setPendingStartChat(true);
    addMessage('', false, undefined, {
      formSchema: startChatForm.formSchema,
      trigger: startChatForm.trigger,
      status: 'pending',
      confirm: 'accepted',
    });
    // We deliberately do not list `messages.length` so the gate fires
    // exactly once per fresh widget mount; switching sessions later
    // shouldn't re-trigger the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botConfig, cForms]);

  // Action chips rendered inside `ChatbotBody` after applicable bot
  // messages. Recomputed only when the form config changes.
  const chipLabel = (form: FlatForm): string =>
    (form.trigger.trigger_button_title as string)
    || (form.trigger.label as string)
    || form.name;

  const linkActions = React.useMemo(
    () => formsByTriggerValue(cForms, 'BUTTON_ON_LINK').map(form => ({
      key: form.name,
      label: chipLabel(form),
      onClick: () => addFormMessage(form),
    })),
    // addFormMessage closes over `dispatch` (stable) — recomputing on
    // every render would needlessly re-render ChatbotBody.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cForms],
  );
  const cardActions = React.useMemo(
    () => formsByTriggerValue(cForms, 'BUTTON_ON_CARD').map(form => ({
      key: form.name,
      label: chipLabel(form),
      onClick: () => addFormMessage(form),
    })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cForms],
  );

  const getBotResponse = async (userMessage: string) => {
    setBotIsTyping(true);

    const chatUrl = getChatUrl();
    if (!chatUrl) {
      setBotIsTyping(false);
      addMessage(
        'Chatbot is not configured. Set VITE_BOT_ID, or initialize with { botId } (and optionally { baseUrl }).',
        false,
      );
      return;
    }

    const userId = getEmlyUserId();
    const sessionId = activeSessionId || `session-${uuidv4()}`;

    try {
      // The backend (`POST /widget/{botId}/chat`) always returns NDJSON.
      // We use `stream:true` so token chunks come through and the final
      // `done` chunk carries the persisted `message_id` (the non-stream
      // path returns a hardcoded placeholder). Tokens are accumulated
      // before rendering — the widget UX is still single-shot, but the
      // rating feature needs the real id from the done chunk.
      const response = await fetch(chatUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Emly-UserID': userId,
          'X-Emly-SessionID': sessionId,
        },
        body: JSON.stringify({
          user_id: userId,
          session_id: sessionId,
          timestamp: Date.now(),
          messages: [{ role: 'user', content: userMessage }],
          stream: true,
        }),
      });

      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }

      const raw = await response.text();
      let accumulated = '';
      let messageId: number | undefined;
      let citations: Citation[] | undefined;
      for (const line of raw.split('\n')) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const obj = JSON.parse(trimmed) as {
            message?: { content?: string };
            done?: boolean;
            message_id?: number | null;
            citations?: Citation[];
          };
          const content = obj?.message?.content;
          if (typeof content === 'string' && content.length > 0) {
            accumulated += content;
          }
          if (obj?.done && typeof obj.message_id === 'number') {
            messageId = obj.message_id;
          }
          if (Array.isArray(obj?.citations) && obj.citations.length > 0) {
            citations = obj.citations;
          }
          // NB: don't run candidate/data extraction on the chunk envelope.
          // The `done` chunk carries the RAG citations array, and each
          // citation's `metadata.title` makes it look like a candidate
          // record — that path would dump citations into `Message.data`
          // and render them as a card regardless of `show_citations`.
        } catch {
          // Skip malformed lines; the next line may still be valid JSON.
        }
      }

      // Only run candidate-payload detection on the assembled assistant
      // reply — i.e. when the LLM returned a JSON-stringified candidate
      // list inline. Pass through the normalized text + extracted data.
      const resolved = accumulated
        ? resolveBotResponse({ message: { content: accumulated } })
        : { text: 'No response received.', data: undefined };
      const botResponse: { text: string; data?: unknown } = {
        text: resolved.text || accumulated || 'No response received.',
        data: resolved.data,
      };

      // Look for a `<internal_code>` marker; if it matches a
      // configured PROMPT-trigger, drop a form bubble after the
      // (cleaned) text. This is the fork in `Chat.js` where bot
      // markdown was scanned for `internal_code` tags.
      const { code, cleaned } = extractInternalCode(botResponse.text);
      if (cleaned) {
        addMessage(cleaned, false, botResponse.data, undefined, messageId, citations);
      }
      if (code) {
        const matched = findFormByTriggerCode(cForms, code);
        if (matched) addFormMessage(matched);
      }
    } catch (error) {
      console.error('Failed to fetch chatbot response:', error);
      addMessage('Unable to reach the chatbot service right now.', false);
    } finally {
      setBotIsTyping(false);
    }
  };

  const sendMessage = (text: string) => {
    const messageToSend = text.trim();
    if (messageToSend === '') return;
    // While the START_CHAT capture form is pending, swallow messages —
    // the footer is also disabled, but defend against programmatic
    // calls (nudge auto-send, post-limit chip) that bypass the input.
    if (pendingStartChat) return;
    // Auto-name the session from the first user query
    if (activeSessionId && messages.length === 0) {
      const sessionLabel = messageToSend.length > 30
        ? messageToSend.slice(0, 30) + '…'
        : messageToSend;
      dispatch(updateSessionName({ sessionId: activeSessionId, newName: sessionLabel }));
    }
    addMessage(messageToSend, true);
    setNewMessage('');
    void getBotResponse(messageToSend);
  };

  const handleSendMessage = () => sendMessage(newMessage);

  const handleClose = () => {
    setIsOpen(false); // Close the chatbot immediately
    setIsMaximized(false);
    // setShowThemeMenu(false);
    setShowSessionDropdown(false);
    setIsClosing(true); // Trigger animation on the chat icon
    setTimeout(() => {
      setIsClosing(false); // Reset animation state after duration
    }, 600); // Duration of the animation
  };

  const handleOpen = () => {
    setIsFirstLoad(false);
    setIsOpening(true);
    setTimeout(() => {
      setIsOpen(true);
      setIsOpening(false);
    }, 450); // Match animation duration
  };

  useEffect(() => {
    if (isOpen) recordImpression('long');
  }, [isOpen]);

  const startSessionTransition = (minimumMs = 450) => {
    setSessionIsTransitioning(true);

    if (sessionTransitionTimeoutRef.current) {
      window.clearTimeout(sessionTransitionTimeoutRef.current);
    }

    sessionTransitionTimeoutRef.current = window.setTimeout(() => {
      setSessionIsTransitioning(false);
      sessionTransitionTimeoutRef.current = null;
    }, minimumMs);
  };

  useEffect(() => {
    return () => {
      if (sessionTransitionTimeoutRef.current) {
        window.clearTimeout(sessionTransitionTimeoutRef.current);
      }
    };
  }, []);

  const handleNewSession = () => {
    startSessionTransition();
    const newSession = createNewSessionUtil();
    dispatch(addSession(newSession));
    dispatch(switchSession(newSession.id));
    setShowSessionDropdown(false);
  };

  const handleSwitchSession = (sessionId: string) => {
    startSessionTransition();
    dispatch(switchSession(sessionId));
    setShowSessionDropdown(false);
  };

  const handleDeleteSession = (sessionId: string) => {
    startSessionTransition();
    dispatch(deleteSession(sessionId));
    setShowSessionDropdown(false);
  };

  const handleToggleSessionDropdown = () => {
    setShowSessionDropdown(prev => !prev);
    // setShowThemeMenu(false);
  };

  const handleToggleMaximize = () => {
    setIsMaximized(prev => !prev);
  };

  /* Theme handlers - hidden
  const handleToggleThemeMenu = () => {
    if (!showThemeButton) {
      return;
    }
    setShowThemeMenu(prev => !prev);
    setShowSessionDropdown(false);
  };

  const handleSelectTheme = (nextThemeId: string) => {
    setThemeId(nextThemeId);
    setShowThemeMenu(false);
  };

  useEffect(() => {
    if (!showThemeMenu) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) {
        return;
      }
      if (target.closest('.emw-theme-menu') || target.closest('.emw-theme-button')) {
        return;
      }
      setShowThemeMenu(false);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setShowThemeMenu(false);
      }
    };

    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [showThemeMenu]);
  */

  return (
    <>
      {!isOpen && (
        <>
          <button
            type="button"
            aria-label="Open chat"
            className={`chat-icon chat-icon--${resolvedLauncherPosition} ${resolvedLauncherLabel ? 'chat-icon--with-label' : ''} ${isFirstLoad ? 'animate-entry' : ''} ${isClosing ? 'animate-rotate' : ''} ${isOpening ? 'animate-rotate-out' : ''}`}
            onClick={handleOpen}
          >
            {resolvedOpenIcon ? (
              <img src={resolvedOpenIcon} alt="" className="chat-icon-img" />
            ) : (
              <MessageCircle size={28} aria-hidden="true" />
            )}
            {resolvedLauncherLabel && <span className="chat-icon-label">{resolvedLauncherLabel}</span>}
          </button>
          <Nudge
            config={botConfig?.nudges}
            position={resolvedLauncherPosition === 'left' ? 'left' : 'right'}
            onClick={(query) => {
              // Match the launcher-nudge contract from your-assistant: open
              // the chat and send the configured `nudge_query` as the
              // first user turn, so the visitor lands inside an answer.
              handleOpen();
              setTimeout(() => sendMessage(query), 500);
            }}
          />
        </>
      )}

      {isOpen && (
        <div
          className={`emls-chatbot-widget emls-chatbot-widget--${resolvedLauncherPosition} ${isMaximized ? 'emls-chatbot-widget-maximized' : ''}`}
          style={{
            ...resolvedTheme.vars,
            '--header-background': resolvedTheme.vars['--cb-accent'] ?? resolvedHeaderBackground,
            '--header-foreground': resolvedTheme.vars['--cb-header-fg'] ?? resolvedHeaderForeground,
            '--body-background': resolvedTheme.vars['--cb-body-bg'] ?? resolvedBodyBackground,
            '--user-message-background': resolvedTheme.vars['--cb-user-bg'] ?? resolvedUserMessageBackground,
            '--user-message-foreground': resolvedTheme.vars['--cb-user-fg'] ?? resolvedUserMessageForeground,
            '--bot-message-background': resolvedTheme.vars['--cb-bot-bg'] ?? resolvedBotMessageBackground,
            '--bot-message-foreground': resolvedTheme.vars['--cb-bot-fg'] ?? resolvedBotMessageForeground,
            '--footer-background': resolvedTheme.vars['--cb-panel'] ?? footerBackground,
          } as React.CSSProperties}
        >
          {sessionIsTransitioning && (
            <div className="emw-session-overlay" aria-label="Switching session" role="status">
              <div className="emw-session-spinner" aria-hidden="true" />
            </div>
          )}
          <ChatbotHeader
            headerIcon={resolvedHeaderIcon}
            closeIconUrl={resolvedCloseIcon}
            onClose={handleClose}
            onToggleMaximize={handleToggleMaximize}
            isMaximized={isMaximized}
            title={resolvedTitle}
            subtitle={resolvedSubtitle}
            headline={resolvedHeadline}
            onNewSession={handleNewSession}
            showHistory={resolvedShowHistory}
            showMinMax={resolvedShowMinMax}
            showClose={resolvedShowClose}
            onToggleSessionDropdown={handleToggleSessionDropdown}
          />
          {/* Theme menu - hidden
          {showThemeMenu && showThemeButton && (
            <div className="emw-theme-menu" role="menu" aria-label="Theme selection">
              {themes.map(theme => (
                <button
                  key={theme.id}
                  type="button"
                  className={`emw-theme-menu__item ${theme.id === themeId ? 'is-active' : ''}`}
                  onClick={() => handleSelectTheme(theme.id)}
                  title={theme.name}
                  aria-label={`${theme.name} theme${theme.id === themeId ? ' (selected)' : ''}`}
                >
                  <span className="emw-theme-menu__swatch" style={{ background: theme.swatch }} aria-hidden="true" />
                </button>
              ))}
            </div>
          )}
          */}
          {showSessionDropdown && resolvedShowHistory && (
            <ChatSessionManager
              headerIcon={resolvedHeaderIcon}
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSwitchSession={handleSwitchSession}
              onDeleteSession={handleDeleteSession}
              showHistory={resolvedShowHistory}
              onToggleSessionDropdown={handleToggleSessionDropdown}
            />
          )}
          {(showStarterMessages || (formsByTriggerValue(cForms, 'STARTER_MESSAGES').length > 0 && !messages.some(m => m.isUserMessage))) && (
            <ul className="starter-messages" role="list">
              {resolvedStarterMessages.map((msg, i) => (
                <li key={`s-${i}`}>
                  <button type="button" onClick={() => sendMessage(msg)}>{msg}</button>
                </li>
              ))}
              {formsByTriggerValue(cForms, 'STARTER_MESSAGES').map((form, i) => {
                // The chip label is whatever the admin set for
                // `quick_start_string` on the trigger; falls back to the
                // form's name for diagnosability.
                const label = (form.trigger.quick_start_string as string) || form.name;
                return (
                  <li key={`f-${i}`}>
                    <button type="button" onClick={() => addFormMessage(form)}>{label}</button>
                  </li>
                );
              })}
            </ul>
          )}
          <ChatbotBody
            chatIcon={resolvedHeaderIcon}
            messages={messages}
            botIsTyping={botIsTyping}
            openLinkInSameTab={Boolean(botConfig?.open_link_in_same_tab)}
            onFormSubmit={handleFormSubmit}
            onFormCancel={handleFormCancel}
            onFormConfirm={handleFormConfirm}
            onPostLimitEngage={(query) => sendMessage(query)}
            onVerifyOtp={handleVerifyOtp}
            onResendOtp={handleResendOtp}
            linkActions={linkActions}
            cardActions={cardActions}
            showFeedback={Boolean(botConfig?.feedback)}
            sessionId={activeSessionId}
            userId={getEmlyUserId()}
            showCitations={Boolean(botConfig?.show_citations)}
          />
          <ChatbotFooter
            newMessage={newMessage}
            setNewMessage={setNewMessage}
            handleSendMessage={handleSendMessage}
            placeholderMessage={pendingStartChat ? 'Please complete the form to start chatting…' : resolvedPlaceholder}
            botIsTyping={botIsTyping || pendingStartChat}
          />
          <div className="emly-attribution" aria-label="Branding">
            <span>Powered by</span>
            <a
              href="https://emlylabs.com/"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="Emly Labs"
            >
              <img src={emlyLabsLogo} alt="Emly Labs" />
            </a>
          </div>

        </div>
      )}
    </>
  );
};

export default ChatbotWidget;
