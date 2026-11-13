// Derive a URL-safe slug from a free-text name. Mirrors the backend's
// constraint in `routes/admin_bots._SLUG_RE`:
//   ^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$
// i.e. lowercase alphanumerics + hyphens, 2-64 chars, no leading or
// trailing hyphen. Multiple separators collapse to one hyphen.

export const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$/;
export const SLUG_MAX = 64;

const COMBINING_MARKS = /[̀-ͯ]/g;

export function slugify(input: string): string {
  return input
    .toLowerCase()
    .normalize("NFKD")
    .replace(COMBINING_MARKS, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SLUG_MAX);
}

export function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug);
}
