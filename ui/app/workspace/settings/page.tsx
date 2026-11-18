"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AdminShell from "@/components/AdminShell";
import { AdminUser, api } from "@/lib/api";

/**
 * Workspace-level defaults page (superadmin-only).
 *
 * The backend stores a free-form JSON blob; the UI surfaces a small set
 * of well-known keys that the rest of the admin understands today
 * (default launcher label, default LLM model, default file size cap) and
 * a raw JSON escape hatch for everything else. We deliberately avoid
 * making this a typed monolith — the keys here are advisory; the
 * per-bot config still wins at the runtime layer.
 */

type Settings = Record<string, unknown>;

const KNOWN_FIELDS: { key: string; label: string; type: "text" | "number"; hint?: string }[] = [
  {
    key: "default_launcher_label",
    label: "Default launcher label",
    type: "text",
    hint: 'Pre-fills the "Ask anything" button on new bots.',
  },
  {
    key: "default_llm_model",
    label: "Default LLM model",
    type: "text",
    hint: "e.g. openai/gpt-4o-mini. Used as the LLM tab's initial value for new bots.",
  },
  {
    key: "default_temperature",
    label: "Default temperature",
    type: "number",
    hint: "0–2. Applied when a new bot doesn't override.",
  },
  {
    key: "default_max_file_size_mb",
    label: "Default max file size (MB)",
    type: "number",
    hint: "Pre-fills the per-bot file upload cap.",
  },
  {
    key: "otp_expires_in_minutes",
    label: "Widget passcode lifetime (minutes)",
    type: "number",
    hint: "1–30. How long a one-time passcode sent to a widget user stays valid. Default 5.",
  },
];

type PriceRow = { model: string; input: string; output: string };

/** `model_prices` (services/model_prices.py): model -> USD per 1M tokens. */
function priceRowsFrom(value: unknown): PriceRow[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>).map(([model, p]) => {
    const price = (p && typeof p === "object" ? p : {}) as Record<string, unknown>;
    return {
      model,
      input: price.input_per_1m === undefined ? "" : String(price.input_per_1m),
      output: price.output_per_1m === undefined ? "" : String(price.output_per_1m),
    };
  });
}

function pricesFromRows(rows: PriceRow[]): Record<string, { input_per_1m: number; output_per_1m: number }> | undefined {
  const out: Record<string, { input_per_1m: number; output_per_1m: number }> = {};
  for (const r of rows) {
    const model = r.model.trim();
    if (!model) continue;
    out[model] = { input_per_1m: Number(r.input || 0), output_per_1m: Number(r.output || 0) };
  }
  return Object.keys(out).length ? out : undefined;
}

