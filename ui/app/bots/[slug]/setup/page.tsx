"use client";

import {
  ChangeEvent,
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowRight,
  Check,
  Info,
  KeyRound,
  Loader2,
  MessageCircle,
  Mic,
  Radio,
  RotateCw,
  Upload,
} from "lucide-react";
import { useBot } from "@/components/BotShell";
import WidgetPreview from "@/components/WidgetPreview";
import { ApiError, BotFile, api } from "@/lib/api";
import { markSetupDismissed } from "@/lib/setup-state";

type Cfg = Record<string, any>;
type StepId = "voice" | "knowledge" | "try" | "embed";

// Templates whose default config relies on RAG. If the user picked one of
// these we open the wizard on the Knowledge step with a primed dropzone.
// Keep in sync with `services/bot_templates.py` — Lead capture is the
// only non-RAG template in the current catalog.
const RAG_TEMPLATE_IDS = new Set(["support-faq", "internal-knowledge-base", "blank"]);

const ALL_STEPS: { id: StepId; label: string; icon: typeof Mic }[] = [
  { id: "voice", label: "Voice", icon: Mic },
  { id: "knowledge", label: "Knowledge", icon: Upload },
  { id: "try", label: "Try it", icon: MessageCircle },
  { id: "embed", label: "Embed", icon: Radio },
];

export default function BotSetupPage() {
  const { bot, currentRole, refreshBot } = useBot();
  const router = useRouter();
  const search = useSearchParams();
  const writable = currentRole === "owner" || currentRole === "admin";
  const templateId = search?.get("template") ?? null;
  const startKnowledge = templateId ? RAG_TEMPLATE_IDS.has(templateId) : false;
  const [activeStep, setActiveStep] = useState<StepId>(
    startKnowledge ? "knowledge" : "voice",
  );

  const [config, setConfig] = useState<Cfg>({});
  const [configVersion, setConfigVersion] = useState<number | null>(null);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [llmStatus, setLlmStatus] = useState<LlmStatus | null>(null);
  const [files, setFiles] = useState<BotFile[] | null>(null);
  const [fileTotal, setFileTotal] = useState<number | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const loadConfig = useCallback(async () => {
    try {
      const res = await api.getBotConfig(bot.slug);
      setConfig((res.config ?? {}) as Cfg);
      setConfigVersion(res.config_version);
    } catch {
      /* page stays interactive; user can still skip ahead */
    } finally {
      setConfigLoaded(true);
    }
  }, [bot.slug]);

  const loadFiles = useCallback(async () => {
    try {
      // The setup card only renders the first few files as a preview, so
      // a small page is plenty — the "N files in this bot" label uses
      // the server-side total separately.
      const res = await api.listBotFiles(bot.slug, { page: 1, pageSize: 10 });
      setFiles(res.items);
      setFileTotal(res.grand_total);
    } catch {
      setFiles([]);
      setFileTotal(0);
    }
  }, [bot.slug]);

  const loadKey = useCallback(async () => {
    try {
      const res = await api.getBotApiKeyStatus(bot.slug);
      setLlmStatus({
        missing: res.missing,
        hasDeploymentModel: res.has_deployment_model,
        hasDeploymentEndpoint: res.has_deployment_endpoint,
      });
    } catch {
      setLlmStatus(null);
    }
  }, [bot.slug]);

  useEffect(() => {
    loadConfig();
    loadFiles();
    loadKey();
  }, [loadConfig, loadFiles, loadKey]);

  function goNext() {
    const idx = ALL_STEPS.findIndex((s) => s.id === activeStep);
    if (idx < ALL_STEPS.length - 1) setActiveStep(ALL_STEPS[idx + 1].id);
    else finish();
  }

  function finish() {
    markSetupDismissed(bot.id);
    router.push(`/bots/${bot.slug}/dashboard`);
  }

  return (
    <>
      <div className="header">
        <div>
          <h1>Set up {bot.name}</h1>
          <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
            A short walk-through. You can skip any step and finish later from the dashboard.
          </div>
        </div>
        <Link
          href={`/bots/${bot.slug}/config`}
          className="ghost compact"
          style={{ textDecoration: "none" }}
        >
          Open full config
        </Link>
      </div>

      <SetupStepper active={activeStep} onSelect={setActiveStep} />

      {llmStatus && llmStatus.missing.length > 0 && (
        <LlmSetupBanner
          slug={bot.slug}
          config={config}
          version={configVersion}
          status={llmStatus}
          writable={writable}
          onSaved={async () => {
            await loadConfig();
            await loadKey();
            setReloadKey((k) => k + 1);
          }}
        />
      )}

      {activeStep === "voice" && (
        <VoiceStep
          slug={bot.slug}
          config={config}
          version={configVersion}
          loaded={configLoaded}
          writable={writable}
          onSaved={async () => {
            await loadConfig();
            await refreshBot();
            setReloadKey((k) => k + 1);
          }}
          onContinue={goNext}
        />
      )}

      {activeStep === "knowledge" && (
        <KnowledgeStep
          slug={bot.slug}
          writable={writable}
          files={files}
          fileTotal={fileTotal}
          onChanged={loadFiles}
          onContinue={goNext}
        />
      )}

      {activeStep === "try" && (
        <TryStep
          slug={bot.slug}
          reloadKey={reloadKey}
          starterMessage={firstStarterMessage(config)}
          onContinue={goNext}
        />
      )}

      {activeStep === "embed" && (
        <EmbedStep
          slug={bot.slug}
          botId={bot.id}
          onFinish={finish}
        />
      )}
    </>
  );
}

