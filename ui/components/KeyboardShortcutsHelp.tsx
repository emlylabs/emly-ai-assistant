"use client";

import { useEffect, useState } from "react";
import Modal from "@/components/Modal";
import {
  formatChord,
  useShortcut,
  useShortcutRegistry,
  type ShortcutGroup,
} from "@/components/KeyboardShortcuts";

/**
 * Modal that lists every registered shortcut, grouped by section. Opens on
 * `?` (which the provider dispatches even when the focus is on a non-input
 * element). Also exposes a checkbox to toggle the sidebar hint chips so
 * users can teach themselves the chords incrementally.
 */
export default function KeyboardShortcutsHelp() {
  const [open, setOpen] = useState(false);
  const { all, hintsEnabled, setHintsEnabled } = useShortcutRegistry();

  useShortcut({
    chord: "?",
    description: "Show keyboard shortcuts",
    group: "global",
    handler: () => setOpen((v) => !v),
  });
  useShortcut({
    chord: "shift+/",
    description: "Show keyboard shortcuts (alt)",
    group: "global",
    handler: () => setOpen((v) => !v),
  });

  // Snapshot the registry into local state on open so re-renders inside
  // the modal don't blow away the list. Snapshot is shallow — the
  // registry never mutates entries in place.
  const [snapshot, setSnapshot] = useState<ReturnType<typeof all>>([]);
  useEffect(() => {
    if (!open) return;
    setSnapshot(all());
  }, [open, all]);

  const groups: { key: ShortcutGroup; label: string }[] = [
    { key: "global", label: "Global" },
    { key: "bot", label: "On this bot" },
    { key: "config", label: "In the config editor" },
    { key: "modal", label: "Inside dialogs" },
  ];

  const grouped = groups.map((g) => ({
    ...g,
    items: snapshot.filter((s) => (s.group ?? "global") === g.key),
  }));

  return (
    <Modal
      open={open}
      onClose={() => setOpen(false)}
      title="Keyboard shortcuts"
      size="md"
    >
      <div className="modal-body" style={{ display: "grid", gap: 16 }}>
        {grouped.map(({ key, label, items }) =>
          items.length === 0 ? null : (
            <section key={key}>
              <h3 style={{ marginTop: 0, marginBottom: 8, fontSize: 13 }}>{label}</h3>
              <ul
                style={{
                  listStyle: "none",
                  margin: 0,
                  padding: 0,
                  display: "grid",
                  gap: 4,
                }}
              >
                {items.map((it) => (
                  <li
                    key={it.id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      gap: 16,
                      alignItems: "center",
                      fontSize: 13,
                    }}
                  >
                    <span>{it.description}</span>
                    <span className="kbd-row" aria-label={`Chord: ${it.chord}`}>
                      {formatChord(it.chord).map((k, i, arr) => (
                        <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                          <kbd>{k}</kbd>
                          {i < arr.length - 1 && (
                            <span
                              className="muted"
                              style={{ fontSize: 11 }}
                              aria-hidden="true"
                            >
                              {it.chord.includes(" ") ? "then" : "+"}
                            </span>
                          )}
                        </span>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ),
        )}
        <label
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            fontSize: 13,
            marginTop: 8,
            paddingTop: 12,
            borderTop: "1px solid var(--border)",
          }}
        >
          <input
            type="checkbox"
            checked={hintsEnabled}
            onChange={(e) => setHintsEnabled(e.target.checked)}
          />
          Show shortcut hints next to sidebar items
        </label>
      </div>
    </Modal>
  );
}
