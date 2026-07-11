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

Open **http://localhost:8080** in your browser. Sign in with the `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` and `AUTH_LOCAL_BOOTSTRAP_PASSWORD` you set in `.env`.

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

---

## Configuration Reference

All configuration is done via environment variables. Copy `.env.sample` to `.env` and edit it.

### Essential Variables

| Variable | What it does | Default / Example |
|----------|-------------|-------------------|
| `AUTH_BOOTSTRAP_SUPERADMIN_EMAIL` | Superadmin email (created on first boot) | `admin@example.com` |
| `AUTH_LOCAL_BOOTSTRAP_PASSWORD` | Superadmin password | Set your own |
| `QDRANT_URL` | Vector database URL (leave unset for embedded mode) | `http://localhost:6333` |
| `DATA_DIR` | Where the app stores files, models, and database | `./data` (local) / `/app/data` (Docker) |
| `SENTENCE_TRANSFORMERS_HOME` | Embedding model cache directory | `./data/models/embedding` (local) / `/app/data/models/embedding` (Docker) |

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
> ```
>
> `SENTENCE_TRANSFORMERS_HOME` is where the embedding model cache is stored. It also controls the re-ranking model cache (`{DATA_DIR}/models/re_ranking`).

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

# Docker Compose PostgreSQL (already configured in docker-compose.yml)
DATABASE_URL=postgresql://emly:emlypassword@localhost:5432/emly
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
#   DATABASE_URL=postgresql://emly:your_password@localhost:5432/emly
#   AUTH_BOOTSTRAP_SUPERADMIN_EMAIL, AUTH_LOCAL_BOOTSTRAP_PASSWORD
#   OPENAI_API_KEY, OPENAI_BASE_URL

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
│  (Database)  │    │  (Vector DB) │
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
