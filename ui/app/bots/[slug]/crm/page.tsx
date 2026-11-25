"use client";

import { FormEvent, useEffect, useState } from "react";
import { useBot } from "@/components/BotShell";
import { parseServerDate } from "@/lib/aggregations";
import { ApiError, CrmCatalog, CrmConnection, CrmProviderInfo, api } from "@/lib/api";

const CRM_ERRORS: Record<string, string> = {
  denied: "The provider did not grant access. Start the connection again.",
  exchange:
    "The authorization code could not be exchanged. Check the client id, secret, data center or environment, and that the redirect URL matches.",
  state: "That connection attempt expired. Start it again.",
  missing: "Save a client id and client secret before connecting.",
};

const ZOHO_AUTH_HOSTS = new Set([
  "accounts.zoho.com",
  "accounts.zoho.eu",
  "accounts.zoho.in",
  "accounts.zoho.com.au",
  "accounts.zoho.jp",
  "accounts.zohocloud.ca",
  "accounts.zoho.sa",
  "accounts.zoho.com.cn",
]);

const SALESFORCE_AUTH_HOSTS = new Set(["login.salesforce.com", "test.salesforce.com"]);
// A My Domain login host, as crm/providers.py allows it.
const SALESFORCE_MY_DOMAIN =
  /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.(?:develop|sandbox|scratch|trailblaze))?\.my\.salesforce\.com$/;

type Draft = { clientId: string; secret: string; dataCenter: string; loginUrl: string };

function needsLoginUrl(provider: CrmProviderInfo, dataCenter: string): boolean {
  return Boolean(provider.data_centers.find((c) => c.id === dataCenter)?.login_url_required);
}

function draftFor(provider: CrmProviderInfo, row: CrmConnection | undefined, prev?: Draft): Draft {
  const dataCenter = row?.data_center || prev?.dataCenter || provider.data_centers[0]?.id || "us";
  return {
    clientId: row?.client_id || prev?.clientId || "",
    secret: "",
    dataCenter,
    // Only a My Domain choice has a URL worth showing back.
    loginUrl: needsLoginUrl(provider, dataCenter) ? row?.login_url || prev?.loginUrl || "" : prev?.loginUrl || "",
  };
}

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  const date = parseServerDate(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function isAuthorizeUrl(provider: string, url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    if (provider === "salesforce") {
      return SALESFORCE_AUTH_HOSTS.has(parsed.hostname) || SALESFORCE_MY_DOMAIN.test(parsed.hostname);
    }
    return ZOHO_AUTH_HOSTS.has(parsed.hostname);
  } catch {
    return false;
  }
}

