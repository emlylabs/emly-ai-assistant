import logging
import math
import os
import time
import urllib.parse
import uuid
from pathlib import Path

# G6c: NAME_FILE secrets, before anything reads NAME. See utils/secret_files.py.
from utils.secret_files import load_secret_files

load_secret_files()

# G2c: before anything imports LangChain. See utils/tracing_guard.py.
from utils.tracing_guard import disable_unapproved_tracing  # noqa: E402

disable_unapproved_tracing()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from routes import (
    admin_audit,
    admin_bot_channels,
    admin_bot_dashboard,
    admin_bot_files,
    admin_bot_realtime,
    admin_bot_sessions,
    admin_bots,
    admin_widget,
    admin_admins,
    auth as auth_routes,
    auth_issuer,
    bff_auth,
    channels as channels_route,
    chat,
    widget,
    workspace as workspace_routes,
)
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from models.admin_users import AdminUsers
from services.bot_purge import PURGE_GRACE, purge_all_pending
from services.scheduler import start_scheduler
from utils import logging_context
from version import APP_VERSION, BUILT_AT, GIT_SHA, info as version_info

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

log = logging.getLogger(__name__)

# Phase 6.5: every log line carries bot_id / user_id / session_id /
# request_id when one is set on the request. The middleware below sets
# these contextvars; the filter copies them into log records.
logging_context.install()

# Phase 10: redact bearer tokens, cookies, and password fields out of log
# records before they hit the handlers. Defense-in-depth — anything that
# accidentally `log.info("body=%s", body)`s a secret gets scrubbed.
# Installed in the LogRecord factory so it covers records from every logger
# (a root-logger filter would only see records logged on root itself).
from services.auth.logging_filter import install_global as _install_redaction  # noqa: E402
_install_redaction()

logo = """
 _____           _ _            _    ___      _                    _
| ____|_ __ ___ (_) |_   _     / \\  |_ _|    / \\   __ _  ___ _ __ | |_ ___
|  _| | '_ ` _ \\| | | | | |   / _ \\  | |    / _ \\ / _` |/ _ \\ '_ \\| __/ __|
| |___| | | | | | | | |_| |  / ___ \\ | |   / ___ \\ (_| |  __/ | | | |_\\__ \\
|_____|_| |_| |_|_|_|\\__, | /_/   \\_\\___| /_/   \\_\\__, |\\___|_| |_|\\__|___/
                     |___/                        |___/
"""
print(logo)


def _assert_runtime_topology() -> None:
    """Refuse to boot in a configuration that we know is broken.

    - Embedded Qdrant + multi-worker: the embedded client uses a local
      file lock; the second worker silently fails to open the DB.
    - SQLite + multi-worker: write contention will lock-storm under
      chat traffic. This is a warning, not a hard fail — operators may
      knowingly accept it for dev / staging.
    - Embedded auth issuer + multi-worker: the keystore is on-disk; with
      `WEB_CONCURRENCY > 1` and a non-shared filesystem each worker would
      generate its own keypair on first boot. Until DB-backed key storage
      lands (Phase 11), refuse to boot in this configuration unless the
      operator has explicitly opted out by disabling the embedded issuer.
    """
    web_concurrency = int(os.environ.get("WEB_CONCURRENCY", "1"))
    qdrant_url = os.environ.get("QDRANT_URL")
    if web_concurrency > 1 and not qdrant_url:
        raise RuntimeError(
            "WEB_CONCURRENCY > 1 with embedded Qdrant is not supported. "
            "Set QDRANT_URL to a Qdrant server (docker-compose for staging, "
            "cluster for prod) before scaling the worker count."
        )
    db_url = os.environ.get("DATABASE_URL", "")
    if web_concurrency > 1 and (db_url.startswith("sqlite") or db_url == ""):
        log.warning(
            "WEB_CONCURRENCY=%d with SQLite — expect write contention "
            "under load. Use Postgres for any production-like deploy.",
            web_concurrency,
        )
    from services.auth.issuer.factory import is_local_issuer_enabled
    if web_concurrency > 1 and is_local_issuer_enabled():
        raise RuntimeError(
            "WEB_CONCURRENCY > 1 with the embedded auth issuer (AUTH_LOCAL_ISSUER_ENABLED=true) "
            "is not supported on a non-shared filesystem. Either point AUTH_LOCAL_KEYS_DIR at a "
            "shared volume, set AUTH_LOCAL_ISSUER_ENABLED=false (delegate identity to an external "
            "OIDC IdP), or stay single-replica. DB-backed key storage is Phase 11 future work."
        )
    # Refuse to boot in production-like modes without an explicit APP_BASE_URL.
    # Falling back to ``request.url.netloc`` (Host header) for the OIDC redirect
    # / post-login normalization opens a Host-spoof window where return_to
    # could resolve to an attacker-controlled origin.
    from utils.runtime import current_mode, is_prod_mode

    mode = current_mode()
    if is_prod_mode() and not os.environ.get("APP_BASE_URL"):
        raise RuntimeError(
            f"MODE={mode!r} requires APP_BASE_URL to be set (the public-facing origin of this app). "
            "Without it the OIDC callback redirect derives from the Host header, which is "
            "user-controllable and unsafe for production deploys."
        )


