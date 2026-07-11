import json
import logging
import os
import secrets
import time
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from routes import (
    actions,
    admin,
    admin_audit,
    admin_bot_channels,
    admin_bot_dashboard,
    admin_bot_files,
    admin_bot_realtime,
    admin_bot_sessions,
    admin_bots,
    admin_dashboard,
    admin_admins,
    auth as auth_routes,
    auth_issuer,
    channels as channels_route,
    chat,
    widget,
)
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from models.admin_users import AdminUsers
from services.bot_purge import PURGE_GRACE, purge_all_pending
from utils import logging_context
from utils.utils import create_emly_user

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
from services.auth.logging_filter import install as _install_redaction  # noqa: E402
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
        log.info("Embedded OIDC issuer disabled (AUTH_LOCAL_ISSUER_ENABLED=false).")
        return
    ks = get_keystore()
    log.info("Embedded OIDC issuer ready; current kid=%s", ks.current_kid())


def _bootstrap_pending_superadmin() -> None:
    """Pre-stage the first admin in ``pending_admins`` from
    ``AUTH_BOOTSTRAP_SUPERADMIN_EMAIL``.

    Idempotent — runs on every boot. Behavior:
      - No admin yet, no pending row → create both, seed credential.
      - Pending row exists, ``AUTH_LOCAL_BOOTSTRAP_PASSWORD`` set →
        reconcile the credential to match the env (operator's env wins).
      - Pending row exists, env password unset → leave the existing
        credential alone (don't churn the random one).
      - Admin user exists (post-activation) → do nothing; password reset
        goes through the proper change-password flow.

    The temporary password is printed exactly once with the
    ``[BOOTSTRAP]`` prefix when it was auto-generated.
    """
    bootstrap_email = os.environ.get("AUTH_BOOTSTRAP_SUPERADMIN_EMAIL", "").strip().lower()
    if not bootstrap_email:
        return

    if AdminUsers.get_by_email(bootstrap_email) is not None:
        # Already activated — env-based password reset isn't supported here.
        # Operators use /api/auth/local/password/change (Phase 2c follow-up)
        # or rotate via SQL.
        return

    from services.auth.issuer.factory import is_local_issuer_enabled
    from models.pending_admins import PendingAdmins

    pending_exists = PendingAdmins.get_active_by_email(bootstrap_email) is not None
    if not pending_exists:
        PendingAdmins.create(
            email=bootstrap_email,
            invited_by=None,
            is_superadmin=True,
            bot_assignments=[],
        )

    if not is_local_issuer_enabled():
        return

    from models.local_credentials import LocalCredentials
    from services.auth.issuer.passwords import hash_password

    sentinel_id = f"bootstrap:{bootstrap_email}"
    env_password = os.environ.get("AUTH_LOCAL_BOOTSTRAP_PASSWORD", "").strip()
    existing_credential = LocalCredentials.get(sentinel_id)

    if existing_credential is None:
        # First-time provisioning: use env password if set, else generate.
        password = env_password or secrets.token_urlsafe(18)
        LocalCredentials.upsert(
            admin_id=sentinel_id,
            password_hash=hash_password(password),
            must_change=True,
        )
        if env_password:
            log.warning(
                "[BOOTSTRAP] superadmin %s — credential seeded from "
                "AUTH_LOCAL_BOOTSTRAP_PASSWORD env var",
                bootstrap_email,
            )
        else:
            log.warning(
                "[BOOTSTRAP] superadmin %s — temporary password: %s "
                "(must be changed on first login; pin AUTH_LOCAL_BOOTSTRAP_PASSWORD "
                "in env to reuse a known value across rebuilds)",
                bootstrap_email,
                password,
            )
    elif env_password:
        # Reconcile to env — the operator changed/added AUTH_LOCAL_BOOTSTRAP_PASSWORD
        # since the last boot, and the pending admin is still un-activated.
        LocalCredentials.upsert(
            admin_id=sentinel_id,
            password_hash=hash_password(env_password),
            must_change=True,
        )
        log.warning(
            "[BOOTSTRAP] superadmin %s — credential reconciled to "
            "AUTH_LOCAL_BOOTSTRAP_PASSWORD env var",
            bootstrap_email,
        )
    else:
        log.info(
            "[BOOTSTRAP] superadmin %s — credential already provisioned; "
            "set AUTH_LOCAL_BOOTSTRAP_PASSWORD to reset to a known value",
            bootstrap_email,
        )


_assert_runtime_topology()
_init_embedded_issuer()
_bootstrap_pending_superadmin()
_start_purge_scheduler()


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