function ModelPricesCard({
  value,
  disabled,
  onChange,
}: {
  value: unknown;
  disabled: boolean;
  onChange: (next: unknown) => void;
}) {
  const [rows, setRows] = useState<PriceRow[]>(() => priceRowsFrom(value));
  const serialized = JSON.stringify(value ?? null);
  useEffect(() => {
    setRows(priceRowsFrom(value));
    // Re-sync only when the stored value changes (reload, raw JSON edit),
    // not on every parent render: `value` is a fresh object each time.
  }, [serialized]);

  function update(next: PriceRow[]) {
    setRows(next);
    onChange(pricesFromRows(next));
  }

  return (
    <div className="card">
      <h2 style={{ marginTop: 0 }}>Model prices</h2>
      <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
        USD per million tokens, used for the spend figures on every bot&rsquo;s
        Analytics page. A name matches the model a bot records exactly, or by
        its last part (<code>gpt-4o-mini</code> also prices{" "}
        <code>openai/gpt-4o-mini</code>). Models without a price show
        &ldquo;—&rdquo;, not $0: enter 0 for a free self-hosted model. Spend
        counts the tokens of answers only.
      </p>
      {rows.length > 0 && (
        <table className="table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th style={{ textAlign: "left" }}>Model</th>
              <th style={{ textAlign: "right" }}>Input $ / 1M</th>
              <th style={{ textAlign: "right" }}>Output $ / 1M</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td>
                  <input
                    aria-label="Model name"
                    value={r.model}
                    disabled={disabled}
                    onChange={(e) => update(rows.map((x, j) => (j === i ? { ...x, model: e.target.value } : x)))}
                  />
                </td>
                <td style={{ textAlign: "right" }}>
                  <input
                    aria-label={`Input price for ${r.model || "model"}`}
                    type="number"
                    min={0}
                    step="any"
                    value={r.input}
                    disabled={disabled}
                    onChange={(e) => update(rows.map((x, j) => (j === i ? { ...x, input: e.target.value } : x)))}
                  />
                </td>
                <td style={{ textAlign: "right" }}>
                  <input
                    aria-label={`Output price for ${r.model || "model"}`}
                    type="number"
                    min={0}
                    step="any"
                    value={r.output}
                    disabled={disabled}
                    onChange={(e) => update(rows.map((x, j) => (j === i ? { ...x, output: e.target.value } : x)))}
                  />
                </td>
                <td style={{ textAlign: "right" }}>
                  <button
                    className="ghost"
                    disabled={disabled}
                    onClick={() => update(rows.filter((_, j) => j !== i))}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <button
        className="ghost"
        disabled={disabled}
        onClick={() => setRows([...rows, { model: "", input: "", output: "" }])}
        style={{ marginTop: 8 }}
      >
        Add model
      </button>
    </div>
  );
}

export default function WorkspaceSettingsPage() {
  const [me, setMe] = useState<AdminUser | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [rawJson, setRawJson] = useState<string>("{}");
  const [rawJsonError, setRawJsonError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [meRes, settingsRes] = await Promise.all([
        api.me(),
        api.getWorkspaceSettings(),
      ]);
      setMe(meRes);
      setSettings(settingsRes.settings ?? {});
      setRawJson(JSON.stringify(settingsRes.settings ?? {}, null, 2));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function setField(key: string, value: unknown) {
    setSettings((prev) => {
      const next = { ...(prev ?? {}) };
      if (value === "" || value === undefined || value === null) {
        delete next[key];
      } else {
        next[key] = value;
      }
      setRawJson(JSON.stringify(next, null, 2));
      setRawJsonError(null);
      return next;
    });
  }

  function onRawJsonChange(text: string) {
    setRawJson(text);
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        setRawJsonError(null);
        setSettings(parsed as Settings);
      } else {
        setRawJsonError("Top-level must be a JSON object.");
      }
    } catch (err: unknown) {
      setRawJsonError(err instanceof Error ? err.message : "Invalid JSON");
    }
  }

  async function onSave() {
    if (!settings) return;
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await api.putWorkspaceSettings(settings);
      setSettings(res.settings ?? {});
      setRawJson(JSON.stringify(res.settings ?? {}, null, 2));
      setSuccess("Saved.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <AdminShell>
      <div className="header">
        <div>
          <h1>Workspace settings</h1>
          <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
            Defaults applied to new bots and surfaced inline as &ldquo;Using
            workspace default&rdquo; in the per-bot editor.{" "}
            <Link href="/workspace/templates">Templates →</Link>
          </div>
        </div>
        <div className="row">
          <button className="ghost" onClick={refresh} disabled={loading || saving}>
            Reload
          </button>
          <button onClick={onSave} disabled={!me?.is_superadmin || saving || !!rawJsonError}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {success && <div className="success">{success}</div>}

      {!me?.is_superadmin && !loading && (
        <div className="banner advisory">
          You can read workspace defaults but only superadmins can change
          them.
        </div>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Common defaults</h2>
            {KNOWN_FIELDS.map((field) => {
              const value = settings?.[field.key];
              return (
                <div className="field" key={field.key}>
                  <label htmlFor={`ws-${field.key}`}>{field.label}</label>
                  <input
                    id={`ws-${field.key}`}
                    type={field.type}
                    value={
                      value === undefined || value === null ? "" : String(value)
                    }
                    onChange={(e) =>
                      setField(
                        field.key,
                        field.type === "number"
                          ? e.target.value === ""
                            ? ""
                            : Number(e.target.value)
                          : e.target.value,
                      )
                    }
                    disabled={!me?.is_superadmin}
                  />
                  {field.hint && (
                    <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                      {field.hint}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <ModelPricesCard
            value={settings?.model_prices}
            disabled={!me?.is_superadmin}
            onChange={(next) => setField("model_prices", next)}
          />

          <div className="card">
            <h2 style={{ marginTop: 0 }}>
              <label htmlFor="ws-raw-json">Raw JSON</label>
            </h2>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
              Everything else lives here. Keys you set in the form above
              are mirrored into this blob. The schema is intentionally
              loose — keys you define here surface as ghost text in the
              per-bot editor when a matching field reads them.
            </p>
            <textarea
              id="ws-raw-json"
              value={rawJson}
              onChange={(e) => onRawJsonChange(e.target.value)}
              rows={14}
              style={{
                width: "100%",
                fontFamily: "var(--font-mono)",
                fontSize: 12,
              }}
              spellCheck={false}
              disabled={!me?.is_superadmin}
              aria-invalid={!!rawJsonError}
            />
            {rawJsonError && (
              <div className="error" style={{ fontSize: 12, marginTop: 4 }}>
                {rawJsonError}
              </div>
            )}
          </div>
        </>
      )}
    </AdminShell>
  );
}
