"use client";

/**
 * Post-IdP-callback landing page.
 *
 * The backend's `/api/admin/auth/callback` already issues the session cookie
 * trio (id_token, encrypted refresh_token, public expires_at) and 302s here.
 * We just forward the user to `return_to` — the middleware on the
 * destination page will verify the cookie is there, and the shell's
 * initial `/me` probe will verify it's still valid. If something went
 * wrong upstream, the destination page's 401 handler redirects to
 * `/login`.
 *
 * Wrapped in Suspense because `useSearchParams()` opts the page out of
 * static pre-rendering otherwise (Next 16+ requirement).
 */

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

function Inner() {
  const router = useRouter();
  const params = useSearchParams();
  const returnTo = params.get("return_to") || "/bots";

  useEffect(() => {
    router.replace(returnTo);
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
