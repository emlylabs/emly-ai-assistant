"use client";

/**
 * Shown when an OIDC-authenticated user has no `pending_admin` row matching
 * their email — `/api/admin/auth/callback` 302s here. They're who they say
 * they are, but nobody has authorised them to access this deployment yet.
 *
 * Wrapped in Suspense because `useSearchParams()` opts the page out of
 * static pre-rendering otherwise (Next 16+ requirement).
 */

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

function Inner() {
  const params = useSearchParams();
  const email = params.get("email") ?? "";

  return (
    <div className="center">
      <div className="card narrow">
        <h1 style={{ marginTop: 0 }}>Access required</h1>
        <p>
          Your sign-in succeeded
          {email ? (
            <>
              {" "}as <strong>{email}</strong>
            </>
          ) : null}
          , but you haven&apos;t been added as an administrator on this deployment.
        </p>
        <p className="muted">
          Ask an existing superadmin to invite this email via the Admins tab,
          then sign in again.
        </p>
        <a href="/login" style={{ marginTop: "1.5em", display: "inline-block" }}>
          Back to sign in
        </a>
      </div>
    </div>
  );
}

export default function RequestAccessPage() {
  return (
    <Suspense fallback={<div className="center"><p className="muted">Loading…</p></div>}>
      <Inner />
    </Suspense>
  );
}
