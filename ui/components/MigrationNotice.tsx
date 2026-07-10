"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";

// One-time breadcrumb for admins arriving after the rename. Answers the
// obvious "wait, where's the Topics tab?" question without nagging.
//
// Storage key includes a version so future renames can re-show the
// notice without touching this component.

const STORAGE_KEY = "emly:migrationNotice:v1:dismissed";

export default function MigrationNotice() {
  const [dismissed, setDismissed] = useState(true); // start hidden to avoid SSR flash

  useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);

  function dismiss() {
    try {
      window.localStorage.setItem(STORAGE_KEY, "1");
    } catch {}
    setDismissed(true);
  }

  if (dismissed) return null;

  return (
    <div
      role="status"
      style={{
        marginBottom: 16,
        padding: "10px 12px",
        background: "var(--panel-bg)",
        border: "1px solid var(--panel-border)",
        borderRadius: 6,
        fontSize: 13,
        display: "flex",
        alignItems: "center",
        gap: 12,
      }}
    >
      <span style={{ flex: 1 }}>
        We&apos;ve renamed a few things to make the bot config easier to follow:{" "}
        <strong>Topics</strong> is now <strong>Skills</strong>,{" "}
        <strong>Forms</strong> is now <strong>Actions</strong>, and{" "}
        <strong>RAG</strong> is now <strong>Knowledge</strong>. The persisted
        config is unchanged.
      </span>
      <button
        type="button"
        className="ghost"
        onClick={dismiss}
        aria-label="Hide"
        style={{ padding: "4px 8px", fontSize: 12 }}
      >
        <X size={12} strokeWidth={1.75} /> Hide
      </button>
    </div>
  );
}
