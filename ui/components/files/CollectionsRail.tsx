"use client";

import type { BotFile, DocumentType } from "@/lib/api";

/** Logical buckets shown in the rail. We collapse the 6-value `DocumentType`
 * enum into 5 user-facing groups so the rail isn't cluttered with
 * single-item categories — `support_article`, `faq` and `product` all
 * land under the closest UI-friendly heading. */
type CollectionKey = "all" | "documents" | "pages" | "faq" | "products" | "other";

type Collection = {
  key: CollectionKey;
  label: string;
  /** Filter predicate run against each `BotFile`. */
  match: (f: BotFile) => boolean;
};

const COLLECTIONS: Collection[] = [
  { key: "all", label: "All files", match: () => true },
  {
    key: "documents",
    label: "Documents",
    match: (f) => f.document_type === "document" || f.document_type === "support_article",
  },
  {
    key: "pages",
    label: "Web pages",
    match: (f) => f.document_type === "web_page",
  },
  {
    key: "faq",
    label: "FAQ",
    match: (f) => f.document_type === "faq",
  },
  {
    key: "products",
    label: "Products",
    match: (f) => f.document_type === "product",
  },
  {
    key: "other",
    label: "Other",
    match: (f) => f.document_type === "other",
  },
];

type CollectionsRailProps = {
  files: BotFile[];
  selected: CollectionKey;
  onSelect: (key: CollectionKey) => void;
  /** Optional embedding model name for the small info block at the bottom
   * of the rail. Hidden when not supplied. */
  embeddingModel?: string;
  /** Optional ISO timestamp shown as "last reindex" — derive from the most
   * recent `BotFile.updated_on` on the caller side. */
  lastSyncedAt?: string | null;
};

/**
 * Left rail listing collections with per-bucket counts. Matches the
 * mockup's Knowledge layout but uses our existing `document_type` field
 * — no schema change.
 */
export default function CollectionsRail({
  files,
  selected,
  onSelect,
  embeddingModel,
  lastSyncedAt,
}: CollectionsRailProps) {
  // Pre-compute counts so empty buckets still show "0" rather than vanishing.
  const counts = COLLECTIONS.reduce<Record<CollectionKey, number>>(
    (acc, c) => {
      acc[c.key] = files.filter(c.match).length;
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

/** Re-export the keyed predicate so the page can filter rows with the same
 * logic the rail buckets them. */
export function matchCollection(key: CollectionKey, file: BotFile): boolean {
  const c = COLLECTIONS.find((x) => x.key === key);
  return c ? c.match(file) : true;
}

export type { CollectionKey };

/** Document-type → human-friendly label, used for the file row's badge. */
export function documentTypeLabel(t: DocumentType): string {
  return t.replace(/_/g, " ");
}
