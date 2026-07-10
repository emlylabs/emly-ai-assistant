// Tiny fetch wrapper. Auth is cookie-based — every request sends
// `credentials: "include"` so the httpOnly session cookie set by
// `/api/admin/auth/callback` flows automatically. JS never reads or writes
// the cookie. On 401 we redirect to the OIDC login endpoint; the session
// cookie either lands on return or the IdP routes the user to "Request
// access" / "Email not verified" pages.

export type AdminUser = {
  id: string;
  email: string;
  name: string | null;
  is_active: boolean;
  is_superadmin: boolean;
  issuer: string | null;
  subject: string | null;
  email_verified: boolean;
  last_login_at: string | null;
  memberships: { bot_id: string; role: "owner" | "admin" | "viewer" }[];
};

export type PendingAdmin = {
  id: string;
  email: string;
  invited_by: string | null;
  is_superadmin: boolean;
  bot_assignments: { bot_id: string; role: string }[];
  created_at: string;
  expires_at: string | null;
  consumed_at: string | null;
  consumed_by: string | null;
};

// AdminInvite — legacy invite-token type, no longer issued by the backend.
// Kept as a type alias for any straggler consumer until Phase 9 cleanup.
export type AdminInvite = never;

// Backend now returns bot-scoped fields (Phase 0 multi-bot strip dropped
// bot_type / dataset_id; per-bot dashboard endpoints land later in
// backend Phase 4). Today's `/api/admin/dashboard/stats` is still
// deployment-wide — see the global-data banner in `BotShell`.
export type DashboardStats = {
  bot_id: string;
  admin_count: number;
  end_user_count: number;
  message_count: number;
  last_import: Record<string, unknown> | null;
};

export type Message = {
  id?: number;
  bot_id?: string;
  user_id: string;
  session_id: string;
  message: string;
  role: string;
  created_on: string;
  updated_on: string;
  not_useful: boolean;
  expanded_query: string | null;
  page: string | null;
  topic: string | null;
  // Phase 1–7 backend-backfill telemetry. All nullable: a bot that hasn't
  // opted into deflection / CSAT / async enrichment leaves these `null`,
  // and the UI must render `—` rather than fabricating zeros.
  channel_id?: string | null;
  model_used?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  response_time_ms?: number | null;
  is_deflected?: boolean | null;
  deflection_method?: string | null;
  rating?: number | null;
  rated_at?: string | null;
  citations?: string | null;
};

export type DailySessionBucket = {
  /** ISO `YYYY-MM-DD` key in UTC. */
  day: string;
  started: number;
  resolved: number;
};

export type EmlySession = {
  id: string;
  bot_id: string;
  user_id: string;
  channel_id: string | null;
  started_at: string;
  last_activity_at: string;
  ended_at: string | null;
  turn_count: number;
  is_resolved: boolean | null;
  resolved_at: string | null;
  resolved_by: string | null;
  sentiment_score: number | null;
  sentiment_label: string | null;
  intent: string | null;
  intent_confidence: number | null;
  enrichment_at: string | null;
  taken_over_by: string | null;
  taken_over_at: string | null;
  taken_over_until: string | null;
};

export type SessionRow = {
  session: EmlySession;
  last_message: Message | null;
};

export type SessionListResponse = {
  items: SessionRow[];
  total: number;
  skip: number;
  limit: number;
};

export type SessionDetailResponse = {
  session: EmlySession;
  messages: Message[];
};

export type SessionRatingFilter = "rated" | "unrated" | "positive" | "negative";

export type SessionListParams = {
  skip?: number;
  limit?: number;
  channel_id?: string;
  is_resolved?: boolean;
  session_id?: string;
  user_id?: string;
  /** ISO-8601 datetime. */
  started_after?: string;
  /** ISO-8601 datetime. */
  started_before?: string;
  rating?: SessionRatingFilter;
};

export type DailyMessageBucket = {
  /** ISO `YYYY-MM-DD` key in UTC. */
  day: string;
  count: number;
  user_count?: number;
  assistant_count?: number;
};