export default function BotCrmPage() {
  const { bot, currentRole } = useBot();
  const [superadmin, setSuperadmin] = useState(false);
  const [catalog, setCatalog] = useState<CrmCatalog | null>(null);
  const [connections, setConnections] = useState<CrmConnection[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [bannerOk, setBannerOk] = useState(true);
  const [copied, setCopied] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const writable = superadmin || currentRole === "owner" || currentRole === "admin";

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) setSuperadmin(Boolean(me.is_superadmin));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("crm") === "connected") {
      setBannerOk(true);
      setBanner("Connected. A captured email is created as a lead, or added to the lead that already has that email.");
    }
    const code = params.get("crm_error");
    if (code) {
      setBannerOk(false);
      setBanner(CRM_ERRORS[code] || "Could not connect.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.crmCatalog(), api.listCrmConnections(bot.slug)])
      .then(([nextCatalog, nextConnections]) => {
        if (cancelled) return;
        setCatalog(nextCatalog);
        setConnections(nextConnections);
        setDrafts((prev) => {
          const next = { ...prev };
          for (const provider of nextCatalog.providers) {
            const row = nextConnections.find((item) => item.provider === provider.id);
            next[provider.id] = draftFor(provider, row, prev[provider.id]);
          }
          return next;
        });
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load CRM settings");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [bot.slug]);

  async function copy(text: string, key: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      window.setTimeout(() => setCopied((current) => (current === key ? null : current)), 1500);
    } catch {
      setBannerOk(false);
      setBanner("Could not copy. Select the redirect URL and copy it manually.");
    }
  }

  function updateDraft(provider: string, patch: Partial<Draft>) {
    setDrafts((prev) => ({
      ...prev,
      [provider]: { ...(prev[provider] || { clientId: "", secret: "", dataCenter: "us", loginUrl: "" }), ...patch },
    }));
  }

  async function reload() {
    const [nextCatalog, nextConnections] = await Promise.all([
      api.crmCatalog(),
      api.listCrmConnections(bot.slug),
    ]);
    setCatalog(nextCatalog);
    setConnections(nextConnections);
    setDrafts((prev) => {
      const next = { ...prev };
      for (const provider of nextCatalog.providers) {
        const row = nextConnections.find((item) => item.provider === provider.id);
        next[provider.id] = draftFor(provider, row);
      }
      return next;
    });
  }

  async function onSave(event: FormEvent, provider: CrmProviderInfo) {
    event.preventDefault();
    const draft = drafts[provider.id];
    if (!draft) return;
    setBusy(provider.id);
    setBanner(null);
    try {
      await api.saveCrmCredentials(bot.slug, provider.id, {
        client_id: draft.clientId.trim(),
        client_secret: draft.secret.trim() || undefined,
        data_center: provider.data_centers.length > 0 ? draft.dataCenter : undefined,
        login_url: needsLoginUrl(provider, draft.dataCenter) ? draft.loginUrl.trim() : undefined,
      });
      await reload();
      setBannerOk(true);
      setBanner("Saved. Connect to finish sign-in. Saving clears any previous tokens.");
    } catch (err) {
      setBannerOk(false);
      setBanner(err instanceof ApiError ? err.message : "Could not save");
    } finally {
      setBusy(null);
    }
  }

  async function onConnect(provider: CrmProviderInfo) {
    setBusy(provider.id);
    setBanner(null);
    try {
      const { authorize_url: url } = await api.startCrmOAuth(bot.slug, provider.id);
      if (!isAuthorizeUrl(provider.id, url)) {
        setBannerOk(false);
        setBanner(`The authorization URL was not for ${provider.name}, so it was not opened.`);
        return;
      }
      window.location.assign(url);
    } catch (err) {
      setBannerOk(false);
      setBanner(err instanceof ApiError ? err.message : "Could not start the connection");
    } finally {
      setBusy(null);
    }
  }

  async function onDisconnect(provider: CrmProviderInfo) {
    if (!window.confirm(`Disconnect ${provider.name}? Captured leads will stop syncing there.`)) return;
    setBusy(provider.id);
    setBanner(null);
    try {
      await api.disconnectCrm(bot.slug, provider.id);
      await reload();
      setBannerOk(true);
      setBanner(`${provider.name} disconnected.`);
    } catch (err) {
      setBannerOk(false);
      setBanner(err instanceof ApiError ? err.message : "Could not disconnect");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <div className="header">
        <div>
          <h1>CRM</h1>
          <p className="muted" style={{ margin: "4px 0 0" }}>
            Connect Zoho CRM or Salesforce with your own app. When a visitor leaves an email, that
            lead is created, or the new capture is added to the lead that already has the email.
          </p>
        </div>
      </div>

      {banner && (
        <div className={bannerOk ? "banner" : "banner warning"} role="status" style={{ marginBottom: 16 }}>
          {banner}
        </div>
      )}
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {catalog && !catalog.public_base_configured && (
        <div className="banner warning" style={{ marginBottom: 16 }}>
          Set PUBLIC_BASE_URL (or APP_BASE_URL) to the origin your CRM can reach. Until
          then the redirect URL is only right for this machine.
        </div>
      )}
      {loading && <p className="muted">Loading…</p>}

      {catalog?.providers.map((provider) => {
        const row = connections.find((item) => item.provider === provider.id) || null;
        const draft = drafts[provider.id] || draftFor(provider, row ?? undefined);
        const savedId = row?.client_id || "";
        const wantsLoginUrl = needsLoginUrl(provider, draft.dataCenter);
        const dirty =
          draft.clientId.trim() !== savedId ||
          draft.secret.trim() !== "" ||
          (provider.data_centers.length > 0 && draft.dataCenter !== (row?.data_center || "")) ||
          (wantsLoginUrl && draft.loginUrl.trim().replace(/\/+$/, "").toLowerCase() !== (row?.login_url || ""));
        const locked = !writable || busy === provider.id;
        return (
          <section className="card" key={provider.id} style={{ marginBottom: 16 }}>
            <h2 style={{ marginTop: 0 }}>{provider.name}</h2>
            {provider.requirements.length > 0 && (
              <ul>
                {provider.requirements.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            )}
            {provider.sections && provider.sections.length > 0 ? (
              <ol>
                {provider.sections.map((section) => (
                  <li key={section.title} style={{ marginBottom: 8 }}>
                    <strong>{section.title}</strong>
                    <ul style={{ marginTop: 4 }}>
                      {section.items.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
            ) : (
              <ol>
                {provider.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            )}

            <div className="field">
              <label htmlFor={`${provider.id}-redirect`}>Redirect URL</label>
              <div className="row">
                <input id={`${provider.id}-redirect`} readOnly value={provider.redirect_url} />
                <button type="button" className="ghost" onClick={() => copy(provider.redirect_url, provider.id)}>
                  {copied === provider.id ? "Copied" : "Copy"}
                </button>
              </div>
              <p className="muted" style={{ marginTop: 6 }}>
                Paste this into the provider&apos;s app. It has to match exactly. This page is the
                only place it comes from.
              </p>
            </div>

            <div className="field">
              <label htmlFor={`${provider.id}-scopes`}>Scopes</label>
              <code id={`${provider.id}-scopes`} style={{ display: "block", whiteSpace: "pre-wrap" }}>
                {provider.scopes.join(provider.id === "zoho" ? "," : " ")}
              </code>
              <p className="muted">{provider.scope_help}</p>
            </div>

            <form onSubmit={(event) => onSave(event, provider)}>
              <div className="field">
                <label htmlFor={`${provider.id}-client`}>Client id</label>
                <input
                  id={`${provider.id}-client`}
                  value={draft.clientId}
                  autoComplete="off"
                  disabled={locked}
                  onChange={(event) => updateDraft(provider.id, { clientId: event.target.value })}
                />
              </div>
              {provider.data_centers.length > 0 && (
                <div className="field">
                  <label htmlFor={`${provider.id}-dc`}>{provider.data_center_label || "Data center"}</label>
                  <select
                    id={`${provider.id}-dc`}
                    value={draft.dataCenter}
                    disabled={locked}
                    onChange={(event) => updateDraft(provider.id, { dataCenter: event.target.value })}
                  >
                    {provider.data_centers.map((center) => (
                      <option key={center.id} value={center.id}>
                        {center.label}
                        {center.accounts_host ? ` (${center.accounts_host.replace("https://", "")})` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {wantsLoginUrl && (
                <div className="field">
                  <label htmlFor={`${provider.id}-login`}>My Domain URL</label>
                  <input
                    id={`${provider.id}-login`}
                    type="url"
                    value={draft.loginUrl}
                    autoComplete="off"
                    disabled={locked}
                    placeholder="https://yourorg-dev-ed.develop.my.salesforce.com"
                    onChange={(event) => updateDraft(provider.id, { loginUrl: event.target.value })}
                  />
                  <p className="muted">
                    Setup &gt; My Domain shows it. Sign-in for this org goes to this address instead of
                    login.salesforce.com.
                  </p>
                </div>
              )}
              <div className="field">
                <label htmlFor={`${provider.id}-secret`}>Client secret</label>
                <input
                  id={`${provider.id}-secret`}
                  type="password"
                  value={draft.secret}
                  autoComplete="new-password"
                  placeholder={row?.has_secret ? "Saved — paste a new secret to replace it" : ""}
                  disabled={locked}
                  onChange={(event) => updateDraft(provider.id, { secret: event.target.value })}
                />
                <p className="muted">Write-only. The saved secret is never shown again.</p>
              </div>
              <div className="row">
                <button
                  type="submit"
                  disabled={locked || !draft.clientId.trim() || (wantsLoginUrl && !draft.loginUrl.trim())}
                >
                  Save
                </button>
                <button
                  type="button"
                  className="ghost"
                  disabled={locked || !row?.has_secret || dirty}
                  onClick={() => onConnect(provider)}
                >
                  Connect
                </button>
                {row && (
                  <button
                    type="button"
                    className="ghost"
                    disabled={locked}
                    onClick={() => onDisconnect(provider)}
                  >
                    Disconnect
                  </button>
                )}
              </div>
            </form>

            <Status row={row} />
          </section>
        );
      })}
    </div>
  );
}

function Status({ row }: { row: CrmConnection | null }) {
  if (!row) {
    return <p className="muted">Not connected.</p>;
  }
  if (row.status === "connected" || row.status === "error") {
    return (
      <div style={{ marginTop: 16 }}>
        <p>
          {row.status === "connected" ? "Connected." : "The last sync failed."}{" "}
          {row.expires_at ? `Access token expires ${formatWhen(row.expires_at)}. It refreshes automatically a minute before then.` : ""}
          {row.api_domain ? ` API ${row.api_domain.replace("https://", "")}.` : ""}
        </p>
        {row.last_error && (
          <p className="error" role="alert">
            {row.last_error}
          </p>
        )}
      </div>
    );
  }
  return <p className="muted">Credentials saved. Connect to finish sign-in.</p>;
}
