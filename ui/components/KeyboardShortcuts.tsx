"use client";

import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/**
 * Workspace-wide keyboard-shortcut registry.
 *
 * One `document` keydown listener dispatches into a registry of
 * `{chord, handler, when, ...}` tuples. Two-key sequences ("g d") are
 * supported via a 1500ms chord-state window. Modifier chords ("cmd+k",
 * "cmd+shift+z") fire on the keydown of the trailing key, modifiers
 * matched explicitly.
 *
 * Suppression rules:
 *  - When the active element is an `<input>`, `<textarea>`, or anything
 *    with `isContentEditable === true`, single-key + sequence shortcuts
 *    are suppressed so admins typing into forms don't trigger jumps.
 *    Modifier chords (with cmd/ctrl) still fire — those are explicit and
 *    rarely collide.
 *  - When a modal is open (signalled by `data-modal-open` on <html>),
 *    only `Escape` fires through; everything else stays inside the modal.
 *
 * Consumers register via the `useShortcut` hook. The provider also tracks
 * `data-shortcut-hints` on <html> so the `g d` chord chips on the sidebar
 * stay opt-in.
 */

export type ShortcutGroup = "global" | "bot" | "config" | "modal";

export type ShortcutDef = {
  /** Canonical chord representation. Examples:
   *   "cmd+k"        — meta or ctrl + K
   *   "cmd+shift+z"  — meta or ctrl + shift + Z
   *   "g d"          — press g then d within 1500ms
   *   "?"            — literal Shift+/ that arrives as "?"
   *   "/"            — bare slash
   *   "c"            — single c
   *   "Escape"       — single Escape (always fires, even with modals open) */
  chord: string;
  /** Fires after suppression rules pass. */
  handler: (e: KeyboardEvent) => void;
  /** Optional gate; if returns false the handler is skipped. */
  when?: () => boolean;
  /** Plain description shown in the help overlay. */
  description: string;
  /** Section grouping in the help overlay. */
  group?: ShortcutGroup;
};

type RegistryEntry = ShortcutDef & { id: number };

type Ctx = {
  /** Register a shortcut. Returns an unregister function. */
  register: (def: ShortcutDef) => () => void;
  /** Snapshot of all registered shortcuts — used by the help overlay. */
  all: () => RegistryEntry[];
  /** Whether the "show shortcut chips next to nav items" pref is enabled. */
  hintsEnabled: boolean;
  /** Toggle the hint preference. */
  setHintsEnabled: (next: boolean) => void;
};

const ShortcutsContext = createContext<Ctx | null>(null);

const HINTS_KEY = "bf-shortcut-hints";

/** 1500ms window for two-key sequences. Conservative: long enough that
 * a hesitant user lands the second key, short enough that `g` typed in
 * normal flow doesn't unexpectedly arm the registry. */
const SEQUENCE_TIMEOUT_MS = 1500;

function readHints(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(HINTS_KEY) === "1";
  } catch {
    return false;
  }
}

function activeIsInput(): boolean {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName?.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if ((el as HTMLElement).isContentEditable) return true;
  return false;
}

function modalOpen(): boolean {
  return document.documentElement.hasAttribute("data-modal-open");
}

/** Normalize a KeyboardEvent into the canonical chord string. Used to
 * match against registered chords. Returns null for keys we can't
 * meaningfully dispatch (modifier-only presses, IME composition). */
function eventToChord(e: KeyboardEvent): string | null {
  if (e.isComposing) return null;
  const key = e.key;
  if (!key) return null;
  if (key === "Shift" || key === "Control" || key === "Alt" || key === "Meta") {
    return null;
  }
  const parts: string[] = [];
  if (e.metaKey || e.ctrlKey) parts.push("cmd");
  if (e.altKey) parts.push("alt");
  // Shift is captured implicitly by the resulting key character (e.g.
  // Shift+/ -> "?"). Explicit shift goes into the chord for keys where the
  // shifted form would otherwise be ambiguous — letters, digits.
  if (e.shiftKey && key.length === 1 && /[a-z0-9]/.test(key.toLowerCase())) {
    parts.push("shift");
  }
  // Normalize alpha keys to lowercase so "Cmd+K" and "Cmd+k" match.
  parts.push(key.length === 1 ? key.toLowerCase() : key);
  return parts.join("+");
}