def _start_purge_scheduler() -> None:
    """Daily background sweep that hard-purges bots whose soft-delete
    grace window has expired (Tier 2). The scheduler instance is global
    to the process — APScheduler's BackgroundScheduler runs in its own
    thread, so the FastAPI request loop is unaffected.
    """

    def _run() -> None:
        try:
            purged = purge_all_pending()
            if purged:
                log.info("Bot purge sweep removed %d bot(s): %s", len(purged), purged)
        except Exception:
            log.exception("Bot purge sweep failed")

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(_run, "cron", hour=3, minute=0, id="bot_purge_sweep", replace_existing=True)
    scheduler.start()
    log.info(
        "Bot purge scheduler started (daily 03:00 UTC, grace=%s)",
        PURGE_GRACE,
    )


def _init_embedded_issuer() -> None:
    """Initialise the embedded OIDC issuer's keystore at boot.

    Generates the RS256 keypair on first boot if missing. Fails fast if the
    keystore can't be opened (typically a permissions issue on
    ``AUTH_LOCAL_KEYS_DIR``). No-op when the embedded issuer is disabled.
    """
    from services.auth.issuer.factory import get_keystore, is_local_issuer_enabled
    if not is_local_issuer_enabled():
        log.info("Built-in sign-in issuer disabled; admins sign in through the external IdP.")
        return
    ks = get_keystore()
    log.info("Embedded OIDC issuer ready; current kid=%s", ks.current_kid())
    from utils.runtime import is_prod_mode

    if is_prod_mode():
        log.warning(
            "Built-in sign-in (email + password) is ACTIVE in production. Configure an "
            "external IdP (AUTH_OIDC_ISSUER, ...) for SSO; the built-in issuer then turns "
            "off by default, or set AUTH_LOCAL_ISSUER_ENABLED=false explicitly."
        )


def _log_llm_endpoint_policy() -> None:
    """G2a: say at boot whether model traffic is restricted, and flag a
    deployment default endpoint that the allowlist itself blocks."""
    from services.llm_settings import ALLOWLIST_ENV, endpoint_allowlist, endpoint_policy_warnings

    entries = endpoint_allowlist()
    if entries is not None:
        log.info("%s: model traffic limited to %d approved endpoint(s)", ALLOWLIST_ENV, len(entries))
    for w in endpoint_policy_warnings():
        log.warning(w["message"])


def _bootstrap_pending_superadmin() -> None:
    """Pre-stage the first admin from ``AUTH_BOOTSTRAP_SUPERADMIN_EMAIL``.
    See ``services.auth.bootstrap`` — a generated temporary password is
    written to a 0600 file under DATA_DIR, never logged."""
    from services.auth.bootstrap import bootstrap_pending_superadmin

    bootstrap_pending_superadmin()


_assert_runtime_topology()
# C8: production needs an explicit, valid BOT_SECRETS_KEY.
from services.secrets import validate_configuration as _validate_secrets_key  # noqa: E402
_validate_secrets_key()
_init_embedded_issuer()
from services.auth.factory import check_required_authentication as _check_mfa  # noqa: E402
_check_mfa()  # G3b: required acr/amr need an external IdP
from services.auth.session_limits import check_settings as _check_session_limits  # noqa: E402
_check_session_limits()  # G3a: a malformed AUTH_SESSION_MAX_AGE_SECONDS stops start-up
_log_llm_endpoint_policy()
from services.crawl_fetch import log_crawl_settings as _log_crawl_settings  # noqa: E402
_log_crawl_settings()  # G10a: a malformed CRAWL_ALLOWED_PRIVATE_CIDRS stops start-up
from services.egress import check_qdrant_settings as _check_qdrant  # noqa: E402
_check_qdrant()  # G8c: a remote Qdrant needs an API key in production
_bootstrap_pending_superadmin()
_start_purge_scheduler()

