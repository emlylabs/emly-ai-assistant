import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { NudgesConfig, NudgeEntry } from '../embed';

interface NudgeProps {
  /** Whole `nudges` block from `/widget/{bot}/config`. */
  config?: NudgesConfig | null;
  /** Launcher anchor — `'left'` flips the arrow side. */
  position: 'left' | 'right';
  /** Click handler. The widget should open the chat and send `query`
   * as the first user message. */
  onClick: (query: string) => void;
}

/** Floating tooltip rendered near the chat launcher when the widget is
 * collapsed. Cycles through `config.nudge[]`, showing each for
 * `duration` ms, with `wait_time` ms before the first appearance, up
 * to `count` total appearances. Closing dismisses for the rest of the
 * page lifetime. Mirrors the launcher-nudge component embedded in the
 * vendored react-chat-widget that your-assistant uses (see
 * `node_modules/react-chat-widget/lib/index.js` `nudge_label` /
 * `nudge_query` / `nudge_close`). */
const EMPTY_NUDGES: NudgeEntry[] = [];

const Nudge: React.FC<NudgeProps> = ({ config, position, onClick }) => {
  // Memoize so the useEffect deps below don't churn on every render —
  // `config?.nudge ?? []` would create a fresh `[]` each time and
  // restart the cycle. Stable identity → stable timer.
  const nudges = useMemo(() => config?.nudge ?? EMPTY_NUDGES, [config?.nudge]);
  // Each nudge runs `wait_time` (initial gap before any nudge appears)
  // → `duration` (visible) → hidden gap → next nudge → … until
  // `count` cycles complete or the user dismisses. Defaults match
  // your-assistant: 15s wait, 15s duration, 3 cycles.
  const waitTime = config?.wait_time ?? 15000;
  const duration = config?.duration ?? 15000;
  const count = config?.count ?? 3;

  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(false);
  const [closed, setClosed] = useState(false);
  // Tracks total appearances across cycles so we honor `count`.
  const shownRef = useRef(0);

  useEffect(() => {
    if (closed || nudges.length === 0) return;
    let showTimer: ReturnType<typeof setTimeout> | null = null;
    let hideTimer: ReturnType<typeof setTimeout> | null = null;

    const showOne = () => {
      if (shownRef.current >= count) return;
      shownRef.current += 1;
      setVisible(true);
      hideTimer = setTimeout(() => {
        setVisible(false);
        // Schedule the next one after the same wait gap so nudges
        // don't pile up back-to-back.
        if (shownRef.current < count) {
          showTimer = setTimeout(() => {
            setIndex((i) => (i + 1) % nudges.length);
            showOne();
          }, waitTime);
        }
      }, duration);
    };

    showTimer = setTimeout(showOne, waitTime);
    return () => {
      if (showTimer) clearTimeout(showTimer);
      if (hideTimer) clearTimeout(hideTimer);
    };
  }, [nudges, waitTime, duration, count, closed]);

  if (closed || nudges.length === 0 || !visible) return null;
  const current = nudges[index];
  const label = current?.nudge_label?.trim();
  const query = current?.nudge_query?.trim() || label || '';
  if (!label) return null;

  return (
    <div className={`emw-nudge emw-nudge--${position}`} role="status" aria-live="polite">
      <button
        type="button"
        className="emw-nudge-bubble"
        onClick={() => query && onClick(query)}
      >
        <span className="emw-nudge-label">{label}</span>
        <span className="emw-nudge-cta">View</span>
      </button>
      <button
        type="button"
        className="emw-nudge-close"
        aria-label="Dismiss"
        onClick={(e) => {
          e.stopPropagation();
          setVisible(false);
          setClosed(true);
        }}
      >
        ×
      </button>
      <span className="emw-nudge-arrow" aria-hidden="true" />
    </div>
  );
};

export default Nudge;
