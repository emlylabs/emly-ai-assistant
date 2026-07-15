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
  // 008_space_invites_and_default_space — pending rows now carry an
  // explicit lifecycle and optional URL token. Legacy rows show
  // ``kind: 'superadmin_legacy'`` and ``token: null``.
  token?: string | null;
  status?: "pending" | "accepted" | "rejected" | "expired" | "revoked";
  kind?: "space_invite" | "superadmin_legacy";
  target_bot_id?: string | null;
  responded_at?: string | null;
  responded_by?: string | null;
};

export type MyInvite = {
  token: string;
  status: "pending" | "accepted" | "rejected" | "expired" | "revoked";
  email: string;
  role: string;
  bot_slug: string | null;
  bot_name: string | null;
  is_superadmin: boolean;
  inviter_email: string | null;
  inviter_name: string | null;
  created_at: string;
  expires_at: string | null;
};

export type InviteByToken = {
  token: string;
  status: "pending" | "accepted" | "rejected" | "expired" | "revoked";
  kind: "space_invite" | "superadmin_legacy";
  email: string;
  role: string;
  bot_slug: string | null;
  bot_name: string | null;
  is_superadmin: boolean;
  inviter_name: string | null;
  inviter_email: string | null;
  created_at: string;
  expires_at: string | null;
};

export type SpaceInviteCreated = PendingAdmin & {
  invite_url: string | null;
  bot_slug: string | null;
  bot_name: string | null;
};

export type Message = {
  id?: number;
  bot_id?: string;
  user_id: string;
  session_id: string;
  message: string;
  role: string;
  created_at: string;
  updated_at: string;
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
  created_at: string | null;
  updated_at: string | null;
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
  tags: string[];
  /** Auto-created default space — gets a one-shot rename. */
  is_default?: boolean;
  /** ``true`` once the default space has been renamed; further renames are
   * locked server-side. */
  default_renamed?: boolean;
  created_at: string;
  updated_at: string;
  /** Set when the bot is in the soft-delete grace window; null otherwise. */
  deleted_at?: string | null;
};

/** One row of `GET /api/admin/bots-deleted` — soft-deleted bots within the
 * 7-day restore grace window. */
export type DeletedBot = {
  id: string;
  slug: string;
  name: string;
  deleted_at: string;
  hard_purge_at: string;
};

export type ConfigHistoryEntry = {
  id: string;
  config_version: number;
  admin_id: string | null;
  admin_email: string | null;
  created_at: string;
};

export type ConfigHistoryDetail = ConfigHistoryEntry & {
  config: Record<string, unknown>;
};

/** One row of the per-bot draft slot — populated only while an admin has
 * unpublished edits pending. */
export type ConfigDraft = {
  config: Record<string, unknown>;
  updated_at: string;
  updated_by_admin_id: string | null;
  updated_by_admin_email: string | null;
};

/** Response shape for `GET/PUT /api/admin/bots/{slug}/config`. */
export type BotConfigResponse = {
  config: Record<string, unknown>;
  config_version: number;
  last_updated_by_admin_id: string | null;
  last_updated_by_admin_email: string | null;
  last_updated_at: string | null;
  draft: ConfigDraft | null;
};

/** One row of `GET /api/admin/bots/summary` — Phase 5 workspace overview. */
export type BotSummary = {
  slug: string;
  name: string;
  is_active: boolean;
  msgs_24h: number;
  sessions_24h: number | null;
  csat_avg: number | null;
  csat_count: number | null;
  p95_latency_ms: number | null;
  deflection_rate: number | null;
  active_channels: ChannelType[];
  owner_email: string | null;
  tags: string[];
  /** File count for this bot's knowledge base. Backfilled by §11 T10. */
  file_count: number;
  /** Total bytes across the bot's uploaded files. Backfilled by §11 T10. */
  storage_bytes: number;
  updated_at: string;
};

