"use client";

/**
 * Post-IdP-callback landing page.
 *
 * The backend's `/api/admin/auth/callback` already issues the session cookie
 * and 302s here. This component just confirms `/me` works (cookie was set
 * correctly) and routes the user into the app or to the login screen if
 * something went wrong upstream.
 *
 * Wrapped in Suspense because `useSearchParams()` opts the page out of
 * static pre-rendering otherwise (Next 16+ requirement).
 */

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";

function Inner() {
  const router = useRouter();
  const params = useSearchParams();
  const returnTo = params.get("return_to") || "/bots";

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then(() => {
        if (!cancelled) router.replace(returnTo);
      })
      .catch(() => {
        if (!cancelled) router.replace("/login");
      });
    return () => {
      cancelled = true;
    };
  }, [router, returnTo]);

  return (
    <div className="center">
      <p className="muted">Signing you in…</p>
    </div>
  );
}

export default function AuthCallbackPage() {
  return (
    <Suspense fallback={<div className="center"><p className="muted">Signing you in…</p></div>}>
      <Inner />
    </Suspense>
  );
}
