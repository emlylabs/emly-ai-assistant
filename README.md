# Emly AI Assistant

An open-source, multi-tenant AI chatbot platform. Build, configure, and deploy intelligent chatbots with RAG (Retrieval-Augmented Generation), an admin dashboard, and an embeddable widget — all from a single FastAPI service.

## What's Inside

| Component | What it does |
|-----------|-------------|
| **Backend** (`main.py`, `routes/`, `services/`, `agents/`, `db/`) | FastAPI server with LangGraph-powered chat workflows, RAG search, and auto-applied database migrations. |
| **Admin UI** (`ui/`) | Next.js dashboard to manage bots, upload files, configure prompts, view conversations, and inspect RAG results. |
| **Embeddable Widget** (`widget/`) | A drop-in `<script>` snippet that adds a chat widget to any website. |
| **Channel Adapters** (`channels/`) | Integrations for Slack, Google Chat, and more. |

---

## Prerequisites

| Tool | Version | Why you need it | Install link |
|------|---------|-----------------|--------------|
| **Python** | 3.13+ | Runs the backend | [python.org](https://www.python.org/downloads/) |
| **uv** | latest | Python package & environment manager (fast replacement for pip + venv) | [docs.astral.sh/uv](https://docs.astral.sh/uv/) |
| **Node.js** | 22+ | Builds the admin UI and widget (not needed if using Docker only) | [nodejs.org](https://nodejs.org/) |
| **Docker + Docker Compose** | latest | Runs the full stack (recommended for first-timers) | [docs.docker.com](https://docs.docker.com/get-docker/) |

---

## Quick Start with Docker (Recommended)

This is the easiest way to get everything running — backend, database, and vector store — in one command.

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/emly-ai-assistant.git
cd emly-ai-assistant

# 2. Create your environment file
cp .env.sample .env

# 3. Open .env and fill in at minimum:
#    - AUTH_BOOTSTRAP_SUPERADMIN_EMAIL  (your admin login email)
#    - AUTH_LOCAL_BOOTSTRAP_PASSWORD     (your admin login password)
#    - POSTGRES_PASSWORD  (required; e.g. `openssl rand -hex 24`)
#
#    The LLM (model, endpoint, API key) is chosen per bot in the setup
#    wizard. To give every bot a default instead, also set MODEL,
#    OPENAI_BASE_URL and OPENAI_API_KEY.
#
#    Leave DATABASE_URL and QDRANT_URL as-is — Docker Compose
#    overrides them to point at the containers automatically.

# 4. (Linux only) Fix file permissions for the container user
mkdir -p ./data ./data/postgres ./data/qdrant
sudo chown -R 10001:10001 ./data

# 5. Start the stack
docker compose up --build
```

Open **http://localhost:8080** in your browser. Sign in with the `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` and `AUTH_LOCAL_BOOTSTRAP_PASSWORD` you set in `.env`. On first sign-in you'll be asked to choose a new password.

### What's Running

| Service | Port | Description |
|---------|------|-------------|
| **app** | 8080 | FastAPI backend + Admin UI + Widget |
| **postgres** | (internal) | PostgreSQL database |
| **qdrant** | (internal) | Vector database for RAG. Not published on the host; see the commented `ports:` in `docker-compose.yml` to expose it on loopback for debugging. Telemetry is disabled |

### Useful Docker Commands

```bash
# View logs
docker compose logs -f app

# Rebuild after code changes
docker compose up --build app

# Open a shell inside the app container
docker compose exec app bash

# Stop everything (data is preserved)
docker compose down

# Stop and delete all data
docker compose down -v
rm -rf ./data
```

### Kubernetes

Kustomize manifests are in [`deploy/k8s`](deploy/k8s):

| Path | What it is |
|---|---|
| `base/` | The app (one replica, `Recreate` upgrades), its data volume, Qdrant (a StatefulSet, telemetry off, reachable only from the app), Services, a TLS Ingress and NetworkPolicies that deny everything not listed |
| `components/postgres/` | Optional Postgres StatefulSet, for when you don't use a managed database |
| `overlays/example/` | What you copy and edit: namespace (with the `restricted` Pod Security level), image, host name, proxy range and the external addresses the app may reach |
| `overlays/in-cluster-postgres/` | The example plus the Postgres component |

```bash
kubectl create namespace emly
kubectl -n emly create secret generic emly-secrets \
  --from-literal=BOT_SECRETS_KEY='<Fernet key>' \
  --from-literal=DATABASE_URL='postgresql://emly:<password>@<host>:5432/emly?sslmode=verify-full' \
  --from-literal=OPENAI_API_KEY='<key>'
kubectl -n emly create secret tls emly-tls --cert=tls.crt --key=tls.key
kubectl apply -k deploy/k8s/overlays/<your-overlay>
```

- **One replica only.** Sign-in state, rate limits and the scheduler live in the app's memory and on its `ReadWriteOnce` data volume, so the Deployment runs one pod and replaces it on upgrade (`strategy: Recreate`): expect a short outage while the new pod loads its models (the readiness probe on `/api/readyz` holds traffic until then).
- **Secrets** are mounted from the `emly-secrets` Secret as files and read through `NAME_FILE` ([Secrets from files](#secrets-from-files)); none are in the manifests or the pod's environment. Add a `NAME_FILE` entry in your overlay for each optional secret you set.
- **Network:** everything is denied by default. The app may reach DNS and Qdrant; Qdrant only accepts the app and reaches nothing. Your overlay lists, by address, the model endpoint, database, mail server and anything else in [Outbound connections](#outbound-connections) (`overlays/example/egress-external.yaml`), and names the ingress controller's namespace (`ingress-nginx` in the base).
- **Pods** run as non-root with no privilege escalation, all capabilities dropped and the default seccomp profile. The app's root filesystem is still writable: a read-only one hasn't been tested with the model libraries yet.
- CI renders every overlay and validates it with kubeconform.
- **Not tested on a cluster yet** (no `kind` run in CI). Treat the first deployment as a trial on a test namespace.

---

## Local Development (Without Docker)

Use this when you want hot-reload on the backend or UI during development.

---

## Production hardening

Checklist for a production deployment. Each variable is described in full under [Configuration Reference](#configuration-reference); this section says what to set and why. To report a vulnerability, see [SECURITY.md](SECURITY.md).

**Required**

| Setting | Production value | Why |
|---------|------------------|-----|
| `MODE` | `prod` | Turns on the production defaults below: API docs off, `Secure` cookies, `BOT_SECRETS_KEY` required, built-in sign-in off when an external IdP is set |
| `APP_BASE_URL`, `PUBLIC_BASE_URL` | Your public `https://` URL | Required with `MODE=prod`. An https URL also turns on HSTS and `Secure` cookies, and is the base for links in emails |
| `BOT_SECRETS_KEY` | A Fernet key you generate and back up | Encrypts stored API keys and channel credentials, and signs widget tokens. The app refuses to start in prod without it. Rotate with `BOT_SECRETS_KEY_PREVIOUS` ([how](#rotating-the-secrets-key)). Prefer `BOT_SECRETS_KEY_FILE` and the other `*_FILE` variables ([secrets from files](#secrets-from-files)) |
| `POSTGRES_PASSWORD` | A long random value | Docker Compose refuses to start without it; there is no default password |
| `FORWARDED_ALLOW_IPS` | Only your reverse proxy's address or subnet | Client IPs (rate limits, audit log) are read from proxy headers only when they come from these addresses. Never `*` |

**Sign-in and admin access**

| Setting | Production value | Why |
|---------|------------------|-----|
| `AUTH_OIDC_*` | Your IdP | With an external IdP set, built-in password sign-in is off in prod unless `AUTH_LOCAL_ISSUER_ENABLED=true` |
| `AUTH_LOCAL_ISSUER_ENABLED` | Leave unset (or `false`) when you use an IdP | Avoids a second, password-based way in |
| `AUTH_LOCAL_MIN_PASSWORD_LENGTH` | `12` or more | Applies when built-in sign-in is used; the first sign-in must replace the bootstrap password |
| `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL`, `AUTH_LOCAL_BOOTSTRAP_PASSWORD` | First boot only | A generated password is written to `$DATA_DIR/bootstrap_password.txt` (mode 0600), never to the log, and deleted after first sign-in |
| `AUTH_OIDC_REQUEST_OFFLINE_ACCESS`, `AUTH_OIDC_EMAIL_CLAIMS`, `AUTH_OIDC_TRUST_EMAIL_UNCONDITIONALLY` | Per your IdP (see [SSO](#external-identity-provider-sso)) | Keep `AUTH_OIDC_TRUST_EMAIL_UNCONDITIONALLY` `false`; for Entra ID set `AUTH_OIDC_TRUSTED_EMAIL_CLAIMS=upn` instead |
| `AUTH_OIDC_REQUIRED_AMR` (or `AUTH_OIDC_REQUIRED_ACR`) | The value your IdP sends after MFA, e.g. `mfa` ([check first](#identity-provider-setup)) | Admins can't sign in without a second factor, even if a conditional-access policy is missed. Google: enforce 2-step verification at Google |
| `AUTH_SESSION_MAX_AGE_SECONDS` | `43200` (12 h, the prod default) or your policy | Admin sessions end a fixed time after sign-in instead of refreshing for as long as a tab is open |
| `AUTH_COOKIE_SECURE`, `AUTH_COOKIE_SAMESITE` | Leave unset | Defaults are `Secure` in prod and `SameSite=lax` |
| `AUTH_ALLOW_BEARER` | Leave unset (off) unless a script needs the admin API | Admin APIs then accept only the session cookie |
| `BOT_CREATION` | `writers` (default) or `superadmin` | Who may create bots |
| `ENABLE_API_DOCS` | Leave unset | API docs stay off in prod |
| `ALLOW_LANGSMITH_TRACING` | Leave unset | A `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` left in `.env` stops the app starting, so it can't silently send conversations to LangSmith |

**Widget and visitors**

| Setting | Production value | Why |
|---------|------------------|-----|
| Each bot's *Allowed widget origins* | Your sites, e.g. `https://www.example.com` | New bots allow only this app's own pages. Bots that existed before were migrated to `*` (any site): in production `/api/readyz` lists every bot still at `*`. This stops other websites embedding the widget; it doesn't stop direct requests, so also keep the app reachable only from your network if the assistant is staff-only (see [Who can use the widget](#who-can-use-the-widget)) |
| `WIDGET_TOKEN_TTL_SECONDS`, `WIDGET_TOKEN_RENEWAL_WINDOW_SECONDS`, `WIDGET_TOKEN_ROTATION_GRACE_SECONDS` | Defaults | Widget requests need a short-lived token bound to the bot and visitor |
| `WIDGET_ICON_HOSTS` | Your own image hosts, if any | Stops an icon URL sending every visitor's browser to a third-party host. Unset allows any host |
| `WIDGET_MAX_REQUEST_MB` | `110` (default), or your largest legitimate form upload | Visitors are anonymous; this caps what one request can store before it is checked |
| `AUTH_RATE_*` | Defaults, or tighter | Limits for widget chat, uploads, OTP and sign-in. Counters are per process |
| Each bot's *Data retention* | Your retention period (new bots start at 365 days; older bots may be empty, which keeps everything) | Conversations, submissions and attachments older than this are deleted nightly |
| Each bot's *Alert emails contain* | *Only a link* (the default for new bots) | Keeps transcripts, visitor details and files out of mailboxes. Bots that existed before were migrated to the full alert: review them |
| Workspace *OTP expiry* | 5 minutes (default) | One-time codes are single-use, hashed, and locked after 5 wrong attempts |

**Email, models and data**

| Setting | Production value | Why |
|---------|------------------|-----|
| `EMAIL_SERVER` (+ port, user, password) | Your SMTP relay | Without it, OTP codes, invites and alerts are not sent (logged at boot). TLS certificates are verified |
| `MODEL`, `OPENAI_BASE_URL`, `OPENAI_API_KEY` | Optional deployment defaults | There is no built-in model or endpoint; a bot without them refuses to answer |
| `REQUIRE_PER_BOT_LLM_KEY` | `true` | Each bot must have its own API key, so departments don't share one key's spend and data path. A bot without one refuses to answer and its config page says why |
| `LLM_ENDPOINT_ALLOWLIST` | The model endpoints you have approved | Bot owners can't send conversations anywhere else. Unset allows any endpoint and shows a warning on `/api/readyz` |
| `CRAWL_ALLOWED_PRIVATE_CIDRS` | Only the ranges that hold intranet sites you want indexed | Admins can then crawl those addresses, and only those. Leave unset if you crawl public sites only |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | `1` (set by the image) | The image ships pinned models and never downloads at runtime |
| `SENTENCE_TRANSFORMERS_HOME`, `RE_RANKING_MODEL_HOME` | Leave unset in Docker | The models live in `/opt/models`. An old `.env` pointing these into `/app/data` should be cleaned up |
| `RAG_EMBEDDING_MODEL_REVISION`, `RE_RANKING_MODEL_REVISION`, `RAG_EMBEDDING_TRUST_REMOTE_CODE` | Only when using non-default models | Pin a commit; allow remote code only for a model you have reviewed |
| `AUDIT_RETENTION_DAYS`, `WEBHOOK_DEDUPE_RETENTION_HOURS` | `365` and `24` (defaults), or your policy | Nightly cleanup |
| `AUDIT_FAIL_CLOSED` | Leave unset (on in prod) | Security actions fail with 503 rather than happen unaudited when the audit log can't be written |
| `AUDIT_DATA_ACCESS` | `true` if you must show who read which conversation | Records each admin's reads of conversations and visitor details |
| `AUDIT_LOG_STDOUT` | `true`, and ship container stdout to your SIEM | The audit table can be edited by anyone with database access; the shipped copy can't |
| Backups | Database, search index, `DATA_DIR` and the secrets key | See [Backup and restore](#backup-and-restore) |

**Network and channels**

- **TLS:** the app serves plain HTTP on port 8080, and the compose file publishes it on every interface. In production put a TLS-terminating reverse proxy or load balancer in front (Caddy, nginx, Traefik, or your cloud's), publish only the proxy, and bind the app's port to the proxy's network (e.g. `"127.0.0.1:8080:8080"` when the proxy runs on the host). Set `APP_BASE_URL` and `PUBLIC_BASE_URL` to the `https://` URL, which turns on HSTS and `Secure` cookies, and set `FORWARDED_ALLOW_IPS` to the proxy's address. TLS to Postgres and Qdrant: see below.
- Keep Qdrant and Postgres off the public network. The compose file doesn't publish their ports.
- A Postgres outside the private network must be reached over TLS: add `?sslmode=verify-full&sslrootcert=/path/to/ca.pem` (or at least `sslmode=require`) to `DATABASE_URL`. Options in the URL are passed to the driver unchanged. In production, a remote `DATABASE_URL` without it is flagged at boot and on `/api/readyz`.
- Qdrant holds the text of every indexed passage. Outside compose, run it with `QDRANT__TELEMETRY_DISABLED=true` (compose sets this) and, if it isn't on the same private network, with https and `QDRANT_API_KEY`. A `QDRANT_URL` that isn't a private address or an internal name is flagged at boot and on `/api/readyz`. When `MODE=prod`, such a Qdrant without `QDRANT_API_KEY` stops start-up.
- Check the "Outbound destinations" block in the start-up log against the systems you expect (see [Outbound connections](#outbound-connections)).
- Telegram bots installed before webhook secrets were generated must be re-installed: updates without the secret are now rejected.
- The image runs as a non-root user and handles `SIGTERM` for clean shutdowns.
- The admin UI is served with a Content-Security-Policy that runs only this app's scripts (inline scripts are allowed by hash, never `unsafe-inline`) and sends requests only to this origin. The sign-in and sign-in error pages allow no scripts at all. A reverse proxy that adds its own `Content-Security-Policy` header would combine with these (browsers enforce both), so don't add a looser one.

## Configuration Reference

All configuration is done via environment variables. Copy `.env.sample` to `.env` and edit it.

### Essential Variables

| Variable | What it does | Default / Example |
|----------|-------------|-------------------|
| `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` | Superadmin email (created on first boot) | `admin@example.com` |
| `AUTH_LOCAL_BOOTSTRAP_PASSWORD` | Superadmin password | Set your own |
| `QDRANT_URL` | Vector database URL (leave unset for embedded mode) | `http://localhost:6333` |
| `DATA_DIR` | Where the app stores files, models, and database | `./data` (local) / `/app/data` (Docker) |
| `SENTENCE_TRANSFORMERS_HOME` | Embedding model cache directory. Leave unset in Docker | `./data/models/embedding` (local) / `/opt/models/embedding` (Docker) |

### LLM provider

Each bot has its own model, endpoint and API key, set in the setup wizard or the bot's LLM settings. These variables are optional defaults for bots that leave a value blank. There is no built-in model or endpoint: a bot that still lacks one after these defaults refuses to answer (visitors get a short "not available" reply) and its config page shows what is missing.

| Variable | What it does | Default |
|----------|-------------|---------|
| `MODEL` | Default model name, e.g. `gpt-4o-mini` | unset |
| `OPENAI_BASE_URL` | Default OpenAI-compatible endpoint, including the version path, e.g. `https://api.openai.com/v1` or `https://openrouter.ai/api/v1` | unset |
| `OPENAI_API_KEY` | Default API key (also the embedding key when `EMBEDDING_PROVIDER=openai`) | unset |
| `REQUIRE_PER_BOT_LLM_KEY` | `true`: bots don't fall back to `OPENAI_API_KEY`. A bot without its own key refuses to answer, and its config page says it needs one. `OPENAI_API_KEY` is then used only for remote embeddings | `false` |
| `LLM_ENDPOINT_ALLOWLIST` | Model endpoints any bot may use, comma-separated: base URLs (`https://llm.example.com/v1`), hosts with an optional port (`llm.internal:8443`) or `*.example.com`. A bot whose endpoint isn't listed can't save it and refuses to answer; it never falls back to another endpoint. `OPENAI_BASE_URL` must be listed too | unset (any endpoint) |

### Runtime and Security

| Variable | What it does | Default |
|----------|-------------|---------|
| `MODE` | `dev` for local development. `prod` (or `production`, `preprod`, `stage`, `staging`) switches on production-safe defaults and requires `APP_BASE_URL` | `dev` |
| `ENABLE_API_DOCS` | Serve the interactive API docs at `/docs`, `/redoc` and `/openapi.json` | `true` in dev, `false` when `MODE=prod` |
| `ALLOW_LANGSMITH_TRACING` | Allow LangSmith tracing, which sends every prompt and reply (conversation text included) to LangSmith's cloud. Unless this is `true`, a `LANGSMITH_TRACING` / `LANGCHAIN_TRACING_V2` (or alias) set to `true` stops the app starting when `MODE=prod`; in development it is removed with a warning | `false` |
| `BOT_SECRETS_KEY` | Fernet key that encrypts stored bot API keys and channel credentials. Separate keys derived from it encrypt the admin refresh cookie, sign widget tokens and hash one-time passcodes, so none of these can be used in place of another. **Required when `MODE=prod`**; in development it is generated into `$DATA_DIR/.bot_secrets_key`. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | dev: generated |
| `BOT_SECRETS_KEY_PREVIOUS` | Retired keys (comma-separated) that still decrypt and verify during a rotation. See [Rotating the secrets key](#rotating-the-secrets-key) | — |
| `AUDIT_FAIL_CLOSED` | When an audit row for a sign-in, permission, configuration or other security event can't be written, fail the request with 503 instead of carrying on. Changes made before the audit write may already be saved. Background jobs only log | `true` when `MODE=prod`, else `false` |
| `AUDIT_DATA_ACCESS` | Record a `data.read` audit row when an admin lists or opens conversations, messages or visitor details: who, which bot, which session, message or visitor, the route, and which filters a list used (`search` when the text search was used, never the search text or any content). One row per admin, bot and target per 10 minutes, per process. When on, these rows follow `AUDIT_FAIL_CLOSED` | `false` |
| `AUDIT_LOG_STDOUT` | Also write every audit event as one JSON line on standard output: `{"type":"emly.audit","ts":…,"id":…,"stored":true,"action":…,"success":…,"admin_id":…,"bot_id":…,"target_type":…,"target_id":…,"ip":…,"ua":…,"payload":{…}}`. Collect container output with your log pipeline and keep these lines in your SIEM, where nobody with access to the app's database can change them. The line is written even when the database row couldn't be (`"stored":false`). Same content as the audit table, so it contains admin and invitee email addresses | `false` |
| `AUDIT_RETENTION_DAYS` | Days to keep audit log rows. A daily job (03:30 in the scheduler's time zone, Asia/Kolkata) deletes older rows and records the purge. `0` keeps them forever | `365` |
| `WEBHOOK_DEDUPE_RETENTION_HOURS` | Hours to keep the per-event rows that stop Slack, Teams and Google Chat retries being processed twice. Deleted by the nightly data job (03:45), which also removes OTP rows a day after they expire | `24` |
| `BOT_CREATION` | Who can create (or duplicate) bots in the workspace. `writers`: superadmins, plus anyone who is owner or admin of at least one bot (viewer-only members can't). `any`: every admin. `superadmin`: superadmins only | `writers` |
| `WIDGET_TOKEN_TTL_SECONDS` | Lifetime of the widget token that `/widget/{ref}/init` issues | `86400` (24 h) |
| `WIDGET_MAX_REQUEST_MB` | Largest widget request body (a form submission with all its files), counted as it arrives; bigger requests get 413 before they are stored. Each file is also checked against the bot's *Max file size*. Raise it if a bot's forms take more or larger files | `110` (two 50 MB files plus the form) |
| `WIDGET_ICON_HOSTS` | Hosts the widget's logo, avatar and launcher icons may load from, comma-separated: `cdn.example.com`, `assets.internal:8443` or `*.example.com`. Visitors' browsers fetch these images, which tells the host their IP address and the page they're on. When set, an icon on any other host can't be saved, and one stored earlier isn't served (the widget shows its default). This app's own host, paths on the embedding site (`/logo.png`) and inline `data:image/` icons are always allowed | unset (any host) |
| `WIDGET_TOKEN_ROTATION_GRACE_SECONDS` | After a bot's widget signing secret is rotated, how long tokens signed with the previous secret are still accepted | `86400` (24 h) |
| `WIDGET_TOKEN_RENEWAL_WINDOW_SECONDS` | How long after expiry a widget token still proves its visitor id to `/init`, so a returning visitor keeps their history. A visitor id that already has history is never issued without that proof | `2592000` (30 days) |
| `FORWARDED_ALLOW_IPS` | Proxy addresses (comma-separated IPs or CIDRs) trusted to set `X-Forwarded-For` / `X-Forwarded-Proto`. Client IPs from these headers feed rate limits and the audit log, so never use `*`. Exposed directly (the default compose stack): leave the default. Behind a reverse-proxy container in compose: the proxy container's IP or the compose network's subnet, e.g. `172.18.0.0/16` (see `docker network inspect`). Kubernetes: the range your ingress controller connects from, usually the pod CIDR, e.g. `10.244.0.0/16` | `127.0.0.1` |
| `AUTH_RATE_*` | Per-client rate limits, in [`limits`](https://limits.readthedocs.io/) syntax such as `20/minute`. `WIDGET_INIT` (30/min), `WIDGET_CHAT` (20/min), `WIDGET_ACTION` (20/min) and `WIDGET_UPLOAD` (10/min) are per IP per bot. `OTP_SEND_TARGET` (5 per 10 min) is per bot and email address, `OTP_SEND_IP` (20/hour) and `OTP_VERIFY` (10 per 10 min) per IP per bot. `LOCAL_AUTHORIZE` (5/min) and `LOCAL_TOKEN` (10/min) are per IP. Counters are in memory, per process | see left |
| `CRAWL_ALLOWED_PRIVATE_CIDRS` | Private address ranges admins may crawl, for intranet sites: comma-separated CIDRs such as `10.20.0.0/16`. Every redirect hop is checked against the list. Loopback, link-local (which holds cloud metadata), multicast and known metadata addresses are never crawled, even when a listed range covers them. A malformed value stops start-up | unset (public addresses only) |
| `EMAIL_SERVER` | Your SMTP relay for OTP codes, invites and form alerts. With no value, email is disabled and a line is logged at boot. Port 465 uses implicit TLS and 587 uses STARTTLS, both with certificate verification | unset (email off) |

#### Visitor data: retention and erasure

- **Retention:** each bot's *Config → Limits → Data retention* sets how many days to keep conversations. Every night (03:45) messages, conversations, form submissions and chat attachments older than that are deleted, along with visitor profiles that have nothing left. Empty keeps everything. New bots start at 365 days (unless their workspace template sets a value); bots created before this default, and duplicates of them, keep what they had.
- **Erasure:** a bot owner can delete everything stored about one visitor from the bot's *Users* page (or `DELETE /api/admin/bots/{slug}/end-users/{id}`): profile, conversations, messages, form submissions, attachments (including the files), channel identities and OTP rows. The audit log records who did it and how many rows went, never the content.
- **Deleting a bot** removes its chat attachments, uploaded files and every row tied to it once the 7-day grace period ends. The audit log is kept.

#### Chat-platform users

When someone messages a bot through a chat platform, Emly keeps a profile for them so admins can see who asked. Every member of the bot, viewers included, can see it. Retention and erasure (above) delete it like a website visitor's.

| Platform | What is stored |
|---|---|
| Slack | Name, from `users.info` (scope `users:read`). Email only when the channel's *Store sender email addresses* is on **and** you granted `users:read.email` (the setup guide doesn't ask for it) |
| Microsoft Teams | Display name |
| Google Chat | Display name. Email only when the channel's *Store sender email addresses* is on (Google sends it with every message; it is otherwise used only as a one-way hash to recognise the sender) |
| WhatsApp | Profile name and phone number |
| Telegram | First and last name, or username |

*Store sender email addresses* is per channel (*Channels* page), off by default. Turning it off stops new addresses being stored; addresses stored before are kept until retention or erasure removes them.

Plus a one-way hash of the platform's user id, to recognise the sender next time. A name or email taken from a form the person filled in is never overwritten.

#### Uploaded files

Admin documents and widget form attachments are checked by their bytes, not only by the type the browser declares. Refused with 415:

- programs (Windows, Linux and macOS executables) and files named like one (`.exe`, `.js`, `.bat`, `.ps1`, `.jar`, …);
- archives (zip, gzip, 7z, rar, tar). Word, Excel, PowerPoint and OpenDocument files are zips inside and are accepted, unless they unpack to more than 512 MB;
- a file whose content isn't what its name and declared type say, such as a zip named `notes.txt` or a program named `report.pdf`.

This is a type check, not a malware scan: a PDF with a malicious payload is still a PDF. Scan stored files with your own tooling if you need that (`DATA_DIR/bots/*/uploads/` and `DATA_DIR/attachments/`).

#### Secrets from files

For Docker and Kubernetes secrets, set `NAME_FILE` to a file's path instead of setting `NAME`, and the app reads the value from that file at start-up. The value then isn't in `docker inspect`, the pod spec or the process environment of other tools. A trailing newline is removed. Setting both `NAME` and `NAME_FILE`, or pointing at a missing or empty file, stops start-up. Only the variable names are logged.

This works for `BOT_SECRETS_KEY`, `BOT_SECRETS_KEY_PREVIOUS`, `OPENAI_API_KEY`, `DATABASE_URL`, `QDRANT_API_KEY`, `EMAIL_PASSWORD`, `AUTH_OIDC_CLIENT_SECRET`, `AUTH_LOCAL_BOOTSTRAP_PASSWORD`, `META_APP_SECRET` and `META_CLIENT_SECRET`. The Postgres image reads `POSTGRES_PASSWORD_FILE` itself.

#### Rotating the secrets key

1. Generate a new key, set it as `BOT_SECRETS_KEY` and move the old one to `BOT_SECRETS_KEY_PREVIOUS`, then restart. Everything keeps working: existing values still decrypt.
2. Re-encrypt stored secrets with the new key: `python -m scripts.rotate_bot_secrets` (in Docker: `docker compose exec app python -m scripts.rotate_bot_secrets`). Add `--dry-run` to check first. It exits non-zero and lists anything no configured key can read.
3. After `WIDGET_TOKEN_RENEWAL_WINDOW_SECONDS` (30 days by default), remove `BOT_SECRETS_KEY_PREVIOUS`. Until then, widget visitors with older tokens keep their identity and admin refresh cookies stay valid. Outstanding one-time passcodes (at most 30 minutes old) stop working at step 1.

### RAG / Embeddings

| Variable | What it does | Default |
|----------|-------------|---------|
| `EMBEDDING_PROVIDER` | `huggingface` (embeds inside this deployment) or `openai` (every document chunk and search query is sent to `OPENAI_BASE_URL`, which is then required and must be on `LLM_ENDPOINT_ALLOWLIST` if that is set; the app won't start otherwise) | `huggingface` |
| `RAG_EMBEDDING_MODEL` | Embedding model name | `Alibaba-NLP/gte-base-en-v1.5` |
| `RAG_TOP_K` | Fallback passage count for searches made without a bot. Answers use each bot's *Top K* setting | `5` |
| `CHUNK_SIZE` | Text chunk size for splitting | `2048` |
| `CHUNK_OVERLAP` | Overlap between chunks | `256` |
| `ENABLE_RAG_HYBRID_SEARCH` | Load the CrossEncoder re-ranker so bots can use it. Each bot's *Hybrid search* setting decides whether its answers are re-ranked; without this the setting has no effect | `false` |
| `RE_RANKING_MODEL` | Cross-encoder for re-ranking (a name under `cross-encoder/`, or a full repo id) | `ms-marco-TinyBERT-L2-v2` |
| `RE_RANKING_MODEL_HOME` | Re-ranking model cache directory. Leave unset in Docker | `./data/models/re_ranking` (local) / `/opt/models/re_ranking` (Docker) |
| `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | Load models from the local cache only, never from Hugging Face. Set to `1` in the Docker image | unset (local) / `1` (Docker) |
| `RAG_EMBEDDING_MODEL_REVISION`, `RE_RANKING_MODEL_REVISION` | Commit to load when you use a model other than the defaults (the defaults are pinned in `utils/model_pins.py`) | unset |
| `RAG_EMBEDDING_TRUST_REMOTE_CODE` | Allow a non-default embedding model to run its own Python code from Hugging Face. Only for models you have reviewed, at a pinned revision | `false` |

The Docker image downloads the default embedding and re-ranking models at build time, at the commits pinned in `utils/model_pins.py`, and then runs with Hugging Face downloads switched off: the app makes no requests to Hugging Face at runtime. To use a different model in Docker, change the pins and rebuild.

### Authentication

The app includes a built-in OIDC identity provider — no external auth service needed.

| Variable | What it does | Default |
|----------|-------------|---------|
| `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` | First admin email | `admin@example.com` |
| `AUTH_LOCAL_BOOTSTRAP_PASSWORD` | First admin password. If unset, a random one is written to `$DATA_DIR/bootstrap_password.txt` (mode 0600, never logged) and the file is deleted after the first sign-in | — |
| `AUTH_LOCAL_ISSUER_ENABLED` | Use built-in email + password sign-in (`true`) or only an external IdP (`false`). If unset it is on, except when `MODE=prod` and `AUTH_OIDC_ISSUER` points at an external IdP. In production the app logs a warning while built-in sign-in is active | `true` (see left) |
| `AUTH_COOKIE_SECURE` | Force the admin session cookies' `Secure` flag on or off. Unset: on when `MODE=prod` or `APP_BASE_URL` is https, otherwise it follows the request scheme | (auto) |
| `AUTH_ALLOW_BEARER` | Accept `Authorization: Bearer <ID token>` on admin APIs, for scripts and CI. When off, a request carrying only a Bearer header gets 401 `bearer_disabled`. Widget tokens are unaffected | `false` |
| `AUTH_COOKIE_SAMESITE` | SameSite for the admin session cookies. Keep `lax`: `strict` breaks the redirect back from the IdP after sign-in. CSRF is handled by an Origin check | `lax` |
| `AUTH_SESSION_MAX_AGE_SECONDS` | How long an admin stays signed in, counted from sign-in. After it, the background refresh is refused (audited as `auth.session_expired`) and the admin signs in again; no session cookie is set to outlive it. Applies to built-in and external sign-in, and the built-in issuer refuses older refresh tokens even when presented to it directly. The last ID token stays valid until it expires (usually within the hour). `0` means no limit. Sessions with no visible admin tab already end when the ID token expires, because the UI only refreshes while a tab is visible | `43200` (12 h) when `MODE=prod`, else no limit |
| `AUTH_LOCAL_MIN_PASSWORD_LENGTH` | Minimum length for a new built-in password. The bootstrap password, and any password flagged for change, must be replaced at first sign-in | `12` |

To use an external IdP (Keycloak, Okta, Auth0, Entra, etc.), see the detailed [Authentication section](#authentication-details) below.

### All Variables

See [`.env.sample`](.env.sample) for the complete list with inline documentation.

---

### Step 1 — Install `uv`

```bash
# macOS / Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# Then restart your shell, or run:
source ~/.bashrc   # or ~/.zshrc on macOS

# Windows (PowerShell):
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify it's installed:
```bash
uv --version
```

### Step 2 — Create a Virtual Environment

```bash
# Create a .venv virtual environment in the project folder
uv venv
```

This creates a `.venv/` directory with a Python 3.13 virtual environment.

### Step 3 — Activate the Virtual Environment

You **must** activate the venv before running any Python or pip commands manually.

**Linux:**
```bash
source .venv/bin/activate
```

**macOS (bash):**
```bash
source .venv/bin/activate
```

**macOS (zsh — default on newer Macs):**
```bash
source .venv/bin/activate
```

**Windows (Command Prompt):**
```cmd
.venv\Scripts\activate.bat
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
```

> **Tip:** You'll know the venv is active when your terminal prompt shows `(.venv)` at the beginning.

To deactivate later:
```bash
deactivate
```

### Step 4 — Install Dependencies

```bash
# Install all Python dependencies from pyproject.toml
uv sync
```

This reads `pyproject.toml`, resolves versions, and installs everything into the `.venv`.

> **Shortcut:** If you don't want to manually activate the venv every time, you can use `uv run` instead. For example: `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8080`. The `uv run` command automatically uses the `.venv` without needing to activate it.

### Step 5 — Install and Configure PostgreSQL

> **Important:** This project requires **PostgreSQL**. SQLite is not supported for local development due to compatibility issues.

> **Important for local development:** In your `.env` file, set these paths for local development (the default `.env.sample` has Docker paths like `/app/data`):
>
> ```env
> DATA_DIR=./data
> SENTENCE_TRANSFORMERS_HOME=./data/models/embedding
> RE_RANKING_MODEL_HOME=./data/models/re_ranking
> ```
>
> These are the embedding and re-ranking model caches. Run `uv run python -m utils.model_pins` once to download the pinned models; after that you can set `HF_HUB_OFFLINE=1` to work without Hugging Face access.

#### Install PostgreSQL

If you don't have PostgreSQL installed, follow the instructions for your operating system:

**Linux (Ubuntu/Debian):**

```bash
# Update package list
sudo apt update

# Install PostgreSQL and contrib extensions
sudo apt install -y postgresql postgresql-contrib

# Start and enable PostgreSQL service
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Verify installation
psql --version
```

**Linux (Fedora/RHEL/CentOS):**

```bash
# Install PostgreSQL
sudo dnf install -y postgresql-server postgresql-contrib

# Initialize the database
sudo postgresql-setup --initdb

# Start and enable PostgreSQL service
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Verify installation
psql --version
```

**Windows:**

1. Download the installer from [postgresql.org/download](https://www.postgresql.org/download/windows/)
2. Run the installer and follow the setup wizard
3. Remember the password you set for the `postgres` superuser
4. Keep the default port `5432`
5. After installation, PostgreSQL service starts automatically

Or using **winget** (Windows Package Manager):

```powershell
winget install PostgreSQL.PostgreSQL.16
```

Or using **Chocolatey**:

```powershell
choco install postgresql --params '/Password:yourpassword'
```

#### Create Database and User

After installing PostgreSQL, you need to create a database and user for the application.

**Linux:**

```bash
# Switch to the postgres system user
sudo -i -u postgres

# Open PostgreSQL prompt
psql
```

**Windows (Command Prompt or PowerShell):**

```cmd
# Open PowerShell as Administrator, then run:
psql -U postgres
```

> **Note:** On Windows, you may need to add PostgreSQL to your PATH. The default location is `C:\Program Files\PostgreSQL\16\bin`.

**In the PostgreSQL prompt, run these commands:**

```sql
-- Create a new user for the application
CREATE USER emly WITH PASSWORD 'your_secure_password';

-- Create the database
CREATE DATABASE emly OWNER emly;

-- Grant all privileges on the database to the user
GRANT ALL PRIVILEGES ON DATABASE emly TO emly;

-- Exit the prompt
\q
```

**Linux — exit back to your user:**

```bash
exit
```

#### Configure PostgreSQL Authentication

By default, PostgreSQL uses `peer` authentication on Linux, which may cause connection issues. You need to update the authentication method.

**Linux:**

1. Find your `pg_hba.conf` file:

```bash
sudo find / -name "pg_hba.conf" 2>/dev/null
```

Usually located at `/etc/postgresql/<version>/main/pg_hba.conf`

2. Edit the file:

```bash
sudo nano /etc/postgresql/16/main/pg_hba.conf  # Replace 16 with your version
```

3. Find these lines and change `peer`/`scram-sha-256` to `md5`:

```
# IPv4 local connections:
host    all             all             127.0.0.1/32            md5
# IPv6 local connections:
host    all             all             ::1/128                 md5
```

4. Restart PostgreSQL:

```bash
sudo systemctl restart postgresql
```

**Windows:**

Edit `pg_hba.conf` (usually at `C:\Program Files\PostgreSQL\16\data\pg_hba.conf`) and ensure the local connections use `md5` authentication as shown above, then restart the PostgreSQL service.

#### Configure Environment Variables

Set these in your `.env` file:

```env
DATA_DIR=./data
DATABASE_URL=postgresql://emly:your_secure_password@localhost:5432/emly
```

**Real examples:**

```env
# Local PostgreSQL with custom user
DATABASE_URL=postgresql://emly:your_secure_password@localhost:5432/emly

# Local PostgreSQL with default postgres user
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/emly

# Remote PostgreSQL (e.g., AWS RDS, Supabase, Neon, etc.)
DATABASE_URL=postgresql://myuser:mypassword@db.example.com:5432/emly

# Docker Compose PostgreSQL (uncomment its ports: in docker-compose.yml first)
DATABASE_URL=postgresql://emly:<POSTGRES_PASSWORD>@localhost:5432/emly
```

#### Verify Database Connection

Test your connection before running the app:

```bash
# Using psql
psql -h localhost -U emly -d emly

# If successful, you'll see:
# psql (16.x)
# Type "help" for help.
# emly=>
```

#### Alternative: Use Docker for PostgreSQL Only

If you prefer to run PostgreSQL via Docker while developing locally:

```bash
# Start only PostgreSQL and Qdrant via Docker
docker compose up postgres qdrant
```

This uses the pre-configured credentials from `docker-compose.yml`.

### Step 6 — Configure the Vector Database (Qdrant)

**Option A: Embedded mode (no extra setup)**
Leave `QDRANT_URL` unset in your `.env`. Qdrant will run inside the app process and store data at `./data/qdrant_db/`. Works only with a single worker.

**Option B: Qdrant server (recommended)**
Run Qdrant separately and set:

```env
QDRANT_URL=http://localhost:6333
```

### Step 7 — Build the Widget, Then the Admin UI

```bash
# Build the widget bundle
cd widget
npm install
npm run build-widget
cp dist/emly-widget.js ../ui/public/emly-widget.js
cd ..

# Build the admin UI
cd ui
npm install
npm run build
cd ..
```

### Step 8 — Start the Backend

```bash
# Option 1: Using uvicorn directly
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8080 --env-file .env
```

Open **http://localhost:8080** in your browser.

### Full Local Setup Summary

Here's the complete sequence from scratch:

```bash
# Clone and enter the project
git clone https://github.com/YOUR_USERNAME/emly-ai-assistant.git
cd emly-ai-assistant

# Create and activate Python virtual environment
uv venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate.bat     # Windows (cmd)
# .venv\Scripts\Activate.ps1     # Windows (PowerShell)

# Install all Python dependencies
uv sync

# Create .env file
cp .env.sample .env
# Edit .env and set:
#   DATA_DIR=./data (for local dev, not /app/data which is for Docker)
#   SENTENCE_TRANSFORMERS_HOME=./data/models/embedding
#   RE_RANKING_MODEL_HOME=./data/models/re_ranking
#   DATABASE_URL=postgresql://emly:your_password@localhost:5432/emly
#   AUTH_BOOTSTRAP_SUPERADMIN_EMAIL, AUTH_LOCAL_BOOTSTRAP_PASSWORD
#   Optionally MODEL, OPENAI_BASE_URL, OPENAI_API_KEY as defaults for all bots

# Build widget
cd widget && npm install && npm run build-widget && cp dist/emly-widget.js ../ui/public/emly-widget.js && cd ..

# Build admin UI
cd ui && npm install && npm run build && cd ..

# Create data directory
mkdir -p ./data

# Start the server
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8080 --env-file .env
```


## Admin UI Routes

| Path | What it does |
|------|-------------|
| `/` | Home — redirects to login if not signed in |
| `/login` | Sign in |
| `/admins` | Manage admin users |
| `/bots/{slug}/dashboard` | Bot overview |
| `/bots/{slug}/config` | Bot settings, prompts, topics |
| `/bots/{slug}/files` | Upload files and manage RAG content |
| `/bots/{slug}/conversations` | View chat history |
| `/bots/{slug}/members` | Manage bot team members |

---

## API Endpoints

| Prefix | Purpose |
|--------|---------|
| `/emly/api/chat` | Streaming chat endpoint |
| `/widget/{slug}/*` | Widget chat and public config |
| `/api/admin/auth/*` | Login, logout, session |
| `/api/admin/bots/*` | Bot CRUD, config, files, RAG |
| `/api/admin/admins/*` | Admin management |
| `/api/admin/audit/export` | Whole audit log as CSV (superadmins). Filters: `admin_id`, `bot_id`, `action`, `success`, `since`, `until` |
| `/api/admin/bots/{slug}/audit/export` | One bot's audit log as CSV (owners of that bot) |
| `/api/livez` | Health check (liveness) |
| `/api/readyz` | Readiness check (waits for models + DB) |
| `/docs`, `/openapi.json` | Interactive API docs and schema. Off when `MODE=prod` unless `ENABLE_API_DOCS=true` |

---

## Embedding the Widget

Add this snippet to any website:

```html
<script>
  (function(w,d,s,o,f,js,fjs){
    w['EmlyWidget']=o;w[o]=w[o]||function(){(w[o].q=w[o].q||[]).push(arguments)};
    js=d.createElement(s);fjs=d.getElementsByTagName(s)[0];
    js.id=o;js.src=f;js.async=1;fjs.parentNode.insertBefore(js,fjs);
  })(window,document,'script','emly','/emly-widget.js');
  emly('init', {
    baseUrl: 'https://your-emly-instance.com',
    botId: 'your-bot-slug'
  });
</script>
```

Add your site to the bot's *Allowed widget origins* (Config → Limits) first; new bots only allow this app's own pages. See [`widget/README.md`](widget/README.md) for the build and the widget's public API.

### Who can use the widget

The widget has no sign-in. Anyone who can reach this app's address can open a conversation with any bot, by putting the widget on a page or by calling its API directly. *Allowed widget origins* stops other websites embedding it in a browser; it can't stop a script that sends requests itself.

So a staff-only assistant (an HR or IT helpdesk) is protected by where the app is reachable:

- Publish the app only on your internal network or VPN: an internal load balancer or ingress, no public DNS record, or a reverse proxy that allows only your office and VPN address ranges.
- If the admin UI must be reachable from outside, put the widget paths (`/widget/` and `/emly-widget.js`) behind the internal-only route and the admin paths behind your identity provider.
- Set *Allowed widget origins* on every bot to your intranet's address. In production `/api/readyz` lists bots still open to any site.
- Visitors are told apart only by the token the widget stores in their browser. A form a visitor fills in records what they typed, not who they are.

A public-facing assistant (customer support on your website) needs none of this: it is meant to be reachable by anyone.

---

## Authentication Details

### Default: Built-in Auth (No Setup Required)

The app runs its own OIDC identity provider out of the box. Set these in `.env`:

```env
AUTH_BOOTSTRAP_SUPERADMIN_EMAIL=admin@example.com
AUTH_LOCAL_BOOTSTRAP_PASSWORD=your-secure-password
```

On first boot, the app creates an RSA keypair and seeds the admin account.

### External Identity Provider (SSO)

To use Keycloak, Okta, Auth0, Microsoft Entra, or any OIDC-compliant provider:

```env
AUTH_LOCAL_ISSUER_ENABLED=false
AUTH_OIDC_ISSUER=https://your-idp.example.com/realms/your-realm
AUTH_OIDC_AUDIENCE=your-audience
AUTH_OIDC_CLIENT_ID=your-client-id
AUTH_OIDC_CLIENT_SECRET=your-client-secret
APP_BASE_URL=https://emly.yourdomain.com
```

Provider-specific settings:

| Variable | What it does | Default |
|----------|-------------|---------|
| `AUTH_OIDC_REQUEST_OFFLINE_ACCESS` | Add `offline_access` to the requested scopes so the IdP issues a refresh token and sessions renew silently. **Google:** set `false`. Sessions then last until the ID token expires and admins sign in again (usually silently, through Google's own session) | `true` |
| `AUTH_OIDC_EMAIL_CLAIMS` | Comma-separated token claims to read the admin's email from, first match wins. Only values containing `@` are used. **Entra ID** often omits `email`: use `email,upn` | `email` |
| `AUTH_OIDC_REQUIRED_AMR` | Require multi-factor sign-in: comma-separated methods, at least one of which must be in the ID token's `amr` claim, e.g. `mfa`. A sign-in without one is refused with "Multi-factor sign-in required" and audited as `auth.login_failed` (`reason: mfa_required`). Checked at sign-in, not on every request. Before turning it on, sign in once with MFA and check what your IdP puts in the ID token's `amr` (Okta, for example, sends `mfa`; see [Identity provider setup](#identity-provider-setup)). **Google** doesn't send a reliable `amr`: enforce 2-step verification in the Google Admin console instead | unset |
| `AUTH_OIDC_REQUIRED_ACR` | Accepted `acr` values (comma-separated), for IdPs that express the level that way. Also sent to the IdP as `acr_values` so it asks for the second factor up front | unset |
| `AUTH_OIDC_TRUSTED_EMAIL_CLAIMS` | Claims whose address counts as verified even without `email_verified`, comma-separated. Each must also be in `AUTH_OIDC_EMAIL_CLAIMS`. **Single-tenant Entra ID:** `upn`, since the tenant owns every UPN domain. Prefer this to trusting every claim | unset |
| `AUTH_OIDC_TRUST_EMAIL_UNCONDITIONALLY` | Treat every email the IdP sends as verified. An address taken from a fallback claim (`upn`, `preferred_username`) counts as **unverified** otherwise, and pending invites aren't activated for unverified addresses. Set `true` only for an IdP whose directory you control. `AUTH_OIDC_TRUSTED_EMAIL_CLAIMS` is the narrower choice | `false` |

```env
# Google Workspace
AUTH_OIDC_REQUEST_OFFLINE_ACCESS=false

# Microsoft Entra ID (single tenant)
AUTH_OIDC_EMAIL_CLAIMS=email,upn
AUTH_OIDC_TRUSTED_EMAIL_CLAIMS=upn
```

`AUTH_OIDC_REQUIRED_ACR` and `AUTH_OIDC_REQUIRED_AMR` need an external IdP: with the built-in sign-in, which is password-only, the app refuses to start.

The OIDC callback URL to register with your IdP is:
```
https://emly.yourdomain.com/api/admin/auth/callback
```

Pre-register admins before their first login:
```bash
curl -X POST https://emly.yourdomain.com/api/admin/admins/pending \
  -H "Cookie: emly_admin_session=<superadmin-cookie>" \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com"}'
```

### Identity provider setup

For every IdP: register a web application (confidential client) with the redirect URI `https://<your APP_BASE_URL>/api/admin/auth/callback`, and request the scopes `openid email profile`. The app adds `offline_access` for refresh tokens unless `AUTH_OIDC_REQUEST_OFFLINE_ACCESS=false`. Set `AUTH_OIDC_ISSUER` to the issuer exactly as the IdP's discovery document states it, `AUTH_OIDC_CLIENT_ID` and `AUTH_OIDC_CLIENT_SECRET` (or `AUTH_OIDC_CLIENT_SECRET_FILE`), and `AUTH_OIDC_AUDIENCE` to the client ID unless your IdP issues ID tokens for another audience. Then set `AUTH_LOCAL_ISSUER_ENABLED=false` so password sign-in is off.

Access is by invitation: signing in at the IdP doesn't by itself give access. Invite admins from the Admins page (or pre-register them as above), with a role on each bot.

**Microsoft Entra ID**
- Issuer: `https://login.microsoftonline.com/<tenant-id>/v2.0`. Only single-tenant apps work: the issuer must match exactly, and the multi-tenant `common` endpoint issues tokens with per-tenant issuers.
- Entra often leaves out the `email` claim. Set `AUTH_OIDC_EMAIL_CLAIMS=email,upn` and `AUTH_OIDC_TRUSTED_EMAIL_CLAIMS=upn`, so the UPN counts as a verified address. Your tenant owns every UPN domain.
- MFA: enforce it with a Conditional Access policy for this app. To have Emly refuse sign-ins without it as well, decode an ID token from an MFA sign-in, check that `amr` lists `mfa`, then set `AUTH_OIDC_REQUIRED_AMR=mfa`.

**Okta**
- Issuer: your authorization server, e.g. `https://<org>.okta.com/oauth2/default` (or `https://<org>.okta.com` for the org server).
- MFA: require it in the app's authentication policy. Okta's ID tokens list the methods used in `amr` (for example `pwd`, `mfa`, `otp`); confirm on one sign-in, then set `AUTH_OIDC_REQUIRED_AMR=mfa`.

**Google Workspace**
- Issuer: `https://accounts.google.com`. Set `AUTH_OIDC_REQUEST_OFFLINE_ACCESS=false`: Google doesn't issue refresh tokens for `offline_access`, so sessions end when the ID token expires (about an hour) and admins sign in again, usually without a prompt.
- MFA: enforce 2-step verification in the Google Admin console. Google's ID tokens don't carry a reliable `amr`, so leave `AUTH_OIDC_REQUIRED_AMR` unset.

### Removing an admin's access

1. **Disable or remove them in Emly** (Admins page). This takes effect on their next request: every request re-reads the admin record.
2. **Disable them at the IdP.** They can't sign in again. A session already open lasts until its ID token expires (usually up to an hour) or its next refresh, which the IdP normally refuses for a disabled user.
3. Every session ends `AUTH_SESSION_MAX_AGE_SECONDS` after sign-in at the latest (12 hours by default in production).

Admin APIs accept the ID token as a `Bearer` header only when `AUTH_ALLOW_BEARER=true` (for scripts and CI), with the same checks. Off by default, so a token copied out of a browser can't be replayed from elsewhere.

---

## Backup and restore

Back these up together, from the same moment where possible:

| What | Where (compose defaults) | How |
|------|--------------------------|-----|
| Database: bots, configs, conversations, admins, audit log | Postgres (`./data/postgres`) | `docker compose exec -T postgres pg_dump -U emly -Fc emly > emly-$(date +%F).dump`, or your managed database's snapshots |
| Search index (document vectors) | Qdrant (`./data/qdrant`) | Stop Qdrant and copy the directory, or use Qdrant's snapshot API. It can also be rebuilt by re-indexing each bot's files, which costs time (and embedding calls with `EMBEDDING_PROVIDER=openai`) |
| Uploaded documents | `DATA_DIR/bots/*/uploads/` (`./data/bots`) | File copy. Needed to re-index |
| Visitor attachments | `DATA_DIR/attachments/` | File copy |
| Scheduled jobs (pending alert emails, crawls) | `DATA_DIR/actions.db` | File copy (SQLite) |
| Built-in sign-in keys | `AUTH_LOCAL_KEYS_DIR` (default `DATA_DIR/auth_keys/`) | File copy. Without them, every admin signs in again |
| `BOT_SECRETS_KEY` (and any `BOT_SECRETS_KEY_PREVIOUS`) | Your secret store or `.env` | Keep it **separately** from the database backup. Without it, stored bot API keys and channel credentials can't be decrypted |

**Restore order:**

1. Put `.env` (with the same `BOT_SECRETS_KEY`) in place. Don't start the app yet. Keep `APP_BASE_URL` the same if the address is the same; see the note below if it changes.
2. Start Postgres, wait until the database accepts queries, then restore. On a fresh volume Postgres first runs a temporary server while it initialises, and `pg_isready` already answers before the `emly` database exists, so wait for a real query:

   ```bash
   docker compose up -d postgres
   until docker compose exec -T postgres psql -U emly -d emly -c 'select 1' >/dev/null 2>&1; do sleep 2; done
   docker compose exec -T postgres pg_restore -U emly -d emly --clean --if-exists < emly.dump
   ```
3. Restore `./data/qdrant` (with Qdrant stopped), or plan to re-index.
4. Restore `DATA_DIR` (uploads, attachments, `actions.db`, `auth_keys`).
5. Start the app. Boot runs any pending migrations and checks every bot's config. A bot whose config no longer loads doesn't stop start-up: it is named in the start-up log and on `/api/readyz`, and answers "not available" until fixed. Then check `/api/readyz`, sign in, and ask one bot a question that its documents answer.

**Restoring at a different address** (a recovery site, a move, a test restore): set `APP_BASE_URL` to the new address. Admins who use the built-in sign-in are moved to it the first time they sign in (audited as `admin.relinked`, reason `built_in_issuer_moved`). With an external identity provider, add the new redirect URI (`<new address>/api/admin/auth/callback`) to the app registration first. Sessions from the old address end.

Test the restore on a separate host before you rely on it.

**Drill record.** 2026-11-15, compose, `claim-gaps-4`: a bot with one indexed document, a conversation and an admin with a changed password were backed up with the steps above, the stack was removed, and everything was restored into empty volumes. Row counts, file hashes and the signing key matched; the admin signed in with the same password; a search returned the same passage with the same score. The drill found the four problems fixed on `claim-gaps-5`: `PORT` in `.env` broke the container port, step 2's start-up race, sign-in after an address change, and unknown `/api/` paths answering 200. Not covered: stored bot API keys and channel credentials (none were set), and a scheduled job pending in `actions.db`.

## What the model sees

Every model call goes to the bot's own endpoint (its LLM settings, or the `OPENAI_BASE_URL` default), which must be on `LLM_ENDPOINT_ALLOWLIST` when that is set. For each visitor message there are at most three calls. The same pipeline serves the widget and every chat channel.

| Call | When | What it contains |
|------|------|------------------|
| Topic routing | The bot has two or more topics, and isn't partway through collecting details for one | Routing instructions; each topic's name and description; the current topic's name; the last `LATEST_N_MESSAGES` (default 3) exchanges of this conversation; the visitor's message |
| Detail extraction | The topic collects details (slots) | The visitor's message, and the name, description and type of each detail to extract |
| Answer | Always, except when a knowledge topic finds no passage above the threshold (then a fixed reply, `DEFAULT_MESSAGE`, no call) | The answer template (the topic's, else the bot's) filled with: the visitor's message; the details collected so far; the last `LATEST_N_MESSAGES` exchanges in this topic; for knowledge topics, up to the bot's *Top K* (default 5) passages from its documents that score at least its *Embedding threshold* |

Questions that ask for a missing detail come from templates and make no call.

**Answer only from your documents** (*Config → Answer only from your documents*, `require_citation`; on for new bots): topics that answer directly also search the bot's documents, so they behave like knowledge topics: an answer is built from matching passages, and with no match the bot gives the fixed reply instead of answering from the model's general knowledge. Topics that collect details (forms) are unaffected. Existing bots keep it off until an owner turns it on.

**Retrieved passages are data, not instructions.** Wherever an answer template puts `{context}`, the passages arrive between `### Reference passages start ###` and `### Reference passages end ###`, after a line telling the model to use them as information and to ignore any instructions inside them. A passage can't close that fence early (the markers are removed from passage text). This lowers, but can't remove, the risk that a document someone uploaded or a crawled page steers an answer. The model has no tools to call: the worst it can do is answer wrongly. Admins control which documents and sites a bot uses.

**Other calls:**

- **Form-alert summary**, only when the bot's alerts are set to the full alert (new bots default to link-only, which makes no call): this conversation's messages and the summary instructions. The submitted form's field values are left out.
- **Sentiment and intent**, only when *Analyse conversations* is on: one call per conversation, once it has been quiet for 30 minutes (again if it later resumes), containing the visitor's messages in that conversation (the last 4,000 characters; not the bot's answers, not form values) and the classifier instructions.
- **Embeddings** run inside the deployment by default. With `EMBEDDING_PROVIDER=openai`, every document chunk and every search query is sent to `OPENAI_BASE_URL`. The re-ranker always runs locally.

**Never sent:** the visitor's name, email, phone, IP address or location; the values of submitted forms; files visitors attach; other conversations; other bots' documents; API keys. LangSmith tracing stays off unless `ALLOW_LANGSMITH_TRACING=true`.

## What the logs contain

- **Never:** message text, form values, passwords, tokens, cookies or API keys.
- **Email addresses** are masked in every log line as `<email:1a2b3c4d at corp.example>`. That's a short SHA-256 hash of the lower-cased address plus its domain, so lines about the same person still match. To find someone, hash their address the same way.
- **Admins and invites** are named by id (`adm-…`, `pend-…`). Failed sign-ins name the sign-in provider and the user's subject id there.
- **The audit log** records who did what, with ids. A visitor's rating is recorded as the score only.
- **`last_login_at`** is updated at sign-in only, not on every request.
- **Exception: `AUDIT_LOG_STDOUT`.** When on, audit events also go to standard output as JSON lines (`"type":"emly.audit"`) with the same content as the audit table, unmasked, including admin and invitee email addresses. Route them to your SIEM rather than a general log store.

## Outbound connections

These are the only places the backend opens a connection to another system, besides Postgres. `tests/security/test_network_clients.py` fails if a module starts importing a network client that isn't listed here.

At start-up the app logs an "Outbound destinations" block naming, by host only, what this deployment is configured to use: the default model endpoint and every endpoint a bot has set, embeddings, Qdrant, the mail server, the identity provider, connected chat platforms, Hugging Face downloads and LangSmith.

For one bot, its *Settings* page shows the same as "Where this bot's data goes" (`GET /api/admin/bots/{slug}/data-recipients`, any member): the model endpoint its answers, alert summaries and classification use, embeddings, the search index, each connected chat platform, the form alert recipients with what alerts contain, and LangSmith when allowed. Each row is marked as inside or outside your network (by address or internal name; no DNS lookup).

| Destination | When | Code |
|-------------|------|------|
| The bot's model endpoint (or `OPENAI_BASE_URL`) | Answers, routing and detail extraction; enrichment when *Analyse conversations* is on; alert summaries for full alerts. See [What the model sees](#what-the-model-sees). | `agents/conversation_agent.py`, `services/enrichment.py`, `services/widget_email.py` |
| `OPENAI_BASE_URL` for embeddings | Only with `EMBEDDING_PROVIDER=openai` | `config.py` |
| Qdrant (`QDRANT_URL`) | Indexing and search | `agents/rag_manager.py` |
| Mail server (`EMAIL_SERVER`) | Form alerts, invites, sign-in codes | `services/email_service.py` |
| External identity provider | Sign-in, when `AUTH_PROVIDER=oidc`: discovery, keys, token exchange | `services/auth/oidc.py` |
| Slack, Teams, Google Chat, WhatsApp, Telegram | Only for connected channels: replies, the sender's profile (Slack), and the platforms' signing keys | `channels/dispatcher.py`, `channels/auth/_http.py`, `channels/auth/service_account_jwt.py`, `channels/telegram.py` |
| Websites an admin crawls | Only when an admin adds a crawl. Private and internal addresses are refused unless their range is in `CRAWL_ALLOWED_PRIVATE_CIDRS`, and each connection goes to the address that was checked. | `services/crawl_fetch.py` |
| Hugging Face Hub | Only when model downloads are on. The Docker image sets `HF_HUB_OFFLINE=1` and bakes the models in. | `config.py`, `utils/model_pins.py` |

Teams and Google Chat check each incoming message's signature with keys fetched from Microsoft and Google, so those two channels need outbound access to them even when everything else runs inside your network. Slack, WhatsApp and Telegram also need to reach their platforms; only the web widget works with no outbound connection.

The browser loads the widget from this app. Logo, avatar and launcher icons set by an admin load from wherever their URLs point, unless `WIDGET_ICON_HOSTS` limits them.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Your Website                          │
│              <script src="emly-widget.js">               │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│                 Emly AI Assistant                         │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  FastAPI     │  │  Admin UI    │  │  Widget        │  │
│  │  Backend     │  │  (Next.js)   │  │  (UMD Bundle)  │  │
│  └──────┬──────┘  └──────────────┘  └────────────────┘  │
│         │                                                │
│  ┌──────┴──────┐  ┌──────────────┐                      │
│  │  LangGraph  │  │  RAG Manager │                      │
│  │  Workflow    │  │  (Qdrant)    │                      │
│  └─────────────┘  └──────────────┘                      │
└──────────────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
┌──────────────┐    ┌──────────────┐
│  PostgreSQL  │    │   Qdrant     │
│  (Database)  │    │  (Vector DB) │
└──────────────┘    └──────────────┘
```


---

## Project Structure

```
emly-ai-assistant/
├── main.py                 # FastAPI app entry point
├── config.py               # Environment variable loading
├── start.sh                # Quick-start script
├── pyproject.toml          # Python dependencies
├── .env.sample             # Environment variable template
├── docker-compose.yml      # Full stack (app + postgres + qdrant)
├── Dockerfile              # 3-stage build (widget → UI → runtime)
│
├── routes/                 # API route handlers
├── services/               # Business logic (auth, data, email)
├── agents/                 # LangGraph workflows, RAG manager
├── db/                     # Database models (Peewee ORM)
├── models/                 # Pydantic schemas
├── migrations/             # Auto-applied database migrations
├── utils/                  # Helper functions
│
├── ui/                     # Admin dashboard (Next.js)
│   ├── app/                # Pages and layouts
│   ├── public/             # Static assets
│   └── out/                # Built static export
│
├── widget/                 # Embeddable chat widget
│   ├── src/                # Source code
│   └── dist/               # Built bundle
│
└── channels/               # Channel adapters (Slack, etc.)
```

---

## Troubleshooting

### "Permission denied" on Linux (Docker)
The container runs as user `emly` (uid 10001). Fix with:
```bash
sudo chown -R 10001:10001 ./data
```

### Database errors

If you encounter database errors:

1. Verify PostgreSQL is running:
```bash
# Linux
sudo systemctl status postgresql

# Windows (PowerShell)
Get-Service postgresql*
```

2. Test the database connection:
```bash
psql -h localhost -U emly -d emly
```

3. Check your `DATABASE_URL` in `.env` is correct

4. If needed, recreate the database:
```bash
sudo -i -u postgres  # Linux
psql
DROP DATABASE IF EXISTS emly;
CREATE DATABASE emly OWNER emly;
\q
exit
```

### Embedding model download is slow on first boot
Outside Docker, the first run downloads the embedding model (~500MB) and caches it. The Docker image already contains it.

### "Could not load the embedding model … Hugging Face downloads are off"
The image loads models from `/opt/models` only. An older `.env` may still set `SENTENCE_TRANSFORMERS_HOME=/app/data/models/embedding`; remove that line (and any `RE_RANKING_MODEL_HOME` under `/app/data`) and restart.

### Port 8080 already in use
Change the port in `.env`:
```env
PORT=9000
```
Or stop the other process using that port.

### Widget not loading in Admin UI
Rebuild the widget and UI:
```bash
cd widget && npm run build-widget && cp dist/emly-widget.js ../ui/public/emly-widget.js && cd ..
cd ui && npm run build && cd ..
```

---

## License

See [LICENSE](LICENSE) for details.
