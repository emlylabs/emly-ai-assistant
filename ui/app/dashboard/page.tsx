"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Phase 2 of multi-bot-ui.md: legacy /dashboard URL redirects to /bots.
// Bookmarked URLs and any external integrations don't 404 on first deploy.
export default function LegacyDashboardRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/bots");
  }, [router]);
  return (
    <div className="center">
      <p className="muted">Redirecting…</p>
    </div>
  );
}
