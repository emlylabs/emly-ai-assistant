"use client";

import {
  ReactElement,
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/**
 * Accessible tooltip. Shows on hover (200ms delay) AND on keyboard focus,
 * with `role="tooltip"` and an `aria-describedby` linkage so screen readers
 * announce the label when the trigger is focused.
 *
 * The popover is rendered into `document.body` via a portal and positioned
 * with `position: fixed` against the trigger's bounding rect. This matters
 * for the collapsed sidebar: the rail's `.sidebar-nav` has `overflow-x:
 * hidden` to prevent rogue horizontal scrollbars, which would otherwise
 * clip an inline absolutely-positioned popover.
 *
 * The trigger must accept a ref via `React.cloneElement`. For Next.js
 * `<Link>` and Lucide icons that's automatic; native elements work
 * directly.
 *
 * On coarse pointers (touch) the hover path is suppressed — a tap would
 * otherwise fire synthetic mouseenter and leave a stale tooltip hanging
 * after the action completes. Keyboard focus still opens the popover so
 * Bluetooth-keyboard tablet users keep the affordance.
 */

const isCoarsePointer =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(pointer: coarse)").matches;

type Placement = "top" | "right" | "bottom" | "left";

export type TooltipProps = {
  /** Label shown inside the popover. Plain text — no rich formatting. */
  label: string;
  /** Side of the trigger the popover anchors to. Default "top". */
  placement?: Placement;
  /** Single React element trigger. Its ref + aria-describedby get wired. */
  children: ReactElement<{
    id?: string;
    ref?: React.Ref<HTMLElement>;
    onMouseEnter?: (e: React.MouseEvent<HTMLElement>) => void;
    onMouseLeave?: (e: React.MouseEvent<HTMLElement>) => void;
    onFocus?: (e: React.FocusEvent<HTMLElement>) => void;
    onBlur?: (e: React.FocusEvent<HTMLElement>) => void;
    "aria-describedby"?: string;
  }>;
  /** Override the open delay on hover. Focus opens instantly regardless. */
  delayMs?: number;
};

type Coords = { left: number; top: number; transform: string };

function placeFor(rect: DOMRect, placement: Placement): Coords {
  switch (placement) {
    case "right":
      return {
        left: rect.right + 8,
        top: rect.top + rect.height / 2,
        transform: "translateY(-50%)",
      };
    case "left":
      return {
        left: rect.left - 8,
        top: rect.top + rect.height / 2,
        transform: "translate(-100%, -50%)",
      };
    case "bottom":
      return {
        left: rect.left + rect.width / 2,
        top: rect.bottom + 6,
        transform: "translateX(-50%)",
      };
    case "top":
    default:
      return {
        left: rect.left + rect.width / 2,
        top: rect.top - 6,
        transform: "translate(-50%, -100%)",
      };
  }
}

export default function Tooltip({
  label,
  placement = "top",
  children,
  delayMs = 200,
}: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const [coords, setCoords] = useState<Coords | null>(null);
  const [mounted, setMounted] = useState(false);
  const id = useId();
  const timerRef = useRef<number | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  // `createPortal` needs `document`, which isn't around during SSR/static
  // export. Defer the portal until after first client paint.
  useEffect(() => setMounted(true), []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const show = useCallback(
    (immediate: boolean) => {
      clearTimer();
      if (immediate) {
        setVisible(true);
        return;
      }
      timerRef.current = window.setTimeout(() => setVisible(true), delayMs);
    },
    [clearTimer, delayMs],
  );

  const hide = useCallback(() => {
    clearTimer();
    setVisible(false);
  }, [clearTimer]);

  // Measure the trigger after it becomes visible so the popover can be
  // positioned in viewport coordinates. The popover's `data-visible` flag
  // stays false until `coords` is non-null, so a brief one-paint gap
  // between this effect and the previous render doesn't show a stale
  // location — opacity only flips to 1 on the next render after coords
  // arrive.
  useEffect(() => {
    if (!visible || !triggerRef.current) return;
    setCoords(placeFor(triggerRef.current.getBoundingClientRect(), placement));
  }, [visible, placement]);

  // The trigger's position can change while the tooltip is open (page
  // scrolls, viewport resizes). Hide rather than chase the trigger — the
  // user can re-hover when they want it back.
  useEffect(() => {
    if (!visible) return;
    const onChange = () => hide();
    window.addEventListener("scroll", onChange, true);
    window.addEventListener("resize", onChange);
    return () => {
      window.removeEventListener("scroll", onChange, true);
      window.removeEventListener("resize", onChange);
    };
  }, [visible, hide]);

  const child = children;
  const existing = child.props;
  const childRef = (child as unknown as { ref?: React.Ref<HTMLElement> }).ref;

  // Compose the trigger's existing ref (if any) with ours so we can measure
  // it without stealing the consumer's ref.
  const composedRef = useCallback(
    (node: HTMLElement | null) => {
      triggerRef.current = node;
      if (typeof childRef === "function") childRef(node);
      else if (childRef && typeof childRef === "object") {
        (childRef as React.MutableRefObject<HTMLElement | null>).current = node;
      }
    },
    [childRef],
  );

  const enhanced = cloneElement(child, {
    ref: composedRef,
    "aria-describedby":
      [existing["aria-describedby"], visible ? id : null]
        .filter(Boolean)
        .join(" ") || undefined,
    onMouseEnter: (e: React.MouseEvent<HTMLElement>) => {
      if (!isCoarsePointer) show(false);
      existing.onMouseEnter?.(e);
    },
    onMouseLeave: (e: React.MouseEvent<HTMLElement>) => {
      if (!isCoarsePointer) hide();
      existing.onMouseLeave?.(e);
    },
    onFocus: (e: React.FocusEvent<HTMLElement>) => {
      show(true);
      existing.onFocus?.(e);
    },
    onBlur: (e: React.FocusEvent<HTMLElement>) => {
      hide();
      existing.onBlur?.(e);
    },
  });

  const popover =
    mounted && typeof document !== "undefined"
      ? createPortal(
          <span
            id={id}
            role="tooltip"
            className="tooltip-popover"
            data-visible={visible && coords ? "true" : "false"}
            style={{
              position: "fixed",
              left: coords?.left ?? 0,
              top: coords?.top ?? 0,
              transform: coords?.transform,
            }}
          >
            {label}
          </span>,
          document.body,
        )
      : null;

  return (
    <span className="tooltip-anchor">
      {enhanced}
      {popover}
    </span>
  );
}
