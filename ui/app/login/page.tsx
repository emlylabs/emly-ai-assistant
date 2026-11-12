"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { loginUrl } from "@/lib/api";

function Inner() {
  const params = useSearchParams();
  const returnTo = params.get("return_to") || "/bots";

  // No client-side `me()` probe. The middleware (and the FastAPI
  // catchall in static-export prod) handles "you need a session"
  // for the protected paths; `/login` is intentionally public so a
  // user with an expired or missing cookie can reach it. If they
  // happen to be already signed in and land here anyway, the IdP
  // round-trip on click is harmless (it'll mint fresh cookies and
  // forward to `return_to`).
  return (
    <div className="center">
      <div className="card narrow">
        <p className="eyebrow">Emly admin</p>
        <h1 style={{ marginTop: 8 }}>Sign in</h1>
        <p className="muted" style={{ marginTop: 12 }}>
          Sign in via your configured identity provider.
        </p>
        <a
          href={loginUrl(returnTo)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            width: "100%",
            marginTop: 20,
            padding: "10px 16px",
            background: "var(--fg)",
            color: "var(--surface)",
            textDecoration: "none",
            borderRadius: "var(--radius)",
            fontWeight: 600,
          }}
        >
          Continue to sign in
          <ArrowRight size={14} strokeWidth={1.75} />
        </a>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="center"><p className="muted">Loading…</p></div>}>
      <Inner />
    </Suspense>
  );
}
