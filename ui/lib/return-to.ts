/**
 * The one rule for where the console may send someone after sign-in (S10).
 *
 * Only a path on this origin: it must start with a single "/". "//host" and
 * "/\host" are read by browsers as another origin, and control characters
 * can be stripped into one, so all of those fall back. Mirrors
 * `_is_safe_return_to` in routes/auth.py.
 */
export function safeReturnTo(value: string | null | undefined, fallback = "/bots"): string {
  if (!value) return fallback;
  if (!value.startsWith("/") || value.startsWith("//")) return fallback;
  if (value.includes("\\") || /[\u0000-\u001f\u007f]/.test(value)) return fallback;
  return value;
}