from services.email_service import log_email_status  # noqa: E402

log_email_status()


def _register_channel_adapters() -> None:
    """Import side-effect: each adapter module calls
    ``channels.registry.register`` at import time."""
    # Import order matters only for log clarity — registry rejects
    # duplicates, so import-time side-effects are idempotent.
    import channels.telegram  # noqa: F401
    import channels.slack  # noqa: F401
    import channels.whatsapp_cloud  # noqa: F401
    import channels.google_chat  # noqa: F401
    import channels.teams  # noqa: F401
    from channels.dispatcher import install_redaction_filter

    install_redaction_filter()


_register_channel_adapters()


# Load every bot's config_json under the current schema at boot. N4: a
# bot whose config doesn't load is logged and listed on /api/readyz, and
# answers "not configured" until fixed; it no longer stops the other
# bots. Only a failure to list the bots at all stops start-up.
try:
    from services.bot_config import boot_validate_all_configs
    boot_validate_all_configs()
except Exception:
    log.exception("Bot config schema validation failed at boot")
    raise

# G2d: one INFO block naming every outbound destination, by host only.
from services.egress import log_egress_summary  # noqa: E402

log_egress_summary()


class CustomMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        logging_context.request_id_var.set(request_id)
        try:
            user_id = request.headers.get("X-Emly-UserID")
            session_id = request.headers.get("X-Emly-SessionID")
            if user_id:
                logging_context.user_id_var.set(user_id)
            if session_id:
                logging_context.session_id_var.set(session_id)
            response = await call_next(request)
            if user_id:
                response.headers["X-Emly-UserID"] = user_id
            if session_id:
                response.headers["X-Emly-SessionID"] = session_id
            response.headers["Access-Control-Expose-Headers"] = "X-Emly-UserID, X-Emly-SessionID, X-Emly-PageID"
            return response
        except Exception:
            logging.exception("Unhandled exception in CustomMiddleware")
            return JSONResponse(status_code=500, content={"message": "Internal server error"})


from utils.runtime import (  # noqa: E402
    API_DOC_PATHS,
    api_docs_enabled,
    fastapi_docs_kwargs,
)

# A5: /docs, /redoc and /openapi.json are off when MODE is production-like,
# unless ENABLE_API_DOCS=true.
app = FastAPI(**fastapi_docs_kwargs())
# Phase 10: rate-limit auth-sensitive endpoints. Routes opt in by decorating
# their handlers with `@limiter.limit(...)` — the middleware below converts
# 429 responses into JSON-shaped errors instead of plain text.
from services.auth.ratelimit import limiter  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402
app.state.limiter = limiter
# S2: the body cap sits innermost, directly around the routes. Outside a
# BaseHTTPMiddleware (SlowAPI, CustomMiddleware) its 413 would reach FastAPI
# wrapped in an ExceptionGroup and come back as a 400.
from services.request_limits import RequestBodyLimitMiddleware  # noqa: E402
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(SlowAPIMiddleware)


