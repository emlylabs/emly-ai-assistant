# Emly AI Agents

A FastAPI service that hosts configurable, multi-tenant chatbots. Each bot has its own LangGraph workflow, retrieval-augmented generation over a Qdrant slice, an admin UI for content management, and pluggable channel adapters.

## Overview

- **Multi-bot runtime.** Bots are first-class DB entities. Per-bot configuration, encrypted LLM API keys, members, files, and conversations all live under a `bots(id, slug, …)` row. The same process serves every bot; tenancy is enforced in code by a payload-indexed `bot_id` filter on every Qdrant call.
- **Admin UI.** A static-exported Next.js app mounted at `/`. CRUD for bots, members, config, files, conversations; per-bot RAG search inspector; backend-driven website crawler with document-type tagging.
- **Embeddable widget.** A standalone UMD bundle (`emly-widget.js`) that customer sites drop in via a `<script>` tag. Talks to bot-scoped widget endpoints (`/widget/{slug}/*`).
- **LangGraph workflow.** Topic routing + slot filling, configurable via the per-bot `config_json` blob. RAG and prompt sources are resolved at request time so config edits take effect on the next message.
- **OIDC admin auth.** Embedded OIDC issuer in-process by default; flip one env var to delegate to Keycloak / Okta / Auth0 / Entra. Cookie sessions, not localStorage JWTs.

The full architecture lives in [docs/multi-bot.md](docs/multi-bot.md); the admin API contract in [docs/api-multibot.md](docs/api-multibot.md).

## Repository layout

| Path | What it is |
| --- | --- |
| `main.py`, `routes/`, `services/`, `agents/`, `db/`, `models/`, `migrations/` | The FastAPI **backend** — request handlers, the LangGraph workflow, RAG manager, Peewee ORM + auto-applied migrations. |
| `ui/` | The **admin UI** — Next.js app, statically exported into `ui/out/` and served by FastAPI's catch-all at `/`. |
| `widget/` | The **embeddable widget** — webpack-built UMD bundle (`widget/dist/emly-widget.js`) that's copied into `ui/public/emly-widget.js` so the static export ships it. |
| `channels/` | Channel adapter scaffolding (Slack, Google Chat, …). |
| `docs/` | Architecture and API docs. |
| `docker-compose.yml`, `Dockerfile`, `dockerize` | Container-first deployment. The Dockerfile is a 3-stage build: `widget-builder` → `ui-builder` → Python `runtime`. |
| `.env.sample` | Documented template — copy to `.env` for local runs. |

## Prerequisites

