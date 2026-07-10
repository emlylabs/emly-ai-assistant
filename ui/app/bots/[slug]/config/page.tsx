"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBot } from "@/components/BotShell";
import ActionsSection from "@/components/actions/ActionsSection";
import SummaryCard from "@/components/SummaryCard";
import MigrationNotice from "@/components/MigrationNotice";
import ValidationPanel from "@/components/ValidationPanel";
import WidgetPreview from "@/components/WidgetPreview";
import SkillTypePicker from "@/components/skills/SkillTypePicker";
import FieldsEditor from "@/components/skills/FieldsEditor";
import PromptEditor from "@/components/skills/PromptEditor";
import { ApiError, api } from "@/lib/api";
import { validateConfig } from "@/lib/config-validation";
import { GLOBAL_PROMPT_DEFAULTS } from "@/lib/defaults";
import { applyType, inferType } from "@/lib/skill-types";

type TabId = "identity" | "widget" | "topics" | "forms" | "llm" | "rag" | "limits" | "raw";

const TABS: { id: TabId; label: string }[] = [
  { id: "identity", label: "Bot setup" },
  { id: "widget", label: "Appearance" },
  { id: "topics", label: "Skills" },
  { id: "forms", label: "Actions" },
  { id: "rag", label: "Knowledge" },
  { id: "llm", label: "LLM" },
  { id: "limits", label: "Limits" },
  { id: "raw", label: "Raw JSON" },
];

type Cfg = Record<string, any>;