def _retry_after_seconds(request, exc: RateLimitExceeded) -> int:
    """Seconds until the exceeded window resets, from the limiter's own
    counters (slowapi records the limit it hit on ``request.state``)."""
    try:
        item, keys = request.state.view_rate_limit
        reset_at, _remaining = limiter.limiter.get_window_stats(item, *keys)
        return max(1, math.ceil(reset_at - time.time()))
    except Exception:
        try:
            return max(1, int(exc.limit.limit.get_expiry()))
        except Exception:
            return 60


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_exceeded_handler(request, exc: RateLimitExceeded):
    from services.auth.error_page import error_page, wants_html

    retry_after = _retry_after_seconds(request, exc)
    if wants_html(request):
        wait = (
            f"{retry_after} seconds" if retry_after < 120
            else f"{math.ceil(retry_after / 60)} minutes"
        )
        return error_page(
            "Too many sign-in attempts",
            "You've tried to sign in too many times in a short period. "
            f"Please wait {wait} and try again.",
            status_code=429,
            retry_after=retry_after,
        )
    return JSONResponse(
        {"detail": {"code": "rate_limited", "limit": str(exc.detail)}},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


app.include_router(chat.router, prefix="", tags=["widget"], include_in_schema=False)
app.include_router(admin_admins.router, prefix="/api/admin", tags=["admin-mgmt"])
app.include_router(admin_bots.router, prefix="/api/admin", tags=["admin-bots"])
app.include_router(admin_widget.router, prefix="/api/admin", tags=["admin-widget"])
app.include_router(admin_bot_files.router, prefix="/api/admin", tags=["admin-bot-files"])
app.include_router(admin_bot_dashboard.router, prefix="/api/admin", tags=["admin-bot-dashboard"])
app.include_router(admin_bot_channels.router, prefix="/api/admin", tags=["admin-bot-channels"])
# Phase 5 backend-backfill: per-session aggregation + audit-log read endpoints.
app.include_router(admin_bot_sessions.router, prefix="/api/admin", tags=["admin-bot-sessions"])
app.include_router(admin_audit.router, prefix="/api/admin", tags=["admin-audit"])
# Phase 9 backend-backfill: SSE live conversation feed (single-replica only).
app.include_router(admin_bot_realtime.router, prefix="/api/admin", tags=["admin-bot-realtime"])
# Workspace-scoped admin (§6): defaults, saved templates, bulk bot actions.
app.include_router(workspace_routes.router, prefix="/api/admin", tags=["workspace"])
app.include_router(channels_route.router, prefix="", tags=["channels"])
# New OIDC admin client — login/callback/logout/me. Always mounted.
app.include_router(auth_routes.router, prefix="/api/admin", tags=["admin-auth-oidc"])
# BFF (Backend-for-Frontend) auth surface — silent refresh + refresh-claims
# endpoints that the JS proactive-refresh interceptor calls. Mounted at
# /api/auth (NOT /api/admin) so the embedded issuer's loopback discovery
# and the public `session_expires_at` cookie stay clearly separated from
# the admin-only surface.
app.include_router(bff_auth.router, prefix="", tags=["bff-auth"])
# Embedded OIDC issuer — mounted only when AUTH_LOCAL_ISSUER_ENABLED=true (default).
# Endpoints are at /.well-known/* and /api/auth/local/*.
from services.auth.issuer.factory import is_local_issuer_enabled  # noqa: E402
if is_local_issuer_enabled():
    app.include_router(auth_issuer.router, prefix="", tags=["auth-issuer"])
# Public widget-support endpoints — no /api prefix; the widget calls
# the same origin as it loads from. Mounted at "" so the routes look
# like ``GET /widget/{bot_id}/config`` and ``POST /widget/{bot_id}/action``.
app.include_router(widget.router, prefix="", tags=["widget"], include_in_schema=False)
app.add_middleware(CustomMiddleware)
# G13c: hard cap on widget request bodies, counted as bytes arrive. Inside
# the CORS layers so the widget can read the 413.
# Record caller IP / user agent for audit() calls made while handling a request.
from services.audit import AuditRequestContextMiddleware, AuditUnavailable  # noqa: E402
app.add_middleware(AuditRequestContextMiddleware)


@app.exception_handler(AuditUnavailable)
async def _audit_unavailable(request: Request, exc: AuditUnavailable):
    # Q-G5a: a security event couldn't be audited, so the request fails.
    return JSONResponse(status_code=503, content={"detail": {"code": "audit_unavailable"}})
# CSRF: cookie-authenticated mutating requests must carry an Origin from the
# trusted-list. Bearer-only callers (no cookie) bypass — without ambient
# credentials there's no CSRF surface. Mounted before CORS so the response
# headers from CORS still apply on a 403.
from services.auth.csrf import OriginCheckMiddleware  # noqa: E402
app.add_middleware(OriginCheckMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # Cannot be True with allow_origins=["*"] per the CORS spec; the auth model
    # here is header-based (X-Emly-UserID), not cookie-based, so False is correct.
    # WidgetCORSMiddleware (added below, runs outermost) overrides these wide-open
    # headers for ``/widget/{slug}/*`` using each bot's ``widget_allowed_origins``.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Per-bot CORS enforcement for the public widget surfaces. Sits outside the
# global CORSMiddleware so it can short-circuit preflight and overwrite the
# wide-open ``*`` on actual responses with the resolved per-bot origin.
from services.auth.widget_cors import WidgetCORSMiddleware  # noqa: E402
app.add_middleware(WidgetCORSMiddleware)
# Security headers (nosniff, Referrer-Policy, HSTS on https, anti-framing
# outside the widget surfaces). Added last = outermost, so it also covers
# responses the CORS layers short-circuit (403s, preflights).
from services.security_headers import SecurityHeadersMiddleware  # noqa: E402
app.add_middleware(SecurityHeadersMiddleware)


@app.on_event("startup")
def _log_version_banner() -> None:
    logging.info(
        "emly-ai-assistant version=%s sha=%s built_at=%s",
        APP_VERSION, GIT_SHA, BUILT_AT,
    )


@app.on_event("startup")
def _boot_scheduler() -> None:
    start_scheduler()


INVITE_EXPIRY_SWEEP_REF = "services.invite_email:sweep_expired_invites"
DATA_RETENTION_PURGE_REF = "services.data_retention:purge_expired_data"


@app.on_event("startup")
def _register_data_retention_purge() -> None:
    """Nightly per-bot retention purge plus expired OTP and webhook dedupe
    cleanup (D4). Registered by import path for the persistent jobstore."""
    try:
        from services.scheduler import scheduler

        scheduler.add_job(
            DATA_RETENTION_PURGE_REF,
            "cron",
            hour=3,
            minute=45,
            id="data_retention_purge",
            replace_existing=True,
        )
    except Exception:
        logging.exception("failed to register data retention purge")


@app.on_event("startup")
def _register_audit_retention_purge() -> None:
    """Daily purge of audit rows older than AUDIT_RETENTION_DAYS (C7).
    Registered by import path so the persistent jobstore can store it."""
    try:
        from services.scheduler import scheduler

        scheduler.add_job(
            "services.audit:purge_expired_audit_rows",
            "cron",
            hour=3,
            minute=30,
            id="audit_retention_purge",
            replace_existing=True,
        )
    except Exception:
        logging.exception("failed to register audit retention purge")


@app.on_event("startup")
def _register_invite_expiry_sweep() -> None:
    """Hourly sweep that promotes any ``pending`` ``pending_admin`` row whose
    ``expires_at`` is in the past to ``status='expired'``. Idempotent; the
    accept route also re-checks expiry per-row before granting access.
    Registered by import path: the persistent jobstore can't pickle a
    nested function, so the job never got stored before."""
    try:
        from services.scheduler import scheduler

        scheduler.add_job(
            INVITE_EXPIRY_SWEEP_REF,
            "interval",
            hours=1,
            id="invite_expiry_sweep",
            replace_existing=True,
        )
    except Exception:
        logging.exception("failed to register invite expiry sweep")


@app.on_event("startup")
def _recover_pending_file_embeds() -> None:
    # A pod restart mid-embed leaves file rows stuck in pending/embedding
    # forever; this sweep re-queues them so the runtime self-heals.
    try:
        from routes.admin_bot_files import recover_pending_embeds
        n = recover_pending_embeds()
        if n:
            logging.info("Re-queued %d pending file embed(s) on startup", n)
    except Exception:
        logging.exception("Pending-embed recovery failed at startup")


@app.on_event("startup")
async def _start_crawl_worker_supervisor() -> None:
    # Resume any in-progress crawl jobs and pick up future ones.
    try:
        from services.crawl_worker import start_supervisor
        start_supervisor()
    except Exception:
        logging.exception("Crawl-worker supervisor failed to start")


@app.on_event("startup")
async def _start_enrichment_worker() -> None:
    # Phase 8 backend-backfill: async sentiment + intent classifier worker.
    # Opt-in per bot via `bots.config_json["enrichment_enabled"]`; the
    # worker no-ops on disabled bots. Set ENRICHMENT_DISABLED=1 to skip
    # the worker entirely (e.g. CI / migrations-only boots).
    try:
        from services.enrichment import start_worker as start_enrichment_worker
        start_enrichment_worker()
    except Exception:
        logging.exception("Enrichment worker failed to start")


@app.on_event("shutdown")
async def _stop_enrichment_worker() -> None:
    try:
        from services.enrichment import stop_worker as stop_enrichment_worker
        stop_enrichment_worker()
    except Exception:
        logging.debug("Enrichment worker shutdown failed", exc_info=True)


@app.get("/api/health")
async def health():
    """Legacy alias for ``/api/livez`` — kept so existing dashboards keep working."""
    return JSONResponse(status_code=200, content={"message": "Bot is running"})


@app.get("/api/version")
async def version():
    """Build provenance for the running container.

    Returns ``{version, git_sha, built_at}`` from values baked in by the
    Dockerfile (``APP_VERSION`` / ``GIT_SHA`` / ``BUILT_AT`` build args).
    No auth — health-adjacent and useful for incident triage.
    """
    return JSONResponse(status_code=200, content=version_info())


@app.get("/api/livez")
async def livez():
    """Liveness probe. Returns 200 as long as the process is up.

    Don't add dependency checks here — Kubernetes will SIGKILL the
    worker on failure, which is too aggressive for transient deps.
    """
    return JSONResponse(status_code=200, content={"status": "alive"})


@app.get("/api/readyz")
async def readyz():
    """Readiness probe.

    Returns 200 only when the worker can handle traffic — embedding
    model loaded, Qdrant reachable, DB reachable. Cold-start typically
    takes 5-30s; ``initialDelaySeconds`` should be ≥30 to avoid the
    Kubernetes scheduler routing requests at a half-booted pod.

    Tier 2 of multi-bot-ui plan: returns ``warnings`` alongside
    ``issues`` so the admin UI can surface non-fatal deploy hazards
    (embedded Qdrant + multi-worker, SQLite + multi-worker, wide-open
    CORS) as a system banner.
    """
    issues: list[str] = []
    warnings = _collect_runtime_warnings()

    try:
        from config import EMBEDDING_MODEL_INSTANCE
        if EMBEDDING_MODEL_INSTANCE is None:
            issues.append("embedding_model_not_loaded")
    except Exception as e:
        issues.append(f"embedding_model_error: {e}")

    try:
        from agents.rag_manager import get_rag_manager
        get_rag_manager().client.get_collections()
    except Exception as e:
        issues.append(f"qdrant_unreachable: {e}")

    try:
        from db.db import DB
        DB.execute_sql("SELECT 1")
    except Exception as e:
        issues.append(f"db_unreachable: {e}")

    if issues:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "issues": issues, "warnings": warnings},
        )
    return JSONResponse(
        status_code=200, content={"status": "ready", "warnings": warnings}
    )


