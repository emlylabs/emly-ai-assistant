"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { api, loginUrl } from "@/lib/api";

function Inner() {
  const router = useRouter();
  const params = useSearchParams();
  const returnTo = params.get("return_to") || "/bots";
  const [checking, setChecking] = useState(true);

  // If we already have a valid session cookie, skip the sign-in screen.
  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then(() => {
        if (!cancelled) router.replace(returnTo);
      })
      .catch(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router, returnTo]);

  if (checking) {
    return (
      <div className="center">
        <p className="muted">Checking session…</p>
      </div>
    );
  }

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