/** One row of `GET /api/admin/bots/validation-summary`. */
export type BotValidationCounts = {
  slug: string;
  errors: number;
  warnings: number;
  info: number;
};

/** Mirror of `services.config_validation.Issue`. The TS validator
 * (`ui/lib/config-validation.ts`) emits the same shape for client-side
 * preview, but the keys here come from the server. */
export type ConfigValidationIssue = {
  severity: "error" | "warning" | "info";
  key: string;
  section:
    | "identity"
    | "widget"
    | "topics"
    | "forms"
    | "llm"
    | "rag"
    | "limits"
    | "raw"
    | "knowledge";
  message: string;
  cta?: { label: string; href?: string; tab?: string };
};

export type ConfigValidationResponse = {
  issues: ConfigValidationIssue[];
  counts: { error: number; warning: number; info: number };
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
  created_at: string | null;
  updated_at: string | null;
};

export type BotFileStatusBucket = "indexed" | "in_progress" | "errors";

export type BotFileListResponse = {
  items: BotFile[];
  total: number;
  page: number;
  page_size: number;
  grand_total: number;
  total_bytes: number;
  last_updated_at: string | null;
  status_counts: Record<BotFileStatusBucket, number>;
  document_type_counts: Partial<Record<DocumentType, number>>;
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
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
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
  created_at: string | null;
  updated_at: string | null;
};