export function KeyboardShortcutsProvider({ children }: { children: ReactNode }) {
  const registryRef = useRef<RegistryEntry[]>([]);
  const nextIdRef = useRef(1);
  const sequenceStateRef = useRef<{ prefix: string; expires: number } | null>(null);
  const [hintsEnabled, setHintsEnabledState] = useState(false);

  // Sync the hints state with localStorage + a data attribute on <html>
  // so CSS can hook it without a render prop.
  useEffect(() => {
    const initial = readHints();
    setHintsEnabledState(initial);
    document.documentElement.setAttribute(
      "data-shortcut-hints",
      initial ? "1" : "0",
    );
  }, []);

  const setHintsEnabled = useCallback((next: boolean) => {
    setHintsEnabledState(next);
    try {
      window.localStorage.setItem(HINTS_KEY, next ? "1" : "0");
    } catch {
      // localStorage may be blocked; the in-memory state still wins for
      // the current session.
    }
    document.documentElement.setAttribute(
      "data-shortcut-hints",
      next ? "1" : "0",
    );
  }, []);

  const register = useCallback((def: ShortcutDef) => {
    const id = nextIdRef.current++;
    registryRef.current.push({ ...def, id });
    return () => {
      registryRef.current = registryRef.current.filter((r) => r.id !== id);
    };
  }, []);

  const all = useCallback(() => registryRef.current.slice(), []);

  useEffect(() => {
    function dispatch(e: KeyboardEvent) {
      const chord = eventToChord(e);
      if (!chord) return;

      // Escape always fires through — modals depend on it to close. Any
      // other key while a modal is open belongs to the modal.
      const isEscape = chord === "Escape";
      if (modalOpen() && !isEscape) return;

      // Sequence handling. If we have an active prefix and the new key
      // forms `<prefix> <key>`, fire the sequence handler.
      const now = Date.now();
      const seq = sequenceStateRef.current;
      const isModifierChord = chord.includes("+");
      const isSequenceCandidate =
        !isModifierChord && /^[a-z0-9?/]$/.test(chord) && !activeIsInput();

      if (seq && seq.expires >= now && isSequenceCandidate) {
        const sequenceChord = `${seq.prefix} ${chord}`;
        sequenceStateRef.current = null;
        for (const r of registryRef.current) {
          if (r.chord === sequenceChord && (!r.when || r.when())) {
            e.preventDefault();
            r.handler(e);
            return;
          }
        }
        // Sequence didn't match — fall through and let the second key
        // resolve as a single-key chord below.
      }

      // Drop the stale prefix, if any.
      if (seq && seq.expires < now) {
        sequenceStateRef.current = null;
      }

      // Input-focus suppression for single-key chords. Modifier chords
      // pass through even from inputs because they're explicit.
      if (!isModifierChord && !isEscape && activeIsInput()) return;

      // Direct chord match.
      for (const r of registryRef.current) {
        if (r.chord === chord && (!r.when || r.when())) {
          e.preventDefault();
          r.handler(e);
          return;
        }
      }

      // No direct match — see if this key could be the prefix of a
      // sequence. Prefixes are the first char of any "<x> <y>" chord
      // currently registered.
      if (isSequenceCandidate) {
        const isPrefix = registryRef.current.some(
          (r) => r.chord.length > 1 && r.chord[1] === " " && r.chord[0] === chord,
        );
        if (isPrefix) {
          sequenceStateRef.current = {
            prefix: chord,
            expires: now + SEQUENCE_TIMEOUT_MS,
          };
        }
      }
    }
    document.addEventListener("keydown", dispatch);
    return () => document.removeEventListener("keydown", dispatch);
  }, []);

  const ctx = useMemo<Ctx>(
    () => ({ register, all, hintsEnabled, setHintsEnabled }),
    [register, all, hintsEnabled, setHintsEnabled],
  );

  return <ShortcutsContext.Provider value={ctx}>{children}</ShortcutsContext.Provider>;
}

/** Register a keyboard shortcut for the lifetime of the calling
 * component. Re-registers if any of the inputs change — pass stable
 * handler refs (useCallback) if that's wasteful. */
export function useShortcut(def: ShortcutDef) {
  const ctx = useContext(ShortcutsContext);
  useEffect(() => {
    if (!ctx) return;
    return ctx.register(def);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx, def.chord, def.handler, def.when, def.description, def.group]);
}

/** Batch registration for components that need to register N>1 shortcuts.
 * Avoids the for-loop-of-hooks pattern that fails Rules of Hooks. The
 * `defs` array is treated as the dependency identity — change a chord or
 * handler and the whole bundle re-registers. */
export function useShortcuts(defs: ShortcutDef[]) {
  const ctx = useContext(ShortcutsContext);
  // Stable-ish key over the array; chord+description is enough since
  // handlers usually change identity per render but their behaviour
  // doesn't. Consumers wanting stricter re-registration can memoize the
  // array itself.
  const key = defs.map((d) => `${d.chord}::${d.description}`).join("|");
  useEffect(() => {
    if (!ctx) return;
    const unregisters = defs.map((d) => ctx.register(d));
    return () => unregisters.forEach((u) => u());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx, key]);
}

/** Read access to the registry — used by `KeyboardShortcutsHelp`. */
export function useShortcutRegistry() {
  const ctx = useContext(ShortcutsContext);
  if (!ctx) {
    return {
      all: () => [],
      hintsEnabled: false,
      setHintsEnabled: () => {},
    };
  }
  return { all: ctx.all, hintsEnabled: ctx.hintsEnabled, setHintsEnabled: ctx.setHintsEnabled };
}

/** Helper used by Modal to signal "shortcuts pause while I'm open." */
export function markModalOpen(open: boolean) {
  if (typeof document === "undefined") return;
  if (open) document.documentElement.setAttribute("data-modal-open", "1");
  else document.documentElement.removeAttribute("data-modal-open");
}

/** Pretty-print a chord for the help overlay and inline hints. Splits
 * sequence chords on space; modifier chords on +. */
export function formatChord(chord: string): string[] {
  if (chord.includes(" ")) return chord.split(" ");
  return chord
    .split("+")
    .map((p) =>
      p === "cmd" ? "⌘" : p === "shift" ? "⇧" : p === "alt" ? "⌥" : p.length === 1 ? p.toUpperCase() : p,
    );
}
