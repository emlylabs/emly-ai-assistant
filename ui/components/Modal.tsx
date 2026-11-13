"use client";

import {
  CSSProperties,
  ReactNode,
  RefObject,
  useEffect,
  useId,
  useRef,
} from "react";
import { X } from "lucide-react";
import { markModalOpen } from "@/components/KeyboardShortcuts";
import { focusableWithin, useFocusTrap } from "@/lib/focus-trap";

/**
 * Shared modal primitive. Every centered dialog in the admin UI funnels
 * through this — focus trap, focus restore, body-scroll lock, Esc + backdrop
 * close, and phone-safe sizing live here so the per-site dialog bodies stay
 * a presentational concern.
 *
 * Sizes map to viewport-aware widths so every modal is full-bleed-safe under
 * 640px without each consumer remembering to set responsive bounds:
 *
 *   sm — min(420px, 100vw - 32px)   confirms, small forms
 *   md — min(640px, 100vw - 32px)   the bulk of forms
 *   lg — min(720px, 100vw - 32px)   wizards, diffs, multi-step
 *
 * The component is not portalled — it renders inline at the consumer site
 * with `position: fixed; inset: 0; z-index: 60`. That matches the previous
 * hand-rolled pattern but lifts z-index above the existing 50 so a stale
 * inline dialog underneath can't paint over a new Modal-rendered one during
 * migration.
 */

export type ModalSize = "sm" | "md" | "lg";

export type ModalProps = {
  /** When false the component renders nothing — no portal, no listeners. */
  open: boolean;
  /** Fired on Esc, backdrop click, or programmatic close. */
  onClose: () => void;
  /** Visible heading; renders inside the modal header strip. */
  title?: string;
  /** Off-screen-only label — use when there's no visible title. */
  ariaLabel?: string;
  /** Width tier — see comment above for the responsive bounds. */
  size?: ModalSize;
  /** Initial focus target. If unset, the first focusable child gets focus. */
  initialFocusRef?: RefObject<HTMLElement | null>;
  /** Set false while the dialog is mid-submit to prevent accidental close. */
  closeOnBackdrop?: boolean;
  /** Set false to keep Esc bound to inner controls (rare). */
  closeOnEsc?: boolean;
  /** Set false to hide the X button in the header. */
  showClose?: boolean;
  /** Extra class names on the inner card. */
  className?: string;
  /** Extra style overrides on the inner card. */
  cardStyle?: CSSProperties;
  /** Body content. */
  children: ReactNode;
};

const SIZE_TO_MAX: Record<ModalSize, number> = {
  sm: 420,
  md: 640,
  lg: 720,
};

export default function Modal({
  open,
  onClose,
  title,
  ariaLabel,
  size = "md",
  initialFocusRef,
  closeOnBackdrop = true,
  closeOnEsc = true,
  showClose = true,
  className,
  cardStyle,
  children,
}: ModalProps) {
  const cardRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  // Focus management: remember where focus was, move it inward on open,
  // restore on close. Storing in a ref (not state) so closing during the
  // render cycle still reads the right element.
  const lastFocusedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    // Snapshot the active element so we can restore focus on close. We do
    // this before moving focus inward so the snapshot is the trigger, not
    // any element inside the dialog.
    lastFocusedRef.current = document.activeElement as HTMLElement | null;

    // Move focus inward. Defer to the next tick so children that mount in
    // the same render commit have a chance to attach refs.
    const id = window.setTimeout(() => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus();
        return;
      }
      const card = cardRef.current;
      if (!card) return;
      const first = focusableWithin(card)[0];
      first?.focus();
    }, 0);

    return () => {
      window.clearTimeout(id);
      // Restore focus to the trigger. Guarded against the element being
      // gone (e.g. a row that got unmounted by a parent re-render).
      const target = lastFocusedRef.current;
      if (target && document.contains(target)) target.focus();
    };
  }, [open, initialFocusRef]);

  // Body-scroll lock. Adds a style on <html> rather than <body> so layouts
  // that use `body { overflow }` for other purposes (none in this app, but
  // safer) don't fight us. The previous overflow gets restored on unmount.
  // Also signals to the KeyboardShortcuts provider that single-key chords
  // should pause while the modal is open (Esc still passes through).
  useEffect(() => {
    if (!open) return;
    const root = document.documentElement;
    const prev = root.style.overflow;
    root.style.overflow = "hidden";
    markModalOpen(true);
    return () => {
      root.style.overflow = prev;
      markModalOpen(false);
    };
  }, [open]);

  // Esc + Tab handling. The shared focus-trap hook owns Tab cycling; we
  // surface Esc here so `closeOnEsc=false` (mid-submit) can suppress it
  // without disabling the trap.
  useFocusTrap({
    open,
    containerRef: cardRef,
    onEscape: closeOnEsc ? onClose : undefined,
  });

  if (!open) return null;

  const maxPx = SIZE_TO_MAX[size];
  // `calc(100vw - 32px)` keeps a 16px gutter on each side of the viewport.
  // `min(...)` lets the card grow up to its tier max on roomy screens.
  const widthStyle: CSSProperties = {
    width: `min(${maxPx}px, calc(100vw - 32px))`,
    maxHeight: "calc(100vh - 32px)",
    overflow: "auto",
  };

  // Backdrop intercepts clicks; the inner card stops propagation in JSX so
  // a click inside doesn't dismiss. closeOnBackdrop=false leaves the click
  // silent (used while submitting).
  function onBackdropClick(e: React.MouseEvent) {
    if (e.target !== e.currentTarget) return;
    if (closeOnBackdrop) onClose();
  }

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? titleId : undefined}
      aria-label={!title ? ariaLabel : undefined}
      onClick={onBackdropClick}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 60,
        padding: 16,
      }}
    >
      <div
        ref={cardRef}
        className={["modal-card", "card", className].filter(Boolean).join(" ")}
        style={{ ...widthStyle, ...cardStyle }}
      >
        {(title || showClose) && (
          <div className="modal-header">
            {title ? (
              <h2 id={titleId} className="modal-title">
                {title}
              </h2>
            ) : (
              <span />
            )}
            {showClose && (
              <button
                type="button"
                className="ghost modal-close"
                onClick={onClose}
                aria-label="Close dialog"
              >
                <X size={16} strokeWidth={1.75} aria-hidden="true" />
              </button>
            )}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
