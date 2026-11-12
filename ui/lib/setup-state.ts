// Tracks "has the admin finished or dismissed the first-run setup for
// this bot?" purely in localStorage. The dashboard checks this together
// with the file count to decide whether to show the Resume setup pill.
//
// Per-bot, not per-admin: if a teammate sets up a bot on their machine,
// other admins still see the pill until they upload a file or dismiss
// it themselves. That's intentional — the alternative requires server-
// side state and we don't have telemetry yet (improve-ux.md §13).

const PREFIX = "bf-bot-setup:";

export function setupStorageKey(botId: string): string {
  return `${PREFIX}${botId}`;
}

export function isSetupDismissed(botId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return localStorage.getItem(setupStorageKey(botId)) === "dismissed";
  } catch {
    return false;
  }
}

export function markSetupDismissed(botId: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(setupStorageKey(botId), "dismissed");
  } catch {
    /* localStorage may be disabled */
  }
}

export function clearSetupDismissed(botId: string): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(setupStorageKey(botId));
  } catch {
    /* localStorage may be disabled */
  }
}