# Validate every bot's config_json against the current Pydantic schema
# at boot. A bot whose config is unparseable is a deploy bug, not a
# runtime fallback — fail fast in CI/CD.
try:
    from services.bot_config import boot_validate_all_configs
    boot_validate_all_configs()
except Exception:
    log.exception("Bot config schema validation failed at boot")
    raise


class CustomMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        logging_context.request_id_var.set(request_id)
        try:
            user_id = request.headers.get("X-Emly-UserID")
            session_id = request.headers.get("X-Emly-SessionID")
            page_id = request.headers.get("X-Emly-PageID")
            bot_id = request.headers.get("X-Emly-BotID")
            if bot_id:
                logging_context.bot_id_var.set(bot_id)
                request.state.bot_id = bot_id
            if user_id:
                logging_context.user_id_var.set(user_id)
            if session_id:
                logging_context.session_id_var.set(session_id)
            # Match the exact widget shape: ``/widget/{ref}/{action}``
            # (three trailing segments). Anything deeper or shorter is
            # not a widget route and shouldn't bypass the helper.
            _path_parts = request.url.path.split("/")
            _is_widget_route = (
                len(_path_parts) == 4 and _path_parts[1] == "widget" and _path_parts[3] != ""
            )
            _is_widget_chat = _is_widget_route and _path_parts[3] == "chat"
            # Auto-create an end-user for legacy /emly/* surfaces. Widget routes
            # do their own bot-scoped upsert in `routes/widget.py`, so we skip
            # the helper there to avoid double-inserting the row.
            if request.url.path.startswith("/emly/") and not _is_widget_route and bot_id:
                create_emly_user(request, bot_id)
            # Inject identity headers into the body for both chat surfaces:
            # - legacy `/emly/api/chat` (requires X-Emly-BotID)
            # - path-scoped `/widget/{bot_slug-or-id}/chat` (bot from URL)
            is_chat = request.url.path == "/emly/api/chat" or _is_widget_chat
            if is_chat:
                # Read the body once; the widget-chat fallback path needs to
                # peek at it before deciding whether to 400.
                body = await request.body()
                body_str = body.decode("utf-8")
                try:
                    data = json.loads(body_str) if body_str else {}
                except json.JSONDecodeError:
                    return JSONResponse(status_code=400, content={"message": "Invalid JSON body"})
                if not isinstance(data, dict):
                    return JSONResponse(status_code=400, content={"message": "Body must be a JSON object"})

                if _is_widget_chat:
                    # The path-scoped widget endpoint identifies the bot via
                    # the URL and the AgentRequest body already carries
                    # ``user_id``/``session_id``. The legacy widget bundle
                    # has a race where the chat fetch can fire before its
                    # localStorage-backed identity init populates the
                    # ``X-Emly-UserID``/``X-Emly-SessionID`` headers — fall
                    # back to body fields, generating fresh IDs only as a
                    # last resort. The response still echoes the resolved
                    # IDs in the response headers so the widget can adopt
                    # them for subsequent calls.
                    body_user = data.get("user_id") if isinstance(data.get("user_id"), str) else None
                    body_session = data.get("session_id") if isinstance(data.get("session_id"), str) else None
                    user_id = user_id or body_user or f"emly-gs-{uuid.uuid4()}"
                    session_id = session_id or body_session or f"session-{uuid.uuid4()}"
                else:
                    # Legacy /emly/api/chat keeps the strict header
                    # contract — its callers always set X-Emly-* headers
                    # and a silent fallback would mask integration bugs.
                    if not user_id:
                        return JSONResponse(status_code=400, content={"message": "User ID is required"})
                    if not session_id:
                        return JSONResponse(status_code=400, content={"message": "Session ID is required"})
                    if not bot_id:
                        return JSONResponse(status_code=400, content={"message": "Bot ID is required (X-Emly-BotID header)"})

                data["user_id"] = user_id
                data["session_id"] = session_id
                data["timestamp"] = int(time.time())
                data["page_id"] = page_id
                if bot_id:
                    data["bot_id"] = bot_id
                request._body = json.dumps(data).encode("utf-8")
            response = await call_next(request)
            if user_id:
                response.headers["X-Emly-UserID"] = user_id
            if session_id:
                response.headers["X-Emly-SessionID"] = session_id
            if bot_id:
                response.headers["X-Emly-BotID"] = bot_id
            response.headers["Access-Control-Expose-Headers"] = "X-Emly-UserID, X-Emly-SessionID, X-Emly-PageID, X-Emly-BotID"
            return response
        except Exception:
            logging.exception("Unhandled exception in CustomMiddleware")
            return JSONResponse(status_code=500, content={"message": "Internal server error"})


