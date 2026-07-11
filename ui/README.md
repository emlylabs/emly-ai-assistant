# Emly admin UI

Static Next.js app served at `/` by the FastAPI backend. Talks to the admin
auth & management endpoints under `/api/admin/*`.

## Develop

```bash
cd ui
npm install
npm run dev    # http://localhost:3000
```

When developing locally, Next.js dev server runs on port 3000. The browser
calls go to `/api/admin/*` on the same origin — point that to your running
backend by hitting it through e.g. `next dev -p 3000` while FastAPI runs on
8080, then navigate to `http://localhost:8080` for the integrated experience.
For pure SPA dev, set up a proxy in `next.config.js` or run the backend at
8080 with CORS enabled (already configured).

## Build for production

```bash
npm run build
```

Outputs static HTML/JS into `ui/out/`. The FastAPI `main.py` catch-all route
serves that directory. The Dockerfile builds this stage automatically.

## Routes

- `/` — redirects to `/admins` (if signed in) or `/login`.
- `/login` — email + password.
- `/admins` — list admins, send invites, revoke pending invites.
- `/accept-invite?token=…` — invitee sets a password.

The bearer token is stored in `localStorage` under `emly_admin_token` and
attached as `Authorization: Bearer …` to every `/api/admin/*` request. On a
401 the client clears the token and redirects to `/login`.
