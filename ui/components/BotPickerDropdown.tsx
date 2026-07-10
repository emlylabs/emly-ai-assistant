"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import type { Bot } from "@/lib/api";

type BotPickerDropdownProps = {
  /** The bot the user is currently viewing. */
  current: { slug: string; name: string };
  /** Full list of bots the admin can switch to. `null` means still loading;
   * the trigger stays clickable but the menu shows an empty state. */
  bots: Bot[] | null;
  /** Called when the user picks a different bot. The receiving component is
   * responsible for the actual navigation (preserving the current tab). */
  onSwitch: (slug: string) => void;
};

/**
 * Bot picker rendered inline inside the breadcrumb. Replaces the standalone
 * `<select>` that used to live in the topbar — the bot name now does double
 * duty as both breadcrumb segment and switcher trigger. Click opens a
 * lightweight popover; outside-click and Escape close it.
 */
export default function BotPickerDropdown({ current, bots, onSwitch }: BotPickerDropdownProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLSpanElement>(null);

  // Close on outside click. Listening on `pointerdown` instead of `click`
  // so the menu disappears as soon as the user reaches outside, before the
  // upstream page handler fires.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function handlePick(targetSlug: string) {
    setOpen(false);
    if (targetSlug !== current.slug) onSwitch(targetSlug);
  }

  return (
    <span className="bot-picker" ref={wrapperRef}>
      <button
        type="button"
        className="bot-picker-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{current.name}</span>
        <ChevronDown
          size={13}
          strokeWidth={1.75}
          className="bot-picker-caret"
          aria-hidden="true"
        />
      </button>

      {open && (
        <div className="bot-picker-popover" role="listbox" aria-label="Switch bot">
          {bots === null ? (
            <div className="bot-picker-empty">Loading bots…</div>
          ) : bots.length === 0 ? (
            <div className="bot-picker-empty">No other bots</div>
          ) : (
            <ul className="bot-picker-list">
              {bots.map((b) => {
                const active = b.slug === current.slug;
                return (
                  <li key={b.id}>
                    <button
                      type="button"
                      className={
                        active ? "bot-picker-item active" : "bot-picker-item"
                      }
                      role="option"
                      aria-selected={active}
                      onClick={() => handlePick(b.slug)}
                    >
                      <span className="bot-picker-item-main">
                        <span className="bot-picker-item-name">{b.name}</span>
                        <span className="bot-picker-item-slug">{b.slug}</span>
                      </span>
                      {active && (
                        <Check
                          size={13}
                          strokeWidth={2}
                          aria-hidden="true"
                          style={{ flex: "0 0 auto" }}
                        />
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </span>
  );
}
