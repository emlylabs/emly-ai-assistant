"use client";

import type { MessageUsageByModel } from "@/lib/api";

type ModelUsageCardProps = {
  /** Per-model token aggregates from `/bots/{slug}/messages/by-model`.
   * `null` while the request is in flight. */
  rows: MessageUsageByModel[] | null;
  /** Window length in days, surfaced in the subtitle. */
  rangeDays: number;
  /** Prior-period rows for the same window length. When provided the
   * header gains a "vs prior" total-cost delta. */
  priorRows?: MessageUsageByModel[] | null;
};

/**
 * Per-`model_used` breakdown of token usage and dollar cost. Reads
 * server-aggregated rows so the count is exact (not a sample of the
 * recently-loaded message list). Honest by design:
 *  - if a model isn't in the local price table we still surface its
 *    tokens but show `—` for cost so the gap reads as a missing
 *    pricing entry rather than a free model.
 *  - if no assistant turns landed with token telemetry, the card
 *    shows an empty-state hint rather than `$0.00`.
 */

// Per-1K-token list prices in USD. Owned by this component since the
// analytics page also reads the same table for the LLM-spend KPI; the
// page imports it from here so the two stay in lockstep.
export const PRICE_TABLE_PER_1K: Record<string, { prompt: number; completion: number }> = {
  "openai/gpt-4o": { prompt: 0.005, completion: 0.015 },
  "openai/gpt-4o-mini": { prompt: 0.00015, completion: 0.0006 },
  "openai/gpt-4-turbo": { prompt: 0.01, completion: 0.03 },
  "openai/gpt-4": { prompt: 0.03, completion: 0.06 },
  "openai/gpt-3.5-turbo": { prompt: 0.0005, completion: 0.0015 },
  // Fallbacks for cases where the runtime persists the bare model name
  // without the `<provider>/` prefix.
  "gpt-4o": { prompt: 0.005, completion: 0.015 },
  "gpt-4o-mini": { prompt: 0.00015, completion: 0.0006 },
  "gpt-4-turbo": { prompt: 0.01, completion: 0.03 },
  "gpt-4": { prompt: 0.03, completion: 0.06 },
  "gpt-3.5-turbo": { prompt: 0.0005, completion: 0.0015 },
};

/** Apply pricing to a usage row. Returns `null` when the model is
 * absent from the price table — caller distinguishes "no price" from
 * "$0". */
export function priceUsageRow(row: MessageUsageByModel): number | null {
  const price = PRICE_TABLE_PER_1K[row.model];
  if (!price) return null;
  return (
    (row.prompt_tokens / 1000) * price.prompt +
    (row.completion_tokens / 1000) * price.completion
  );
}

/** Aggregate spend across rows. Returns the total of priced rows plus
 * a `priced` flag so the caller knows whether at least one row matched
 * the table. */
export function totalSpend(rows: MessageUsageByModel[]): { total: number; priced: boolean } {
  let total = 0;
  let priced = false;
  for (const row of rows) {
    const cost = priceUsageRow(row);
    if (cost !== null) {
      priced = true;
      total += cost;
    }
  }
  return { total, priced };
}

function formatUsd(n: number): string {
  if (n < 0.01) return `$${n.toFixed(4)}`;
  if (n < 1) return `$${n.toFixed(3)}`;
  return `$${n.toFixed(2)}`;
}

function spendDelta(current: number, prior: number): string {
  if (prior === 0) return "";
  const pct = ((current - prior) / prior) * 100;
  if (Math.abs(pct) < 0.05) return "0.0% vs prior";
  const sign = pct > 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}% vs prior`;
}

export default function ModelUsageCard({ rows, rangeDays, priorRows }: ModelUsageCardProps) {
  if (rows === null) {
    return (
      <div className="card" style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
        Loading model usage…
      </div>
    );
  }

  // Sort by cost desc (priced first), then by turn count.
  const enriched = rows.map((row) => ({ ...row, costUsd: priceUsageRow(row) }));
  enriched.sort((a, b) => (b.costUsd ?? 0) - (a.costUsd ?? 0) || b.turns - a.turns);

  const totalPrompt = enriched.reduce((a, r) => a + r.prompt_tokens, 0);
  const totalCompletion = enriched.reduce((a, r) => a + r.completion_tokens, 0);
  const total = totalSpend(rows);
  const hasUnpriced = enriched.some((r) => r.costUsd === null);

  return (
    <div className="card" style={{ padding: 0 }}>
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
        }}
      >
        <div>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Model usage &amp; cost</div>
          <div className="muted" style={{ fontSize: 11.5 }}>
            assistant turns · last {rangeDays} days
          </div>
        </div>
        <span
          className="muted"
          style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}
        >
          {totalPrompt.toLocaleString()} in · {totalCompletion.toLocaleString()} out · {formatUsd(total.total)}
          {priorRows && total.priced && (() => {
            const prior = totalSpend(priorRows);
            if (!prior.priced) return null;
            return (
              <span style={{ marginLeft: 8 }}>
                · {spendDelta(total.total, prior.total)}
              </span>
            );
          })()}
        </span>
      </div>

      {enriched.length === 0 ? (
        <div style={{ padding: 16, color: "var(--muted)", fontSize: 12.5 }}>
          No assistant turns with <code>model_used</code> in the window. Phase 2
          telemetry capture lands these — once new traffic flows through, this
          card fills in.
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left" }}>Model</th>
                <th style={{ textAlign: "right" }}>Turns</th>
                <th style={{ textAlign: "right" }}>Prompt tok.</th>
                <th style={{ textAlign: "right" }}>Completion tok.</th>
                <th style={{ textAlign: "right" }}>Cost</th>
              </tr>
            </thead>
            <tbody>
              {enriched.map((r) => (
                <tr key={r.model}>
                  <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{r.model}</td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {r.turns.toLocaleString()}
                  </td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {r.prompt_tokens.toLocaleString()}
                  </td>
                  <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {r.completion_tokens.toLocaleString()}
                  </td>
                  <td
                    style={{
                      textAlign: "right",
                      fontFamily: "var(--font-mono)",
                      color: r.costUsd === null ? "var(--muted)" : undefined,
                    }}
                    title={r.costUsd === null ? "No price entry for this model" : undefined}
                  >
                    {r.costUsd === null ? "— no price" : formatUsd(r.costUsd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasUnpriced && (
        <div
          className="muted"
          style={{ padding: "10px 16px", fontSize: 11.5, borderTop: "1px solid var(--border)" }}
        >
          Some models have no entry in the local price table — their tokens are
          counted but excluded from the total cost. Add them to{" "}
          <code>PRICE_TABLE_PER_1K</code> in <code>ModelUsageCard.tsx</code>.
        </div>
      )}
    </div>
  );
}
