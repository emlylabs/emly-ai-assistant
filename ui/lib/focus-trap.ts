"use client";

import { type RefObject, useEffect } from "react";

/** Standard focusable-element selector. Excludes tabIndex=-1 (an
 * opt-out marker) and `disabled` controls. */
const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Read the live list of focusable descendants of `container`. Filters
 * out elements that are visually hidden (offsetParent === null) so an
 * `<input hidden>` or a CSS-hidden ghost button doesn't trap focus. */
export function focusableWithin(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
  ).filter((el) => !el.hasAttribute("disabled") && el.offsetParent !== null);
}

type Options = {
  /** When false the trap is inert. Toggle via `open` rather than mount/unmount
   * so the hook order stays stable across renders. */
  open: boolean;
  /** Container whose focusable descendants form the trap ring. */
  containerRef: RefObject<HTMLElement | null>;
  /** Optional Escape handler. The hook only handles Tab cycling — Esc is
   * surfaced as a separate callback so consumers can choose to ignore
   * Escape while submitting. */
  onEscape?: () => void;
};

/**
 * Cycle Tab / Shift+Tab focus within the container's focusable descendants.
 * If focus is on the last element, Tab wraps to the first; Shift+Tab on
 * the first wraps to the last. Focus outside the container is pulled back
 * to the first focusable element on Tab.
 *
 * Used by `<Modal>` and `<CommandPalette>` — anywhere a temporary
 * focus-trapped surface needs to behave like a dialog without restoring
 * focus or locking scroll (those concerns live in the consumer).
 */
export function useFocusTrap({ open, containerRef, onEscape }: Options) {
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && onEscape) {
        e.preventDefault();
        onEscape();
        return;
      }
      if (e.key !== "Tab") return;
      const card = containerRef.current;
      if (!card) return;
      const focusables = focusableWithin(card);
      if (focusables.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !card.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, containerRef, onEscape]);
}