export type MessageCountByChannel = {
  channel_id: string | null;
  channel_type: string | null;
  display_name: string | null;
  count: number;
};

export type SentimentBreakdown = {
  positive: number;
  neutral: number;
  negative: number;
  unrated: number;
};

export type IntentCount = {
  intent: string;
  count: number;
};

export type EnrichmentSummary = {
  cohort_size: number;
  enriched_count: number;
  sentiment: SentimentBreakdown;
  intents: IntentCount[];
};

export type TopCitedFile = {
  file_id: string | null;
  filename: string | null;
  citation_count: number;
};

export type CitationStats = {
  assistant_turns: number;
  with_citations: number;
  citation_rate: number | null;
  top_files: TopCitedFile[];
};

export type LatencyQuantiles = {
  count: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
};

export type HeatmapCell = {
  /** Mon=0 .. Sun=6 (UTC). */
  day_of_week: number;
  /** 0..23 UTC. */
  hour: number;
  count: number;
};

export type MessageUsageByModel = {
  /** The exact `model_used` string persisted on `emly_messages`, e.g.
   * `openai/gpt-4o`. The frontend applies a local price table to turn
   * these tallies into a USD figure. */
  model: string;
  turns: number;
  prompt_tokens: number;
  completion_tokens: number;
};

export type MessageCountByTopic = {
  /** Empty string is the "[unclassified]" bucket — null and empty
   * topics are merged server-side. */
  topic: string;
  count: number;
};

export type FunnelResponse = {
  /** Cohort: sessions whose `started_at` falls in the requested window. */
  started: number;
  /** Sessions in the cohort with ≥1 user message that the intent router
   * classified (non-null, non-empty `topic`). */
  understood: number;
  /** Sessions in the cohort whose `is_resolved` flag is true. */
  resolved: number;
  understood_rate: number | null;
  resolved_rate: number | null;
};

export type BotReport = {
  // What `EMLYMessages.get_report` returns. Numeric fields are `null` when
  // there are zero observations in the window — the UI distinguishes that
  // from a real "0%" reading. See `models/emly_messages.py:get_report`.
  actions: number;
  form_submission_timestamps: string[];
  users: number;
  conversations: number;
  engagement: number;
  impressions: number;
  short_impressions: number;
  conversion_rate: number;
  messages: number;
  average_message_per_conversation: number;
  average_conversations_per_user: number;
  deflection_rate: number | null;
  deflection_count: number;
  csat_avg: number | null;
  csat_count: number;
  p95_latency_ms: number | null;
  resolution_rate: number | null;
};

export type EndUser = {
  id: string;
  bot_id?: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone: string | null;
  ip: string;
  browser: string;
  timestamp: number;
  country: string | null;
  city: string | null;
  region: string | null;
  latitude: number | null;
  longitude: number | null;
  created_on: string | null;
  updated_on: string | null;
  meta: Record<string, unknown> | null;
};

export type Paginated<T> = {
  items: T[];
  total: number;
  skip: number;
  limit: number;
};

// ---------------------------------------------------------------------------
// Multi-bot types (Phase 1+ of multi-bot-ui.md)
// ---------------------------------------------------------------------------
export type Bot = {
  id: string;
  slug: string;
  name: string;
  is_active: boolean;
  is_deleted: boolean;
  config_schema_version: number;
  config_version: number;
  created_at: string;
  updated_at: string;
};

export type Role = "owner" | "admin" | "viewer";

export type Membership = {
  admin_id: string;
  bot_id: string;
  role: Role;
  created_at: string;
};

export type ChannelType = "slack" | "telegram" | "whatsapp_cloud" | "google_chat" | "teams" | "web_widget";

export type ChannelTypeInfo = {
  type: ChannelType;
  requires_oauth_callback: boolean;
  allows_direct_static_install: boolean;
  install_addressing: "by_path" | "by_payload";
  default_reply_mode: "sync" | "async";
  supported_reply_modes: ("sync" | "async")[];
  secrets_schema: Record<string, unknown>;
  install_url_template: string;
};

