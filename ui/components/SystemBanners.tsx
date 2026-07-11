"use client";

import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { SystemWarning, fetchReadyz } from "@/lib/api";

const DISMISS_KEY_PREFIX = "emly_dismiss_warning_";

function isDismissed(code: string): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(DISMISS_KEY_PREFIX + code) === "1";
}

function dismiss(code: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(DISMISS_KEY_PREFIX + code, "1");
}

export default function SystemBanners() {
  const [warnings, setWarnings] = useState<SystemWarning[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchReadyz().then((r) => {
      if (!cancelled && r?.warnings) setWarnings(r.warnings);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = warnings.filter((w) => !isDismissed(w.code));
  if (visible.length === 0) return null;

  return (
    <div style={{ marginBottom: 16 }}>
      {visible.map((w) => (
        <div
          key={w.code}
          className="banner advisory"
          style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}
        >
          <div className="banner-icon" style={{ flex: 1 }}>
            <Info strokeWidth={1.75} />
            <div>{w.message}</div>
          </div>
          <button
            type="button"
            className="ghost"
            style={{ padding: "2px 8px", fontSize: 12 }}
            onClick={() => {
              dismiss(w.code);
              setTick(tick + 1);
            }}
            aria-label={`Dismiss ${w.code}`}
          >
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}