- **Python 3.13** (matches `pyproject.toml`'s `requires-python` and the `python:3.13-slim` runtime in the Dockerfile).
- **`uv`** for Python dependency management. Install: <https://docs.astral.sh/uv/>.
- **Node.js 22+** to develop or rebuild the admin UI / widget locally. Not needed if you only run the prebuilt Docker image.
- **Docker + Docker Compose** if you want the all-in-one stack (recommended for first-time setup).

## Quick start — Docker Compose

The recommended path. Brings up the FastAPI app, Postgres, and Qdrant in server mode — same shape as production, with bind-mounted persistence so you can poke at uploads, the embedding cache, and the database directly on the host.

```bash
# 1. Seed the env file. Open .env afterwards and fill in any CHANGE_ME values
#    (at minimum ADMIN_EMAIL, ADMIN_PASSWORD, and an LLM key for whichever
#    provider you'll point bots at).
cp .env.sample .env

# 2. Boot the stack. First run does the full image build (slow — pulls Node,
#    Python, pre-fetches the embedding model). Subsequent runs reuse the
#    layered cache, so edit-rebuild iteration is fast.
docker compose up --build
```

The app comes up on <http://localhost:8080>. Sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`; the seeder creates that admin on first boot if `admin_user` is empty.

The compose stack:

| Service | Image | Host port | Persistence |
| --- | --- | --- | --- |
| `app` | built from this repo's `Dockerfile` | `8080` | `${DATA_DIR_HOST:-./data}` → `/app/data` (bind) |
| `postgres` | `postgres:16-alpine` | not exposed | `${POSTGRES_DATA_HOST:-./data/postgres}` → `/var/lib/postgresql/data` (bind) |
| `qdrant` | `qdrant/qdrant:v1.12.4` | `6333` HTTP, `6334` gRPC | `${QDRANT_DATA_HOST:-./data/qdrant}` → `/qdrant/storage` (bind) |

Compose overrides `DATABASE_URL` and `QDRANT_URL` at the app service level so they always point at the colocated containers, regardless of what's in `.env`. That way the same `.env` works for both compose and bare `docker run` flows.

Common follow-up commands:

```bash
docker compose up --build app        # rebuild only the app image (fast — picks up source changes)
docker compose logs -f app           # tail app logs
docker compose exec app bash         # shell into the running app container
docker compose down                  # stop everything; bind-mounted data survives
```

`docker compose down` does **not** delete the host data dirs — that's deliberate. To wipe app/db/vector data, `rm -rf ./data` (or whichever paths you pointed `DATA_DIR_HOST` / `POSTGRES_DATA_HOST` / `QDRANT_DATA_HOST` at) yourself.

### Persistent data

Everything that needs to survive container restarts lives on the host filesystem under `./data` (or wherever you pointed `DATA_DIR_HOST`). What's inside after a fresh boot:

- `bots/{bot_id}/uploads/{file_id}/...` — original files for each bot.
- `models/embedding/` — HuggingFace sentence-transformer cache. Pre-warmed by the Dockerfile so cold starts are fast.
- `models/re_ranking/` — CrossEncoder cache (only if `ENABLE_RAG_HYBRID_SEARCH=true`).
- `qdrant_db/` — only if `QDRANT_URL` is unset (embedded mode). With the compose stack you'll never see this dir; Qdrant runs in a sibling container and stores its data under `./data/qdrant/`.
- `auth_keys/` — RSA keypair for the embedded OIDC issuer (when `AUTH_LOCAL_ISSUER_ENABLED=true`).
- `.admin_jwt_secret` — JWT signing key, auto-generated on first boot.
- `.bot_secrets_key` — Fernet key for per-bot LLM API key encryption.
- `emlygenai_app.db` — SQLite file, only when `DATABASE_URL` points at sqlite.
- `postgres/`, `qdrant/` — sibling bind mounts for the Postgres and Qdrant containers (when running compose).

**Linux only — file ownership.** The app container runs as a non-root user (`emly`, uid 10001). When Docker creates a bind-mount target as root on first run, the container can't write to it. Pre-create the directories before the first `docker compose up`:

```bash
mkdir -p ./data ./data/postgres ./data/qdrant && sudo chown -R 10001:10001 ./data
```

This isn't needed on macOS or Windows Docker Desktop — those handle UID mapping transparently.

## Local development (without Docker)

Useful when you want hot reload on the backend or UI. The full chain is **widget → admin UI → backend** because the static UI export bundles the widget bundle into `ui/public/`.

You'll still want Postgres and Qdrant running somewhere — either the compose stack (just `docker compose up postgres qdrant`) or your own. Without `QDRANT_URL`, the backend falls back to embedded Qdrant under `${DATA_DIR}/qdrant_db`, which is single-worker only.

```bash
# 1. Python deps.
uv sync

# 2. Build the widget bundle. Output: widget/dist/emly-widget.js,
#    auto-copied into ui/public/emly-widget.js so the Next.js export
#    ships the matching code.
cd widget && npm install && npm run build-widget && cp dist/emly-widget.js ../ui/public/emly-widget.js && cd ..

# 3. Build the admin UI once so the FastAPI catch-all has something to serve.
cd ui && npm install && npm run build && cd ..

# 4. Boot the API.
uvicorn main:app --reload --host 0.0.0.0 --port 8080
# or:
bash start.sh        # honors HOST/PORT env, defaults 0.0.0.0:8080
```

The `bots` table and other schema land automatically on first import of `db/db.py` via `peewee_migrate`. There is no separate migrate command — starting the app migrates.

### Backend (FastAPI)

```bash
uv sync
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

- Routers are mounted in [main.py](main.py); see the [API surface](#api-surface) table below.
- `import config` has real side effects: it constructs the embedding model at import time. Don't import it from anything that needs to stay cheap (e.g. ad-hoc scripts) without thinking through the cold-start cost.
- Migrations live in `migrations/` and run automatically on import of `db/db.py`. Drop a numbered `NNN_*.py` migration into `migrations/` — the next boot picks it up.
- There are no test suites or linters wired up in this repo.

### Admin UI (`ui/`)

A Next.js 16 app served two ways: as a static export at `/` in production, or as a hot-reloading dev server during UI work.

```bash
cd ui
npm install
npm run dev     # http://localhost:3000, proxied calls go to the backend at :8080
# or
npm run build   # outputs static HTML/JS into ui/out/, which FastAPI serves
```

Browser calls go to `/api/admin/*` on the same origin. For the integrated experience, run the backend on `:8080` and visit it directly (FastAPI's catch-all serves `ui/out/`). For pure SPA dev with hot reload, run `npm run dev` on `:3000` while the backend runs on `:8080`; CORS is wide open so cross-origin calls work.

Routes:

- `/` — admin home (redirects to login when signed out).
- `/login` — OIDC login (kicks off `/api/admin/auth/login`).
- `/admins` — list admins, send invites, revoke pending.
- `/bots/{slug}/...` — per-bot config, files, members, RAG inspector, conversations, crawl jobs.

Auth uses an httpOnly cookie session (`emly_admin_session` by default), not a `localStorage` token. See [Authentication](#authentication).

### Embeddable widget (`widget/`)

The widget is a self-contained UMD bundle that customer sites drop in via a `<script>` tag. Inside this repo it's also bundled into the admin UI (so the backend serves a copy at `/emly-widget.js`).

```bash
cd widget
npm install
npm run dev          # http://localhost:5173 — Vite dev mode for component work
npm run build-widget # webpack production build → widget/dist/emly-widget.js
```

After building, the bundle needs to land in `ui/public/emly-widget.js` so the static export picks it up. The Docker build does this automatically; locally:

```bash
cp widget/dist/emly-widget.js ui/public/emly-widget.js
```

Configure the widget at build time via `widget/.env.local`:

```env
VITE_BACKEND_BASE_URL=http://localhost:8080
VITE_BOT_ID=support-faq
VITE_USER_ID_EXPIRY_DAYS=5
```

Either env var can be overridden at runtime by passing `baseUrl` / `botId` to `ChatbotWidget.initialize()`. The widget calls `${baseUrl}/widget/${botId}/chat`. See [widget/EMBED.md](widget/EMBED.md) for the embed snippet, theming, and the public API.

## Configuration

Two layers, in this order of precedence:

1. **Environment variables** — process-level knobs read at import in `config.py`.
2. **Per-bot `config_json`** — written by the admin UI through `/api/admin/bots/{slug}/config`. Holds topics, prompts, slot definitions, RAG thresholds, file-upload limits, and trigger forms. Versioned with optimistic concurrency.

### Environment variables you actually need

| Var | Purpose |
| --- | --- |
| `MODE` | `dev` / `prod`. Affects logging defaults. |
| `DATA_DIR` | Base dir for SQLite, embedded Qdrant, file uploads, embedding-model cache. |
| `DATA_DIR_HOST`, `POSTGRES_DATA_HOST`, `QDRANT_DATA_HOST` | Host paths bind-mounted into the app, postgres, and qdrant containers. |
| `DATABASE_URL` | PostgreSQL or SQLite. Compose overrides this to point at the colocated `postgres` service. |
| `MODEL`, `MAX_TOKENS`, `TEMPERATURE` | Deployment-level LLM fallbacks for bots whose `config_json` doesn't override them. The per-bot provider/model/key normally come from `bots.config_json` + `bots.api_key_encrypted`. |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | Used when `EMBEDDING_PROVIDER=openai`, and as a deployment-wide fallback for bots without a per-bot key. |
| `RAG_EMBEDDING_MODEL`, `EMBEDDING_PROVIDER` | Embedding backend. Default: `Alibaba-NLP/gte-base-en-v1.5` (HuggingFace). |
| `RAG_TOP_K`, `CHUNK_SIZE`, `CHUNK_OVERLAP` | RAG retrieval / chunking knobs. |
| `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION` | Optional. Embedded mode under `{DATA_DIR}/qdrant_db` if `QDRANT_URL` is unset. Default collection: `bots`. |
| `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` | Email of the first admin seeded on an empty `admin_user` table. Required to bootstrap the embedded issuer. |
| `AUTH_LOCAL_BOOTSTRAP_PASSWORD` | Initial password for the bootstrap admin. Without it a random password is generated and printed to logs once. |
| `AUTH_LOCAL_ISSUER_ENABLED` | `true` (default) runs the embedded OIDC issuer in-process. `false` delegates to the external IdP at `AUTH_OIDC_ISSUER`. |
| `AUTH_OIDC_ISSUER`, `AUTH_OIDC_AUDIENCE`, `AUTH_OIDC_CLIENT_ID`, `AUTH_OIDC_CLIENT_SECRET` | OIDC client config. Required when delegating to an external IdP — see [Authentication](#authentication). |
| `APP_BASE_URL` | Public-facing URL the app advertises (cookie domain, OIDC redirect URI). Falls back to the incoming request when unset. |
| `BOT_SECRETS_KEY` | Fernet key used to encrypt per-bot LLM API keys at rest. |
| `PUBLIC_BASE_URL` | URL embedded in admin invite emails and channel webhooks. |
| `EMAIL_SERVER`, `EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD`, `EMAIL_FROM` | SMTP for admin invites. |
| `ENABLE_RAG_HYBRID_SEARCH` | `true` enables `CrossEncoder` re-ranking on retrieved chunks. |

The full list (and inline docs) is in [.env.sample](.env.sample) and [config.py](config.py).

## API surface

Routers are mounted in [main.py](main.py):

| Prefix | Purpose |
| --- | --- |
| `/emly/api/chat` | Streaming chat endpoint. Requires `X-Emly-UserID` / `X-Emly-SessionID` / `X-Emly-PageID` headers. |
| `/emly/api/*` | End-user actions, OTP, file upload-by-end-user, scheduler integrations. |
| `/widget/{slug}/*` | Path-scoped widget endpoints — chat + public config for the embeddable widget. |
| `/api/admin/auth/*` | OIDC login, callback, `me`, logout. |
| `/api/admin/admins/*` | Cross-bot admin user management + invite flow. |
| `/api/admin/bots/*` | Per-bot CRUD: config, members, files, dashboard, RAG search, crawl jobs. |
| `/api/v1/*` | Legacy admin routes still used by some imports / metrics endpoints. |
| `/api/livez`, `/api/readyz` | Kubernetes liveness / readiness probes. |
| `/.well-known/openid-configuration`, `/.well-known/jwks.json`, `/api/auth/local/*` | Embedded OIDC issuer (when enabled). |
| `/` | SPA catch-all that serves `ui/out/<path>.html`, falling back to `index.html`. |

`CustomMiddleware` (in `main.py`) injects user/session/page headers into chat request bodies and rejects chat calls missing them. CORS is wide open (`allow_origins=["*"]`) but `allow_credentials=False` — the auth model is header- and bearer-token-based, not cookie-based.

## Authentication

Admin sign-in runs an OIDC authorization-code flow with PKCE. Out of the box the app
also **hosts** the issuer in-process — no external IdP required. When you want SSO,
flip one env var and point at any OIDC-compliant provider (Keycloak, Okta, Auth0,
Microsoft Entra ID, Cognito, …). Sessions are httpOnly cookies, not JWTs in
`localStorage`.

The provider-agnostic plumbing lives in `services/auth/oidc.py`; the embedded issuer
in `routes/auth_issuer.py` and `services/auth/issuer/`. Per-bot membership and roles
(`owner` / `admin` / `viewer`) are layered on top in `admin_bot_memberships`, with a
"must keep ≥1 owner" guard on owner removal.

### Default — embedded issuer

Boots zero-config; only the bootstrap admin is required:

```bash
AUTH_BOOTSTRAP_SUPERADMIN_EMAIL=admin@example.com
# Optional. Without this a random password is printed to logs once.
AUTH_LOCAL_BOOTSTRAP_PASSWORD=correct-horse-battery-staple
```

On first boot the app:

- Generates a 4096-bit RSA keypair under `{DATA_DIR}/auth_keys/` (override with `AUTH_LOCAL_KEYS_DIR`). The key is loaded into the JWKS exposed at `/.well-known/jwks.json`.
- Seeds a `pending_admins` row + a `local_credentials` entry for the bootstrap email.
- Mounts discovery + token endpoints under `/.well-known/openid-configuration` and `/api/auth/local/*`.

A user hits `/login`, the browser is redirected to `/api/admin/auth/login`, the issuer
renders a password form, and on success the cookie session is set.

Login attempts are rate-limited (`AUTH_RATE_LOCAL_AUTHORIZE`, default `5/minute` per
IP) and credentials lock out after `AUTH_LOCAL_LOCKOUT_THRESHOLD` failures (default
5) for `AUTH_LOCAL_LOCKOUT_DURATION_SECONDS` (default 15 min).

### Switching to an external IdP

Disable the embedded issuer and supply OIDC client config:

```bash
AUTH_LOCAL_ISSUER_ENABLED=false
AUTH_OIDC_ISSUER=https://your-idp.example.com/...
AUTH_OIDC_AUDIENCE=...
AUTH_OIDC_CLIENT_ID=...
AUTH_OIDC_CLIENT_SECRET=...           # omit for public PKCE-only clients
AUTH_OIDC_SCOPES="openid email profile"
APP_BASE_URL=https://emly.example.com  # used to build the redirect URI
```

The OIDC redirect URI to register with your IdP is always
`${APP_BASE_URL}/api/admin/auth/callback`. If the app is reachable on multiple
hostnames, set `AUTH_OIDC_ALLOWED_REDIRECT_URIS` to a comma-separated list.

The cookie session holds the **access token** issued by the IdP and verifies it on
every authenticated request. The IdP must therefore issue **JWT** access tokens
whose `aud` matches `AUTH_OIDC_AUDIENCE`. The notes per provider below cover this.

Pre-stage admins before they sign in for the first time — without a row in
`pending_admins` (or an existing `admin_user`), the IdP callback redirects them
to `/request-access`:

```bash
curl -X POST https://emly.example.com/api/admin/admins/pending \
  -H "Cookie: emly_admin_session=<superadmin-cookie>" \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com"}'
```

The first time `alice@example.com` signs in via the IdP, the pending row is
consumed and an `admin_user` row is created keyed on `(issuer, subject)`. To
relink an existing email to a new issuer (e.g. provider migration) set
`AUTH_ALLOW_EMAIL_RELINK=true` for a one-shot window.

### Provider examples

App-side env is identical apart from values. The IdP-side setup is what differs.

#### Keycloak

```bash
AUTH_LOCAL_ISSUER_ENABLED=false
AUTH_OIDC_ISSUER=https://keycloak.example.com/realms/emly
AUTH_OIDC_AUDIENCE=emly-admin-api
AUTH_OIDC_CLIENT_ID=emly-admin
AUTH_OIDC_CLIENT_SECRET=<keycloak-client-secret>
APP_BASE_URL=https://emly.example.com
```

In the realm:

1. Clients → Create client → `emly-admin`, type **OpenID Connect**, "Client authentication" **on** (= confidential).
2. Valid Redirect URIs: `https://emly.example.com/api/admin/auth/callback`. Web Origins: `https://emly.example.com`.
3. Client → Client scopes → `<client>-dedicated` → Add mapper → **Audience**. Included Custom Audience: `emly-admin-api`. Token Claim Name: `aud`. Without this, Keycloak only puts the client_id in `azp` and the audience check fails.
4. Copy the client secret from the Credentials tab.

If you can't add the mapper for some reason, set
`AUTH_OIDC_AUDIENCE_FALLBACK_TO_CLIENT_ID=true` and use `AUTH_OIDC_AUDIENCE=emly-admin`.

#### Okta

```bash
AUTH_LOCAL_ISSUER_ENABLED=false
AUTH_OIDC_ISSUER=https://your-tenant.okta.com/oauth2/default
AUTH_OIDC_AUDIENCE=api://default            # the auth server's audience, NOT the client_id
AUTH_OIDC_CLIENT_ID=0oa...
AUTH_OIDC_CLIENT_SECRET=<okta-client-secret>
APP_BASE_URL=https://emly.example.com
```

In the Okta admin console:

1. Applications → Create App Integration → **OIDC – Web Application**.
2. Sign-in redirect URI: `https://emly.example.com/api/admin/auth/callback`. Sign-out redirect URI (optional): `https://emly.example.com/`.
3. Use the default authorization server (`oauth2/default`) or create a custom one. Set `AUTH_OIDC_ISSUER` to its issuer URI and `AUTH_OIDC_AUDIENCE` to its audience (Security → API → Authorization Servers; the audience is `api://default` for the default server).
4. Assign the application to the users / groups who should be able to sign in.

#### Microsoft Entra ID (Azure AD)

Entra access tokens default to `aud=Microsoft Graph`. To get a JWT scoped to your
own app, you must expose an Application ID URI and request its `.default` scope.

```bash
AUTH_LOCAL_ISSUER_ENABLED=false
AUTH_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
AUTH_OIDC_AUDIENCE=api://emly-admin-api
AUTH_OIDC_CLIENT_ID=<application-client-id>
AUTH_OIDC_CLIENT_SECRET=<entra-client-secret>
AUTH_OIDC_SCOPES="openid email profile api://emly-admin-api/.default"
APP_BASE_URL=https://emly.example.com
```

In the Azure portal (App registrations → your app):

1. Authentication → Add platform "Web" → redirect URI `https://emly.example.com/api/admin/auth/callback`. Enable "ID tokens" under "Implicit grant".
2. Expose an API → set Application ID URI to `api://emly-admin-api` (or any URI you prefer; mirror it in `AUTH_OIDC_AUDIENCE`). Add a scope (e.g. `access_as_user`) and authorise the app itself as a client.
3. API permissions → Add a permission → My APIs → your app → `access_as_user` → Grant admin consent.
4. Certificates & secrets → New client secret. Copy the value.

For single-tenant deployments, `<tenant-id>` is the tenant GUID. For multi-tenant
use `common` or `organizations` — but note that audience verification still needs
`api://...` to be granted to the principal in their home tenant.

#### Google

Heads-up: **Google issues opaque access tokens, not JWTs.** This app stores the
access token in the session cookie and verifies it as a JWT on every request, so a
plain Google OIDC integration will fail at the first authenticated call after
sign-in completes. Use Google only after switching the cookie storage path to
hold the `id_token` (or pairing it with a server-side session table) — not yet
implemented in this codebase. The discovery doc and PKCE flow themselves work
fine; the breakage is on token verification.

For reference, the values you'd set are:

```bash
AUTH_LOCAL_ISSUER_ENABLED=false
AUTH_OIDC_ISSUER=https://accounts.google.com
AUTH_OIDC_AUDIENCE=<client-id>.apps.googleusercontent.com
AUTH_OIDC_AUDIENCE_FALLBACK_TO_CLIENT_ID=true
AUTH_OIDC_CLIENT_ID=<client-id>.apps.googleusercontent.com
AUTH_OIDC_CLIENT_SECRET=<google-client-secret>
APP_BASE_URL=https://emly.example.com
```

In the Google Cloud Console: APIs & Services → Credentials → Create OAuth client
ID → **Web application** → Authorised redirect URI
`https://emly.example.com/api/admin/auth/callback`.

### Cookies, CSRF, rate limits

| Var | Default | Purpose |
| --- | --- | --- |
| `AUTH_COOKIE_NAME` | `emly_admin_session` | Cookie holding the access token. |
| `AUTH_COOKIE_SECURE` | inferred from `APP_BASE_URL` (HTTPS → `true`) | Force the `Secure` flag on/off. |
| `AUTH_COOKIE_SAMESITE` | `lax` | `lax` is required for the IdP's 302 → callback to carry the cookie. `strict` will silently bounce users back to login. |
| `AUTH_COOKIE_DOMAIN` | host-only | Set when the UI is served from a subdomain. |
| `AUTH_CSRF_TRUSTED_ORIGINS` | derived from `APP_BASE_URL` | Comma-separated origins permitted on cookie-authenticated mutating requests. |
| `AUTH_OIDC_ALLOWED_REDIRECT_URIS` | derived from request | Override when the app is reachable on multiple hostnames. |
| `AUTH_OIDC_JWKS_CACHE_TTL` | `3600` | Seconds between JWKS refetches. |
| `AUTH_OIDC_LEEWAY_SECONDS` | `30` | Clock-skew leeway on `exp` / `nbf`. |
| `AUTH_RATE_LOGIN`, `AUTH_RATE_CALLBACK`, `AUTH_RATE_LOCAL_AUTHORIZE`, `AUTH_RATE_LOCAL_TOKEN`, `AUTH_RATE_WIDGET_INIT` | `10/min`, `20/min`, `5/min`, `10/min`, `30/min` | Per-IP slowapi limits on the auth endpoints. |

### See also

The full architecture, security model, and migration plan live in [docs/auth.md](docs/auth.md).

## Persistence

- **Relational.** Peewee + `peewee_migrate`. Migrations under `migrations/` are applied on import of `db/db.py`. Sqlite under `DATA_DIR` if `DATABASE_URL` is unset; PostgreSQL otherwise.
- **Vector.** Qdrant. Embedded under `{DATA_DIR}/qdrant_db` for local work, or a server cluster via `QDRANT_URL` / `QDRANT_API_KEY`. One shared collection (default name `bots`) with payload indexes on `bot_id` and `file_id`. **Every Qdrant call must go through `agents/rag_manager.py`** — that module is the multi-tenant boundary. Importing `qdrant_client` anywhere else is a security regression.
- **Embedding cache.** HuggingFace sentence-transformers cached under `SENTENCE_TRANSFORMERS_HOME`. The Docker image pre-downloads the default model so cold starts are fast.

## Files and ingestion

Per-bot file uploads land at `POST /api/admin/bots/{slug}/files` and are stored under `{DATA_DIR}/bots/{bot_id}/uploads/{file_id}/`. The `services/data_service.py` pipeline extracts text (PDF / DOCX / TXT / HTML / MD / CSV), chunks it with `RecursiveCharacterTextSplitter`, and upserts via `RAGManager.upsert(bot_id, file_id, chunks)`. Each chunk's metadata carries the file's `document_type` (one of `web_page` / `document` / `product` / `support_article` / `faq` / `other`) so the chat surface can label citations.

A backend-driven website crawler creates a `crawl_jobs` row from `POST /api/admin/bots/{slug}/crawl/jobs`. A supervisor task started at FastAPI startup picks up running jobs, walks links with same-host / path-prefix / regex / robots / dedupe filters, and feeds discovered pages into the same upload+embed pipeline. Jobs are recoverable across pod restarts; pages stuck mid-flight on boot are reset to `queued`.

## Docker (manual)

The `Dockerfile` is a 3-stage build, layered for fast incremental rebuilds:

1. `node:22-slim` (`widget-builder`) — webpack-builds the UMD widget bundle (`widget/dist/emly-widget.js`).
2. `node:22-slim` (`ui-builder`) — runs `npm ci` + `next build` for the admin UI. The widget bundle from stage 1 is copied into `ui/public/emly-widget.js` *before* the build so the static export ships the matching widget code.
3. `python:3.13-slim` (`runtime`) — installs system packages (apt cache mounted), pulls Python deps with `uv` (uv cache mounted), pre-fetches the embedding model, then copies app source last. The static UI from stage 2 lands at `/app/ui/out`.

Build:

```bash
docker build -t emly-ai-assistant:dev .

docker run --rm -p 8080:8080 --env-file .env emly-ai-assistant:dev
```

The image runs as a non-root user (`emly`, uid 10001), exposes `:8080`, and ships a `HEALTHCHECK` against `/api/livez`. The supplied `dockerize` script + `docker-env.sh` wrap this for the deploy environments (`-e dev|stage|preprod|prod`, optional `-p` to push):

```bash
source docker-env.sh
./dockerize -e dev          # build only
./dockerize -e dev -p yes   # build + push
```

## Operational notes

- `import config` has real side effects: it constructs the embedding model at import time. Don't import it from anything that needs to stay cheap (e.g. ad-hoc scripts) without thinking through the cold-start cost. The Qdrant client is built lazily in `agents.rag_manager.get_rag_manager()`, so it's safe to import that one without forcing a connection.
- **Single-replica until S3 + Redis ship.** File uploads land on the pod's local `DATA_DIR`; rate-limit and per-session lock state live in process. Booting with `WEB_CONCURRENCY > 1` and embedded Qdrant is a hard error (see `_assert_runtime_topology`). Horizontal scale requires the Future-Items work in `docs/multi-bot.md`.
- **Liveness vs readiness.** `/api/livez` is always 200 once Uvicorn is up. `/api/readyz` gates on the embedding model, Qdrant, and DB readiness — that's what k8s should poll, with `initialDelaySeconds: 30+` to absorb embedding cold-start.
