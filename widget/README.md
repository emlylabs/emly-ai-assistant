# Emly Widget

This directory contains the embeddable React chat widget for Emly AI Assistant.
It can run in Vite during development or build into a standalone UMD bundle for
external websites.

## Development

```bash
cd widget
npm install
npm run dev
```

Vite runs on:

```text
http://localhost:5173
```

Create `widget/.env.local` for local defaults:

```env
VITE_BACKEND_BASE_URL=http://localhost:8080
VITE_BOT_ID=support-faq
VITE_USER_ID_EXPIRY_DAYS=5
```

The widget calls:

```text
${VITE_BACKEND_BASE_URL}/widget/${VITE_BOT_ID}/chat
${VITE_BACKEND_BASE_URL}/widget/${VITE_BOT_ID}/config
```

Runtime values passed to `ChatbotWidget.initialize({ baseUrl, botId })` override
the build-time defaults.

## UMD Build

```bash
npm run build-widget
```

Output:

```text
dist/emly-widget.js
```

For the integrated FastAPI app:

```bash
cd ..
cp widget/dist/emly-widget.js ui/public/emly-widget.js
```

Then rebuild `ui/` so the static export includes the current widget script.

## Public API

```js
window.ChatbotWidget.initialize(
  {
    baseUrl: "https://your-domain.example.com",
    botId: "support-faq",
    position: "bottom-right",
    theme: "slate"
  },
  "emly-chat"
);

window.ChatbotWidget.destroy();
```

See [EMBED.md](EMBED.md) for the full embed guide.