function firstStarterMessage(config: Cfg): string | null {
  const list = Array.isArray(config.starter_messages) ? config.starter_messages : null;
  if (!list) return null;
  for (const m of list) {
    if (typeof m === "string" && m.trim()) return m.trim();
  }
  return null;
}

function SetupStepper({
  active,
  onSelect,
}: {
  active: StepId;
  onSelect: (id: StepId) => void;
}) {
  const activeIdx = ALL_STEPS.findIndex((s) => s.id === active);
  return (
    <div
      style={{
        display: "flex",
        gap: 4,
        borderBottom: "1px solid var(--panel-border)",
        marginBottom: 16,
        overflowX: "auto",
      }}
      role="tablist"
      aria-label="Setup steps"
    >
      {ALL_STEPS.map((step, i) => {
        const Icon = step.icon;
        const isActive = step.id === active;
        const isDone = i < activeIdx;
        return (
          <button
            key={step.id}
            role="tab"
            aria-selected={isActive}
            className="ghost"
            onClick={() => onSelect(step.id)}
            style={{
              borderRadius: 0,
              border: "none",
              borderBottom: isActive ? "2px solid var(--accent)" : "2px solid transparent",
              color: isActive ? "var(--text)" : "var(--text-muted)",
              paddingBottom: 8,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            {isDone ? (
              <Check size={14} strokeWidth={2} style={{ color: "var(--success)" }} />
            ) : (
              <Icon size={14} strokeWidth={1.75} />
            )}
            {i + 1}. {step.label}
          </button>
        );
      })}
    </div>
  );
}

function StepCard({
  title,
  description,
  children,
  primaryLabel,
  onPrimary,
  primaryDisabled,
  primarySpinner,
  secondaryLabel = "Skip for now",
  onSecondary,
}: {
  title: string;
  description: string;
  children: ReactNode;
  primaryLabel: string;
  onPrimary: () => void;
  primaryDisabled?: boolean;
  primarySpinner?: boolean;
  secondaryLabel?: string;
  onSecondary: () => void;
}) {
  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>{title}</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        {description}
      </p>
      {children}
      <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
        <button type="button" className="ghost" onClick={onSecondary}>
          {secondaryLabel}
        </button>
        <button type="button" onClick={onPrimary} disabled={primaryDisabled}>
          {primarySpinner ? (
            <Loader2 size={14} strokeWidth={1.75} className="spin" />
          ) : null}
          {primaryLabel}
        </button>
      </div>
    </div>
  );
}

type LlmStatus = {
  missing: ("model" | "endpoint" | "api_key")[];
  hasDeploymentModel: boolean;
  hasDeploymentEndpoint: boolean;
};

function LlmSetupBanner({
  slug,
  config,
  version,
  status,
  writable,
  onSaved,
}: {
  slug: string;
  config: Cfg;
  version: number | null;
  status: LlmStatus;
  writable: boolean;
  onSaved: () => Promise<void>;
}) {
  const llm: Cfg =
    config?.llm && typeof config.llm === "object" && !Array.isArray(config.llm)
      ? (config.llm as Cfg)
      : {};
  const currentModel = typeof llm.model === "string" ? llm.model : "";
  const currentEndpoint = typeof llm.api_endpoint === "string" ? llm.api_endpoint : "";
  const needModel = status.missing.includes("model");
  const needEndpoint = status.missing.includes("endpoint");
  const needKey = status.missing.includes("api_key");

  const [model, setModel] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready =
    (!needModel || model.trim() !== "") &&
    (!needEndpoint || endpoint.trim() !== "") &&
    (!needKey || key.trim() !== "");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setSaving(true);
    setError(null);
    try {
      if (needModel || needEndpoint) {
        const nextLlm: Cfg = { ...llm };
        if (needModel) nextLlm.model = model.trim();
        if (needEndpoint) nextLlm.api_endpoint = endpoint.trim();
        await api.putBotConfig(slug, { ...config, llm: nextLlm }, version ?? undefined);
      }
      if (needKey) await api.putBotApiKey(slug, key.trim());
      setKey("");
      await onSaved();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setError("Someone else just edited this bot. Reload and try again.");
      } else {
        setError(err instanceof Error ? err.message : "Failed to save");
      }
    } finally {
      setSaving(false);
    }
  }

  const value = (current: string, hasDefault: boolean) =>
    current ? (
      <code style={{ fontFamily: "var(--font-mono)" }}>{current}</code>
    ) : hasDefault ? (
      <span className="muted">deployment default</span>
    ) : (
      <span className="error">not set</span>
    );

  return (
    <div className="banner warning banner-icon" style={{ marginBottom: 16 }}>
      <KeyRound strokeWidth={1.75} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>Choose the LLM this bot uses.</div>
        <div style={{ fontSize: 13, marginTop: 2, color: "var(--muted)" }}>
          The bot can&apos;t answer messages until it has a model, an
          OpenAI-compatible endpoint and an API key. Visitors see a short
          &ldquo;not available&rdquo; reply until then.
        </div>
        {!writable || !(needModel || needEndpoint || needKey) ? (
          <dl
            style={{
              margin: "8px 0 0",
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              columnGap: 8,
              rowGap: 2,
              fontSize: 12,
            }}
          >
            <dt className="muted">Model</dt>
            <dd style={{ margin: 0 }}>{value(currentModel, status.hasDeploymentModel)}</dd>
            <dt className="muted">Endpoint</dt>
            <dd style={{ margin: 0 }}>{value(currentEndpoint, status.hasDeploymentEndpoint)}</dd>
            <dt className="muted">API key</dt>
            <dd style={{ margin: 0 }}>{needKey ? <span className="error">not set</span> : "set"}</dd>
          </dl>
        ) : (
          <form onSubmit={onSubmit} style={{ marginTop: 8, display: "grid", gap: 8 }}>
            {needModel && (
              <input
                type="text"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="Model, e.g. gpt-4o-mini"
                aria-label="Model"
                style={{ fontFamily: "var(--font-mono)" }}
                autoComplete="off"
              />
            )}
            {needEndpoint && (
              <input
                type="url"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder="Endpoint, e.g. https://api.openai.com/v1"
                aria-label="API endpoint"
                style={{ fontFamily: "var(--font-mono)" }}
                autoComplete="off"
              />
            )}
            {needKey && (
              <input
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="API key, e.g. sk-…"
                aria-label="API key"
                style={{ fontFamily: "var(--font-mono)" }}
                autoComplete="off"
              />
            )}
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button type="submit" disabled={saving || !ready}>
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        )}
        <div style={{ marginTop: 8, fontSize: 12 }}>
          More options (temperature, changing these later) are in{" "}
          <Link href={`/bots/${slug}/config?tab=llm`}>LLM settings</Link>.
        </div>
        {error && <div className="error" style={{ marginTop: 6 }}>{error}</div>}
      </div>
    </div>
  );
}

