"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useBot } from "@/components/BotShell";
import {
  ApiError,
  ChannelHealth,
  ChannelInstall,
  ChannelType,
  ChannelTypeInfo,
  api,
} from "@/lib/api";

type Banner = { kind: "ok" | "err"; message: string } | null;

export default function BotChannelsPage() {
  const { bot } = useBot();
  const [origin, setOrigin] = useState("https://your-deployment.example.com");
  const [copied, setCopied] = useState<string | null>(null);

  const [types, setTypes] = useState<ChannelTypeInfo[]>([]);
  const [installs, setInstalls] = useState<ChannelInstall[]>([]);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<Banner>(null);
  // Loader build id, used to pin `?v=` and `data-version` on the embed snippet.
  // Null until the first fetch resolves, in which case we degrade to an
  // unpinned URL — better than blocking the snippet on a network call.
  const [widgetVersion, setWidgetVersion] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") setOrigin(window.location.origin);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .widgetInfo()
      .then((info) => {
        if (!cancelled) setWidgetVersion(info.version);
      })
      .catch(() => {
        // Non-fatal — snippet still works without the ?v= pin.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [t, i] = await Promise.all([api.listChannelTypes(), api.listChannels(bot.slug)]);
      setTypes(t);
      setInstalls(i);
    } catch (e) {
      setBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setLoading(false);
    }
  }, [bot.slug]);

  useEffect(() => {
    if (!bot.slug) return;
    void refresh();
  }, [bot.slug, refresh]);

  // Surface the post-install redirect signal from `/channels/.../oauth/callback`.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    if (sp.get("installed") === "1") {
      const ch = sp.get("channel_id");
      setBanner({
        kind: "ok",
        message: ch ? `Installed (channel ${ch}).` : "Channel installed.",
      });
      const url = new URL(window.location.href);
      url.searchParams.delete("installed");
      url.searchParams.delete("channel_id");
      window.history.replaceState({}, "", url.toString());
      void refresh();
    }
  }, [refresh]);

  // Use bot.id (not slug) so a future rename doesn't break a customer's
  // pasted snippet. Backend route accepts both, but only id is stable.
  const widgetUrl = `${origin}/widget/${bot.id}/chat`;
  // ?v=<build-id> busts customer-side CDN caches on each loader release.
  // data-version on the tag is documentation — the URL is what pins.
  const widgetScriptUrl = widgetVersion
    ? `${origin}/emly-widget.js?v=${encodeURIComponent(widgetVersion)}`
    : `${origin}/emly-widget.js`;
  const previewUrl = `${origin}/widget-test.html?bot=${encodeURIComponent(bot.id)}`;

  const embedSnippet = widgetVersion
    ? `<script src="${widgetScriptUrl}"
        data-bot-id="${bot.id}"
        data-base-url="${origin}"
        data-version="${widgetVersion}"></script>`
    : `<script src="${widgetScriptUrl}"
        data-bot-id="${bot.id}"
        data-base-url="${origin}"></script>`;

  // CSP directives the customer's site needs. We name the origin
  // explicitly so the snippet is paste-ready; ``'unsafe-inline'`` for
  // ``style-src`` is unavoidable today because the loader injects its
  // own <style> tag at runtime via webpack style-loader.
  const cspDirectives = [
    `script-src ${origin}`,
    `connect-src ${origin}`,
    `img-src ${origin} data:`,
    `style-src 'unsafe-inline'`,
    `font-src data:`,
  ].join("; ");
  const cspHeader = `Content-Security-Policy: ${cspDirectives}`;

  const curlSnippet = `curl -X POST "${widgetUrl}" \\
  -H "Content-Type: application/json" \\
  -H "X-Emly-UserID: end-user-123" \\
  -H "X-Emly-SessionID: session-456" \\
  -d '{
    "user_id": "end-user-123",
    "session_id": "session-456",
    "timestamp": ${Math.floor(Date.now() / 1000)},
    "messages": [{"role": "user", "content": "hello"}],
    "stream": true
  }'`;

  function copy(text: string, label: string) {
    navigator.clipboard?.writeText(text);
    setCopied(label);
    setTimeout(() => setCopied((curr) => (curr === label ? null : curr)), 1500);
  }

  const typesByName = useMemo(() => {
    const out: Partial<Record<ChannelType, ChannelTypeInfo>> = {};
    for (const t of types) out[t.type] = t;
    return out;
  }, [types]);

  // Hand-off helpers for the developer who will install the embed.
  // - Print: opens the OS print dialog; the @media print stylesheet in
  //   globals.css hides shell chrome and emits a clean A4-ready document.
  // - Copy as Markdown: flattens the embed snippet + CSP directives + the
  //   per-bot HTTP endpoint into a single markdown blob that an admin can
  //   paste into a ticket or email.
  function buildMarkdownHandoff(): string {
    const lines: string[] = [];
    lines.push(`# Embed — ${bot.name}`);
    lines.push("");
    lines.push("## HTML snippet");
    lines.push("```html");
    lines.push(embedSnippet);
    lines.push("```");
    lines.push("");
    if (cspDirectives.length > 0) {
      lines.push("## Content-Security-Policy directives");
      lines.push("Add these to your existing `Content-Security-Policy` response header:");
      lines.push("```");
      for (const d of cspDirectives) lines.push(d);
      lines.push("```");
      lines.push("");
    }
    lines.push("## HTTP endpoint (for non-browser clients)");
    lines.push("```");
    lines.push(`POST ${widgetUrl}`);
    lines.push("```");
    lines.push("");
    lines.push(`Hosted test page: ${previewUrl}`);
    return lines.join("\n");
  }

  return (
    <>
      <div className="header">
        <h1>Channels — {bot.name}</h1>
        <div className="row print-hidden" style={{ gap: 8 }}>
          <button
            className="ghost compact"
            onClick={() => copy(buildMarkdownHandoff(), "handoff")}
            title="Copy embed snippet + CSP + endpoint as a single markdown bundle"
          >
            {copied === "handoff" ? "Copied" : "Copy as Markdown"}
          </button>
          <button
            className="ghost compact"
            onClick={() => window.print()}
            title="Open the print dialog with shell chrome hidden"
          >
            Print embed instructions
          </button>
        </div>
      </div>

      {banner && (
        <div
          className={banner.kind === "ok" ? "banner" : "banner warning"}
          style={{ marginTop: 16, marginBottom: 0 }}
        >
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <div>{banner.message}</div>
            <button className="ghost" onClick={() => setBanner(null)}>
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Web widget */}
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Web widget</h2>
        <p className="muted">
          Paste this snippet into your site&apos;s HTML. The loader injects a
          chat launcher in the corner; clicking it opens the chat widget
          wired to this bot.
        </p>

        <div style={{ marginTop: 12 }}>
          <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", marginBottom: 4 }}>
            Embed snippet
          </div>
          <pre className="snippet">{embedSnippet}</pre>
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 8, gap: 8 }}>
            <a
              href={previewUrl}
              target="_blank"
              rel="noreferrer"
              className="ghost"
              style={{ display: "inline-block", padding: "8px 16px", borderRadius: 6, border: "1px solid var(--panel-border)", textDecoration: "none" }}
            >
              Test in new tab
            </a>
            <button className="ghost" onClick={() => copy(embedSnippet, "embed")}>
              {copied === "embed" ? "Copied" : "Copy snippet"}
            </button>
          </div>
          {widgetVersion && (
            <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
              Pinned to loader build <code>{widgetVersion}</code>. Omit{" "}
              <code>?v=</code> to always pull the latest deployed version.
            </div>
          )}
        </div>

        <SharePreview
          previewUrl={previewUrl}
          qrSrc={api.botPreviewQrUrl(bot.slug)}
          copied={copied}
          copy={copy}
        />

        <CspGuidance
          origin={origin}
          cspHeader={cspHeader}
          cspDirectives={cspDirectives}
          copied={copied}
          copy={copy}
        />

        <details style={{ marginTop: 20 }}>
          <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>
            HTTP integration contract (for non-browser clients)
          </summary>
          <div style={{ marginTop: 12 }}>
            <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", marginBottom: 4 }}>
              Endpoint
            </div>
            <div className="row" style={{ alignItems: "center", gap: 8 }}>
              <code style={{ flex: 1, padding: "8px 12px", background: "var(--paper)", borderRadius: 6, border: "1px solid var(--panel-border)" }}>
                POST {widgetUrl}
              </code>
              <button className="ghost" onClick={() => copy(widgetUrl, "widgetUrl")}>
                {copied === "widgetUrl" ? "Copied" : "Copy URL"}
              </button>
            </div>

            <div style={{ marginTop: 16 }}>
              <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", marginBottom: 4 }}>
                Curl example
              </div>
              <pre className="snippet">{curlSnippet}</pre>
              <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
                <button className="ghost" onClick={() => copy(curlSnippet, "curl")}>
                  {copied === "curl" ? "Copied" : "Copy curl"}
                </button>
              </div>
            </div>
          </div>
        </details>
      </div>

      {/* Installed channels */}
      <InstalledChannels
        installs={installs.filter((c) => c.type !== "web_widget")}
        loading={loading}
        slug={bot.slug}
        onChange={refresh}
        onBanner={setBanner}
        copied={copied}
        copy={copy}
      />

      {/* Connect new channel */}
      <ConnectChannels
        types={typesByName}
        installs={installs}
        slug={bot.slug}
        botId={bot.id}
        onChange={refresh}
        onBanner={setBanner}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Share preview — QR + copy-link for the hosted /widget-test.html page.