app = FastAPI()
# Phase 10: rate-limit auth-sensitive endpoints. Routes opt in by decorating
# their handlers with `@limiter.limit(...)` — the middleware below converts
# 429 responses into JSON-shaped errors instead of plain text.
from services.auth.ratelimit import limiter  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_exceeded_handler(request, exc: RateLimitExceeded):
    return JSONResponse({"detail": {"code": "rate_limited", "limit": str(exc.detail)}}, status_code=429)
app.include_router(chat.router, prefix="/emly", tags=["chat"])
# Phase 4: path-scoped widget endpoint at /widget/{bot_slug}/chat. Same
# router; the route itself fully qualifies the URL so a second include
# under "" exposes ``/widget/...`` without re-prefixing the legacy
# ``/emly/...`` paths. (FastAPI resolves on full path, so the duplicate
# include doesn't shadow the legacy router.)
app.include_router(chat.router, prefix="", tags=["widget"], include_in_schema=False)
app.include_router(actions.router, prefix="/emly", tags=["actions"])
app.include_router(admin.router, prefix="/api/v1", tags=["admin"])
app.include_router(admin_admins.router, prefix="/api/admin", tags=["admin-mgmt"])
app.include_router(admin_dashboard.router, prefix="/api/admin", tags=["admin-dashboard"])
app.include_router(admin_bots.router, prefix="/api/admin", tags=["admin-bots"])
app.include_router(admin_bot_files.router, prefix="/api/admin", tags=["admin-bot-files"])
app.include_router(admin_bot_dashboard.router, prefix="/api/admin", tags=["admin-bot-dashboard"])
app.include_router(admin_bot_channels.router, prefix="/api/admin", tags=["admin-bot-channels"])
# Phase 5 backend-backfill: per-session aggregation + audit-log read endpoints.
app.include_router(admin_bot_sessions.router, prefix="/api/admin", tags=["admin-bot-sessions"])
app.include_router(admin_audit.router, prefix="/api/admin", tags=["admin-audit"])
# Phase 9 backend-backfill: SSE live conversation feed (single-replica only).
app.include_router(admin_bot_realtime.router, prefix="/api/admin", tags=["admin-bot-realtime"])
app.include_router(channels_route.router, prefix="", tags=["channels"])
# New OIDC admin client — login/callback/logout/me. Always mounted.
app.include_router(auth_routes.router, prefix="/api/admin", tags=["admin-auth-oidc"])
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

    return out


# ----------------------------------------------------------------------------
# UI: serve the Next.js static export from /ui/out at the application root.
# We register a catch-all *last* so all explicit /emly, /api, /api/v1 routes
# above take precedence. Unknown paths fall back to index.html so the SPA
# can handle client-side routing (/login, /admins, /accept-invite, etc.).
# ----------------------------------------------------------------------------
_UI_DIR = Path(__file__).parent / "ui" / "out"
if (_UI_DIR / "_next").exists():
    app.mount("/_next", StaticFiles(directory=_UI_DIR / "_next"), name="next-assets")


def _serve_ui_path(rel_path: str) -> Response:
    if not _UI_DIR.exists():
        return JSONResponse(
            status_code=503,
            content={"message": "UI not built. Run `npm install && npm run build` in /ui."},
        )

    # Try the literal path first (handles /favicon.ico, /file.svg, etc.).
    candidate = (_UI_DIR / rel_path).resolve()
    if _UI_DIR.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)

    # Next.js static export emits a foo.html for the /foo route.
    if rel_path:
        html_candidate = (_UI_DIR / f"{rel_path}.html").resolve()
        if _UI_DIR.resolve() in html_candidate.parents and html_candidate.is_file():
            return FileResponse(html_candidate)

    # Per-bot routes — `/bots/<slug>/<tab>` — are statically exported
    # under a placeholder slug `_` (Next.js requires `generateStaticParams`
    # under `output: "export"`; runtime slugs aren't known at build
    # time). Substitute `_` for the actual slug and serve that scaffold;
    # the client-side router reads the real slug from `useParams`.
    parts = rel_path.split("/") if rel_path else []
    if len(parts) >= 3 and parts[0] == "bots":
        placeholder = ["bots", "_", *parts[2:]]
        scaffold = (_UI_DIR / f"{'/'.join(placeholder)}.html").resolve()
        if _UI_DIR.resolve() in scaffold.parents and scaffold.is_file():
            return FileResponse(scaffold)

    # SPA fallback.
    index = _UI_DIR / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(status_code=404, content={"message": "Not found"})


@app.get("/")
async def ui_root():
    return _serve_ui_path("")


@app.get("/{full_path:path}")
async def ui_catchall(full_path: str):
    return _serve_ui_path(full_path)
