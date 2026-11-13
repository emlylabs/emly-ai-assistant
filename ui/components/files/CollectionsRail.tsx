"use client";

import type { DocumentType } from "@/lib/api";

/** Logical buckets shown in the rail. We collapse the 6-value `DocumentType`
 * enum into 5 user-facing groups so the rail isn't cluttered with
 * single-item categories — `support_article`, `faq` and `product` all
 * land under the closest UI-friendly heading. */
type CollectionKey = "all" | "documents" | "pages" | "faq" | "products" | "other";

type Collection = {
  key: CollectionKey;
  label: string;
  /** The raw `document_type` values that fall into this bucket. Empty
   * for `all`, which means "no filter". */
  documentTypes: DocumentType[];
};

const COLLECTIONS: Collection[] = [
  { key: "all", label: "All", documentTypes: [] },
  { key: "documents", label: "Documents", documentTypes: ["document", "support_article"] },
  { key: "pages", label: "Web pages", documentTypes: ["web_page"] },
  { key: "faq", label: "FAQ", documentTypes: ["faq"] },
  { key: "products", label: "Products", documentTypes: ["product"] },
  { key: "other", label: "Other", documentTypes: ["other"] },
];

type CollectionsRailProps = {
  /** Per-raw-document-type counts from the server. We sum the relevant
   * subset for each bucket. */
  documentTypeCounts: Partial<Record<DocumentType, number>>;
  /** Total file count for the bot, drives the "All" bucket. */
  grandTotal: number;
  selected: CollectionKey;
  onSelect: (key: CollectionKey) => void;
  /** Optional embedding model name for the small info block at the bottom
   * of the rail. Hidden when not supplied. */
  embeddingModel?: string;
  /** Optional ISO timestamp shown as "last reindex". */
  lastSyncedAt?: string | null;
};

/**
 * Left rail listing collections with per-bucket counts. Matches the
 * mockup's Knowledge layout but uses our existing `document_type` field
 * — no schema change.
 */
export default function CollectionsRail({
  documentTypeCounts,
  grandTotal,
  selected,
  onSelect,
  embeddingModel,
  lastSyncedAt,
}: CollectionsRailProps) {
  // Pre-compute counts so empty buckets still show "0" rather than vanishing.
  const counts = COLLECTIONS.reduce<Record<CollectionKey, number>>(
    (acc, c) => {
      acc[c.key] =
        c.key === "all"
          ? grandTotal
          : c.documentTypes.reduce(
              (sum, dt) => sum + (documentTypeCounts[dt] ?? 0),
              0,
            );
      return acc;
    },
    { all: 0, documents: 0, pages: 0, faq: 0, products: 0, other: 0 },
  );

  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="nav-label" style={{ padding: "0 4px 8px" }}>
        Collections
      </div>
      <div className="kb-collections">
        {COLLECTIONS.map((c) => (
          <button
            key={c.key}
            type="button"
            className="kb-collection"
            data-active={c.key === selected ? "true" : undefined}
            onClick={() => onSelect(c.key)}
          >
            <span>{c.label}</span>
            <span className="kb-collection-meta">{counts[c.key]}</span>
          </button>
        ))}
      </div>

      {(embeddingModel || lastSyncedAt) && (
        <>
          <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "14px 0" }} />
          <div className="nav-label" style={{ padding: "0 4px 8px" }}>
            Embedding
          </div>
          {embeddingModel && (
            <div className="meta-row" style={{ padding: "2px 0" }}>
              <span className="key">Model</span>
              <span className="val" style={{ wordBreak: "break-all" }}>
                {embeddingModel}
              </span>
            </div>
          )}
          {lastSyncedAt && (
            <div className="meta-row" style={{ padding: "2px 0" }}>
              <span className="key">Last update</span>
              <span className="val">{new Date(lastSyncedAt).toLocaleString()}</span>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Return the raw `document_type` values backing a rail bucket, so the
 * page can pass them to the paginated list endpoint. Empty for `all`. */
export function collectionDocumentTypes(key: CollectionKey): DocumentType[] {
  const c = COLLECTIONS.find((x) => x.key === key);
  return c ? c.documentTypes : [];
}

export type { CollectionKey };

/** Document-type → human-friendly label, used for the file row's badge. */
export function documentTypeLabel(t: DocumentType): string {
  return t.replace(/_/g, " ");
}
