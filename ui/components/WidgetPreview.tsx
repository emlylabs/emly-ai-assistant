"use client";

import { useEffect } from "react";

// Mounts the production widget bundle (widget/dist/emly-widget.js, served
// by FastAPI from ui/public/) on the config page so admins can interact
// with the real chat experience instead of the synthetic test drawer.
// `reloadKey` should change whenever the saved config changes — bumping
// it destroys the current instance and re-initializes, which causes the
// widget to refetch /widget/{slug}/config.

// Cache-bust the bundle on each page load. Without this, the browser
// serves a stale `/emly-widget.js` after a widget rebuild and admins see
// old behavior until a hard reload. Computed once at module load so a
// single bundle is shared across mounts in the same SPA session.
const WIDGET_SRC = `/emly-widget.js?v=${Date.now()}`;
const CONTAINER_ID = "emly-config-preview-widget";

type WidgetApi = {
  initialize: (options?: Record<string, unknown>, containerId?: string) => void;
  destroy: () => void;
};

declare global {
  interface Window {
    ChatbotWidget?: WidgetApi;
  }
}

let scriptPromise: Promise<void> | null = null;

function loadWidgetScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.ChatbotWidget) return Promise.resolve();
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[data-emly-widget-preview="1"]`,
    );
    if (existing) {
      if (window.ChatbotWidget) {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("emly-widget.js failed to load")),
        { once: true },
      );
      return;
    }
    const script = document.createElement("script");
    script.src = WIDGET_SRC;
    script.async = true;
    script.dataset.emlyWidgetPreview = "1";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("emly-widget.js failed to load"));
    document.head.appendChild(script);
  });
  return scriptPromise;
}

type Props = { slug: string; reloadKey: string | number };

export default function WidgetPreview({ slug, reloadKey }: Props) {
  useEffect(() => {
    let cancelled = false;
    loadWidgetScript()
      .then(() => {
        if (cancelled) return;
        const api = window.ChatbotWidget;
        if (!api) return;
        try {
          api.destroy();
        } catch {}
        api.initialize(
          { baseUrl: window.location.origin, botId: slug },
          CONTAINER_ID,
        );
      })
      .catch((err) => {
        console.error("[widget-preview]", err);
      });
    return () => {
      cancelled = true;
      try {
        window.ChatbotWidget?.destroy();
      } catch {}
    };
  }, [slug, reloadKey]);

  return null;
}
