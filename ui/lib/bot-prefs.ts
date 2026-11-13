// Per-admin local preferences for the bot picker, command palette, and
// workspace overview. All state is localStorage-only — device-local, no
// backend round-trip — so a fat-fingered "Forget data" wipes them but
// doesn't break access to the bots themselves.
//
// Keys are namespaced by admin id so a shared workstation doesn't leak
// pins between accounts. The admin id is read from the cached `/me`
// response that AdminShell / BotShell already maintains.

const PIN_PREFIX = "bf-bot-pins:";
const RECENT_PREFIX = "bf-bot-recent:";
const LAST_TAB_PREFIX = "bf-bot-last-tab:";
const RECENT_MAX = 8;

function safeReadList(key: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === "string");
  } catch {
    return [];
  }
}

function safeWrite(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota or disabled */
  }
}

export function readPinnedSlugs(adminId: string | null | undefined): string[] {
  if (!adminId) return [];
  return safeReadList(PIN_PREFIX + adminId);
}

export function togglePinnedSlug(adminId: string | null | undefined, slug: string): string[] {
  if (!adminId) return [];
  const current = readPinnedSlugs(adminId);
  const next = current.includes(slug)
    ? current.filter((s) => s !== slug)
    : [...current, slug];
  safeWrite(PIN_PREFIX + adminId, next);
  return next;
}

export function readRecentSlugs(adminId: string | null | undefined): string[] {
  if (!adminId) return [];
  return safeReadList(RECENT_PREFIX + adminId);
}

/** Push a slug onto the recent-list, deduped and capped at RECENT_MAX. */
export function noteRecentSlug(adminId: string | null | undefined, slug: string) {
  if (!adminId || !slug) return;
  const current = readRecentSlugs(adminId);
  const filtered = current.filter((s) => s !== slug);
  filtered.unshift(slug);
  safeWrite(RECENT_PREFIX + adminId, filtered.slice(0, RECENT_MAX));
}

/** Per-bot "last viewed tab" — used so switching back to a bot lands the
 * user where they left off, not on the dashboard. */
export function readLastTab(
  adminId: string | null | undefined,
  slug: string,
): string | null {
  if (!adminId || !slug) return null;
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(LAST_TAB_PREFIX + adminId);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    const v = (parsed as Record<string, unknown>)[slug];
    return typeof v === "string" ? v : null;
  } catch {
    return null;
  }
}

export function writeLastTab(
  adminId: string | null | undefined,
  slug: string,
  tab: string,
) {
  if (!adminId || !slug || !tab) return;
  if (typeof window === "undefined") return;
  try {
    const raw = localStorage.getItem(LAST_TAB_PREFIX + adminId);
    const parsed = raw ? JSON.parse(raw) : {};
    const next = parsed && typeof parsed === "object" ? { ...parsed } : {};
    next[slug] = tab;
    localStorage.setItem(LAST_TAB_PREFIX + adminId, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}