function VoiceStep({
  slug,
  config,
  version,
  loaded,
  writable,
  onSaved,
  onContinue,
}: {
  slug: string;
  config: Cfg;
  version: number | null;
  loaded: boolean;
  writable: boolean;
  onSaved: () => Promise<void>;
  onContinue: () => void;
}) {
  const currentWelcome =
    (config.global_prompts && typeof config.global_prompts === "object"
      ? String((config.global_prompts as Cfg).welcome_message ?? "")
      : "") || "";
  const [welcome, setWelcome] = useState(currentWelcome);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-sync local state once the loaded config arrives. After that, leave
  // the field alone so the user's typing isn't clobbered by a re-fetch.
  const seededRef = useRef(false);
  useEffect(() => {
    if (loaded && !seededRef.current) {
      setWelcome(currentWelcome);
      seededRef.current = true;
    }
  }, [loaded, currentWelcome]);

  async function save() {
    if (!writable) {
      onContinue();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const next = {
        ...config,
        global_prompts: {
          ...(config.global_prompts ?? {}),
          welcome_message: welcome.trim() || undefined,
        },
      };
      await api.putBotConfig(slug, next, version ?? undefined);
      await onSaved();
      onContinue();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 409) {
        setError("Someone else just edited this bot. Reload and try again.");
      } else {
        setError(err instanceof Error ? err.message : "Save failed");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <StepCard
      title="Give your bot a voice"
      description="The first thing visitors see when they open the chat. You can refine all prompts and the LLM in Config later."
      primaryLabel={saving ? "Saving…" : "Save & continue"}
      onPrimary={save}
      primaryDisabled={!writable || saving || !loaded}
      primarySpinner={saving}
      onSecondary={onContinue}
    >
      <div className="field">
        <label htmlFor="setup-welcome">Welcome message</label>
        <textarea
          id="setup-welcome"
          rows={3}
          placeholder="Hi! How can I help today?"
          value={welcome}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setWelcome(e.target.value)}
          disabled={!writable || !loaded}
          style={{ width: "100%", fontFamily: "inherit" }}
        />
      </div>
      {error && <div className="error">{error}</div>}
    </StepCard>
  );
}

