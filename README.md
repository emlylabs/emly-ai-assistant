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
#    - ADMIN_EMAIL        (your admin login email)
#    - ADMIN_PASSWORD     (your admin login password)
#    - OPENAI_API_KEY     (your LLM provider API key)
#    - OPENAI_BASE_URL    (your LLM provider base URL)
#
#    Leave DATABASE_URL and QDRANT_URL as-is — Docker Compose
#    overrides them to point at the containers automatically.

# 4. (Linux only) Fix file permissions for the container user
mkdir -p ./data ./data/postgres ./data/qdrant
sudo chown -R 10001:10001 ./data

# 5. Start the stack
docker compose up --build
```

Open **http://localhost:8080** in your browser. Sign in with the `ADMIN_EMAIL` and `ADMIN_PASSWORD` you set in `.env`.

### What's Running

| Service | Port | Description |
|---------|------|-------------|
| **app** | 8080 | FastAPI backend + Admin UI + Widget |
| **postgres** | (internal) | PostgreSQL database |
| **qdrant** | 6333, 6334 | Vector database for RAG |

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

---

## Local Development (Without Docker)

Use this when you want hot-reload on the backend or UI during development.

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

### Step 2 — Configure the Database

You have two options: **SQLite** (simple, no setup) or **PostgreSQL** (production-grade).

#### Option A: SQLite (Easiest — No Extra Software)

SQLite stores everything in a single file. Perfect for local development and testing.

Open your `.env` file and set:

```env
DATABASE_URL=sqlite:///data/emlygenai_app.db
```

That's it. No database server needed. The file will be created automatically at `./data/emlygenai_app.db` when the app starts.

> **Note:** SQLite works only with a **single worker process**. Do not set `WEB_CONCURRENCY` > 1 with SQLite.

#### Option B: PostgreSQL (Recommended for Production)

If you have PostgreSQL installed locally (or running via Docker), set these in your `.env`:

```env
DATABASE_URL=postgresql://USERNAME:PASSWORD@HOST:PORT/DATABASE_NAME
```

**Real examples:**

```env
# Local PostgreSQL with default user
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/emly

# Remote PostgreSQL (e.g., AWS RDS, Supabase, Neon, etc.)
DATABASE_URL=postgresql://myuser:mypassword@db.example.com:5432/emly

# Docker Compose PostgreSQL (already configured in docker-compose.yml)
DATABASE_URL=postgresql://emly:emlypassword@localhost:5432/emly
```

If running only PostgreSQL + Qdrant via Docker (while developing the backend locally):

```bash
docker compose up postgres qdrant
```

### Step 3 — Configure the Vector Database (Qdrant)

**Option A: Embedded mode (no extra setup)**
Leave `QDRANT_URL` unset in your `.env`. Qdrant will run inside the app process and store data at `./data/qdrant_db/`. Works only with a single worker.

**Option B: Qdrant server (recommended)**
Run Qdrant separately and set:

```env
QDRANT_URL=http://localhost:6333
```

### Step 4 — Build the Admin UI and Widget

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

### Step 5 — Start the Backend

```bash
# Option 1: Using uvicorn directly
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8080

# Option 2: Using the start script
bash start.sh
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
# Edit .env — set DATABASE_URL (see options above), ADMIN_EMAIL, ADMIN_PASSWORD, OPENAI_API_KEY, etc.

# Build widget
cd widget && npm install && npm run build-widget && cp dist/emly-widget.js ../ui/public/emly-widget.js && cd ..

# Build admin UI
cd ui && npm install && npm run build && cd ..

# Create data directory
mkdir -p ./data

# Start the server
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

---

## Configuration Reference

All configuration is done via environment variables. Copy `.env.sample` to `.env` and edit it.

### Essential Variables

| Variable | What it does | Default / Example |
|----------|-------------|-------------------|
| `DATABASE_URL` | Database connection string | See [database section](#step-2--configure-the-database) above |
| `ADMIN_EMAIL` | Superadmin email (created on first boot) | `admin@example.com` |
| `ADMIN_PASSWORD` | Superadmin password | Set your own |
| `OPENAI_API_KEY` | LLM API key (used as fallback for bots without their own key) | Your API key |
| `OPENAI_BASE_URL` | LLM API base URL | `https://openrouter.ai/api/v1` |
| `MODEL` | Default LLM model | `google/gemma-4-26b-a4b-it:free` |
| `QDRANT_URL` | Vector database URL (leave unset for embedded mode) | `http://localhost:6333` |
| `DATA_DIR` | Where the app stores files, models, and database | `./data` (local) / `/app/data` (Docker) |

### LLM Provider Configuration

Per-bot LLM settings (provider, model, API key) are configured through the Admin UI under each bot's **Config** tab. The environment variables (`MODEL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`) serve as deployment-wide fallbacks only.

### RAG / Embeddings

| Variable | What it does | Default |
|----------|-------------|---------|
| `EMBEDDING_PROVIDER` | `huggingface` or `openai` | `huggingface` |
| `RAG_EMBEDDING_MODEL` | Embedding model name | `Alibaba-NLP/gte-base-en-v1.5` |
| `RAG_TOP_K` | Number of chunks to retrieve | `5` |
| `CHUNK_SIZE` | Text chunk size for splitting | `2048` |
| `CHUNK_OVERLAP` | Overlap between chunks | `256` |
| `ENABLE_RAG_HYBRID_SEARCH` | Enable CrossEncoder re-ranking | `false` |

### Authentication

The app includes a built-in OIDC identity provider — no external auth service needed.

| Variable | What it does | Default |
|----------|-------------|---------|
| `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` | First admin email | `admin@example.com` |
| `AUTH_LOCAL_BOOTSTRAP_PASSWORD` | First admin password (random if unset) | — |
| `AUTH_LOCAL_ISSUER_ENABLED` | Use built-in auth (`true`) or external IdP (`false`) | `true` |

To use an external IdP (Keycloak, Okta, Auth0, Entra, etc.), see the detailed [Authentication section](#authentication-details) below.

### All Variables

See [`.env.sample`](.env.sample) for the complete list with inline documentation.

---

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
| `/api/livez` | Health check (liveness) |
| `/api/readyz` | Readiness check (waits for models + DB) |

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

See [`widget/EMBED.md`](widget/EMBED.md) for full customization (themes, user IDs, callbacks).

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

---

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
│  (or SQLite) │    │  (Vector DB) │
└──────────────┘    └──────────────┘
```

For detailed architecture docs, see [`docs/multi-bot.md`](docs/multi-bot.md).

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
├── channels/               # Channel adapters (Slack, etc.)
└── docs/                   # Architecture and API docs
```

---

## Troubleshooting

### "Permission denied" on Linux (Docker)
The container runs as user `emly` (uid 10001). Fix with:
```bash
sudo chown -R 10001:10001 ./data
```

### Database errors after switching between SQLite and PostgreSQL
Delete the data directory and start fresh:
```bash
rm -rf ./data
mkdir -p ./data
```

### Embedding model download is slow on first boot
The first run downloads the embedding model (~500MB). This is cached for subsequent runs.

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
