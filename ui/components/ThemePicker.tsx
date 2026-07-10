"use client";

import { useCallback, useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type Theme = "paper" | "ink";

const STORAGE_KEY = "bf-theme";

function readTheme(): Theme {
  if (typeof window === "undefined") return "ink";
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "paper" ? "paper" : "ink";
  } catch {
    return "ink";
  }
}

function applyTheme(t: Theme) {
  document.documentElement.setAttribute("data-theme", t);
  try {
    localStorage.setItem(STORAGE_KEY, t);
  } catch {
    // localStorage may be blocked (private mode, sandbox); ignore.
  }
}

export default function ThemePicker() {
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    const stored = readTheme();
    setTheme(stored);
    applyTheme(stored);

    function onStorage(e: StorageEvent) {
      if (e.key !== STORAGE_KEY) return;
      const val = e.newValue as Theme | null;
      const next = val === "paper" ? "paper" : "ink";
      setTheme(next);
      applyTheme(next);
    }

    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const pick = useCallback((t: Theme) => {
    setTheme(t);
    applyTheme(t);
  }, []);

  return (
    <div className="seg" role="group" aria-label="Theme">
      <button
        type="button"
        className={theme === "paper" ? "active" : ""}
        onClick={() => pick("paper")}
        aria-label="Paper theme"
        title="Paper"
      >
        <Sun size={13} strokeWidth={1.75} />
        Paper
      </button>
      <button
        type="button"
        className={theme === "ink" ? "active" : ""}
        onClick={() => pick("ink")}
        aria-label="Ink theme"
        title="Ink"
      >
        <Moon size={13} strokeWidth={1.75} />
        Ink
      </button>
    </div>
  );
}