// ---------------------------------------------------------------------------
//
// The hosted preview page is itself public — anyone with the link can open it
// and chat as a real end user (subject to the bot's `widget_allowed_origins`
// allowlist, which already gates the deployment's own origin). We surface
// that posture in the body copy so admins don't assume the link is private.
function SharePreview({
  previewUrl,
  qrSrc,
  copied,
  copy,
}: {
  previewUrl: string;
  qrSrc: string;
  copied: string | null;
  copy: (text: string, label: string) => void;
}) {
  return (
    <div
      style={{
        marginTop: 20,
        padding: 16,
        borderRadius: 8,
        border: "1px solid var(--panel-border)",
        background: "var(--paper)",
      }}
    >
      <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", marginBottom: 8 }}>
        Share preview
      </div>
      <div
        className="row"
        style={{ alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}
      >
        <img
          src={qrSrc}
          alt="QR code for the share-preview page"
          width={144}
          height={144}
          style={{
            width: 144,
            height: 144,
            background: "white",
            padding: 8,
            borderRadius: 6,
            border: "1px solid var(--panel-border)",
            flexShrink: 0,
          }}
        />
        <div style={{ flex: 1, minWidth: 220 }}>
          <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            Scan or share this link to preview the bot in a hosted page —
            useful for showing a stakeholder on mobile without pasting the
            snippet on their site.
          </p>
          <div className="row" style={{ alignItems: "center", gap: 8 }}>
            <code
              style={{
                flex: 1,
                padding: "6px 10px",
                background: "var(--surface)",
                borderRadius: 6,
                border: "1px solid var(--panel-border)",
                fontSize: 12,
                wordBreak: "break-all",
              }}
            >
              {previewUrl}
            </code>
            <button className="ghost" onClick={() => copy(previewUrl, "previewUrl")}>
              {copied === "previewUrl" ? "Copied" : "Copy link"}
            </button>
          </div>
          <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>
            The preview page is public — anyone with the link can chat as
            an end user. Chat itself is still gated by the bot&apos;s{" "}
            <em>Allowed widget origins</em> list (Config → Limits).
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CSP guidance — directives the customer's site needs to load the widget.
// ---------------------------------------------------------------------------
//
// The loader injects styles at runtime via webpack style-loader, which forces
// `style-src 'unsafe-inline'` on the customer side. Until the loader switches
// to nonces or extracted CSS, there's no cleaner story to recommend.
function CspGuidance({
  origin,
  cspHeader,
  cspDirectives,
  copied,
  copy,
}: {
  origin: string;
  cspHeader: string;
  cspDirectives: string;
  copied: string | null;
  copy: (text: string, label: string) => void;
}) {
  return (
    <details style={{ marginTop: 20 }}>
      <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>
        Content-Security-Policy directives
      </summary>
      <div style={{ marginTop: 12 }}>
        <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
          If the customer&apos;s site sets a Content-Security-Policy, it must
          allow the widget to load scripts and call back to{" "}
          <code>{origin}</code>. Paste these directives into the existing
          policy (merge if there&apos;s already a <code>script-src</code>,
          <code> connect-src</code>, etc.).
        </p>
        <pre className="snippet">{cspHeader}</pre>
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 8, gap: 8 }}>
          <button className="ghost" onClick={() => copy(cspDirectives, "cspDirectives")}>
            {copied === "cspDirectives" ? "Copied" : "Copy directives"}
          </button>
          <button className="ghost" onClick={() => copy(cspHeader, "cspHeader")}>
            {copied === "cspHeader" ? "Copied" : "Copy full header"}
          </button>
        </div>
        <ul
          className="muted"
          style={{ fontSize: 12, marginTop: 12, paddingLeft: 18, lineHeight: 1.7 }}
        >
          <li>
            <code>script-src {origin}</code> — needed to load{" "}
            <code>emly-widget.js</code>.
          </li>
          <li>
            <code>connect-src {origin}</code> — XHR/fetch + streaming chat
            requests to <code>/widget/&lt;id&gt;/*</code>.
          </li>
          <li>
            <code>img-src {origin} data:</code> — bot avatar plus a few
            inline data-URI icons rendered by the launcher.
          </li>
          <li>
            <code>style-src &apos;unsafe-inline&apos;</code> — the loader injects
            its CSS at runtime; until it&apos;s ported to a nonce-able
            extracted stylesheet, this is unavoidable on the customer
            side.
          </li>
          <li>
            <code>font-src data:</code> — only if the customer&apos;s policy
            already forbids data-URI fonts; the launcher inlines a small
            icon font.
          </li>
        </ul>
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Installed channels list
// ---------------------------------------------------------------------------
function InstalledChannels({
  installs,
  loading,
  slug,
  onChange,
  onBanner,
  copied,
  copy,
}: {
  installs: ChannelInstall[];
  loading: boolean;
  slug: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
  copied: string | null;
  copy: (text: string, label: string) => void;
}) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0 }}>Installed channels</h2>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : installs.length === 0 ? (
        <p className="muted">No channels installed yet — connect one below.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {installs.map((install) => (
            <InstallRow
              key={install.id}
              install={install}
              slug={slug}
              onChange={onChange}
              onBanner={onBanner}
              copied={copied}
              copy={copy}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function InstallRow({
  install,
  slug,
  onChange,
  onBanner,
  copied,
  copy,
}: {
  install: ChannelInstall;
  slug: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
  copied: string | null;
  copy: (text: string, label: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<ChannelHealth | null>(null);
  const [showSecrets, setShowSecrets] = useState(false);

  async function runHealth() {
    setBusy(true);
    try {
      const res = await api.channelHealth(slug, install.id);
      setHealth(res);
    } catch (e) {
      setHealth({ ok: false, info: { error: errorMessage(e) } });
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive() {
    setBusy(true);
    try {
      await api.setChannelActive(slug, install.id, !install.is_active);
      onChange();
    } catch (e) {
      onBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  async function softDelete() {
    if (!confirm(`Disable this ${install.type} install?`)) return;
    setBusy(true);
    try {
      await api.deleteChannel(slug, install.id, false);
      onBanner({ kind: "ok", message: "Channel disabled (soft delete)." });
      onChange();
    } catch (e) {
      onBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  async function hardDelete() {
    if (!confirm(`Permanently uninstall and revoke this ${install.type} channel? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await api.deleteChannel(slug, install.id, true);
      onBanner({ kind: "ok", message: "Channel removed and revoked on the platform side." });
      onChange();
    } catch (e) {
      onBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: 12,
        background: install.is_active ? "transparent" : "var(--fg-soft)",
        opacity: install.is_active ? 1 : 0.85,
      }}
    >
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600 }}>
            {prettyType(install.type)}{" "}
            <span className="muted" style={{ fontWeight: 400, fontSize: 13 }}>
              {install.display_name ?? install.external_id ?? install.id}
            </span>
          </div>
          <div className="muted" style={{ fontSize: 12 }}>
            {install.is_active ? "Active" : "Disabled"} ·
            {" "}created {new Date(install.created_at).toLocaleString()}
            {install.secrets_rotated_at && (
              <> · secrets rotated {new Date(install.secrets_rotated_at).toLocaleString()}</>
            )}
          </div>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <button className="ghost" onClick={runHealth} disabled={busy}>
            Health
          </button>
          <button className="ghost" onClick={toggleActive} disabled={busy}>
            {install.is_active ? "Disable" : "Enable"}
          </button>
          {install.is_active ? (
            <button className="ghost" onClick={softDelete} disabled={busy}>
              Delete
            </button>
          ) : (
            <button className="ghost danger" onClick={hardDelete} disabled={busy} style={{ borderColor: "var(--error)", color: "var(--error)" }}>
              Uninstall
            </button>
          )}
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", marginBottom: 4 }}>
          Webhook URL
        </div>
        <div className="row" style={{ alignItems: "center", gap: 8 }}>
          <code style={{ flex: 1, padding: "6px 10px", background: "var(--paper)", borderRadius: 6, border: "1px solid var(--panel-border)", fontSize: 12, wordBreak: "break-all" }}>
            {install.webhook_url}
          </code>
          <button className="ghost" onClick={() => copy(install.webhook_url, `wh-${install.id}`)}>
            {copied === `wh-${install.id}` ? "Copied" : "Copy"}
          </button>
        </div>
        {install.type === "telegram" && (
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            Telegram&apos;s webhook is auto-registered via <code>setWebhook</code> on install — no manual step.
          </div>
        )}
        {install.type === "slack" && (
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            Set this URL as the Slack app&apos;s <em>Event Subscriptions</em> request URL.
          </div>
        )}
        {install.type === "google_chat" && (
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            Paste this URL into your Chat app&apos;s <em>HTTP endpoint URL</em> and{" "}
            <em>Authentication audience</em> — both fields, exact match.
          </div>
        )}
      </div>

      {health && (
        <div className={health.ok ? "banner" : "banner warning"} style={{ marginTop: 10, marginBottom: 0 }}>
          <div className="eyebrow" style={{ marginBottom: 4 }}>Healthcheck</div>
          <div style={{ fontSize: 13 }}>{health.ok ? "✓ OK" : "✗ Failed"}</div>
          <pre className="snippet" style={{ marginTop: 6, fontSize: 11 }}>
            {JSON.stringify(health.info, null, 2)}
          </pre>
        </div>
      )}

      <details style={{ marginTop: 10 }} open={showSecrets} onToggle={(e) => setShowSecrets((e.target as HTMLDetailsElement).open)}>
        <summary className="muted" style={{ cursor: "pointer", fontSize: 12 }}>
          Stored secrets (redacted)
        </summary>
        <pre className="snippet" style={{ marginTop: 6, fontSize: 11 }}>
          {JSON.stringify(install.secrets_redacted, null, 2)}
        </pre>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connect a channel
// ---------------------------------------------------------------------------
function ConnectChannels({
  types,
  installs,
  slug,
  botId,
  onChange,
  onBanner,
}: {
  types: Partial<Record<ChannelType, ChannelTypeInfo>>;
  installs: ChannelInstall[];
  slug: string;
  botId: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
}) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0 }}>Connect a channel</h2>
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 16 }}>
        <SlackPanel
          info={types.slack}
          slug={slug}
          onChange={onChange}
          onBanner={onBanner}
        />
        <TelegramPanel
          info={types.telegram}
          slug={slug}
          onChange={onChange}
          onBanner={onBanner}
        />
        <WhatsAppPanel
          info={types.whatsapp_cloud}
          slug={slug}
          onChange={onChange}
          onBanner={onBanner}
        />
        <TeamsPanel
          info={types.teams}
          slug={slug}
          onChange={onChange}
          onBanner={onBanner}
        />
        <GoogleChatPanel
          info={types.google_chat}
          slug={slug}
          onChange={onChange}
          onBanner={onBanner}
        />
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
        bot id: <code>{botId}</code>
      </div>
    </div>
  );
}

function WhatsAppPanel({
  info,
  slug,
  onChange,
  onBanner,
}: {
  info: ChannelTypeInfo | undefined;
  slug: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [accessToken, setAccessToken] = useState("");
  const [phoneNumberId, setPhoneNumberId] = useState("");
  const [wabaId, setWabaId] = useState("");
  const [verifyToken, setVerifyToken] = useState("");
  const [displayPhoneNumber, setDisplayPhoneNumber] = useState("");
  const [connectError, setConnectError] = useState<string | null>(null);

  if (!info) return null;

  async function submit() {
    if (!accessToken.trim() || !phoneNumberId.trim() || !wabaId.trim() || !verifyToken.trim()) return;
    setBusy(true);
    setConnectError(null);
    try {
      await api.createChannel(slug, {
        type: "whatsapp_cloud",
        secrets: {
          access_token: accessToken.trim(),
          phone_number_id: phoneNumberId.trim(),
          waba_id: wabaId.trim(),
          verify_token: verifyToken.trim(),
          display_phone_number: displayPhoneNumber.trim(),
        },
      });
      onBanner({ kind: "ok", message: "WhatsApp connected — webhook subscription requested." });
      setOpen(false);
      setAccessToken("");
      setPhoneNumberId("");
      setWabaId("");
      setVerifyToken("");
      setDisplayPhoneNumber("");
      onChange();
    } catch (e) {
      const msg = errorMessage(e);
      onBanner({ kind: "err", message: msg });
      setConnectError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ border: "1px solid var(--panel-border)", borderRadius: 8, padding: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>WhatsApp (Meta Cloud)</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Static install with a long-lived system-user token from Meta Business Manager.
            Outbound calls Graph API; inbound is HMAC-verified with{" "}
            <code>META_APP_SECRET</code> (deployment-wide env var).
          </div>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="ghost">
          {open ? "Cancel" : "Connect WhatsApp"}
        </button>
      </div>

      <SetupGuide>
        <li>
          In{" "}
          <a href="https://business.facebook.com/" target="_blank" rel="noreferrer">
            Meta Business Manager
          </a>
          , create (or pick) a Meta App at{" "}
          <a href="https://developers.facebook.com/apps" target="_blank" rel="noreferrer">
            developers.facebook.com/apps
          </a>{" "}
          and add the <em>WhatsApp</em> product to it.
        </li>
        <li>
          Open <em>WhatsApp → API Setup</em>. Note the <em>Phone number ID</em> and the{" "}
          <em>WhatsApp Business Account ID</em> (WABA ID).
        </li>
        <li>
          In <em>Business Settings → System Users</em>, create a system user. Grant it{" "}
          <code>whatsapp_business_management</code> and{" "}
          <code>whatsapp_business_messaging</code> on the WABA. Generate a long-lived access
          token and copy it.
        </li>
        <li>
          On the Meta App&apos;s <em>Settings → Basic</em> page, copy the{" "}
          <em>App Secret</em>. Set it on this deployment as the{" "}
          <code>META_APP_SECRET</code> env var (one-time, deployment-wide — all WhatsApp
          installs share it for HMAC signature verification).
        </li>
        <li>
          Pick any random string as your <em>verify token</em> (e.g. a UUID). You&apos;ll paste
          the same value into both this form and Meta&apos;s webhook config.
        </li>
        <li>
          Fill the form below and click <em>Connect</em>. A webhook URL appears on the new
          install row above.
        </li>
        <li>
          Back in the Meta App, open <em>WhatsApp → Configuration → Webhooks</em>. Paste the
          webhook URL as <em>Callback URL</em> and the same string as <em>Verify token</em>;
          save. Subscribe to the <code>messages</code> field.
        </li>
        <li>
          Send a test message to the WhatsApp business number — replies should arrive.
        </li>
      </SetupGuide>

      {open && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <SecretField label="System-user access token" value={accessToken} onChange={setAccessToken} type="password" placeholder="EAAB…" />
          <SecretField label="Phone number ID" value={phoneNumberId} onChange={setPhoneNumberId} placeholder="106540352242922" />
          <SecretField label="WABA ID" value={wabaId} onChange={setWabaId} placeholder="102290129340398" />
          <SecretField
            label="Verify token (admin-chosen string used in the GET handshake)"
            value={verifyToken}
            onChange={setVerifyToken}
            type="password"
            placeholder="any random string you'll paste into Meta's webhook config"
          />
          <SecretField label="Display phone number (optional)" value={displayPhoneNumber} onChange={setDisplayPhoneNumber} placeholder="+15550001111" />
          <div className="muted" style={{ fontSize: 12 }}>
            After save, copy the webhook URL from the install row and paste it into the
            Meta App&apos;s WhatsApp webhook config along with this verify_token.
            Set <code>META_APP_SECRET</code> on the deployment env.
          </div>
          {connectError && (
            <WhatsAppErrorPanel
              error={connectError}
              phoneNumberId={phoneNumberId.trim()}
              wabaId={wabaId.trim()}
              onDismiss={() => setConnectError(null)}
            />
          )}
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button onClick={submit} disabled={busy || !accessToken.trim() || !phoneNumberId.trim() || !wabaId.trim() || !verifyToken.trim()}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function WhatsAppErrorPanel({
  error,
  phoneNumberId,
  wabaId,
  onDismiss,
}: {
  error: string;
  phoneNumberId: string;
  wabaId: string;
  onDismiss: () => void;
}) {
  const hints = classifyWhatsAppError(error);
  const pid = phoneNumberId || "<PHONE_NUMBER_ID>";
  const debugCmds = [
    `# 1. Verify the access token and inspect its scopes`,
    `curl -s "https://graph.facebook.com/v21.0/debug_token?input_token=$TOKEN&access_token=$TOKEN" | jq`,
    ``,
    `# 2. Confirm the Phone number ID resolves to a phone-number node`,
    `curl -s "https://graph.facebook.com/v21.0/${pid}?fields=display_phone_number,verified_name" \\`,
    `  -H "Authorization: Bearer $TOKEN" | jq`,
    ``,
    ...(wabaId
      ? [
          `# 3. List phone numbers attached to the WABA`,
          `curl -s "https://graph.facebook.com/v21.0/${wabaId}/phone_numbers" \\`,
          `  -H "Authorization: Bearer $TOKEN" | jq`,
          ``,
        ]
      : []),
    `# 4. Reproduce the failing call (GET, non-mutating)`,
    `curl -s "https://graph.facebook.com/v21.0/${wabaId || "<WABA_ID>"}/subscribed_apps" \\`,
    `  -H "Authorization: Bearer $TOKEN" | jq`,
  ].join("\n");
  const scriptCmd = `./test_wa.sh -t "$TOKEN" -p "${pid}"${wabaId ? ` -w "${wabaId}"` : ""}`;

  async function copyToClipboard(text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // best effort — fall through silently
    }
  }

  return (
    <div
      role="alert"
      style={{
        border: "1px solid var(--error)",
        background: "var(--error-soft)",
        borderRadius: 8,
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ fontWeight: 600, color: "var(--error)" }}>Connection failed</div>
        <button className="ghost" onClick={onDismiss} aria-label="Dismiss error">
          Dismiss
        </button>
      </div>

      <div>
        <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
          Raw error from Meta
        </div>
        <pre
          style={{
            margin: 0,
            padding: 10,
            background: "var(--panel-bg, rgba(0,0,0,0.04))",
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
            fontSize: 12,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {error}
        </pre>
      </div>

      <div>
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
          Likely causes &amp; fixes
        </div>
        <ol style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 8 }}>
          {hints.map((h, i) => (
            <li key={i} style={{ fontSize: 13 }}>
              <div style={{ fontWeight: 600 }}>{h.title}</div>
              <div className="muted" style={{ fontSize: 12 }}>{h.body}</div>
            </li>
          ))}
        </ol>
      </div>

      <div>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <div style={{ fontWeight: 600, fontSize: 13 }}>
            Reproduce on your terminal
          </div>
          <button className="ghost" onClick={() => copyToClipboard(debugCmds)}>
            Copy commands
          </button>
        </div>
        <pre
          style={{
            margin: 0,
            padding: 10,
            background: "var(--panel-bg, rgba(0,0,0,0.04))",
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
            fontSize: 11,
            overflowX: "auto",
            lineHeight: 1.45,
          }}
        >
{`export TOKEN='<paste your access token>'

${debugCmds}`}
        </pre>
        <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
          Or run the bundled one-shot diagnostic from the repo root — it runs all of the
          above and prints the most likely root cause:
        </div>
        <pre
          style={{
            margin: "6px 0 0 0",
            padding: 8,
            background: "var(--panel-bg, rgba(0,0,0,0.04))",
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
            fontSize: 11,
          }}
        >
{`export TOKEN='<paste your access token>'
${scriptCmd}`}
        </pre>
      </div>
    </div>
  );
}

type WhatsAppHint = { title: string; body: string };

function classifyWhatsAppError(raw: string): WhatsAppHint[] {
  const e = raw.toLowerCase();
  const hints: WhatsAppHint[] = [];
  const has = (s: string) => e.includes(s);

  // Code-based fast paths (the backend now stamps "code=NNN, subcode=NNN" into the detail).
  if (has("code=190") || has("invalid oauth") || has("session has expired") || has("access token")) {
    hints.push({
      title: "The access token is invalid or expired",
      body:
        "Regenerate a long-lived system-user token in Business Settings → System Users → <user> → Generate New Token. " +
        "Tokens minted before the system user was assigned to the WABA also fail this way — assign first, then regenerate.",
    });
  }
  if (has("code=200") || has("missing permissions") || has("(#10)") || has("(#200)") || has("permission")) {
    hints.push({
      title: "Token is missing required WhatsApp scopes",
      body:
        "The system user needs whatsapp_business_management and whatsapp_business_messaging granted on the WABA. " +
        "Open Business Settings → System Users → Assign Assets, pick the WABA, enable both permissions, then regenerate the token.",
    });
  }
  if (has("subcode=33") || has("does not exist") || has("unsupported post request") || has("cannot be loaded")) {
    hints.push({
      title: "The WABA ID or Phone number ID is wrong or unreachable for this token",
      body:
        "Meta subscribes webhooks at the WABA level (POST /{waba_id}/subscribed_apps), not the phone-number level. " +
        "Confirm the WABA ID is correct — open WhatsApp → API Setup in the Meta App dashboard and copy the " +
        "'WhatsApp Business Account ID'. If the IDs are correct, the system user that minted the token has not " +
        "been assigned to the WABA — fix that in Business Settings → Accounts → WhatsApp Accounts → Add People.",
    });
  }
  if (has("code=100") && !has("subcode=33")) {
    hints.push({
      title: "Meta returned a generic 'invalid parameter' (code=100)",
      body:
        "This usually means either the Phone number ID is wrong OR the token lacks whatsapp_business_management. " +
        "Run the diagnostic curl below to see which is the case — it'll fail on step 1 (token) or step 2 (ID lookup).",
    });
  }
  if (has("status 401") || has("status 403")) {
    hints.push({
      title: "Meta refused the call entirely (401/403)",
      body:
        "Almost always a token problem: expired, wrong app, or never granted WhatsApp scopes. " +
        "Regenerate the system-user token and confirm both whatsapp_business_management and " +
        "whatsapp_business_messaging are listed under its scopes.",
    });
  }

  // Always-on tail: generic checklist if nothing matched, or as a final sanity prompt.
  if (hints.length === 0) {
    hints.push({
      title: "Unrecognised error — run the diagnostic below",
      body:
        "The reproduction commands below will pinpoint whether the token, the Phone number ID, " +
        "or the WABA assignment is the broken piece.",
    });
  }
  hints.push({
    title: "Also double-check META_APP_SECRET",
    body:
      "If install succeeds but inbound webhooks still 403, the deployment is missing the META_APP_SECRET env var " +
      "(used for HMAC verification of every inbound payload). Set it from the Meta App → Settings → Basic page.",
  });
  return hints;
}

function TeamsPanel({
  info,
  slug,
  onChange,
  onBanner,
}: {
  info: ChannelTypeInfo | undefined;
  slug: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [appId, setAppId] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [appType, setAppType] = useState<"MultiTenant" | "SingleTenant">("MultiTenant");

  if (!info) return null;

  async function submit() {
    if (!appId.trim() || !appPassword.trim()) return;
    setBusy(true);
    try {
      const secrets: Record<string, unknown> = {
        app_id: appId.trim(),
        app_password: appPassword.trim(),
        app_type: appType,
      };
      if (appType === "SingleTenant" && tenantId.trim()) {
        secrets.tenant_id = tenantId.trim();
      }
      await api.createChannel(slug, { type: "teams", secrets });
      onBanner({ kind: "ok", message: "Teams app credentials saved. Paste the webhook URL into your Teams app manifest." });
      setOpen(false);
      setAppId("");
      setAppPassword("");
      setTenantId("");
      setAppType("MultiTenant");
      onChange();
    } catch (e) {
      onBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ border: "1px solid var(--panel-border)", borderRadius: 8, padding: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Microsoft Teams</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Bot Framework AAD client_credentials. Register an app at{" "}
            <a href="https://dev.teams.microsoft.com/apps" target="_blank" rel="noreferrer">dev.teams.microsoft.com</a>{" "}
            and paste the app id + password. Sideload the Teams manifest into your tenant separately.
          </div>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="ghost">
          {open ? "Cancel" : "Connect Teams"}
        </button>
      </div>

      <SetupGuide>
        <li>
          Open{" "}
          <a href="https://dev.teams.microsoft.com/apps" target="_blank" rel="noreferrer">
            dev.teams.microsoft.com/apps
          </a>{" "}
          and create a new app (or open an existing one). This wraps an Azure AD app
          registration that the Bot Framework will use.
        </li>
        <li>
          In{" "}
          <a href="https://portal.azure.com/" target="_blank" rel="noreferrer">
            Azure Portal
          </a>{" "}
          → <em>App registrations</em>, find the corresponding app. Decide between
          multi-tenant (any org can install) and single-tenant (your org only). For
          single-tenant, copy the <em>Directory (tenant) ID</em>.
        </li>
        <li>
          On the app registration page, copy the <em>Application (client) ID</em> — this is
          the <em>App ID</em> field below.
        </li>
        <li>
          Open <em>Certificates &amp; secrets</em> → <em>New client secret</em>. Copy the
          secret <em>Value</em> immediately (it&apos;s only shown once). This is the{" "}
          <em>App password</em> field below.
        </li>
        <li>
          Fill the form below — App ID, App password, tenant type, and tenant ID if
          single-tenant. Click <em>Connect</em>. We mint a Bot Framework AAD token to
          validate the credentials before saving.
        </li>
        <li>
          Copy the webhook URL from the install row above. In Azure Portal → your bot
          resource → <em>Configuration</em>, paste it as the <em>Messaging endpoint</em>.
        </li>
        <li>
          Back in dev.teams.microsoft.com, ensure your app has a <em>Bot</em> capability
          configured. Then <em>Publish to org</em> or <em>Download manifest</em> as a zip.
        </li>
        <li>
          In{" "}
          <a
            href="https://admin.teams.microsoft.com/policies/manage-apps"
            target="_blank"
            rel="noreferrer"
          >
            Teams admin center
          </a>{" "}
          → <em>Manage apps</em> → <em>Upload an app</em>, upload the manifest zip. Once
          approved for your tenant, users can add the bot from the Teams app catalog.
        </li>
      </SetupGuide>

      {open && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <SecretField label="App ID (MicrosoftAppId)" value={appId} onChange={setAppId} placeholder="00000000-0000-0000-0000-000000000000" />
          <SecretField label="App password (MicrosoftAppPassword)" value={appPassword} onChange={setAppPassword} type="password" />
          <label className="muted" style={{ fontSize: 12 }}>App type</label>
          <select
            value={appType}
            onChange={(e) => setAppType(e.target.value as "MultiTenant" | "SingleTenant")}
            style={{ padding: "8px 10px", background: "var(--paper)", color: "var(--text)", border: "1px solid var(--panel-border)", borderRadius: 6 }}
          >
            <option value="MultiTenant">Multi-tenant</option>
            <option value="SingleTenant">Single-tenant</option>
          </select>
          {appType === "SingleTenant" && (
            <SecretField label="Tenant ID (single-tenant apps only)" value={tenantId} onChange={setTenantId} placeholder="aad-tenant-guid" />
          )}
          <div className="muted" style={{ fontSize: 12 }}>
            On save we mint a Bot Framework AAD token to validate the credentials. Inbound JWTs
            are verified against Bot Framework&apos;s JWKS; <code>serviceUrl</code> is allow-listed
            to <code>smba.trafficmanager.net</code>.
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button onClick={submit} disabled={busy || !appId.trim() || !appPassword.trim()}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function GoogleChatPanel({
  info,
  slug,
  onChange,
  onBanner,
}: {
  info: ChannelTypeInfo | undefined;
  slug: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serviceAccountJson, setServiceAccountJson] = useState("");

  if (!info) return null;

  async function submit() {
    if (!serviceAccountJson.trim()) return;
    setBusy(true);
    let parsed: Record<string, unknown>;
    let saEmail = "";
    try {
      parsed = JSON.parse(serviceAccountJson);
      saEmail = String(parsed.client_email ?? "");
      if (!saEmail) throw new Error("service account JSON missing client_email");
    } catch (e) {
      onBanner({ kind: "err", message: `Service account JSON invalid: ${errorMessage(e)}` });
      setBusy(false);
      return;
    }
    try {
      await api.createChannel(slug, {
        type: "google_chat",
        secrets: {
          service_account_json: parsed,
          service_account_email: saEmail,
        },
      });
      onBanner({
        kind: "ok",
        message:
          "Google Chat connected. Copy the webhook URL from the row above and paste it into your Chat app config.",
      });
      setOpen(false);
      setServiceAccountJson("");
      onChange();
    } catch (e) {
      onBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ border: "1px solid var(--panel-border)", borderRadius: 8, padding: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Google Chat</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Service-account install. Create an SA in your GCP project, enable the Chat API,
            paste the JSON key here. We auto-generate the webhook URL after install — copy it
            from the row above and paste it into your Chat app config (it&apos;s also used as
            the inbound JWT audience).
          </div>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="ghost">
          {open ? "Cancel" : "Connect Google Chat"}
        </button>
      </div>

      <SetupGuide>
        <li>
          Open{" "}
          <a href="https://console.cloud.google.com/" target="_blank" rel="noreferrer">
            Google Cloud Console
          </a>{" "}
          and pick (or create) a project that will own the Chat app.
        </li>
        <li>
          <em>APIs &amp; Services → Library</em> → search for <em>Google Chat API</em> and
          enable it.
        </li>
        <li>
          <em>IAM &amp; Admin → Service Accounts</em> → <em>Create service account</em>. Name
          it (e.g. <code>chat-bot</code>). No project roles are required for Chat itself.
          Open the service account → <em>Keys</em> → <em>Add key → Create new key → JSON</em>{" "}
          and save the downloaded file.
        </li>
        <li>
          Paste the JSON key below and click <em>Connect</em>. The install row that appears
          will show the canonical webhook URL — shape{" "}
          <code>{"<deployment>/channels/google_chat/<channel_id>/events"}</code>. Copy it.
        </li>
        <li>
          Open <em>APIs &amp; Services → Google Chat API → Configuration</em> and fill in:
          <ul style={{ marginTop: 4, paddingLeft: 18 }}>
            <li>App name, avatar URL, description, interactive features.</li>
            <li>
              <em>Connection settings</em>: <em>HTTP endpoint URL</em> = the webhook URL you
              just copied.
            </li>
            <li>
              <em>Authentication audience</em>: <em>HTTP endpoint URL</em> (same value — the
              webhook URL is the JWT audience and must match exactly).
            </li>
            <li>
              <em>Service account</em>: the <code>client_email</code> from the JSON key.
            </li>
            <li>
              <em>Visibility</em>: who can install this Chat app (your domain, specific users,
              or DMs only).
            </li>
          </ul>
        </li>
        <li>
          In Google Chat, search the app by name and add it to a DM or space. Send a message
          to test.
        </li>
      </SetupGuide>

      {open && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <label className="muted" style={{ fontSize: 12 }}>Service account JSON</label>
          <textarea
            value={serviceAccountJson}
            onChange={(e) => setServiceAccountJson(e.target.value)}
            placeholder='{"type": "service_account", "project_id": …, "client_email": …, "private_key": …}'
            rows={8}
            style={{
              width: "100%",
              padding: "8px 10px",
              background: "var(--paper)",
              color: "var(--text)",
              border: "1px solid var(--panel-border)",
              borderRadius: 6,
              fontFamily: "monospace",
              fontSize: 12,
            }}
          />
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button onClick={submit} disabled={busy || !serviceAccountJson.trim()}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SetupGuide({ children }: { children: React.ReactNode }) {
  return (
    <details style={{ marginTop: 10 }}>
      <summary
        className="muted"
        style={{ cursor: "pointer", fontSize: 12, userSelect: "none" }}
      >
        Setup guide — step-by-step
      </summary>
      <ol
        style={{
          marginTop: 8,
          marginBottom: 0,
          paddingLeft: 20,
          fontSize: 12,
          lineHeight: 1.7,
          color: "var(--text-muted)",
        }}
      >
        {children}
      </ol>
    </details>
  );
}

function SecretField({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="muted" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
        {label}
      </label>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: "100%",
          padding: "8px 10px",
          background: "var(--paper)",
          color: "var(--text)",
          border: "1px solid var(--panel-border)",
          borderRadius: 6,
          fontFamily: "monospace",
          fontSize: 13,
        }}
      />
    </div>
  );
}

function SlackPanel({
  info,
  slug,
  onChange,
  onBanner,
}: {
  info: ChannelTypeInfo | undefined;
  slug: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [accessToken, setAccessToken] = useState("");
  const [signingSecret, setSigningSecret] = useState("");

  if (!info) return null;

  async function submit() {
    if (!accessToken.trim() || !signingSecret.trim()) return;
    setBusy(true);
    try {
      await api.createChannel(slug, {
        type: "slack",
        secrets: {
          access_token: accessToken.trim(),
          signing_secret: signingSecret.trim(),
        },
      });
      onBanner({
        kind: "ok",
        message: "Slack workspace connected. Paste the webhook URL into your Slack app's Event Subscriptions config.",
      });
      setOpen(false);
      setAccessToken("");
      setSigningSecret("");
      onChange();
    } catch (e) {
      onBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ border: "1px solid var(--panel-border)", borderRadius: 8, padding: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Slack</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Per-bot Slack app. Create one at{" "}
            <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer">api.slack.com/apps</a>,
            install it to your workspace, paste the bot token + signing secret here.
          </div>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="ghost">
          {open ? "Cancel" : "Connect Slack"}
        </button>
      </div>

      <SetupGuide>
        <li>
          Go to{" "}
          <a href="https://api.slack.com/apps" target="_blank" rel="noreferrer">
            api.slack.com/apps
          </a>{" "}
          → <em>Create New App</em> → <em>From scratch</em>. Name it and pick the workspace
          you want to install it into.
        </li>
        <li>
          Open <em>OAuth &amp; Permissions</em> → <em>Bot Token Scopes</em> and add:{" "}
          <code>app_mentions:read</code>, <code>chat:write</code>, <code>im:history</code>,{" "}
          <code>im:read</code>, <code>im:write</code>, <code>channels:history</code>,{" "}
          <code>users:read</code>.
        </li>
        <li>
          Click <em>Install to Workspace</em> at the top of the same page and authorize. Copy
          the <em>Bot User OAuth Token</em> (starts with <code>xoxb-</code>) into the field below.
        </li>
        <li>
          Open <em>Basic Information</em> → <em>App Credentials</em> and copy the{" "}
          <em>Signing Secret</em> into the field below. Click <em>Connect</em> here.
        </li>
        <li>
          A new install row will appear above with a webhook URL. Copy it.
        </li>
        <li>
          Back in your Slack app, open <em>Event Subscriptions</em>, toggle it on, and paste the
          webhook URL as the <em>Request URL</em>. Slack will verify it live.
        </li>
        <li>
          Under <em>Subscribe to bot events</em>, add <code>message.im</code> and{" "}
          <code>app_mention</code>. Save changes — Slack will prompt to reinstall the app to
          apply the new scopes; accept.
        </li>
        <li>
          Test by DMing the bot or @-mentioning it in a channel it&apos;s been added to.
        </li>
      </SetupGuide>

      {open && (
        <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <SecretField
            label="Bot User OAuth Token (xoxb-…)"
            value={accessToken}
            onChange={setAccessToken}
            type="password"
            placeholder="xoxb-…"
          />
          <SecretField
            label="Signing secret (Basic information → App credentials)"
            value={signingSecret}
            onChange={setSigningSecret}
            type="password"
          />
          <div className="muted" style={{ fontSize: 12 }}>
            Required bot scopes: <code>app_mentions:read</code>, <code>chat:write</code>,{" "}
            <code>im:history</code>, <code>im:read</code>, <code>im:write</code>,{" "}
            <code>channels:history</code>, <code>users:read</code>.
            After save, copy the webhook URL from the install row and paste it as your Slack
            app&apos;s <em>Event Subscriptions</em> request URL; subscribe to bot events{" "}
            <code>message.im</code> and <code>app_mention</code>.
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button onClick={submit} disabled={busy || !accessToken.trim() || !signingSecret.trim()}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function TelegramPanel({
  info,
  slug,
  onChange,
  onBanner,
}: {
  info: ChannelTypeInfo | undefined;
  slug: string;
  onChange: () => void;
  onBanner: (b: Banner) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState("");

  if (!info) {
    return null;
  }

  async function submit() {
    if (!token.trim()) return;
    setBusy(true);
    try {
      await api.createChannel(slug, {
        type: "telegram",
        secrets: { bot_token: token.trim() },
      });
      onBanner({ kind: "ok", message: "Telegram bot connected — webhook auto-registered." });
      setOpen(false);
      setToken("");
      onChange();
    } catch (e) {
      onBanner({ kind: "err", message: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        border: "1px solid var(--panel-border)",
        borderRadius: 8,
        padding: 16,
      }}
    >
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>Telegram</div>
          <div className="muted" style={{ fontSize: 12 }}>
            Static-token install. Create a bot via{" "}
            <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">@BotFather</a>{" "}
            and paste the HTTP API token.
          </div>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="ghost">
          {open ? "Cancel" : "Connect Telegram"}
        </button>
      </div>

      <SetupGuide>
        <li>
          Open Telegram and start a chat with{" "}
          <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
            @BotFather
          </a>
          .
        </li>
        <li>
          Send <code>/newbot</code>. Follow the prompts: pick a display name, then a unique
          username that ends in <code>bot</code> (e.g. <code>acme_support_bot</code>).
        </li>
        <li>
          BotFather replies with an HTTP API token shaped like{" "}
          <code>123456789:ABCdef-ghi…</code>. Copy it.
        </li>
        <li>
          Paste the token into the field below and click <em>Connect</em>. We call{" "}
          <code>getMe</code> to fetch the bot identity, then call <code>setWebhook</code>{" "}
          ourselves with a fresh secret token — no further BotFather configuration needed.
        </li>
        <li>
          In Telegram, search the bot&apos;s username and open the chat. Send <code>/start</code>{" "}
          — replies should now stream from this assistant.
        </li>
        <li>
          Optional polish (in BotFather): <code>/setdescription</code>, <code>/setabouttext</code>,{" "}
          <code>/setuserpic</code>, <code>/setcommands</code>.
        </li>
      </SetupGuide>

      {open && (
        <div style={{ marginTop: 12 }}>
          <label className="muted" style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
            Bot token
          </label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="123456:ABCdef…"
            style={{
              width: "100%",
              padding: "8px 10px",
              background: "var(--paper)",
              color: "var(--text)",
              border: "1px solid var(--panel-border)",
              borderRadius: 6,
              fontFamily: "monospace",
              fontSize: 13,
            }}
          />
          <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
            We call <code>getMe</code> to learn the bot id, then{" "}
            <code>setWebhook</code> ourselves with a fresh secret token.
            You won&apos;t need to configure anything in BotFather.
          </div>
          <div className="row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
            <button onClick={submit} disabled={busy || !token.trim()}>
              {busy ? "Connecting…" : "Connect"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function prettyType(type: ChannelType): string {
  switch (type) {
    case "slack":
      return "Slack";
    case "telegram":
      return "Telegram";
    case "whatsapp_cloud":
      return "WhatsApp";
    case "google_chat":
      return "Google Chat";
    case "teams":
      return "Microsoft Teams";
    case "web_widget":
      return "Web widget";
    default:
      return type;
  }
}

function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}