def _collect_runtime_warnings() -> list[dict[str, str]]:
    """Non-fatal deploy-shape hazards. Each entry is `{code, message}`
    — the code is a stable identifier for the UI to dismiss/render
    against; the message is operator-facing prose."""
    out: list[dict[str, str]] = []
    web_concurrency = int(os.environ.get("WEB_CONCURRENCY", "1"))
    qdrant_url = os.environ.get("QDRANT_URL")
    db_url = os.environ.get("DATABASE_URL", "")

    # Embedded Qdrant + multi-worker is *blocked* by the boot assertion
    # in `_assert_runtime_topology` — reaching here means the assertion
    # passed. Still surface a notice when single-worker so operators
    # know they can't scale out without server Qdrant.
    if not qdrant_url:
        out.append({
            "code": "embedded_qdrant",
            "message": "Vector DB is in embedded mode. Set QDRANT_URL before scaling beyond one worker.",
        })

    if web_concurrency > 1 and (db_url.startswith("sqlite") or db_url == ""):
        out.append({
            "code": "sqlite_multi_worker",
            "message": "Multi-worker deploy with SQLite — expect write contention. Use Postgres for production.",
        })

    from services.llm_settings import endpoint_policy_warnings

    out.extend(endpoint_policy_warnings())

    from services.egress import qdrant_warnings

    out.extend(qdrant_warnings())
    from services.egress import postgres_tls_warnings

    out.extend(postgres_tls_warnings())

    from utils.runtime import is_prod_mode

    if is_prod_mode():
        from services.auth.widget_cors import bots_open_to_any_site

        open_bots = bots_open_to_any_site()
        if open_bots:
            out.append({
                "code": "widget_any_origin",
                "message": f"{len(open_bots)} bot(s) let any website embed their widget (Allowed widget "
                           f"origins is *): {', '.join(open_bots[:20])}"
                           f"{' …' if len(open_bots) > 20 else ''}. Set each one's origins to your own sites.",
            })

    from services.audit import audit_write_failures

    failures = audit_write_failures()
    if failures:
        out.append({
            "code": "audit_write_failures",
            "message": f"{failures} audit log write(s) failed since start-up. Check the database and "
                       "the AUDIT WRITE FAILED lines in the logs.",
        })

    from services.bot_config import invalid_bot_configs

    broken = invalid_bot_configs()
    if broken:
        out.append({
            "code": "bot_config_invalid",
            "message": f"{len(broken)} bot(s) have a config that can't be loaded and won't answer until an "
                       f"admin saves a valid one: {', '.join(sorted(broken))}. See the start-up log.",
        })
    return out