export type ChannelInstall = {
  id: string;
  bot_id: string;
  type: ChannelType;
  external_id: string | null;
  display_name: string | null;
  is_active: boolean;
  created_at: string;
  secrets_rotated_at: string | null;
  config: Record<string, unknown> | null;
  secrets_redacted: Record<string, unknown>;
  webhook_url: string;
};

export type ChannelHealth = {
  ok: boolean;
  info: Record<string, unknown>;
};

export type DocumentType =
  | "web_page"
  | "document"
  | "product"
  | "support_article"
  | "faq"
  | "other";

export const DOCUMENT_TYPES: DocumentType[] = [
  "web_page",
  "document",
  "product",
  "support_article",
  "faq",
  "other",
];

export type BotFile = {
  id: string;
  bot_id: string;
  file_name: string;
  mime_type: string | null;
  size_bytes: number | null;
  sha256: string | null;
  embedding_status: "pending" | "embedding" | "embedded" | "failed";
  error_message: string | null;
  document_type: DocumentType;
  created_on: string | null;
  updated_on: string | null;
};

export type CrawlJobOptions = {
  sameHostOnly: boolean;
  pathPrefix: string;
  includeRegex: string;
  excludeRegex: string;
  maxDepth: number;
  maxPages: number;
  respectRobots: boolean;
  skipThinPages: boolean;
  thinThreshold: number;
  skipAlreadyImported: boolean;
  politeDelayMs: number;
  concurrency: number;
};

export type CrawlJobStatus = "running" | "paused" | "completed" | "cancelled" | "failed";

export type CrawlJob = {
  id: string;
  bot_id: string;
  seed_url: string;
  options: CrawlJobOptions;
  status: CrawlJobStatus;
  pages_total: number;
  pages_done: number;
  pages_skipped: number;
  pages_failed: number;
  document_type: DocumentType;
  created_by_admin_id: string | null;
  created_on: string | null;
  updated_on: string | null;
  completed_on: string | null;
  error_message: string | null;
};

export type CrawlJobPage = {
  id: number;
  job_id: string;
  url: string;
  depth: number;
  state:
    | "queued"
    | "fetching"
    | "uploading"
    | "embedding"
    | "done"
    | "skipped"
    | "failed";
  reason: string | null;
  file_id: string | null;
  created_on: string | null;
  updated_on: string | null;
};

// Auth is cookie-based; these helpers exist for backwards-compatibility
// with call sites that still expect a getToken/setToken interface. They're
// no-ops now — the browser sends the httpOnly cookie automatically.
export function getToken(): string | null {
  return null;
}

export function setToken(_token: string | null): void {
  /* no-op — cookies are httpOnly */
}

/** Build the URL the UI should redirect to in order to start the OIDC login flow. */
export function loginUrl(returnTo?: string): string {
  const target = returnTo ?? (typeof window !== "undefined" ? window.location.pathname + window.location.search : "/");
  const safe = target.startsWith("/") && !target.startsWith("//") ? target : "/";
  return `/api/admin/auth/login?return_to=${encodeURIComponent(safe)}`;
}

function redirectToLogin() {
  if (typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login") || window.location.pathname.startsWith("/auth/")) return;
  window.location.href = loginUrl();
}

class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts?: { auth?: boolean }
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const auth = opts?.auth ?? true;

  const res = await fetch(`/api/admin${path}`, {
    method,
    headers,
    credentials: "include",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401 && auth) {
    redirectToLogin();
  }

  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    let detail: unknown = undefined;
    try {
      const data = await res.json();
      detail = data?.detail;
      if (detail) message = typeof detail === "string" ? detail : JSON.stringify(detail);
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, message, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (entries.length === 0) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of entries) sp.append(k, String(v));
  return `?${sp.toString()}`;
}

// XHR-based upload so we can report progress (`fetch` can't).
export type UploadHandle = {
  promise: Promise<BotFile>;
  abort: () => void;
};

function uploadMultipart(
  path: string,
  file: File,
  onProgress?: (loaded: number, total: number) => void,
  extraFields?: Record<string, string>
): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<BotFile>((resolve, reject) => {
    xhr.open("POST", `/api/admin${path}`);
    xhr.withCredentials = true;
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded, e.total);
      };
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch (e) {
          reject(new ApiError(xhr.status, "Failed to parse upload response"));
        }
      } else {
        let message = `${xhr.status} ${xhr.statusText}`;
        let detail: unknown = undefined;
        try {
          const data = JSON.parse(xhr.responseText);
          detail = data?.detail;
          if (detail) message = typeof detail === "string" ? detail : JSON.stringify(detail);
        } catch {
          // ignore
        }
        reject(new ApiError(xhr.status, message, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error during upload"));
    xhr.onabort = () => reject(new ApiError(0, "Upload aborted"));
    const fd = new FormData();
    fd.append("file", file);
    if (extraFields) {
      for (const [k, v] of Object.entries(extraFields)) fd.append(k, v);
    }
    xhr.send(fd);
  });
  return { promise, abort: () => xhr.abort() };
}