function KnowledgeStep({
  slug,
  writable,
  files,
  fileTotal,
  onChanged,
  onContinue,
}: {
  slug: string;
  writable: boolean;
  files: BotFile[] | null;
  fileTotal: number | null;
  onChanged: () => Promise<void>;
  onContinue: () => void;
}) {
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  async function upload(list: FileList | null) {
    if (!list || list.length === 0) return;
    if (!writable) {
      setError("Your role doesn't permit uploads on this bot.");
      return;
    }
    setError(null);
    const items = Array.from(list);
    setUploading((n) => n + items.length);
    await Promise.allSettled(
      items.map((file) =>
        api
          .uploadBotFile(slug, file, undefined, "document")
          .promise.catch((err: unknown) => {
            setError(
              err instanceof ApiError
                ? `${err.status}: ${err.message}`
                : err instanceof Error
                ? err.message
                : "Upload failed",
            );
          })
          .finally(() => setUploading((n) => Math.max(0, n - 1))),
      ),
    );
    await onChanged();
  }

  // Prefer the server-side total when available; fall back to the
  // sample length so the gate works before the first fetch resolves.
  const fileCount = fileTotal ?? files?.length ?? 0;

  return (
    <StepCard
      title="Add some knowledge"
      description="Drop documents your bot can answer from. PDF, Markdown, plain text — chunked and embedded automatically. Without files, the bot will say it doesn't know."
      primaryLabel={fileCount > 0 ? "Continue" : "Continue without files"}
      onPrimary={onContinue}
      primaryDisabled={uploading > 0}
      onSecondary={onContinue}
    >
      <div
        className={`dropzone ${dragActive ? "active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (writable) setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragActive(false);
          if (writable) upload(e.dataTransfer.files);
        }}
      >
        <p style={{ marginTop: 0 }}>
          {writable
            ? "Drag and drop files here, or"
            : "Your role doesn't permit uploads on this bot."}
        </p>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            upload(e.target.files);
            if (fileInputRef.current) fileInputRef.current.value = "";
          }}
        />
        <button
          type="button"
          className="ghost"
          disabled={!writable}
          onClick={() => fileInputRef.current?.click()}
        >
          Choose files
        </button>
      </div>

      {uploading > 0 && (
        <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          Uploading {uploading} file{uploading === 1 ? "" : "s"}…
        </p>
      )}

      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}

      {files && files.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
            {fileCount} file{fileCount === 1 ? "" : "s"} in this bot:
          </div>
          <ul style={{ margin: 0, paddingLeft: 16, fontSize: 13 }}>
            {files.slice(0, 5).map((f) => (
              <li key={f.id}>
                {f.file_name}{" "}
                <FileEmbedStatus status={f.embedding_status} />
              </li>
            ))}
            {fileCount > 5 && (
              <li className="muted">…and {fileCount - 5} more.</li>
            )}
          </ul>
        </div>
      )}
    </StepCard>
  );
}

function FileEmbedStatus({ status }: { status?: string }) {
  if (!status) return null;
  if (status === "completed" || status === "success") {
    return (
      <span style={{ color: "var(--success)", fontSize: 12 }}>· embedded</span>
    );
  }
  if (status === "failed") {
    return <span style={{ color: "var(--error)", fontSize: 12 }}>· failed</span>;
  }
  return (
    <span className="muted" style={{ fontSize: 12 }}>· {status}</span>
  );
}

function TryStep({
  slug,
  reloadKey,
  starterMessage,
  onContinue,
}: {
  slug: string;
  reloadKey: number;
  starterMessage: string | null;
  onContinue: () => void;
}) {
  return (
    <StepCard
      title="Try it out"
      description="The widget loads in this page exactly as your visitors will see it. Open the chat in the bottom-right and send a message."
      primaryLabel="Continue"
      onPrimary={onContinue}
      secondaryLabel="Skip"
      onSecondary={onContinue}
    >
      <div
        className="banner advisory banner-icon"
        style={{ marginBottom: 12 }}
      >
        <Info strokeWidth={1.75} />
        <div>
          {starterMessage ? (
            <>
              Try asking: <code>{starterMessage}</code>
            </>
          ) : (
            <>Try saying hello, or ask something only your knowledge would know.</>
          )}
        </div>
      </div>
      <WidgetPreview slug={slug} reloadKey={reloadKey} />
    </StepCard>
  );
}

function EmbedStep({
  slug,
  botId,
  onFinish,
}: {
  slug: string;
  botId: string;
  onFinish: () => void;
}) {
  return (
    <StepCard
      title="Embed it on your site"
      description="The Channels tab shows the snippet your developers paste into the customer site, plus allowed-origin rules and a streaming chat contract."
      primaryLabel="Finish setup"
      onPrimary={onFinish}
      secondaryLabel="Open Channels"
      onSecondary={() => {
        markSetupDismissed(botId);
        window.location.assign(`/bots/${slug}/channels`);
      }}
    >
      <div style={{ display: "grid", gap: 12 }}>
        <Link
          href={`/bots/${slug}/channels`}
          className="bot-card"
          style={{ padding: 14, textDecoration: "none", color: "inherit" }}
        >
          <div className="row" style={{ alignItems: "center", gap: 10 }}>
            <Radio size={18} strokeWidth={1.75} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>Get the embed snippet</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                Allowed origins, streaming endpoint, sample curl.
              </div>
            </div>
            <ArrowRight size={16} strokeWidth={1.75} />
          </div>
        </Link>
        <Link
          href={`/bots/${slug}/config`}
          className="bot-card"
          style={{ padding: 14, textDecoration: "none", color: "inherit" }}
        >
          <div className="row" style={{ alignItems: "center", gap: 10 }}>
            <RotateCw size={18} strokeWidth={1.75} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>Tune the full config</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                Skills, actions, voice, knowledge, LLM, limits, raw JSON.
              </div>
            </div>
            <ArrowRight size={16} strokeWidth={1.75} />
          </div>
        </Link>
      </div>
    </StepCard>
  );
}
