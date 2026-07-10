"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  CrawlJob,
  CrawlJobOptions,
  CrawlJobPage,
  CrawlJobStatus,
  DOCUMENT_TYPES,
  DocumentType,
  api,
} from "@/lib/api";

const POLL_MS = 3000;

const DEFAULT_OPTIONS: CrawlJobOptions = {
  sameHostOnly: true,
  pathPrefix: "",
  includeRegex: "",
  excludeRegex: "",
  maxDepth: 3,
  maxPages: 100,
  respectRobots: true,
  skipThinPages: true,
  thinThreshold: 200,
  skipAlreadyImported: true,
  politeDelayMs: 250,
  concurrency: 3,
};

const ACTIVE_STATUSES: CrawlJobStatus[] = ["running", "paused"];

export default function CrawlTab({
  slug,
  writable,
  onCrawlComplete,
}: {
  slug: string;
  writable: boolean;
  onCrawlComplete?: () => void;
}) {
  const [jobs, setJobs] = useState<CrawlJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  const refreshJobs = useCallback(async () => {
    try {
      const list = await api.listCrawlJobs(slug);
      setJobs(list);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load crawl jobs");
    } finally {
      setJobsLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    refreshJobs();
  }, [refreshJobs]);

  // Poll the jobs list while any job is active so the table updates live.
  useEffect(() => {
    const anyActive = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
    if (!anyActive) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refreshJobs();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [jobs, refreshJobs]);

  const selectedJob = useMemo(
    () => jobs.find((j) => j.id === selectedJobId) ?? null,
    [jobs, selectedJobId]
  );

  return (
    <div>
      <div className="banner advisory" style={{ marginBottom: 12 }}>
        <strong>How this works:</strong> the crawler runs on the server. You can
        close this tab — progress is recoverable and the bot will keep importing
        in the background. Server-side fetch only, so JavaScript-rendered pages
        and login-gated content won&apos;t be captured.
      </div>

      {!writable && (
        <div className="banner warning" style={{ marginBottom: 12 }}>
          Your role doesn&apos;t permit crawl jobs on this bot.
        </div>
      )}

      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

      <NewJobForm
        slug={slug}
        writable={writable}
        onCreated={(j) => {
          setJobs((prev) => [j, ...prev]);
          setSelectedJobId(j.id);
        }}
      />

      <JobsList
        jobs={jobs}
        loading={jobsLoading}
        selectedJobId={selectedJobId}
        onSelect={setSelectedJobId}
      />

      {selectedJob && (
        <JobDetail
          slug={slug}
          job={selectedJob}
          writable={writable}
          onChange={(j) => {
            setJobs((prev) => prev.map((x) => (x.id === j.id ? j : x)));
            if (j.status === "completed" || j.status === "cancelled" || j.status === "failed") {
              onCrawlComplete?.();
            }
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------
function NewJobForm({
  slug,
  writable,
  onCreated,
}: {
  slug: string;
  writable: boolean;
  onCreated: (j: CrawlJob) => void;
}) {
  const [seedUrl, setSeedUrl] = useState("");
  const [documentType, setDocumentType] = useState<DocumentType>("web_page");
  const [options, setOptions] = useState<CrawlJobOptions>(DEFAULT_OPTIONS);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prefixDirty, setPrefixDirty] = useState(false);

  // Auto-fill path prefix from seed URL until the user types over it.
  useEffect(() => {
    if (prefixDirty || !seedUrl) return;
    try {
      const u = new URL(seedUrl);
      const path = u.pathname || "/";
      const idx = path.lastIndexOf("/");
      const dir = idx >= 0 ? path.slice(0, idx + 1) : "/";
      setOptions((prev) => ({ ...prev, pathPrefix: dir === "/" ? "" : dir }));
    } catch {
      // ignore — mid-typing
    }
  }, [seedUrl, prefixDirty]);

  function patch<K extends keyof CrawlJobOptions>(key: K, value: CrawlJobOptions[K]) {
    setOptions((prev) => ({ ...prev, [key]: value }));
  }

  const seedValid = (() => {
    try {
      const u = new URL(seedUrl);
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  })();

  async function start() {
    if (!writable || !seedValid) return;
    setCreating(true);
    setError(null);
    try {
      const job = await api.createCrawlJob(slug, {
        seed_url: seedUrl,
        document_type: documentType,
        options,
      });
      setSeedUrl("");
      setPrefixDirty(false);
      onCreated(job);
    } catch (err: unknown) {
      setError(
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
          ? err.message
          : "Failed to create crawl job"
      );
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 14 }}>Start a crawl</h2>

      <div className="row" style={{ gap: 12 }}>
        <div className="field" style={{ flex: 1 }}>
          <label>Seed URL</label>
          <input
            type="url"
            placeholder="https://docs.example.com/getting-started/"
            value={seedUrl}
            onChange={(e) => setSeedUrl(e.target.value)}
            disabled={creating}
          />
        </div>
        <div className="field" style={{ width: 200 }}>
          <label>Document type</label>
          <select
            value={documentType}
            onChange={(e) => setDocumentType(e.target.value as DocumentType)}
            disabled={creating}
          >
            {DOCUMENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="row" style={{ gap: 12 }}>
        <div className="field" style={{ flex: 1 }}>
          <label>Path prefix</label>
          <input
            type="text"
            placeholder="/docs/ (auto-filled from seed)"
            value={options.pathPrefix}
            onChange={(e) => {
              setPrefixDirty(true);
              patch("pathPrefix", e.target.value);
            }}
            disabled={creating}
          />
        </div>
        <div className="field" style={{ width: 180 }}>
          <label>
            <input
              type="checkbox"
              checked={options.sameHostOnly}
              onChange={(e) => patch("sameHostOnly", e.target.checked)}
              disabled={creating}
            />{" "}
            Same host only
          </label>
        </div>
      </div>

      <div className="row" style={{ gap: 12 }}>
        <div className="field" style={{ flex: 1 }}>
          <label>Include regex</label>
          <input
            type="text"
            placeholder="(optional)"
            value={options.includeRegex}
            onChange={(e) => patch("includeRegex", e.target.value)}
            disabled={creating}
          />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label>Exclude regex</label>
          <input
            type="text"
            placeholder="(optional)"
            value={options.excludeRegex}
            onChange={(e) => patch("excludeRegex", e.target.value)}
            disabled={creating}
          />
        </div>
      </div>

      <div className="row" style={{ gap: 12 }}>
        <div className="field" style={{ width: 110 }}>
          <label>Max depth</label>
          <input
            type="number"
            min={0}
            max={20}
            value={options.maxDepth}
            onChange={(e) => patch("maxDepth", Number(e.target.value))}
            disabled={creating}
          />
        </div>
        <div className="field" style={{ width: 130 }}>
          <label>Max pages</label>
          <input
            type="number"
            min={1}
            max={5000}
            value={options.maxPages}
            onChange={(e) => patch("maxPages", Number(e.target.value))}
            disabled={creating}
          />
        </div>
        <div className="field" style={{ width: 130 }}>
          <label>Concurrency</label>
          <input
            type="number"
            min={1}
            max={10}
            value={options.concurrency}
            onChange={(e) => patch("concurrency", Number(e.target.value))}
            disabled={creating}
          />
        </div>
      </div>

      <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
        <label>
          <input
            type="checkbox"
            checked={options.respectRobots}
            onChange={(e) => patch("respectRobots", e.target.checked)}
            disabled={creating}
          />{" "}
          Respect robots.txt
        </label>
        <label>
          <input
            type="checkbox"
            checked={options.skipThinPages}
            onChange={(e) => patch("skipThinPages", e.target.checked)}
            disabled={creating}
          />{" "}
          Skip thin pages (&lt;{options.thinThreshold} chars)
        </label>
        <label>
          <input
            type="checkbox"
            checked={options.skipAlreadyImported}
            onChange={(e) => patch("skipAlreadyImported", e.target.checked)}
            disabled={creating}
          />{" "}
          Skip already imported
        </label>
        <label>
          <input
            type="checkbox"
            checked={options.politeDelayMs > 0}
            onChange={(e) => patch("politeDelayMs", e.target.checked ? 250 : 0)}
            disabled={creating}
          />{" "}
          Polite delay (250ms / host)
        </label>
      </div>

      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}

      <div className="row" style={{ marginTop: 12, gap: 8 }}>
        <button onClick={start} disabled={!writable || !seedValid || creating}>
          {creating ? "Starting…" : "Start crawl"}
        </button>
      </div>
    </div>
  );
}

function JobsList({
  jobs,
  loading,
  selectedJobId,
  onSelect,
}: {
  jobs: CrawlJob[];
  loading: boolean;
  selectedJobId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 14 }}>
        Crawl jobs ({jobs.length})
      </h2>
      {loading ? (
        <p className="muted">Loading…</p>
      ) : jobs.length === 0 ? (
        <p className="muted">No crawl jobs yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Seed URL</th>
              <th style={{ width: 120 }}>Type</th>
              <th style={{ width: 110 }}>Status</th>
              <th style={{ width: 220 }}>Progress</th>
              <th style={{ width: 160 }}>Created</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr
                key={j.id}
                onClick={() => onSelect(j.id)}
                style={{
                  cursor: "pointer",
                  background: selectedJobId === j.id ? "rgba(255,255,255,0.04)" : undefined,
                }}
              >
                <td className="cell-truncate" style={{ maxWidth: 320 }} title={j.seed_url}>
                  {j.seed_url}
                </td>
                <td className="muted">{j.document_type.replace(/_/g, " ")}</td>
                <td>
                  <span className={`status-pill ${jobStatusClass(j.status)}`}>{j.status}</span>
                </td>
                <td className="muted" style={{ fontSize: 12 }}>
                  {j.pages_done}✓ {j.pages_skipped}↷ {j.pages_failed}✗ / {j.pages_total}
                </td>
                <td className="muted" style={{ whiteSpace: "nowrap", fontSize: 12 }}>
                  {j.created_on ? new Date(j.created_on).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function JobDetail({
  slug,
  job,
  writable,
  onChange,
}: {
  slug: string;
  job: CrawlJob;
  writable: boolean;
  onChange: (j: CrawlJob) => void;
}) {
  const [pages, setPages] = useState<CrawlJobPage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isActive = job.status === "running" || job.status === "paused";

  const refreshPages = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listCrawlJobPages(slug, job.id, { limit: 500 });
      setPages(list);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load pages");
    } finally {
      setLoading(false);
    }
  }, [slug, job.id]);

  useEffect(() => {
    refreshPages();
  }, [refreshPages]);

  // Poll while job is running.
  useEffect(() => {
    if (!isActive) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refreshPages();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [isActive, refreshPages]);

  async function transition(action: "pause" | "resume" | "cancel") {
    try {
      const updated =
        action === "pause"
          ? await api.pauseCrawlJob(slug, job.id)
          : action === "resume"
          ? await api.resumeCrawlJob(slug, job.id)
          : await api.cancelCrawlJob(slug, job.id);
      onChange(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : `${action} failed`);
    }
  }

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 style={{ marginTop: 0, fontSize: 14 }} title={job.id}>
          Job — {job.seed_url}
        </h2>
        <div className="row" style={{ gap: 8 }}>
          {writable && job.status === "running" && (
            <button className="ghost" onClick={() => transition("pause")}>
              Pause
            </button>
          )}
          {writable && job.status === "paused" && (
            <button className="ghost" onClick={() => transition("resume")}>
              Resume
            </button>
          )}
          {writable && isActive && (
            <button className="danger" onClick={() => transition("cancel")}>
              Cancel
            </button>
          )}
          <button className="ghost" onClick={refreshPages}>
            Refresh
          </button>
        </div>
      </div>

      <div className="row" style={{ gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
        <Counter label="Total" value={job.pages_total} />
        <Counter label="Done" value={job.pages_done} accent="var(--success)" />
        <Counter label="Skipped" value={job.pages_skipped} accent="var(--text-muted)" />
        <Counter label="Failed" value={job.pages_failed} accent="var(--error)" />
        <Counter label="Pending" value={Math.max(0, job.pages_total - job.pages_done - job.pages_skipped - job.pages_failed)} />
      </div>

      {job.error_message && (
        <div className="error" style={{ marginBottom: 12 }}>{job.error_message}</div>
      )}

      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

      {loading && pages.length === 0 ? (
        <p className="muted">Loading pages…</p>
      ) : pages.length === 0 ? (
        <p className="muted">No pages yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>URL</th>
              <th style={{ width: 60 }}>Depth</th>
              <th style={{ width: 110 }}>State</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {pages.map((p) => (
              <tr key={p.id}>
                <td className="cell-truncate" style={{ maxWidth: 360 }} title={p.url}>
                  {p.url}
                </td>
                <td className="muted">{p.depth}</td>
                <td>
                  <span className={`status-pill ${pageStateClass(p.state)}`} title={p.reason ?? undefined}>
                    {p.state === "embedding" && "⟳ "}
                    {p.state}
                  </span>
                </td>
                <td className="muted cell-truncate" style={{ maxWidth: 280 }}>
                  {p.reason ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Counter({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div style={{ minWidth: 90 }}>
      <div className="muted" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4 }}>
        {label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 600, color: accent ?? "var(--text)" }}>{value}</div>
    </div>
  );
}

function jobStatusClass(s: CrawlJobStatus): string {
  switch (s) {
    case "running":
      return "embedding";
    case "paused":
      return "pending";
    case "completed":
      return "embedded";
    case "cancelled":
      return "pending";
    case "failed":
      return "failed";
  }
}

function pageStateClass(s: CrawlJobPage["state"]): string {
  switch (s) {
    case "done":
      return "embedded";
    case "embedding":
    case "fetching":
    case "uploading":
      return "embedding";
    case "failed":
      return "failed";
    case "skipped":
    case "queued":
    default:
      return "pending";
  }
}