export default function BotConfigPage() {
  const { bot, currentRole, refreshBot } = useBot();
  const writable = currentRole === "owner" || currentRole === "admin";

  const [config, setConfig] = useState<Cfg>({});
  const [savedConfig, setSavedConfig] = useState<Cfg>({});
  const [version, setVersion] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ current: number; expected: number } | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("identity");
  const [fileCount, setFileCount] = useState<number | undefined>(undefined);
  const [hasApiKey, setHasApiKey] = useState<boolean | undefined>(undefined);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setConflict(null);
    setSuccess(null);
    try {
      const res = await api.getBotConfig(bot.slug);
      const cfg = (res.config ?? {}) as Cfg;
      setConfig(cfg);
      setSavedConfig(cfg);
      setVersion(res.config_version);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load config");
    } finally {
      setLoading(false);
    }
  }, [bot.slug]);

  useEffect(() => {
    load();
  }, [load]);

  // Fetch file count + API key status for the summary card / validation.
  useEffect(() => {
    api.listBotFiles(bot.slug).then((files) => setFileCount(files.length)).catch(() => setFileCount(undefined));
    api.getBotApiKeyStatus(bot.slug).then((r) => setHasApiKey(r.has_key)).catch(() => setHasApiKey(undefined));
  }, [bot.slug]);

  const issues = useMemo(() => validateConfig(config, { fileCount }), [config, fileCount]);
  const hasUnsavedEdits = useMemo(
    () => JSON.stringify(config) !== JSON.stringify(savedConfig),
    [config, savedConfig],
  );

  async function save() {
    setError(null);
    setSuccess(null);
    setConflict(null);
    setSaving(true);
    try {
      const res = await api.putBotConfig(bot.slug, config, version ?? undefined);
      const next = (res.config ?? config) as Cfg;
      setConfig(next);
      setSavedConfig(next);
      setVersion(res.config_version);
      setSuccess("Saved.");
      refreshBot();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        const detail =
          typeof err.detail === "object" && err.detail
            ? (err.detail as { current_version?: number; expected_version?: number })
            : {};
        if (detail.current_version != null && detail.expected_version != null) {
          setConflict({ current: detail.current_version, expected: detail.expected_version });
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : "Failed to save");
      }
    } finally {
      setSaving(false);
    }
  }

  // Cmd/Ctrl+S triggers Save. Use a ref so the listener always reads the
  // latest `save` closure without re-binding on every render.
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  });
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const isSaveCombo = (e.metaKey || e.ctrlKey) && (e.key === "s" || e.key === "S");
      if (!isSaveCombo) return;
      e.preventDefault();
      if (!writable || saving || loading) return;
      if (!hasUnsavedEdits) return;
      saveRef.current();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [writable, saving, loading, hasUnsavedEdits]);

  // Helpers used by every section to mutate a sub-key without losing the rest.
  function patch<K extends keyof Cfg>(key: K, value: Cfg[K]) {
    setConfig((prev) => ({ ...prev, [key]: value }));
  }

  function patchPath(path: string[], value: any) {
    // Structural sharing: shallow-copy only the path we touch so
    // siblings keep their object identity. This avoids triggering
    // ``[config]`` effects in untouched tabs (notably RawJSONSection,
    // which would otherwise clobber in-progress edits) and is much
    // cheaper than a full ``JSON.parse(JSON.stringify(...))`` per
    // keystroke for large topic trees.
    setConfig((prev) => {
      const next: Cfg = { ...prev };
      let cur: any = next;
      for (let i = 0; i < path.length - 1; i++) {
        const key = path[i];
        const child = cur[key];
        cur[key] = child != null && typeof child === "object" && !Array.isArray(child) ? { ...child } : {};
        cur = cur[key];
      }
      cur[path[path.length - 1]] = value;
      return next;
    });
  }

  return (
    <>
      <div className="header">
        <h1>Config — {bot.name}</h1>
        <div className="row">
          <button className="ghost" onClick={load} disabled={loading || saving}>
            Reload
          </button>
          <button onClick={save} disabled={loading || saving || !writable}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {!writable && (
        <div className="banner advisory">
          You can read this config but your role doesn&apos;t permit saves.
        </div>
      )}

      {conflict && (
        <div className="banner warning">
          <strong>Config has been updated by another tab.</strong> Your edits
          would overwrite version {conflict.current} (you started from
          version {conflict.expected}).{" "}
          <button className="ghost" onClick={load} style={{ marginLeft: 8 }}>
            Reload to see latest
          </button>
        </div>
      )}

      {error && (
        <div className="error" role="alert" aria-live="assertive">
          {error}
        </div>
      )}
      {success && (
        <div className="success" role="status" aria-live="polite">
          {success}
        </div>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <MigrationNotice />
          <SummaryCard
            config={config}
            fileCount={fileCount}
            hasApiKey={hasApiKey}
            onJump={(t) => setActiveTab(t as TabId)}
          />
          <ValidationPanel
            issues={issues}
            onJump={(i) => i.section && setActiveTab(i.section as TabId)}
          />
          <div
            role="tablist"
            aria-label="Config sections"
            style={{
              display: "flex",
              gap: 4,
              borderBottom: "1px solid var(--panel-border)",
              marginBottom: 16,
              flexWrap: "wrap",
            }}
          >
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={activeTab === t.id}
                className="ghost"
                onClick={() => setActiveTab(t.id)}
                style={{
                  borderRadius: 0,
                  border: "none",
                  borderBottom:
                    activeTab === t.id ? "2px solid var(--accent)" : "2px solid transparent",
                  color: activeTab === t.id ? "var(--text)" : "var(--text-muted)",
                  paddingBottom: 8,
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          {activeTab === "identity" && (
            <IdentitySection config={config} patchPath={patchPath} writable={writable} />
          )}
          {activeTab === "widget" && (
            <WidgetSection config={config} patchPath={patchPath} writable={writable} />
          )}
          {activeTab === "topics" && (
            <TopicsSection config={config} patch={patch} writable={writable} />
          )}
          {activeTab === "forms" && (
            <ActionsSection config={config} patch={patch} writable={writable} />
          )}
          {activeTab === "llm" && (
            <LLMSection
              slug={bot.slug}
              config={config}
              patchPath={patchPath}
              writable={writable}
            />
          )}
          {activeTab === "rag" && (
            <RAGSection
              config={config}
              patchPath={patchPath}
              writable={writable}
              fileCount={fileCount}
              slug={bot.slug}
            />
          )}
          {activeTab === "limits" && (
            <LimitsSection config={config} patchPath={patchPath} writable={writable} />
          )}
          {activeTab === "raw" && (
            <RawJSONSection
              config={config}
              savedConfig={savedConfig}
              setConfig={setConfig}
              writable={writable}
            />
          )}

          {version != null && (
            <div className="muted" style={{ fontSize: 12, marginTop: 16, textAlign: "left" }}>
              config_version: {version}
            </div>
          )}
        </>
      )}

      {!loading && (
        <WidgetPreview slug={bot.slug} reloadKey={version ?? 0} />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Identity tab
// ---------------------------------------------------------------------------
function IdentitySection({
  config,
  patchPath,
  writable,
}: {
  config: Cfg;
  patchPath: (path: string[], value: any) => void;
  writable: boolean;
}) {
  const widget = config.widget ?? {};
  const globalPrompts = config.global_prompts ?? {};
  const starterMessages: string[] = Array.isArray(config.starter_messages) ? config.starter_messages : [];
  const supportEmail: string = typeof config.support_email === "string" ? config.support_email : "";
  const whatsappLink: string = typeof config.whatsapp_link === "string" ? config.whatsapp_link : "";
  const whatsappMessage: string = typeof config.whatsapp_message === "string" ? config.whatsapp_message : "";
  const accounts: Record<string, any> =
    config.social_handles && typeof config.social_handles === "object" && !Array.isArray(config.social_handles)
      ? (config.social_handles.accounts && typeof config.social_handles.accounts === "object" ? config.social_handles.accounts : {})
      : {};
  const accountEntries = Object.entries(accounts).map(([k, v]) => ({
    platform: k,
    value: v && typeof v === "object" ? String((v as any).value ?? "") : String(v ?? ""),
    icon: v && typeof v === "object" ? String((v as any).icon ?? "") : "",
  }));
  const termsBody: string =
    config.terms_of_service && typeof config.terms_of_service === "object" && !Array.isArray(config.terms_of_service)
      ? String((config.terms_of_service as any).terms ?? "")
      : "";

  function setAccounts(next: { platform: string; value: string; icon: string }[]) {
    const dict: Record<string, { value: string; icon?: string }> = {};
    for (const { platform, value, icon } of next) {
      const key = platform.trim();
      if (!key) continue;
      const entry: { value: string; icon?: string } = { value };
      const trimmedIcon = icon.trim();
      if (trimmedIcon) entry.icon = trimmedIcon;
      dict[key] = entry;
    }
    patchPath(["social_handles", "accounts"], Object.keys(dict).length ? dict : undefined);
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Bot voice &amp; messages</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        The default things the bot says when no skill-specific prompt overrides them.
      </p>
      <div className="field">
        <label htmlFor="cfg-welcome">Welcome message</label>
        <textarea
          id="cfg-welcome"
          rows={2}
          placeholder={GLOBAL_PROMPT_DEFAULTS.welcome_message}
          value={globalPrompts.welcome_message ?? ""}
          onChange={(e) =>
            patchPath(["global_prompts", "welcome_message"], e.target.value || undefined)
          }
          disabled={!writable}
          style={textareaStyle}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          The first thing visitors see when they open the chat.
        </div>
      </div>
      <div className="field">
        <label htmlFor="cfg-goodbye">Goodbye message</label>
        <input
          id="cfg-goodbye"
          type="text"
          placeholder={GLOBAL_PROMPT_DEFAULTS.goodbye_message}
          value={globalPrompts.goodbye_message ?? ""}
          onChange={(e) =>
            patchPath(["global_prompts", "goodbye_message"], e.target.value || undefined)
          }
          disabled={!writable}
        />
      </div>
      <div className="field">
        <label htmlFor="cfg-error">Error message</label>
        <input
          id="cfg-error"
          type="text"
          placeholder={GLOBAL_PROMPT_DEFAULTS.error_message}
          value={globalPrompts.error_message ?? ""}
          onChange={(e) =>
            patchPath(["global_prompts", "error_message"], e.target.value || undefined)
          }
          disabled={!writable}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Shown if the bot can&apos;t process a request.
        </div>
      </div>
      <div className="field">
        <label htmlFor="cfg-slot-q">Default field question</label>
        <input
          id="cfg-slot-q"
          type="text"
          placeholder={GLOBAL_PROMPT_DEFAULTS.slot_question}
          value={globalPrompts.slot_question ?? ""}
          onChange={(e) =>
            patchPath(["global_prompts", "slot_question"], e.target.value || undefined)
          }
          disabled={!writable}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Used when a skill needs to ask for a piece of information and you haven&apos;t
          set a custom question for that field. Use <code>{"{slot_name}"}</code> to insert the field name.
        </div>
      </div>

      <h2 style={{ marginTop: 24 }}>What end users see</h2>
      <div className="field">
        <label htmlFor="cfg-title">Title</label>
        <input
          id="cfg-title"
          type="text"
          placeholder="Acme Support"
          value={widget.title ?? ""}
          onChange={(e) => patchPath(["widget", "title"], e.target.value || undefined)}
          disabled={!writable}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Shown in the widget header. Defaults to the bot&apos;s display name.
        </div>
      </div>
      <div className="field">
        <label htmlFor="cfg-subtitle">Subtitle</label>
        <input
          id="cfg-subtitle"
          type="text"
          placeholder="Online · usually replies in seconds"
          value={widget.subtitle ?? ""}
          onChange={(e) => patchPath(["widget", "subtitle"], e.target.value || undefined)}
          disabled={!writable}
        />
      </div>
      <div className="field">
        <label htmlFor="cfg-placeholder">Input placeholder</label>
        <input
          id="cfg-placeholder"
          type="text"
          placeholder="Ask me anything…"
          value={widget.input_placeholder ?? ""}
          onChange={(e) => patchPath(["widget", "input_placeholder"], e.target.value || undefined)}
          disabled={!writable}
        />
      </div>
      <div className="field">
        <label htmlFor="cfg-logo">Logo URL</label>
        <input
          id="cfg-logo"
          type="url"
          placeholder="https://your-site.com/logo.png"
          value={widget.logo ?? ""}
          onChange={(e) => patchPath(["widget", "logo"], e.target.value || undefined)}
          disabled={!writable}
        />
      </div>
      <ListField
        label="Starter messages"
        hint="Quick-reply chips shown above the input on first load."
        values={starterMessages}
        onChange={(v) => patchPath(["starter_messages"], v.length ? v : undefined)}
        placeholder="How do I get started?"
        disabled={!writable}
      />

      <h2 style={{ marginTop: 24 }}>Contact &amp; social</h2>
      <div className="field">
        <label htmlFor="cfg-support-email">Support email</label>
        <input
          id="cfg-support-email"
          type="text"
          placeholder="support@example.com"
          value={supportEmail}
          onChange={(e) => patchPath(["support_email"], e.target.value || undefined)}
          disabled={!writable}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Forwarded to lead/callback form submissions. Comma-separated for multiple recipients.
        </div>
      </div>
      <div className="field">
        <label htmlFor="cfg-wa-link">WhatsApp link</label>
        <input
          id="cfg-wa-link"
          type="url"
          placeholder="https://wa.me/15555550123"
          value={whatsappLink}
          onChange={(e) => patchPath(["whatsapp_link"], e.target.value || undefined)}
          disabled={!writable}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          The launcher only shows a WhatsApp button when this is set.
        </div>
      </div>
      <div className="field">
        <label htmlFor="cfg-wa-message">WhatsApp prefill message</label>
        <textarea
          id="cfg-wa-message"
          rows={2}
          placeholder="Hi, I would like to know more about your services."
          value={whatsappMessage}
          onChange={(e) => patchPath(["whatsapp_message"], e.target.value || undefined)}
          disabled={!writable}
          style={textareaStyle}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Supports <code>{"{BASE_URL}"}</code>, <code>{"{USER_ID}"}</code>, <code>{"{SESSION_ID}"}</code> placeholders.
        </div>
      </div>
      <div className="field">
        <label>Social accounts</label>
        <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
          Platform key (e.g. <code>whatsapp</code>, <code>instagram</code>), account URL, and an icon URL. The widget renders the icon next to the entry — without one, the menu item shows a broken image.
        </div>
        <div style={{ display: "grid", gap: 8 }}>
          {accountEntries.length === 0 && (
            <span className="muted" style={{ fontSize: 13 }}>(no accounts)</span>
          )}
          {accountEntries.map((row, i) => (
            <div key={i} className="row" style={{ gap: 8, alignItems: "flex-start", flexWrap: "wrap" }}>
              <input
                type="text"
                placeholder="instagram"
                value={row.platform}
                onChange={(e) =>
                  setAccounts(accountEntries.map((x, idx) => (idx === i ? { ...x, platform: e.target.value } : x)))
                }
                disabled={!writable}
                style={{ width: 140 }}
              />
              <input
                type="text"
                placeholder="https://instagram.com/yourhandle"
                value={row.value}
                onChange={(e) =>
                  setAccounts(accountEntries.map((x, idx) => (idx === i ? { ...x, value: e.target.value } : x)))
                }
                disabled={!writable}
                style={{ flex: "1 1 240px", minWidth: 200 }}
              />
              <input
                type="url"
                placeholder="https://cdn.example.com/icons/instagram.svg"
                value={row.icon}
                onChange={(e) =>
                  setAccounts(accountEntries.map((x, idx) => (idx === i ? { ...x, icon: e.target.value } : x)))
                }
                disabled={!writable}
                style={{ flex: "1 1 200px", minWidth: 180 }}
              />
              {writable && (
                <button
                  className="ghost"
                  onClick={() => setAccounts(accountEntries.filter((_, idx) => idx !== i))}
                  style={{ padding: "4px 8px", fontSize: 12 }}
                >
                  Remove
                </button>
              )}
            </div>
          ))}
          {writable && (
            <button
              className="ghost"
              onClick={() => setAccounts([...accountEntries, { platform: "", value: "", icon: "" }])}
              style={{ alignSelf: "flex-start" }}
            >
              + Add account
            </button>
          )}
        </div>
      </div>

      <h2 style={{ marginTop: 24 }}>Terms of service</h2>
      <div className="field">
        <label htmlFor="cfg-tos">Terms text</label>
        <textarea
          id="cfg-tos"
          rows={10}
          placeholder="Your terms here…"
          value={termsBody}
          onChange={(e) =>
            patchPath(["terms_of_service", "terms"], e.target.value || undefined)
          }
          disabled={!writable}
          style={textareaStyle}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Rendered in the widget&apos;s terms dialog.
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Widget tab — theme + launcher
// ---------------------------------------------------------------------------
function WidgetSection({
  config,
  patchPath,
  writable,
}: {
  config: Cfg;
  patchPath: (path: string[], value: any) => void;
  writable: boolean;
}) {
  const widget = config.widget ?? {};
  const theme = widget.theme ?? {};
  const nudges = (config.nudges ?? {}) as {
    nudge?: { nudge_label?: string; nudge_query?: string }[];
    wait_time?: number;
    duration?: number;
    count?: number | string;
  };
  const nudgeList = Array.isArray(nudges.nudge) ? nudges.nudge : [];
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Theme</h2>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        <ColorField
          label="Launcher background"
          value={theme.launcher_background}
          onChange={(v) => patchPath(["widget", "theme", "launcher_background"], v)}
          disabled={!writable}
        />
        <ColorField
          label="Container background"
          value={theme.container_background}
          onChange={(v) => patchPath(["widget", "theme", "container_background"], v)}
          disabled={!writable}
        />
        <ColorField
          label="Header background"
          value={theme.header_background}
          onChange={(v) => patchPath(["widget", "theme", "header_background"], v)}
          disabled={!writable}
        />
        <ColorField
          label="Header foreground"
          value={theme.header_foreground}
          onChange={(v) => patchPath(["widget", "theme", "header_foreground"], v)}
          disabled={!writable}
        />
        <ColorField
          label="User message background"
          value={theme.user_message_background}
          onChange={(v) => patchPath(["widget", "theme", "user_message_background"], v)}
          disabled={!writable}
        />
        <ColorField
          label="User message foreground"
          value={theme.user_message_foreground}
          onChange={(v) => patchPath(["widget", "theme", "user_message_foreground"], v)}
          disabled={!writable}
        />
        <ColorField
          label="Bot message background"
          value={theme.bot_message_background}
          onChange={(v) => patchPath(["widget", "theme", "bot_message_background"], v)}
          disabled={!writable}
        />
        <ColorField
          label="Bot message foreground"
          value={theme.bot_message_foreground}
          onChange={(v) => patchPath(["widget", "theme", "bot_message_foreground"], v)}
          disabled={!writable}
        />
      </div>

      <h2 style={{ marginTop: 24 }}>Launcher</h2>
      <div className="field">
        <label htmlFor="cfg-launcher-pos">Launcher position</label>
        <select
          id="cfg-launcher-pos"
          value={widget.launcher_position ?? "right"}
          onChange={(e) => patchPath(["widget", "launcher_position"], e.target.value)}
          disabled={!writable}
          style={selectStyle}
        >
          <option value="right">Right</option>
          <option value="left">Left</option>
        </select>
      </div>
      <div style={{ marginBottom: 16 }}>
        <FlagToggle
          label="Open chat automatically on page load"
          checked={Boolean(widget.open_on_load)}
          onChange={(v) => patchPath(["widget", "open_on_load"], v)}
          disabled={!writable}
        />
      </div>
      <div className="field">
        <label htmlFor="cfg-launcher-label">Launcher label</label>
        <input
          id="cfg-launcher-label"
          type="text"
          placeholder="Chat With Us"
          value={typeof config.launcher_label === "string" ? config.launcher_label : ""}
          onChange={(e) => patchPath(["launcher_label"], e.target.value || undefined)}
          disabled={!writable}
        />
      </div>
      <div style={{ marginBottom: 16 }}>
        <FlagToggle
          label="Show launcher label next to icon"
          checked={Boolean(config.is_icon_with_label)}
          onChange={(v) => patchPath(["is_icon_with_label"], v || undefined)}
          disabled={!writable}
        />
      </div>
      <div className="field">
        <label htmlFor="cfg-open-icon">Open icon URL</label>
        <input
          id="cfg-open-icon"
          type="url"
          placeholder="https://your-cdn.com/open.png"
          value={typeof config.open_icon === "string" ? config.open_icon : ""}
          onChange={(e) => patchPath(["open_icon"], e.target.value || undefined)}
          disabled={!writable}
        />
      </div>
      <div className="field">
        <label htmlFor="cfg-close-icon">Close icon URL</label>
        <input
          id="cfg-close-icon"
          type="url"
          placeholder="https://your-cdn.com/close.png"
          value={typeof config.close_icon === "string" ? config.close_icon : ""}
          onChange={(e) => patchPath(["close_icon"], e.target.value || undefined)}
          disabled={!writable}
        />
      </div>

      <h2 style={{ marginTop: 24 }}>Nudges</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>
        Speech-bubble prompts that pop above the launcher to invite a click. Clicking sends the query as the user&apos;s message.
      </p>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(180px, 1fr))",
          columnGap: 16,
          rowGap: 16,
          alignItems: "start",
        }}
      >
        <NumberField
          label="Wait before first nudge (ms)"
          value={typeof nudges.wait_time === "number" ? nudges.wait_time : undefined}
          defaultValue={5000}
          onChange={(v) => patchPath(["nudges", "wait_time"], v)}
          disabled={!writable}
          min={0}
          step={500}
          fluid
        />
        <NumberField
          label="Bubble duration (ms)"
          value={typeof nudges.duration === "number" ? nudges.duration : undefined}
          defaultValue={15000}
          onChange={(v) => patchPath(["nudges", "duration"], v)}
          disabled={!writable}
          min={0}
          step={500}
          fluid
        />
        <NumberField
          label="Cycle count"
          hint="How many nudges to show before stopping. Widget defaults to 3."
          value={
            typeof nudges.count === "number"
              ? nudges.count
              : typeof nudges.count === "string" && nudges.count.trim() !== ""
              ? Number(nudges.count)
              : undefined
          }
          defaultValue={3}
          onChange={(v) => patchPath(["nudges", "count"], v)}
          disabled={!writable}
          min={0}
          fluid
        />
      </div>
      <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
        {nudgeList.length === 0 && (
          <span className="muted" style={{ fontSize: 13 }}>(no nudges yet)</span>
        )}
        {nudgeList.map((n, i) => (
          <div
            key={i}
            className="row"
            style={{ gap: 8, alignItems: "flex-start", borderBottom: "1px solid var(--panel-border)", paddingBottom: 8 }}
          >
            <div style={{ flex: 1, display: "grid", gap: 4 }}>
              <input
                type="text"
                placeholder="Label users see"
                value={n.nudge_label ?? ""}
                onChange={(e) =>
                  patchPath(
                    ["nudges", "nudge"],
                    nudgeList.map((x, idx) => (idx === i ? { ...x, nudge_label: e.target.value } : x))
                  )
                }
                disabled={!writable}
              />
              <input
                type="text"
                placeholder="Query sent on click"
                value={n.nudge_query ?? ""}
                onChange={(e) =>
                  patchPath(
                    ["nudges", "nudge"],
                    nudgeList.map((x, idx) => (idx === i ? { ...x, nudge_query: e.target.value } : x))
                  )
                }
                disabled={!writable}
              />
            </div>
            {!writable ? null : (
              <button
                className="ghost"
                onClick={() =>
                  patchPath(
                    ["nudges", "nudge"],
                    nudgeList.filter((_, idx) => idx !== i)
                  )
                }
                style={{ padding: "4px 8px", fontSize: 12 }}
              >
                Remove
              </button>
            )}
          </div>
        ))}
        {writable && (
          <button
            className="ghost"
            onClick={() =>
              patchPath(["nudges", "nudge"], [...nudgeList, { nudge_label: "", nudge_query: "" }])
            }
            style={{ alignSelf: "flex-start" }}
          >
            + Add nudge
          </button>
        )}
      </div>

      <h2 style={{ marginTop: 24 }}>Layout</h2>
      <div style={{ display: "grid", gap: 8 }}>
        <FlagToggle
          label="Show maximize/minimize button"
          checked={config.show_min_max !== false}
          onChange={(v) => patchPath(["show_min_max"], v ? undefined : false)}
          disabled={!writable}
        />
        <FlagToggle
          label="Show close button"
          checked={config.show_close !== false}
          onChange={(v) => patchPath(["show_close"], v ? undefined : false)}
          disabled={!writable}
        />
        <FlagToggle
          label="Show history menu"
          checked={config.show_menu !== false}
          onChange={(v) => patchPath(["show_menu"], v ? undefined : false)}
          disabled={!writable}
        />
        <FlagToggle
          label="Open at maximum window size by default"
          checked={Boolean(config.max_window)}
          onChange={(v) => patchPath(["max_window"], v || undefined)}
          disabled={!writable}
        />
        <FlagToggle
          label="Open links in same tab"
          checked={Boolean(config.open_link_in_same_tab)}
          onChange={(v) => patchPath(["open_link_in_same_tab"], v || undefined)}
          disabled={!writable}
        />
        <FlagToggle
          label="Show feedback widget on responses"
          checked={Boolean(config.feedback)}
          onChange={(v) => patchPath(["feedback"], v || undefined)}
          disabled={!writable}
        />
        <FlagToggle
          label="Show citations under responses"
          checked={Boolean(config.show_citations)}
          onChange={(v) => patchPath(["show_citations"], v || undefined)}
          disabled={!writable}
        />
      </div>
    </div>
  );
}

function FlagToggle({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
      />
      {label}
    </label>
  );
}

function ColorField({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value?: string;
  onChange: (v: string | undefined) => void;
  disabled?: boolean;
}) {
  return (
    <div className="field" style={{ marginBottom: 0 }}>
      <label>{label}</label>
      <div className="row" style={{ gap: 8, alignItems: "center" }}>
        <input
          type="color"
          value={value ?? "#000000"}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          style={{ width: 40, height: 32, padding: 0, border: "1px solid var(--panel-border)", borderRadius: 4 }}
        />
        <input
          type="text"
          placeholder="#000000 or transparent"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value || undefined)}
          disabled={disabled}
          style={{ flex: 1 }}
        />
        {value && !disabled && (
          <button
            type="button"
            className="ghost"
            onClick={() => onChange(undefined)}
            style={{ padding: "4px 8px", fontSize: 12 }}
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Topics tab
// ---------------------------------------------------------------------------
function TopicsSection({
  config,
  patch,
  writable,
}: {
  config: Cfg;
  patch: <K extends keyof Cfg>(key: K, value: Cfg[K]) => void;
  writable: boolean;
}) {
  const topics: Record<string, any> = config.topics ?? {};
  const names = Object.keys(topics);

  function addTopic() {
    let i = 1;
    let name = "new_topic";
    while (topics[name]) {
      i += 1;
      name = `new_topic_${i}`;
    }
    patch("topics", {
      ...topics,
      [name]: {
        name,
        description: "",
        requires_rag: false,
        skip_slot_filling: true,
        slots: [],
        prompts: { llm_response: "" },
      },
    });
  }

  function updateTopic(name: string, fn: (t: any) => any) {
    patch("topics", { ...topics, [name]: fn(topics[name]) });
  }

  function removeTopic(name: string) {
    if (!confirm(`Remove topic "${name}"? Conversations under this topic stay; the bot just stops routing to it.`)) return;
    const next = { ...topics };
    delete next[name];
    patch("topics", next);
  }

  function renameTopic(oldName: string, newName: string) {
    if (!newName.trim() || newName === oldName) return;
    if (topics[newName]) {
      alert(`Topic "${newName}" already exists.`);
      return;
    }
    const next: Record<string, any> = {};
    for (const [k, v] of Object.entries(topics)) {
      if (k === oldName) {
        next[newName] = { ...v, name: newName };
      } else {
        next[k] = v;
      }
    }
    patch("topics", next);
  }

  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <p className="muted" style={{ margin: 0 }}>
          A bot must have at least one topic to chat. Multi-topic bots route via the
          intent router; single-topic bots skip the router entirely.
        </p>
        <button onClick={addTopic} disabled={!writable}>
          Add topic
        </button>
      </div>

      {names.length === 0 ? (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>No topics yet. Add one to enable chat.</p>
        </div>
      ) : (
        names.map((name) => (
          <TopicEditor
            key={name}
            name={name}
            topic={topics[name]}
            config={config}
            onChange={(fn) => updateTopic(name, fn)}
            onRename={(newName) => renameTopic(name, newName)}
            onRemove={() => removeTopic(name)}
            writable={writable}
          />
        ))
      )}
    </div>
  );
}

function TopicEditor({
  name,
  topic,
  config,
  onChange,
  onRename,
  onRemove,
  writable,
}: {
  name: string;
  topic: any;
  config: Cfg;
  onChange: (fn: (t: any) => any) => void;
  onRename: (newName: string) => void;
  onRemove: () => void;
  writable: boolean;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState(name);
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        {renaming ? (
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <input
              type="text"
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              autoFocus
            />
            <button
              onClick={() => {
                onRename(draftName.trim());
                setRenaming(false);
              }}
              disabled={!writable}
            >
              Rename
            </button>
            <button
              className="ghost"
              onClick={() => {
                setDraftName(name);
                setRenaming(false);
              }}
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>{name}</h3>
            {writable && (
              <button
                className="ghost"
                onClick={() => setRenaming(true)}
                style={{ padding: "2px 8px", fontSize: 12 }}
              >
                Rename
              </button>
            )}
          </div>
        )}
        <button className="ghost" onClick={onRemove} disabled={!writable}>
          Remove
        </button>
      </div>

      <div className="field" style={{ marginTop: 12 }}>
        <label>When to use this skill</label>
        <input
          type="text"
          placeholder="When the visitor wants to book a callback…"
          value={topic.description ?? ""}
          onChange={(e) => onChange((t) => ({ ...t, description: e.target.value }))}
          disabled={!writable}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Plain-English summary the bot uses to decide between skills.
        </div>
      </div>

      <div className="field" style={{ marginTop: 16 }}>
        <label style={{ display: "block", marginBottom: 8 }}>Skill type</label>
        <SkillTypePicker
          value={inferType({
            requires_rag: Boolean(topic.requires_rag),
            skip_slot_filling: Boolean(topic.skip_slot_filling),
          })}
          onChange={(type) => {
            const flags = applyType(type);
            const slotCount = Array.isArray(topic.slots) ? topic.slots.length : 0;
            // Warn when switching away from a "collects info" mode while
            // fields exist — they stay in the config but stop being asked.
            const wasCollecting = !topic.skip_slot_filling;
            const willCollect = !flags.skip_slot_filling;
            if (wasCollecting && !willCollect && slotCount > 0) {
              const ok = confirm(
                `This skill currently collects ${slotCount} field${
                  slotCount === 1 ? "" : "s"
                }. Switching will keep the fields in the config but stop asking visitors for them. Continue?`,
              );
              if (!ok) return;
            }
            onChange((t) => ({ ...t, ...flags }));
          }}
          disabled={!writable}
        />
      </div>

      <div className="field" style={{ marginTop: 16 }}>
        <FieldsEditor
          slots={topic.slots ?? []}
          onChange={(slots) => onChange((t) => ({ ...t, slots }))}
          disabled={!writable}
        />
      </div>

      <div className="field">
        <PromptEditor
          value={topic.prompts?.llm_response ?? ""}
          onChange={(v) =>
            onChange((t) => ({
              ...t,
              prompts: { ...(t.prompts ?? {}), llm_response: v },
            }))
          }
          disabled={!writable}
          config={config}
          topicName={name}
          slots={topic.slots ?? []}
        />
      </div>
    </div>
  );
}

// Skills tab uses `components/skills/{SkillTypePicker,FieldsEditor,PromptEditor}.tsx`.
// The legacy `SlotsEditor` row-based component was removed in Phase 2 of
// `ux-redesign.md`. The persisted shape (`topics[name].slots[]`) is unchanged.

// Forms / Actions tab — implementation lives in `components/actions/*`.
// The persisted shape (`config.c_forms_selected`) is unchanged; the new
// editor (Phase 3 of `ux-redesign.md`) presents it as plain-language
// "actions" with a wizard for new entries.

// ---------------------------------------------------------------------------
// LLM tab
// ---------------------------------------------------------------------------
function LLMSection({
  slug,
  config,
  patchPath,
  writable,
}: {
  slug: string;
  config: Cfg;
  patchPath: (path: string[], value: any) => void;
  writable: boolean;
}) {
  const llm = config.llm ?? {};
  const [hasKey, setHasKey] = useState<boolean | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [keyMsg, setKeyMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [keySaving, setKeySaving] = useState(false);

  useEffect(() => {
    api
      .getBotApiKeyStatus(slug)
      .then((r) => setHasKey(r.has_key))
      .catch(() => setHasKey(null));
  }, [slug]);

  async function saveKey(value: string | null) {
    setKeyMsg(null);
    setKeySaving(true);
    try {
      await api.putBotApiKey(slug, value);
      setHasKey(value !== null && value !== "");
      setKeyDraft("");
      setKeyMsg({ ok: true, text: value ? "Key saved." : "Key cleared." });
    } catch (err: unknown) {
      setKeyMsg({ ok: false, text: err instanceof Error ? err.message : "Failed" });
    } finally {
      setKeySaving(false);
    }
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Provider</h2>
      <div className="field">
        <label htmlFor="cfg-model-type">Provider type</label>
        <select
          id="cfg-model-type"
          value={llm.model_type ?? "openai"}
          onChange={(e) => patchPath(["llm", "model_type"], e.target.value)}
          disabled={!writable}
          style={selectStyle}
        >
          <option value="openai">OpenAI / OpenAI-compatible</option>
          <option value="google">Google</option>
          <option value="anthropic">Anthropic</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="cfg-model">Model</label>
        <input
          id="cfg-model"
          type="text"
          placeholder="google/gemma-4-26b-a4b-it:free"
          value={llm.model ?? ""}
          onChange={(e) => patchPath(["llm", "model"], e.target.value || undefined)}
          disabled={!writable}
        />
      </div>
      <div className="field">
        <label htmlFor="cfg-endpoint">API endpoint (optional)</label>
        <input
          id="cfg-endpoint"
          type="url"
          placeholder="https://openrouter.ai/api/v1"
          value={llm.api_endpoint ?? ""}
          onChange={(e) => patchPath(["llm", "api_endpoint"], e.target.value || undefined)}
          disabled={!writable}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Defaults to the provider&apos;s standard URL when blank.
        </div>
      </div>
      <div className="field">
        <label>
          Temperature
          <input
            type="number"
            step={0.1}
            min={0}
            max={2}
            value={llm.temperature ?? 0.5}
            onChange={(e) =>
              patchPath(["llm", "temperature"], e.target.value === "" ? undefined : Number(e.target.value))
            }
            disabled={!writable}
            style={{ width: 100, marginLeft: 12 }}
          />
        </label>
      </div>

      <h2 style={{ marginTop: 24 }}>API key</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        Stored encrypted at rest. The current value is never returned —
        only whether one is set. Falls back to the deployment-wide key when cleared.
      </p>
      <div className="row" style={{ alignItems: "center", gap: 8 }}>
        <span className={hasKey ? "tag" : "tag muted"}>
          {hasKey === null ? "checking…" : hasKey ? "key configured" : "no key set — using deployment fallback"}
        </span>
      </div>
      <div className="field" style={{ marginTop: 12 }}>
        <label htmlFor="cfg-api-key">Set new key</label>
        <input
          id="cfg-api-key"
          type="password"
          placeholder="sk-..."
          value={keyDraft}
          onChange={(e) => setKeyDraft(e.target.value)}
          disabled={!writable || keySaving}
          autoComplete="off"
        />
      </div>
      {keyMsg && <div className={keyMsg.ok ? "success" : "error"}>{keyMsg.text}</div>}
      <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
        {hasKey && (
          <button
            type="button"
            className="ghost"
            onClick={() => {
              if (confirm("Remove the bot's API key? Chat will fall back to the deployment-wide key.")) {
                saveKey(null);
              }
            }}
            disabled={!writable || keySaving}
          >
            Clear key
          </button>
        )}
        <button
          type="button"
          onClick={() => saveKey(keyDraft)}
          disabled={!writable || keySaving || !keyDraft}
        >
          {keySaving ? "Saving…" : "Save key"}
        </button>
      </div>

      <h2 style={{ marginTop: 24 }}>Report analysis prompt</h2>
      <div className="field">
        <label htmlFor="cfg-report-prompt">Prompt used for conversation reports</label>
        <textarea
          id="cfg-report-prompt"
          rows={6}
          placeholder="Analyze user-assistant interactions to identify trends, issues, and improvement areas…"
          value={typeof config.report_analysis_prompt === "string" ? config.report_analysis_prompt : ""}
          onChange={(e) => patchPath(["report_analysis_prompt"], e.target.value || undefined)}
          disabled={!writable}
          style={textareaStyle}
        />
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Used by the admin metric/report generator to summarize transcripts.
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RAG tab
// ---------------------------------------------------------------------------
function RAGSection({
  config,
  patchPath,
  writable,
  fileCount,
  slug,
}: {
  config: Cfg;
  patchPath: (path: string[], value: any) => void;
  writable: boolean;
  fileCount?: number;
  slug: string;
}) {
  const rag = config.rag ?? {};
  const topics: Record<string, any> = config.topics ?? {};
  const ragSkills = Object.entries(topics)
    .filter(([, t]) => Boolean((t as any)?.requires_rag))
    .map(([name]) => name);
  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Knowledge base</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        Files you upload that the bot can reference when answering questions.
      </p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          marginBottom: 16,
        }}
      >
        <div
          style={{
            padding: 12,
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
          }}
        >
          <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>Files</div>
          <div style={{ fontSize: 18, fontWeight: 600 }}>
            {fileCount === undefined ? "—" : `${fileCount} file${fileCount === 1 ? "" : "s"}`}
          </div>
          <a
            href={`/bots/${encodeURIComponent(slug)}/files`}
            className="ghost"
            style={{
              display: "inline-block",
              marginTop: 8,
              fontSize: 12,
              padding: "2px 8px",
              border: "1px solid var(--panel-border)",
              borderRadius: 4,
              textDecoration: "none",
            }}
          >
            Manage files →
          </a>
        </div>
        <div
          style={{
            padding: 12,
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
          }}
        >
          <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>Used by skill</div>
          {ragSkills.length === 0 ? (
            <div className="muted" style={{ fontSize: 12 }}>
              No skill is set to <em>Answer from knowledge</em>. Files won&apos;t be used until you
              change a skill&apos;s type.
            </div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {ragSkills.map((s) => (
                <code
                  key={s}
                  style={{
                    padding: "2px 8px",
                    background: "var(--panel-bg)",
                    border: "1px solid var(--panel-border)",
                    borderRadius: 999,
                    fontSize: 11,
                  }}
                >
                  {s}
                </code>
              ))}
            </div>
          )}
        </div>
      </div>

      <h3 style={{ marginTop: 16 }}>Retrieval behavior</h3>
      <NumberField
        label="How many chunks to use per question"
        hint="Higher = more context per answer, but may dilute relevance."
        value={rag.top_k}
        defaultValue={5}
        onChange={(v) => patchPath(["rag", "top_k"], v)}
        disabled={!writable}
        min={1}
        max={50}
      />
      <NumberField
        label="Drop low-relevance hits below score"
        hint="0.0 keeps everything; 0.5 is fairly aggressive."
        value={rag.embedding_threshold}
        defaultValue={0.2}
        onChange={(v) => patchPath(["rag", "embedding_threshold"], v)}
        disabled={!writable}
        min={0}
        max={1}
        step={0.05}
      />
      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={Boolean(rag.enable_hybrid_search)}
            onChange={(e) => patchPath(["rag", "enable_hybrid_search"], e.target.checked)}
            disabled={!writable}
            style={{ marginRight: 8 }}
          />
          Re-rank candidates with cross-encoder (slower; better quality)
        </label>
      </div>

      <button
        type="button"
        className="ghost"
        onClick={() => setShowAdvanced((v) => !v)}
        style={{
          justifyContent: "flex-start",
          padding: "8px 0 0",
          fontSize: 12,
          color: "var(--text-muted)",
        }}
      >
        {showAdvanced ? "▾" : "▸"} Advanced retrieval (chunking)
      </button>
      {showAdvanced && (
        <div style={{ paddingLeft: 16, borderLeft: "2px solid var(--panel-border)", marginTop: 8 }}>
          <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
            Chunk settings only matter when re-ingesting files. Existing files keep their original
            chunking until re-indexed.
          </p>
          <NumberField
            label="Chunk size"
            hint="Characters per chunk. Bigger = more context per chunk; smaller = more precise retrieval."
            value={rag.chunk_size}
            defaultValue={2048}
            onChange={(v) => patchPath(["rag", "chunk_size"], v)}
            disabled={!writable}
            min={128}
            max={8192}
            step={64}
          />
          <NumberField
            label="Chunk overlap"
            hint="Characters of overlap between chunks. Helps catch context that crosses a chunk boundary."
            value={rag.chunk_overlap}
            defaultValue={256}
            onChange={(v) => patchPath(["rag", "chunk_overlap"], v)}
            disabled={!writable}
            min={0}
            max={2048}
            step={32}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Limits tab
// ---------------------------------------------------------------------------
function LimitsSection({
  config,
  patchPath,
  writable,
}: {
  config: Cfg;
  patchPath: (path: string[], value: any) => void;
  writable: boolean;
}) {
  const limits = config.limits ?? {};
  const allowedOrigins = (limits.widget_allowed_origins ?? ["*"]) as string[];
  const mimeAllowlist = (limits.mime_allowlist ?? []) as string[];
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Cost &amp; usage</h2>
      <NumberField
        label="Daily token cap"
        hint="Reject chat once this many LLM tokens are spent in 24h. Empty = no cap. (Enforcement ships when the cost ledger lands; advisory until then.)"
        value={limits.daily_token_cap}
        defaultValue={undefined}
        onChange={(v) => patchPath(["limits", "daily_token_cap"], v)}
        disabled={!writable}
        min={0}
      />
      <NumberField
        label="Messages per minute, per user"
        hint="Rate limit for a single end user."
        value={limits.messages_per_minute_per_user}
        defaultValue={undefined}
        onChange={(v) => patchPath(["limits", "messages_per_minute_per_user"], v)}
        disabled={!writable}
        min={0}
      />
      <NumberField
        label="Messages per minute, bot-wide"
        hint="Aggregate cap across all end users."
        value={limits.messages_per_minute_per_bot}
        defaultValue={undefined}
        onChange={(v) => patchPath(["limits", "messages_per_minute_per_bot"], v)}
        disabled={!writable}
        min={0}
      />

      <h2 style={{ marginTop: 24 }}>Files</h2>
      <NumberField
        label="Max file size (MB)"
        value={limits.max_file_size_mb}
        defaultValue={50}
        onChange={(v) => patchPath(["limits", "max_file_size_mb"], v)}
        disabled={!writable}
        min={1}
      />
      <NumberField
        label="File count cap"
        value={limits.file_count_cap}
        defaultValue={10000}
        onChange={(v) => patchPath(["limits", "file_count_cap"], v)}
        disabled={!writable}
        min={1}
      />
      <NumberField
        label="Total storage quota (MB)"
        value={limits.total_storage_quota_mb}
        defaultValue={undefined}
        onChange={(v) => patchPath(["limits", "total_storage_quota_mb"], v)}
        disabled={!writable}
        min={0}
      />
      <ListField
        label="MIME allowlist"
        hint="Empty = use deployment defaults. Per-bot override."
        values={mimeAllowlist}
        onChange={(v) => patchPath(["limits", "mime_allowlist"], v)}
        placeholder="application/pdf"
        disabled={!writable}
      />

      <h2 style={{ marginTop: 24 }}>Widget origins</h2>
      <ListField
        label="Allowed widget origins"
        hint="Origins this bot's widget can be embedded on. One per line. Examples: https://customer-site.com, https://*.customer-site.com (any subdomain), or * to allow any origin. Enforced on /widget/{slug}/* requests; non-matching origins get 403."
        values={allowedOrigins}
        onChange={(v) => patchPath(["limits", "widget_allowed_origins"], v)}
        placeholder="https://customer-site.com"
        disabled={!writable}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Raw JSON tab
// ---------------------------------------------------------------------------
function RawJSONSection({
  config,
  savedConfig,
  setConfig,
  writable,
}: {
  config: Cfg;
  savedConfig: Cfg;
  setConfig: (next: Cfg) => void;
  writable: boolean;
}) {
  const [text, setText] = useState(() => JSON.stringify(config, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);

  const modifiedKeys = useMemo(() => diffKeys(savedConfig, config), [savedConfig, config]);
  // Track local edits separately from parent updates: as long as the
  // user has unapplied changes here, sibling-tab edits in the parent
  // shouldn't blow away their textarea contents. ``dirty`` flips off
  // again on Apply / Reset.
  const [dirty, setDirty] = useState(false);
  const lastConfigRef = useRef(config);

  useEffect(() => {
    if (config === lastConfigRef.current) return;
    lastConfigRef.current = config;
    if (!dirty) {
      setText(JSON.stringify(config, null, 2));
    }
  }, [config, dirty]);

  function commit() {
    setParseError(null);
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== "object" || Array.isArray(parsed) || parsed === null) {
        setParseError("Top level must be an object.");
        return;
      }
      setConfig(parsed);
      setDirty(false);
    } catch (err: unknown) {
      setParseError(err instanceof Error ? err.message : "Invalid JSON");
    }
  }

  function reset() {
    setText(JSON.stringify(config, null, 2));
    setDirty(false);
    setParseError(null);
  }

  return (
    <div className="card">
      <p className="muted" style={{ marginTop: 0 }}>
        Edit the entire <code>config_json</code> blob directly. Apply commits the
        text into the in-memory config; you still need to hit Save at the top
        to persist.
      </p>

      {modifiedKeys.length > 0 && (
        <div
          style={{
            marginBottom: 12,
            padding: 10,
            border: "1px solid var(--panel-border)",
            borderRadius: 6,
            background: "var(--panel-bg)",
            fontSize: 12,
          }}
        >
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <strong>Changes since last save: {modifiedKeys.length}</strong>
            <button
              type="button"
              className="ghost"
              onClick={() => setShowDiff((v) => !v)}
              style={{ padding: "2px 8px", fontSize: 12 }}
            >
              {showDiff ? "Hide diff" : "Show diff"}
            </button>
          </div>
          {showDiff && (
            <ul style={{ margin: "8px 0 0 16px", padding: 0, fontFamily: "ui-monospace, Menlo, monospace" }}>
              {modifiedKeys.slice(0, 30).map((k) => (
                <li key={k}>{k}</li>
              ))}
              {modifiedKeys.length > 30 && (
                <li className="muted">…and {modifiedKeys.length - 30} more</li>
              )}
            </ul>
          )}
        </div>
      )}

      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setDirty(true);
        }}
        spellCheck={false}
        readOnly={!writable}
        style={{ ...textareaStyle, minHeight: 480, fontSize: 12 }}
      />
      {parseError && <div className="error" style={{ marginTop: 8 }}>{parseError}</div>}
      {dirty && !parseError && (
        <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
          Unapplied edits in this tab — switching tabs keeps them, but
          another tab&apos;s edits will only be visible after you Apply
          or Reset.
        </div>
      )}
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 8, gap: 8 }}>
        <button className="ghost" onClick={reset} disabled={!dirty}>
          Reset
        </button>
        <button onClick={commit} disabled={!writable}>
          Apply
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lightweight key-path diff for the Raw JSON section. Returns dotted paths
// of every leaf where saved vs draft differ.
// ---------------------------------------------------------------------------
function diffKeys(saved: unknown, draft: unknown, path = ""): string[] {
  if (saved === draft) return [];
  if (
    saved === null ||
    draft === null ||
    typeof saved !== "object" ||
    typeof draft !== "object" ||
    Array.isArray(saved) !== Array.isArray(draft)
  ) {
    return [path || "(root)"];
  }
  const out: string[] = [];
  if (Array.isArray(saved) && Array.isArray(draft)) {
    if (saved.length !== draft.length) return [path || "(root)"];
    for (let i = 0; i < saved.length; i++) {
      out.push(...diffKeys(saved[i], draft[i], `${path}[${i}]`));
    }
    return out;
  }
  const keys = new Set([
    ...Object.keys(saved as Record<string, unknown>),
    ...Object.keys(draft as Record<string, unknown>),
  ]);
  for (const k of keys) {
    const childPath = path ? `${path}.${k}` : k;
    out.push(...diffKeys((saved as Record<string, unknown>)[k], (draft as Record<string, unknown>)[k], childPath));
  }
  return out;
}

// ---------------------------------------------------------------------------
// Tiny shared field components
// ---------------------------------------------------------------------------
function NumberField({
  label,
  hint,
  value,
  defaultValue,
  onChange,
  disabled,
  min,
  max,
  step,
  fluid,
}: {
  label: string;
  hint?: string;
  value: number | undefined | null;
  defaultValue: number | undefined;
  onChange: (v: number | undefined) => void;
  disabled?: boolean;
  min?: number;
  max?: number;
  step?: number;
  /** Stretch the input to fill its container instead of a fixed 200px width. */
  fluid?: boolean;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <input
        type="number"
        value={value ?? ""}
        placeholder={defaultValue != null ? String(defaultValue) : "(no limit)"}
        onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
        disabled={disabled}
        min={min}
        max={max}
        step={step}
        style={fluid ? { width: "100%", boxSizing: "border-box" } : { width: 200 }}
      />
      {hint && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

function ListField({
  label,
  hint,
  values,
  onChange,
  placeholder,
  disabled,
}: {
  label: string;
  hint?: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState("");
  return (
    <div className="field">
      <label>{label}</label>
      {hint && <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>{hint}</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
        {values.length === 0 && (
          <span className="muted" style={{ fontSize: 13 }}>(empty)</span>
        )}
        {values.map((v, i) => (
          <span
            key={`${v}-${i}`}
            className="tag"
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            {v}
            {!disabled && (
              <button
                className="ghost"
                onClick={() => onChange(values.filter((_, idx) => idx !== i))}
                style={{ padding: 0, fontSize: 12, border: "none", color: "var(--text-muted)" }}
              >
                ✕
              </button>
            )}
          </span>
        ))}
      </div>
      <div className="row" style={{ gap: 8 }}>
        <input
          type="text"
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          disabled={disabled}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (draft.trim()) {
                onChange([...values, draft.trim()]);
                setDraft("");
              }
            }
          }}
        />
        <button
          className="ghost"
          disabled={disabled || !draft.trim()}
          onClick={() => {
            onChange([...values, draft.trim()]);
            setDraft("");
          }}
        >
          Add
        </button>
      </div>
    </div>
  );
}

const textareaStyle: React.CSSProperties = {
  width: "100%",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: 13,
  padding: 12,
  background: "var(--paper)",
  color: "var(--text)",
  border: "1px solid var(--panel-border)",
  borderRadius: 6,
};

const selectStyle: React.CSSProperties = {
  padding: "8px 12px",
  background: "var(--paper)",
  color: "var(--text)",
  border: "1px solid var(--panel-border)",
  borderRadius: 6,
};
