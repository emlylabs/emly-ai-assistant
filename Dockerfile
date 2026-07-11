# syntax=docker/dockerfile:1.7
#
# Build (BuildKit is required — `DOCKER_BUILDKIT=1` is the default in modern
# Docker Desktop, set it explicitly on older daemons):
#
#   docker build -t emly-ai-assistant:dev .
#
# Run:
#   docker run --rm -p 8080:8080 --env-file .env emly-ai-assistant:dev
#
# Layer order is engineered for fast incremental rebuilds. The slow layers —
# system packages, Python deps, the embedding-model download, and the npm
# install — are independent of source changes and cache across rebuilds via
# BuildKit cache mounts. The application source is the last `COPY`, so the
# typical edit-rebuild loop only re-runs the final copy + bytecode compile.

################################################################################
# Stage 1a — Embeddable widget bundle (webpack)
################################################################################
# Produces /widget/dist/emly-widget.js — the UMD bundle that customer sites
# embed via <script src="/emly-widget.js">. Lives in its own stage so a change
# under widget/ rebuilds the bundle without re-running the Next.js build, and
# vice versa. The committed copy at ui/public/emly-widget.js is overwritten in
# the next stage so we never ship a stale bundle.
FROM node:22-slim AS widget-builder

WORKDIR /widget

# Lockfile-only layer: re-runs only when package.json / a lockfile changes.
# The widget package currently has yarn.lock / pnpm-lock.yaml but no
# package-lock.json — `npm install` works with any of them; switch to
# `npm ci` once a package-lock.json is committed.
COPY widget/package.json widget/package-lock.json* widget/yarn.lock* widget/pnpm-lock.yaml* ./
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund

# Source + build. Cached as long as widget/ source files don't change.
COPY widget/ ./
RUN npm run build-widget


################################################################################
# Stage 1b — Next.js admin UI (static export)
################################################################################
FROM node:22-slim AS ui-builder

WORKDIR /ui

# Lockfile-only layer: re-runs only when package.json / lock change.
COPY ui/package.json ui/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# Source + build. Cached as long as ui/ source files don't change. The .next
# cache is mounted so subsequent edits within ui/ recompile incrementally.
COPY ui/ ./
# Overwrite any committed ui/public/emly-widget.js with the bundle we just
# built — guarantees the static export ships the matching widget code.
COPY --from=widget-builder /widget/dist/emly-widget.js ./public/emly-widget.js
RUN --mount=type=cache,target=/ui/.next/cache \
    npm run build


################################################################################
# Stage 2 — Python runtime
################################################################################
FROM python:3.13-slim AS runtime

# Pull the uv binary from its official image — no `pip install uv` round-trip.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# ---- System packages ---------------------------------------------------------
# Cache mounts keep apt's package archive on the BuildKit cache, so subsequent
# rebuilds reuse the downloaded .deb files. The lists/-only delete inside the
# layer keeps the final image small.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        pandoc netcat-openbsd curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# ---- Non-root runtime user ---------------------------------------------------
# Created early so every subsequent COPY can use --chown without a slow `-R`
# chown pass at the end of the build.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin emly && \
    chown emly:emly /app

# ---- Non-root runtime user ---------------------------------------------------
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin emly && \
    chown emly:emly /app && \
    mkdir -p /home/emly/.cache/huggingface && \
    mkdir -p /app/data/models/embedding && \
    chown -R emly:emly /home/emly/.cache && \
    chown -R emly:emly /app/data

USER emly

USER emly

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# ---- Dependency layer: invalidates only when lockfile / pyproject changes ----
COPY --chown=emly:emly pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/home/emly/.cache/uv,uid=10001,gid=10001 \
    uv sync --frozen --no-install-project --no-dev

# ---- Embedding model layer: invalidates only when the model name changes -----
ENV SENTENCE_TRANSFORMERS_HOME=/app/data/models/embedding \
    RAG_EMBEDDING_MODEL=Alibaba-NLP/gte-base-en-v1.5
RUN python -c "import os; from sentence_transformers import SentenceTransformer; \
SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], \
cache_folder=os.environ['SENTENCE_TRANSFORMERS_HOME'], \
trust_remote_code=True, device='cpu')"

# ---- Application source: changes most often, copied last ---------------------
# .dockerignore strips node_modules, .next, .venv, .git, data/, etc.
COPY --chown=emly:emly . .

# Pull the pre-built static UI in from stage 1.
COPY --from=ui-builder --chown=emly:emly /ui/out ./ui/out

EXPOSE 8080

# Liveness probe matches /api/livez (always 200 once Uvicorn is up). Readiness
# (/api/readyz) gates on embedding model + Qdrant + DB and is what Kubernetes
# should poll, but for plain Docker `livez` is the right one.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/livez || exit 1

CMD ["bash", "job.sh"]