# ----------------------------------------------------------------------------
# UI: serve the Next.js static export from /ui/out at the application root.
# We register a catch-all *last* so all explicit /emly, /api, /api/v1 routes
# above take precedence. Unknown paths fall back to index.html so the SPA
# can handle client-side routing (/login, /admins, /accept-invite, etc.).
#
# BFF server-side gate: the catchall also acts as the "middleware.ts"
# for the static-export build. Next.js's runtime middleware is excluded
# by `output: "export"`, so this is the only place the SPA's auth
# redirect can be enforced server-side. The companion `ui/middleware.ts`
# is kept for dev parity (and any future server-side deployment); the
# runtime enforcement lives here in production.
# ----------------------------------------------------------------------------
_UI_DIR = Path(__file__).parent / "ui" / "out"
if (_UI_DIR / "_next").exists():
    app.mount("/_next", StaticFiles(directory=_UI_DIR / "_next"), name="next-assets")


# UI paths that require a session cookie. Anything outside this set is
# public (login, request-access, the root index which does its own
# client-side redirect). The set is deliberately narrow — we'd rather
# over-serve a public page than under-serve a protected one, because
# each protected page has its own client-side `me()` check that
# double-gates against a missing/expired id_token.
_PROTECTED_UI_PREFIXES = (
    "/bots/",
    "/admins",
    "/workspace/",
    "/accept-invite",
    "/users",
    "/conversations",
    "/dashboard",
    "/config",
)