export type CrawlSchedule = {
  id: string;
  bot_id: string;
  seed_url: string;
  options: Record<string, unknown>;
  document_type: string;
  cadence: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_job_id: string | null;
  created_by_admin_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type CrawlPageState =
  | "queued"
  | "fetching"
  | "uploading"
  | "embedding"
  | "done"
  | "skipped"
  | "failed";

export type CrawlJobPagesResponse = {
  items: CrawlJobPage[];
  total: number;
  counts: Record<CrawlPageState, number>;
  limit: number;
  offset: number;
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

let _redirecting = false;
function redirectToLogin() {
  if (typeof window === "undefined") return;
  if (_redirecting) return;
  if (window.location.pathname.startsWith("/login") || window.location.pathname.startsWith("/auth/")) return;
  _redirecting = true;
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
    let message = `${res.status} ${res.statusText}`.trim();
    let detail: unknown = undefined;
    try {
      const data = await res.json();
      detail = data?.detail;
      // Backend's generic exception handler in main.py returns
      // `{"message": "Internal server error"}` for 500s — fall through to
      // it so the UI shows something more readable than a bare "500".
      const payloadMessage = typeof data?.message === "string" ? data.message : null;
      if (detail) {
        message = typeof detail === "string" ? detail : JSON.stringify(detail);
      } else if (payloadMessage) {
        message = payloadMessage;
      }
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
// ----- Workspace (§6) -----

export type WorkspaceSettingsResponse = {
  settings: Record<string, unknown>;
  updated_at: string;
  updated_by_admin_id: string | null;
};

export type WorkspaceTemplate = {
  id: string;
  name: string;
  description: string | null;
  created_by_admin_id: string | null;
  created_at: string;
  updated_at: string;
};

export type WorkspaceTemplateDetail = WorkspaceTemplate & {
  config_json: Record<string, unknown>;
};

export type BulkBotActionResult = {
  slug: string;
  status: string;
  detail: string | null;
};

/** One row of the workspace-wide crawl monitor (`GET /api/admin/crawl-jobs`).
 * Differs from `CrawlJob` only by carrying the bot's slug + name for
 * cross-bot rendering. §11 T7. */
export type WorkspaceCrawlJobRow = {
  id: string;
  bot_id: string;
  bot_slug: string;
  bot_name: string;
  seed_url: string;
  status: CrawlJobStatus;
  pages_total: number;
  pages_done: number;
  pages_skipped: number;
  pages_failed: number;
  document_type: string;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
};

export type WorkspaceCrawlJobsResponse = {
  items: WorkspaceCrawlJobRow[];
  total: number;
  limit: number;
  offset: number;
};

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

  // Space invites (new in 008). Owner-scoped invite creation; tokenised
  // accept/reject UX; per-space pending-invite list.
  createSpaceInvite: (payload: { email: string; bot_slug: string; role: string }) =>
    request<SpaceInviteCreated>("POST", "/admins/invites", payload),
  listMyInvites: () => request<MyInvite[]>("GET", "/admins/invites/mine"),
  getInviteByToken: (token: string) =>
    request<InviteByToken>(
      "GET",
      `/admins/invites/by-token/${encodeURIComponent(token)}`,
      undefined,
      { auth: false },
    ),
  acceptInvite: (token: string) =>
    request<{ token: string; status: string; bot_slug: string | null }>(
      "POST",
      `/admins/invites/${encodeURIComponent(token)}/accept`,
    ),
  rejectInvite: (token: string) =>
    request<void>(
      "POST",
      `/admins/invites/${encodeURIComponent(token)}/reject`,
    ),
  revokeInvite: (token: string) =>
    request<void>(
      "DELETE",
      `/admins/invites/${encodeURIComponent(token)}`,
    ),
  listInvitesForBot: (slug: string) =>
    request<MyInvite[]>(
      "GET",
      `/admins/invites/for-bot/${encodeURIComponent(slug)}`,
    ),

  // Cross-bot membership matrix (superadmin).
  getAdminMemberships: (adminId: string) =>
    request<{
      admin: AdminUser;
      rows: { bot_id: string; bot_slug: string; bot_name: string; role: Role | null }[];
    }>("GET", `/admins/${encodeURIComponent(adminId)}/memberships`),
  bulkUpdateAdminMemberships: (
    adminId: string,
    assignments: { bot_slug: string; role: Role | null }[],
  ) =>
    request<{
      results: { bot_slug: string; status: string; detail: string | null }[];
    }>(
      "POST",
      `/admins/${encodeURIComponent(adminId)}/memberships`,
      { assignments },
    ),

  // Atomic owner transfer.
  transferBotOwnership: (
    slug: string,
    payload: {
      bot_slug: string;
      new_owner_admin_id: string;
      leaving_owner_new_role?: "admin" | "viewer" | null;
    },
  ) =>
    request<{
      bot_slug: string;
      new_owner_admin_id: string;
      previous_owner_admin_id: string | null;
      previous_owner_new_role: string | null;
    }>("POST", `/bots/${encodeURIComponent(slug)}/transfer-ownership`, payload),

  // Workspace defaults + templates + bulk actions (§6).
  getWorkspaceSettings: () =>
    request<WorkspaceSettingsResponse>("GET", "/workspace/settings"),
  putWorkspaceSettings: (settings: Record<string, unknown>) =>
    request<WorkspaceSettingsResponse>("PUT", "/workspace/settings", { settings }),
  listWorkspaceTemplates: () =>
    request<WorkspaceTemplate[]>("GET", "/workspace/templates"),
  getWorkspaceTemplate: (id: string) =>
    request<WorkspaceTemplateDetail>(
      "GET",
      `/workspace/templates/${encodeURIComponent(id)}`,
    ),
  createWorkspaceTemplate: (payload: {
    name: string;
    description?: string;
    source_bot_slug?: string;
    config_json?: Record<string, unknown>;
  }) =>
    request<WorkspaceTemplateDetail>(
      "POST",
      "/workspace/templates",
      payload,
    ),
  deleteWorkspaceTemplate: (id: string) =>
    request<void>(
      "DELETE",
      `/workspace/templates/${encodeURIComponent(id)}`,
    ),
  listWorkspaceCrawlJobs: (opts?: { limit?: number; offset?: number }) =>
    request<WorkspaceCrawlJobsResponse>(
      "GET",
      `/crawl-jobs${qs({ limit: opts?.limit, offset: opts?.offset })}`,
    ),
  bulkBotAction: (payload: {
    action: "pause" | "unpause" | "delete" | "tag-add" | "tag-remove";
    slugs: string[];
    tag?: string;
  }) =>
    request<{ results: BulkBotActionResult[] }>(
      "POST",
      "/bots/bulk-action",
      payload,
    ),

  // Bot CRUD
  listBots: () => request<Bot[]>("GET", "/bots"),
  /**
   * Aggregate-per-bot row used by the workspace overview table. Replaces
   * the prior N+1 over `/dashboard/stats`. Backend caches for 60s per admin.
   */
  listBotsSummary: () => request<BotSummary[]>("GET", "/bots/summary"),
  /**
   * Per-bot validation counts for the workspace overview health icon.
   * Replaces the N+1 client-side `getBotConfig` + `validateConfig` loop
   * the page used to do for every row. Cached server-side by
   * (bot_id, config_version) — implicitly invalidated on save.
   */
  listBotsValidationSummary: () =>
    request<BotValidationCounts[]>("GET", "/bots/validation-summary"),
  /**
   * Validate an in-progress config blob without saving. Used by the
   * editor's preview path so the issue list matches what publish / save
   * gates will enforce server-side.
   */
  validateBotConfig: (
    slug: string,
    config: Record<string, unknown>,
  ) =>
    request<ConfigValidationResponse>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/config/validate`,
      { config },
    ),
  getBot: (slug: string) => request<Bot>("GET", `/bots/${encodeURIComponent(slug)}`),
  listBotTemplates: () =>
    request<{ id: string; name: string; description: string; preview_topics: string[] }[]>(
      "GET",
      "/bot-templates"
    ),
  createBot: (payload: {
    slug: string;
    name: string;
    template_id?: string;
    source_bot_id?: string;
  }) => request<Bot>("POST", "/bots", payload),
  checkBotSlugAvailable: (slug: string) =>
    request<{ slug: string; available: boolean; reason?: "invalid" | "taken" }>(
      "GET",
      `/bots/slug-available${qs({ slug })}`,
    ),
  patchBot: (
    slug: string,
    payload: { name?: string; tags?: string[]; is_active?: boolean; slug?: string },
  ) => request<Bot>("PATCH", `/bots/${encodeURIComponent(slug)}`, payload),
  deleteBot: (slug: string) =>
    request<{ status: string; bot_id: string }>("DELETE", `/bots/${encodeURIComponent(slug)}`),
  listDeletedBots: () => request<DeletedBot[]>("GET", "/bots-deleted"),
  restoreBot: (slug: string) =>
    request<Bot>("POST", `/bots/${encodeURIComponent(slug)}/restore`),
  listConfigHistory: (slug: string) =>
    request<ConfigHistoryEntry[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/config/history`,
    ),
  getConfigHistoryEntry: (slug: string, entryId: string) =>
    request<ConfigHistoryDetail>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/config/history/${encodeURIComponent(entryId)}`,
    ),
  restoreConfigHistory: (slug: string, entryId: string) =>
    request<BotConfigResponse>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/config/history/${encodeURIComponent(entryId)}/restore`,
    ),

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
    request<BotConfigResponse>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/config`
    ),
  putBotConfig: (
    slug: string,
    config: Record<string, unknown>,
    expected_version?: number,
    options?: { force?: boolean },
  ) =>
    request<BotConfigResponse>(
      "PUT",
      `/bots/${encodeURIComponent(slug)}/config${options?.force ? "?force=1" : ""}`,
      { config, expected_version }
    ),
  putBotConfigDraft: (
    slug: string,
    config: Record<string, unknown>,
  ) =>
    request<BotConfigResponse>(
      "PUT",
      `/bots/${encodeURIComponent(slug)}/config/draft`,
      { config }
    ),
  discardBotConfigDraft: (slug: string) =>
    request<BotConfigResponse>(
      "DELETE",
      `/bots/${encodeURIComponent(slug)}/config/draft`
    ),
  publishBotConfigDraft: (slug: string) =>
    request<BotConfigResponse>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/config/draft/publish`
    ),
  getBotApiKeyStatus: (slug: string) =>
    request<{ has_key: boolean; has_deployment_fallback: boolean }>(
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
  listBotFiles: (
    slug: string,
    opts?: {
      page?: number;
      pageSize?: number;
      documentTypes?: DocumentType[];
      status?: BotFileStatusBucket;
      q?: string;
    },
  ) =>
    request<BotFileListResponse>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/files${qs({
        page: opts?.page,
        page_size: opts?.pageSize,
        document_types:
          opts?.documentTypes && opts.documentTypes.length > 0
            ? opts.documentTypes.join(",")
            : undefined,
        status: opts?.status,
        q: opts?.q && opts.q.length > 0 ? opts.q : undefined,
      })}`,
    ),
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
  /** Cross-bot single-file copy. ``slug`` is the *target*; the source is
   * in the body. The backend clones the file row, the file on disk, and
   * the Qdrant points (no re-embed). §11 T11. */
  copyFileToBot: (targetSlug: string, payload: { source_slug: string; file_id: string }) =>
    request<BotFile>(
      "POST",
      `/bots/${encodeURIComponent(targetSlug)}/files/copy-from`,
      payload,
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
  listCrawlJobPages: (
    slug: string,
    jobId: string,
    opts?: { limit?: number; offset?: number; states?: string[] }
  ) =>
    request<CrawlJobPagesResponse>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs/${encodeURIComponent(jobId)}/pages${qs({
        limit: opts?.limit,
        offset: opts?.offset,
        states: opts?.states && opts.states.length > 0 ? opts.states.join(",") : undefined,
      })}`
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
  // Recurring crawl schedules (§11 T5). One-shot crawls still flow
  // through the createCrawlJob path; this surface is for "run daily".
  listCrawlSchedules: (slug: string) =>
    request<CrawlSchedule[]>(
      "GET",
      `/bots/${encodeURIComponent(slug)}/crawl/schedules`,
    ),
  createCrawlSchedule: (
    slug: string,
    payload: {
      seed_url: string;
      document_type: string;
      options: CrawlJobOptions;
      cadence: string;
      run_immediately?: boolean;
    },
  ) =>
    request<CrawlSchedule>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/crawl/schedules`,
      payload,
    ),
  patchCrawlSchedule: (slug: string, scheduleId: string, enabled: boolean) =>
    request<CrawlSchedule>(
      "PATCH",
      `/bots/${encodeURIComponent(slug)}/crawl/schedules/${encodeURIComponent(scheduleId)}`,
      { enabled },
    ),
  deleteCrawlSchedule: (slug: string, scheduleId: string) =>
    request<void>(
      "DELETE",
      `/bots/${encodeURIComponent(slug)}/crawl/schedules/${encodeURIComponent(scheduleId)}`,
    ),
  cancelCrawlJob: (slug: string, jobId: string) =>
    request<CrawlJob>(
      "POST",
      `/bots/${encodeURIComponent(slug)}/crawl/jobs/${encodeURIComponent(jobId)}/cancel`
    ),

  // -------------------------------------------------------------------------
  // Widget loader metadata (Channels tab — version pinning + preview QR).
  // -------------------------------------------------------------------------
  widgetInfo: () =>
    request<{ version: string | null; built_at: string | null }>(
      "GET",
      "/widget/info"
    ),
  /** Browser-facing URL for the per-bot preview QR (used as `<img src>`). */
  botPreviewQrUrl: (slug: string) =>
    `/api/admin/bots/${encodeURIComponent(slug)}/preview-qr.svg`,

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
};

export { ApiError };