export type SystemWarning = { code: string; message: string };
export type ReadyzResponse = {
  status: "ready" | "not_ready";
  warnings?: SystemWarning[];
  issues?: string[];
};

// readyz lives at /api/readyz (no /admin prefix); separate fetcher.
export async function fetchReadyz(): Promise<ReadyzResponse | null> {
  try {
    const res = await fetch("/api/readyz");
    if (!res.ok && res.status !== 503) return null;
    return (await res.json()) as ReadyzResponse;
  } catch {
    return null;
  }
}

// ----- Phase 11 backend-backfill UI types -----
export type AuditLog = {
  id: string;
  admin_id: string | null;
  bot_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  payload: Record<string, unknown> | null;
  ip: string | null;
  ua: string | null;
  success: boolean;
  created_at: string;
};

export type AuditListResponse = {
  items: AuditLog[];
  skip: number;
  limit: number;
};

export const api = {
  // Auth — login is a redirect (loginUrl()), not an API call. /me confirms
  // the session cookie is valid.
  me: () => request<AdminUser>("GET", "/auth/me"),
  logout: () =>
    request<{ provider_logout_url: string | null }>("POST", "/auth/logout", undefined, { auth: false }),

  // Phase 11 backend-backfill: audit log readers.
  listAuditLogs: (opts?: {
    admin_id?: string;
    bot_id?: string;
    action?: string;
    success?: boolean;
    skip?: number;
    limit?: number;
  }) =>
    request<AuditListResponse>("GET", `/audit-logs${qs({ ...opts })}`),
  listBotAuditLogs: (
    slug: string,
    opts?: { action?: string; success?: boolean; skip?: number; limit?: number },
  ) =>
    request<AuditListResponse>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/audit-logs${qs({ ...opts })}`,
    ),

  // Admin user management (superadmin)
  listAdmins: () =>
    request<{ admins: AdminUser[]; pending: PendingAdmin[] }>("GET", "/admins"),
  updateAdmin: (id: string, payload: { is_active?: boolean; is_superadmin?: boolean }) =>
    request<AdminUser>("PATCH", `/admins/${encodeURIComponent(id)}`, payload),
  deleteAdmin: (id: string) =>
    request<void>("DELETE", `/admins/${encodeURIComponent(id)}`),
  listPendingAdmins: () => request<PendingAdmin[]>("GET", "/admins/pending"),
  createPendingAdmin: (payload: {
    email: string;
    is_superadmin?: boolean;
    bot_assignments?: { bot_id: string; role: string }[];
  }) => request<PendingAdmin>("POST", "/admins/pending", payload),
  revokePendingAdmin: (email: string) =>
    request<void>("DELETE", `/admins/pending/${encodeURIComponent(email)}`),

  // Bot CRUD
  listBots: () => request<Bot[]>("GET", "/bots"),
  getBot: (slug: string) => request<Bot>("GET", `/bots/${encodeURIComponent(slug)}`),
  listBotTemplates: () =>
    request<{ id: string; name: string; description: string; preview_topics: string[] }[]>(
      "GET",
      "/bot-templates"
    ),
  createBot: (payload: { slug: string; name: string; template_id?: string }) =>
    request<Bot>("POST", "/bots", payload),
  patchBot: (slug: string, payload: { name?: string }) =>
    request<Bot>("PATCH", `/bots/${encodeURIComponent(slug)}`, payload),
  deleteBot: (slug: string) =>
    request<{ status: string; bot_id: string }>("DELETE", `/bots/${encodeURIComponent(slug)}`),

  // Membership
  listBotAdmins: (slug: string) =>
    request<Membership[]>("GET", `/bots/${encodeURIComponent(slug)}/admins`),
  grantMembership: (slug: string, payload: { admin_id: string; role: Role }) =>
    request<Membership>("POST", `/bots/${encodeURIComponent(slug)}/admins`, payload),
  updateMembershipRole: (slug: string, adminId: string, payload: { role: Role }) =>
    request<Membership>(
      "PATCH",
      `/bots/${encodeURIComponent(slug)}/admins/${encodeURIComponent(adminId)}`,
      payload
    ),
  revokeMembership: (slug: string, adminId: string) =>
    request<void>(
      "DELETE",
      `/bots/${encodeURIComponent(slug)}/admins/${encodeURIComponent(adminId)}`
    ),

  // Slug-scoped admin views
  botDashboardStats: (slug: string) =>
    request<{
      bot_id: string;
      slug: string;
      name: string;
      end_user_count: number;
      message_count: number;
      file_count: number;
      member_count: number;
    }>("GET", `/bots/${encodeURIComponent(slug)}/dashboard/stats`),
  listBotMessages: (
    slug: string,
    opts?: { skip?: number; limit?: number; user_id?: string; session_id?: string }
  ) =>
    request<Paginated<Message>>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages${qs({ ...opts })}`
    ),
  /** Shared shape for analytics queries: window + optional channel
   * scope. ``channel_id`` matches the column on `bot_channel.id`. */
  // (Type aliasing inline since the api object is a const literal.)
  getBotReport: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<BotReport>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/report${qs({ ...opts })}`,
    ),
  getBotSessionsDaily: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<DailySessionBucket[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/sessions/daily${qs({ ...opts })}`,
    ),
  listBotSessions: (slug: string, opts?: SessionListParams) =>
    request<SessionListResponse>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/sessions${qs({ ...opts })}`,
    ),
  getBotSession: (slug: string, sessionId: string) =>
    request<SessionDetailResponse>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/sessions/${encodeURIComponent(sessionId)}`,
    ),
  resolveBotSession: (
    slug: string,
    sessionId: string,
    payload: { is_resolved: boolean; reason?: string },
  ) =>
    request<EmlySession>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/sessions/${encodeURIComponent(sessionId)}/resolve`,
      payload,
    ),
  getBotMessagesDaily: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<DailyMessageBucket[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages/daily${qs({ ...opts })}`,
    ),
  getBotMessageUsageByModel: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<MessageUsageByModel[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages/by-model${qs({ ...opts })}`,
    ),
  getBotMessageCountsByTopic: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<MessageCountByTopic[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages/by-topic${qs({ ...opts })}`,
    ),
  getBotFunnel: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<FunnelResponse>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/funnel${qs({ ...opts })}`,
    ),
  getBotMessageCountsByChannel: (
    slug: string,
    /** Channel mix is bot-wide by definition; no `channel_id` filter. */
    opts?: { from?: number; to?: number },
  ) =>
    request<MessageCountByChannel[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages/by-channel${qs({ ...opts })}`,
    ),
  getBotEnrichmentSummary: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<EnrichmentSummary>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/sessions/enrichment-summary${qs({ ...opts })}`,
    ),
  getBotCitationStats: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<CitationStats>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages/citation-stats${qs({ ...opts })}`,
    ),
  getBotLatencyQuantiles: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<LatencyQuantiles>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages/latency-quantiles${qs({ ...opts })}`,
    ),
  getBotMessagesHeatmap: (
    slug: string,
    opts?: { from?: number; to?: number; channel_id?: string },
  ) =>
    request<HeatmapCell[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/messages/heatmap${qs({ ...opts })}`,
    ),
  updateBotMessage: (slug: string, id: number, payload: { not_useful: boolean }) =>
    request<Message>(
      "PATCH",
      `/bots/${encodeURIComponent(slug)}/messages/${id}`,
      payload
    ),
  deleteBotMessage: (slug: string, id: number) =>
    request<void>("DELETE", `/bots/${encodeURIComponent(slug)}/messages/${id}`),
  listBotEndUsers: (slug: string, opts?: { skip?: number; limit?: number }) =>
    request<Paginated<EndUser>>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/end-users${qs({ ...opts })}`
    ),
  getBotConfig: (slug: string) =>
    request<{ config: Record<string, unknown>; config_version: number }>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/config`
    ),
  putBotConfig: (
    slug: string,
    config: Record<string, unknown>,
    expected_version?: number
  ) =>
    request<{ config: Record<string, unknown>; config_version: number }>(
      "PUT",
      `/bots/${encodeURIComponent(slug)}/config`,
      { config, expected_version }
    ),
  getBotApiKeyStatus: (slug: string) =>
    request<{ has_key: boolean }>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/api-key/status`
    ),
  putBotApiKey: (slug: string, api_key: string | null) =>
    request<void>(
      "PUT",
      `/bots/${encodeURIComponent(slug)}/api-key`,
      { api_key }
    ),
  ragSearch: (
    slug: string,
    payload: { query: string; top_k?: number; threshold?: number }
  ) =>
    request<{
      query: string;
      top_k: number;
      threshold: number;
      hits: { score: number | null; chunk: string; metadata: Record<string, unknown> }[];
    }>("POST", `/bots/${encodeURIComponent(slug)}/rag/search`, payload),

  // Files
  listBotFiles: (slug: string) =>
    request<BotFile[]>("GET", `/bots/${encodeURIComponent(slug)}/files`),
  uploadBotFile: (
    slug: string,
    file: File,
    onProgress?: (loaded: number, total: number) => void,
    documentType: DocumentType = "document"
  ) =>
    uploadMultipart(
      `/bots/${encodeURIComponent(slug)}/files`,
      file,
      onProgress,
      { document_type: documentType }
    ),
  patchBotFile: (slug: string, fileId: string, payload: { document_type?: DocumentType }) =>
    request<BotFile>(
      "PATCH",
      `/bots/${encodeURIComponent(slug)}/files/${encodeURIComponent(fileId)}`,
      payload
    ),
  deleteBotFile: (slug: string, fileId: string) =>
    request<void>(
      "DELETE",
      `/bots/${encodeURIComponent(slug)}/files/${encodeURIComponent(fileId)}`
    ),
  reindexBotFile: (slug: string, fileId: string) =>
    request<BotFile>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/files/${encodeURIComponent(fileId)}/reindex`
    ),
  reindexBot: (slug: string) =>
    request<{ status: string; files_queued: number }>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/reindex`
    ),

  // Crawl jobs (backend-resident, recoverable)
  createCrawlJob: (
    slug: string,
    payload: { seed_url: string; document_type: DocumentType; options: CrawlJobOptions }
  ) =>
    request<CrawlJob>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs`,
      payload
    ),
  listCrawlJobs: (slug: string) =>
    request<CrawlJob[]>("GET", `/bots/${encodeURIComponent(slug)}/crawl/jobs`),
  getCrawlJob: (slug: string, jobId: string) =>
    request<CrawlJob>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs/${encodeURIComponent(jobId)}`
    ),
  listCrawlJobPages: (slug: string, jobId: string, opts?: { limit?: number; offset?: number }) =>
    request<CrawlJobPage[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs/${encodeURIComponent(jobId)}/pages${qs({ ...opts })}`
    ),
  pauseCrawlJob: (slug: string, jobId: string) =>
    request<CrawlJob>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs/${encodeURIComponent(jobId)}/pause`
    ),
  resumeCrawlJob: (slug: string, jobId: string) =>
    request<CrawlJob>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs/${encodeURIComponent(jobId)}/resume`
    ),
  cancelCrawlJob: (slug: string, jobId: string) =>
    request<CrawlJob>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs/${encodeURIComponent(jobId)}/cancel`
    ),

  // -------------------------------------------------------------------------
  // Channels — Slack/Telegram/WhatsApp/Teams/GoogleChat install management.
  // -------------------------------------------------------------------------
  listChannelTypes: () => request<ChannelTypeInfo[]>("GET", "/channels/types"),
  listChannels: (slug: string) =>
    request<ChannelInstall[]>("GET", `/bots/${encodeURIComponent(slug)}/channels`),
  createChannel: (
    slug: string,
    payload: { type: ChannelType; secrets: Record<string, unknown>; config?: Record<string, unknown> }
  ) =>
    request<ChannelInstall>("POST", `/bots/${encodeURIComponent(slug)}/channels`, payload),
  updateChannelSecrets: (slug: string, channelId: string, secrets: Record<string, unknown>) =>
    request<ChannelInstall>(
      "PUT",
      `/bots/${encodeURIComponent(slug)}/channels/${encodeURIComponent(channelId)}/secrets`,
      { secrets }
    ),
  updateChannelConfig: (slug: string, channelId: string, config: Record<string, unknown>) =>
    request<ChannelInstall>(
      "PUT",
      `/bots/${encodeURIComponent(slug)}/channels/${encodeURIComponent(channelId)}/config`,
      { config }
    ),
  setChannelActive: (slug: string, channelId: string, is_active: boolean) =>
    request<ChannelInstall>(
      "PUT",
      `/bots/${encodeURIComponent(slug)}/channels/${encodeURIComponent(channelId)}/active`,
      { is_active }
    ),
  deleteChannel: (slug: string, channelId: string, hard = false) =>
    request<{ status: string }>(
      "DELETE",
      `/bots/${encodeURIComponent(slug)}/channels/${encodeURIComponent(channelId)}${hard ? "?hard=true" : ""}`
    ),
  channelHealth: (slug: string, channelId: string) =>
    request<ChannelHealth>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/channels/${encodeURIComponent(channelId)}/health`
    ),
  channelOAuthStart: (slug: string, payload: { type: ChannelType; redirect_to?: string }) =>
    request<{ authorize_url: string }>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/channels/oauth-start`,
      payload
    ),

  // ---------------------------------------------------------------------------
  // Legacy global routes — used by Phase 2 pages until backend slug-scoped
  // routes ship. Each consumer surfaces the "deployment-wide data" banner.
  // ---------------------------------------------------------------------------
  /** @deprecated — backend route is global; awaits per-bot variant. */
  dashboardStats: () => request<DashboardStats>("GET", "/dashboard/stats"),
  /** @deprecated — backend route is global. */
  listMessages: (opts?: { skip?: number; limit?: number; user_id?: string; session_id?: string }) =>
    request<Paginated<Message>>("GET", `/messages${qs({ ...opts })}`),
  /** @deprecated — backend route is global. */
  updateMessage: (id: string | number, payload: { not_useful: boolean }) =>
    request<Message>("PATCH", `/messages/${encodeURIComponent(String(id))}`, payload),
  /** @deprecated — backend route is global. */
  deleteMessage: (id: string | number) =>
    request<void>("DELETE", `/messages/${encodeURIComponent(String(id))}`),
  /** @deprecated — backend route is global. */
  listEndUsers: (opts?: { skip?: number; limit?: number }) =>
    request<Paginated<EndUser>>("GET", `/end-users${qs({ ...opts })}`),
  /** @deprecated — backend route is global. */
  getEndUser: (id: string) => request<EndUser>("GET", `/end-users/${encodeURIComponent(id)}`),
  /** @deprecated — backend route is global; writes to JOB_ID's bot. */
  getConfig: () => request<Record<string, unknown>>("GET", "/config"),
  /** @deprecated — backend route is global; writes to JOB_ID's bot. */
  putConfig: (config: Record<string, unknown>) =>
    request<Record<string, unknown>>("PUT", "/config", { config }),
};

export { ApiError };