def _session_cookie_name() -> str:
    return os.environ.get("AUTH_COOKIE_NAME", "emly_admin_session")


def _is_protected_ui_path(rel_path: str) -> bool:
    """Does the requested UI path need a session cookie?

    Only matches exact prefixes that map to a protected page in the
    Next.js app/. We compare against the *path* (no query string)
    so ``/bots?new=1`` is still treated as protected. The FastAPI
    ``{full_path:path}`` route strips the leading ``/`` so we
    normalise both sides here.
    """
    if not rel_path:
        return False  # root is the public landing
    normalised = "/" + rel_path.lstrip("/")
    for prefix in _PROTECTED_UI_PREFIXES:
        if normalised == prefix.rstrip("/") or normalised.startswith(prefix):
            return True
    return False


def _login_redirect_target(request: Request) -> str:
    """Build ``/api/admin/auth/login?return_to=...`` for the current request.

    The ``return_to`` is a same-origin absolute path so the IdP
    callback (or its host-relative post-login redirect) lands the user
    back where they started. ``urlparse`` guards against any future
    operator who tries to inject an offsite target.
    """
    app_base = _app_base_url_from_request(request)
    raw = request.url.path
    if not raw.startswith("/") or raw.startswith("//"):
        return f"{app_base}/api/admin/auth/login"
    return f"{app_base}/api/admin/auth/login?return_to={urllib.parse.quote(raw, safe='/?&=')}"


