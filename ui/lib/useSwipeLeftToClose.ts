"use client";

import { useRef } from "react";
import type { TouchEvent } from "react";

/**
 * Returns touch handlers that fire `onClose` when the user swipes the
 * attached element ≥60px to the left. Used to dismiss the mobile drawer
 * with a flick — keyboard/tap paths still work; this is additive.
 *
 * No live visual feedback: the drawer doesn't track the finger. Snapping
 * at gesture end keeps the implementation small and avoids tug-of-war
 * with the underlying CSS transition.
 */
export function useSwipeLeftToClose(onClose: () => void) {
  const startX = useRef<number | null>(null);

  return {
    onTouchStart: (e: TouchEvent) => {
      startX.current = e.touches[0]?.clientX ?? null;
    },
    onTouchEnd: (e: TouchEvent) => {
      if (startX.current === null) return;
      const endX = e.changedTouches[0]?.clientX ?? startX.current;
      const delta = endX - startX.current;
      startX.current = null;
      if (delta < -60) onClose();
    },
  };
}
