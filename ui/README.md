# Emly Admin UI

This directory contains the Next.js admin console for Emly AI Assistant. It is
built as a static export and served by the FastAPI backend from `ui/out/`.

The UI talks to backend routes under `/api/admin/*` and uses the admin OIDC
session cookie set by `/api/admin/auth/login` and `/api/admin/auth/callback`.
Admin access tokens are not stored in `localStorage`.

## Develop

Install dependencies:

```bash
cd ui
npm install
```

Run the Next.js dev server:

```bash
npm run dev
```

The dev server listens on:

```text
http://localhost:3000
```

For the integrated production-like experience, build the UI and visit the
FastAPI app on `http://localhost:8080`. FastAPI serves `ui/out/` at `/`.

## Build

```bash
cd ui
npm install
npm run build
```

The build output is:

```text
ui/out/
```

The root FastAPI app in `main.py` serves this directory through a catch-all
route. API routes are registered before the catch-all, so `/api/admin/*`,
`/widget/*`, `/channels/*`, and health endpoints keep precedence.

## Widget Asset

The admin static export also ships the embeddable widget script from:

```text
ui/public/emly-widget.js
```

When changing the widget, rebuild it first:

```bash
cd widget
npm run build-widget
cd ..
cp widget/dist/emly-widget.js ui/public/emly-widget.js
cd ui
npm run build
```

The Dockerfile performs this copy automatically during the multi-stage build.

## Routes

Main route groups:

- `/` redirects to the appropriate admin entry point.
- `/login` starts the OIDC login flow.
- `/auth/callback` completes the OIDC callback.
- `/dashboard`, `/conversations`, `/users` show workspace-level views.
- `/bots` lists active bots.
- `/bots/deleted` lists soft-deleted bots.
- `/bots/{slug}/dashboard` shows bot overview metrics.
- `/bots/{slug}/setup` guides bot setup.
- `/bots/{slug}/config` edits prompts, topics, RAG, limits, forms, and widget behavior.
- `/bots/{slug}/files` manages uploads and crawl jobs.
- `/bots/{slug}/retrieval` inspects RAG search results.
- `/bots/{slug}/conversations` shows sessions and messages.
- `/bots/{slug}/analytics` shows bot analytics.
- `/bots/{slug}/channels` manages channel integrations.
- `/bots/{slug}/members` manages bot-level admin roles.
- `/bots/{slug}/audit` shows bot audit logs.
- `/workspace/settings`, `/workspace/templates`, `/workspace/crawls` manage workspace-level settings.
- `/admins`, `/admins/{admin_id}`, `/admins/audit`, `/admins/me` manage admin users and audit logs.
- `/request-access` is shown when a signed-in OIDC user has not been invited.
- `/accept-invite` handles invite acceptance.

## Authentication Model

The admin UI relies on an httpOnly cookie. Browser JavaScript cannot read the
session token. The UI checks the current user through:

```text
GET /api/admin/auth/me
```

Mutating admin requests are protected by the backend's origin check middleware.
If a request returns `401`, the UI routes the user back through `/login`.