def _app_base_url_from_request(request: Request) -> str:
    """Mirror of ``_app_base_url`` in routes/auth.py but without the
    cross-module import — kept local so the catchall can stay
    self-contained."""
    explicit = os.environ.get("APP_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    netloc = request.url.netloc or request.headers.get("host")
    if netloc:
        return f"{request.url.scheme}://{netloc}".rstrip("/")
    return "http://localhost:8080"


def _ui_file(path: Path) -> FileResponse:
    """Serve a file from the UI export. HTML pages carry the admin UI's
    Content-Security-Policy, with hashes for their inline scripts (G13b)."""
    if path.suffix == ".html":
        from services.ui_csp import ui_policy_for_file

        return FileResponse(path, headers={"Content-Security-Policy": ui_policy_for_file(path)})
    return FileResponse(path)


def _serve_ui_path(request: Request | None, rel_path: str) -> Response:
    # With API docs disabled, their paths must 404 — not fall through to
    # the SPA shell below and look like they exist.
    if rel_path.strip("/") in API_DOC_PATHS and not api_docs_enabled():
        return JSONResponse(status_code=404, content={"message": "Not found"})
    # Unknown API and widget paths are 404s, not the admin UI's HTML with a
    # 200: a client calling a wrong or retired endpoint must see the error
    # (found in the G8b restore drill). The UI has no pages under these.
    first = rel_path.strip("/").split("/", 1)[0]
    if first in ("api", "widget"):
        return JSONResponse(status_code=404, content={"message": "Not found"})

    if not _UI_DIR.exists():
        return JSONResponse(
            status_code=503,
            content={"message": "UI not built. Run `npm install && npm run build` in /ui."},
        )

    # BFF server-side gate: when the requested path is a protected UI
    # route and no session cookie is present, 302 to the IdP login
    # flow with the original path stashed in ``return_to``. We do a
    # presence check only (no JWT verification) so the catchall stays
    # cheap on the hot path; the per-page `me()` probe in the React
    # shell catches the "expired cookie" case and the JS proactive
    # refresh keeps that from happening in practice. This mirrors the
    # `ui/middleware.ts` redirect for dev / non-static-export deploys.
    if request is not None and _is_protected_ui_path(rel_path):
        if not request.cookies.get(_session_cookie_name()):
            return RedirectResponse(
                url=_login_redirect_target(request),
                status_code=302,
            )

    # Try the literal path first (handles /favicon.ico, /file.svg, etc.).
    candidate = (_UI_DIR / rel_path).resolve()
    if _UI_DIR.resolve() in candidate.parents and candidate.is_file():
        return _ui_file(candidate)

    # Next.js static export emits a foo.html for the /foo route.
    if rel_path:
        html_candidate = (_UI_DIR / f"{rel_path}.html").resolve()
        if _UI_DIR.resolve() in html_candidate.parents and html_candidate.is_file():
            return _ui_file(html_candidate)

    # Per-bot routes — `/bots/<slug>/<tab>` — are statically exported
    # under a placeholder slug `_` (Next.js requires `generateStaticParams`
    # under `output: "export"`; runtime slugs aren't known at build
    # time). Substitute `_` for the actual slug and serve whatever the
    # build emitted — both the `.html` scaffold AND the App Router's
    # per-segment RSC payloads (`__next.bots.$d$slug.<tab>.__PAGE__.txt`,
    # `<tab>.txt`, ...). Without this, RSC fetches during client-side
    # navigation fall through to index.html, Next gives up on the soft
    # nav, and the whole shell remounts on every menu click.
    # `/admins/<admin_id>` follows the same pattern; its static siblings
    # (`/admins/me`, `/admins/audit`) were already served by the literal
    # `.html` lookup above.
    parts = rel_path.split("/") if rel_path else []
    rewritten = None
    if len(parts) >= 3 and parts[0] == "bots":
        rewritten = "/".join(["bots", "_", *parts[2:]])
    elif len(parts) >= 2 and parts[0] == "admins" and parts[1]:
        # Keep any extension so `/admins/<id>.txt` maps to `/admins/_.txt`.
        _, dot, ext = parts[1].partition(".")
        rewritten = "/".join(["admins", f"_{dot}{ext}", *parts[2:]])
    if rewritten is not None:
        literal = (_UI_DIR / rewritten).resolve()
        if _UI_DIR.resolve() in literal.parents and literal.is_file():
            return _ui_file(literal)
        scaffold = (_UI_DIR / f"{rewritten}.html").resolve()
        if _UI_DIR.resolve() in scaffold.parents and scaffold.is_file():
            return _ui_file(scaffold)

    # SPA fallback.
    index = _UI_DIR / "index.html"
    if index.is_file():
        return _ui_file(index)
    return JSONResponse(status_code=404, content={"message": "Not found"})


@app.api_route("/", methods=["GET", "HEAD"])
async def ui_root(request: Request):
    return _serve_ui_path(request, "")


@app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
async def ui_catchall(request: Request, full_path: str):
    return _serve_ui_path(request, full_path)
